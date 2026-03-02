# mode.py — shared runtime mode state
#
# Provides a thread-safe, toggleable trading mode.
# Both main.py (signal processor) and web.py (dashboard toggle)
# import this module to read/write the current mode.
#
# Modes:
#   "paper"  — simulated fills, no real orders
#   "global" — real orders via py-clob-client (international)
#   "us"     — real orders via US FCM API

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

_lock = threading.Lock()
_current_mode: str = os.getenv("POLYMARKET_MODE", "paper").lower()


def get_mode() -> str:
    """Get the current trading mode."""
    with _lock:
        return _current_mode


def set_mode(mode: str) -> str:
    """
    Set trading mode. Returns the new mode.
    Validates that the mode is known before switching.
    """
    global _current_mode
    mode = mode.lower().strip()

    if mode not in ("paper", "global", "us"):
        raise ValueError(f"Unknown mode: '{mode}'. Use: paper | global | us")

    with _lock:
        old = _current_mode
        _current_mode = mode

    if old != mode:
        log.info("Mode switched: %s → %s", old.upper(), mode.upper())

    return mode


def can_go_live() -> dict:
    """
    Check if live trading credentials are configured.
    Returns a dict with readiness status per mode.
    """
    return {
        "paper": True,
        "global": bool(os.getenv("POLY_PRIVATE_KEY")),
        "us": bool(
            os.getenv("POLY_US_API_KEY")
            and os.getenv("POLY_US_API_SECRET")
            and os.getenv("POLY_US_API_PASSPHRASE")
        ),
    }
