"""PC/SC backend for nfc-agent: CCID readers such as the Zoweetek ZW-12026-12.

nfcpy only drives readers it has its own driver for (RC-S380, PN53x, ...), so
CCID readers go through pcscd + libccid instead. pcscd runs inside this
container as a child process.

The container gets no udev events, so pcscd never hears about a reader that is
plugged in or pulled out after it started. Instead we watch the USB device list
in sysfs and restart pcscd whenever it changes.
"""
import logging
import os
import subprocess
import time
from typing import Callable

log = logging.getLogger("nfc-agent.pcsc")

GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]     # PC/SC Part 3 "Get Data": UID of the contactless card
READ_PAGES_0_3 = [0xFF, 0xB0, 0x00, 0x00, 0x10]  # Read Binary: 16 bytes from page 0 (NFC Forum Type 2)
SYSFS_USB = "/sys/bus/usb/devices"
USB_SETTLE_S = 1.0  # let a newly plugged device finish enumerating before pcscd scans


def usb_fingerprint(root: str = SYSFS_USB) -> frozenset:
    """The set of attached USB devices; it changes when anything is plugged or unplugged."""
    devices = set()
    try:
        names = os.listdir(root)
    except OSError:
        return frozenset()
    for name in names:
        try:
            with open(os.path.join(root, name, "devnum")) as f:
                devices.add((name, f.read().strip()))
        except OSError:
            pass  # interfaces (1-1:1.0) have no devnum
    return frozenset(devices)


def uid_from_type2_pages(data: bytes) -> str | None:
    """UID from the first 16 bytes of an NFC Forum Type 2 tag (NTAG21x, MIFARE Ultralight).

    Page 0-2 hold UID0-2, BCC0, UID3-6, BCC1; the check bytes keep us from
    returning garbage when the card is not a Type 2 tag.
    """
    if len(data) < 9:
        return None
    uid = data[0:3] + data[4:8]
    if data[3] != 0x88 ^ data[0] ^ data[1] ^ data[2] or data[8] != data[4] ^ data[5] ^ data[6] ^ data[7]:
        return None
    return uid.hex().upper()


class PcscDaemon:
    """pcscd as a child process, restarted to pick up reader changes."""

    # --disable-polkit: there is no polkit daemon in the container, so every client would be refused.
    def __init__(self, cmd: tuple = ("pcscd", "--foreground", "--disable-polkit")):
        self.cmd = cmd
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        self.stop()
        os.makedirs("/run/pcscd", exist_ok=True)
        debug = log.isEnabledFor(logging.DEBUG)
        self.proc = subprocess.Popen(list(self.cmd) + (["--debug"] if debug else []),
                                     stdout=None if debug else subprocess.DEVNULL,
                                     stderr=None if debug else subprocess.DEVNULL)
        time.sleep(1.0)  # pcscd scans the USB bus at startup; clients connecting earlier see no readers

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


class PcscError(Exception):
    pass


