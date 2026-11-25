"""Exercise player-owned live events through HTTP with independent club data."""
from copy import deepcopy
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.follow import new_follow
from app.pairing import DEFAULT_RULES
from app.server import Store, make_handler


ROSTER = "No,Name,Rating,Pts\n1,Ada,2000,0\n2,Ben,1900,0\n3,Cara,1800,0\n4,Dave,1700,0\n"
ROUND_ONE = "No,Name,Rating,Pts,R1\n1,Ben,1900,1,W4b\n2,Ada,2000,1,W3w\n3,Cara,1800,0,L2b\n4,Dave,1700,0,L1w\n"
ROUND_TWO = "No,Name,Rating,Pts,R1,R2\n1,Ben,1900,1.5,W4b,D2w\n2,Ada,2000,1.5,W3w,D1b\n3,Cara,1800,1,L2b,W4b\n4,Dave,1700,0,L1w,L3w\n"
SOURCE_URL = "https://chess-results.com/independent-club-standings.html"


def html_snapshot(csv_text):
    lines = [line.split(",") for line in csv_text.strip().splitlines()]
    head = "<tr>" + "".join(f"<th>{cell}</th>" for cell in lines[0]) + "</tr>"
    rows = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in lines[1:])
    return ("<!doctype html><title>Independent Club Standings</title><h2>Club</h2><table>" + head + rows + "</table>").encode()


class FollowServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / "state.json")
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

    def setUp(self):
        with self.store.lock:
            self.store.follow_previews.clear()
            self.store.apply(new_follow({"name": "Independent Club Open", "profile": "unknown", "rounds": None}), DEFAULT_RULES)

    def request(self, path, body=None, method=None, expected=200):
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_port, timeout=10)
        try:
            connection.request(method or ("POST" if body is not None else "GET"), path,
                               json.dumps(body) if body is not None else None,
                               {"Content-Type": "application/json"})
            response = connection.getresponse()
            data = json.loads(response.read())
            self.assertEqual(response.status, expected, (path, data))
            return data
        finally:
            connection.close()

    def preview_text(self, text):
        return self.request("/api/follow/preview", {"text": text, "filename": "club.csv"})

    def apply_preview(self, preview):
        return self.request("/api/follow/apply", {"previewId": preview["previewId"]})

    def apply_text(self, text):
        return self.apply_preview(self.preview_text(text))

    def confirm_setup(self, **extra):
        return self.request("/api/follow/settings", {"profile": "us-chess", "rounds": 5,
                            "firstRoundColor": "W", "schedulePolicy": "single", **extra}, "PUT")

    def test_unknown_event_without_opponents_persists_across_restart(self):
        state = self.request("/api/follow/new", {"name": "A Future Club Event", "playerQuery": "Ada",
                             "rounds": None, "profile": "unknown", "firstRoundColor": None})
        self.assertEqual(state["tournament"]["players"], [])
        self.assertEqual(state["forecast"]["status"], "blocked")
        self.assertFalse(state["followStatus"]["readiness"]["ready"])
        self.assertEqual(state["tournament"]["tracking"]["profile"], "unknown")
        self.assertFalse(state["tournament"]["tracking"]["roundsKnown"])
        self.assertIsNone(state["tournament"]["tracking"]["firstRoundColor"])
        restarted = Store(self.store.path).state()
        self.assertEqual(restarted["tournament"]["name"], "A Future Club Event")
        self.assertEqual(restarted["tournament"]["players"], [])
        self.assertEqual(restarted["tournament"]["tracking"]["playerQuery"], "Ada")
        self.assertEqual(restarted["forecast"]["status"], "blocked")

    def test_roster_preview_is_read_only_until_reviewed_application(self):
        before = self.store.state()
        saved = self.store.path.read_bytes()
        preview = self.preview_text(ROSTER)
        self.assertEqual(preview["summary"]["addedPlayers"], 4)
        self.assertTrue(preview["summary"]["rosterOnly"])
        self.assertEqual(self.store.state(), before)
        self.assertEqual(self.store.path.read_bytes(), saved)
        applied = self.apply_preview(preview)
        self.assertEqual(len(applied["tournament"]["players"]), 4)
        self.assertEqual(applied["tournament"]["completedRounds"], 0)
        self.assertEqual(applied["forecast"]["status"], "blocked", "The procedure is still unknown")
        self.assertTrue(all(not p["results"] for p in applied["tournament"]["players"]))
        self.assertTrue(all("pairingNumber" not in p and "seed" not in p for p in applied["tournament"]["players"]))
        self.request("/api/follow/apply", {"previewId": preview["previewId"]}, expected=409)

    def test_live_round_refresh_keeps_player_identity_and_advances_forecast(self):
        roster = self.apply_text(ROSTER)
        focus_id = next(p["id"] for p in roster["tournament"]["players"] if p["name"] == "Ada")
        known = self.confirm_setup(focusPlayerId=focus_id)
        self.assertEqual(known["forecast"]["status"], "ok")
        current = self.apply_text(ROUND_ONE)
        self.assertEqual(current["tournament"]["completedRounds"], 1)
        self.assertEqual(next(p for p in current["tournament"]["players"] if p["id"] == focus_id)["name"], "Ada")
        predicted = self.request("/api/forecast", {"rules": {"profile": "us-chess"}})
        self.assertEqual((predicted["status"], predicted["round"]), ("ok", 2))
        self.assertEqual(len(predicted["pairings"]), 2)
        refreshed = self.apply_text(ROUND_TWO)
        self.assertEqual(refreshed["tournament"]["completedRounds"], 2)
        self.assertEqual(refreshed["tournament"]["tracking"]["focusPlayerId"], focus_id)
        self.assertEqual((refreshed["forecast"]["status"], refreshed["forecast"]["round"]), ("ok", 3))
        self.assertEqual(refreshed["rules"]["profile"], "us-chess")

    def test_settings_change_invalidates_an_earlier_preview(self):
        preview = self.preview_text(ROSTER)
        changed = self.request("/api/follow/settings", {"section": "Club Section"}, "PUT")
        error = self.request("/api/follow/apply", {"previewId": preview["previewId"]}, expected=409)
        self.assertIn("changed", error["error"])
        self.assertEqual(self.store.state(), changed)
        self.assertEqual(self.store.tournament["players"], [])

    def test_positive_standings_totals_without_history_block_a_fictional_first_round(self):
        self.confirm_setup()
        applied = self.apply_text("No,Name,Rating,Pts\n1,Ada,2000,2\n2,Ben,1900,1.5\n3,Cara,1800,1\n4,Dave,1700,0.5\n")
        self.assertEqual(applied["tournament"]["completedRounds"], 0)
        self.assertEqual(applied["forecast"]["status"], "blocked")
        self.assertEqual(applied["forecast"]["pairings"], [])
        self.assertIn("totals alone", " ".join(applied["forecast"]["issues"]))
        self.assertEqual([p["sourceReportedScore"] for p in applied["tournament"]["players"]], [2, 1.5, 1, 0.5])
        predicted = self.request("/api/forecast", {"rules": {"profile": "us-chess"}})
        self.assertEqual(predicted["status"], "blocked", "A manual generate must not bypass missing evidence")

    def test_alternative_profile_is_labelled_without_rewriting_confirmed_setup(self):
        self.confirm_setup()
        self.apply_text(ROSTER)
        predicted = self.request("/api/forecast", {"rules": {"profile": "fide-constraints"}})
        self.assertEqual(predicted["status"], "ok")
        self.assertTrue(any("Event setup lists us-chess" in note and "alternative rule hypothesis" in note for note in predicted["assumptions"]))
        self.assertEqual(self.store.tournament["tracking"]["profile"], "us-chess")

    def test_unsupported_format_stays_blocked_when_a_swiss_profile_is_requested(self):
        self.request("/api/follow/settings", {"profile": "other", "rounds": 5}, "PUT")
        applied = self.apply_text(ROSTER)
        self.assertEqual(applied["forecast"]["status"], "blocked")
        self.assertIn("unsupported pairing format", " ".join(applied["forecast"]["issues"]))
        predicted = self.request("/api/forecast", {"rules": {"profile": "us-chess"}})
        self.assertEqual(predicted["status"], "blocked")
        self.assertEqual(predicted["pairings"], [])

    def test_online_source_two_snapshot_flow_saves_source_only_on_apply(self):
        self.confirm_setup(section="Club")
        snapshots = [(html_snapshot(ROUND_ONE), SOURCE_URL, "text/html", False),
                     (html_snapshot(ROUND_TWO), SOURCE_URL, "text/html", False)]
        with patch("app.feeds.fetch_source", side_effect=snapshots) as fetched:
            first = self.request("/api/follow/preview", {"url": SOURCE_URL, "section": "Club"})
            self.assertEqual(self.store.tournament["players"], [])
            self.assertEqual(self.store.tournament["tracking"]["sourceUrl"], "")
            current = self.apply_preview(first)
            self.assertEqual(current["tournament"]["tracking"]["sourceUrl"], SOURCE_URL)
            self.assertEqual(current["tournament"]["completedRounds"], 1)
            before = deepcopy(current["tournament"])
            second = self.request("/api/follow/preview", {"url": SOURCE_URL, "section": "Club"})
            self.assertEqual(self.store.tournament, before)
            self.assertEqual(second["summary"]["addedPlayers"], 0)
            updated = self.apply_preview(second)
            self.assertEqual(updated["tournament"]["completedRounds"], 2)
            self.assertEqual(updated["tournament"]["name"], "Independent Club Open")
            self.assertEqual((updated["forecast"]["status"], updated["forecast"]["round"]), ("ok", 3))
            self.assertEqual(fetched.call_count, 2)

    def test_missing_played_colors_stay_unknown_and_block_color_sensitive_forecasting(self):
        self.confirm_setup()
        without_colors = ROUND_ONE.replace("W4b", "W4").replace("W3w", "W3").replace("L2b", "L2").replace("L1w", "L1")
        preview = self.preview_text(without_colors)
        self.assertEqual(len(preview["summary"]["missingColors"]), 4)
        applied = self.apply_preview(preview)
        self.assertTrue(all(r["color"] is None for p in applied["tournament"]["players"] for r in p["results"]))
        self.assertEqual(applied["forecast"]["status"], "blocked")
        self.assertFalse(applied["followStatus"]["readiness"]["ready"])
        self.assertEqual(applied["followStatus"]["readiness"]["missingColors"], 4)

    def test_partial_source_does_not_remove_players_or_their_previous_results(self):
        self.confirm_setup()
        first = self.apply_text(ROUND_ONE)
        previous = {p["id"]: deepcopy(p) for p in first["tournament"]["players"]}
        partial = self.preview_text("No,Name,Rating,Pts,R1\n2,Ada,2000,1,W3w\n3,Cara,1800,0,L2b\n")
        self.assertEqual(partial["summary"]["preservedPlayers"], 2)
        updated = self.apply_preview(partial)
        self.assertEqual(len(updated["tournament"]["players"]), 4)
        for player in updated["tournament"]["players"]:
            self.assertTrue(player["active"])
            self.assertEqual(player["results"], previous[player["id"]]["results"])
        self.assertEqual(updated["forecast"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
