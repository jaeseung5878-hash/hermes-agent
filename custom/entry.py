"""
Hermes Smart Router — Railway entrypoint.

Bootstraps the Hermes gateway with our Smart Router shimmed into the
per-turn model resolution hook.

Boot sequence:
    1. Ensure HERMES_HOME directory exists (Railway volume = /data).
    2. Seed a minimal config.yaml if absent (model + OpenRouter provider).
    3. Install the Smart Router shim over agent.smart_model_routing.resolve_turn_route.
    4. Run gateway.run.start_gateway.

Run with:
    python -m custom.entry
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _ensure_hermes_home() -> Path:
    """Ensure HERMES_HOME exists and is writable. Default to /data on Railway."""
    hermes_home = os.environ.get("HERMES_HOME")
    if not hermes_home:
        if Path("/data").is_dir():
            hermes_home = "/data"
        else:
            hermes_home = str(Path.home() / ".hermes")
        os.environ["HERMES_HOME"] = hermes_home

    home_path = Path(hermes_home)
    home_path.mkdir(parents=True, exist_ok=True)
    logging.info("HERMES_HOME = %s", home_path)
    return home_path


_DEFAULTS: dict = {
    # The Smart Router shim overrides per-turn routing, so model/provider here
    # only serve as the fallback "primary" when the shim is bypassed.
    "model": {
        "default": "openai/gpt-5.4-nano",
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
    },
    # Disable Hermes's built-in smart routing — our shim replaces it entirely.
    "smart_model_routing": {"enabled": False},
    "agent": {
        "gateway_timeout": 900,
        "gateway_timeout_warning": 600,
        "gateway_notify_interval": 300,
    },
    # Slack behaviour overrides — reply in-channel (not thread) and keep
    # mention-gated messaging in shared channels.
    "platforms": {
        "slack": {
            "enabled": True,
            "extra": {
                "reply_in_thread": False,
            },
        },
    },
}


def _deep_merge_defaults(existing: dict, defaults: dict) -> bool:
    """Recursively inject missing default keys into ``existing``.

    Only adds keys that don't exist; never overwrites user values. Returns
    True if any key was added (caller decides whether to re-serialize).

    Exception: ``platforms.slack.extra.reply_in_thread`` is forced to the
    default when the user did not explicitly override it, because the
    upstream default (True) produces a UX we don't want on this bot.
    """
    changed = False
    for key, default_val in defaults.items():
        if key not in existing:
            existing[key] = default_val
            changed = True
            continue
        if isinstance(default_val, dict) and isinstance(existing[key], dict):
            if _deep_merge_defaults(existing[key], default_val):
                changed = True
    return changed


def _seed_config_if_missing(home_path: Path) -> Path:
    """Ensure config.yaml exists and contains the required Smart-Router defaults.

    - If the file is absent, write a fresh copy from ``_DEFAULTS``.
    - If the file already exists, deep-merge missing keys but never overwrite
      user-set values (idempotent; safe to run on every boot).
    """
    import yaml  # pyyaml comes in via hermes-agent core deps

    config_path = home_path / "config.yaml"
    if not config_path.exists():
        config_path.write_text(
            yaml.safe_dump(_DEFAULTS, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        logging.info("Seeded default config.yaml at %s", config_path)
        return config_path

    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logging.error("Failed to parse %s (%s); leaving untouched", config_path, e)
        return config_path

    if not isinstance(loaded, dict):
        logging.warning("config.yaml root is not a mapping; overwriting")
        config_path.write_text(
            yaml.safe_dump(_DEFAULTS, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return config_path

    changed = _deep_merge_defaults(loaded, _DEFAULTS)

    # Force-override fields we care about regardless of what the file had.
    # Deep-merge only fills MISSING keys; this block stomps on any stale value
    # that would break the bot's UX (e.g. reply_in_thread from an old seed).
    platforms_block = loaded.setdefault("platforms", {})
    if not isinstance(platforms_block, dict):
        platforms_block = {}
        loaded["platforms"] = platforms_block
    slack_block = platforms_block.setdefault("slack", {})
    if not isinstance(slack_block, dict):
        slack_block = {}
        platforms_block["slack"] = slack_block
    slack_block["enabled"] = True
    slack_extra = slack_block.setdefault("extra", {})
    if not isinstance(slack_extra, dict):
        slack_extra = {}
        slack_block["extra"] = slack_extra
    if slack_extra.get("reply_in_thread") is not False:
        slack_extra["reply_in_thread"] = False
        changed = True

    if changed:
        config_path.write_text(
            yaml.safe_dump(loaded, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        logging.info("Patched config.yaml at %s", config_path)

    # Diagnostic: dump the effective Slack platform block so we can verify the
    # on-disk shape from the deploy logs.
    logging.info(
        "Effective platforms.slack = %s",
        loaded.get("platforms", {}).get("slack"),
    )

    return config_path


def _install_router_shim() -> None:
    """Replace agent.smart_model_routing.resolve_turn_route with our Smart Router.

    This must happen BEFORE gateway starts handling messages. gateway/run.py:971
    performs the import lazily inside ``_resolve_turn_agent_config`` so a
    module-attribute swap is sufficient — no need for sys.modules trickery.
    """
    # Ensure the package root is on sys.path so `import agent.smart_model_routing`
    # works when this module is invoked via `python -m custom.entry` from /app.
    here = Path(__file__).resolve().parent.parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))

    from custom.router_adapter import install_shim
    install_shim()

    # Fix synthetic thread_id leak in Slack adapter so reply_in_thread=False
    # actually posts directly into channels for top-level mentions.
    from custom.slack_patch import install_patch as _install_slack_patch
    _install_slack_patch()


def _preflight_env() -> None:
    """Validate critical env vars. Fail fast with actionable errors."""
    missing: list[str] = []
    for key in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "OPENROUTER_API_KEY"):
        if not os.environ.get(key):
            missing.append(key)
    if missing:
        logging.error(
            "Missing required env vars: %s. "
            "SLACK_BOT_TOKEN (xoxb-), SLACK_APP_TOKEN (xapp-, Socket Mode), "
            "OPENROUTER_API_KEY must be set.",
            ", ".join(missing),
        )
        # Do not hard-exit — let gateway surface the platform-specific error.


async def _run_gateway() -> None:
    """Boot the A2A proxy, then run the Hermes gateway."""
    from gateway.config import load_gateway_config
    from gateway.run import start_gateway
    from custom.a2a_proxy import start_proxy

    # Start the A2A OpenAI-compatible proxy first so routing decisions targeting
    # CLAUDE_A2A can reach it from the moment gateway begins dispatching.
    proxy_runner = await start_proxy()
    if proxy_runner is None:
        logging.warning(
            "A2A proxy did not start — CLAUDE_A2A routes will fail. "
            "This is usually a port conflict on 127.0.0.1:8888."
        )

    try:
        config = load_gateway_config()
        await start_gateway(config)
    finally:
        if proxy_runner is not None:
            await proxy_runner.cleanup()


def main() -> int:
    _setup_logging()
    home_path = _ensure_hermes_home()
    _seed_config_if_missing(home_path)
    _install_router_shim()
    _preflight_env()

    logging.info("Starting Hermes gateway…")
    try:
        asyncio.run(_run_gateway())
    except KeyboardInterrupt:
        logging.info("Gateway stopped by KeyboardInterrupt")
        return 0
    except Exception:
        logging.exception("Gateway crashed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
