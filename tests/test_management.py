import pytest
from fastapi.testclient import TestClient

from management.app import create_app
from management.config import Settings

USER_UID = "04A1B2C3D4E5F6"
USER2_UID = "04A1B2C3D4E5F7"
ITEM_UID = "04112233445566"


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, admin_password="adminpass123")
    app = create_app(settings, local_device=True)
    with TestClient(app) as client:
        token = (tmp_path / "device_token").read_text().strip()
        dev = {"X-Device-Id": settings.local_device_id, "Authorization": f"Bearer {token}"}
        yield client, dev, app


def login(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "adminpass123"})
    assert r.status_code == 200


def seed(client):
    login(client)
    u1 = client.post("/api/v1/users", json={"tag_uid": USER_UID, "name": "山田"}).json()
    u2 = client.post("/api/v1/users", json={"tag_uid": USER2_UID, "name": "佐藤"}).json()
    it = client.post("/api/v1/items", json={"tag_uid": ITEM_UID, "name": "ノートPC 1"}).json()
    return u1, u2, it


def touch(client, dev, user_uid, item_uid=ITEM_UID):
    return client.post("/api/v1/touches", json={"user_uid": user_uid, "item_uid": item_uid}, headers=dev).json()


def test_device_auth_required(env):
    client, dev, _ = env
    assert client.post("/api/v1/scans", json={"uid": USER_UID}).status_code == 401
    bad = {**dev, "Authorization": "Bearer nope"}
    assert client.post("/api/v1/scans", json={"uid": USER_UID}, headers=bad).status_code == 401


def test_admin_required_for_writes(env):
    client, _, _ = env
    r = client.post("/api/v1/users", json={"tag_uid": USER_UID, "name": "x"})
    assert r.status_code == 401
    assert client.get("/api/v1/items").status_code == 200  # reads are public


def test_scan_kinds_and_unknown_tag(env):
    client, dev, _ = env
    seed(client)
    assert client.post("/api/v1/scans", json={"uid": "04:a1:b2:c3:d4:e5:f6"}, headers=dev).json()["kind"] == "user"
    assert client.post("/api/v1/scans", json={"uid": ITEM_UID}, headers=dev).json()["kind"] == "item"
    assert client.post("/api/v1/scans", json={"uid": "04FFFFFFFFFFFF"}, headers=dev).json()["kind"] == "unknown"
    tags = client.get("/api/v1/unknown-tags").json()
    assert [t["uid"] for t in tags] == ["04FFFFFFFFFFFF"]
    # Registering the tag removes it from the unknown list.
    client.post("/api/v1/items", json={"tag_uid": "04FFFFFFFFFFFF", "name": "工具箱"})
    assert client.get("/api/v1/unknown-tags").json() == []


def test_checkout_return_transfer(env):
    client, dev, _ = env
    u1, u2, it = seed(client)

    r = touch(client, dev, USER_UID)
    assert r["action"] == "checkout" and r["user"]["name"] == "山田"
    assert client.get("/api/v1/items").json()[0]["status"] == "on_loan"

    r = touch(client, dev, USER2_UID)
    assert r["action"] == "transfer" and r["previous_user"]["name"] == "山田"
    item = client.get("/api/v1/items").json()[0]
    assert item["loan"]["user_name"] == "佐藤"

    r = touch(client, dev, USER2_UID)
    assert r["action"] == "return"
    assert client.get("/api/v1/items").json()[0]["status"] == "available"

    loans = client.get("/api/v1/loans").json()
    assert loans["total"] == 2
    assert sorted(l["end_reason"] for l in loans["loans"]) == ["return", "transfer"]


def test_inactive_user_or_item_is_error(env):
    client, dev, _ = env
    u1, _, it = seed(client)
    client.patch(f"/api/v1/users/{u1['id']}", json={"active": False})
    assert touch(client, dev, USER_UID)["action"] == "error"
    client.patch(f"/api/v1/users/{u1['id']}", json={"active": True})
    client.patch(f"/api/v1/items/{it['id']}", json={"active": False})
    assert touch(client, dev, USER_UID)["action"] == "error"


