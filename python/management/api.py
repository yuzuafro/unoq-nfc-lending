import asyncio
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .service import Service, ServiceError

JST = timezone(timedelta(hours=9))
COOKIE = "nfc_session"

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------- schemas
class ScanIn(BaseModel):
    uid: str


class TouchIn(BaseModel):
    user_uid: str
    item_uid: str


class HeartbeatIn(BaseModel):
    reader_ok: bool | None = None


class UserIn(BaseModel):
    tag_uid: str
    name: str = Field(min_length=1, max_length=100)
    department: str = ""
    note: str = ""
    active: bool = True


class UserPatch(BaseModel):
    tag_uid: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    department: str | None = None
    note: str | None = None
    active: bool | None = None


class ItemIn(BaseModel):
    tag_uid: str
    name: str = Field(min_length=1, max_length=100)
    category: str = ""
    location: str = ""
    note: str = ""
    active: bool = True


class ItemPatch(BaseModel):
    tag_uid: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = None
    location: str | None = None
    note: str | None = None
    active: bool | None = None


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    current: str
    new: str


class DeviceIn(BaseModel):
    id: str
    name: str = ""


# ---------------------------------------------------------------- dependencies
def svc(request: Request) -> Service:
    return request.app.state.service


def require_device(request: Request, x_device_id: str = Header(default=""),
                   authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    if not svc(request).authenticate_device(x_device_id, token):
        raise HTTPException(401, detail={"code": "device_auth", "message": "端末の認証に失敗しました"})
    return x_device_id


def current_admin(request: Request) -> int | None:
    return request.app.state.sessions.get(request.cookies.get(COOKIE))


def require_admin(request: Request) -> int:
    admin_id = current_admin(request)
    if admin_id is None:
        raise HTTPException(401, detail={"code": "login_required", "message": "管理者ログインが必要です"})
    return admin_id


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ServiceError as e:
        raise HTTPException(e.status, detail={"code": e.code, "message": e.message})


def jst_day_start(d: date | None, plus_days: int = 0) -> datetime | None:
    if d is None:
        return None
    return datetime.combine(d + timedelta(days=plus_days), time(0), JST).astimezone(timezone.utc)


# ---------------------------------------------------------------- terminal
@router.post("/scans")
def scan(body: ScanIn, request: Request, device_id: str = Depends(require_device)):
    return call(svc(request).scan, device_id, body.uid)


@router.post("/touches")
def touch(body: TouchIn, request: Request, device_id: str = Depends(require_device)):
    return call(svc(request).touch, device_id, body.user_uid, body.item_uid)


@router.post("/devices/{device_id}/heartbeat")
def heartbeat(device_id: str, body: HeartbeatIn, request: Request, auth_id: str = Depends(require_device)):
    if device_id != auth_id:
        raise HTTPException(403, detail={"code": "device_mismatch", "message": "端末IDが一致しません"})
    svc(request).heartbeat(device_id, body.reader_ok)
    return {"ok": True}


# ---------------------------------------------------------------- public reads
@router.get("/summary")
def summary(request: Request):
    return {**svc(request).summary(), "recent": svc(request).recent_activity()}


@router.get("/items")
def list_items(request: Request, q: str = "", status: str = ""):
    return svc(request).list_items(q, status)


@router.get("/users")
def list_users(request: Request, q: str = ""):
    return svc(request).list_users(q)


def _loan_filters(active: bool | None, user_id: int | None, item_id: int | None,
                  date_from: date | None, date_to: date | None) -> dict:
    return {"active": active, "user_id": user_id, "item_id": item_id,
            "date_from": jst_day_start(date_from), "date_to": jst_day_start(date_to, 1)}


@router.get("/loans")
def list_loans(request: Request, active: bool | None = None, user_id: int | None = None,
               item_id: int | None = None, date_from: date | None = Query(None, alias="from"),
               date_to: date | None = Query(None, alias="to"),
               limit: int = Query(200, le=1000), offset: int = 0):
    return svc(request).list_loans(limit=limit, offset=offset,
                                   **_loan_filters(active, user_id, item_id, date_from, date_to))


@router.get("/loans.csv")
def loans_csv(request: Request, active: bool | None = None, user_id: int | None = None,
              item_id: int | None = None, date_from: date | None = Query(None, alias="from"),
              date_to: date | None = Query(None, alias="to")):
    body = svc(request).loans_csv(**_loan_filters(active, user_id, item_id, date_from, date_to))
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="loans.csv"'})


@router.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    hub = ws.app.state.hub
    q = hub.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=25)
            except asyncio.TimeoutError:
                event = {"type": "ping"}
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(q)


