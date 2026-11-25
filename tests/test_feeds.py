"""Operational refresh behavior across unrelated publisher snapshots."""
from copy import deepcopy
from io import BytesIO
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

from app.feeds import fetch_feed, parse_feed_bytes, parse_feed_rows, preview_feed, snapshot_hash
from app.validation import validate_tournament
from app.importers import parse_bytes


def workspace():
    return {"name": "City Weekend", "section": "U1800", "rounds": 5, "completedRounds": 0,
            "players": [], "tracking": {"roundsKnown": True, "playerName": "Ada One", "sourceUrl": "https://organizer.example/standings"},
            "sourceSettings": {"mergeRound": 3}, "warnings": [], "provenance": {"source": "My event setup"}}


def incoming(text):
    return parse_feed_bytes(text.encode(), "live.csv")


def generic_workbook(sheets):
    """Create real OOXML package bytes without a spreadsheet dependency."""
    stream = BytesIO()
    main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with ZipFile(stream, "w") as archive:
        declarations, relationships = [], []
        for number, (name, rows) in enumerate(sheets, 1):
            declarations.append(f'<sheet name="{escape(name)}" sheetId="{number}" r:id="rId{number}"/>')
            relationships.append(f'<Relationship Id="rId{number}" Type="{rel_ns}/worksheet" Target="worksheets/sheet{number}.xml"/>')
            xml_rows = []
            for row_number, cells in rows.items():
                body = []
                for column, value in cells.items():
                    reference = f"{column}{row_number}"
                    if isinstance(value, (int, float)):
                        body.append(f'<c r="{reference}"><v>{value}</v></c>')
                    else:
                        body.append(f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
                xml_rows.append(f'<row r="{row_number}">{"".join(body)}</row>')
            archive.writestr(f"xl/worksheets/sheet{number}.xml", f'<worksheet xmlns="{main_ns}"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{main_ns}" xmlns:r="{rel_ns}"><sheets>{"".join(declarations)}</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(relationships) + '</Relationships>')
    return stream.getvalue()


class FeedTests(unittest.TestCase):
    def test_static_html_crosstable_imports_colors_and_identity_links(self):
        html = b'''<html><title>City Weekend</title><h2>U1800</h2><table><tr><th>#</th><th>Player</th><th>Rating</th><th>Pts</th><th>R1</th></tr>
        <tr><td>8</td><td>Ada One <a href="https://ratings.uschess.org/player/12345678">12345678</a></td><td>1700</td><td>1</td><td>W 3 w</td></tr>
        <tr><td>3</td><td>Ben Two</td><td>1600</td><td>0</td><td>L8b</td></tr></table></html>'''
        event = parse_feed_bytes(html, "event.html")
        self.assertEqual(event["name"], "City Weekend")
        self.assertEqual(event["section"], "U1800")
        self.assertEqual(event["players"][0]["usChessId"], "12345678")
        self.assertEqual(event["players"][0]["name"], "Ada One")
        self.assertEqual(event["players"][0]["results"][0]["color"], "W")
        self.assertNotIn("seed", event["players"][0])
        self.assertEqual(event["completedRounds"], 1)

    def test_final_ranks_change_but_stable_internal_identities_do_not(self):
        first = preview_feed(workspace(), incoming("Rank,Player,Rating,Pts,R1\n1,Ada One,1700,1,W2w\n2,Ben Two,1600,0,L1b\n"))
        original = deepcopy(first["tournament"])
        second = preview_feed(original, incoming("Rank,Player,Rating,Pts,R1,R2\n1,Ben Two,1600,1,L2b,W2w\n2,Ada One,1700,1,W1w,L1b\n"))
        self.assertEqual([(p["id"], p["name"]) for p in second["tournament"]["players"]], [("p1", "Ada One"), ("p2", "Ben Two")])
        self.assertEqual(second["tournament"]["players"][0]["results"][1]["opponentId"], "p2")
        self.assertEqual(second["summary"]["updatedResults"], 0)
        self.assertEqual(second["summary"]["addedResults"], 2)
        self.assertEqual(second["tournament"]["completedRounds"], 2)
        self.assertEqual(second["tournament"]["name"], "City Weekend")
        self.assertEqual(original, first["tournament"], "Preview must not mutate its input")

    def test_known_setup_is_preserved_and_missing_players_are_not_withdrawn(self):
        current = preview_feed(workspace(), incoming("Name,Rating\nAda One,1700\nBen Two,1600\n"))["tournament"]
        current["players"][0].update(pairingNumber=7, pairingRating=1750, schedule="Slow", scheduledByes=[{"round": 3, "points": .5}])
        current["forcedPairs"] = [["p1", "p2"]]
        proposal = preview_feed(current, incoming("Name,Rating,Points\nAda One,1800,0\n"))
        player = proposal["tournament"]["players"][0]
        self.assertEqual((player["rating"], player["pairingNumber"], player["pairingRating"], player["schedule"]), (1700, 7, 1750, "Slow"))
        self.assertEqual(player["scheduledByes"], [{"round": 3, "points": .5}])
        self.assertEqual(proposal["tournament"]["forcedPairs"], [["p1", "p2"]])
        self.assertEqual(proposal["tournament"]["tracking"], current["tracking"])
        self.assertTrue(proposal["tournament"]["players"][1]["active"])
        self.assertEqual(proposal["summary"]["preservedPlayers"], 1)

    def test_membership_identity_wins_over_name_and_row_order(self):
        current = preview_feed(workspace(), incoming("Rank,Name,USCF ID\n1,Ada One,12345678\n2,Ben Two,87654321\n"))["tournament"]
        proposal = preview_feed(current, incoming("Rank,Name,USCF ID\n9,Ada A. One,12345678\n3,Ben Two,87654321\n"))
        self.assertEqual(proposal["summary"]["addedPlayers"], 0)
        self.assertEqual(proposal["summary"]["identityMatches"][0]["method"], "usChessId")
        self.assertEqual(proposal["summary"]["identityMatches"][0]["playerId"], "p1")

    def test_changed_or_ambiguous_membership_identity_is_rejected(self):
        current = preview_feed(workspace(), incoming("Name,USCF ID\nAda One,12345678\n"))["tournament"]
        with self.assertRaisesRegex(ValueError, "membership ID changed"):
            preview_feed(current, incoming("Name,USCF ID\nAda One,87654321\n"))
        current["players"].append({"id": "other", "name": "Ada One", "rating": 1700, "results": []})
        with self.assertRaisesRegex(ValueError, "Ambiguous name"):
            preview_feed(current, incoming("Name\nAda One\n"))

    def test_duplicate_incoming_member_is_not_merged(self):
        with self.assertRaisesRegex(ValueError, "duplicate membership"):
            preview_feed(workspace(), incoming("Name,USCF ID\nAda One,12345678\nBen Two,12345678\n"))

    def test_totals_only_standings_never_invent_history_or_starting_order(self):
        proposal = preview_feed(workspace(), incoming("Rank,Name,Rating,Points\n1,Ada One,1700,2.5\n2,Ben Two,1600,2\n"))
        self.assertTrue(proposal["summary"]["rosterOnly"])
        self.assertEqual(proposal["tournament"]["completedRounds"], 0)
        for player in proposal["tournament"]["players"]:
            self.assertEqual(player["results"], [])
            self.assertNotIn("seed", player)
            self.assertNotIn("pairingNumber", player)
            self.assertNotIn("reportedScore", player)
            self.assertIn("sourceReportedScore", player)

    def test_feed_warnings_are_replaced_when_roster_and_totals_are_reconciled(self):
        current = workspace()
        current["warnings"] = ["Director confirmed 5 rounds; keep this manual note."]
        roster = preview_feed(current, incoming("Rank,Name,Points\n1,Ada One,1\n2,Ben Two,0\n"))["tournament"]
        self.assertTrue(any("Only a roster" in warning for warning in roster["warnings"]))
        self.assertTrue(any("source total 1" in warning for warning in roster["warnings"]))
        final = preview_feed(roster, incoming("Rank,Name,Points,R1\n1,Ada One,1,W2w\n2,Ben Two,0,L1b\n"))["tournament"]
        self.assertFalse(any("Only a roster" in warning or "supplies no round history" in warning for warning in final["warnings"]))
        self.assertFalse(any("source total" in warning for warning in final["warnings"]))
        self.assertIn("Director confirmed 5 rounds; keep this manual note.", final["warnings"])
        self.assertEqual(final["provenance"]["lastStandingsRefresh"]["warningMessages"],
                         [warning for warning in final["warnings"] if not warning.startswith("Director")])

    def test_early_unattributed_feed_warnings_migrate_when_evidence_resolves(self):
        roster = preview_feed(workspace(), incoming("Rank,Name,Points\n1,Ada One,1\n2,Ben Two,0\n"))["tournament"]
        roster["provenance"]["lastStandingsRefresh"].pop("warningMessages")
        roster["warnings"].append("Ada One: source total needs a director review; keep this manual note.")
        refreshed = preview_feed(roster, incoming("Rank,Name,Points,R1\n1,Ada One,1,W2w\n2,Ben Two,0,L1b\n"))["tournament"]
        self.assertFalse(any("Only a roster" in warning or "supplies no round history" in warning for warning in refreshed["warnings"]))
        self.assertFalse(any("is retained as published evidence" in warning for warning in refreshed["warnings"]))
        self.assertIn("Ada One: source total needs a director review; keep this manual note.", refreshed["warnings"])

    def test_partial_refresh_does_not_erase_old_rounds_or_known_colors(self):
        current = preview_feed(workspace(), incoming("Rank,Name,R1,R2\n1,Ada One,W2w,L2b\n2,Ben Two,L1b,W1w\n"))["tournament"]
        proposal = preview_feed(current, incoming("Rank,Name,R2\n1,Ada One,L2\n2,Ben Two,W1\n"))
        self.assertEqual(len(proposal["tournament"]["players"][0]["results"]), 2)
        self.assertEqual(proposal["tournament"]["players"][0]["results"][1]["color"], "B")
        self.assertEqual(proposal["summary"]["updatedResults"], 0)
        self.assertEqual(proposal["summary"]["missingColors"], [])

    def test_corrections_are_visible_and_reviewed_instead_of_silently_applied(self):
        current = preview_feed(workspace(), incoming("Rank,Name,R1\n1,Ada One,W2w\n2,Ben Two,L1b\n"))["tournament"]
        before = deepcopy(current)
        proposal = preview_feed(current, incoming("Rank,Name,R1\n1,Ada One,D2w\n2,Ben Two,D1b\n"))
        self.assertEqual(len(proposal["summary"]["corrections"]), 2)
        self.assertEqual(proposal["summary"]["updatedResults"], 2)
        self.assertTrue(proposal["requiresReview"])
        self.assertEqual(proposal["baseHash"], snapshot_hash(current))
        self.assertEqual(current, before)

    def test_missing_colors_stay_unknown(self):
        proposal = preview_feed(workspace(), incoming("Rank,Name,R1\n1,Ada One,W2\n2,Ben Two,L1\n"))
        self.assertEqual(len(proposal["summary"]["missingColors"]), 2)
        self.assertIsNone(proposal["tournament"]["players"][0]["results"][0]["color"])

    def test_pending_source_does_not_erase_a_reviewed_final_result(self):
        current = preview_feed(workspace(), incoming("Rank,Name,R1\n1,Ada One,W2w\n2,Ben Two,L1b\n"))["tournament"]
        proposal = preview_feed(current, incoming("Rank,Name,R1\n1,Ada One,UNPLAYED\n2,Ben Two,UNPLAYED\n"))
        self.assertEqual(proposal["summary"]["preservedRecords"], 2)
        self.assertEqual(proposal["tournament"]["completedRounds"], 1)
        self.assertEqual(proposal["tournament"]["players"][0]["results"][0]["result"], "W")

    def test_unknown_ratings_can_be_learned_but_known_setup_is_retained(self):
        current = preview_feed(workspace(), incoming("Name,Rating\nAda One,0\n"))["tournament"]
        learned = preview_feed(current, incoming("Name,Rating\nAda One,1700\n"))["tournament"]
        self.assertEqual(learned["players"][0]["rating"], 1700)
        proposal = preview_feed(learned, incoming("Name,Rating\nAda One,1800\n"))
        self.assertEqual(proposal["tournament"]["players"][0]["rating"], 1700)
        self.assertEqual(proposal["summary"]["setupDifferences"][0]["published"], 1800)

    def test_conflicting_membership_columns_and_duplicate_rounds_are_rejected(self):
        current = preview_feed(workspace(), incoming("Name,USCF ID,FIDE ID\nAda One,12345678,111111\n"))["tournament"]
        with self.assertRaisesRegex(ValueError, "Conflicting membership"):
            preview_feed(current, incoming("Name,USCF ID,FIDE ID\nAda One,12345678,222222\n"))
        source = incoming("Name,R1\nAda One,B\n")
        source["players"][0]["results"].append(deepcopy(source["players"][0]["results"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate records"):
            preview_feed(workspace(), source)

    def test_correction_that_invalidates_cutoff_is_explicit(self):
        current = preview_feed(workspace(), incoming("Rank,Name,R1\n1,Ada One,W2w\n2,Ben Two,L1b\n"))["tournament"]
        correction = incoming("Rank,Name,R1\n1,Ada One,UNPLAYED\n2,Ben Two,UNPLAYED\n")
        correction["players"][0]["results"][0]["opponentId"] = "99"
        proposal = preview_feed(current, correction)
        self.assertEqual(proposal["tournament"]["completedRounds"], 1)
        self.assertTrue(any("committed cutoff" in item["message"] for item in proposal["summary"]["conflicts"]))

    def test_u_is_unspecified_availability_without_future_withdrawal(self):
        proposal = preview_feed(workspace(), incoming("Rank,Name,R1\n1,Ada One,U\n2,Ben Two,B\n"))
        player = proposal["tournament"]["players"][0]
        self.assertTrue(player["results"][0]["availabilityUnknown"])
        self.assertTrue(player["active"])
        self.assertNotIn("withdrawnAfterRound", player)
        self.assertEqual(player.get("scheduledByes", []), [])

    def test_worksheet_rows_and_chess_results_tokens(self):
        event = parse_feed_rows([["Nr", "Player", "Elo", "R1"], ["1", "Ada", "1800", "2w1"], ["2", "Ben", "1700", "1b0"]], section="Open")
        self.assertEqual(event["players"][0]["results"][0]["opponentId"], "2")
        self.assertEqual(event["players"][0]["results"][0]["color"], "W")
        with self.assertRaisesRegex(ValueError, "Unrecognized round"):
            parse_feed_rows([["Name", "R1"], ["Ada", "probably won"]])

    def test_generic_xlsx_uses_real_sparse_columns_without_sample_settings(self):
        data = generic_workbook([("Published U1800", {
            1: {"A": "Rank", "C": "Player", "F": "Rating", "H": "Points", "AA": "R1"},
            2: {"A": 8, "C": "Ada One", "F": 1700, "H": 1, "AA": "W3w"},
            4: {"A": 3, "C": "Ben Two", "F": 1600, "H": 0, "AA": "L8b"},
        })])
        source = parse_bytes(data, "club-results.xlsx")
        self.assertEqual([p["name"] for p in source["players"]], ["Ada One", "Ben Two"])
        self.assertEqual(source["completedRounds"], 1)
        self.assertEqual(source["players"][0]["results"][0]["opponentId"], "3")
        self.assertEqual(source["players"][1]["results"][0]["color"], "B")
        self.assertNotIn("mergeRound", source.get("sourceSettings", {}))
        self.assertNotIn("accelerationConfirmed", source.get("sourceSettings", {}))
        self.assertTrue(all("seed" not in p and "pairingNumber" not in p for p in source["players"]))
        proposal = preview_feed(workspace(), source)
        self.assertEqual(proposal["tournament"]["players"][0]["results"][0]["opponentId"], "p2")
        self.assertEqual(proposal["tournament"]["name"], "City Weekend")
        self.assertEqual(proposal["tournament"]["section"], "U1800")

    def test_generic_xlsx_multiple_recognizable_sections_fail_explicitly(self):
        data = generic_workbook([("Open", {1: {"A": "Name"}, 2: {"A": "Ada"}}),
                                 ("U1600", {1: {"A": "Player"}, 2: {"A": "Ben"}})])
        with self.assertRaisesRegex(ValueError, "Multiple sections"):
            parse_bytes(data, "many-sections.xlsx")

    def test_multi_section_html_requires_explicit_choice(self):
        html = b'<h2>Open</h2><table><tr><th>Name</th></tr><tr><td>Ada</td></tr></table><h2>U1600</h2><table><tr><th>Name</th></tr><tr><td>Ben</td></tr></table>'
        with self.assertRaisesRegex(ValueError, "Choose one section"):
            parse_feed_bytes(html, "results.html")
        selected = parse_feed_bytes(html, "results.html", section="U1600")
        self.assertEqual(selected["players"][0]["name"], "Ben")
        self.assertEqual(selected["section"], "U1600")

    def test_explicit_section_column_cannot_mix_tournament_sections(self):
        csv = b"Section,Rank,Name,R1\nOpen,1,Ada One,W2w\nOpen,2,Ben Two,L1b\nU1600,1,Cam Three,W2w\nU1600,2,Dee Four,L1b\n"
        with self.assertRaisesRegex(ValueError, "Choose one section"):
            parse_feed_bytes(csv, "all-sections.csv")
        selected = parse_feed_bytes(csv, "all-sections.csv", section="u1600")
        self.assertEqual(selected["section"], "u1600")
        self.assertEqual([p["name"] for p in selected["players"]], ["Cam Three", "Dee Four"])
        self.assertEqual(selected["players"][0]["results"][0]["opponentId"], "2")
        with self.assertRaisesRegex(ValueError, "selected section was not found"):
            parse_feed_bytes(csv, "all-sections.csv", section="U1200")

    def test_single_table_heading_mismatch_is_disclosed_without_overriding_explicit_section(self):
        html = b'<h2>Weekend Results</h2><table><tr><th>Section</th><th>Name</th></tr><tr><td>Open</td><td>Ada</td></tr><tr><td>U1600</td><td>Ben</td></tr></table>'
        source = parse_feed_bytes(html, "results.html", section="U1600")
        self.assertEqual(source["players"][0]["name"], "Ben")
        self.assertEqual(source["feed"]["sourceTableLabel"], "Weekend Results")
        self.assertTrue(any("source table is labelled" in warning for warning in source["warnings"]))

    def test_javascript_only_pages_explain_missing_supported_source(self):
        with self.assertRaisesRegex(ValueError, "JavaScript-only"):
            parse_feed_bytes(b'<html><div id="root"></div><script>loadStandings()</script></html>', "page.html")

    def test_live_fetch_uses_public_https_safety_and_short_cache(self):
        with patch("app.feeds.fetch_source", return_value=(b"Name,Rating\nAda,1700\n", "https://organizer.example/table.csv", "text/csv", False)) as fetch:
            response = fetch_feed("https://organizer.example/table.csv")
            fetch.assert_called_once_with("https://organizer.example/table.csv", allow_public=True, max_age=30)
            self.assertEqual(response["tournament"]["players"][0]["name"], "Ada")

    def test_hash_changes_when_reviewed_data_changes(self):
        initial = workspace()
        changed = deepcopy(initial)
        changed["tracking"]["playerName"] = "Ben Two"
        self.assertNotEqual(snapshot_hash(initial), snapshot_hash(changed))


if __name__ == "__main__":
    unittest.main()
