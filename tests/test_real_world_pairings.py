"""US rule examples and independently constructed procedural regressions.

Numerical cases refer to US Chess 2026 rules 29E5c/e and 29E7. The artificial
inactive opponents below provide coherent reciprocal evidence without making
up games among the players whose next pairing is being tested.
"""
from copy import deepcopy
import unittest
import time
from unittest.mock import patch

from app.pairing import normalize_rules
from app.us_swiss import _Planner, SearchStopped, forecast_us
from app.validation import validate_tournament


def history_fixture(rows, scores=None):
    rounds = max(len(history) for _, history in rows)
    players = []
    for index, (rating, history) in enumerate(rows, 1):
        player = {"id": str(index), "name": f"Player {index}", "rating": rating,
                  "pairingNumber": index, "active": True, "results": [],
                  "initialScore": .5 * (rounds - sum(color in "WB" for color in history))}
        players.append(player)
        for rnd, color in enumerate(history, 1):
            if color == "x":
                player["results"].append({"round": rnd, "opponentId": None, "result": "ZERO", "color": None, "played": False})
                continue
            opponent_id = str(1000 + index * 100 + rnd)
            player["results"].append({"round": rnd, "opponentId": opponent_id, "result": "D", "color": color, "played": True})
            players.append({"id": opponent_id, "name": f"Prior opponent {index}/{rnd}", "rating": 1200,
                            "active": False, "entryRound": rnd, "withdrawnAfterRound": rnd,
                            "results": [{"round": rnd, "opponentId": str(index), "result": "D", "color": "B" if color == "W" else "W", "played": True}]})
        if scores is not None:
            player["initialScore"] += scores[index - 1] - .5 * rounds
    return validate_tournament({"name": "Procedural regression", "section": "Open", "rounds": rounds + 3,
                                "completedRounds": rounds, "players": players, "warnings": []})


def predict(tournament, **rules):
    return forecast_us(tournament, normalize_rules({"profile": "us-chess", "colorPolicy": "us-chess", **rules}))


def opponent_pairs(result):
    return {frozenset((pair["white"]["id"], pair["black"]["id"])) for pair in result["pairings"]}