# ---------------------------------------------------------------- auth
@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    admin_id = svc(request).login(body.username, body.password)
    if admin_id is None:
        raise HTTPException(401, detail={"code": "bad_credentials", "message": "ユーザー名かパスワードが違います"})
    token = request.app.state.sessions.create(admin_id)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        max_age=request.app.state.sessions.ttl)
    return {"username": body.username}


@router.post("/auth/logout")
def logout(request: Request, response: Response):
    request.app.state.sessions.delete(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request):
    admin_id = current_admin(request)
    return {"username": svc(request).admin_name(admin_id) if admin_id else None}


@router.post("/auth/password")
def change_password(body: PasswordIn, request: Request, admin_id: int = Depends(require_admin)):
    call(svc(request).change_password, admin_id, body.current, body.new)
    return {"ok": True}


# ---------------------------------------------------------------- admin writes
@router.post("/users", status_code=201)
def create_user(body: UserIn, request: Request, _: int = Depends(require_admin)):
    return call(svc(request).create_user, body.model_dump())


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, request: Request, _: int = Depends(require_admin)):
    return call(svc(request).update_user, user_id, body.model_dump(exclude_unset=True))


@router.post("/items", status_code=201)
def create_item(body: ItemIn, request: Request, _: int = Depends(require_admin)):
    return call(svc(request).create_item, body.model_dump())


@router.patch("/items/{item_id}")
def update_item(item_id: int, body: ItemPatch, request: Request, _: int = Depends(require_admin)):
    return call(svc(request).update_item, item_id, body.model_dump(exclude_unset=True))


@router.post("/loans/{loan_id}/close")
def close_loan(loan_id: int, request: Request, _: int = Depends(require_admin)):
    return call(svc(request).close_loan, loan_id)


@router.post("/registration-sessions", status_code=201)
def start_registration(request: Request, _: int = Depends(require_admin)):
    return svc(request).registrations.start()


@router.get("/registration-sessions/{sid}")
def get_registration(sid: str, request: Request, _: int = Depends(require_admin)):
    s = svc(request).registrations.get(sid)
    if not s:
        raise HTTPException(404, detail={"code": "not_found", "message": "登録モードが見つかりません"})
    return s


@router.delete("/registration-sessions/{sid}")
def cancel_registration(sid: str, request: Request, _: int = Depends(require_admin)):
    svc(request).registrations.cancel(sid)
    return {"ok": True}


@router.get("/unknown-tags")
def list_unknown(request: Request, _: int = Depends(require_admin)):
    return svc(request).list_unknown()


@router.delete("/unknown-tags/{uid}")
def delete_unknown(uid: str, request: Request, _: int = Depends(require_admin)):
    call(svc(request).delete_unknown, uid)
    return {"ok": True}


@router.get("/devices")
def list_devices(request: Request, _: int = Depends(require_admin)):
    return svc(request).list_devices()


@router.post("/devices", status_code=201)
def create_device(body: DeviceIn, request: Request, _: int = Depends(require_admin)):
    token = call(svc(request).create_device, body.id, body.name)
    return {"id": body.id, "token": token}


@router.delete("/devices/{device_id}")
def delete_device(device_id: str, request: Request, _: int = Depends(require_admin)):
    call(svc(request).delete_device, device_id)
    return {"ok": True}


@router.get("/backup")
def download_backup(request: Request, _: int = Depends(require_admin)):
    backups = request.app.state.backups
    if backups is None:
        raise HTTPException(400, detail={"code": "not_sqlite", "message": "SQLite 以外の DB はその DB の機能でバックアップしてください"})
    path = backups.snapshot()
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
