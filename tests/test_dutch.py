"""Real BBP 6.0.0 integration plus independently checked exchange boundaries.

C5 and C9 vectors are adapted from the engine's Apache-2.0 test fixtures at
16a000f9811de322b0e835d5643198226165b5a9, retaining expected ordered pairings.
https://github.com/BieremaBoyzProgramming/bbpPairings/tree/v6.0.0/test/tests
"""
from copy import deepcopy
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from app.dutch import availability, forecast_dutch, manifest, serialize_tournament, _read_output
from app.importers import parse_text
from app.pairing import normalize_rules
from app.validation import validate_tournament


RULES = normalize_rules({"profile": "fide-dutch"})


def event(count):
    return validate_tournament({"rounds": 7, "completedRounds": 0,
        "players": [{"id": str(i), "name": f"Player {i}", "rating": 2400 - i * 50,
                     "active": True, "results": []} for i in range(1, count + 1)]})


def game(tournament, white, black, result="W", rnd=1, played=True):
    reverse = {"W": "L", "L": "W", "D": "D", "ADJOURNED": "ADJOURNED", "FORFEIT_WIN": "FORFEIT_LOSS", "FORFEIT_LOSS": "FORFEIT_WIN"}
    by_id = {p["id"]: p for p in tournament["players"]}
    by_id[str(white)]["results"].append({"round": rnd, "opponentId": str(black), "color": "W" if played else None, "result": result, "played": played})
    by_id[str(black)]["results"].append({"round": rnd, "opponentId": str(white), "color": "B" if played else None, "result": reverse[result], "played": played})


def fixture_row(number, rating, score, rounds):
    header = list(" " * 91)
    def put(start, end, value):
        header[start:end] = str(value).ljust(end - start)[:end - start]
    put(0, 3, "001")
    put(4, 8, f"{number:4d}")
    put(14, 47, f"Test{number:04d} Player{number:04d}")
    put(48, 52, f"{rating:4d}")
    put(80, 84, f"{score:4.1f}")
    return "".join(header) + "".join(f"{opponent:04d} {color} {outcome}  " for opponent, color, outcome in rounds)


def official_fixture(which):
    if which == "C5":
        rows = [
            fixture_row(1, 2720, 2, [(4, "w", "1"), (2, "b", "1")]),
            fixture_row(2, 2701, 1, [(5, "b", "1"), (1, "w", "0")]),
            fixture_row(3, 2697, 2, [(6, "w", "1"), (4, "b", "1")]),
            fixture_row(4, 2689, 0, [(1, "b", "0"), (3, "w", "0")]),
            fixture_row(5, 2673, 1, [(2, "w", "0"), (6, "b", "1")]),
            fixture_row(6, 2664, 0, [(3, "b", "0"), (5, "w", "0")]),
        ]
        t = parse_text("012 Dutch 2025 C5 test\n" + "\n".join(rows) + "\nXXR 3\n", "fixture.trf")
        t["players"][3]["scheduledByes"] = [{"round": 3, "points": 0}]
        return validate_tournament(t), [("1", "5"), ("3", "2")], "6"
    rows = [
        fixture_row(1, 2720, 1, [(3, "w", "1")]),
        fixture_row(2, 2701, 1, [(4, "b", "1")]),
        fixture_row(3, 2697, 0, [(1, "b", "0")]),
        fixture_row(4, 2689, 0, [(2, "w", "0")]),
        fixture_row(5, 2673, 0, [(0, "-", "Z")]),
    ]
    t = parse_text("012 Dutch 2025 C9 test\n" + "\n".join(rows) + "\nXXR 3\n", "fixture.trf")
    return validate_tournament(t), [("2", "1"), ("3", "5")], "4"