def test_tag_uid_unique_across_users_and_items(env):
    client, _, _ = env
    seed(client)
    r = client.post("/api/v1/items", json={"tag_uid": USER_UID, "name": "dup"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "uid_in_use"
    assert client.post("/api/v1/users", json={"tag_uid": "xyz", "name": "bad"}).status_code == 400


def test_edit_user_and_replace_tag(env):
    client, dev, _ = env
    u1, _, _ = seed(client)
    touch(client, dev, USER_UID)
    r = client.patch(f"/api/v1/users/{u1['id']}", json={"name": "山田 太郎", "tag_uid": "04000000000001"})
    assert r.status_code == 200 and r.json()["tag_uid"] == "04000000000001"
    # History stays attached to the user after the tag is replaced.
    loans = client.get(f"/api/v1/loans?user_id={u1['id']}").json()["loans"]
    assert loans[0]["user_name"] == "山田 太郎"


def test_registration_mode_captures_next_scan(env):
    client, dev, _ = env
    login(client)
    s = client.post("/api/v1/registration-sessions").json()
    r = client.post("/api/v1/scans", json={"uid": "04ABABABABABAB"}, headers=dev).json()
    assert r["kind"] == "captured"
    got = client.get(f"/api/v1/registration-sessions/{s['id']}").json()
    assert got["status"] == "captured" and got["uid"] == "04ABABABABABAB"
    # The captured tag is not recorded as unknown, and the next scan is normal again.
    assert client.get("/api/v1/unknown-tags").json() == []
    assert client.post("/api/v1/scans", json={"uid": "04ABABABABABAB"}, headers=dev).json()["kind"] == "unknown"


def test_admin_close_loan_and_csv(env):
    client, dev, _ = env
    seed(client)
    touch(client, dev, USER_UID)
    loan = client.get("/api/v1/loans?active=true").json()["loans"][0]
    assert client.post(f"/api/v1/loans/{loan['id']}/close").json()["end_reason"] == "admin"
    assert client.post(f"/api/v1/loans/{loan['id']}/close").status_code == 409
    csv = client.get("/api/v1/loans.csv").text
    assert "ノートPC 1" in csv and "admin" in csv


def test_summary_and_heartbeat(env):
    client, dev, app = env
    seed(client)
    touch(client, dev, USER_UID)
    s = client.get("/api/v1/summary").json()
    assert s["on_loan"] == 1 and s["available"] == 0 and s["recent"][0]["type"] == "checkout"
    device_id = dev["X-Device-Id"]
    assert client.post(f"/api/v1/devices/{device_id}/heartbeat", json={"reader_ok": True}, headers=dev).status_code == 200
    devices = client.get("/api/v1/devices").json()
    assert devices[0]["reader_ok"] is True and devices[0]["last_seen_at"]


def test_websocket_receives_touch_events(env):
    client, dev, _ = env
    seed(client)
    with client.websocket_connect("/api/v1/ws/events") as ws:
        touch(client, dev, USER_UID)
        event = ws.receive_json()
        while event["type"] == "ping":
            event = ws.receive_json()
        assert event["type"] == "checkout" and event["item"]["name"] == "ノートPC 1"


def test_password_change_and_backup(env):
    client, _, _ = env
    login(client)
    assert client.post("/api/v1/auth/password", json={"current": "wrong", "new": "newpass123"}).status_code == 403
    assert client.post("/api/v1/auth/password", json={"current": "adminpass123", "new": "newpass123"}).status_code == 200
    r = client.get("/api/v1/backup")
    assert r.status_code == 200 and r.content[:15] == b"SQLite format 3"


def test_web_ui_is_served(env):
    client, _, _ = env
    r = client.get("/")
    assert r.status_code == 200 and "備品管理" in r.text
