"""API-key guard for mutation endpoints.

When ``CORTEX_API_KEY`` is set (see ``app.core.config.Settings``), every
mutation route must carry ``X-API-Key: <key>``; anything else gets 401.
When it is unset (the default), the guard is inert so local development and
the demo sandbox keep working without a key. Read-only GET routes are never
gated — this is a write guard, not an auth system.
"""

from fastapi import Header, HTTPException

from app.core.config import settings


async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    configured = (settings.cortex_api_key or "").strip()
    if not configured:
        return
    if x_api_key is None or x_api_key != configured:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
