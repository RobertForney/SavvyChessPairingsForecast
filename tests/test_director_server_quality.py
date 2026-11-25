"""Adversarial HTTP and persistence checks for an issued local event."""
from copy import deepcopy
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.server import Store, make_handler


class DirectorServerQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / "managed.json")
        handler = make_handler(cls.store)
        handler.log_message = lambda *args: None
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.worker = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.worker.join(timeout=5)
        cls.temp.cleanup()

    def request(self, path, body=None, method=None):
        if isinstance(body, dict) and path.startswith("/api/director/") and self.store.tournament.get("managed", {}).get("enabled"):
            body = {"expectedEventId": self.store.tournament["managed"]["eventId"], **body}
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        try:
            connection.request(method or ("POST" if body is not None else "GET"), path,
                               json.dumps(body) if body is not None else None,
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = json.loads(response.read())
            return response.status, payload
        finally:
            connection.close()

    def successful(self, path, body=None, method=None):
        if isinstance(body, dict) and path.startswith("/api/director/") and self.store.tournament.get("managed", {}).get("enabled"):
            body = {"expectedRevision": self.store.tournament["managed"]["revision"], **body}
        code, result = self.request(path, body, method)
        self.assertEqual(code, 200, (path, result))
        return result

    def setUp(self):
        self.successful("/api/director/new", {"name": "Independent Quality Cup", "rounds": 3,
                        "profile": "us-chess", "firstRoundColor": "W", "acknowledgeEngineLimits": True})
        for name, rating in (("Alex", 2000), ("Blair", 1900), ("Casey", 1800), ("Devon", 1700)):
            self.successful("/api/director/players", {"name": name, "rating": rating})
        state = self.successful("/api/director/draft", {})
        self.draft_id = state["tournament"]["managed"]["draft"]["id"]
        self.issued = self.successful("/api/director/issue", {"draftId": self.draft_id})

    def test_older_mutation_routes_cannot_change_announced_assignments(self):
        before = self.store.state()
        changed = deepcopy(before["tournament"])
        changed["name"] = "Unreviewed generic replacement"
        changed.pop("managed", None)
        attempts = [("/api/tournament", changed, "PUT"),
                    ("/api/forecast", {"rules": {"profile": "fide-dutch"}}, "POST"),
                    ("/api/follow/settings", {"name": "Changed through follower"}, "PUT"),
                    ("/api/demo", {}, "POST"), ("/api/reset", {}, "POST")]
        for path, body, method in attempts:
            with self.subTest(path=path):
                code, result = self.request(path, body, method)
                self.assertIn(code, {400, 409}, (path, result))
                self.assertEqual(self.store.state(), before)

    def test_an_unobserved_follow_workspace_replacement_cannot_erase_the_event(self):
        before = self.store.state()
        for guard in ({}, {"expectedEventId": "old-event", "expectedRevision": self.store.tournament["managed"]["revision"]}):
            with self.subTest(guard=guard):
                code, result = self.request("/api/follow/new", {"name": "Unobserved follow replacement", **guard})
                self.assertEqual(code, 409, result)
                self.assertEqual(self.store.state(), before)

    def test_forged_issued_round_in_generic_json_is_rejected(self):
        before = self.store.state()
        tampered = deepcopy(before["tournament"])
        round_ = tampered["managed"]["rounds"][0]
        round_["snapshot"]["pairings"][0]["white"], round_["snapshot"]["pairings"][0]["black"] = round_["snapshot"]["pairings"][0]["black"], round_["snapshot"]["pairings"][0]["white"]
        code, result = self.request("/api/tournament", tampered, "PUT")
        self.assertIn(code, {400, 409}, result)
        self.assertEqual(self.store.state(), before)

    def test_restoring_a_modified_pairing_snapshot_does_not_replace_the_event(self):
        before = self.store.state()
        tampered = deepcopy(before["tournament"])
        pairing = tampered["managed"]["rounds"][0]["snapshot"]["pairings"][0]
        pairing["white"], pairing["black"] = pairing["black"], pairing["white"]
        code, result = self.request("/api/director/restore", {"tournament": tampered, "rules": self.store.rules,
                         "expectedRevision": self.store.tournament["managed"]["revision"]})
        self.assertIn(code, {400, 409}, result)
        self.assertEqual(self.store.state(), before)

    def test_backup_rules_cannot_change_the_procedure_of_an_issued_event(self):
        before = self.store.state()
        altered_rules = {**self.store.rules, "profile": "fide-dutch"}
        code, result = self.request("/api/director/restore", {"tournament": deepcopy(before["tournament"]),
                         "rules": altered_rules, "expectedRevision": self.store.tournament["managed"]["revision"]})
        self.assertIn(code, {400, 409}, result)
        self.assertEqual(self.store.state(), before)

    def test_exported_backup_survives_browser_number_serialization(self):
        backup = self.successful("/api/director/export?format=json")
        original_rounds = deepcopy(backup["tournament"]["managed"]["rounds"])
        original_id = backup["tournament"]["managed"]["eventId"]

        def browser_numbers(value):
            # JSON.parse/JSON.stringify serializes integral JSON numbers as 0,
            # 1, etc., even when Python originally exported 0.0 or 1.0.
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, list):
                return [browser_numbers(item) for item in value]
            if isinstance(value, dict):
                return {key: browser_numbers(item) for key, item in value.items()}
            return value

        self.assertIsInstance(original_rounds[0]["snapshot"]["pairings"][0]["white"]["score"], float)
        browser_backup = browser_numbers(backup)
        self.assertIsInstance(browser_backup["tournament"]["managed"]["rounds"][0]["snapshot"]["pairings"][0]["white"]["score"], int)
        restored = self.successful("/api/director/restore", browser_backup)
        self.assertEqual(restored["tournament"]["managed"]["rounds"], original_rounds)
        self.assertNotEqual(restored["tournament"]["managed"]["eventId"], original_id)
        self.assertEqual(Store(self.store.path).tournament, restored["tournament"])

    def test_one_point_requested_bye_closes_and_prevents_a_later_allocated_bye(self):
        self.successful("/api/director/new", {"name": "Requested Bye Cup", "rounds": 3,
                        "profile": "us-chess", "firstRoundColor": "W", "acknowledgeEngineLimits": True})
        for index in range(5):
            self.successful("/api/director/players", {"name": f"Player {index + 1}", "rating": 2000 - index * 100})
        self.successful("/api/director/players", {"id": "p1", "scheduledByes": [{"round": 1, "points": 1}]})
        draft = self.successful("/api/director/draft", {})["tournament"]["managed"]["draft"]
        self.assertTrue(draft["quality"]["canIssue"], draft["quality"])
        issued = self.successful("/api/director/issue", {"draftId": draft["id"]})
        snapshot = issued["tournament"]["managed"]["rounds"][0]["snapshot"]
        self.assertEqual([bye["player"]["id"] for bye in snapshot["requestedByes"]], ["p1"])
        self.successful("/api/director/results", {"round": 1,
                        "results": [{"board": pair["board"], "result": "D"} for pair in snapshot["pairings"]]})
        closed = self.successful("/api/director/close", {"round": 1})
        requested_player = next(player for player in closed["tournament"]["players"] if player["id"] == "p1")
        self.assertEqual(len(requested_player["results"]), 1)
        self.assertEqual(requested_player["results"][0]["points"], 1)
        self.assertFalse(requested_player["results"][0]["played"])
        self.assertIsNone(requested_player["results"][0]["color"])
        next_draft = self.successful("/api/director/draft", {})["tournament"]["managed"]["draft"]
        self.assertTrue(next_draft["quality"]["canIssue"], next_draft["quality"])
        self.assertNotIn("p1", [bye["player"]["id"] for bye in next_draft["forecast"]["byes"]])

    def test_issuing_the_same_draft_twice_does_not_create_another_round(self):
        before = self.store.state()
        code, result = self.request("/api/director/issue", {"draftId": self.draft_id, "expectedRevision": self.store.tournament["managed"]["revision"]})
        self.assertIn(code, {400, 409}, result)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(len(self.store.tournament["managed"]["rounds"]), 1)

    def test_incomplete_close_does_not_advance_or_write_partial_history(self):
        before = self.store.state()
        code, result = self.request("/api/director/close", {"round": 1, "expectedRevision": self.store.tournament["managed"]["revision"]})
        self.assertIn(code, {400, 409}, result)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(self.store.tournament["completedRounds"], 0)
        self.assertTrue(all(not player["results"] for player in self.store.tournament["players"]))

    def test_result_revision_conflict_cannot_overwrite_a_newer_board_entry(self):
        revision = self.store.tournament["managed"]["revision"]
        self.successful("/api/director/results", {"round": 1, "expectedRevision": revision,
                        "results": [{"board": 1, "result": "W"}]})
        before = self.store.state()
        code, result = self.request("/api/director/results", {"round": 1, "expectedRevision": revision,
                         "results": [{"board": 1, "result": "L"}]})
        self.assertIn(code, {400, 409}, result)
        self.assertEqual(self.store.state(), before)

    def test_existing_event_mutations_require_an_observed_revision(self):
        before = self.store.state()
        actions = [("settings", {"name": "Unobserved change"}), ("draft", {}),
                   ("issue", {"draftId": self.draft_id}), ("close", {"round": 1}),
                   ("reopen", {"reason": "Unobserved correction"})]
        for action, body in actions:
            with self.subTest(action=action):
                code, result = self.request("/api/director/" + action, body)
                self.assertEqual(code, 400, result)
                self.assertIn("revision", result.get("error", "").lower())
                self.assertEqual(self.store.state(), before)

    def test_replacing_an_event_cannot_reuse_an_old_tab_revision(self):
        stale_id = self.store.tournament["managed"]["eventId"]
        stale_revision = self.store.tournament["managed"]["revision"]
        self.successful("/api/director/new", {"name": "Replacement Quality Cup", "rounds": 3,
                        "profile": "us-chess", "firstRoundColor": "W", "acknowledgeEngineLimits": True})
        for name, rating in (("Alex", 2000), ("Blair", 1900), ("Casey", 1800), ("Devon", 1700)):
            self.successful("/api/director/players", {"name": name, "rating": rating})
        state = self.successful("/api/director/draft", {})
        self.successful("/api/director/issue", {"draftId": state["tournament"]["managed"]["draft"]["id"]})
        self.assertEqual(self.store.tournament["managed"]["revision"], stale_revision)
        before = self.store.state()
        code, result = self.request("/api/director/results", {"round": 1, "expectedRevision": stale_revision,
                         "expectedEventId": stale_id, "results": [{"board": 1, "result": "W"}]})
        self.assertEqual(code, 409, result)
        self.assertEqual(self.store.state(), before)

    def test_failed_result_persistence_preserves_the_preceding_round_state(self):
        from app.director import record_results
        before = self.store.state()
        saved = self.store.path.read_bytes()
        proposed = record_results(self.store.tournament, 1, [{"board": 1, "result": "D"}],
                                  expected_revision=self.store.tournament["managed"]["revision"])
        with patch.object(self.store, "save", side_effect=OSError("Fixture: disk unavailable")):
            with self.assertRaises(OSError):
                self.store.apply(proposed, managed_transition=True)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(self.store.path.read_bytes(), saved)

    def test_status_validation_failure_precedes_persistence(self):
        from app.director import record_results
        before = self.store.state()
        saved = self.store.path.read_bytes()
        proposed = record_results(self.store.tournament, 1, [{"board": 1, "result": "D"}],
                                  expected_revision=self.store.tournament["managed"]["revision"])
        with patch("app.director.status", side_effect=ValueError("Fixture: invalid derived round view")):
            with self.assertRaises(ValueError):
                self.store.apply(proposed, managed_transition=True)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(self.store.path.read_bytes(), saved)

    def test_failed_close_persistence_cannot_double_count_results(self):
        from app.director import close_round
        current = self.store.tournament["managed"]["revision"]
        boards = self.store.tournament["managed"]["rounds"][0]["snapshot"]["pairings"]
        self.successful("/api/director/results", {"round": 1, "expectedRevision": current,
                        "results": [{"board": pair["board"], "result": "D"} for pair in boards]})
        before = self.store.state()
        saved = self.store.path.read_bytes()
        proposed = close_round(self.store.tournament, self.store.rules, 1)
        with patch.object(self.store, "save", side_effect=OSError("Fixture: close could not be persisted")):
            with self.assertRaises(OSError):
                self.store.apply(proposed, managed_transition=True)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(self.store.path.read_bytes(), saved)
        self.successful("/api/director/close", {"round": 1})
        self.assertEqual(self.store.tournament["completedRounds"], 1)
        self.assertTrue(all(len(player["results"]) == 1 for player in self.store.tournament["players"]))

    def test_restored_closed_history_must_match_its_recorded_round_results(self):
        boards = self.store.tournament["managed"]["rounds"][0]["snapshot"]["pairings"]
        self.successful("/api/director/results", {"round": 1,
                        "results": [{"board": pair["board"], "result": "D"} for pair in boards]})
        self.successful("/api/director/close", {"round": 1})
        before = self.store.state()
        tampered = deepcopy(before["tournament"])
        tampered["players"][0]["results"][0]["result"] = "W"
        tampered["players"][0]["results"][0].pop("points", None)
        code, result = self.request("/api/director/restore", {"tournament": tampered, "rules": self.store.rules,
                         "expectedRevision": self.store.tournament["managed"]["revision"]})
        self.assertIn(code, {400, 409}, result)
        self.assertEqual(self.store.state(), before)
