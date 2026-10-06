"""Business rules. All DB writes go through here so terminals share one set of rules."""
import csv
import io
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .events import EventHub
from .models import Admin, AppSetting, Device, Item, Loan, UnknownTag, User, utcnow
from .security import hash_password, hash_token, new_token, verify_password

UID_RE = re.compile(r"^[0-9A-F]{8,20}$")

# Optional attribute fields whose label (and whether they are used at all) each site
# can change from 設定, e.g. 部署 → 課, チーム → 社員種別. Column names stay the same.
FIELD_DEFAULTS = {
    "users": {"department": "部署", "team": "チーム"},
    "items": {"asset_no": "管理番号", "category": "カテゴリ", "location": "保管場所"},
}
FIELD_LABEL_MAX = 20


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def normalize_uid(uid: str) -> str:
    cleaned = re.sub(r"[\s:\-]", "", uid or "").upper()
    if not UID_RE.match(cleaned):
        raise ServiceError("invalid_uid", "UID は16進数（8〜20桁）で指定してください")
    return cleaned


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:  # SQLite drops tzinfo; values are stored as UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def user_dict(u: User) -> dict:
    return {"id": u.id, "tag_uid": u.tag_uid, "name": u.name, "department": u.department,
            "team": u.team, "note": u.note, "active": u.active,
            "created_at": iso(u.created_at), "updated_at": iso(u.updated_at)}


def item_dict(i: Item) -> dict:
    return {"id": i.id, "tag_uid": i.tag_uid, "name": i.name, "asset_no": i.asset_no,
            "category": i.category, "location": i.location, "note": i.note, "active": i.active,
            "created_at": iso(i.created_at), "updated_at": iso(i.updated_at)}


def loan_dict(l: Loan) -> dict:
    return {"id": l.id, "item_id": l.item_id, "item_name": l.item.name, "item_asset_no": l.item.asset_no,
            "user_id": l.user_id, "user_name": l.user.name, "user_team": l.user.team,
            "device_id": l.device_id, "started_at": iso(l.started_at),
            "ended_at": iso(l.ended_at), "end_reason": l.end_reason}


class Registrations:
    """Short-lived 'read a tag for this form' sessions started from the Web UI."""

    def __init__(self, ttl: int):
        self.ttl = ttl
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self) -> dict:
        with self._lock:
            self._expire()
            # Only the newest waiting session receives the next tag.
            for s in self._sessions.values():
                if s["status"] == "waiting":
                    s["status"] = "cancelled"
            sid = uuid.uuid4().hex
            s = {"id": sid, "status": "waiting", "uid": None, "existing": None,
                 "expires_at": time.time() + self.ttl}
            self._sessions[sid] = s
            return dict(s)

    def get(self, sid: str) -> dict | None:
        with self._lock:
            self._expire()
            s = self._sessions.get(sid)
            return dict(s) if s else None

    def cancel(self, sid: str) -> None:
        with self._lock:
            s = self._sessions.get(sid)
            if s and s["status"] == "waiting":
                s["status"] = "cancelled"

    def capture(self, uid: str, existing: dict | None) -> dict | None:
        with self._lock:
            self._expire()
            for s in self._sessions.values():
                if s["status"] == "waiting":
                    s.update(status="captured", uid=uid, existing=existing)
                    return dict(s)
        return None

    def _expire(self) -> None:
        now = time.time()
        for sid, s in list(self._sessions.items()):
            if s["status"] == "waiting" and s["expires_at"] < now:
                s["status"] = "expired"
            if s["expires_at"] + 600 < now:
                del self._sessions[sid]


