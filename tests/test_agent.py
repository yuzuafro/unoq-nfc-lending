"""nfc-agent: debounce and the PC/SC backend, with pyscard replaced by a fake."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nfc-agent"))
import agent  # noqa: E402
import pcsc  # noqa: E402

NTAG_UID = bytes.fromhex("04A1B2C3D4E5F6")
CL = "Zoweetek ZW-12026-12 [CCID Interface] 00 01"
CONTACT = "Zoweetek ZW-12026-12 [CCID Interface] 00 00"


def type2_pages(uid: bytes) -> bytes:
    bcc0 = 0x88 ^ uid[0] ^ uid[1] ^ uid[2]
    bcc1 = uid[3] ^ uid[4] ^ uid[5] ^ uid[6]
    return uid[0:3] + bytes([bcc0]) + uid[3:7] + bytes([bcc1, 0x48, 0, 0, 0xE1, 0x10, 0x3E, 0])


class FakeScard:
    """Just enough of smartcard.scard. `cards` maps reader -> (get_uid response, page response)."""
    SCARD_S_SUCCESS = 0
    SCARD_E_TIMEOUT = 0x8010000A
    SCARD_E_NO_READERS_AVAILABLE = 0x8010002E
    SCARD_E_NO_SMARTCARD = 0x8010000C
    SCARD_SCOPE_USER = 0
    SCARD_SHARE_SHARED = 2
    SCARD_PROTOCOL_T0, SCARD_PROTOCOL_T1 = 1, 2
    SCARD_LEAVE_CARD = 0
    SCARD_STATE_UNAWARE, SCARD_STATE_CHANGED, SCARD_STATE_EMPTY = 0, 0x2, 0x10
    SCARD_STATE_PRESENT, SCARD_STATE_MUTE = 0x20, 0x200

    def __init__(self, readers):
        self.readers = readers
        self.cards: dict[str, tuple[bytes, bytes]] = {}
        self.sent = []

    def SCardGetErrorMessage(self, hr):
        return "error"

    def SCardEstablishContext(self, scope):
        return 0, "ctx"

    def SCardReleaseContext(self, ctx):
        return 0

    def SCardListReaders(self, ctx, groups):
        return (0, list(self.readers)) if self.readers else (self.SCARD_E_NO_READERS_AVAILABLE, [])

    def _event(self, reader):
        return (self.SCARD_STATE_PRESENT if reader in self.cards else self.SCARD_STATE_EMPTY)

    def SCardGetStatusChange(self, ctx, timeout, states):
        out = [(r, self._event(r) | self.SCARD_STATE_CHANGED, b"") for r, cur in states
               if cur & ~self.SCARD_STATE_CHANGED != self._event(r)]
        return (0, out) if out else (self.SCARD_E_TIMEOUT, [])

    def SCardConnect(self, ctx, reader, share, proto):
        if reader not in self.cards:
            return self.SCARD_E_NO_SMARTCARD, None, 0
        return 0, reader, self.SCARD_PROTOCOL_T1

    def SCardTransmit(self, card, proto, apdu):
        self.sent.append((card, apdu))
        get_uid, pages = self.cards[card]
        return 0, list(get_uid if apdu == pcsc.GET_UID else pages)

    def SCardDisconnect(self, card, disposition):
        return 0


def make(readers=(CL, CONTACT)):
    s = FakeScard(list(readers))
    got, seen = [], []
    r = pcsc.PcscReader(s, got.append, seen.append, poll_ms=0)
    r.connect()
    return s, r, got, seen


def test_debouncer_drops_repeats_of_a_held_tag():
    t = [0.0]
    d = agent.Debouncer(1.5, clock=lambda: t[0])
    assert d("A")
    t[0] = 1.0
    assert not d("A")       # still held
    assert d("B")
    t[0] = 5.0
    assert d("B")           # touched again later


def test_set_reader_summarises_backends():
    agent.set_reader("nfcpy", None)
    agent.set_reader("pcsc", "Zoweetek")
    assert agent.state["reader"] and agent.state["path"] == "Zoweetek"
    agent.set_reader("pcsc", None)
    assert not agent.state["reader"]


def test_uid_from_get_data():
    s, r, got, seen = make()
    r.poll_once()
    assert seen == [[CL, CONTACT]] and got == []
    s.cards[CL] = (NTAG_UID + b"\x90\x00", b"")
    r.poll_once()
    assert got == ["04A1B2C3D4E5F6"]
    r.poll_once()           # card still on the reader: no second event
    assert got == ["04A1B2C3D4E5F6"]
    del s.cards[CL]
    r.poll_once()
    s.cards[CL] = (NTAG_UID + b"\x90\x00", b"")
    r.poll_once()           # placed again
    assert got == ["04A1B2C3D4E5F6"] * 2


def test_falls_back_to_type2_page_read():
    s, r, got, _ = make()
    s.cards[CL] = (b"\x6A\x81", type2_pages(NTAG_UID) + b"\x90\x00")
    r.poll_once()
    assert got == ["04A1B2C3D4E5F6"]


def test_contact_card_is_ignored():
    s, r, got, _ = make()
    s.cards[CONTACT] = (b"\x6D\x00", b"\x6D\x00")
    r.poll_once()
    assert got == []


def test_type2_check_bytes():
    pages = type2_pages(NTAG_UID)
    assert pcsc.uid_from_type2_pages(pages) == "04A1B2C3D4E5F6"
    assert pcsc.uid_from_type2_pages(bytes([pages[0] ^ 1]) + pages[1:]) is None
    assert pcsc.uid_from_type2_pages(b"\x00" * 4) is None


def test_reader_unplugged_and_none_left():
    s, r, got, seen = make()
    r.poll_once()
    s.readers = []
    r.poll_once()
    assert seen[-1] == [] and r.states == {}


def test_usb_fingerprint(tmp_path):
    (tmp_path / "1-1").mkdir()
    (tmp_path / "1-1" / "devnum").write_text("3\n")
    (tmp_path / "1-1:1.0").mkdir()
    assert pcsc.usb_fingerprint(str(tmp_path)) == {("1-1", "3")}
    assert pcsc.usb_fingerprint(str(tmp_path / "missing")) == frozenset()
