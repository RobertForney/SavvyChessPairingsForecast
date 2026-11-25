"""Independent issuance guards tested with deliberately defective engine output."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from app.director import create_event, generate_draft, issue_round, upsert_player


class DirectorOutputQualityTests(unittest.TestCase):
    def setUp(self):
        self.event, self.rules = create_event({"name": "Independent Assignment Checks", "rounds": 3,
            "profile": "us-chess", "firstRoundColor": "W", "acknowledgeEngineLimits": True})
        for name, rating in (("Alex", 2000), ("Blair", 1900), ("Casey", 1800), ("Devon", 1700)):
            self.event = upsert_player(self.event, {"name": name, "rating": rating})
        valid = generate_draft(self.event, self.rules)
        self.good = deepcopy(valid["managed"]["draft"]["forecast"])

    def assert_defective_output_cannot_be_issued(self, outcome, event=None):
        event = event or self.event
        before = deepcopy(event)
        with patch("app.director.forecast", return_value=outcome):
            proposed = generate_draft(event, self.rules)
        draft = proposed["managed"]["draft"]
        self.assertFalse(draft["quality"]["canIssue"], draft["quality"])
        with self.assertRaises(ValueError):
            issue_round(proposed, self.rules, draft["id"])
        self.assertEqual(event, before, "An invalid candidate must not mutate the event that was reviewed")

    def test_complete_coverage_does_not_make_two_byes_valid_in_one_even_pool(self):
        bad = deepcopy(self.good)
        removed = bad["pairings"].pop()
        bad["bye"] = None
        bad["byes"] = [{"player": deepcopy(removed[side]), "points": self.rules["byePoints"]}
                       for side in ("white", "black")]
        self.assert_defective_output_cannot_be_issued(bad)

    def test_same_profile_with_different_engine_rules_is_not_the_reviewed_procedure(self):
        bad = deepcopy(self.good)
        bad["rules"]["firstRoundColor"] = "B"
        self.assert_defective_output_cannot_be_issued(bad)

    def test_a_forced_pair_is_not_optional_when_coverage_is_otherwise_complete(self):
        event = deepcopy(self.event)
        event["forcedPairs"] = [{"whiteId": "p1", "blackId": "p2"}]
        self.assert_defective_output_cannot_be_issued(deepcopy(self.good), event)

    def test_an_engine_cannot_reverse_the_confirmed_first_round_color_draw(self):
        bad = deepcopy(self.good)
        board = bad["pairings"][0]
        board["white"], board["black"] = board["black"], board["white"]
        self.assert_defective_output_cannot_be_issued(bad)

    def test_a_completed_candidate_from_a_budget_stopped_search_is_not_issuable(self):
        bad = deepcopy(self.good)
        bad["warnings"].append("The US procedural search reached its budget. A complete candidate was found; further alternatives may exist.")
        bad["stats"]["exhaustive"] = False
        self.assert_defective_output_cannot_be_issued(bad)

    def test_structured_search_stop_cannot_depend_on_an_english_warning(self):
        bad = deepcopy(self.good)
        bad["warnings"] = []
        bad["stats"]["exhaustive"] = False
        bad["stats"]["candidateQuotaReached"] = False
        self.assert_defective_output_cannot_be_issued(bad)

    def test_duplicate_board_labels_cannot_be_hidden_by_correct_player_coverage(self):
        bad = deepcopy(self.good)
        bad["pairings"][1]["board"] = bad["pairings"][0]["board"]
        self.assert_defective_output_cannot_be_issued(bad)
