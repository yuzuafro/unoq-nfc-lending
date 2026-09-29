import hashlib
import hmac
import secrets
import threading
import time

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), dklen=32, **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    """In-memory admin sessions. A restart logs admins out, which is acceptable here."""

    def __init__(self, ttl_seconds: int):
        self.ttl = ttl_seconds
        self._sessions: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def create(self, admin_id: int) -> str:
        token = new_token()
        with self._lock:
            self._sessions[token] = (admin_id, time.time() + self.ttl)
        return token

    def get(self, token: str | None) -> int | None:
        if not token:
            return None
        with self._lock:
            entry = self._sessions.get(token)
            if not entry:
                return None
            admin_id, expires = entry
            if expires < time.time():
                del self._sessions[token]
                return None
            return admin_id

    def delete(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)
