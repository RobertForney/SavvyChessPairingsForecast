"""Run local events through HTTP, persistence, exports and native pairings."""
from copy import deepcopy
import csv
import http.client
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from app.dutch import availability
from app.follow import new_follow
from app.pairing import DEFAULT_RULES, player_score
from app.server import Store, make_handler


class DirectorServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / "local-event.json")
        handler = make_handler(cls.store)
        handler.log_message = lambda *args: None
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(timeout=5)
        cls.temp.cleanup()

    def setUp(self):
        self.store.apply(new_follow({"name": "A different followed event"}), DEFAULT_RULES, replace_workspace=True)

    def request(self, path, body=None, expected=200):
        if body is not None:
            body = deepcopy(body)
            if path.startswith("/api/director/") and self.store.tournament.get("managed"):
                body.setdefault("expectedRevision", self.store.tournament["managed"]["revision"])
                body.setdefault("expectedEventId", self.store.tournament["managed"]["eventId"])
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=15)
        try:
            conn.request("POST" if body is not None else "GET", path,
                         json.dumps(body) if body is not None else None, {"Content-Type": "application/json"})
            response = conn.getresponse()
            raw = response.read()
            self.assertEqual(response.status, expected, raw.decode("utf-8-sig"))
            return json.loads(raw) if "application/json" in response.getheader("Content-Type", "") else raw
        finally:
            conn.close()

    def create_us(self, rounds=3):
        return self.request("/api/director/new", {"name": "Community Open", "rounds": rounds,
                            "profile": "us-chess", "firstRoundColor": "W", "acknowledgeEngineLimits": True})

    def add_field(self, size=4):
        text = "name,rating,team\n" + "".join(f"Player {i},{2000-i*100},Club {i % 2}\n" for i in range(1, size + 1))
        return self.request("/api/director/roster", {"text": text})

    def issue(self):
        draft = self.request("/api/director/draft", {})["tournament"]["managed"]["draft"]
        return self.request("/api/director/issue", {"draftId": draft["id"]})

    def test_new_event_defaults_to_dutch_and_bad_roster_is_atomic(self):
        state = self.request("/api/director/new", {"name": "Club Open", "rounds": 4})
        self.assertEqual(state["rules"]["profile"], "fide-dutch")
        self.assertEqual(state["tournament"]["players"], [])
        before, saved = self.store.state(), self.store.path.read_bytes()
        self.request("/api/director/roster", {"text": "name,rating\nValid,1800\nInvalid,nope\n"}, 400)
        self.assertEqual(self.store.state(), before)
        self.assertEqual(self.store.path.read_bytes(), saved)

    def test_issued_pending_results_close_restart_and_backup_restore(self):
        self.create_us(rounds=1)
        self.add_field()
        issued = self.issue()
        event = issued["tournament"]
        round_ = event["managed"]["rounds"][0]
        snapshot = deepcopy(round_["snapshot"])
        self.assertTrue(all(not p["results"] for p in event["players"]))
        self.assertEqual(event["completedRounds"], 0)
        self.request("/api/director/close", {"round": 1}, 400)
        codes = [{"board": board["board"], "result": "W" if i == 0 else "D"} for i, board in enumerate(snapshot["pairings"])]
        entered = self.request("/api/director/results", {"round": 1, "results": codes})
        self.assertEqual(entered["tournament"]["completedRounds"], 0)
        closed = self.request("/api/director/close", {"round": 1})
        self.assertEqual(closed["directorStatus"]["stage"], "complete")
        self.assertEqual(closed["tournament"]["managed"]["rounds"][0]["snapshot"], snapshot)
        self.assertEqual(sum(player_score(p) for p in closed["tournament"]["players"]), 2)
        loaded = Store(self.store.path)
        self.assertEqual(loaded.tournament, closed["tournament"])
        backup = self.request("/api/director/export?format=json")
        self.assertEqual(backup["kind"], "savvy-managed-event")
        self.create_us()
        restored = self.request("/api/director/restore", backup)
        self.assertEqual(restored["tournament"]["players"], closed["tournament"]["players"])
        self.assertEqual(restored["tournament"]["managed"]["rounds"], closed["tournament"]["managed"]["rounds"])
        self.assertNotEqual(restored["tournament"]["managed"]["eventId"], closed["tournament"]["managed"]["eventId"])
        self.assertEqual(restored["tournament"]["managed"]["audit"][-1]["action"], "event-restored")
        self.assertEqual(restored["rules"], backup["rules"])

    def test_print_and_csv_use_fixed_issued_assignments_and_escape_fields(self):
        self.create_us()
        for name, rating in (("=Prize Player", 1800), ("<script>alert(1)</script>", 1700)):
            self.request("/api/director/players", {"name": name, "rating": rating})
        issued = self.issue()["tournament"]["managed"]["rounds"][0]
        exported = self.request("/api/director/export?format=csv&round=1").decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(exported)))
        self.assertEqual(len(rows), 1)
        self.assertEqual({rows[0]["white_id"], rows[0]["black_id"]},
                         {issued["snapshot"]["pairings"][0]["white"]["id"], issued["snapshot"]["pairings"][0]["black"]["id"]})
        self.assertIn("'=Prize Player", exported)
        sheet = self.request("/api/director/print?round=1").decode()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", sheet)
        self.assertNotIn("<script>", sheet)
        self.assertIn("@media print", sheet)
        self.assertIn("Round 1", sheet)
        self.request("/api/director/results", {"round": 1, "results": [{"board": 1, "result": "DOUBLE_FORFEIT"}]})
        sheet = self.request("/api/director/print?round=1").decode()
        self.assertIn("0–0 (double forfeit)", sheet)
        csv_result = self.request("/api/director/export?format=csv&round=1").decode("utf-8-sig")
        self.assertIn("DOUBLE_FORFEIT", csv_result)

    @unittest.skipUnless(availability()["available"], "Pinned native Dutch engine is not installed")
    def test_native_odd_field_byes_forfeit_and_second_round(self):
        self.request("/api/director/new", {"name": "Five Player Open", "rounds": 3})
        self.add_field(5)
        issued = self.issue()
        round_ = issued["tournament"]["managed"]["rounds"][0]
        self.assertTrue(round_["snapshot"]["engine"].startswith("BBP Pairings"))
        self.assertEqual(len(round_["snapshot"]["byes"]), 1)
        bye_id = round_["snapshot"]["byes"][0]["player"]["id"]
        results = [{"board": board["board"], "result": "FORFEIT_WIN" if i == 0 else "D"}
                   for i, board in enumerate(round_["snapshot"]["pairings"])]
        self.request("/api/director/results", {"round": 1, "results": results})
        closed = self.request("/api/director/close", {"round": 1})
        self.assertEqual(sum(player_score(p) for p in closed["tournament"]["players"]), 3)
        bye = next(p for p in closed["tournament"]["players"] if p["id"] == bye_id)["results"][0]
        self.assertFalse(bye["played"])
        self.assertIsNone(bye["color"])
        next_draft = self.request("/api/director/draft", {})["tournament"]["managed"]["draft"]
        self.assertEqual(next_draft["round"], 2)
        allocated = next_draft["forecast"].get("byes") or [{"player": next_draft["forecast"]["bye"]}]
        self.assertNotEqual(allocated[0]["player"]["id"], bye_id)