class DutchExchangeTests(unittest.TestCase):
    def test_manifest_pins_actual_release_and_executable(self):
        pinned = manifest()
        self.assertEqual(pinned["version"], "6.0.0")
        self.assertEqual(pinned["sourceCommit"], "16a000f9811de322b0e835d5643198226165b5a9")
        self.assertEqual(len(pinned["executableSha256"]), 64)
        self.assertIn("v6.0.0", pinned["archiveUrl"])

    def test_exchange_keeps_pairing_numbers_not_rating_or_score_order(self):
        t = event(4)
        for p in t["players"]:
            p["pairingNumber"] = int(p["id"])
            p["id"] = "external-" + p["id"]
            p["rating"] = int(p["pairingNumber"]) * 100
        trf, numbered, _, _, _ = serialize_tournament(t, RULES)
        self.assertEqual([numbered[i]["id"] for i in range(1, 5)], ["external-1", "external-2", "external-3", "external-4"])
        self.assertIn("XXC white1", trf)

    def test_tpn_gaps_are_not_silently_compacted(self):
        t = event(2)
        t["players"][0]["pairingNumber"] = 2
        t["players"][1]["pairingNumber"] = 4
        with self.assertRaisesRegex(ValueError, "parity"):
            serialize_tournament(t, RULES)

    def test_future_byes_and_withdrawals_are_distinct_from_history(self):
        t = event(4)
        t["players"][0]["scheduledByes"] = [{"round": 1, "points": .5}]
        t["players"][1]["active"] = False
        trf, _, eligible, requested, _ = serialize_tournament(t, RULES)
        rows = [line for line in trf.splitlines() if line.startswith("001")]
        self.assertTrue(rows[0].endswith("0000 - H  "))
        self.assertTrue(rows[1].endswith("0000 - Z  "))
        self.assertEqual(eligible, {"3", "4"})
        self.assertEqual(requested[0]["points"], .5)

    def test_manual_acceleration_and_prohibitions_are_exported(self):
        t = event(4)
        t["acceleration"] = "manual"
        t["players"][0]["virtualPoints"] = [{"round": 1, "points": 1}]
        t["prohibitedPairs"] = [["1", "3"]]
        trf, _, _, _, _ = serialize_tournament(t, RULES)
        self.assertIn("XXA    1  1.0", trf)
        self.assertIn("XXP 1 3", trf)

    def test_baku_flag_and_manual_acceleration_cannot_be_mixed(self):
        for acceleration in ("baku", {"method": "baku"}):
            t = event(8)
            t["acceleration"] = acceleration
            trf, _, _, _, _ = serialize_tournament(t, RULES)
            self.assertIn("192 FIDE_DUTCH_2025_BAKU\n", trf)
            self.assertNotIn("XXA", trf)
            t["players"][0]["virtualPoints"] = [{"round": 1, "points": 1}]
            with self.assertRaisesRegex(ValueError, "combined"):
                serialize_tournament(t, RULES)
        t = event(8)
        t["acceleration"] = {"method": "baku", "rounds": 3}
        with self.assertRaisesRegex(ValueError, "overrides"):
            serialize_tournament(t, RULES)

    def test_zero_manual_entries_do_not_disable_baku(self):
        t = event(8)
        t["acceleration"] = "baku"
        t["players"][0]["virtualPoints"] = [{"round": 1, "points": 0}]
        trf, _, _, _, _ = serialize_tournament(t, RULES)
        self.assertIn("FIDE_DUTCH_2025_BAKU", trf)
        self.assertNotIn("XXA", trf)

    def test_unsupported_semantics_do_not_silently_fall_back(self):
        for extra in ({"forcedPairs": [["1", "2"]]},):
            with self.assertRaisesRegex(ValueError, "forced"):
                serialize_tournament({**event(2), **extra}, RULES)
        t = event(2)
        t["players"][0]["initialScore"] = .5
        with self.assertRaisesRegex(ValueError, "starting"):
            serialize_tournament(t, RULES)

    def test_independent_output_check_rejects_bad_coverage_and_rematches(self):
        t = event(4)
        _, numbered, eligible, _, _ = serialize_tournament(t, RULES)
        for output in ("2\n1 3\n1 4\n", "1\n1 3\n", "2\n1 99\n2 4\n", "2\n1 1\n2 4\n", "1\n1 0\n"):
            with self.assertRaises(ValueError):
                _read_output(output, numbered, eligible, t, RULES)
        game(t, 1, 3)
        game(t, 2, 4)
        t["completedRounds"] = 1
        with self.assertRaisesRegex(ValueError, "repeated"):
            _read_output("2\n1 3\n2 4\n", numbered, eligible, t, RULES)

    def test_no_engine_means_no_approximate_fallback(self):
        with patch("app.dutch.availability", return_value={"available": False, "reason": "not installed", "ruleEdition": "FIDE Dutch 2026-02-01"}):
            f = forecast_dutch(event(4), RULES)
        self.assertEqual(f["status"], "blocked")
        self.assertEqual(f["pairings"], [])
        self.assertIn("not installed", f["issues"])

    def test_unreadable_or_malformed_metadata_is_unavailable_not_an_app_crash(self):
        for error in (OSError("unreadable"), ValueError("malformed"), KeyError("missing")):
            with patch("app.dutch.manifest", side_effect=error):
                result = availability()
                self.assertFalse(result["available"])
                self.assertIn("could not be verified", result["reason"])


