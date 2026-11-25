"""The general publisher path retains the existing network isolation boundary."""
import socket
import unittest
from unittest.mock import patch

from app.sources import resolve_source


class LiveSourceNetworkTests(unittest.TestCase):
    def test_explicit_public_publisher_is_supported_only_on_the_follow_path(self):
        address = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("app.sources.socket.getaddrinfo", return_value=address):
            parts, pinned = resolve_source("https://club.example.org/standings", allow_public=True)
            self.assertEqual(parts.hostname, "club.example.org")
            self.assertEqual(pinned, "93.184.216.34")
            with self.assertRaises(ValueError):
                resolve_source("https://club.example.org/standings")

    def test_follow_sources_still_reject_private_mixed_dns_and_unsafe_urls(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        for addresses in ([private], [public, private]):
            with self.subTest(addresses=addresses), patch("app.sources.socket.getaddrinfo", return_value=addresses):
                with self.assertRaisesRegex(ValueError, "public"):
                    resolve_source("https://club.example.org/results", allow_public=True)
        for url in ("http://club.example.org/a", "https://user:secret@club.example.org/a", "https://club.example.org:8443/a"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                resolve_source(url, allow_public=True)
