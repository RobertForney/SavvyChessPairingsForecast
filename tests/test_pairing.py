"""Regression cases use complete synthetic events, never the partial seed workbook."""

from copy import deepcopy
from itertools import product
import unittest
from unittest.mock import patch

from app.pairing import forecast
from app.validation import validate_tournament


def event(count, ratings=None):
    return {
        "name": "Synthetic test event", "section": "Open", "rounds": 7,
        "completedRounds": 0,
        "players": [
            {"id": str(i), "name": f"Player {i}",
             "rating": ratings[i - 1] if ratings else 2400 - i * 100,
             "active": True, "results": []}
            for i in range(1, count + 1)
        ],
    }


def add_round(tournament, games=(), absences=None):
    """Games are (white id, black id, white result); all other rows are explicit."""
    rnd = tournament["completedRounds"] + 1
    records = {}
    complement = {"W": "L", "L": "W", "D": "D",
                  "FORFEIT_WIN": "FORFEIT_LOSS", "FORFEIT_LOSS": "FORFEIT_WIN"}
    for white, black, outcome in games:
        for identifier, opponent, color, result in (
            (str(white), str(black), "W", outcome),
            (str(black), str(white), "B", complement[outcome]),
        ):
            if identifier in records:
                raise AssertionError("Synthetic fixture assigns a player twice")
            records[identifier] = {"round": rnd, "opponentId": opponent,
                                   "color": color, "result": result,
                                   "played": result in {"W", "D", "L"}}
    for player in tournament["players"]:
        identifier = player["id"]
        if identifier not in records:
            absence = (absences or {}).get(identifier, "ZERO")
            extra = absence if isinstance(absence, dict) else {"result": absence}
            records[identifier] = {"round": rnd, "opponentId": None,
                                   "color": None, "played": False, **extra}
        player["results"].append(records[identifier])
    tournament["completedRounds"] = rnd
    return tournament


def predict(tournament, **rules):
    return forecast(validate_tournament(tournament), rules)


def assignments(candidate):
    return [(board["white"]["id"], board["black"]["id"])
            for board in candidate["pairings"]]


def perfect_matchings(ids):
    """Plain exhaustive enumeration; independent of the engine's search/pruning."""
    if not ids:
        yield ()
        return
    first, rest = ids[0], ids[1:]
    for index, opponent in enumerate(rest):
        for remaining in perfect_matchings(rest[:index] + rest[index + 1:]):
            yield ((first, opponent),) + remaining


