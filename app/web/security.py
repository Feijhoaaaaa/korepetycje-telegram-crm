from __future__ import annotations

from collections import defaultdict, deque
from math import ceil
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

MAX_JSON_BYTES = 64 * 1024


class RateLimiter:
    """Small in-process limiter; the launcher intentionally runs one API process."""

    def __init__(self) -> None:
        self._requests: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def retry_after(self, client: str, bucket: str, limit: int, window: int = 60) -> int | None:
        now = monotonic()
        requests = self._requests[(client, bucket)]
        while requests and requests[0] <= now - window:
            requests.popleft()
        if len(requests) >= limit:
            return max(1, ceil(window - (now - requests[0])))
        requests.append(now)
        return None


def _client_address(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("CF-Connecting-IP")
    return forwarded.strip() if forwarded and peer in {"127.0.0.1", "::1"} else peer


def _rate_bucket(request: Request) -> tuple[str, int]:
    path = request.url.path
    if path.endswith("/exports/send") or path.endswith("/exports.xlsx") or "/payments/receipts/" in path:
        return "sensitive", 10
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        return "mutation", 60
    return "read", 120


def _security_headers(response: Response, *, api: bool) -> None:
    response.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; script-src 'self' https://telegram.org; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'"
    ))
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=()")
    if api:
        response.headers.setdefault("Cache-Control", "no-store")


def install_security_middleware(app: FastAPI) -> None:
    limiter = RateLimiter()

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        is_api = request.url.path.startswith("/api/")
        response: Response
        if is_api:
            if request.method not in {"GET", "HEAD", "OPTIONS"} and request.headers.get(
                "content-type", ""
            ).partition(";")[0].strip().lower() == "application/json":
                declared = request.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_JSON_BYTES:
                    response = JSONResponse({"detail": "request_too_large"}, status_code=413)
                    _security_headers(response, api=True)
                    return response
                if len(await request.body()) > MAX_JSON_BYTES:
                    response = JSONResponse({"detail": "request_too_large"}, status_code=413)
                    _security_headers(response, api=True)
                    return response

            bucket, limit = _rate_bucket(request)
            retry_after = limiter.retry_after(_client_address(request), bucket, limit)
            if retry_after is not None:
                response = JSONResponse(
                    {"detail": "rate_limit_exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
                _security_headers(response, api=True)
                return response

        response = await call_next(request)
        _security_headers(response, api=is_api)
        return response