class Service:
    def __init__(self, session_factory: sessionmaker, hub: EventHub, registration_ttl: int = 60):
        self.sf = session_factory
        self.hub = hub
        self.registrations = Registrations(registration_ttl)

    @contextmanager
    def session(self):
        s: Session = self.sf()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ------------------------------------------------------------------ terminal
    def _find_tag(self, s: Session, uid: str) -> tuple[str, User | Item] | None:
        u = s.scalar(select(User).where(User.tag_uid == uid))
        if u:
            return "user", u
        i = s.scalar(select(Item).where(Item.tag_uid == uid))
        if i:
            return "item", i
        return None

    def scan(self, device_id: str, uid: str) -> dict:
        uid = normalize_uid(uid)
        with self.session() as s:
            found = self._find_tag(s, uid)
            existing = {"kind": found[0], "id": found[1].id, "name": found[1].name} if found else None
            captured = self.registrations.capture(uid, existing)
            if captured:
                self.hub.publish("captured", uid=uid, session_id=captured["id"], existing=existing)
                return {"kind": "captured", "uid": uid}
            if found:
                kind, obj = found
                return {"kind": kind, "uid": uid, "id": obj.id, "name": obj.name, "active": obj.active}
            tag = s.get(UnknownTag, uid)
            now = utcnow()
            if tag:
                tag.last_seen_at, tag.device_id = now, device_id
            else:
                s.add(UnknownTag(uid=uid, device_id=device_id, first_seen_at=now, last_seen_at=now))
        self.hub.publish("unknown_tag", uid=uid, device_id=device_id)
        return {"kind": "unknown", "uid": uid}

    def touch(self, device_id: str, user_uid: str, item_uid: str) -> dict:
        user_uid, item_uid = normalize_uid(user_uid), normalize_uid(item_uid)
        try:
            with self.session() as s:
                user = s.scalar(select(User).where(User.tag_uid == user_uid))
                item = s.scalar(select(Item).where(Item.tag_uid == item_uid))
                if not user or not item:
                    return {"action": "error", "reason": "not_found", "message": "登録されていないタグです"}
                if not user.active:
                    return {"action": "error", "reason": "user_inactive", "message": f"{user.name} さんは無効化されています"}
                if not item.active:
                    return {"action": "error", "reason": "item_inactive", "message": f"{item.name} は無効化されています"}

                now = utcnow()
                current = s.scalar(select(Loan).where(Loan.item_id == item.id, Loan.ended_at.is_(None)))
                previous_user = None
                if current is None:
                    action = "checkout"
                    s.add(Loan(item_id=item.id, user_id=user.id, device_id=device_id, started_at=now))
                elif current.user_id == user.id:
                    action = "return"
                    current.ended_at, current.end_reason = now, "return"
                else:
                    # Hand over: close the current loan and open a new one in the same transaction.
                    action = "transfer"
                    previous_user = {"id": current.user.id, "name": current.user.name}
                    current.ended_at, current.end_reason = now, "transfer"
                    s.flush()  # release the open-loan unique index before inserting
                    s.add(Loan(item_id=item.id, user_id=user.id, device_id=device_id, started_at=now))
                result = {"action": action, "item": {"id": item.id, "name": item.name},
                          "user": {"id": user.id, "name": user.name}, "previous_user": previous_user}
        except IntegrityError:
            return {"action": "error", "reason": "conflict", "message": "同時に操作されました。もう一度タッチしてください"}
        self.hub.publish(action, device_id=device_id, item=result["item"], user=result["user"],
                         previous_user=previous_user)
        return result

    # ------------------------------------------------------------------ field labels
    def get_fields(self) -> dict:
        """{"users": {"department": {"label": "部署", "enabled": True}, ...}, "items": {...}}"""
        with self.session() as s:
            row = s.get(AppSetting, "fields")
            saved = json.loads(row.value) if row and row.value else {}
        return {kind: {key: {"label": default, "enabled": True, **saved.get(kind, {}).get(key, {})}
                       for key, default in defaults.items()}
                for kind, defaults in FIELD_DEFAULTS.items()}

    def set_fields(self, data: dict) -> dict:
        """Merge label / enabled changes. Unknown fields are rejected; a blank label means the default."""
        fields = self.get_fields()
        for kind, entries in data.items():
            for key, change in (entries or {}).items():
                if key not in FIELD_DEFAULTS.get(kind, {}):
                    raise ServiceError("unknown_field", f"項目 {kind}.{key} は変更できません")
                if change.get("label") is not None:
                    label = change["label"].strip() or FIELD_DEFAULTS[kind][key]
                    if len(label) > FIELD_LABEL_MAX:
                        raise ServiceError("label_too_long", f"項目名は{FIELD_LABEL_MAX}文字以内にしてください")
                    fields[kind][key]["label"] = label
                if change.get("enabled") is not None:
                    fields[kind][key]["enabled"] = bool(change["enabled"])
        with self.session() as s:
            row = s.get(AppSetting, "fields") or AppSetting(key="fields")
            row.value = json.dumps(fields, ensure_ascii=False)
            s.add(row)
        self.hub.publish("fields_changed")
        return fields

    # ------------------------------------------------------------------ devices
    def authenticate_device(self, device_id: str, token: str) -> bool:
        with self.session() as s:
            d = s.get(Device, device_id)
            return bool(d and token and d.token_hash == hash_token(token))

    def heartbeat(self, device_id: str, reader_ok: bool | None) -> None:
        with self.session() as s:
            d = s.get(Device, device_id)
            if d:
                d.last_seen_at, d.reader_ok = utcnow(), reader_ok

    def list_devices(self) -> list[dict]:
        with self.session() as s:
            return [{"id": d.id, "name": d.name, "last_seen_at": iso(d.last_seen_at), "reader_ok": d.reader_ok}
                    for d in s.scalars(select(Device).order_by(Device.id))]

    def create_device(self, device_id: str, name: str) -> str:
        """Create a device, or issue a new token for an existing one. Returns the token (shown once)."""
        if not re.match(r"^[A-Za-z0-9_.-]{1,64}$", device_id):
            raise ServiceError("invalid_device_id", "端末IDは英数字と _ . - で指定してください")
        token = new_token()
        with self.session() as s:
            d = s.get(Device, device_id)
            if d:
                d.token_hash = hash_token(token)
                d.name = name or d.name
            else:
                s.add(Device(id=device_id, name=name, token_hash=hash_token(token)))
        return token

    def delete_device(self, device_id: str) -> None:
        with self.session() as s:
            d = s.get(Device, device_id)
            if not d:
                raise ServiceError("not_found", "端末が見つかりません", 404)
            s.delete(d)

    # ------------------------------------------------------------------ users / items
    def _check_uid_free(self, s: Session, uid: str, own: tuple[str, int] | None = None) -> None:
        found = self._find_tag(s, uid)
        if found and (own is None or (found[0], found[1].id) != own):
            kind = "ユーザー" if found[0] == "user" else "備品"
            raise ServiceError("uid_in_use", f"このタグは{kind}「{found[1].name}」に登録済みです", 409)

    def list_users(self, q: str = "", active: bool | None = None) -> list[dict]:
        with self.session() as s:
            stmt = select(User).order_by(User.name)
            if q:
                like = f"%{q}%"
                stmt = stmt.where(or_(User.name.ilike(like), User.department.ilike(like),
                                      User.team.ilike(like), User.tag_uid.ilike(like)))
            if active is not None:
                stmt = stmt.where(User.active.is_(active))
            users = list(s.scalars(stmt))
            open_loans = s.scalars(select(Loan).where(Loan.ended_at.is_(None))).all()
            by_user: dict[int, list] = {}
            for l in open_loans:
                by_user.setdefault(l.user_id, []).append({"loan_id": l.id, "item_id": l.item_id,
                                                          "item_name": l.item.name, "started_at": iso(l.started_at)})
            return [{**user_dict(u), "loans": by_user.get(u.id, [])} for u in users]

    def create_user(self, data: dict) -> dict:
        with self.session() as s:
            uid = normalize_uid(data["tag_uid"])
            self._check_uid_free(s, uid)
            u = User(tag_uid=uid, name=data["name"].strip(), department=data.get("department", ""),
                     team=data.get("team", ""), note=data.get("note", ""), active=data.get("active", True))
            s.add(u)
            self._forget_unknown(s, uid)
            s.flush()
            out = user_dict(u)
        self.hub.publish("user_changed", id=out["id"])
        return out

    def update_user(self, user_id: int, data: dict) -> dict:
        with self.session() as s:
            u = s.get(User, user_id)
            if not u:
                raise ServiceError("not_found", "ユーザーが見つかりません", 404)
            if "tag_uid" in data and data["tag_uid"] is not None:
                uid = normalize_uid(data["tag_uid"])
                self._check_uid_free(s, uid, ("user", u.id))
                u.tag_uid = uid
                self._forget_unknown(s, uid)
            for f in ("name", "department", "team", "note", "active"):
                if data.get(f) is not None:
                    setattr(u, f, data[f].strip() if isinstance(data[f], str) else data[f])
            s.flush()
            out = user_dict(u)
        self.hub.publish("user_changed", id=user_id)
        return out

    def list_items(self, q: str = "", status: str = "") -> list[dict]:
        with self.session() as s:
            stmt = select(Item).order_by(Item.name)
            if q:
                like = f"%{q}%"
                stmt = stmt.where(or_(Item.name.ilike(like), Item.asset_no.ilike(like), Item.category.ilike(like),
                                      Item.location.ilike(like), Item.tag_uid.ilike(like)))
            items = list(s.scalars(stmt))
            open_loans = {l.item_id: l for l in s.scalars(select(Loan).where(Loan.ended_at.is_(None)))}
            out = []
            for i in items:
                l = open_loans.get(i.id)
                st = "inactive" if not i.active else ("on_loan" if l else "available")
                if status and st != status:
                    continue
                loan = {"loan_id": l.id, "user_id": l.user_id, "user_name": l.user.name,
                        "user_team": l.user.team, "started_at": iso(l.started_at)} if l else None
                out.append({**item_dict(i), "status": st, "loan": loan})
            return out

    def create_item(self, data: dict) -> dict:
        with self.session() as s:
            uid = normalize_uid(data["tag_uid"])
            self._check_uid_free(s, uid)
            asset_no = data.get("asset_no", "").strip()
            self._check_asset_no_free(s, asset_no)
            i = Item(tag_uid=uid, name=data["name"].strip(), asset_no=asset_no, category=data.get("category", ""),
                     location=data.get("location", ""), note=data.get("note", ""), active=data.get("active", True))
            s.add(i)
            self._forget_unknown(s, uid)
            s.flush()
            out = item_dict(i)
        self.hub.publish("item_changed", id=out["id"])
        return out

    def update_item(self, item_id: int, data: dict) -> dict:
        with self.session() as s:
            i = s.get(Item, item_id)
            if not i:
                raise ServiceError("not_found", "備品が見つかりません", 404)
            if "tag_uid" in data and data["tag_uid"] is not None:
                uid = normalize_uid(data["tag_uid"])
                self._check_uid_free(s, uid, ("item", i.id))
                i.tag_uid = uid
                self._forget_unknown(s, uid)
            if data.get("asset_no") is not None:
                self._check_asset_no_free(s, data["asset_no"].strip(), i.id)
            for f in ("name", "asset_no", "category", "location", "note", "active"):
                if data.get(f) is not None:
                    setattr(i, f, data[f].strip() if isinstance(data[f], str) else data[f])
            s.flush()
            out = item_dict(i)
        self.hub.publish("item_changed", id=item_id)
        return out

    def _check_asset_no_free(self, s: Session, asset_no: str, own_id: int | None = None) -> None:
        if not asset_no:  # blank is allowed for any number of items
            return
        found = s.scalar(select(Item).where(Item.asset_no == asset_no))
        if found and found.id != own_id:
            label = self.get_fields()["items"]["asset_no"]["label"]
            raise ServiceError("asset_no_in_use", f"この{label}は備品「{found.name}」に登録済みです", 409)

    @staticmethod
    def _forget_unknown(s: Session, uid: str) -> None:
        tag = s.get(UnknownTag, uid)
        if tag:
            s.delete(tag)

    # ------------------------------------------------------------------ loans
    def list_loans(self, active: bool | None = None, user_id: int | None = None, item_id: int | None = None,
                   date_from: datetime | None = None, date_to: datetime | None = None,
                   limit: int = 200, offset: int = 0) -> dict:
        with self.session() as s:
            stmt = select(Loan)
            if active is True:
                stmt = stmt.where(Loan.ended_at.is_(None))
            elif active is False:
                stmt = stmt.where(Loan.ended_at.is_not(None))
            if user_id:
                stmt = stmt.where(Loan.user_id == user_id)
            if item_id:
                stmt = stmt.where(Loan.item_id == item_id)
            if date_from:
                stmt = stmt.where(or_(Loan.ended_at.is_(None), Loan.ended_at >= date_from))
            if date_to:
                stmt = stmt.where(Loan.started_at < date_to)
            total = s.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = s.scalars(stmt.order_by(Loan.started_at.desc(), Loan.id.desc()).limit(limit).offset(offset))
            return {"total": total, "loans": [loan_dict(l) for l in rows]}

    def loans_csv(self, **filters) -> str:
        data = self.list_loans(limit=100000, **filters)["loans"]
        fields = self.get_fields()
        asset_no, team = fields["items"]["asset_no"], fields["users"]["team"]
        # (header, value) pairs; the customizable ones follow the site's labels and are left out when unused.
        cols = [("貸出ID", lambda l: l["id"]), ("備品", lambda l: l["item_name"])]
        if asset_no["enabled"]:
            cols.append((asset_no["label"], lambda l: l["item_asset_no"]))
        cols.append(("ユーザー", lambda l: l["user_name"]))
        if team["enabled"]:
            cols.append((team["label"], lambda l: l["user_team"]))
        cols += [("端末", lambda l: l["device_id"] or ""), ("開始(UTC)", lambda l: l["started_at"]),
                 ("終了(UTC)", lambda l: l["ended_at"] or ""), ("終了理由", lambda l: l["end_reason"] or "")]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([h for h, _ in cols])
        for l in data:
            w.writerow([get(l) for _, get in cols])
        return "\ufeff" + buf.getvalue()  # BOM so Excel opens it as UTF-8

    def close_loan(self, loan_id: int) -> dict:
        with self.session() as s:
            l = s.get(Loan, loan_id)
            if not l:
                raise ServiceError("not_found", "貸出が見つかりません", 404)
            if l.ended_at is not None:
                raise ServiceError("already_closed", "この貸出はすでに終了しています", 409)
            l.ended_at, l.end_reason = utcnow(), "admin"
            s.flush()
            out = loan_dict(l)
        self.hub.publish("return", item={"id": out["item_id"], "name": out["item_name"]},
                         user={"id": out["user_id"], "name": out["user_name"]}, by_admin=True)
        return out

    def recent_activity(self, limit: int = 20) -> list[dict]:
        with self.session() as s:
            started = s.scalars(select(Loan).order_by(Loan.started_at.desc()).limit(limit)).all()
            ended = s.scalars(select(Loan).where(Loan.ended_at.is_not(None))
                              .order_by(Loan.ended_at.desc()).limit(limit)).all()
            def who(l: Loan) -> dict:
                return {"item_name": l.item.name, "item_asset_no": l.item.asset_no,
                        "user_name": l.user.name, "user_team": l.user.team}

            events = []
            for l in started:
                events.append({"at": iso(l.started_at), "type": "checkout", **who(l)})
            for l in ended:
                if l.end_reason == "transfer":
                    continue  # shown via the new loan's checkout; avoid a duplicate line
                events.append({"at": iso(l.ended_at), "type": "return", **who(l),
                               "by_admin": l.end_reason == "admin"})
            events.sort(key=lambda e: e["at"], reverse=True)
            return events[:limit]

    def summary(self) -> dict:
        with self.session() as s:
            items_active = s.scalar(select(func.count()).select_from(Item).where(Item.active.is_(True)))
            on_loan = s.scalar(select(func.count()).select_from(Loan).where(Loan.ended_at.is_(None)))
            users_active = s.scalar(select(func.count()).select_from(User).where(User.active.is_(True)))
            unknown = s.scalar(select(func.count()).select_from(UnknownTag))
        return {"items_active": items_active, "on_loan": on_loan, "available": items_active - on_loan,
                "users_active": users_active, "unknown_tags": unknown}

    # ------------------------------------------------------------------ unknown tags
    def list_unknown(self) -> list[dict]:
        with self.session() as s:
            return [{"uid": t.uid, "device_id": t.device_id, "first_seen_at": iso(t.first_seen_at),
                     "last_seen_at": iso(t.last_seen_at)}
                    for t in s.scalars(select(UnknownTag).order_by(UnknownTag.last_seen_at.desc()))]

    def delete_unknown(self, uid: str) -> None:
        with self.session() as s:
            tag = s.get(UnknownTag, normalize_uid(uid))
            if tag:
                s.delete(tag)

    # ------------------------------------------------------------------ admins
    def ensure_admin(self, username: str, password: str) -> bool:
        """Create the first admin if none exists. Returns True when one was created."""
        with self.session() as s:
            if s.scalar(select(func.count()).select_from(Admin)):
                return False
            s.add(Admin(username=username, password_hash=hash_password(password)))
            return True

    def login(self, username: str, password: str) -> int | None:
        with self.session() as s:
            a = s.scalar(select(Admin).where(Admin.username == username))
            return a.id if a and verify_password(password, a.password_hash) else None

    def admin_name(self, admin_id: int) -> str | None:
        with self.session() as s:
            a = s.get(Admin, admin_id)
            return a.username if a else None

    def change_password(self, admin_id: int, current: str, new: str) -> None:
        if len(new) < 8:
            raise ServiceError("weak_password", "パスワードは8文字以上にしてください")
        with self.session() as s:
            a = s.get(Admin, admin_id)
            if not a or not verify_password(current, a.password_hash):
                raise ServiceError("wrong_password", "現在のパスワードが違います", 403)
            a.password_hash = hash_password(new)

    def reset_password(self, new: str, username: str | None = None) -> str:
        """Set a password without the current one (CLI only). With no username,
        the sole admin is reset. Returns the username that was reset."""
        if len(new) < 8:
            raise ServiceError("weak_password", "パスワードは8文字以上にしてください")
        with self.session() as s:
            if username:
                a = s.scalar(select(Admin).where(Admin.username == username))
                if not a:
                    raise ServiceError("not_found", f"管理者 '{username}' はいません", 404)
            else:
                admins = s.scalars(select(Admin)).all()
                if len(admins) != 1:
                    names = ", ".join(x.username for x in admins) or "なし"
                    raise ServiceError("ambiguous", f"--username で指定してください（管理者: {names}）")
                a = admins[0]
            a.password_hash = hash_password(new)
            return a.username
