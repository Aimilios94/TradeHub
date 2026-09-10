"""Bearer-token auth for engine endpoints called by n8n / the owner's tools.

Plan §16: n8n → engine calls carry `Authorization: Bearer <ENGINE_API_TOKEN>`. `/health`, `/plugins`
and `/seed/*` stay open (read-only, no secrets); everything that touches market data, the DB or
the broker requires the token. If the token is not configured the engine refuses (fail safe).
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

_bearer = HTTPBearer(auto_error=False)


def require_token(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    expected = get_settings().engine_api_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ENGINE_API_TOKEN is not configured")
    if creds is None or creds.scheme.lower() != "bearer" or not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token")
