"""Rate limits and streamed request-body bounds before endpoint parsing."""
from starlette.responses import JSONResponse
from pai.kernel.errors import AuthError
from pai.platform.limits import consume, enabled, usage_subject

class RequestLimitsMiddleware:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith("/health/"):
            return await self.app(scope, receive, send)
        settings = self.settings
        maximum = settings.document_max_bytes + 65536
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = maximum + 1
        if length > maximum or length < 0:
            return await JSONResponse({"error": {"code": "UPLOAD_TOO_LARGE", "message": "Request body exceeds the upload limit."}}, status_code=413)(scope, receive, send)
        try:
            if enabled(settings):
                ip = (scope.get("client") or ("unknown",))[0]
                await consume(settings, [("requests", ip, 1, settings.request_limit_per_minute, 60)])
            count = 0
            async def bounded_receive():
                nonlocal count
                message = await receive()
                if message["type"] == "http.request":
                    count += len(message.get("body", b""))
                    if count > maximum:
                        # FastAPI preserves HTTPException status during multipart parsing.
                        from starlette.exceptions import HTTPException
                        raise HTTPException(status_code=413, detail="Request body exceeds upload limit.")
                return message
            token = usage_subject.set("anonymous")
            try:
                await self.app(scope, bounded_receive, send)
            finally:
                usage_subject.reset(token)
        except AuthError as exc:
            await JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code,
                headers={"Retry-After": str(getattr(exc, "retry_after", 5))})(scope, receive, send)
