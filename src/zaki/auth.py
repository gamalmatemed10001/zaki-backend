"""Single-user auth per spec §8: "Endpoint protected by a personal API
key/token (single-user system, no public signup)."
"""

import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from zaki.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]


def require_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    expected = settings.zaki_api_key.get_secret_value()
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-API-Key",
        )
