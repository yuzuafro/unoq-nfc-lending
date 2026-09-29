import os

from edge.display import Pattern
from edge.envfile import load_env_file
from edge.mgmt_client import MgmtUnavailable
from edge.nfc_reader import AgentReader
from edge.touch_fsm import State, Terminal

TAGS = {
    "U1": {"kind": "user", "name": "山田", "active": True},
    "U2": {"kind": "user", "name": "佐藤", "active": True},
    "UX": {"kind": "user", "name": "退職者", "active": False},
    "I1": {"kind": "item", "name": "ノートPC", "active": True},
    "NEW": {"kind": "unknown"},
    "REG": {"kind": "captured"},
}


class FakeClient:
    def __init__(self):
        self.touches, self.down = [], False

    def scan(self, uid):
        if self.down:
            raise MgmtUnavailable("down")
        return TAGS[uid]

    def touch(self, user_uid, item_uid):
        self.touches.append((user_uid, item_uid))
        return {"action": "checkout"}


class FakeDisplay:
    def __init__(self):
        self.shown = []

    def show(self, pattern, duration_ms=0):
        self.shown.append(pattern)


def make():
    client, display = FakeClient(), FakeDisplay()
    return Terminal(client, display), client, display


def test_user_then_item_checks_out():
    t, client, display = make()
    t.handle_uid("U1", 0)
    assert t.state == State.USER and display.shown[-1] == Pattern.USER
    t.handle_uid("I1", 1)
    assert client.touches == [("U1", "I1")]
    assert display.shown[-1] == Pattern.CHECKOUT and t.state == State.RESULT
    t.tick(1 + 2.1)
    assert t.state == State.IDLE and display.shown[-1] == Pattern.IDLE


def test_item_first_is_error():
    t, client, display = make()
    t.handle_uid("I1", 0)
    assert display.shown[-1] == Pattern.ERROR and client.touches == []


def test_user_times_out_after_10s():
    t, client, display = make()
    t.handle_uid("U1", 0)
    t.tick(9.9)
    assert t.state == State.USER
    t.tick(10.0)
    assert t.state == State.IDLE
    t.handle_uid("I1", 11)
    assert display.shown[-1] == Pattern.ERROR and client.touches == []


def test_second_user_replaces_first():
    t, client, _ = make()
    t.handle_uid("U1", 0)
    t.handle_uid("U2", 1)
    t.handle_uid("I1", 2)
    assert client.touches == [("U2", "I1")]


def test_unknown_inactive_captured_offline():
    t, client, display = make()
    t.handle_uid("NEW", 0)
    assert display.shown[-1] == Pattern.UNKNOWN
    t.handle_uid("UX", 1)
    assert display.shown[-1] == Pattern.ERROR
    t.handle_uid("REG", 2)
    assert display.shown[-1] == Pattern.CAPTURED
    client.down = True
    t.handle_uid("U1", 3)
    assert display.shown[-1] == Pattern.OFFLINE and t.state == State.RESULT


def test_touch_during_result_starts_new_flow():
    t, client, _ = make()
    t.handle_uid("U1", 0)
    t.handle_uid("I1", 1)
    t.handle_uid("U2", 1.5)  # still showing CHECKOUT
    assert t.state == State.USER


def test_agent_reader_parses_ndjson():
    got = []
    r = AgentReader("http://agent:8100", got.append)
    r._handle('{"uid": "04A1B2C3D4E5F6", "ts": 1}')
    r._handle("not json")
    assert got == ["04A1B2C3D4E5F6"]


def test_env_file(tmp_path, monkeypatch):
    f = tmp_path / "app.env"
    f.write_text("# comment\nMGMT_URL=http://pc:8000\nDEVICE_TOKEN='abc'\n")
    monkeypatch.delenv("MGMT_URL", raising=False)
    monkeypatch.setenv("DEVICE_TOKEN", "from-env")
    load_env_file(f)
    assert os.environ["MGMT_URL"] == "http://pc:8000"
    assert os.environ["DEVICE_TOKEN"] == "from-env"  # real env wins
    monkeypatch.delenv("MGMT_URL")
