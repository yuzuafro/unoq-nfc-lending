import httpx


class MgmtUnavailable(Exception):
    """Management could not be reached or rejected the terminal; the terminal shows OFFLINE."""


class MgmtClient:
    def __init__(self, base_url: str, device_id: str, token: str, timeout: float = 3.0):
        self.device_id = device_id
        self._http = httpx.Client(base_url=base_url.rstrip("/") + "/api/v1", timeout=timeout,
                                  headers={"X-Device-Id": device_id, "Authorization": f"Bearer {token}"})

    def _post(self, path: str, body: dict) -> dict:
        try:
            r = self._http.post(path, json=body)
        except httpx.HTTPError as e:
            raise MgmtUnavailable(str(e)) from e
        if r.status_code >= 400:
            raise MgmtUnavailable(f"{path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def scan(self, uid: str) -> dict:
        return self._post("/scans", {"uid": uid})

    def touch(self, user_uid: str, item_uid: str) -> dict:
        return self._post("/touches", {"user_uid": user_uid, "item_uid": item_uid})

    def heartbeat(self, reader_ok: bool | None) -> None:
        self._post(f"/devices/{self.device_id}/heartbeat", {"reader_ok": reader_ok})
