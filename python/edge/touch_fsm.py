"""Two-step touch state machine: user tag, then item tag (design §5.2).

Pure logic: the clock is passed in and I/O goes through the injected client and
display, so it can be tested without hardware.
"""
import logging
from enum import Enum

from .display import Pattern
from .mgmt_client import MgmtUnavailable

log = logging.getLogger("edge.touch_fsm")

USER_TIMEOUT_S = 10.0
RESULT_S = 2.0
OFFLINE_S = 3.0

ACTION_PATTERN = {"checkout": Pattern.CHECKOUT, "return": Pattern.RETURN,
                  "transfer": Pattern.TRANSFER, "error": Pattern.ERROR}


class State(Enum):
    IDLE = "idle"
    USER = "user"
    RESULT = "result"


class Terminal:
    def __init__(self, client, display):
        self.client = client
        self.display = display
        self.state = State.IDLE
        self.user_uid: str | None = None
        self.user_name: str | None = None
        self._deadline = 0.0

    def start(self) -> None:
        self.display.show(Pattern.IDLE)

    def handle_uid(self, uid: str, now: float) -> None:
        if self.state == State.RESULT:
            self.state = State.IDLE  # a new touch cuts the result display short
        try:
            scan = self.client.scan(uid)
            kind = scan.get("kind")
            if kind == "captured":
                log.info("tag %s captured for registration", uid)
                self._result(Pattern.CAPTURED, now)
            elif kind == "unknown":
                log.info("unknown tag %s", uid)
                self._result(Pattern.UNKNOWN, now)
            elif kind == "user":
                if not scan.get("active", True):
                    log.info("inactive user %s", scan.get("name"))
                    self._result(Pattern.ERROR, now)
                else:
                    self._enter_user(uid, scan.get("name"), now)
            elif kind == "item":
                self._item(uid, scan, now)
            else:
                log.warning("unexpected scan result: %s", scan)
                self._result(Pattern.ERROR, now)
        except MgmtUnavailable as e:
            log.warning("management unavailable: %s", e)
            self._result(Pattern.OFFLINE, now, OFFLINE_S)

    def tick(self, now: float) -> None:
        if self.state != State.IDLE and now >= self._deadline:
            if self.state == State.USER:
                log.info("user %s timed out", self.user_name)
            self._idle()

    # ---------------------------------------------------------------- internals
    def _enter_user(self, uid: str, name: str | None, now: float) -> None:
        self.state, self.user_uid, self.user_name = State.USER, uid, name
        self._deadline = now + USER_TIMEOUT_S
        log.info("user %s — waiting for an item", name)
        self.display.show(Pattern.USER, int(USER_TIMEOUT_S * 1000))

    def _item(self, uid: str, scan: dict, now: float) -> None:
        if self.state != State.USER:
            log.info("item %s touched before a user", scan.get("name"))
            self._result(Pattern.ERROR, now)
            return
        result = self.client.touch(self.user_uid, uid)
        action = result.get("action", "error")
        log.info("%s: %s / %s %s", action, self.user_name, scan.get("name"), result.get("message", ""))
        self._result(ACTION_PATTERN.get(action, Pattern.ERROR), now)

    def _result(self, pattern: Pattern, now: float, seconds: float = RESULT_S) -> None:
        self.state, self.user_uid, self.user_name = State.RESULT, None, None
        self._deadline = now + seconds
        self.display.show(pattern, int(seconds * 1000))

    def _idle(self) -> None:
        self.state, self.user_uid, self.user_name = State.IDLE, None, None
        self.display.show(Pattern.IDLE)
