"""Historical replay must not turn later source knowledge into earlier inputs."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from app.demo import demo_tournament
from app.dutch import availability
from app.importers import load_tournament
from app.replay import replay


ROOT = Path(__file__).resolve().parents[1]


def small_event(count=4):
    return {"name": "Replay test", "section": "Open", "rounds": 4, "completedRounds": 0,
            "players": [{"id": str(i + 1), "pairingNumber": i + 1, "name": f"Player {i + 1}",
                         "rating": 2000 - i * 100, "active": True, "results": []} for i in range(count)],
            "warnings": []}


def add_game(tournament, rnd, white, black, result="D", known_colors=True):
    opposite = {"W": "L", "L": "W", "D": "D", "UNPLAYED": "UNPLAYED"}
    by_id = {p["id"]: p for p in tournament["players"]}
    by_id[str(white)]["results"].append({"round": rnd, "opponentId": str(black),
                                       "color": "W" if known_colors else None, "result": result,
                                       "played": result != "UNPLAYED"})
    by_id[str(black)]["results"].append({"round": rnd, "opponentId": str(white),
                                       "color": "B" if known_colors else None, "result": opposite[result],
                                       "played": result != "UNPLAYED"})


def opponents(result):
    return {frozenset((p["white"]["id"], p["black"]["id"])) for p in result["forecast"]["pairings"]}


class ReplayTests(unittest.TestCase):
    def test_cutoff_does_not_mutate_source(self):
        source = demo_tournament()
        before = deepcopy(source)
        result = replay(source, 1)
        self.assertEqual(source, before)
        self.assertTrue(all(r["round"] <= 1 for p in result["tournament"]["players"] for r in p["results"]))
        self.assertEqual(result["forecast"]["round"], 2)

    def test_future_results_and_snapshot_totals_cannot_change_early_pairings(self):
        source = small_event()
        add_game(source, 1, 1, 3, "W")
        add_game(source, 1, 4, 2, "L")
        source["completedRounds"] = 1
        baseline = replay(source, 1)
        changed = deepcopy(source)
        add_game(changed, 2, 2, 1, "W")
        add_game(changed, 2, 3, 4, "L")
        changed["latestRound"] = 2
        changed["warnings"] = ["Future round 2 has a different score"]
        for p in changed["players"]:
            p["reportedScore"] = 99
            p["score"] = 99
            p["pairingScore"] = 99
        later = replay(changed, 1)
        self.assertEqual(opponents(baseline), opponents(later))
        self.assertEqual(baseline["forecast"]["inputHash"], later["forecast"]["inputHash"])
        self.assertEqual(baseline["forecast"]["objective"], later["forecast"]["objective"])
        self.assertEqual(len(later["sourceSummary"]["reportedTotals"]), 4)
        self.assertTrue(all("reportedScore" not in p and "score" not in p and "pairingScore" not in p for p in later["tournament"]["players"]))

    def test_snapshot_inactive_flags_do_not_retroactively_withdraw_players(self):
        source = small_event()
        baseline = replay(source, 0)
        source["players"][0]["active"] = False
        changed = replay(source, 0)
        self.assertEqual(opponents(baseline), opponents(changed))
        self.assertEqual(baseline["forecast"]["inputHash"], changed["forecast"]["inputHash"])
        self.assertTrue(changed["tournament"]["players"][0]["active"])
        self.assertTrue(any("Undated" in note for note in changed["sourceSummary"]["assumptions"]))

    def test_dated_withdrawal_and_entry_boundaries_apply_at_the_right_round(self):
        source = small_event()
        source["players"][0]["active"] = False
        source["players"][0]["withdrawnAfterRound"] = 1
        source["players"][3]["entryRound"] = 2
        first = replay(source, 0)
        self.assertTrue(first["tournament"]["players"][0]["active"])
        self.assertFalse(first["tournament"]["players"][3]["active"])
        next_round = replay(source, 1)
        self.assertFalse(next_round["tournament"]["players"][0]["active"])
        self.assertTrue(next_round["tournament"]["players"][3]["active"])

    def test_round_comparison_uses_pairings_even_when_outcome_is_unresolved(self):
        source = small_event()
        add_game(source, 1, 1, 3, "UNPLAYED")
        add_game(source, 1, 4, 2, "UNPLAYED")
        result = replay(source, 0)
        comparison = result["comparison"]
        self.assertEqual(result["forecast"]["status"], "ok")
        self.assertEqual(comparison["opponentCompared"], 2)
        self.assertEqual(comparison["opponentMatches"], 2)
        self.assertFalse(result["coverage"][0]["complete"])
        self.assertEqual(result["coverage"][0]["unresolvedPlayers"], ["1", "2", "3", "4"])

    def test_unknown_observed_colors_do_not_become_color_failures(self):
        source = small_event()
        add_game(source, 1, 1, 3, known_colors=False)
        add_game(source, 1, 4, 2, known_colors=False)
        result = replay(source, 0)
        self.assertEqual(result["comparison"]["opponentMatches"], 2)
        self.assertEqual(result["comparison"]["colorCompared"], 0)
        self.assertIsNone(result["comparison"]["colorRate"])
        self.assertEqual(result["comparison"]["missingColorBoards"], 2)
        self.assertFalse(result["coverage"][0]["complete"])

    def test_invalid_named_opponent_byes_cannot_inflate_matches(self):
        source = small_event(2)
        source["players"][0]["results"] = [{"round": 1, "opponentId": "2", "result": "BYE", "played": False}]
        source["players"][1]["results"] = [{"round": 1, "opponentId": "1", "result": "BYE", "played": False}]
        result = replay(source, 0)
        self.assertEqual(result["comparison"]["observedBoards"], 1)
        self.assertEqual(result["comparison"]["invalidAssignmentBoards"], 1)
        self.assertEqual(result["comparison"]["opponentCompared"], 0)
        self.assertIsNone(result["comparison"]["opponentRate"])

    def test_result_rows_without_a_valid_opponent_remain_explicitly_unobserved(self):
        source = small_event()
        baseline = replay(source, 0)
        for player, record in zip(source["players"], (
            {"result": "W"}, {"result": "UNPLAYED", "opponentId": "missing"},
            {"result": "L", "opponentId": "3"}, {"result": "ZERO"},
        )):
            player["results"] = [{"round": 1, **record}]
        result = replay(source, 0)
        comparison = result["comparison"]
        self.assertEqual(result["forecast"]["inputHash"], baseline["forecast"]["inputHash"])
        self.assertEqual(comparison["observedBoards"], 0)
        self.assertEqual(comparison["unobservedPlayers"], ["1", "2", "3"])
        self.assertEqual(comparison["missingOpponentPlayers"], ["1"])
        self.assertEqual(comparison["unknownOpponentPlayers"], ["2"])
        self.assertEqual(comparison["selfPairedPlayers"], ["3"])
        self.assertEqual(comparison["opponentCompared"], 0)
        self.assertIsNone(comparison["opponentRate"])

    def test_post_withdrawal_zero_records_are_not_missed_pairing_byes(self):
        source = small_event(3)
        source["players"][2]["withdrawnAfterRound"] = 0
        source["players"][2]["active"] = False
        source["players"][2]["results"] = [{"round": 1, "opponentId": None, "result": "ZERO", "played": False}]
        add_game(source, 1, 1, 2)
        result = replay(source, 0)
        self.assertEqual(result["forecast"]["status"], "ok")
        self.assertEqual(result["comparison"]["byeCompared"], 0)
        self.assertFalse(result["comparison"]["observedByes"][0]["comparable"])
        self.assertIsNone(result["comparison"]["observedByes"][0]["match"])

    def test_us_chess_u_is_unspecified_availability_and_not_a_pairing_bye(self):
        source = small_event(3)
        source["players"][2]["results"] = [{"round": 1, "result": "ZERO", "played": False,
                                              "sourceCode": "U", "availabilityUnknown": True}]
        add_game(source, 1, 1, 2)
        before = replay(source, 0)
        comparison = before["comparison"]
        self.assertEqual(comparison["byeCompared"], 0)
        self.assertIsNone(comparison["byeRate"])
        self.assertEqual(comparison["availabilityUnknownPlayers"], ["3"])
        self.assertEqual(comparison["unobservedPlayers"], ["3"])
        row = comparison["observedByes"][0]
        self.assertEqual(row["sourceCode"], "U")
        self.assertEqual(row["evidenceStatus"], "availability-unknown")
        self.assertFalse(row["comparable"])
        self.assertIsNone(row["match"])
        revealed = replay(source, 1)
        self.assertTrue(revealed["tournament"]["players"][2]["active"])
        self.assertEqual(revealed["tournament"]["players"][2]["results"][0]["result"], "ZERO")

    def test_outside_participation_game_blocks_only_after_it_is_revealed(self):
        for boundary in ({"entryRound": 2}, {"withdrawnAfterRound": 0}):
            with self.subTest(boundary=boundary):
                source = small_event()
                source["players"][0].update(boundary)
                add_game(source, 1, 1, 3, "W")
                add_game(source, 1, 4, 2)
                before = replay(source, 0)
                self.assertEqual(before["forecast"]["status"], "ok", before["forecast"]["issues"])
                self.assertEqual(before["comparison"]["outsideParticipationBoards"], 1)
                self.assertFalse(before["coverage"][0]["complete"])
                revealed = replay(source, 1)
                self.assertEqual(revealed["forecast"]["status"], "blocked")
                self.assertEqual(revealed["forecast"]["blockingReason"], "history")
                self.assertFalse(revealed["roundStatus"]["historyComplete"])
                self.assertTrue(any("outside the declared entry/withdrawal interval" in issue for issue in revealed["roundStatus"]["issues"]))

    def test_conflicting_next_round_evidence_is_excluded_from_rates(self):
        source = small_event()
        add_game(source, 1, 1, 3)
        add_game(source, 1, 4, 2)
        source["players"][0]["results"].append({"round": 1, "opponentId": None, "color": None, "result": "HBYE", "played": False})
        result = replay(source, 0)
        self.assertEqual(result["comparison"]["observedBoards"], 2)
        self.assertEqual(result["comparison"]["conflictingBoards"], 1)
        self.assertEqual(result["comparison"]["opponentCompared"], 1)
        self.assertEqual(result["comparison"]["opponentMatches"], 1)

    def test_incomplete_history_blocks_instead_of_scoring_zero_accuracy(self):
        source = small_event()
        add_game(source, 1, 1, 3, "UNPLAYED")
        add_game(source, 1, 4, 2)
        add_game(source, 2, 2, 1)
        add_game(source, 2, 3, 4)
        result = replay(source, 1)
        self.assertEqual(result["forecast"]["status"], "blocked")
        self.assertEqual(result["comparison"]["status"], "forecast-blocked")
        self.assertEqual(result["comparison"]["opponentCompared"], 0)
        self.assertIsNone(result["comparison"]["opponentRate"])

    def test_undated_director_locks_do_not_leak_from_live_snapshot(self):
        source = small_event()
        baseline = replay(source, 0)
        source["forcedPairs"] = [["1", "2"]]
        source["prohibitedPairs"] = [["1", "3"]]
        ignored = replay(source, 0)
        self.assertEqual(opponents(baseline), opponents(ignored))
        self.assertEqual(baseline["forecast"]["inputHash"], ignored["forecast"]["inputHash"])
        self.assertTrue(any("Undated next-round director locks" in a for a in ignored["sourceSummary"]["assumptions"]))
        source["replayKnownRestrictions"] = True
        declared = replay(source, 0)
        self.assertIn(frozenset(("1", "2")), opponents(declared))
        self.assertNotIn(frozenset(("1", "3")), opponents(declared))

    def test_premerge_schedules_never_cross_and_can_have_two_allocated_byes(self):
        source = small_event(6)
        source["sourceSettings"] = {"mergeRound": 2}
        for p in source["players"]:
            p["schedule"] = "A" if int(p["id"]) <= 3 else "B"
        result = replay(source, 0)
        self.assertEqual(result["forecast"]["status"], "ok", result["forecast"]["issues"])
        self.assertEqual(result["forecast"]["scheduleContext"]["mode"], "separate")
        self.assertEqual(len(result["forecast"]["pairings"]), 2)
        self.assertEqual(len(result["forecast"]["byes"]), 2)
        self.assertIsNone(result["forecast"]["bye"])
        for pair in result["forecast"]["pairings"]:
            self.assertEqual(pair["white"]["schedule"], pair["black"]["schedule"])
            self.assertEqual(pair["schedule"], pair["white"]["schedule"])

    def test_schedule_merge_applies_to_next_round(self):
        source = small_event(4)
        source["sourceSettings"] = {"mergeRound": 2}
        for p in source["players"]:
            p["schedule"] = "A" if int(p["id"]) % 2 else "B"
        add_game(source, 1, 1, 3)
        add_game(source, 1, 4, 2)
        result = replay(source, 1)
        self.assertEqual(result["forecast"]["status"], "ok", result["forecast"]["issues"])
        self.assertEqual(result["forecast"]["scheduleContext"]["mode"], "merged")
        self.assertTrue(all(p["white"]["schedule"] != p["black"]["schedule"] for p in result["forecast"]["pairings"]))

    def test_phase_engine_error_does_not_masquerade_as_missing_history(self):
        source = small_event()
        source["sourceSettings"] = {"mergeRound": 2}
        for p in source["players"]:
            p["schedule"] = "A" if int(p["id"]) <= 2 else "B"
        unavailable = {**availability(), "available": False, "reason": "Fixture missing native engine"}
        with patch("app.dutch.availability", return_value=unavailable):
            result = replay(source, 0, {"profile": "fide-dutch"})
        self.assertEqual(result["forecast"]["status"], "blocked")
        self.assertEqual(result["forecast"]["blockingReason"], "engine")
        self.assertEqual(result["forecast"]["pairings"], [])

    def test_round_coverage_is_independent_of_missing_future_seed_requirement(self):
        source = small_event()
        for p in source["players"]:
            p.pop("pairingNumber")
        add_game(source, 1, 1, 3)
        add_game(source, 1, 4, 2)
        result = replay(source, 1, {"pairingOrder": "pairing-number"})
        self.assertTrue(result["coverage"][0]["complete"])
        self.assertTrue(result["roundStatus"]["historyComplete"])
        self.assertEqual(result["forecast"]["status"], "blocked")

    def test_cross_schedule_lock_is_not_silently_dropped(self):
        source = small_event()
        source["sourceSettings"] = {"mergeRound": 2}
        source["players"][0]["schedule"] = source["players"][1]["schedule"] = "A"
        source["players"][2]["schedule"] = source["players"][3]["schedule"] = "B"
        source["forcedPairs"] = [["1", "3"]]
        source["replayKnownRestrictions"] = True
        result = replay(source, 0)
        self.assertEqual(result["forecast"]["status"], "blocked")
        self.assertIn("spans unmerged schedules", " ".join(result["forecast"]["issues"]))

    def test_finished_event_has_no_invented_next_round_comparison(self):
        result = replay(demo_tournament(), 7)
        self.assertEqual(result["comparison"]["status"], "finished")
        self.assertEqual(result["comparison"]["opponentCompared"], 0)
        self.assertEqual(result["targetRound"], 8)
        self.assertEqual(result["forecast"]["status"], "blocked")

    def test_invalid_cutoff_rejected(self):
        for cutoff in (-1, True, .5, 99, "1"):
            with self.assertRaisesRegex(ValueError, "cutoff"):
                replay(small_event(), cutoff)


class OriginalTournamentReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = load_tournament(ROOT / "Louisiana_State_Championship_2026_pairings_no_rematches.xlsx")

    def test_round_one_partitions_real_schedules_and_preserves_announced_byes(self):
        result = replay(self.source, 0)
        self.assertEqual(result["sourceSummary"]["players"], 51)
        self.assertEqual(result["forecast"]["status"], "ok", result["forecast"]["issues"])
        self.assertEqual(len(result["forecast"]["pairings"]), 24)
        self.assertEqual(len(result["forecast"]["requestedByes"]), 3)
        self.assertEqual(result["comparison"]["observedBoards"], 24)
        self.assertEqual(result["comparison"]["opponentCompared"], 24)
        self.assertEqual(result["sourceSummary"]["lastCompleteRound"], 1)
        self.assertTrue(result["coverage"][0]["complete"])
        for pair in result["forecast"]["pairings"]:
            self.assertEqual(pair["white"]["schedule"], pair["black"]["schedule"])

    def test_round_two_forecast_uses_only_complete_first_round(self):
        result = replay(self.source, 1)
        self.assertEqual(result["forecast"]["status"], "ok", result["forecast"]["issues"])
        self.assertEqual(result["forecast"]["scheduleContext"]["mode"], "merged")
        self.assertEqual(result["targetRound"], 2)
        self.assertTrue(result["roundStatus"]["historyComplete"])
        self.assertTrue(result["sourceSummary"]["reportedTotals"])
        self.assertEqual(result["comparison"]["conflictingBoards"], 1)

    def test_later_rounds_block_on_real_missing_and_conflicting_evidence(self):
        for cutoff in (2, 3, 6):
            result = replay(self.source, cutoff)
            self.assertEqual(result["forecast"]["status"], "blocked")
            self.assertFalse(result["roundStatus"]["historyComplete"])
            self.assertIsNone(result["comparison"]["opponentRate"])
        round_two = replay(self.source, 2)
        self.assertIn("23", round_two["roundStatus"]["unresolvedPlayers"])
        self.assertIn("25", round_two["roundStatus"]["duplicatePlayers"])

    @unittest.skipUnless(availability()["available"], "Native Dutch dependency is optional")
    def test_native_dutch_handles_schedule_local_tpn_before_merge(self):
        result = replay(self.source, 0, {"profile": "fide-dutch"})
        self.assertEqual(result["forecast"]["status"], "ok", result["forecast"]["issues"])
        self.assertEqual(len(result["forecast"]["pairings"]), 24)
        self.assertEqual(len(result["forecast"]["phaseForecasts"]), 2)
        for phase in result["forecast"]["phaseForecasts"]:
            self.assertIn("BBP Pairings", phase["forecast"]["engine"])
            self.assertTrue(phase["forecast"]["engineDetails"]["available"])


if __name__ == "__main__":
    unittest.main()
