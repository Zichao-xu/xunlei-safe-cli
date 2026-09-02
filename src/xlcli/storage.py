from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from .errors import XLCLIError
from .models import Token

SERVICE = "com.local.xlcli.xunlei"
ACCOUNT = "default"


class Settings:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or (Path.home() / "Library" / "Application Support" / "xlcli")
        self.path = self.root / "settings.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise XLCLIError(f"无法读取设置：{exc}") from exc

    def save(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def device_id(self) -> str:
        data = self.load()
        value = str(data.get("device_id") or "")
        if len(value) != 32:
            seed = f"{uuid.getnode()}:{uuid.uuid4()}"
            value = hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest()
            data["device_id"] = value
            self.save(data)
        return value

    def set_username(self, username: str) -> None:
        data = self.load()
        data["username"] = username
        self.save(data)

    def username(self) -> str:
        return str(self.load().get("username") or "")


class TokenStore:
    """Stores tokens in the user's login Keychain, never in a project file."""

    def __init__(self, backend: Any | None = None) -> None:
        self._backend = backend

    @property
    def backend(self) -> Any:
        if self._backend is None:
            try:
                import keyring
            except ImportError as exc:
                raise XLCLIError("缺少 keyring，无法安全保存登录令牌") from exc
            self._backend = keyring
        return self._backend

    def load(self) -> Token | None:
        raw = self.backend.get_password(SERVICE, ACCOUNT)
        if not raw:
            return None
        try:
            return Token.from_dict(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise XLCLIError("钥匙串中的迅雷令牌格式无效") from exc

    def save(self, token: Token) -> None:
        self.backend.set_password(
            SERVICE,
            ACCOUNT,
            json.dumps(token.as_dict(), separators=(",", ":")),
        )

    def clear(self) -> None:
        try:
            self.backend.delete_password(SERVICE, ACCOUNT)
        except self.backend.errors.PasswordDeleteError:
            pass
