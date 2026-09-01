"""logger.py
log() / log_event() - structured debug logger
- Filter by channel and level (DEBUG_CHANNELS, DEBUG_ALL)
- Print colored output to terminal (ANSI badges)
- Write plain output to file when LOG_FILE is set
- Auto-disable colors outside a real TTY / in CI
"""

from __future__ import annotations

import contextlib
import datetime

# Standard Library
import os
import sys
from typing import Any, Final

# region  ANSI COLOR CONSTANTS

_RESET: Final[str] = "\033[0m"
_BOLD: Final[str] = "\033[1m"
_DIM: Final[str] = "\033[2m"
_WHITE: Final[str] = "\033[97m"
_GRAY: Final[str] = "\033[90m"

# Channel background colors
_BG: Final[dict[str, str]] = {
    "BUS": "\033[44m",  # blue
    "COMMANDS": "\033[42m",  # green
    "APPROVAL": "\033[45m",  # magenta
    "HEARTBEAT": "\033[41m",  # red
    "MQTT": "\033[46m",  # cyan
    "TRACKER": "\033[43m",  # yellow
    "REGISTRY": "\033[104m",  # bright blue
    "SYSTEM": "\033[100m",  # dark gray
    "RULES": "\033[105m",  # bright magenta
}
_BG_DEFAULT: Final[str] = "\033[100m"

# Level text colors
_LEVEL_COLOR: Final[dict[str, str]] = {
    "INFO": "\033[97m",  # white
    "VERBOSE": "\033[93m",  # bright yellow
    "TRACE": "\033[90m",  # dark gray
}

# endregion


# region  CONFIGURATION - read once at import

_LEVELS: Final[dict[str, int]] = {"INFO": 0, "VERBOSE": 1, "TRACE": 2}

_DEBUG: Final[bool] = os.getenv("DEBUG", "0") == "1"
_DEBUG_ALL: Final[bool] = os.getenv("DEBUG_ALL", "0") == "1"
_DEBUG_LEVEL: Final[str] = os.getenv("DEBUG_LEVEL", "INFO").upper()
_raw_channels: Final[str] = os.getenv("DEBUG_CHANNELS", "")
_CHANNELS: Final[set[str]] = (
    set(c.strip() for c in _raw_channels.split(",") if c.strip()) if _raw_channels else {"BUS", "COMMANDS", "APPROVAL"}
)

_LOG_FILE: Final[str] = os.getenv("LOG_FILE", "")
_log_fh = None
_USE_COLOR: Final[bool] = (sys.stdout.isatty() or os.getenv("FORCE_COLOR", "0") == "1") and os.getenv(
    "NO_COLOR", ""
) == ""
if _LOG_FILE:
    try:
        _dir = os.path.dirname(_LOG_FILE)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        _log_fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 (persistent handle for process lifetime)
    except Exception as e:
        print(f"[logger] WARNING: could not open log file {_LOG_FILE!r}: {e}", file=sys.stderr)

# endregion


# region  PRIVATE HELPERS


def _channel_badge(channel: str) -> str:
    """Build an ANSI-colored channel badge string."""
    bg = _BG.get(channel, _BG_DEFAULT)
    return f"{bg}{_WHITE}{_BOLD} {channel:<9}{_RESET}"


def _level_badge(level: str) -> str:
    """Build an ANSI-colored level badge string."""
    color = _LEVEL_COLOR.get(level, _WHITE)
    return f"{color}{level:<7}{_RESET}"


# endregion


# region  PUBLIC API


def log(tag: str, msg: str, channel: str = "SYSTEM", level: str = "INFO") -> None:
    """Emit a structured log line if channel + level permit it.

    Args:
        tag: Module or component identifier.
        msg: Log message text.
        channel: Log channel for filtering (e.g. BUS, COMMANDS, SYSTEM).
        level: Severity level (INFO, VERBOSE, TRACE).
    """
    if not _DEBUG:
        return
    if not _DEBUG_ALL and channel not in _CHANNELS:
        return
    if _LEVELS.get(level, 0) > _LEVELS.get(_DEBUG_LEVEL, 0):
        return

    ts = datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds") + "Z"

    plain = f"[{ts}] [{channel:<9}] [{level:<7}] [{tag}] {msg}"

    if _USE_COLOR:
        colored = f"{_GRAY}{ts}{_RESET}  {_channel_badge(channel)}  {_level_badge(level)}  {_DIM}{tag}{_RESET}  {msg}"
        print(colored)
    else:
        print(plain)

    if _log_fh:
        with contextlib.suppress(Exception):
            _log_fh.write(plain + "\n")


def log_event(event_type: str, payload: dict[str, Any], channel: str = "BUS") -> None:
    """Log an event with its payload summary.

    Args:
        event_type: Event type identifier.
        payload: Event payload data.
        channel: Log channel (default: BUS).
    """
    log(event_type, str(payload), channel=channel, level="VERBOSE")


# endregion
