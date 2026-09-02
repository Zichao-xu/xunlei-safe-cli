from __future__ import annotations

import hashlib
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .errors import VerificationRequired, XLCLIError
from .models import Token
from .storage import Settings, TokenStore

CLIENT_ID = "Xp6vsxz_7IYVw2BB"
CLIENT_SECRET = "Xp6vsy4tN9toTVdMSpomVdXpRmES"
CLIENT_VERSION = "8.31.0.9726"
PACKAGE_NAME = "com.xunlei.downloadprovider"
APP_ID = "40"
APP_KEY = "34a062aaa22f906fca4fefe9fb3a3021"
ALGORITHMS = (
    "9uJNVj/wLmdwKrJaVj/omlQ",
    "Oz64Lp0GigmChHMf/6TNfxx7O9PyopcczMsnf",
    "Eb+L7Ce+Ej48u",
    "jKY0",
    "ASr0zCl6v8W4aidjPK5KHd1Lq3t+vBFf41dqv5+fnOd",
    "wQlozdg6r1qxh0eRmt3QgNXOvSZO6q/GXK",
    "gmirk+ciAvIgA/cxUUCema47jr/YToixTT+Q6O",
    "5IiCoM9B1/788ntB",
    "P07JH0h6qoM6TSUAK2aL9T5s2QBVeY9JWvalf",
    "+oK0AN",
)
USER_AGENT = (
    "ANDROID-com.xunlei.downloadprovider/8.31.0.9726 netWorkType/5G "
    "appid/40 deviceName/Xiaomi_M2004J7AC deviceModel/M2004J7AC "
    "OSVersion/12 protocolVersion/301 platformVersion/10 sdkVersion/512000 "
    "Oauth2Client/0.9 (Linux 4_14_186-perf) (JAVA 0)"
)
DOWNLOAD_USER_AGENT = (
    "Dalvik/2.1.0 (Linux; U; Android 12; M2004J7AC Build/SP1A.210812.016)"
)
AUTH_ORIGIN = "https://xluser-ssl.xunlei.com"
ALLOWED_HOSTS = frozenset({"xluser-ssl.xunlei.com", "api-pan.xunlei.com"})


