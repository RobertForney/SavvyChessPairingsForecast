"""Explicit single-workspace HTTPS deployment settings; loopback by default."""
from __future__ import annotations

import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ServerConfig:
    public_origin: str | None = None
    access_key: str | None = None

    def __post_init__(self):
        if self.public_origin:
            parts = urlsplit(self.public_origin)
            if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                    or parts.path not in {"", "/"} or parts.query or parts.fragment):
                raise ValueError("The public origin must be an HTTPS origin, without credentials, path or query.")
            try:
                parts.hostname.encode("ascii")
                port = parts.port
            except (UnicodeError, ValueError) as exc:
                raise ValueError("Use a valid ASCII HTTPS hostname and port.") from exc
            hostname = parts.hostname.lower()
            # URL authority syntax requires brackets around an IPv6 literal.
            authority = f"[{hostname}]" if ":" in hostname else hostname
            netloc = authority if port in {None, 443} else f"{authority}:{port}"
            object.__setattr__(self, "public_origin", "https://" + netloc)
            if not self.access_key or len(self.access_key) < 32:
                raise ValueError("An HTTPS deployment requires a tournament access key of at least 32 characters.")
        if self.access_key is not None and len(self.access_key) < 32:
            raise ValueError("The tournament access key must have at least 32 characters.")

    def hosts(self, port):
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.public_origin:
            parts = urlsplit(self.public_origin)
            allowed.add(parts.netloc)
            if parts.port is None:
                allowed.add(parts.netloc + ":443")
        return allowed

    def origins(self, port):
        allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        if self.public_origin:
            allowed.add(self.public_origin)
        return allowed

    def authorized(self, header):
        if self.access_key is None:
            return True
        prefix, _, supplied = str(header or "").partition(" ")
        return prefix.lower() == "bearer" and hmac.compare_digest(supplied.encode(), self.access_key.encode())

    def validate_bind(self, host):
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if not loopback and not (self.public_origin and self.access_key):
            raise ValueError("A non-loopback listener requires an explicit HTTPS public origin and tournament access key.")


def initialize_access_key(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(secrets.token_urlsafe(32) + "\n")
    except FileExistsError:
        pass
    if len(path.read_text(encoding="utf-8").strip()) < 32:
        raise ValueError("Existing access-key file is too short; choose another file or repair it explicitly.")
    return path
