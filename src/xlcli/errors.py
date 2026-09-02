class XLCLIError(Exception):
    """Expected, user-facing error."""


class VerificationRequired(XLCLIError):
    def __init__(self, url: str, credit_key: str = "") -> None:
        super().__init__("需要完成迅雷的设备验证")
        self.url = url
        self.credit_key = credit_key


class DownloadLinkExpired(XLCLIError):
    """The cloud download URL must be fetched again."""
