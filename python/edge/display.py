import logging
from enum import IntEnum

log = logging.getLogger("edge.display")


class Pattern(IntEnum):
    # Keep in sync with sketch/patterns.h
    IDLE = 0
    USER = 1
    CHECKOUT = 2
    RETURN = 3
    TRANSFER = 4
    ERROR = 5
    UNKNOWN = 6
    CAPTURED = 7
    OFFLINE = 8


class BridgeDisplay:
    """Asks the MCU sketch to draw a pattern. notify() so a touch is never delayed by the MCU."""

    def show(self, pattern: Pattern, duration_ms: int = 0) -> None:
        from arduino.app_utils import Bridge  # imported lazily so tests run without the Router

        try:
            Bridge.notify("show", int(pattern), int(duration_ms))
        except Exception:
            log.exception("Bridge.notify(show) failed")
