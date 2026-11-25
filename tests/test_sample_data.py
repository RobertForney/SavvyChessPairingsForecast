"""The browser simulation's source must remain the original workbook."""
from collections import Counter
import hashlib
import unittest

from app.importers import load_tournament
from app.sample_data import SAMPLE_PATH, sample_metadata, sample_tournament
from app.validation import validate_tournament


class OriginalSampleTests(unittest.TestCase):
    def test_metadata_describes_original_file_not_a_derived_csv(self):
        metadata = sample_metadata()
        self.assertEqual(metadata["sourceKind"], "original-xlsx")
        self.assertEqual(metadata["filename"], SAMPLE_PATH.name)
        self.assertEqual(metadata["sha256"], hashlib.sha256(SAMPLE_PATH.read_bytes()).hexdigest())
        self.assertEqual(metadata["bytes"], 698257)
        self.assertEqual(metadata["worksheetCount"], 13)
        self.assertEqual(metadata["players"], 51)
        self.assertEqual((metadata["rounds"], metadata["completedRounds"], metadata["latestRound"]), (7, 1, 6))

    def test_validated_sample_preserves_normalized_history_and_source_ids(self):
        original = validate_tournament(load_tournament(SAMPLE_PATH))
        sample = sample_tournament()
        self.assertEqual(sample["players"], original["players"])
        self.assertEqual(sample["warnings"], original["warnings"])
        self.assertEqual(sample["sourceSettings"], original["sourceSettings"])
        self.assertEqual(sample["provenance"]["sourceKind"], "original-xlsx")
        self.assertEqual(sample["provenance"]["sha256"], sample_metadata()["sha256"])
        self.assertFalse(any(record["round"] == 7 for player in sample["players"] for record in player["results"]))

    def test_first_round_has_complete_known_colors_in_separate_schedules(self):
        metadata = sample_metadata()
        self.assertEqual(metadata["schedules"], {"Open 3-Day": 18, "Open 4-Day": 33})
        self.assertEqual(metadata["mergeRound"], 2)
        first = metadata["roundEvidence"][0]
        self.assertEqual(first["completedGames"], 24)
        self.assertEqual(first["byeRecords"], 3)
        self.assertEqual(first["playerRecords"], 51)
        self.assertTrue(first["recordCoverageComplete"])
        self.assertTrue(first["playedColorsComplete"])
        self.assertEqual(first["crossSchedulePairings"], 0)
        self.assertEqual({bye["playerId"] for bye in first["scheduledByes"]}, {"23", "35", "51"})

    def test_conflicts_and_unknown_colors_are_preserved(self):
        metadata = sample_metadata()
        self.assertEqual(metadata["historicalRecords"], 222)
        self.assertEqual(metadata["sourceResultRows"], 105)
        self.assertEqual(metadata["scheduledByeDeclarations"], 15)
        self.assertEqual({(c["playerId"], c["round"]) for c in metadata["conflicts"]}, {("13", 5), ("25", 2), ("25", 3)})
        self.assertEqual(metadata["pairingsWithUnknownColors"], 55)
        self.assertEqual(metadata["playedGamesWithUnknownColors"], 54)
        second = metadata["roundEvidence"][1]
        self.assertEqual((second["completedGames"], second["unresolvedGames"], second["duplicatePlayerIds"]), (24, 1, ["25"]))
        self.assertEqual([(b["playerId"], b["points"]) for b in metadata["nextRoundScheduledByes"]], [("25", .5)])

    def test_later_missing_rows_are_not_withdrawals_or_zero_point_losses(self):
        evidence = sample_metadata()["roundEvidence"]
        self.assertEqual(evidence[2]["missingPlayers"], 3)
        self.assertEqual([rnd["missingPlayers"] for rnd in evidence[3:6]], [28, 28, 28])
        self.assertFalse(evidence[6]["hasEvidence"])
        self.assertEqual(evidence[6]["missingPlayers"], 51)
        self.assertEqual(len(evidence[6]["scheduledByes"]), 2)
        self.assertTrue(all(player["active"] for player in sample_tournament()["players"]))

    def test_cache_returns_isolated_copies_and_never_authors_the_workbook(self):
        before = SAMPLE_PATH.read_bytes()
        tournament = sample_tournament()
        tournament["players"][0]["results"].clear()
        metadata = sample_metadata()
        metadata["roundEvidence"][0]["missingPlayerIds"].append("invented")
        self.assertTrue(sample_tournament()["players"][0]["results"])
        self.assertNotIn("invented", sample_metadata()["roundEvidence"][0]["missingPlayerIds"])
        self.assertEqual(SAMPLE_PATH.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