class PcscReader:
    """Watches every PC/SC reader and reports the UID of each card that is placed on one.

    `scard` is pyscard's smartcard.scard (injectable for tests). `on_uid` gets the
    UID as upper-case hex, the same form nfcpy gives, so tags registered with the
    RC-S380 still match. `on_readers` gets the current reader names.
    """

    def __init__(self, scard, on_uid: Callable[[str], None], on_readers: Callable[[list], None],
                 daemon: PcscDaemon | None = None, fingerprint: Callable[[], frozenset] = usb_fingerprint,
                 poll_ms: int = 500):
        self.scard = scard
        self.on_uid = on_uid
        self.on_readers = on_readers
        self.daemon = daemon
        self.fingerprint = fingerprint
        self.poll_ms = poll_ms
        self.ctx = None
        self.present: set[str] = set()  # readers with a card on them (so a held card is reported once)
        self.states: dict[str, int] = {}
        self.readers: list[str] = []

    # --- pyscard helpers -------------------------------------------------
    def _check(self, hr: int, what: str) -> None:
        if hr != self.scard.SCARD_S_SUCCESS:
            raise PcscError(f"{what}: {self.scard.SCardGetErrorMessage(hr)} (0x{hr & 0xFFFFFFFF:08X})")

    def connect(self) -> None:
        hr, self.ctx = self.scard.SCardEstablishContext(self.scard.SCARD_SCOPE_USER)
        self._check(hr, "SCardEstablishContext")

    def release(self) -> None:
        if self.ctx is not None:
            try:
                self.scard.SCardReleaseContext(self.ctx)
            except Exception:
                pass
        self.ctx = None
        self.present.clear()
        self.states.clear()
        self._set_readers([])

    def _set_readers(self, readers: list) -> None:
        if readers != self.readers:
            self.readers = readers
            if readers:
                log.info("PC/SC readers: %s", ", ".join(readers))
            self.on_readers(readers)

    def list_readers(self) -> list:
        hr, readers = self.scard.SCardListReaders(self.ctx, [])
        if hr == self.scard.SCARD_E_NO_READERS_AVAILABLE:
            return []
        self._check(hr, "SCardListReaders")
        return list(readers)

    def transmit(self, card, protocol, apdu: list) -> tuple[bytes, int]:
        hr, resp = self.scard.SCardTransmit(card, protocol, apdu)
        self._check(hr, "SCardTransmit")
        resp = bytes(resp)
        if len(resp) < 2:
            return b"", 0
        return resp[:-2], (resp[-2] << 8) | resp[-1]

    def read_uid(self, reader: str) -> str | None:
        s = self.scard
        hr, card, protocol = s.SCardConnect(self.ctx, reader, s.SCARD_SHARE_SHARED,
                                            s.SCARD_PROTOCOL_T0 | s.SCARD_PROTOCOL_T1)
        if hr != s.SCARD_S_SUCCESS:  # card already gone, or a contact card that needs another protocol
            log.debug("connect %s: %s", reader, s.SCardGetErrorMessage(hr))
            return None
        try:
            data, sw = self.transmit(card, protocol, GET_UID)
            if sw == 0x9000 and data:
                return data.hex().upper()
            log.debug("GET UID on %s returned SW=%04X; trying Type 2 page read", reader, sw)
            data, sw = self.transmit(card, protocol, READ_PAGES_0_3)
            if sw == 0x9000:
                return uid_from_type2_pages(data)
            log.info("card on %s gave no UID (SW=%04X); contact card?", reader, sw)
            return None
        except PcscError as e:
            log.debug("read %s: %s", reader, e)
            return None
        finally:
            s.SCardDisconnect(card, s.SCARD_LEAVE_CARD)

    # --- main loop -------------------------------------------------------
    def poll_once(self) -> None:
        """Wait up to poll_ms for a card change on any reader and report new cards."""
        s = self.scard
        readers = self.list_readers()
        self._set_readers(readers)
        for gone in set(self.states) - set(readers):
            self.states.pop(gone, None)
            self.present.discard(gone)
        if not readers:
            time.sleep(self.poll_ms / 1000)
            return
        hr, changes = s.SCardGetStatusChange(
            self.ctx, self.poll_ms, [(r, self.states.get(r, s.SCARD_STATE_UNAWARE)) for r in readers])
        if hr == s.SCARD_E_TIMEOUT:
            return
        self._check(hr, "SCardGetStatusChange")
        for reader, event, _atr in changes:
            self.states[reader] = event & ~s.SCARD_STATE_CHANGED
            if event & s.SCARD_STATE_PRESENT and not event & s.SCARD_STATE_MUTE:
                if reader in self.present:
                    continue
                self.present.add(reader)
                uid = self.read_uid(reader)
                if uid:
                    self.on_uid(uid)
            else:
                self.present.discard(reader)

    def run_forever(self, retry_s: float = 3.0) -> None:
        warned = False
        while True:
            usb = self.fingerprint()
            if self.daemon:
                self.daemon.start()
            try:
                self.connect()
                while usb == self.fingerprint() and (self.daemon is None or self.daemon.alive()):
                    self.poll_once()
                    if self.readers:
                        warned = False
                    elif not warned:
                        log.warning("no PC/SC reader found; waiting for one to be plugged in")
                        warned = True
                if self.daemon and not self.daemon.alive():
                    log.warning("pcscd exited; restarting it")
                else:
                    log.info("USB devices changed; restarting pcscd")
                time.sleep(USB_SETTLE_S)
            except PcscError as e:
                log.warning("PC/SC error: %s; restarting in %.0fs", e, retry_s)
                time.sleep(retry_s)
            finally:
                self.release()


def run(on_uid: Callable[[str], None], on_readers: Callable[[list], None]) -> None:
    from smartcard import scard  # imported here so fake_agent and tests do not need pyscard

    PcscReader(scard, on_uid, on_readers, daemon=PcscDaemon()).run_forever()
