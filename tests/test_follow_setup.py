"""Unknown setup must remain explicit and cannot bypass engine prerequisites."""
from copy import deepcopy
import unittest
from app.follow import new_follow, update_follow, workspace_forecast


class FollowSetupTests(unittest.TestCase):
    def test_selected_color_override_is_an_explicit_hypothesis(self):
        event = new_follow({"name": "Club Open", "profile": "us-chess", "rounds": 3, "firstRoundColor": "W"})
        event["players"] = [{"id": "a", "name": "A", "rating": 1800, "results": []},
                            {"id": "b", "name": "B", "rating": 1700, "results": []}]
        result = workspace_forecast(event, {"profile": "us-chess", "firstRoundColor": "B"})
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["orientationUnconfirmed"])
        self.assertTrue(any("alternative color hypothesis" in item for item in result["assumptions"]))

    def test_draft_has_no_invented_field_and_survives_unknown_settings(self):
        event = new_follow({"name": "Any Club Open"})
        self.assertEqual(event["players"], [])
        self.assertFalse(event["tracking"]["roundsKnown"])
        self.assertEqual(event["tracking"]["profile"], "unknown")
        self.assertEqual(event["tracking"]["acceleration"], "unknown")
        self.assertEqual(workspace_forecast(event, {})["status"], "blocked")

    def test_confirming_start_order_requires_actual_initial_numbers(self):
        event = new_follow({"name": "Club Open"})
        event["players"] = [{"id": "a", "name": "A", "rating": 1800, "results": []},
                            {"id": "b", "name": "B", "rating": 1700, "results": []}]
        with self.assertRaisesRegex(ValueError, "every original pairing number"):
            update_follow(event, {"startingOrderKnown": True})
        event["players"][0]["pairingNumber"] = 1
        event["players"][1]["pairingNumber"] = 2
        confirmed = update_follow(event, {"startingOrderKnown": True})
        self.assertTrue(confirmed["tracking"]["startingOrderKnown"])

    def test_unknown_length_blocks_baku_before_native_engine_execution(self):
        event = new_follow({"name": "Club Open", "profile": "fide-dutch", "acceleration": "baku"})
        event["players"] = [{"id": "a", "name": "A", "rating": 1800, "results": []},
                            {"id": "b", "name": "B", "rating": 1700, "results": []}]
        result = workspace_forecast(event, {"profile": "fide-dutch"})
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("number of rounds" in issue for issue in result["issues"]))
        self.assertNotIn("engineDetails", result)

    def test_no_acceleration_cannot_silently_keep_conflicting_virtual_points(self):
        event = new_follow({"name": "Club Open", "rounds": 5})
        event["players"] = [{"id": "a", "name": "A", "rating": 1800, "results": [],
                             "virtualPoints": [{"round": 1, "points": 1}]}]
        before = deepcopy(event)
        with self.assertRaisesRegex(ValueError, "virtual points conflict"):
            update_follow(event, {"acceleration": "none"})
        self.assertEqual(event, before)
