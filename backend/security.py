from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status


def session_cookie_policy(request: Request) -> tuple[bool, str]:
    """Return cookie attributes that work locally and inside the HF iframe."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    proxy_scheme = forwarded_proto.split(",", maxsplit=1)[0].strip().lower()
    secure = request.url.scheme == "https" or proxy_scheme == "https"
    return secure, "none" if secure else "lax"


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 6, window_seconds: int = 10 * 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, client_key: str) -> None:
        now = time.time()
        with self._lock:
            attempts = self._attempts[client_key]
            while attempts and attempts[0] < now - self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.max_attempts:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many sign-in attempts. Try again in a few minutes.",
                )

    def record_failure(self, client_key: str) -> None:
        with self._lock:
            self._attempts[client_key].append(time.time())

    def clear(self, client_key: str) -> None:
        with self._lock:
            self._attempts.pop(client_key, None)


def passwords_match(provided: str, expected: str) -> bool:
    return hmac.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )


def _signing_key(password: str) -> bytes:
    return hashlib.sha256(
        f"vayuchat-session-v1:{password}".encode("utf-8")
    ).digest()


def create_session_token(password: str, ttl_seconds: int) -> str:
    payload = {
        "exp": int(time.time()) + ttl_seconds,
        "nonce": secrets.token_urlsafe(16),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=")
    signature = hmac.new(_signing_key(password), encoded, hashlib.sha256).digest()
    return ".".join(
        (
            encoded.decode("ascii"),
            base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
        )
    )


def verify_session_token(token: str, password: str) -> bool:
    try:
        encoded_text, signature_text = token.split(".", maxsplit=1)
        encoded = encoded_text.encode("ascii")
        expected = hmac.new(_signing_key(password), encoded, hashlib.sha256).digest()
        padded_signature = signature_text + "=" * (-len(signature_text) % 4)
        supplied = base64.urlsafe_b64decode(padded_signature)
        if not hmac.compare_digest(supplied, expected):
            return False
        padded_payload = encoded_text + "=" * (-len(encoded_text) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded_payload))
        return int(payload["exp"]) > int(time.time())
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")[:160]
    return hashlib.sha256(f"{host}:{user_agent}".encode("utf-8")).hexdigest()


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    forwarded_host = request.headers.get("x-forwarded-host")
    expected_host = forwarded_host or request.headers.get("host", "")
    if not expected_host or origin.split("://", maxsplit=1)[-1] != expected_host:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin request rejected.",
        )