class PairingTests(unittest.TestCase):
    def test_played_and_forfeit_games_outside_participation_block_both_engines(self):
        for boundary in ({"entryRound": 2}, {"withdrawnAfterRound": 0}):
            for outcome in ("W", "FORFEIT_WIN"):
                for profile in ("us-chess", "fide-constraints"):
                    with self.subTest(boundary=boundary, outcome=outcome, profile=profile):
                        tournament = event(4)
                        tournament["players"][0].update(boundary)
                        add_round(tournament, [(1, 2, outcome), (3, 4, "D")])
                        result = predict(tournament, profile=profile)
                        self.assertEqual(result["status"], "blocked")
                        self.assertEqual(result["blockingReason"], "history")
                        self.assertTrue(any("outside the declared entry/withdrawal interval" in issue for issue in result["issues"]))

    def test_zero_and_awarded_bye_without_opponents_can_precede_entry(self):
        for result in ("ZERO", "HBYE"):
            with self.subTest(result=result):
                tournament = event(4)
                tournament["players"][0]["entryRound"] = 2
                add_round(tournament, [(2, 3, "D")], {"1": result, "4": "BYE"})
                prediction = predict(tournament)
                self.assertEqual(prediction["status"], "ok", prediction["issues"])

    def assert_valid_candidates(self, tournament, prediction):
        self.assertEqual(prediction["status"], "ok", prediction["issues"])
        expected = {p["id"] for p in tournament["players"] if p.get("active", True)}
        scheduled = {b["player"]["id"] for b in prediction.get("requestedByes", [])}
        histories = {p["id"]: p["results"] for p in tournament["players"]}
        signatures = set()
        for candidate in prediction["candidates"]:
            ids = [identifier for pair in assignments(candidate) for identifier in pair]
            if candidate["bye"]:
                ids.append(candidate["bye"]["id"])
            self.assertEqual(len(ids), len(set(ids)), "A player was assigned more than once")
            self.assertEqual(set(ids) | scheduled, expected)
            signature = (candidate["bye"]["id"] if candidate["bye"] else None,
                         tuple(sorted(tuple(sorted(pair)) for pair in assignments(candidate))))
            self.assertNotIn(signature, signatures)
            signatures.add(signature)
            for white, black in assignments(candidate):
                self.assertNotEqual(white, black)
                for identifier, opponent, next_color in ((white, black, "W"), (black, white, "B")):
                    played = [r for r in histories[identifier]
                              if r["result"] in {"W", "D", "L"} and r.get("played", True)]
                    self.assertNotIn(opponent, [r["opponentId"] for r in played])
                    colors = [r["color"] for r in played] + [next_color]
                    self.assertLessEqual(abs(colors.count("W") - colors.count("B")),
                                         prediction["rules"]["maxColorImbalance"])
                    if len(colors) >= 3 and prediction["rules"]["maxConsecutiveColors"] == 2:
                        self.assertFalse(colors[-3:] == [next_color] * 3)

    def test_complete_coverage_no_rematches_and_unique_candidates(self):
        tournament = event(8)
        add_round(tournament, [(1, 5, "D"), (2, 6, "D"), (3, 7, "D"), (4, 8, "D")])
        add_round(tournament, [(6, 1, "D"), (7, 2, "D"), (8, 3, "D"), (5, 4, "D")])
        result = predict(tournament, profile="fide-constraints", candidateCount=5)
        self.assert_valid_candidates(tournament, result)
        self.assertGreater(len(result["candidates"]), 1)
        self.assertEqual([c["objective"] for c in result["candidates"]],
                         sorted(c["objective"] for c in result["candidates"]))

    def test_streak_limit_forces_due_color(self):
        tournament = event(6)
        add_round(tournament, [(1, 4, "D"), (2, 5, "D"), (3, 6, "D")])
        add_round(tournament, [(1, 5, "D"), (2, 6, "D"), (3, 4, "D")])
        result = predict(tournament, maxColorImbalance=6, maxConsecutiveColors=2)
        self.assert_valid_candidates(tournament, result)
        for white, black in assignments(result):
            self.assertIn(white, {"4", "5", "6"})
            self.assertIn(black, {"1", "2", "3"})

    def test_balance_limit_forces_due_color_independent_of_streak(self):
        tournament = event(6)
        add_round(tournament, [(1, 4, "D"), (2, 5, "D"), (3, 6, "D")])
        add_round(tournament, [(1, 5, "D"), (2, 6, "D"), (3, 4, "D")])
        result = predict(tournament, maxColorImbalance=2, maxConsecutiveColors=6)
        self.assert_valid_candidates(tournament, result)
        self.assertEqual({black for _, black in assignments(result)}, {"1", "2", "3"})

    def test_us_bye_prefers_rated_player_to_unrated_in_same_score_group(self):
        result = predict(event(3, ratings=[2200, 1800, 0]))
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(result["bye"]["id"], "2")

    def test_previous_pairing_bye_is_excluded_even_when_it_awarded_zero(self):
        tournament = event(5)
        add_round(tournament, [(1, 2, "D"), (3, 4, "D")],
                  {"5": {"result": "BYE", "points": 0}})
        result = predict(tournament)
        self.assert_valid_candidates(tournament, result)
        self.assertEqual(result["bye"]["id"], "4")

    def test_repeat_bye_override_is_visible(self):
        tournament = event(5)
        add_round(tournament, [(1, 2, "D"), (3, 4, "D")],
                  {"5": {"result": "BYE", "points": 0}})
        result = predict(tournament, allowRepeatBye=True)
        self.assertEqual(result["bye"]["id"], "5")
        self.assertTrue(any("repeat" in w.lower() and "bye" in w.lower()
                            for w in result["warnings"]))

    def test_all_players_with_prior_pairing_bye_block(self):
        tournament = event(3)
        add_round(tournament, [(1, 2, "D")], {"3": "BYE"})
        add_round(tournament, [(1, 3, "D")], {"2": "BYE"})
        add_round(tournament, [(2, 3, "D")], {"1": "BYE"})
        result = predict(tournament)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("eligible" in issue for issue in result["issues"]))
        self.assertEqual(result["pairings"], [])

    def test_missing_color_blocks_even_when_hard_future_color_limits_are_off(self):
        tournament = event(4)
        add_round(tournament, [(1, 3, "D"), (2, 4, "D")])
        tournament["players"][0]["results"][0]["color"] = None
        for strict in (True, False):
            with self.subTest(strictColors=strict):
                result = predict(tournament, strictColors=strict)
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(any("unknown played color" in issue for issue in result["issues"]))

    def test_duplicate_round_is_not_silently_collapsed(self):
        tournament = event(4)
        add_round(tournament, [(1, 3, "D"), (2, 4, "D")])
        tournament["players"][0]["results"].append(deepcopy(tournament["players"][0]["results"][0]))
        result = forecast(tournament)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("duplicate" in issue for issue in result["issues"]))

    def test_reciprocal_result_contradiction_blocks(self):
        tournament = event(4)
        add_round(tournament, [(1, 3, "W"), (2, 4, "D")])
        tournament["players"][2]["results"][0]["result"] = "W"
        result = predict(tournament)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("disagrees" in issue for issue in result["issues"]))

    def test_nonreciprocal_opponent_blocks(self):
        tournament = event(4)
        add_round(tournament, [(1, 3, "D"), (2, 4, "D")])
        tournament["players"][2]["results"][0]["opponentId"] = "2"
        result = predict(tournament)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("reciprocal" in issue for issue in result["issues"]))

    def test_reciprocal_played_status_must_agree(self):
        tournament = event(4)
        add_round(tournament, [(1, 3, "D"), (2, 4, "D")])
        tournament["players"][0]["results"][0]["played"] = False
        result = predict(tournament)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("played status" in issue for issue in result["issues"]))

    def test_reported_score_discrepancy_blocks(self):
        tournament = event(4)
        add_round(tournament, [(1, 3, "D"), (2, 4, "D")])
        tournament["players"][0]["reportedScore"] = 1
        result = predict(tournament)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("reported score" in issue for issue in result["issues"]))

    def test_first_round_initial_color_and_split_halves_are_deterministic(self):
        tournament = event(8)
        first = predict(tournament, firstRoundColor="W")
        second = predict(tournament, firstRoundColor="W")
        self.assertEqual(assignments(first), assignments(second))
        self.assertEqual({frozenset(pair) for pair in assignments(first)},
                         {frozenset((str(i), str(i + 4))) for i in range(1, 5)})
        colors = {identifier: color for white, black in assignments(first)
                  for identifier, color in ((white, "W"), (black, "B"))}
        self.assertEqual([colors[str(i)] for i in range(1, 5)], ["W", "B", "W", "B"])
        reversed_first = predict(tournament, firstRoundColor="B")
        self.assertEqual(assignments(reversed_first), [(b, w) for w, b in assignments(first)])

    def test_search_timeout_does_not_claim_infeasibility(self):
        # The first clock value starts the deadline; the next expires it before a leaf.
        with patch("app.pairing.time.perf_counter", side_effect=[0.0, 10.0, 10.0]):
            result = predict(event(4), profile="fide-constraints", searchMillis=50)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["stats"]["exhaustive"])
        self.assertTrue(any("Search limit" in issue for issue in result["issues"]))
        self.assertFalse(any("No complete pairing satisfies" in issue for issue in result["issues"]))

    def test_timeout_after_a_complete_candidate_keeps_valid_best_found(self):
        ticks = iter([0.0] * 6 + [10.0] * 100)
        tournament = event(8)
        with patch("app.pairing.time.perf_counter", side_effect=lambda: next(ticks)):
            result = predict(tournament, profile="fide-constraints", searchMillis=50)
        self.assert_valid_candidates(tournament, result)
        self.assertFalse(result["stats"]["exhaustive"])
        self.assertTrue(any("global optimality is not established" in w for w in result["warnings"]))

    def test_exhaustive_infeasible_case_is_distinct_from_search_timeout(self):
        tournament = event(2)
        add_round(tournament, [(1, 2, "D")])
        result = predict(tournament, profile="fide-constraints")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["stats"]["exhaustive"])
        self.assertTrue(any("No complete pairing satisfies" in issue for issue in result["issues"]))

    def test_forfeit_color_is_ignored_and_does_not_forbid_future_opponent(self):
        tournament = event(2)
        add_round(tournament, [(1, 2, "FORFEIT_WIN")])
        result = predict(tournament, profile="fide-constraints", maxConsecutiveColors=1, maxColorImbalance=1)
        self.assert_valid_candidates(tournament, result)
        self.assertIn("Played color history: none / none", result["pairings"][0]["explanations"])
        self.assertIn("No previous played encounter", result["pairings"][0]["explanations"])

    def test_less_than_one_move_result_retains_points_without_played_history(self):
        tournament = event(2)
        add_round(tournament, [(1, 2, "W")])
        for player in tournament["players"]:
            player["results"][0].update(played=False, color=None)
        result = predict(tournament, profile="fide-constraints")
        self.assert_valid_candidates(tournament, result)
        self.assertEqual(sorted([result["pairings"][0]["white"]["score"],
                                 result["pairings"][0]["black"]["score"]]), [0, 1])
        self.assertIn("Played color history: none / none", result["pairings"][0]["explanations"])

    def test_unplayed_full_point_win_excludes_future_pairing_bye(self):
        tournament = event(3, ratings=[800, 1800, 2000])
        add_round(tournament, [(1, 2, "W")], {"3": "BYE"})
        for player in tournament["players"][:2]:
            player["results"][0].update(played=False, color=None)
        add_round(tournament, [(2, 3, "W")])
        # All three now have one point. Player 1 is lowest rated but won unplayed;
        # player 3 already received a pairing bye. Only player 2 is eligible.
        for profile in ("us-chess", "fide-constraints"):
            with self.subTest(profile=profile):
                result = predict(tournament, profile=profile)
                self.assert_valid_candidates(tournament, result)
                self.assertEqual(result["bye"]["id"], "2")

    def test_unsupported_point_override_is_rejected(self):
        tournament = event(2)
        add_round(tournament, [(1, 2, "W")])
        tournament["players"][0]["results"][0]["points"] = .5
        with self.assertRaisesRegex(ValueError, "Custom result points"):
            validate_tournament(tournament)

    def test_requested_bye_excludes_player_and_preserves_awarded_points(self):
        tournament = event(5)
        tournament["players"][4]["scheduledByes"] = [{"round": 1, "points": .5}]
        result = predict(tournament)
        self.assert_valid_candidates(tournament, result)
        self.assertIsNone(result["bye"])
        self.assertEqual([(b["player"]["id"], b["points"]) for b in result["requestedByes"]], [("5", .5)])
        self.assertNotIn("5", [identifier for pair in assignments(result) for identifier in pair])
        self.assertEqual(result["stats"]["activePlayers"], 4)

    def test_future_requested_bye_does_not_remove_player_from_this_round(self):
        tournament = event(4)
        tournament["players"][3]["scheduledByes"] = [{"round": 2, "points": .5}]
        result = predict(tournament)
        self.assert_valid_candidates(tournament, result)
        self.assertEqual(result["requestedByes"], [])
        self.assertIn("4", [identifier for pair in assignments(result) for identifier in pair])

    def test_committed_half_point_bye_changes_us_bye_eligibility(self):
        tournament = event(3)
        tournament["players"][2]["scheduledByes"] = [{"round": 2, "points": .5}]
        us = predict(tournament, profile="us-chess")
        fide = predict(tournament, profile="fide-constraints")
        self.assertEqual(us["bye"]["id"], "2")
        self.assertEqual(fide["bye"]["id"], "3")

    def test_equal_score_pairings_take_priority_over_team_preferences(self):
        tournament = event(4)
        add_round(tournament, [(1, 3, "W"), (2, 4, "W")])
        for player in tournament["players"]:
            player["team"] = "A" if player["id"] in {"1", "2"} else "B"
        result = predict(tournament, avoidSameTeam=True)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual({frozenset(pair) for pair in assignments(result)},
                         {frozenset(("1", "2")), frozenset(("3", "4"))})
        self.assertEqual(result["objective"][1], 0)

    def test_small_field_search_matches_independent_complete_enumeration(self):
        tournament = event(6)
        add_round(tournament, [(1, 4, "W"), (2, 5, "D"), (3, 6, "L")])
        scores = {"1": 1, "6": 1, "2": .5, "5": .5, "3": 0, "4": 0}
        for player in tournament["players"]:
            player["team"] = "A" if player["id"] in {"1", "6"} else "B"
        players = sorted(tournament["players"], key=lambda p: (-scores[p["id"]], -p["rating"]))
        positions = {p["id"]: i for i, p in enumerate(players)}
        # Each score group has exactly two members in this fixture.
        target = {p["id"]: (i + 1 if i % 2 == 0 else i - 1) for i, p in enumerate(players)}
        previous = {p["id"]: p["results"][0] for p in players}
        teams = {p["id"]: p["team"] for p in players}
        objectives = []
        for matching in perfect_matchings(tuple(p["id"] for p in players)):
            if any(previous[a]["opponentId"] == b for a, b in matching):
                continue
            gaps = [abs(scores[a] - scores[b]) for a, b in matching]
            for orientations in product((0, 1), repeat=len(matching)):
                color_cost = 0
                for (a, b), flipped in zip(matching, orientations):
                    for identifier, color in ((a, "B" if flipped else "W"),
                                              (b, "W" if flipped else "B")):
                        colors = [previous[identifier]["color"], color]
                        color_cost += abs(colors.count("W") - colors.count("B")) * 4
                        color_cost += int(colors[0] == colors[1])
                displacement = sum(abs(positions[b] - target[a]) + abs(positions[a] - target[b])
                                   for a, b in matching)
                objectives.append((0, sum(gaps), sum(gap > 0 for gap in gaps), 0,
                                   sum(teams[a] == teams[b] for a, b in matching),
                                   color_cost, displacement))
        result = predict(tournament, profile="fide-constraints", avoidSameTeam=True, searchMillis=5000)
        self.assert_valid_candidates(tournament, result)
        self.assertTrue(result["stats"]["exhaustive"])
        self.assertEqual(tuple(result["objective"]), min(objectives))


if __name__ == "__main__":
    unittest.main()