@unittest.skipUnless(availability()["available"], "Pinned native Dutch engine is not installed; run scripts/setup_dutch_engine.py")
class NativeDutchTests(unittest.TestCase):
    def predict(self, t, **extra):
        result = forecast_dutch(validate_tournament(t), normalize_rules({**RULES, **extra}))
        self.assertEqual(result["status"], "ok", result["issues"])
        return result

    def test_official_2026_c5_and_c9_vectors(self):
        for which in ("C5", "C9"):
            with self.subTest(which=which):
                t, expected, bye = official_fixture(which)
                f = self.predict(t)
                self.assertEqual([(p["white"]["id"], p["black"]["id"]) for p in f["pairings"]], expected)
                self.assertEqual(f["bye"]["id"], bye)

    def test_even_first_round_and_initial_color(self):
        f = self.predict(event(4))
        self.assertEqual([(p["white"]["id"], p["black"]["id"]) for p in f["pairings"]], [("1", "3"), ("4", "2")])
        f = self.predict(event(4), firstRoundColor="B")
        self.assertEqual([(p["white"]["id"], p["black"]["id"]) for p in f["pairings"]], [("3", "1"), ("2", "4")])

    def test_withdrawn_and_requested_byes_are_excluded(self):
        t = event(6)
        t["players"][0]["active"] = False
        t["players"][1]["scheduledByes"] = [{"round": 1, "points": .5}]
        f = self.predict(t)
        self.assertEqual(len(f["pairings"]), 2)
        assigned = {p[c]["id"] for p in f["pairings"] for c in ("white", "black")}
        self.assertEqual(assigned, {"3", "4", "5", "6"})
        self.assertEqual(f["requestedByes"][0]["player"]["id"], "2")

    def test_custom_half_point_pairing_bye_keeps_repeat_eligibility_rule(self):
        t = event(3)
        t["completedRounds"] = 1
        t["players"][0]["results"] = [{"round": 1, "result": "BYE", "played": False, "points": .5}]
        game(t, 2, 3, "D")
        f = self.predict(t, byePoints=.5)
        self.assertNotEqual(f["bye"]["id"], "1")

    def test_actual_prohibited_pair_changes_native_first_round(self):
        t = event(4)
        t["prohibitedPairs"] = [["1", "3"]]
        f = self.predict(t)
        self.assertNotIn(frozenset(("1", "3")), [frozenset((p["white"]["id"], p["black"]["id"])) for p in f["pairings"]])

    def test_actual_manual_acceleration_changes_native_brackets(self):
        t = event(4)
        t["acceleration"] = "manual"
        for p in t["players"][:2]:
            p["virtualPoints"] = [{"round": 1, "points": 1}]
        f = self.predict(t)
        pairs = {frozenset((p["white"]["id"], p["black"]["id"])) for p in f["pairings"]}
        self.assertEqual(pairs, {frozenset(("1", "2")), frozenset(("3", "4"))})

    def test_actual_standard_baku_changes_brackets_without_changing_standings(self):
        ordinary = self.predict(event(8))
        ordinary_pairs = {frozenset((p["white"]["id"], p["black"]["id"])) for p in ordinary["pairings"]}
        for method in ("baku", {"method": "baku"}):
            t = event(8)
            t["acceleration"] = method
            before = deepcopy(t)
            f = self.predict(t)
            accelerated_pairs = {frozenset((p["white"]["id"], p["black"]["id"])) for p in f["pairings"]}
            self.assertEqual(accelerated_pairs, {frozenset(("1", "3")), frozenset(("2", "4")), frozenset(("5", "7")), frozenset(("6", "8"))})
            self.assertNotEqual(accelerated_pairs, ordinary_pairs)
            self.assertEqual(t, before)
            self.assertEqual(f["engineDetails"]["acceleration"], "baku")
            self.assertTrue(all(p[c]["score"] == 0 and "pairingScore" not in p[c] for p in f["pairings"] for c in ("white", "black")))

    def test_actual_standard_baku_is_recomputed_after_played_round(self):
        t = event(8)
        t["acceleration"] = "baku"
        first = self.predict(t)
        for p in first["pairings"]:
            game(t, p["white"]["id"], p["black"]["id"])
        t["completedRounds"] = 1
        native = self.predict(t)
        pairs = {frozenset((p["white"]["id"], p["black"]["id"])) for p in native["pairings"]}
        self.assertIn(frozenset(("1", "4")), pairs)
        self.assertTrue(all(p[c]["score"] in {0, 1} for p in native["pairings"] for c in ("white", "black")))

    def test_double_forfeit_does_not_create_a_played_rematch(self):
        t = event(2)
        for i, p in enumerate(t["players"]):
            p["results"] = [{"round": 1, "result": "FORFEIT_LOSS", "opponentId": str(2 - i), "played": False, "color": None}]
        t["completedRounds"] = 1
        self.assertEqual(len(self.predict(t)["pairings"]), 1)

    def test_impossible_native_pairing_has_no_heuristic_fallback(self):
        t = event(2)
        game(t, 1, 2)
        t["completedRounds"] = 1
        f = forecast_dutch(validate_tournament(t), RULES)
        self.assertEqual(f["status"], "blocked")
        self.assertEqual(f["blockingReason"], "constraints")
        self.assertEqual(f["pairings"], [])


if __name__ == "__main__":
    unittest.main()
