"""Explicit, cached downloads from known tournament result publishers."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

MAX_BYTES = 8 * 1024 * 1024
HOSTS = {"uschess.org", "www.uschess.org", "new.uschess.org", "ratings.uschess.org",
         "fide.com", "www.fide.com", "ratings.fide.com", "chess-results.com", "www.chess-results.com"}
CATALOG = [
    {"name": "FIDE Dutch rules · February 2026", "url": "https://handbook.fide.com/chapter/C0403202602", "kind": "rules"},
    {"name": "US Chess rulebook · January 2026", "url": "https://new.uschess.org/sites/default/files/media/documents/us-chess-rule-book-online-2026.pdf", "kind": "rules"},
    {"name": "FIDE tournament report formats", "url": "https://tec.fide.com/trf-2026/", "kind": "format"},
    {"name": "US Chess API limits · November 2026", "url": "https://new.uschess.org/news/us-chess-api-limits-lowered", "kind": "import"},
]
_cache = {}


def resolve_source(url, allow_public=False):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or (not allow_public and parts.hostname not in HOSTS) or parts.username or parts.password or parts.port not in {None, 443}:
        raise ValueError("Use a public HTTPS standings URL without credentials or a custom port." if allow_public else "Use an HTTPS result URL from uschess.org, ratings.fide.com, fide.com or chess-results.com. Other publishers can be followed in Follow tournament or imported as files.")
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("The source must resolve to public Internet addresses.")
    address = next((a[4][0] for a in addresses if a[0] == socket.AF_INET), addresses[0][4][0])
    return parts, address


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, timeout=12, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        # Pin the validated address and retain hostname-based TLS verification.
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def fetch_source(url, allow_public=False, max_age=600):
    if not isinstance(url, str) or len(url) > 3000:
        raise ValueError("Provide a valid result URL.")
    cached = _cache.get(url)
    if cached and time.monotonic() - cached[0] < max_age:
        return cached[1], cached[2], cached[3], True
    original = url
    for _ in range(4):
        parts, address = resolve_source(url, allow_public=allow_public)
        connection = _PinnedHTTPS(parts.hostname, address)
        try:
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            connection.request("GET", path, headers={"User-Agent": "SavvyPairings/0.1 (user-initiated results import)", "Accept": "text/html,text/plain,application/json,application/octet-stream"})
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location:
                    raise ValueError("The source returned an empty redirect.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError(f"Source returned HTTP {response.status}. Download an export from the organizer and import the file.")
            length = response.getheader("Content-Length")
            if length and int(length) > MAX_BYTES:
                raise ValueError("Source is larger than the 8 MB import limit.")
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("Source is larger than the 8 MB import limit.")
            content_type = response.getheader("Content-Type", "text/plain")
            if len(_cache) >= 20:
                _cache.pop(next(iter(_cache)))
            _cache[original] = (time.monotonic(), data, url, content_type)
            return data, url, content_type, False
        finally:
            connection.close()
    raise ValueError("Too many source redirects. Import a downloaded export instead.")
