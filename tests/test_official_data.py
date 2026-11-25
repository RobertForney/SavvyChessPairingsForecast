"""Pin official visible evidence, its integrity and the separately attributed setup."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from app.official_data import FIXTURE_PATH, _integrity, _round_record, official_metadata, official_tournament
from app.replay import replay
from app.sample_data import sample_tournament


class OfficialDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tournament = official_tournament()
        cls.metadata = official_metadata()

    def test_fixture_is_compact_visible_text_not_html(self):
        raw = FIXTURE_PATH.read_bytes()
        fixture = json.loads(raw)
        self.assertEqual(fixture["schemaVersion"], 1)
        self.assertEqual(len(fixture["rows"]), 51)
        self.assertEqual(self.metadata["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.metadata["sha256"], "afddccba172f2377eefc2edc4a6c724f9c83a7c39a425c8cb2cac92c84cbcd4c")
        self.assertNotIn('"html"', raw.decode("utf-8"))
        self.assertLess(len(raw), 30_000)
        self.assertEqual(fixture["source"]["url"], "https://ratings.uschess.org/event/202609070433")
        self.assertEqual(fixture["source"]["capturedAt"], "2026-11-20T03:39:34.260Z")
        self.assertFalse(fixture["source"]["live"])

    def test_reciprocal_games_totals_and_per_round_counts(self):
        metadata = self.metadata
        self.assertEqual((metadata["players"], metadata["rounds"], metadata["historicalRecords"]), (51, 7, 357))
        self.assertTrue(metadata["reciprocal"])
        self.assertTrue(metadata["totalsMatch"])
        self.assertEqual(metadata["totalPoints"], 172)
        self.assertEqual(metadata["resultCodes"], {"B": 2, "D": 38, "F": 2, "H": 12, "L": 143, "U": 15, "W": 143, "X": 2})
        self.assertEqual([r["pairings"] for r in metadata["roundEvidence"]], [24, 25, 23, 25, 23, 24, 20])
        self.assertEqual([r["availabilityUnknownRecords"] for r in metadata["roundEvidence"]], [0, 0, 0, 0, 3, 2, 10])
        for player in self.tournament["players"]:
            self.assertEqual(sum(r["points"] for r in player["results"]), player["reportedScore"])

    def test_final_rank_never_becomes_seed_or_post_event_rating(self):
        players = {p["id"]: p for p in self.tournament["players"]}
        self.assertEqual(players["14069988"]["sourceId"], "1")  # Winner Robert Forney.
        self.assertEqual(players["14069988"]["pairingNumber"], 5)
        self.assertEqual(players["12845401"]["sourceId"], "4")
        self.assertEqual(players["12845401"]["pairingNumber"], 1)
        self.assertEqual(players["13743486"]["rating"], 2253)  # Pre-event regular rating.
        self.assertEqual(players["13743486"]["pairingRating"], 2317)  # Attributed workbook setup.
        self.assertNotIn("seed", players["14069988"])
        self.assertEqual([p["pairingNumber"] for p in self.tournament["players"]], list(range(1, 52)))
        unknown = {"33245318", "31717987", "33234618"}
        self.assertEqual(set(self.metadata["ratingUnknownPlayers"]), unknown)
        for identifier in unknown:
            self.assertEqual(players[identifier]["rating"], 0)
            self.assertFalse(players[identifier]["ratingKnown"])
        self.assertEqual(self.metadata["schedules"], {"Open 3-Day": 18, "Open 4-Day": 33})
        self.assertEqual(self.metadata["mergeRound"], 2)
        self.assertIn("setupOverlay", self.tournament["provenance"])
        self.assertFalse(self.tournament["sourceSettings"]["accelerationConfirmed"])

    def test_no_future_availability_requests_or_forfeit_colors_inferred(self):
        self.assertTrue(all(p["active"] for p in self.tournament["players"]))
        self.assertTrue(all(not p["scheduledByes"] for p in self.tournament["players"]))
        self.assertTrue(all("withdrawnAfterRound" not in p and "entryRound" not in p for p in self.tournament["players"]))
        records = [r for p in self.tournament["players"] for r in p["results"]]
        unavailable = [r for r in records if r["sourceCode"] == "U"]
        self.assertEqual(len(unavailable), 15)
        for record in unavailable:
            self.assertEqual(record["result"], "ZERO")
            self.assertTrue(record["availabilityUnknown"])
            self.assertFalse(record["played"])
            self.assertIsNone(record["opponentId"])
            self.assertIsNone(record["color"])
        forfeits = [r for r in records if r["sourceCode"] in {"X", "F"}]
        self.assertEqual(len(forfeits), 4)
        self.assertTrue(all(not r["played"] and r["color"] is None and r["opponentId"] for r in forfeits))
        faid = next(p for p in self.tournament["players"] if p["id"] == "33234618")
        self.assertEqual([r["sourceCode"] for r in faid["results"]], ["H", "F", "H", "F", "U", "U", "U"])

    def test_exact_member_id_comparison_and_known_workbook_differences(self):
        comparison = self.metadata["workbookComparison"]
        self.assertEqual(comparison["identityMethod"], "exact-us-chess-id")
        self.assertEqual(comparison["matchedPlayers"], 51)
        self.assertEqual(comparison["missingIds"], [])
        self.assertEqual(comparison["extraIds"], [])
        self.assertEqual(comparison["ratingsChanged"], 13)
        self.assertEqual(comparison["resultsChanged"], 25)
        self.assertEqual(comparison["colorsChanged"], 117)
        self.assertEqual(comparison["officialOnlyRecords"], 138)
        self.assertEqual(comparison["requestedByeConflicts"], 4)
        self.assertEqual(comparison["snapshotTotalsChanged"], 45)
        differences = comparison["details"]["results"]
        self.assertEqual(sum(d["round"] == 1 for d in differences), 16)
        etienne = next(d for d in differences if d["playerId"] == "12546378" and d["round"] == 2)
        self.assertEqual(etienne["workbook"][0]["result"], "UNPLAYED")
        self.assertEqual(etienne["official"]["result"], "FORFEIT_WIN")
        self.assertEqual(etienne["official"]["opponentId"], "33234618")

    def test_bad_tokens_reciprocity_and_totals_fail_instead_of_guessing(self):
        for token in ("?", "W 2", "W 2 ⚪ extra", "X 2 ⚪", "H 2", "W 999 ⚪"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                _round_record(token, 1, {"2": "12345678"}, 2)
        broken = deepcopy(self.tournament)
        broken["players"][0]["results"][0]["result"] = "L"
        with self.assertRaisesRegex(ValueError, "reciprocity"):
            _integrity(broken)
        broken = deepcopy(self.tournament)
        broken["players"][0]["reportedScore"] += .5
        with self.assertRaisesRegex(ValueError, "points disagree"):
            _integrity(broken)

    def test_unknown_identity_blocks_setup_overlay(self):
        original = sample_tournament()
        original["players"][0]["usChessId"] = "99999999"
        with patch("app.official_data.sample_tournament", return_value=original):
            with self.assertRaisesRegex(ValueError, "identity review"):
                official_tournament()

    def test_helpers_are_independent_and_early_replay_excludes_final_snapshot_fields(self):
        first = official_tournament()
        first["players"][0]["name"] = "Changed"
        self.assertNotEqual(official_tournament()["players"][0]["name"], "Changed")
        early = replay(self.tournament, 1)
        self.assertEqual(early["forecast"]["status"], "ok")
        self.assertTrue(all(r["round"] <= 1 for p in early["tournament"]["players"] for r in p["results"]))
        self.assertTrue(all("sourceId" not in p and "reportedScore" not in p for p in early["tournament"]["players"]))
        for cutoff in range(2, 7):
            self.assertEqual(replay(self.tournament, cutoff)["forecast"]["status"], "ok")
        self.assertEqual(replay(self.tournament, 7)["forecast"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
