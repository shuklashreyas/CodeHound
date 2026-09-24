"""Reject oversized submission bodies before JSON parsing."""

from fastapi import HTTPException
from starlette.responses import JSONResponse


class SubmissionBodyLimit:
    def __init__(self, app, maximum=128 * 1024):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or not scope.get("path", "").startswith("/api/verifications")
        ):
            return await self.app(scope, receive, send)
        length = dict(scope.get("headers", [])).get(b"content-length")
        if length:
            try:
                too_large = int(length) > self.maximum
            except ValueError:
                return await JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)(
                    scope, receive, send
                )
            if too_large:
                return await JSONResponse(
                    {"detail": "Submission body is too large."}, status_code=413
                )(scope, receive, send)
        total = 0

        async def bounded_receive():
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.maximum:
                    raise HTTPException(413, "Submission body is too large.")
            return message

        return await self.app(scope, bounded_receive, send)