class RealWorldUSPairingTests(unittest.TestCase):
    def test_29e5c_uses_smaller_equivalent_upper_switch(self):
        tournament = history_fixture([(2000, "WB"), (1980, "BW"), (1800, "WB"), (1500, "BW")])
        result = predict(tournament)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "4")), frozenset(("2", "3"))})
        self.assertIn("20 rating points", " ".join(result["procedure"]))
        for pair in result["pairings"]:
            expected = " / ".join("".join(r["color"] or "x" for r in pair[side]["results"]) for side in ("white", "black"))
            self.assertIn("Played color histories: " + expected, pair["explanations"])

    def test_29e5e_80_point_transposition_beats_cheaper_interchange(self):
        tournament = history_fixture([(2050, "WBW"), (1870, "BWB"), (1850, "WBW"), (1780, "BWB")])
        result = predict(tournament)
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "4")), frozenset(("2", "3"))})
        self.assertIn("Transposition of 70", " ".join(result["procedure"]))

    def test_29e5e_over_80_transposition_loses_to_smaller_interchange(self):
        tournament = history_fixture([(2050, "WBW"), (1870, "BWB"), (1850, "WBW"), (1750, "BWB")])
        result = predict(tournament)
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "2")), frozenset(("3", "4"))})
        self.assertIn("Interchange of 20", " ".join(result["procedure"]))

    def test_29e7_look_ahead_does_not_move_an_unavoidable_color_conflict(self):
        tournament = history_fixture([(2300, "BWB"), (2220, "BWB"), (2180, "BBW"), (2050, "BWB"),
                                      (2040, "BWB"), (1990, "WBW"), (1980, "WBW"), (1950, "WBW")])
        result = predict(tournament)
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "5")), frozenset(("2", "6")), frozenset(("3", "7")), frozenset(("4", "8"))})

    def test_29e7_odd_player_switch_is_evaluated_across_both_brackets(self):
        tournament = history_fixture([(2100, "BWB"), (2080, "BWB"), (1990, "WBW"),
                                      (2050, "WBW"), (1980, "BWB"), (1800, "BWB")], scores=[3, 3, 3, 2.5, 2.5, 2.5])
        result = predict(tournament)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "3")), frozenset(("2", "4")), frozenset(("5", "6"))})
        self.assertIn("50 rating points", " ".join(result["procedure"]))

    def test_threshold_is_not_the_eventual_opponents_rating_gap(self):
        tournament = history_fixture([(2400, "WB"), (2350, "BW"), (1400, "WB"), (900, "BW")])
        result = predict(tournament)
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "4")), frozenset(("2", "3"))})

    def test_over_80_ordinary_alternation_switch_is_not_made(self):
        tournament = history_fixture([(2200, "WB"), (2000, "BW"), (1700, "WB"), (1400, "BW")])
        result = predict(tournament)
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "3")), frozenset(("2", "4"))})

    def test_color_tie_uses_latest_different_round(self):
        tournament = history_fixture([(2000, "WBWB"), (1800, "BWWB")])
        result = predict(tournament)
        self.assertEqual(result["pairings"][0]["white"]["id"], "1")
        self.assertIn("round 2", " ".join(result["pairings"][0]["explanations"]))

    def test_color_tie_compares_rounds_not_compressed_history(self):
        tournament = history_fixture([(2000, "BWxBW"), (1800, "BWBxW")])
        result = predict(tournament)
        self.assertEqual(result["pairings"][0]["white"]["id"], "1")
        self.assertIn("round 4", " ".join(result["pairings"][0]["explanations"]))

    def test_equalization_outranks_alternation(self):
        tournament = history_fixture([(2000, "WBW"), (1800, "BxW")])
        result = predict(tournament)
        self.assertEqual(result["pairings"][0]["black"]["id"], "1")

    def test_higher_rank_gets_color_only_for_identical_histories(self):
        tournament = history_fixture([(2000, "WB"), (1800, "WB")])
        result = predict(tournament)
        self.assertEqual(result["pairings"][0]["white"]["id"], "1")

    def test_natural_split_uses_pairing_number_to_break_rating_ties(self):
        players = [{"id": id_, "name": id_, "rating": 1800, "pairingNumber": pairing, "active": True, "results": []}
                   for id_, pairing in (("a", 4), ("b", 1), ("c", 3), ("d", 2))]
        tournament = validate_tournament({"rounds": 5, "completedRounds": 0, "players": players})
        self.assertEqual(opponent_pairs(predict(tournament)), {frozenset(("b", "c")), frozenset(("d", "a"))})

    def test_forced_pair_and_colors_are_not_changed_by_optional_repairs(self):
        tournament = history_fixture([(2050, "WBW"), (1870, "BWB"), (1850, "WBW"), (1750, "BWB")])
        tournament["forcedPairs"] = [{"whiteId": "1", "blackId": "3"}]
        result = predict(tournament)
        self.assertIn(frozenset(("1", "3")), opponent_pairs(result))
        self.assertTrue(any(p["white"]["id"] == "1" and p["black"]["id"] == "3" for p in result["pairings"]))

    def test_explicit_virtual_points_change_brackets_without_changing_scores(self):
        tournament = validate_tournament({"rounds": 5, "completedRounds": 0, "players": [
            {"id": str(i), "name": str(i), "rating": 2400-i*100, "results": [], "virtualPoints": [{"round": 1, "points": 1}] if i <= 2 else []}
            for i in range(1, 5)]})
        result = predict(tournament)
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "2")), frozenset(("3", "4"))})
        self.assertTrue(all(p[side]["score"] == 0 for p in result["pairings"] for side in ("white", "black")))

    def test_simple_large_first_round_remains_fast_and_natural(self):
        tournament = validate_tournament({"rounds": 7, "completedRounds": 0, "players": [
            {"id": str(i), "name": str(i), "rating": 2500-i*10, "results": []} for i in range(1, 53)]})
        result = predict(tournament, maxNodes=20000, searchMillis=500)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(len(result["pairings"]), 26)
        self.assertIn(frozenset(("1", "27")), opponent_pairs(result))
        self.assertLess(result["stats"]["nodes"], 100)

    def test_odd_player_backtracks_when_lowest_cannot_face_lower_bracket(self):
        tournament = validate_tournament({"rounds": 5, "completedRounds": 0, "players": [
            {"id": str(i), "name": str(i), "rating": 2400-i*100, "initialScore": 2 if i <= 3 else 1, "results": []}
            for i in range(1, 7)], "prohibitedPairs": [["3", str(i)] for i in (4, 5, 6)]})
        result = predict(tournament)
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "3")), frozenset(("2", "4")), frozenset(("5", "6"))})

    def test_half_bye_exception_when_every_other_group_member_has_full_award(self):
        tournament = validate_tournament({"rounds": 5, "completedRounds": 1, "players": [
            {"id": "1", "name": "Half bye", "rating": 1800, "initialScore": .5, "results": [{"round": 1, "result": "HBYE", "played": False}]},
            {"id": "2", "name": "Pairing bye", "rating": 1700, "results": [{"round": 1, "result": "BYE", "played": False}]},
            {"id": "3", "name": "Unplayed win", "rating": 1600, "results": [{"round": 1, "result": "FORFEIT_WIN", "played": False}]},
        ]})
        result = predict(tournament)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(result["bye"]["id"], "1")
        self.assertTrue(any("28L4 exception" in warning for warning in result["warnings"]))

    def test_strict_last_round_exception_requires_explicit_prize_contenders(self):
        tournament = history_fixture([(2000, "WWBB"), (1800, "WWBB")])
        tournament["rounds"] = 5
        for player in tournament["players"]:
            if player["active"]:
                player["prizeContender"] = True
        self.assertEqual(predict(tournament, colorPolicy="strict")["status"], "blocked")
        result = predict(tournament, colorPolicy="strict", allowLastRoundExceptions=True)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertTrue(any("29E5f1" in warning for warning in result["warnings"]))
        tournament["players"][0]["prizeContender"] = False
        self.assertEqual(predict(tournament, colorPolicy="strict", allowLastRoundExceptions=True)["status"], "blocked")

    def test_strict_last_round_exception_does_not_apply_to_earlier_rounds(self):
        tournament = history_fixture([(2000, "WWBB"), (1800, "WWBB")])
        for player in tournament["players"]:
            if player["active"]:
                player["prizeContender"] = True
        self.assertEqual(predict(tournament, colorPolicy="strict", allowLastRoundExceptions=True)["status"], "blocked")

    def test_color_search_timeout_preserves_complete_natural_round(self):
        tournament = history_fixture([(2000, "WB"), (1800, "WB"), (1700, "BW"), (1600, "BW")])
        def stop(self, pairs, **kwargs):
            self.last_color_candidate = (pairs, [], self.quality(pairs))
            self.stopped = True
            raise SearchStopped
        with patch.object(_Planner, "color_plans", stop):
            result = predict(tournament)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(len(result["pairings"]), 2)
        self.assertFalse(result["stats"]["exhaustive"])

    def test_one_reentry_cannot_repeat_an_unchanged_old_opponent(self):
        tournament = validate_tournament({"rounds": 5, "completedRounds": 1, "players": [
            {"id": "1", "name": "Old A", "rating": 1800, "active": False, "withdrawnAfterRound": 1,
             "results": [{"round": 1, "opponentId": "2", "color": "W", "result": "W"}]},
            {"id": "2", "name": "B", "rating": 1700,
             "results": [{"round": 1, "opponentId": "1", "color": "B", "result": "L"}]},
            {"id": "3", "name": "New A", "rating": 1800, "reentryOf": "1", "entryRound": 2, "initialScore": 1, "results": []},
        ]})
        result = predict(tournament)
        self.assertEqual(result["status"], "blocked")

    def test_two_new_reentries_can_meet_and_do_not_inherit_old_colors(self):
        tournament = validate_tournament({"rounds": 5, "completedRounds": 1, "players": [
            {"id": "1", "name": "Old A", "rating": 1800, "active": False, "withdrawnAfterRound": 1,
             "results": [{"round": 1, "opponentId": "2", "color": "W", "result": "W"}]},
            {"id": "2", "name": "Old B", "rating": 1700, "active": False, "withdrawnAfterRound": 1,
             "results": [{"round": 1, "opponentId": "1", "color": "B", "result": "L"}]},
            {"id": "3", "name": "New A", "rating": 1800, "reentryOf": "1", "entryRound": 2, "initialScore": 1, "results": []},
            {"id": "4", "name": "New B", "rating": 1700, "reentryOf": "2", "entryRound": 2, "results": []},
        ]})
        result = predict(tournament)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(result["pairings"][0]["white"]["id"], "3")

    def test_standard_us_policy_ignores_dormant_custom_color_limits(self):
        tournament = history_fixture([(2000, "WW"), (1800, "WW")])
        ordinary = predict(tournament, maxColorImbalance=2, maxConsecutiveColors=2)
        dormant = predict(tournament, maxColorImbalance=6, maxConsecutiveColors=6)
        self.assertEqual(ordinary["status"], "ok", ordinary["issues"])
        self.assertEqual(opponent_pairs(ordinary), opponent_pairs(dormant))
        self.assertEqual(ordinary["warnings"], dormant["warnings"])
        self.assertEqual(ordinary["objective"], dormant["objective"])
        self.assertTrue(any("series" in warning for warning in ordinary["warnings"]))
        disabled = predict(tournament, colorPolicy="strict", strictColors=False, maxColorImbalance=6, maxConsecutiveColors=6)
        self.assertEqual(ordinary["objective"], disabled["objective"])
        self.assertFalse(any("Custom strict color policy" in warning for warning in disabled["warnings"]))

    def test_top_down_continues_after_an_unrepairable_earlier_board(self):
        tournament = history_fixture([(3000, "BWB"), (2000, "WBW"), (1990, "BWB"),
                                      (1000, "BWB"), (700, "WBW"), (690, "BWB")])
        result = predict(tournament, usColorMethod="top-down")
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "4")), frozenset(("2", "6")), frozenset(("3", "5"))})
        self.assertTrue(any("29E6b" in warning for warning in result["warnings"]))

    def test_published_29e7_direct_swap_keeps_intervening_board(self):
        tournament = history_fixture([(2320, "WBWB"), (2278, "BWBW"), (2212, "BWBW"), (2199, "WBWB"), (2178, "WBWB"),
                                      (1980, "WBWB"), (1951, "WBWB"), (1910, "BWBW"), (1896, "BWBW"), (1800, "WBWB")])
        result = predict(tournament, usTranspositionStyle="direct-swap")
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "6")), frozenset(("2", "7")), frozenset(("3", "10")), frozenset(("4", "9")), frozenset(("5", "8"))})
        self.assertTrue(any("Direct-swap" in warning for warning in result["warnings"]))

    def test_published_29e7_shift_moves_intervening_board_consistently(self):
        tournament = history_fixture([(2320, "WBWB"), (2278, "BWBW"), (2212, "BWBW"), (2199, "WBWB"), (2178, "WBWB"),
                                      (1980, "WBWB"), (1951, "WBWB"), (1910, "BWBW"), (1896, "BWBW"), (1800, "WBWB")])
        result = predict(tournament, usTranspositionStyle="shift")
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "6")), frozenset(("2", "7")), frozenset(("3", "10")), frozenset(("4", "8")), frozenset(("5", "9"))})
        self.assertIn("Shift transposition of 34", " ".join(result["procedure"]))

    def test_final_three_way_displacement_cannot_hide_behind_cheap_steps(self):
        tournament = history_fixture([(2400, "WB"), (2350, "WB"), (2300, "BW"), (2000, "BW"), (1950, "WB"), (1900, "BW")])
        players = [dict(p, score=1, pairingScore=1) for p in tournament["players"] if p["active"]]
        planner = _Planner(tournament, normalize_rules({"colorPolicy": "us-chess", "usTranspositionStyle": "shift"}), players, time.perf_counter())
        original = (("1", "4"), ("2", "5"), ("3", "6"))
        intermediate = (("1", "5"), ("2", "4"), ("3", "6"))
        final = (("1", "6"), ("2", "4"), ("3", "5"))
        self.assertEqual(planner._component_switches(original, intermediate), [("transposition", 50)])
        self.assertEqual(planner._component_switches(intermediate, final), [("transposition", 50)])
        self.assertEqual(planner._component_switches(original, final), [("shift transposition", 100)])
        def only_final(self, pairs, **kwargs):
            yield final
        with patch.object(_Planner, "_neighbors", only_final):
            variants = planner.color_plans(original)
        self.assertEqual(planner.signature(variants[0][0]), planner.signature(original))

    def test_unrated_displacement_exemption_is_per_moved_pair_in_a_shift(self):
        tournament = history_fixture([(2400, ""), (2350, ""), (2300, ""), (2000, ""), (1950, ""), (0, "")])
        players = [dict(p, score=0, pairingScore=0) for p in tournament["players"]]
        planner = _Planner(tournament, normalize_rules(), players, time.perf_counter())
        original = (("1", "4"), ("2", "5"), ("3", "6"))
        final = (("1", "6"), ("2", "4"), ("3", "5"))
        self.assertEqual(planner._component_switches(original, final), [("shift transposition", 50)])

    def test_larger_mixed_interchange_cycles_are_not_mislabeled_as_shifts(self):
        tournament = history_fixture([(2400, ""), (2350, ""), (2300, ""), (2000, ""), (1950, ""), (1900, "")])
        planner = _Planner(tournament, normalize_rules(), [dict(p, score=0, pairingScore=0) for p in tournament["players"]], time.perf_counter())
        self.assertIsNone(planner._component_switches((("1", "4"), ("2", "5"), ("3", "6")), (("1", "2"), ("3", "4"), ("5", "6"))))

    def test_assigned_pairing_rating_controls_bye_eligibility_order(self):
        tournament = validate_tournament({"rounds": 5, "completedRounds": 0, "players": [
            {"id": "1", "name": "A", "rating": 2000, "results": []},
            {"id": "2", "name": "B", "rating": 1800, "results": []},
            {"id": "3", "name": "Assigned", "rating": 0, "pairingRating": 1000, "results": []},
        ]})
        self.assertEqual(predict(tournament)["bye"]["id"], "3")

    def test_assigned_pairing_rating_controls_ordinary_odd_player(self):
        tournament = validate_tournament({"rounds": 5, "completedRounds": 0, "players": [
            {"id": str(i), "name": str(i), "rating": 2400-i*100 if i != 3 else 0,
             "pairingRating": 1000 if i == 3 else 2400-i*100, "initialScore": 2 if i <= 3 else 1, "results": []}
            for i in range(1, 7)]})
        result = predict(tournament)
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "2")), frozenset(("3", "4")), frozenset(("5", "6"))})

    def test_assigned_rating_does_not_receive_unrated_switch_exemption(self):
        tournament = history_fixture([(2200, "WB"), (2000, "BW"), (1700, "WB"), (0, "BW")])
        next(p for p in tournament["players"] if p["id"] == "4")["pairingRating"] = 1400
        result = predict(tournament)
        self.assertEqual(opponent_pairs(result), {frozenset(("1", "3")), frozenset(("2", "4"))})


if __name__ == "__main__":
    unittest.main()
