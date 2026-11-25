"""Inputs that affect real pairing decisions, independent of a specific engine."""
from copy import deepcopy
import unittest

from app.importers import parse_text
from app.pairing import forecast, pairing_score, normalize_rules
from app.validation import validate_tournament


def event(count=4):
    return {"name": "Pairing input fixture", "section": "Open", "rounds": 5, "completedRounds": 0,
            "players": [{"id": str(i), "name": f"Player {i}", "rating": 2500 - 100 * i,
                         "active": True, "results": []} for i in range(1, count + 1)]}


def generic(tournament, **rules):
    return forecast(validate_tournament(tournament), {"profile": "fide-constraints", **rules})


def pair_set(result):
    return {frozenset((p["white"]["id"], p["black"]["id"])) for p in result["pairings"]}


class PairingInputTests(unittest.TestCase):
    def test_definitive_pairing_numbers_change_starting_order(self):
        tournament = event()
        for player, number in zip(tournament["players"], (4, 1, 3, 2)):
            player["pairingNumber"] = number
        rating = generic(tournament)
        numbered = generic(tournament, pairingOrder="pairing-number")
        self.assertEqual(pair_set(rating), {frozenset(("1", "3")), frozenset(("2", "4"))})
        self.assertEqual(pair_set(numbered), {frozenset(("2", "3")), frozenset(("4", "1"))})

    def test_pairing_number_mode_requires_complete_numbering(self):
        result = generic(event(), pairingOrder="pairing-number")
        self.assertEqual(result["status"], "blocked")
        self.assertIn("definitive", " ".join(result["issues"]))

    def test_duplicate_pairing_numbers_rejected(self):
        tournament = event()
        for player in tournament["players"]:
            player["pairingNumber"] = 1
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_tournament(tournament)

    def test_assigned_pairing_rating_does_not_replace_published_rating(self):
        tournament = event()
        tournament["players"][2]["rating"] = 0
        tournament["players"][2]["pairingRating"] = 2350
        result = generic(tournament)
        self.assertEqual(pair_set(result), {frozenset(("1", "2")), frozenset(("3", "4"))})
        player = next(p for board in result["pairings"] for p in (board["white"], board["black"]) if p["id"] == "3")
        self.assertEqual(player["rating"], 0)
        self.assertEqual(player["pairingRating"], 2350)

    def test_manual_virtual_points_separate_groups_without_changing_scores(self):
        tournament = event(8)
        tournament["acceleration"] = {"method": "manual"}
        for player in tournament["players"][:4]:
            player["virtualPoints"] = [{"round": 1, "points": 1}]
        result = generic(tournament)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(pair_set(result), {frozenset(("1", "3")), frozenset(("2", "4")),
                                            frozenset(("5", "7")), frozenset(("6", "8"))})
        for board in result["pairings"]:
            for player in (board["white"], board["black"]):
                self.assertEqual(player["score"], 0)
                self.assertEqual(player["pairingScore"], 1 if int(player["id"]) <= 4 else 0)
        self.assertEqual(pairing_score(tournament["players"][0], 2), 0, "Virtual points are round-specific, not cumulative")

    def test_virtual_points_need_unique_rounds_and_half_points(self):
        for values in ([{"round": 1, "points": .1}], [{"round": 1, "points": 1}, {"round": 1, "points": 2}]):
            tournament = event()
            tournament["players"][0]["virtualPoints"] = values
            with self.assertRaisesRegex(ValueError, "half-point|unique"):
                validate_tournament(tournament)

    def test_director_prohibition_changes_natural_pairings(self):
        tournament = event()
        tournament["prohibitedPairs"] = [["1", "3"]]
        result = generic(tournament)
        self.assertEqual(result["status"], "ok")
        self.assertNotIn(frozenset(("1", "3")), pair_set(result))

    def test_locked_pair_and_color_are_honored(self):
        tournament = event()
        tournament["forcedPairs"] = [{"whiteId": "4", "blackId": "1"}]
        result = generic(tournament)
        self.assertEqual(result["status"], "ok")
        self.assertIn(("4", "1"), [(p["white"]["id"], p["black"]["id"]) for p in result["pairings"]])

    def test_inactive_locked_player_blocks(self):
        tournament = event()
        tournament["players"][0]["active"] = False
        tournament["forcedPairs"] = [["1", "2"]]
        result = generic(tournament)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("unavailable", " ".join(result["issues"]))

    def test_conflicting_director_restrictions_rejected(self):
        for additions in ({"forcedPairs": [["1", "2"], ["1", "3"]]},
                          {"forcedPairs": [["1", "2"]], "prohibitedPairs": [["2", "1"]]},
                          {"prohibitedPairs": [["1", "99"]]}):
            with self.assertRaises(ValueError):
                validate_tournament({**event(), **additions})

    def test_forbidden_team_pairs_are_hard_constraints(self):
        tournament = event()
        for player in tournament["players"]:
            player["team"] = "A" if player["id"] in {"1", "3"} else "B"
        result = generic(tournament, teamPolicy="forbid")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(all(p["white"]["team"] != p["black"]["team"] for p in result["pairings"]))

    def test_adjourned_game_counts_as_draw_for_pairing(self):
        tournament = event()
        tournament["completedRounds"] = 1
        for a, b in ((0, 2), (1, 3)):
            tournament["players"][a]["results"] = [{"round": 1, "opponentId": str(b + 1), "color": "W", "result": "ADJOURNED"}]
            tournament["players"][b]["results"] = [{"round": 1, "opponentId": str(a + 1), "color": "B", "result": "ADJOURNED"}]
        result = generic(tournament)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertTrue(all(p["score"] == .5 for b in result["pairings"] for p in (b["white"], b["black"])))
        self.assertTrue(any("adjourned" in warning for warning in result["warnings"]))
        imported = parse_text(__import__("json").dumps(tournament), "adjourned.json")
        self.assertTrue(imported["players"][0]["results"][0]["played"])

    def test_null_optional_starting_points_are_not_a_runtime_error(self):
        tournament = event()
        tournament["players"][0]["initialScore"] = None
        self.assertEqual(generic(tournament)["status"], "ok")

    def test_legacy_hard_limit_requests_remain_strict(self):
        self.assertEqual(normalize_rules({"maxConsecutiveColors": 1})["colorPolicy"], "strict")
        self.assertEqual(normalize_rules()["colorPolicy"], "us-chess")

    def test_reentry_cycles_and_unknown_original_entries_rejected(self):
        for links in (("2", "1"), ("99", None)):
            tournament = event()
            for player, parent in zip(tournament["players"], links):
                if parent:
                    player["reentryOf"] = parent
            with self.assertRaisesRegex(ValueError, "Reentry"):
                validate_tournament(tournament)

    def test_duplicate_active_reentry_including_a_requested_bye_blocks(self):
        tournament = event()
        tournament["players"][1]["reentryOf"] = "1"
        tournament["players"][1]["scheduledByes"] = [{"round": 1, "points": .5}]
        result = generic(tournament)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("same reentered player", " ".join(result["issues"]))

    def test_forecast_fingerprints_are_reproducible_and_rules_sensitive(self):
        tournament = event()
        first, repeated = generic(tournament), generic(tournament)
        changed = generic(tournament, firstRoundColor="B")
        self.assertEqual(first["inputHash"], repeated["inputHash"])
        self.assertEqual(first["rulesHash"], repeated["rulesHash"])
        self.assertEqual(first["inputHash"], changed["inputHash"])
        self.assertNotEqual(first["rulesHash"], changed["rulesHash"])

    def test_unknown_original_lot_order_is_visible_for_tied_ratings(self):
        tournament = event()
        for player in tournament["players"]:
            player["rating"] = 1800
        result = forecast(validate_tournament(tournament), {"profile": "us-chess"})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(any("original lot/seed order" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
