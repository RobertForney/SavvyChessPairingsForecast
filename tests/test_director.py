"""Professional round lifecycle with independent local-club fixtures."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from app.director import (create_event, update_event, upsert_player, remove_player,
                          generate_draft, issue_round, record_results, close_round,
                          reopen_latest, discard_draft, status, validate_managed,
                          pairing_input_hash, rules_hash, restore_event)
from app.dutch import availability
from app.pairing import audit_history
from app.director import pairing_input


def club(count=6, profile="us-chess", rounds=3, acknowledged=True):
    t, r = create_event({"name": "Saturday Club Championship", "section": "Open", "rounds": rounds,
                         "profile": profile, "acknowledgeEngineLimits": acknowledged})
    for i in range(count):
        t = upsert_player(t, {"name": ["Ada", "Ben", "Cara", "Devon", "Eli", "Fran", "Gus"][i], "rating": 2100 - i * 100})
    return t, r


def issue(t, r):
    t = generate_draft(t, r)
    return issue_round(t, r, t["managed"]["draft"]["id"])


def finish(t, r, code="D"):
    active = status(t, r)["activeRound"]
    t = record_results(t, active["number"], [{"board": b["board"], "result": code} for b in active["snapshot"]["pairings"]])
    return close_round(t, r, active["number"])


class DirectorTests(unittest.TestCase):
    def test_browser_json_numbers_preserve_draft_and_issued_backup_fingerprints(self):
        def browser_json(value):
            # JSON.parse/stringify preserves Number values, not Python's
            # distinction between integral floats and integer JSON tokens.
            return json.loads(json.dumps(value), parse_float=lambda text: int(float(text)) if float(text).is_integer() else float(text))
        t, r = club(count=4)
        t["externalContext"] = {"negativeZero": -0.0, "fraction": .5, "small": 1e-7, "threshold": 1e-6}
        t = generate_draft(t, r)
        draft_hash = t["managed"]["draft"]["snapshotHash"]
        parsed = browser_json(t)
        self.assertEqual(validate_managed(parsed)["managed"]["draft"]["snapshotHash"], draft_hash)
        self.assertTrue(status(parsed, browser_json(r))["canIssue"])
        t = issue_round(parsed, browser_json(r), parsed["managed"]["draft"]["id"])
        t = finish(t, r)
        backup = browser_json({"tournament": t, "rules": r})
        restored, _ = restore_event(backup["tournament"], backup["rules"])
        self.assertEqual(restored["managed"]["rounds"], backup["tournament"]["managed"]["rounds"])
        self.assertEqual(restored["managed"]["rounds"][0]["snapshot"]["inputSnapshot"]["externalContext"]["small"], 1e-7)
        self.assertEqual(restored["managed"]["rounds"][0]["snapshot"]["inputSnapshot"]["externalContext"]["fraction"], .5)

    def test_requested_zero_half_and_full_point_byes_close_and_preserve_awards(self):
        for points, code in ((0, "ZERO"), (.5, "HBYE"), (1, "FORFEIT_WIN")):
            with self.subTest(points=points):
                t, r = club(count=5)
                t = upsert_player(t, {"id": "p1", "scheduledByes": [{"round": 1, "points": points}]})
                t = finish(issue(t, r), r)
                record = next(p for p in t["players"] if p["id"] == "p1")["results"][0]
                self.assertEqual((record["result"], record["points"]), (code, points))
                self.assertFalse(record["played"])
                self.assertIsNone(record["opponentId"])
                self.assertFalse(audit_history(pairing_input(t), r)[0])
                bad = deepcopy(t)
                next(p for p in bad["players"] if p["id"] == "p1")["scheduledByes"][0]["points"] = .5 if points != .5 else 0
                self.assertTrue(any("scheduled bye" in issue for issue in audit_history(pairing_input(bad), r)[0]))
                next_draft = generate_draft(t, r)
                self.assertTrue(status(next_draft, r)["canIssue"])
                if points == 1:
                    self.assertNotIn("p1", {b["player"]["id"] for b in next_draft["managed"]["draft"]["forecast"]["byes"]})

    def test_double_forfeit_closes_with_two_unplayed_losses_and_can_pair_again(self):
        t, r = club(count=4)
        t = issue(t, r)
        board = t["managed"]["rounds"][0]["snapshot"]["pairings"][0]
        ids = {board["white"]["id"], board["black"]["id"]}
        t = record_results(t, 1, [{"board": 1, "result": "DOUBLE_FORFEIT"}, {"board": 2, "result": "D"}])
        t = close_round(t, r, 1)
        for player in t["players"]:
            if player["id"] in ids:
                record = player["results"][0]
                self.assertEqual(record["result"], "FORFEIT_LOSS")
                self.assertFalse(record["played"])
                self.assertIsNone(record["color"])
                self.assertIn(record["opponentId"], ids - {player["id"]})
        self.assertFalse(audit_history(pairing_input(t), r)[0])
        self.assertTrue(status(generate_draft(t, r), r)["canIssue"])

    @unittest.skipUnless(availability()["available"], "Pinned native Dutch engine is not installed")
    def test_native_dutch_accepts_double_forfeit_history_for_the_next_round(self):
        t, r = club(count=4, profile="fide-dutch")
        t = issue(t, r)
        t = record_results(t, 1, [{"board": 1, "result": "DOUBLE_FORFEIT"}, {"board": 2, "result": "D"}])
        t = close_round(t, r, 1)
        draft = generate_draft(t, r)
        self.assertTrue(status(draft, r)["canIssue"], status(draft, r)["issues"])

    @unittest.skipUnless(availability()["available"], "Pinned native Dutch engine is not installed")
    def test_native_half_point_allocated_bye_and_requested_full_award_remain_distinct(self):
        t, r = club(count=5, profile="fide-dutch")
        t, r = update_event(t, r, {"rules": {"byePoints": .5}})
        t = upsert_player(t, {"id": "p1", "scheduledByes": [{"round": 1, "points": 1}]})
        t = finish(issue(t, r), r)
        requested = next(p for p in t["players"] if p["id"] == "p1")["results"][0]
        self.assertEqual((requested["result"], requested["points"]), ("FORFEIT_WIN", 1))
        draft = generate_draft(t, r)
        self.assertTrue(status(draft, r)["canIssue"], status(draft, r)["issues"])
        self.assertEqual(draft["managed"]["draft"]["forecast"]["byes"][0]["points"], .5)
        self.assertNotEqual(draft["managed"]["draft"]["forecast"]["byes"][0]["player"]["id"], "p1")

    def test_default_new_event_uses_native_dutch_and_contains_no_invented_players(self):
        t, r = create_event({"name": "New Club Event"})
        self.assertEqual(r["profile"], "fide-dutch")
        self.assertEqual(t["players"], [])
        self.assertEqual(t["completedRounds"], 0)
        self.assertEqual(t["managed"]["revision"], 1)
        self.assertEqual(status(t, r)["stage"], "setup")
        self.assertFalse(status(t, r)["canGenerate"])

    def test_lifecycle_generates_before_results_and_keeps_issued_assignments_fixed(self):
        t, r = club()
        original = deepcopy(t)
        drafted = generate_draft(t, r)
        self.assertEqual(t, original)
        self.assertEqual(drafted["managed"]["draft"]["inputHash"], pairing_input_hash(drafted))
        self.assertTrue(status(drafted, r)["canIssue"])
        issued = issue_round(drafted, r, drafted["managed"]["draft"]["id"])
        snapshot = deepcopy(issued["managed"]["rounds"][0]["snapshot"])
        self.assertTrue(all(not p["results"] for p in issued["players"]))
        self.assertEqual(issued["completedRounds"], 0)
        pending = record_results(issued, 1, [{"board": 1, "result": "W"}])
        self.assertTrue(all(not p["results"] for p in pending["players"]))
        self.assertEqual(pending["managed"]["rounds"][0]["snapshot"], snapshot)
        self.assertFalse(status(pending, r)["canClose"])
        with self.assertRaisesRegex(ValueError, "Every board"):
            close_round(pending, r, 1)
        closed = finish(pending, r)
        self.assertEqual(closed["completedRounds"], 1)
        self.assertEqual(closed["managed"]["rounds"][0]["snapshot"], snapshot)
        self.assertTrue(all(len(p["results"]) == 1 for p in closed["players"]))
        self.assertEqual(sum(p["score"] for p in status(closed, r)["standings"]), 3)
        next_draft = generate_draft(closed, r)
        old_pairs = {frozenset((b["white"]["id"], b["black"]["id"])) for b in snapshot["pairings"]}
        new_pairs = {frozenset((b["white"]["id"], b["black"]["id"])) for b in next_draft["managed"]["draft"]["forecast"]["pairings"]}
        self.assertFalse(old_pairs & new_pairs)

    def test_duplicate_issuance_and_roster_edits_during_play_are_rejected(self):
        t, r = club()
        t = generate_draft(t, r)
        draft_id = t["managed"]["draft"]["id"]
        t = issue_round(t, r, draft_id)
        for action in (lambda: issue_round(t, r, draft_id), lambda: generate_draft(t, r),
                       lambda: upsert_player(t, {"name": "Extra", "rating": 1600}),
                       lambda: update_event(t, r, {"name": "Changed"})):
            with self.assertRaisesRegex(ValueError, "issued round"):
                action()

    def test_stale_rules_and_modified_drafts_cannot_be_issued(self):
        t, r = club()
        t = generate_draft(t, r)
        draft_id = t["managed"]["draft"]["id"]
        with self.assertRaisesRegex(ValueError, "changed after generation"):
            issue_round(t, {**r, "firstRoundColor": "B"}, draft_id)
        changed = deepcopy(t)
        changed["managed"]["draft"]["forecast"]["pairings"][0]["white"]["name"] = "Changed"
        with self.assertRaisesRegex(ValueError, "draft changed"):
            issue_round(changed, r, draft_id)
        edited = upsert_player(t, {"id": "p1", "name": "Ada Corrected"})
        self.assertIsNone(edited["managed"]["draft"])
        with self.assertRaises(ValueError):
            issue_round(edited, r, draft_id)

    def test_us_scope_requires_explicit_review_and_custom_matching_is_not_issuable(self):
        t, r = club(acknowledged=False)
        t = generate_draft(t, r)
        self.assertFalse(status(t, r)["canIssue"])
        with self.assertRaisesRegex(ValueError, "acknowledge"):
            issue_round(t, r, t["managed"]["draft"]["id"])
        for profile in ("fide-constraints", "round-robin", "unknown"):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                create_event({"name": "Unsupported", "profile": profile})

    def test_actual_budget_truncation_blocks_but_candidate_quota_is_not_a_timeout(self):
        t, r = club()
        normal = generate_draft(t, r)["managed"]["draft"]["forecast"]
        quota = deepcopy(normal)
        quota["stats"].update(exhaustive=False, candidateQuotaReached=True)
        with patch("app.director.forecast", return_value=quota):
            draft = generate_draft(t, r)
        self.assertTrue(status(draft, r)["canIssue"])
        stopped = deepcopy(normal)
        stopped["warnings"].append("The US procedural search reached its budget. A complete candidate was found; further alternatives may exist.")
        with patch("app.director.forecast", return_value=stopped):
            draft = generate_draft(t, r)
        self.assertFalse(status(draft, r)["canIssue"])
        with self.assertRaisesRegex(ValueError, "budget"):
            issue_round(draft, r, draft["managed"]["draft"]["id"])

    def test_odd_fields_and_requested_byes_close_into_correct_nonplayed_history(self):
        t, r = club(count=7)
        t = upsert_player(t, {"id": "p1", "scheduledByes": [{"round": 1, "points": .5}]})
        t = issue(t, r)
        self.assertEqual(len(t["managed"]["rounds"][0]["snapshot"]["requestedByes"]), 1)
        self.assertFalse(t["managed"]["rounds"][0]["snapshot"]["byes"])
        t = finish(t, r)
        record = next(p for p in t["players"] if p["id"] == "p1")["results"][0]
        self.assertEqual(record["result"], "HBYE")
        self.assertFalse(record["played"])
        self.assertIsNone(record["color"])
        odd, r = club(count=5)
        odd = issue(odd, r)
        allocated = odd["managed"]["rounds"][0]["snapshot"]["byes"]
        self.assertEqual(len(allocated), 1)
        odd = finish(odd, r)
        bye_player = next(p for p in odd["players"] if p["id"] == allocated[0]["player"]["id"])
        self.assertEqual(bye_player["results"][0]["result"], "BYE")
        self.assertEqual(bye_player["results"][0]["points"], 1)

    def test_forfeits_remain_unplayed_and_do_not_create_color_history(self):
        t, r = club(count=4)
        t = issue(t, r)
        t = record_results(t, 1, [{"board": 1, "result": "FORFEIT_WIN"}, {"board": 2, "result": "D"}])
        white = t["managed"]["rounds"][0]["snapshot"]["pairings"][0]["white"]["id"]
        t = close_round(t, r, 1)
        player = next(p for p in t["players"] if p["id"] == white)
        self.assertEqual(player["results"][0]["result"], "FORFEIT_WIN")
        self.assertFalse(player["results"][0]["played"])
        self.assertIsNone(player["results"][0]["color"])

    def test_results_are_versioned_and_can_be_cleared_without_changing_assignments(self):
        t, r = club()
        t = issue(t, r)
        revision = t["managed"]["revision"]
        saved = record_results(t, 1, [{"board": 1, "result": "W"}], revision)
        with self.assertRaisesRegex(ValueError, "another view"):
            record_results(saved, 1, [{"board": 1, "result": "D"}], revision)
        cleared = record_results(saved, 1, [{"board": 1, "result": None}])
        self.assertEqual(cleared["managed"]["rounds"][0]["results"], {})
        self.assertEqual(cleared["managed"]["rounds"][0]["resultRevision"], 2)
        self.assertEqual(cleared["managed"]["rounds"][0]["snapshotHash"], saved["managed"]["rounds"][0]["snapshotHash"])
        for results in ([{"board": 1, "result": "W"}, {"board": 1, "result": "L"}], [{"board": 80, "result": "W"}], [{"board": 1, "result": "ADJOURNED"}]):
            with self.subTest(results=results), self.assertRaises(ValueError):
                record_results(t, 1, results)

    def test_reopen_latest_preserves_assignment_snapshot_and_audits_correction(self):
        t, r = club(count=4)
        t = finish(issue(t, r), r, "W")
        issued_hash = t["managed"]["rounds"][0]["snapshotHash"]
        with self.assertRaisesRegex(ValueError, "reason"):
            reopen_latest(t, r, "")
        reopened = reopen_latest(t, r, "Board result was transposed")
        self.assertEqual(reopened["completedRounds"], 0)
        self.assertTrue(all(not p["results"] for p in reopened["players"]))
        reopened = record_results(reopened, 1, [{"board": 1, "result": "D"}, {"board": 2, "result": "L"}])
        corrected = close_round(reopened, r, 1)
        self.assertEqual(corrected["managed"]["rounds"][0]["snapshotHash"], issued_hash)
        self.assertTrue(any(a["action"] == "round-reopened" for a in corrected["managed"]["audit"]))
        drafted = generate_draft(corrected, r)
        with self.assertRaisesRegex(ValueError, "Discard"):
            reopen_latest(drafted, r, "Another correction")
        self.assertTrue(status(discard_draft(drafted), r)["canReopen"])

    def test_restore_checks_snapshot_and_completed_history_integrity(self):
        t, r = club(count=4)
        t = finish(issue(t, r), r)
        self.assertEqual(validate_managed(t), t)
        bad = deepcopy(t)
        bad["managed"]["rounds"][0]["snapshot"]["pairings"][0]["board"] = 10
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            validate_managed(bad)
        bad = deepcopy(t)
        bad["players"][0]["results"][0]["result"] = "W"
        with self.assertRaisesRegex(ValueError, "history does not match"):
            validate_managed(bad)
        bad = deepcopy(t)
        bad["managed"]["audit"].pop()
        with self.assertRaisesRegex(ValueError, "revisions"):
            validate_managed(bad)

    def test_issued_inputs_are_retained_and_restored_branches_have_new_identities(self):
        t, r = club(count=4)
        t = finish(issue(t, r), r)
        snapshot = deepcopy(t["managed"]["rounds"][0]["snapshot"])
        self.assertEqual(pairing_input_hash(snapshot["inputSnapshot"]), snapshot["inputHash"])
        t = upsert_player(t, {"id": "p4", "active": False})
        self.assertEqual(t["managed"]["rounds"][0]["snapshot"], snapshot)
        restored, restored_rules = restore_event(t, r)
        self.assertNotEqual(restored["managed"]["eventId"], t["managed"]["eventId"])
        self.assertEqual(restored["managed"]["rounds"], t["managed"]["rounds"])
        self.assertEqual(restored["players"], t["players"])
        self.assertEqual(restored_rules, r)
        self.assertEqual(restored["managed"]["audit"][-1]["details"]["sourceEventId"], t["managed"]["eventId"])

    def test_initial_rating_order_is_frozen_and_late_entries_append_without_renumbering(self):
        t, r = club(count=4)
        t = upsert_player(t, {"id": "p4", "rating": 2600})
        t = generate_draft(t, r)
        self.assertEqual(next(p for p in t["players"] if p["id"] == "p4")["pairingNumber"], 1)
        t = finish(issue_round(t, r, t["managed"]["draft"]["id"]), r)
        numbers = {p["id"]: p["pairingNumber"] for p in t["players"]}
        t = upsert_player(t, {"name": "Late Entrant", "rating": 2500})
        late = next(p for p in t["players"] if p["name"] == "Late Entrant")
        self.assertEqual(late["entryRound"], 2)
        self.assertEqual(late["pairingNumber"], 5)
        self.assertEqual({p["id"]: p["pairingNumber"] for p in t["players"] if p["id"] in numbers}, numbers)
        with self.assertRaisesRegex(ValueError, "initial pairing number"):
            upsert_player(t, {"id": "p1", "pairingNumber": 20})
        with self.assertRaisesRegex(ValueError, "Withdraw"):
            remove_player(t, "p1")

    def test_event_rules_are_frozen_after_issuance_and_equal_scores_share_rank(self):
        t, r = club(count=4, rounds=1)
        t = finish(issue(t, r), r)
        self.assertEqual(status(t, r)["stage"], "complete")
        self.assertFalse(status(t, r)["canGenerate"])
        self.assertEqual({p["rank"] for p in status(t, r)["standings"]}, {1})
        with self.assertRaisesRegex(ValueError, "fixed after"):
            update_event(t, r, {"profile": "fide-dutch"})
        with self.assertRaisesRegex(ValueError, "complete"):
            generate_draft(t, r)

    def test_manual_initial_numbers_are_explicit_and_must_be_complete(self):
        t, r = club(count=4)
        t, r = update_event(t, r, {"startingOrder": "manual"})
        with self.assertRaisesRegex(ValueError, "each number"):
            generate_draft(t, r)
        for i in range(4):
            t = upsert_player(t, {"id": f"p{i + 1}", "pairingNumber": 4 - i})
        self.assertTrue(status(generate_draft(t, r), r)["canIssue"])

    @unittest.skipUnless(availability()["available"], "Pinned native Dutch engine is not installed")
    def test_real_native_dutch_managed_event_completes_two_rounds(self):
        t, r = club(count=6, profile="fide-dutch")
        t = generate_draft(t, r)
        self.assertTrue(status(t, r)["canIssue"], status(t, r)["issues"])
        self.assertIn("BBP Pairings", t["managed"]["draft"]["forecast"]["engine"])
        t = finish(issue_round(t, r, t["managed"]["draft"]["id"]), r)
        t = finish(issue(t, r), r)
        self.assertEqual(t["completedRounds"], 2)
        self.assertEqual(sum(p["score"] for p in status(t, r)["standings"]), 6)


if __name__ == "__main__":
    unittest.main()
