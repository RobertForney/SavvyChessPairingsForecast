import copy
import http.client
import json
import tempfile
import threading
import unittest
import csv
import io
from pathlib import Path
from unittest.mock import patch

from http.server import ThreadingHTTPServer
from app.demo import demo_tournament
from app.importers import parse_text
from app.server import Store, make_handler
from app.sources import resolve_source, fetch_source
from app.validation import validate_tournament
from app.pairing import forecast, DEFAULT_RULES
from app.dutch import availability


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / "state.json")
        handler = make_handler(cls.store)
        handler.log_message = lambda self, *args: None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.worker = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.temp.cleanup()

    def setUp(self):
        with self.store.lock:
            self.store.apply(demo_tournament(), DEFAULT_RULES)

    def request(self, path, body=None, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        data = json.dumps(body).encode() if body is not None else None
        conn.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        response = conn.getresponse()
        content = response.read()
        result = response.status, dict(response.getheaders()), content
        conn.close()
        return result

    def test_home_and_assets_serve_without_dependencies(self):
        for path, expected in (("/", b"Savvy Pairings"), ("/app.js", b"use strict"), ("/styles.css", b"sidebar")):
            status, headers, data = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn(expected, data)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_state_contains_complete_valid_forecast(self):
        status, _, data = self.request("/api/state")
        state = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(state["forecast"]["status"], "ok")
        self.assertEqual(len(state["forecast"]["pairings"]), 6)

    def test_preview_does_not_replace_active_tournament(self):
        status, _, data = self.request("/api/import/text", {"text": "id,name,rating\n1,Ada,1900\n2,Ben,1800\n", "filename": "example.csv"}, "POST")
        self.assertEqual(status, 200)
        imported = json.loads(data)["tournament"]
        self.assertEqual(len(imported["players"]), 2)
        self.assertEqual(len(self.store.tournament["players"]), 12)
        status, _, data = self.request("/api/tournament", imported, "PUT")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(data)["tournament"]["players"]), 2)

    def test_json_export_roundtrips_full_state_without_applying(self):
        status, headers, data = self.request("/api/export?format=json")
        imported = parse_text(data.decode(), "export.json")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(imported["players"], self.store.tournament["players"])
        self.assertEqual(imported["completedRounds"], 3)
        self.assertIn("synthetic", imported["provenance"]["source"])

    def test_csv_export_escapes_spreadsheet_formulas(self):
        self.store.tournament["players"][0]["name"] = "=CMD()"
        self.store.apply(self.store.tournament)
        status, _, data = self.request("/api/export?format=csv")
        self.assertEqual(status, 200)
        self.assertIn(b"'=CMD()", data)

    def test_csv_exports_the_selected_alternative(self):
        self.store.apply(rules={"profile": "fide-constraints"})
        self.assertGreaterEqual(len(self.store.forecast["candidates"]), 2)
        status, _, data = self.request("/api/export?format=csv&candidate=1")
        self.assertEqual(status, 200)
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
        expected = [(p["white"]["id"], p["black"]["id"]) for p in self.store.forecast["candidates"][1]["pairings"]]
        self.assertEqual([(row["white_id"], row["black_id"]) for row in rows], expected)

    def test_csv_rejects_unknown_candidates_and_blocked_forecasts(self):
        self.assertEqual(self.request("/api/export?format=csv&candidate=-1")[0], 400)
        self.assertEqual(self.request("/api/export?format=csv&candidate=99")[0], 400)
        self.store.apply({**demo_tournament(), "acceleration": True})
        self.assertEqual(self.request("/api/export?format=csv")[0], 409)

    def test_invalid_update_does_not_mutate_saved_state(self):
        before = self.store.path.read_bytes()
        invalid = copy.deepcopy(self.store.tournament)
        invalid["players"][1]["id"] = invalid["players"][0]["id"]
        status, _, _ = self.request("/api/tournament", invalid, "PUT")
        self.assertEqual(status, 400)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_server_rejects_cross_origin_and_unknown_host(self):
        self.assertEqual(self.request("/api/demo", {}, "POST", {"Origin": "https://attacker.example"})[0], 403)
        self.assertEqual(self.request("/api/state", headers={"Host": "attacker.example"})[0], 403)

    def test_unknown_paths_do_not_expose_workbook_or_state(self):
        for path in ("/../.local/state.json", "/.local/state.json", "/app/server.py", "/api/missing"):
            self.assertEqual(self.request(path)[0], 404)

    def test_reloading_store_preserves_edits(self):
        changed = copy.deepcopy(self.store.tournament)
        changed["name"] = "Edited event"
        self.request("/api/tournament", changed, "PUT")
        restored = Store(self.store.path)
        self.assertEqual(restored.tournament["name"], "Edited event")
        self.assertEqual(restored.forecast["status"], "ok")

    def test_legacy_defaults_upgrade_to_us_policy_but_custom_limits_remain_strict(self):
        for old_limit, expected in ((2, "us-chess"), (1, "strict")):
            legacy = {key: value for key, value in DEFAULT_RULES.items() if key not in {
                "pairingOrder", "colorPolicy", "usColorMethod", "usColorAlternationLimit", "usColorEqualizationLimit",
                "usEqualizationPriority", "allowLastRoundExceptions", "teamPolicy"}}
            legacy["maxConsecutiveColors"] = old_limit
            self.store.path.write_text(json.dumps({"tournament": demo_tournament(), "rules": legacy}), encoding="utf-8")
            self.assertEqual(Store(self.store.path).rules["colorPolicy"], expected)

    def test_rule_change_recalculates_and_persists(self):
        status, _, data = self.request("/api/forecast", {"rules": {"profile": "fide-constraints"}}, "POST")
        self.assertEqual(status, 200)
        self.assertIn("Dutch", json.loads(data)["warnings"][-1])
        self.assertEqual(Store(self.store.path).rules["profile"], "fide-constraints")

    @unittest.skipUnless(availability()["available"], "Pinned native Dutch engine is optional; run scripts/setup_dutch_engine.py")
    def test_native_dutch_end_to_end_through_http_and_export(self):
        status, _, data = self.request("/api/forecast", {"rules": {"profile": "fide-dutch"}}, "POST")
        result = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertIn("BBP Pairings 6.0.0", result["engine"])
        self.assertEqual(result["engineDetails"]["version"], "6.0.0")
        self.assertEqual(result["stats"]["scoreGroups"], 5)
        self.assertEqual(len(result["inputHash"]), 64)
        self.assertEqual(len(result["candidates"]), 1)
        status, _, exported = self.request("/api/export?format=csv&candidate=0")
        self.assertEqual(status, 200)
        rows = list(csv.DictReader(io.StringIO(exported.decode("utf-8-sig"))))
        self.assertEqual([(r["white_id"], r["black_id"]) for r in rows],
                         [(p["white"]["id"], p["black"]["id"]) for p in result["pairings"]])

    def test_missing_native_engine_does_not_trigger_heuristic_fallback(self):
        unavailable = {**availability(), "available": False, "reason": "Fixture: native engine is unavailable"}
        with patch("app.dutch.availability", return_value=unavailable):
            status, _, data = self.request("/api/forecast", {"rules": {"profile": "fide-dutch"}}, "POST")
            result = json.loads(data)
            self.assertEqual(status, 200, data.decode())
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["blockingReason"], "engine")
            self.assertEqual(result["pairings"], [])
            self.assertIn("unavailable", " ".join(result["issues"]))
            status, _, data = self.request("/api/forecast", {"rules": {"profile": "us-chess"}}, "POST")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(data)["status"], "ok", "The Python US engine should remain independent of the optional executable")

    def test_oversized_request_is_rejected_before_body_read(self):
        self.assertEqual(self.request("/api/import", None, "POST", {"Content-Length": "9000000"})[0], 413)

    def test_source_workbook_remains_blocked(self):
        status, _, data = self.request("/api/reset", {}, "POST")
        result = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(len(result["tournament"]["players"]), 51)
        self.assertEqual(result["forecast"]["status"], "blocked")
        self.assertTrue(result["forecast"]["issues"])