def _md5(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()


class Auth:
    def __init__(
        self,
        settings: Settings | None = None,
        tokens: TokenStore | None = None,
        client: httpx.Client | None = None,
        auth_origin: str = AUTH_ORIGIN,
        allowed_hosts: frozenset[str] = ALLOWED_HOSTS,
    ) -> None:
        self.settings = settings or Settings()
        self.tokens = tokens or TokenStore()
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(30), follow_redirects=False
        )
        self.auth_origin = auth_origin.rstrip("/")
        self.allowed_hosts = allowed_hosts
        self.device_id = self.settings.device_id()
        self.captcha_token = ""
        self._token: Token | None = None

    def _check_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in self.allowed_hosts:
            raise XLCLIError(
                f"安全策略阻止访问非迅雷官方地址：{parsed.hostname or url}"
            )

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "application/json;charset=UTF-8",
            "x-device-id": self.device_id,
            "x-client-id": CLIENT_ID,
            "x-client-version": CLIENT_VERSION,
        }

    def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        self._check_url(url)
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        response = self.client.request(method, url, headers=headers, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if data.get("error") == "review_panel":
            raw_url = str(data.get("reviewurl") or "")
            sep = "&" if "?" in raw_url else "?"
            review_url = f"{raw_url}{sep}deviceid={self.device_sign()}"
            raise VerificationRequired(review_url, str(data.get("creditkey") or ""))
        if response.status_code >= 400:
            error = data.get("error") or f"HTTP {response.status_code}"
            description = data.get("error_description") or data.get("message") or ""
            raise XLCLIError(f"迅雷接口错误：{error} {description}".strip())
        if data.get("error") and data.get("error") != "success":
            raise XLCLIError(
                f"迅雷接口错误：{data.get('error')} "
                f"{data.get('error_description', '')}".strip()
            )
        return data

    def device_sign(self) -> str:
        sha1 = hashlib.sha1(
            f"{self.device_id}{PACKAGE_NAME}{APP_ID}{APP_KEY}".encode(),
            usedforsecurity=False,
        ).hexdigest()
        return f"div101.{self.device_id}{_md5(sha1)}"

    def captcha_sign(self) -> tuple[str, str]:
        timestamp = str(int(time.time() * 1000))
        value = f"{CLIENT_ID}{CLIENT_VERSION}{PACKAGE_NAME}{self.device_id}{timestamp}"
        for algorithm in ALGORITHMS:
            value = _md5(value + algorithm)
        return timestamp, f"1.{value}"

    def refresh_captcha(
        self, action: str, username: str = "", user_id: str = ""
    ) -> None:
        timestamp, signature = self.captcha_sign()
        meta = {
            "client_version": CLIENT_VERSION,
            "package_name": PACKAGE_NAME,
            "timestamp": timestamp,
            "captcha_sign": signature,
        }
        if user_id:
            meta["user_id"] = user_id
        elif username:
            if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", username):
                meta["email"] = username
            elif 11 <= len(username) <= 18:
                meta["phone_number"] = username
            else:
                meta["username"] = username
        data = self.request(
            "POST",
            f"{self.auth_origin}/v1/shield/captcha/init",
            json={
                "action": action,
                "captcha_token": self.captcha_token,
                "client_id": CLIENT_ID,
                "device_id": self.device_id,
                "meta": meta,
                "redirect_uri": "xlaccsdk01://xunlei.com/callback?state=harbor",
            },
        )
        if data.get("url"):
            raise VerificationRequired(str(data["url"]))
        self.captcha_token = str(data.get("captcha_token") or "")
        if not self.captcha_token:
            raise XLCLIError("迅雷未返回验证码令牌")

    def login_password(
        self, username: str, password: str, credit_key: str = ""
    ) -> Token:
        data = self.request(
            "POST",
            f"{self.auth_origin}/xluser.core.login/v3/login",
            headers={
                "User-Agent": "android-ok-http-client/xl-acc-sdk/version-5.0.12.512000"
            },
            json={
                "protocolVersion": "301",
                "sequenceNo": "1000012",
                "platformVersion": "10",
                "isCompressed": "0",
                "appid": APP_ID,
                "clientVersion": CLIENT_VERSION,
                "peerID": "0" * 32,
                "appName": f"ANDROID-{PACKAGE_NAME}",
                "sdkVersion": "512000",
                "devicesign": self.device_sign(),
                "netWorkType": "WIFI",
                "providerName": "NONE",
                "deviceModel": "M2004J7AC",
                "deviceName": "Xiaomi_M2004J7AC",
                "OSVersion": "12",
                "creditkey": credit_key,
                "hl": "zh-CN",
                "userName": username,
                "passWord": password,
                "verifyKey": "",
                "verifyCode": "",
                "isMd5Pwd": "0",
            },
        )
        session_id = str(data.get("sessionID") or "")
        if not session_id:
            raise XLCLIError("登录失败：迅雷未返回 sessionID")
        self.refresh_captcha("POST:/v1/auth/signin/token", username=username)
        token_data = self.request(
            "POST",
            f"{self.auth_origin}/v1/auth/signin/token",
            headers={"X-Captcha-Token": self.captcha_token},
            json={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "provider": "access_end_point_token",
                "signin_token": session_id,
            },
        )
        token = Token.from_api(token_data)
        self.tokens.save(token)
        self.settings.set_username(username)
        self._token = token
        return token

    def login_refresh_token(self, refresh_token: str) -> Token:
        token = self._refresh(refresh_token)
        self.tokens.save(token)
        self._token = token
        return token

    def _refresh(self, refresh_token: str) -> Token:
        data = self.request(
            "POST",
            f"{self.auth_origin}/v1/auth/token",
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
        )
        token = Token.from_api(data)
        if not token.access_token:
            raise XLCLIError("刷新登录失败：迅雷未返回 access token")
        if not token.refresh_token:
            token.refresh_token = refresh_token
        return token

    def token(self) -> Token:
        token = self._token or self.tokens.load()
        if token is None:
            raise XLCLIError("尚未登录，请先运行 xl login")
        if token.expired:
            if not token.refresh_token:
                raise XLCLIError("登录已过期，请重新登录")
            token = self._refresh(token.refresh_token)
            self.tokens.save(token)
        self._token = token
        return token

    def authorization(self) -> str:
        token = self.token()
        return f"{token.token_type} {token.access_token}"

    def ensure_captcha(self, action: str) -> str:
        if not self.captcha_token:
            self.refresh_captcha(action, user_id=self.token().user_id)
        return self.captcha_token

    def logout(self) -> None:
        self.tokens.clear()
        self._token = None

    def close(self) -> None:
        self.client.close()
