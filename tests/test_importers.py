import io
import json
from pathlib import Path
import unittest
from zipfile import ZipFile

from app.importers import ImportError, load_tournament, normalize_tournament, parse_bytes, parse_text


ROOT = Path(__file__).resolve().parents[1]


def trf_player(id_, name, rating=1800, score=0, rounds=()):
    line = list(" " * (91 + 10 * len(rounds)))
    def put(start, end, value):
        line[start:end] = str(value).ljust(end - start)[:end - start]
    put(0, 3, "001")
    put(4, 8, str(id_).rjust(4))
    put(14, 47, name)
    put(48, 52, str(rating).rjust(4))
    put(80, 84, str(score).rjust(4))
    for index, (opponent, color, result) in enumerate(rounds):
        start = 91 + index * 10
        put(start, start + 4, str(opponent).rjust(4))
        put(start + 5, start + 6, color)
        put(start + 7, start + 8, result)
    return "".join(line)


class ImporterTests(unittest.TestCase):
    def test_source_workbook_retains_incomplete_evidence(self):
        tournament = load_tournament(next(ROOT.glob("*.xlsx")))
        self.assertEqual(len(tournament["players"]), 51)
        self.assertEqual(tournament["rounds"], 7)
        self.assertEqual(tournament["completedRounds"], 1)
        self.assertEqual(tournament["latestRound"], 6)
        players = {p["name"]: p for p in tournament["players"]}
        forney = players["Forney, Robert"]
        self.assertEqual(forney["usChessId"], "14069988")
        self.assertEqual(forney["id"], "5")
        self.assertEqual(forney["rating"], 2009)
        self.assertTrue(any(r["round"] == 6 and r["result"] == "W" for r in forney["results"]))
        self.assertIsNone(next(r for r in forney["results"] if r["round"] == 6)["color"])
        tagorda = players["Tagorda, Ramon"]
        self.assertEqual(sum(r["round"] == 2 for r in tagorda["results"]), 2)
        self.assertTrue(any(r["result"] == "UNPLAYED" for p in tournament["players"] for r in p["results"]))
        self.assertTrue(any("acceleration" in warning.lower() for warning in tournament["warnings"]))
        self.assertEqual(players["Beavers, Carl T"]["scheduledByes"][-1], {"round": 7, "points": .5})
        paired_unknown_rows = {r["sourceRow"] for p in tournament["players"] for r in p["results"]
                               if r.get("sourceRow") and r["opponentId"] and r["color"] is None}
        played_unknown_rows = {r["sourceRow"] for p in tournament["players"] for r in p["results"]
                               if r.get("sourceRow") and r["played"] and r["color"] is None}
        self.assertEqual(len(paired_unknown_rows), 55)
        self.assertEqual(len(played_unknown_rows), 54)

    def test_no_formula_forecast_round_is_imported(self):
        tournament = load_tournament(next(ROOT.glob("*.xlsx")))
        self.assertFalse(any(r["round"] == 7 for p in tournament["players"] for r in p["results"]))

    def test_csv_round_tokens_have_optional_known_colors(self):
        tournament = parse_text('id,name,rating,score,R1,R2\n1,"Alpha, Ada",1900,1.5,W2w,HBYE\n2,Beta,1800,0,L1b,ZERO\n')
        self.assertEqual(tournament["completedRounds"], 2)
        first = tournament["players"][0]
        self.assertEqual(first["name"], "Alpha, Ada")
        self.assertEqual(first["reportedScore"], 1.5)
        self.assertEqual(first["results"][0]["color"], "W")
        self.assertEqual(first["results"][1]["result"], "HBYE")
        unknown = parse_text('id,name,R1\n1,Alpha,W2\n2,Beta,L1\n')
        self.assertIsNone(unknown["players"][0]["results"][0]["color"])

    def test_csv_blanks_remain_absent(self):
        tournament = parse_text('id,name,R1,R2\n1,Alpha,W2,\n2,Beta,L1,\n')
        self.assertEqual(tournament["latestRound"], 1)
        self.assertEqual(len(tournament["players"][0]["results"]), 1)

    def test_csv_color_warning_only_for_missing_played_colors(self):
        known = parse_text('id,name,R1\n1,Alpha,W2w\n2,Beta,L1b\n')
        self.assertEqual(known["warnings"], [])
        missing = parse_text('id,name,R1\n1,Alpha,W2\n2,Beta,L1b\n')
        self.assertEqual(len(missing["warnings"]), 1)
        self.assertIn("unknown colors", missing["warnings"][0])
        bye_only = parse_text('id,name,R1\n1,Alpha,BYE\n')
        self.assertEqual(bye_only["warnings"], [])

    def test_bad_csv_is_actionable(self):
        with self.assertRaisesRegex(ImportError, "name column"):
            parse_text('player,elo\nAlpha,1200\n')
        with self.assertRaisesRegex(ImportError, "Unrecognized"):
            parse_text('id,name,R1\n1,Alpha,maybe\n')

    def test_json_duplicate_id_rejected(self):
        with self.assertRaisesRegex(ImportError, "Duplicate"):
            parse_text(json.dumps({"players": [{"id": 1, "name": "Alpha"}, {"id": "1", "name": "Beta"}]}))

    def test_missing_opponent_is_warning_not_fabricated(self):
        tournament = parse_text('id,name,R1\n1,Alpha,W99\n')
        self.assertEqual(tournament["players"][0]["results"][0]["opponentId"], "99")
        self.assertIn("99", tournament["warnings"][1])

    def test_trf_normal_results_and_byes(self):
        source = "\n".join([
            "012 Test Open", "142 5",
            trf_player(1, "Alpha", score=1.5, rounds=[(2, "w", "1"), ("0000", "-", "H")]),
            trf_player(2, "Beta", score=0, rounds=[(1, "b", "0"), ("0000", "-", "Z")]),
        ])
        tournament = parse_text(source, "event.trf")
        self.assertEqual(tournament["name"], "Test Open")
        self.assertEqual(tournament["rounds"], 5)
        self.assertEqual(tournament["completedRounds"], 2)
        self.assertEqual(tournament["players"][0]["results"][1]["result"], "HBYE")
        self.assertEqual(tournament["players"][0]["results"][0]["opponentId"], "2")

    def test_trf_forfeits_and_unrated_games_not_counted_as_played(self):
        source = trf_player(1, "Alpha", rounds=[(2, "w", "+"), (3, "b", "D")])
        results = parse_text(source, "event.trf")["players"][0]["results"]
        self.assertEqual(results[0]["result"], "FORFEIT_WIN")
        self.assertFalse(results[0]["played"])
        self.assertEqual(results[1]["result"], "D")
        self.assertFalse(results[1]["played"])

    def test_trf_explicit_blank_result_is_zero_bye(self):
        source = trf_player(1, "Alpha", rounds=[("0000", "-", " ")])
        result = parse_text(source, "event.trf")["players"][0]["results"][0]
        self.assertEqual(result["result"], "ZERO")

    def test_trf_unsupported_pairing_records_rejected(self):
        with self.assertRaisesRegex(ImportError, "record 250"):
            parse_text("250 1 1.0\n" + trf_player(1, "Alpha"), "event.trf")

    def test_us_chess_html_preserves_unknown_colors_and_ratings(self):
        text = '''<html><title>State Open</title><pre>
 Pair | Player Name                     |Total|Round|Round|
    1 | ALPHA, ADA                      |1.0  |W   2|H    |
      | 12345678 / R: 1900 ->1910        |     |     |     |
    2 | BETA, BEN                       |0.0  |L   1|U    |
      | 87654321 / R: 1800 ->1790        |     |     |     |
 </pre></html>'''
        tournament = parse_text(text, "event.html")
        self.assertEqual(tournament["players"][0]["rating"], 1900)
        self.assertEqual(tournament["players"][0]["usChessId"], "12345678")
        self.assertIsNone(tournament["players"][0]["results"][0]["color"])

    def test_arbitrary_html_is_rejected(self):
        with self.assertRaisesRegex(ImportError, "single section|one classic"):
            parse_text('<html><table><tr><td>1</td></tr></table></html>', "event.html")

    def test_binary_and_empty_errors_are_actionable(self):
        with self.assertRaisesRegex(ImportError, "valid XLSX"):
            parse_bytes(b'not a zip', "broken.xlsx")
        with self.assertRaisesRegex(ImportError, "empty"):
            parse_text("")
        with self.assertRaisesRegex(ImportError, "JSON"):
            parse_text('{broken}', "broken.json")

    def test_nonfinite_rating_and_round_bounds(self):
        with self.assertRaisesRegex(ImportError, "finite whole"):
            normalize_tournament({"players": [{"id": 1, "name": "Alpha", "rating": float("nan")} ]})
        with self.assertRaisesRegex(ImportError, "finite whole"):
            normalize_tournament({"rounds": 5.5, "players": [{"id": 1, "name": "Alpha"}]})
        with self.assertRaisesRegex(ImportError, "round count"):
            normalize_tournament({"rounds": 5, "players": [{"id": 1, "name": "Alpha", "results": [{"round": 6, "result": "BYE"}]}]})

    def test_invalid_numeric_csv_is_not_silently_zero(self):
        for value in ("NaN", "inf", "unknown", "1700.5"):
            with self.subTest(rating=value), self.assertRaises(ImportError):
                parse_text(f'id,name,rating\n1,Alpha,{value}\n')
        with self.assertRaisesRegex(ImportError, "finite"):
            parse_text('id,name,score\n1,Alpha,NaN\n')

    def test_provenance_and_initial_score_survive_json_round_trip(self):
        source = {"rounds": 3, "completedRounds": 0,
                  "provenance": {"source": "original.trf", "format": "TRF", "url": "https://example.org/event", "rawSource": "download"},
                  "players": [{"id": "1", "name": "Alpha", "initialScore": "1.5", "results": [{"round": 1, "result": "BYE"}]}]}
        tournament = normalize_tournament(source, "edited.json")
        self.assertEqual(tournament["provenance"]["source"], "original.trf")
        self.assertEqual(tournament["provenance"]["url"], "https://example.org/event")
        self.assertEqual(tournament["provenance"]["rawSource"], "download")
        self.assertEqual(tournament["provenance"]["importedFile"], "edited.json")
        self.assertEqual(tournament["players"][0]["initialScore"], 1.5)
        self.assertEqual(tournament["completedRounds"], 0)
        self.assertEqual(tournament["latestRound"], 1)

    def test_overstated_completion_is_reduced_with_warning(self):
        tournament = normalize_tournament({"rounds": 3, "completedRounds": 3, "players": [{"id": 1, "name": "Alpha", "results": [{"round": 1, "result": "BYE"}]}]})
        self.assertEqual(tournament["completedRounds"], 1)
        self.assertTrue(any("declared 3" in warning for warning in tournament["warnings"]))

    def test_explicit_participation_ranges_allow_completed_rounds(self):
        tournament = normalize_tournament({"rounds": 3, "players": [
            {"id": 1, "name": "Alpha", "results": [{"round": 1, "result": "BYE"}, {"round": 2, "result": "BYE"}]},
            {"id": 2, "name": "Beta", "entryRound": 2, "results": [{"round": 2, "result": "ZERO"}]},
            {"id": 3, "name": "Gamma", "withdrawnAfterRound": 1, "active": False, "results": [{"round": 1, "result": "ZERO"}]},
        ]})
        self.assertEqual(tournament["completedRounds"], 2)

    def test_malformed_optional_shapes_return_import_errors(self):
        base = {"players": [{"id": 1, "name": "Alpha"}]}
        for field, value in (("provenance", []), ("warnings", "warning")):
            with self.subTest(field=field), self.assertRaises(ImportError):
                normalize_tournament({**base, field: value})
        for field, value in (("scheduledByes", {}), ("entryRound", "early"), ("withdrawnAfterRound", -1), ("initialScore", float("inf"))):
            with self.subTest(field=field), self.assertRaises(ImportError):
                normalize_tournament({"players": [{"id": 1, "name": "Alpha", field: value}]})
        with self.assertRaisesRegex(ImportError, "played flag"):
            normalize_tournament({"players": [{"id": 1, "name": "Alpha", "results": [{"round": 1, "result": "BYE", "played": "true"}]}]})

    def test_all_unknown_trf_records_rejected(self):
        for code in ("152", "162", "172", "192", "240", "250", "260", "XXA", "XXP", "999"):
            with self.subTest(code=code), self.assertRaisesRegex(ImportError, f"record {code}"):
                parse_text(f"{code} settings\n" + trf_player(1, "Alpha"), "event.trf")
        with self.assertRaisesRegex(ImportError, "Team tournaments"):
            parse_text("082 4\n" + trf_player(1, "Alpha"), "event.trf")


if __name__ == "__main__":
    unittest.main()
