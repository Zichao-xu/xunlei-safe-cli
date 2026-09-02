# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub Security Advisories
instead of opening a public issue. Do not include real Xunlei credentials,
tokens, download URLs, or private file names in a report.

## Credential handling

- Passwords and imported refresh tokens are read through hidden terminal input.
- Access and refresh tokens are stored in the macOS login Keychain.
- The local settings file contains only a device identifier and an optional
  non-sensitive username.
- Authenticated API requests are restricted to allowlisted Xunlei HTTPS hosts.
- Bearer tokens are not forwarded to download CDN hosts.

The client ID, client secret, application key, and signing constants embedded
in `auth.py` are compatibility identifiers extracted from the public client
protocol. They are not maintainer credentials and do not grant access to a
user account without that user's own authentication.
