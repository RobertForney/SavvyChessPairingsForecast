"""Deployment and browser-facing platform boundaries, using the real HTTP handler."""
from copy import deepcopy
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from xml.etree import ElementTree as ET

from app.config import ServerConfig, initialize_access_key
from app.pairing import DEFAULT_RULES
from app.sample_data import SAMPLE_FILENAME, SAMPLE_PATH
from app.server import Store, make_handler


ACCESS_KEY = "test-only-tournament-access-key-0123456789"
PUBLIC_ORIGIN = "https://pairings.example.org"


def small_tournament():
    return {
        "name": "Private platform fixture", "section": "Open", "rounds": 3,
        "completedRounds": 0, "warnings": [],
        "players": [
            {"id": str(index), "name": f"Player {index}",
             "rating": 2100 - index * 100, "pairingNumber": index,
             "active": True, "results": []}
            for index in range(1, 5)
        ],
    }


class PlatformConfigTests(unittest.TestCase):
    def test_https_origin_normalizes_default_port_and_keeps_explicit_port(self):
        config = ServerConfig("https://PAIRINGS.example.org:443/", ACCESS_KEY)
        self.assertEqual(config.public_origin, PUBLIC_ORIGIN)
        self.assertEqual(config.hosts(8765), {
            "127.0.0.1:8765", "localhost:8765", "pairings.example.org", "pairings.example.org:443",
        })
        self.assertEqual(config.origins(8765), {
            "http://127.0.0.1:8765", "http://localhost:8765", PUBLIC_ORIGIN,
        })
        custom = ServerConfig("https://PAIRINGS.example.org:8443/", ACCESS_KEY)
        self.assertEqual(custom.public_origin, "https://pairings.example.org:8443")
        self.assertIn("pairings.example.org:8443", custom.hosts(8765))
        self.assertNotIn("pairings.example.org", custom.hosts(8765))

    def test_ipv6_public_origin_keeps_authority_brackets_and_port(self):
        for supplied, normalized, host in (
            ("https://[2001:DB8::1]:443/", "https://[2001:db8::1]", "[2001:db8::1]"),
            ("https://[2001:DB8::1]:8443/", "https://[2001:db8::1]:8443", "[2001:db8::1]:8443"),
        ):
            with self.subTest(origin=supplied):
                config = ServerConfig(supplied, ACCESS_KEY)
                self.assertEqual(config.public_origin, normalized)
                self.assertIn(host, config.hosts(8765))
                self.assertIn(normalized, config.origins(8765))
        config = ServerConfig("https://[2001:db8::1]", ACCESS_KEY)
        self.assertIn("[2001:db8::1]:443", config.hosts(8765))

    def test_public_origin_rejects_non_https_credentials_paths_and_invalid_ports(self):
        invalid = (
            "http://pairings.example.org", "//pairings.example.org", "pairings.example.org",
            "https://user:password@pairings.example.org", "https://pairings.example.org/app",
            "https://pairings.example.org/?key=secret", "https://pairings.example.org/#section",
            "https://pairings.example.org:not-a-port", "https://pairings.example.org:65536",
            "https://", "https://p\N{LATIN SMALL LETTER A WITH DIAERESIS}irings.example.org",
        )
        for origin in invalid:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                ServerConfig(origin, ACCESS_KEY)

    def test_remote_origin_and_optional_local_access_key_require_long_secrets(self):
        for key in (None, "", "x" * 31):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ServerConfig(PUBLIC_ORIGIN, key)
        with self.assertRaises(ValueError):
            ServerConfig(access_key="x" * 31)
        self.assertTrue(ServerConfig().authorized(None))
        self.assertTrue(ServerConfig(access_key=ACCESS_KEY).authorized("Bearer " + ACCESS_KEY))

    def test_non_loopback_bind_requires_both_public_origin_and_access_key(self):
        local = ServerConfig()
        for host in ("127.0.0.1", "127.0.0.2", "::1", "localhost"):
            with self.subTest(host=host):
                local.validate_bind(host)
        for config in (local, ServerConfig(access_key=ACCESS_KEY)):
            for host in ("0.0.0.0", "::", "192.168.1.10", "director.example.org"):
                with self.subTest(config=config, host=host), self.assertRaises(ValueError):
                    config.validate_bind(host)
        remote = ServerConfig(PUBLIC_ORIGIN, ACCESS_KEY)
        for host in ("0.0.0.0", "::", "192.168.1.10"):
            with self.subTest(host=host):
                remote.validate_bind(host)

    def test_bearer_scheme_is_case_insensitive_but_secret_matches_exactly(self):
        config = ServerConfig(access_key=ACCESS_KEY)
        self.assertTrue(config.authorized("bEaReR " + ACCESS_KEY))
        for supplied in (None, "", ACCESS_KEY, "Basic " + ACCESS_KEY,
                         "Bearer wrong", "Bearer " + ACCESS_KEY + "x",
                         "Bearer  " + ACCESS_KEY, "Bearer " + ACCESS_KEY + " "):
            with self.subTest(header=supplied):
                self.assertFalse(config.authorized(supplied))

    def test_key_initialization_never_overwrites_an_existing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "access-key"
            self.assertEqual(initialize_access_key(path), path)
            original = path.read_bytes()
            self.assertGreaterEqual(len(original.decode("utf-8").strip()), 32)
            self.assertEqual(initialize_access_key(path), path)
            self.assertEqual(path.read_bytes(), original)
            path.write_text("too short\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "too short"):
                initialize_access_key(path)
            self.assertEqual(path.read_text(encoding="utf-8"), "too short\n")


class PlatformHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name) / "state.json"
        # Seed a tiny persisted section: Store runs its real forecast once,
        # without loading/forecasting the large sample for every HTTP test.
        path.write_text(json.dumps({"schemaVersion": 2, "tournament": small_tournament(),
                                    "rules": DEFAULT_RULES}), encoding="utf-8")
        cls.store = Store(path)
        handler = make_handler(cls.store, ServerConfig(PUBLIC_ORIGIN, ACCESS_KEY))
        handler.log_message = lambda self, *args: None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.worker = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.worker.join(timeout=2)
        cls.temp.cleanup()

    def request(self, path, body=None, method="GET", headers=None, authorized=False):
        supplied = {"Content-Type": "application/json", **(headers or {})}
        if authorized:
            supplied["Authorization"] = "Bearer " + ACCESS_KEY
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=15)
        try:
            connection.request(method, path,
                               body=json.dumps(body).encode("utf-8") if body is not None else None,
                               headers=supplied)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def live_snapshot(self):
        with self.store.lock:
            return (deepcopy(self.store.tournament), deepcopy(self.store.rules),
                    deepcopy(self.store.forecast), self.store.path.read_bytes())

    def test_health_and_shell_are_public_without_disclosing_tournament(self):
        status, headers, data = self.request("/api/health")
        health = json.loads(data)
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertTrue(health["authRequired"])
        self.assertTrue(health["forecastRequiresConnection"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("tournament", health)
        self.assertNotIn(ACCESS_KEY.encode(), data)
        status, headers, data = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"/manifest.webmanifest", data)
        self.assertIn(b"/simulation.js", data)
        self.assertNotIn(self.store.tournament["name"].encode(), data)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn("worker-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_private_reads_and_mutations_require_bearer_before_processing(self):
        before = self.live_snapshot()
        requests = (
            ("/api/state", "GET", None), ("/api/sample", "GET", None),
            ("/api/sample/source", "GET", None), ("/api/export", "GET", None),
            ("/api/simulation", "POST", {"source": "workspace", "throughRound": 0}),
            ("/api/tournament", "PUT", small_tournament()),
        )
        for path, method, body in requests:
            with self.subTest(path=path, method=method):
                status, headers, data = self.request(path, body, method)
                self.assertEqual(status, 401)
                self.assertEqual(json.loads(data)["code"], "AUTH_REQUIRED")
                self.assertTrue(headers["Content-Type"].startswith("application/json"))
                self.assertNotIn(ACCESS_KEY.encode(), data)
        for header in ("Basic " + ACCESS_KEY, "Bearer wrong"):
            with self.subTest(header=header):
                self.assertEqual(self.request("/api/state", headers={"Authorization": header})[0], 401)
        self.assertEqual(self.request("/api/state", authorized=True)[0], 200)
        self.assertEqual(self.live_snapshot(), before)

    def test_public_proxy_host_and_origin_are_explicitly_trusted(self):
        status, _, data = self.request("/api/state", authorized=True,
                                       headers={"Host": "pairings.example.org", "Origin": PUBLIC_ORIGIN})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["tournament"]["name"], "Private platform fixture")
        self.assertEqual(self.request("/api/health", headers={"Host": "pairings.example.org:443"})[0], 200)
        self.assertEqual(self.request("/api/state", authorized=True,
                                      headers={"Origin": f"http://localhost:{self.httpd.server_port}"})[0], 200)

    def test_unknown_host_or_origin_is_rejected_even_with_valid_secret(self):
        before = self.live_snapshot()
        for host in ("attacker.example.org", "pairings.example.org.attacker.example.org",
                     "pairings.example.org:8443"):
            with self.subTest(host=host):
                self.assertEqual(self.request("/api/state", authorized=True, headers={"Host": host})[0], 403)
                self.assertEqual(self.request("/api/health", headers={"Host": host})[0], 403)
        for origin in ("https://attacker.example.org", "http://pairings.example.org", "null"):
            with self.subTest(origin=origin):
                status, _, _ = self.request("/api/tournament", small_tournament(), "PUT",
                                            {"Origin": origin}, authorized=True)
                self.assertEqual(status, 403)
        self.assertEqual(self.live_snapshot(), before)

    def test_original_workbook_download_matches_bytes_metadata_and_hash(self):
        before = self.live_snapshot()
        status, headers, downloaded = self.request("/api/sample/source", authorized=True)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"],
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn(f'filename="{SAMPLE_FILENAME}"', headers["Content-Disposition"])
        original = SAMPLE_PATH.read_bytes()
        self.assertEqual(downloaded, original)
        status, _, data = self.request("/api/sample", authorized=True)
        sample = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(sample["metadata"]["sourceKind"], "original-xlsx")
        self.assertEqual(sample["metadata"]["filename"], SAMPLE_FILENAME)
        self.assertEqual(sample["metadata"]["bytes"], len(original))
        self.assertEqual(sample["metadata"]["sha256"], hashlib.sha256(downloaded).hexdigest())
        self.assertEqual(len(sample["tournament"]["players"]), 51)
        self.assertEqual(self.live_snapshot(), before)

    def test_http_replay_sources_do_not_replace_or_save_the_live_workspace(self):
        uploaded = small_tournament()
        uploaded["name"] = "Uploaded experiment"
        before = self.live_snapshot()
        experiments = (
            ({"source": "workspace", "throughRound": 0}, "Private platform fixture", 4, 1),
            ({"source": "upload", "tournament": uploaded, "throughRound": 0}, "Uploaded experiment", 4, 1),
            # This cutoff has known gaps and blocks quickly without asking for
            # a second large first-round search. The HTTP operation still succeeds.
            ({"source": "sample", "throughRound": 2}, None, 51, 3),
        )
        for body, expected_name, players, target in experiments:
            with self.subTest(source=body["source"]):
                status, headers, data = self.request("/api/simulation", body, "POST", authorized=True)
                replay = json.loads(data)
                self.assertEqual(status, 200, replay)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(replay["throughRound"], body["throughRound"])
                self.assertEqual(replay["targetRound"], target)
                self.assertEqual(len(replay["tournament"]["players"]), players)
                if expected_name:
                    self.assertEqual(replay["tournament"]["name"], expected_name)
                    self.assertEqual(replay["forecast"]["status"], "ok")
                else:
                    self.assertEqual(replay["forecast"]["status"], "blocked")
                self.assertEqual(self.live_snapshot(), before)

    def test_invalid_replay_requests_preserve_persisted_and_live_state(self):
        before = self.live_snapshot()
        for body in ({"source": "unknown"}, {"source": "workspace", "throughRound": -1},
                     {"source": "workspace", "throughRound": "one"}):
            with self.subTest(body=body):
                status, headers, data = self.request("/api/simulation", body, "POST", authorized=True)
                self.assertEqual(status, 400)
                self.assertTrue(headers["Content-Type"].startswith("application/json"))
                self.assertIn("error", json.loads(data))
                self.assertEqual(self.live_snapshot(), before)

    def test_manifest_and_public_shell_assets_have_usable_mime_types(self):
        status, headers, data = self.request("/manifest.webmanifest")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/manifest+json"))
        manifest = json.loads(data)
        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        icons = {(icon["purpose"], icon["sizes"]): icon for icon in manifest["icons"]}
        self.assertTrue({("any", "192x192"), ("any", "512x512"), ("maskable", "512x512")} <= icons.keys())
        for icon in manifest["icons"]:
            with self.subTest(icon=icon["src"]):
                status, headers, png = self.request(icon["src"])
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], "image/png")
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                width, height = struct.unpack(">II", png[16:24])
                self.assertEqual(f"{width}x{height}", icon["sizes"])
                self.assertEqual(png[25], 2, "Launcher PNGs must be opaque RGB images")
        for path, content_type in (
            ("/pwa.js", "application/javascript"), ("/sw.js", "application/javascript"),
            ("/simulation.js", "application/javascript"), ("/offline.html", "text/html"),
            ("/styles.css", "text/css"), ("/icons/icon.svg", "image/svg+xml"),
        ):
            with self.subTest(path=path):
                status, headers, data = self.request(path)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith(content_type), headers)
                self.assertTrue(data)
                self.assertEqual(headers["Cache-Control"], "no-store")
                if path.endswith(".svg"):
                    self.assertEqual(ET.fromstring(data).tag, "{http://www.w3.org/2000/svg}svg")
        status, headers, png = self.request("/icons/apple-touch-icon.png")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(struct.unpack(">II", png[16:24]), (180, 180))


if __name__ == "__main__":
    unittest.main()
