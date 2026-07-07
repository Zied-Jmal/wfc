"""logger.py
log() / log_event() - structured debug logger
- Filter by channel and level (DEBUG_CHANNELS, DEBUG_ALL)
- Print colored output to terminal (ANSI badges)
- Write plain output to file when LOG_FILE is set
- Auto-disable colors outside a real TTY / in CI
"""

from __future__ import annotations

# Standard Library

import os
import sys
import datetime
from typing import Any, Final

# Third-Party Libraries

# Project Imports

# region  ANSI COLOR CONSTANTS

_RESET: Final = "\033[0m"
_BOLD: Final = "\033[1m"
_DIM: Final = "\033[2m"
_WHITE: Final = "\033[97m"
_GRAY: Final = "\033[90m"

# Channel background colors
_BG: dict[str, str] = {
    "BUS":       "\033[44m",    # blue
    "COMMANDS":  "\033[42m",    # green
    "APPROVAL":  "\033[45m",    # magenta
    "HEARTBEAT": "\033[41m",    # red
    "MQTT":      "\033[46m",    # cyan
    "TRACKER":   "\033[43m",    # yellow
    "REGISTRY":  "\033[104m",   # bright blue
    "SYSTEM":    "\033[100m",   # dark gray
    "RULES":     "\033[105m",   # bright magenta
}
_BG_DEFAULT: Final = "\033[100m"

# Level text colors
_LEVEL_COLOR: dict[str, str] = {
    "INFO":    "\033[97m",      # white
    "VERBOSE": "\033[93m",      # bright yellow
    "TRACE":   "\033[90m",      # dark gray
}

# endregion

# region  CONFIGURATION - read once at import

_LEVELS = {"INFO": 0, "VERBOSE": 1, "TRACE": 2}

_DEBUG        = os.getenv("DEBUG", "0") == "1"
_DEBUG_ALL    = os.getenv("DEBUG_ALL", "0") == "1"
_DEBUG_LEVEL  = os.getenv("DEBUG_LEVEL", "INFO").upper()
_raw_channels = os.getenv("DEBUG_CHANNELS", "")
_CHANNELS: set[str] = (
    set(c.strip() for c in _raw_channels.split(",") if c.strip())
    if _raw_channels
    else {"BUS", "COMMANDS", "APPROVAL"}
)

_LOG_FILE  = os.getenv("LOG_FILE", "")
_log_fh    = None
_USE_COLOR = (sys.stdout.isatty() or os.getenv("FORCE_COLOR", "0") == "1") and os.getenv("NO_COLOR", "") == ""
if _LOG_FILE:
    try:
        _dir = os.path.dirname(_LOG_FILE)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        _log_fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
    except Exception as e:
        print(f"[logger] WARNING: could not open log file {_LOG_FILE!r}: {e}",
              file=sys.stderr)

# endregion

# region  PRIVATE HELPERS

def _channel_badge(channel: str) -> str:
    bg = _BG.get(channel, _BG_DEFAULT)
    return f"{bg}{_WHITE}{_BOLD} {channel:<9}{_RESET}"

def _level_badge(level: str) -> str:
    color = _LEVEL_COLOR.get(level, _WHITE)
    return f"{color}{level:<7}{_RESET}"

# endregion

# region  PUBLIC API

def log(tag: str, msg: str, channel: str = "SYSTEM", level: str = "INFO") -> None:
    """Emit a structured log line if channel + level permit it."""
    if not _DEBUG:
        return
    if not _DEBUG_ALL and channel not in _CHANNELS:
        return
    if _LEVELS.get(level, 0) > _LEVELS.get(_DEBUG_LEVEL, 0):
        return

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds") + "Z"  # pyright: ignore[reportDeprecated]

    plain   = f"[{ts}] [{channel:<9}] [{level:<7}] [{tag}] {msg}"

    if _USE_COLOR:
        colored = (
            f"{_GRAY}{ts}{_RESET}  "
            f"{_channel_badge(channel)}  "
            f"{_level_badge(level)}  "
            f"{_DIM}{tag}{_RESET}  "
            f"{msg}"
        )
        try:
            print(colored)
        except UnicodeEncodeError:
            pass
    else:
        try:
            print(plain)
        except UnicodeEncodeError:
            pass

    if _log_fh:
        try:
            _log_fh.write(plain + "\n")
        except Exception:
            pass

def log_event(event_type: str, payload: dict[str, Any], channel: str = "BUS") -> None:
    """Log an event with its payload summary."""
    log(event_type, str(payload), channel=channel, level="VERBOSE")

# endregion
