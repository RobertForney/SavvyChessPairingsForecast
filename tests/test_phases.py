"""Unrelated tournament fixtures exercise the shared operational pool policy."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from app.dutch import availability
from app.pairing import forecast
from app.replay import replay
from app.validation import validate_tournament


def event(count=4, policy=None, merge_round=2):
    settings = {}
    if policy is not None:
        settings["schedulePolicy"] = policy
    if merge_round is not None:
        settings["mergeRound"] = merge_round
    return validate_tournament({"name": "Autumn Club Swiss", "section": "Championship", "rounds": 4,
        "completedRounds": 0, "sourceSettings": settings,
        "players": [{"id": f"member-{i}", "name": f"Entrant {i}", "rating": 2400 - i * 100,
                     "pairingNumber": i, "active": True, "results": [],
                     "schedule": "Weekend" if i <= count // 2 else "Evenings"} for i in range(1, count + 1)]})


def pairs(result):
    return {frozenset((p["white"]["id"], p["black"]["id"])) for p in result["pairings"]}


def add_draw(tournament, white, black):
    for identifier, opponent, color in ((white, black, "W"), (black, white, "B")):
        next(p for p in tournament["players"] if p["id"] == identifier)["results"].append(
            {"round": 1, "opponentId": opponent, "result": "D", "color": color, "played": True})


class SharedScheduleTests(unittest.TestCase):
    def test_live_and_replay_use_identical_announced_pool_pairings(self):
        source = event()
        unchanged = deepcopy(source)
        live = forecast(source)
        historical = replay(source, 0)["forecast"]
        self.assertEqual(live["status"], "ok", live["issues"])
        self.assertEqual(pairs(live), pairs(historical))
        self.assertEqual(pairs(live), {frozenset(("member-1", "member-2")), frozenset(("member-3", "member-4"))})
        self.assertTrue(all(p["white"]["schedule"] == p["black"]["schedule"] for p in live["pairings"]))
        self.assertEqual([p["board"] for p in live["pairings"]], [1, 2])
        self.assertEqual(source, unchanged)

    def test_merge_boundary_applies_to_the_round_being_forecast(self):
        source = event(policy="merge")
        add_draw(source, "member-1", "member-2")
        add_draw(source, "member-4", "member-3")
        source["completedRounds"] = 1
        live = forecast(source)
        historical = replay(source, 1)["forecast"]
        self.assertEqual(live["status"], "ok", live["issues"])
        self.assertEqual(live["scheduleContext"]["mode"], "merged")
        self.assertEqual(pairs(live), pairs(historical))
        self.assertTrue(all(p["white"]["schedule"] != p["black"]["schedule"] for p in live["pairings"]))

    def test_ambiguous_multiple_schedules_block_before_any_pool_engine_runs(self):
        source = event(merge_round=None)
        with patch("app.pairing._forecast_pool") as pool:
            result = forecast(source)
            pool.assert_not_called()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blockingReason"], "settings")
        self.assertIn("policy", " ".join(result["issues"]))
        self.assertEqual(replay(source, 0)["forecast"]["blockingReason"], "settings")

    def test_explicit_unknown_policy_does_not_confirm_a_leftover_merge_round(self):
        result = forecast(event(policy="unknown", merge_round=2))
        self.assertEqual(result["blockingReason"], "settings")
        self.assertEqual(result["scheduleContext"]["policy"], "unknown")

    def test_single_policy_explicitly_treats_schedule_labels_as_metadata(self):
        result = forecast(event(policy="single", merge_round=None))
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(result["scheduleContext"]["mode"], "single")
        self.assertTrue(all(p["white"]["schedule"] != p["black"]["schedule"] for p in result["pairings"]))

    def test_one_unlabelled_pool_retains_the_generic_single_pool_workflow(self):
        source = event(merge_round=None)
        for p in source["players"]:
            p.pop("schedule")
        self.assertEqual(forecast(source)["status"], "ok")

    def test_never_merge_policy_does_not_silently_merge_at_an_old_round(self):
        source = event(8, policy="separate", merge_round=None)
        for white, black in (("member-1", "member-3"), ("member-4", "member-2"),
                             ("member-5", "member-7"), ("member-8", "member-6")):
            add_draw(source, white, black)
        source["completedRounds"] = 1
        result = forecast(source)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(result["scheduleContext"]["mode"], "separate")
        self.assertTrue(all(p["white"]["schedule"] == p["black"]["schedule"] for p in result["pairings"]))

    def test_unknown_merge_boundary_and_missing_schedule_assignment_block(self):
        for boundary in (None, True, 0, 1.5, 8, "2"):
            with self.subTest(boundary=boundary):
                result = forecast(event(policy="merge", merge_round=boundary))
                self.assertEqual(result["blockingReason"], "settings")
        source = event(policy="separate", merge_round=None)
        source["players"][0].pop("schedule")
        self.assertEqual(forecast(source)["blockingReason"], "settings")

    def test_cross_schedule_lock_and_reentry_fail_closed(self):
        for restriction in ("forcedPairs", "reentryOf"):
            with self.subTest(restriction=restriction):
                source = event()
                if restriction == "forcedPairs":
                    source["forcedPairs"] = [{"whiteId": "member-1", "blackId": "member-3"}]
                else:
                    source["players"][2]["reentryOf"] = "member-1"
                source["replayKnownRestrictions"] = True
                result = forecast(source)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["pairings"], [])
                self.assertEqual(result["candidates"], [])
                self.assertIn("spans unmerged schedules", " ".join(result["issues"]))
                self.assertEqual(replay(source, 0)["forecast"]["blockingReason"], "settings")

    def test_within_schedule_locks_and_cross_schedule_prohibitions_are_respected(self):
        source = event(8)
        source["forcedPairs"] = [{"whiteId": "member-1", "blackId": "member-2"}]
        source["prohibitedPairs"] = [["member-1", "member-5"]]
        result = forecast(source)
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertIn(frozenset(("member-1", "member-2")), pairs(result))
        board = next(p for p in result["pairings"] if p["white"]["id"] == "member-1")
        self.assertEqual(board["black"]["id"], "member-2")

    def test_multiple_allocated_byes_and_one_honest_combined_candidate(self):
        result = forecast(event(6), {"candidateCount": 5})
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual(len(result["byes"]), 2)
        self.assertIsNone(result["bye"])
        self.assertEqual({b["schedule"] for b in result["byes"]}, {"Evenings", "Weekend"})
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["candidates"][0]["byes"], result["byes"])
        self.assertEqual([p["board"] for p in result["pairings"]], [1, 2])

    def test_any_pool_failure_blocks_the_combined_result(self):
        source = event()
        source["prohibitedPairs"] = [["member-1", "member-2"]]
        result = forecast(source)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blockingReason"], "constraints")
        self.assertEqual(result["pairings"], [])
        self.assertEqual(result["byes"], [])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["phaseForecasts"]), 2)

    def test_invalid_history_in_a_fully_inactive_pool_is_not_hidden(self):
        source = event()
        for p in source["players"][:2]:
            p["active"] = False
        source["completedRounds"] = 1
        add_draw(source, "member-3", "member-4")
        result = forecast(source)
        self.assertEqual(result["blockingReason"], "history")
        self.assertTrue(any("Entrant 1: round 1 is missing" in i for i in result["issues"]))

    def test_explicit_local_numbers_control_initial_colors_without_changing_global_order(self):
        source = event()
        for p in source["players"]:
            p["schedulePairingNumber"] = 2 if int(p["id"].split("-")[1]) % 2 else 1
        result = forecast(source, {"pairingOrder": "pairing-number"})
        self.assertEqual(result["status"], "ok", result["issues"])
        self.assertEqual({p["white"]["id"] for p in result["pairings"]}, {"member-2", "member-4"})
        self.assertEqual([p["pairingNumber"] for p in source["players"]], [1, 2, 3, 4])
        historical = replay(source, 0, {"pairingOrder": "pairing-number"})["forecast"]
        self.assertEqual({p["white"]["id"] for p in result["pairings"]}, {p["white"]["id"] for p in historical["pairings"]})
        source["players"][0]["schedulePairingNumber"] = None
        self.assertEqual(forecast(source)["blockingReason"], "settings")

    def test_null_local_number_is_an_unset_value(self):
        source = event()
        for p in source["players"]:
            p["schedulePairingNumber"] = None
        self.assertEqual(forecast(source, {"pairingOrder": "pairing-number"})["status"], "ok")

    def test_native_engine_error_keeps_its_reason_and_has_no_partial_pairing(self):
        missing = {**availability(), "available": False, "reason": "Test dependency unavailable"}
        with patch("app.dutch.availability", return_value=missing):
            result = forecast(event(), {"profile": "fide-dutch"})
        self.assertEqual(result["blockingReason"], "engine")
        self.assertEqual(result["pairings"], [])

    @unittest.skipUnless(availability()["available"], "Pinned native engine is optional")
    def test_native_engine_receives_consecutive_local_numbers_in_live_and_replay(self):
        source = event(6)
        live = forecast(source, {"profile": "fide-dutch"})
        historical = replay(source, 0, {"profile": "fide-dutch"})["forecast"]
        self.assertEqual(live["status"], "ok", live["issues"])
        self.assertEqual(pairs(live), pairs(historical))
        self.assertEqual(len(live["byes"]), 2)
        self.assertEqual(len(live["engineDetails"]["phaseInputs"]), 2)
        for phase in live["phaseForecasts"]:
            self.assertIn("BBP Pairings", phase["forecast"]["engine"])
            self.assertEqual(len(phase["forecast"]["inputHash"]), 64)