class BoundaryTests(unittest.TestCase):
    def test_source_urls_reject_other_hosts_http_and_credentials(self):
        for url in ("http://ratings.fide.com/a", "https://example.com/a", "https://ratings.fide.com.evil.example/a", "https://u:p@ratings.fide.com/a", "https://127.0.0.1/a"):
            with self.assertRaises(ValueError):
                resolve_source(url)

    def test_dns_private_address_is_rejected(self):
        with patch("app.sources.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaisesRegex(ValueError, "public"):
                resolve_source("https://ratings.fide.com/a")

    def test_numeric_values_must_be_finite(self):
        tournament = demo_tournament()
        tournament["players"][0]["initialScore"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            validate_tournament(tournament)

    def test_late_entry_history_and_withdrawal_are_explicit(self):
        tournament = {"rounds": 5, "completedRounds": 1, "players": [
            {"id": "1", "name": "Early", "rating": 1800, "results": [{"round": 1, "result": "ZERO", "played": False}], "withdrawnAfterRound": 1},
            {"id": "2", "name": "Late", "rating": 1700, "entryRound": 2, "results": []},
        ]}
        result = forecast(validate_tournament(tournament))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["bye"]["id"], "2")

    def test_nonstandard_scoring_and_acceleration_block(self):
        for extra in ({"scoring": {"win": 3, "draw": 1, "loss": 0}}, {"acceleration": True}, {"pairingSystem": "FIDE Burstein"}):
            tournament = {**demo_tournament(), **extra}
            result = forecast(validate_tournament(tournament))
            self.assertEqual(result["status"], "blocked")
            self.assertIn("unsupported", " ".join(result["issues"]))

    def test_bye_records_cannot_name_an_opponent(self):
        tournament = {"rounds": 5, "completedRounds": 1, "players": [
            {"id": "1", "name": "A", "rating": 1800, "results": [{"round": 1, "result": "BYE", "opponentId": "2"}]},
            {"id": "2", "name": "B", "rating": 1700, "results": [{"round": 1, "result": "BYE", "opponentId": "1"}]},
        ]}
        result = forecast(validate_tournament(tournament))
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("cannot have an opponent" in issue for issue in result["issues"]))

    def test_scheduled_bye_must_reconcile_with_past_game(self):
        tournament = demo_tournament()
        tournament["players"][0]["scheduledByes"] = [{"round": 1, "points": .5}]
        result = forecast(validate_tournament(tournament))
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("scheduled bye" in issue for issue in result["issues"]))

    def test_double_forfeit_is_a_legitimate_unplayed_pair(self):
        tournament = {"rounds": 5, "completedRounds": 1, "players": [
            {"id": "1", "name": "A", "rating": 1800, "results": [{"round": 1, "result": "FORFEIT_LOSS", "opponentId": "2"}]},
            {"id": "2", "name": "B", "rating": 1700, "results": [{"round": 1, "result": "FORFEIT_LOSS", "opponentId": "1"}]},
        ]}
        result = forecast(validate_tournament(tournament))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["pairings"]), 1)


if __name__ == "__main__":
    unittest.main()
