from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any


@dataclass(slots=True)
class Token:
    access_token: str
    refresh_token: str
    user_id: str
    expires_at: int
    token_type: str = "Bearer"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Token:
        expires_in = int(data.get("expires_in") or 7200)
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            user_id=str(data.get("user_id") or data.get("sub") or ""),
            expires_at=int(time()) + expires_in - 300,
            token_type=str(data.get("token_type") or "Bearer"),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Token:
        return cls(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            user_id=str(data.get("user_id") or ""),
            expires_at=int(data.get("expires_at") or 0),
            token_type=str(data.get("token_type") or "Bearer"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "user_id": self.user_id,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
        }

    @property
    def expired(self) -> bool:
        return self.expires_at <= int(time())


@dataclass(slots=True)
class DriveFile:
    id: str
    name: str
    size: int
    kind: str
    download_url: str = ""
    md5: str = ""


@dataclass(slots=True)
class OfflineTask:
    id: str
    name: str
    status: str
    progress: float
    file_id: str = ""
    message: str = ""
