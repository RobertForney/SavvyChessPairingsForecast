"""Read-only access to the original tournament workbook and its evidence map.

The sample is deliberately incomplete. Metadata describes the evidence that is
present; it does not turn missing rows into results, withdrawals or colors.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
from pathlib import Path
import threading
from xml.etree import ElementTree as ET
from zipfile import ZipFile
from io import BytesIO

from .importers import parse_bytes
from .pairing import PLAYED_RESULTS
from .validation import validate_tournament

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FILENAME = "Louisiana_State_Championship_2026_pairings_no_rematches.xlsx"
SAMPLE_PATH = ROOT / SAMPLE_FILENAME
SOURCE_KIND = "original-xlsx"
_cache_lock = threading.RLock()
_cache_key = None
_cache_tournament = None
_cache_metadata = None


def _sort_ids(identifiers):
    return sorted(identifiers, key=lambda value: (0, int(value)) if value.isdecimal() else (1, value))


def _sheet_names(data):
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with ZipFile(BytesIO(data)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    return [sheet.attrib["name"] for sheet in workbook.findall(f"{namespace}sheets/{namespace}sheet")]


def _evidence_metadata(tournament, data, digest):
    players = tournament["players"]
    by_id = {player["id"]: player for player in players}
    schedules = Counter(player.get("schedule") or "Unspecified" for player in players)
    scheduled = [
        {"playerId": player["id"], "name": player["name"],
         "schedule": player.get("schedule"), "round": bye["round"], "points": bye["points"]}
        for player in players for bye in player.get("scheduledByes", [])
    ]
    conflicts = []
    rounds = []
    all_source_rows = set()
    for rnd in range(1, tournament["rounds"] + 1):
        records = [(player, record) for player in players for record in player["results"] if record["round"] == rnd]
        per_player = defaultdict(list)
        games = defaultdict(list)
        source_rows = set()
        for player, record in records:
            identifier = player["id"]
            per_player[identifier].append(record)
            if record.get("sourceRow") is not None:
                source_rows.add(record["sourceRow"])
                all_source_rows.add(record["sourceRow"])
            if record.get("opponentId"):
                key = ("source", record["sourceRow"]) if record.get("sourceRow") is not None else (
                    "pair", tuple(sorted((identifier, record["opponentId"]))))
                games[key].append((player, record))
        required = {
            player["id"] for player in players
            if player.get("entryRound", 1) <= rnd <= player.get("withdrawnAfterRound", tournament["rounds"])
        }
        duplicates = []
        for identifier in _sort_ids(per_player):
            if len(per_player[identifier]) > 1:
                entry = {"playerId": identifier, "name": by_id[identifier]["name"], "round": rnd,
                         "recordCount": len(per_player[identifier]),
                         "results": [record["result"] for record in per_player[identifier]],
                         "scheduledByePoints": [bye["points"] for bye in by_id[identifier].get("scheduledByes", []) if bye["round"] == rnd]}
                conflicts.append(entry)
                duplicates.append(identifier)
        missing = required - set(per_player)
        unresolved = [identifier for identifier, values in per_player.items() if any(record["result"] == "UNPLAYED" for record in values)]
        unknown_played = sum(record["result"] in PLAYED_RESULTS and record.get("played", True)
                             and record.get("color") not in {"W", "B"} for _, record in records)
        completed_games = sum(len(values) == 2 and all(record["result"] != "UNPLAYED" for _, record in values)
                              for values in games.values())
        unknown_pairings = sum(any((record.get("played", record["result"] in PLAYED_RESULTS) or record["result"] == "UNPLAYED")
                                   and record.get("color") not in {"W", "B"} for _, record in values)
                               for values in games.values())
        unknown_played_games = sum(any(record.get("played", record["result"] in PLAYED_RESULTS)
                                      and record.get("color") not in {"W", "B"} for _, record in values)
                                  for values in games.values())
        schedule_coverage = []
        for schedule in sorted(schedules):
            identifiers = {player["id"] for player in players if (player.get("schedule") or "Unspecified") == schedule and player["id"] in required}
            schedule_coverage.append({"schedule": schedule, "players": len(identifiers),
                                      "playersWithRecords": len(identifiers & set(per_player)),
                                      "missingPlayers": len(identifiers - set(per_player))})
        cross_schedule = sum(len({player.get("schedule") for player, _ in values}) > 1 for values in games.values())
        rounds.append({
            "round": rnd, "hasEvidence": bool(records), "complete": rnd <= tournament["completedRounds"],
            "playerRecords": len(records), "playersExpected": len(required), "playersWithRecords": len(per_player),
            "sourceResultRows": len(source_rows), "pairings": len(games), "completedGames": completed_games,
            "unresolvedGames": sum(any(record["result"] == "UNPLAYED" for _, record in values) for values in games.values()),
            "byeRecords": sum(not record.get("opponentId") and record["result"] in {"BYE", "HBYE", "ZERO"} for _, record in records),
            "missingPlayers": len(missing), "missingPlayerIds": _sort_ids(missing),
            "unresolvedPlayers": len(unresolved), "unresolvedPlayerIds": _sort_ids(unresolved),
            "duplicatePlayers": len(duplicates), "duplicatePlayerIds": duplicates,
            "unknownPlayedColorRecords": unknown_played, "pairingsWithUnknownColors": unknown_pairings,
            "playedGamesWithUnknownColors": unknown_played_games,
            "recordCoverageComplete": not missing and not duplicates and not unresolved,
            "playedColorsComplete": unknown_played == 0,
            "scheduledByes": [bye for bye in scheduled if bye["round"] == rnd],
            "scheduleCoverage": schedule_coverage, "crossSchedulePairings": cross_schedule,
        })
    sheet_names = _sheet_names(data)
    next_round = tournament["completedRounds"] + 1
    return {
        "filename": SAMPLE_FILENAME, "sourceKind": SOURCE_KIND, "sha256": digest, "bytes": len(data),
        "name": tournament["name"], "section": tournament["section"], "players": len(players),
        "rounds": tournament["rounds"], "completedRounds": tournament["completedRounds"], "latestRound": tournament["latestRound"],
        "worksheetCount": len(sheet_names), "worksheetNames": sheet_names,
        "historicalRecords": sum(len(player["results"]) for player in players),
        "sourceResultRows": len(all_source_rows), "scheduledByeDeclarations": len(scheduled),
        "schedules": dict(sorted(schedules.items())), "mergeRound": tournament.get("sourceSettings", {}).get("mergeRound"),
        "roundEvidence": rounds, "conflicts": conflicts,
        "nextRoundScheduledByes": [bye for bye in scheduled if bye["round"] == next_round],
        "pairingsWithUnknownColors": sum(rnd["pairingsWithUnknownColors"] for rnd in rounds),
        "playedGamesWithUnknownColors": sum(rnd["playedGamesWithUnknownColors"] for rnd in rounds),
        "warnings": list(tournament.get("warnings", [])),
        "reportedScoreMeaning": "Cached Standings totals from the source snapshot, not per-round scores.",
        "accelerationConfirmed": tournament.get("sourceSettings", {}).get("accelerationConfirmed", False),
    }


def _snapshot():
    global _cache_key, _cache_tournament, _cache_metadata
    data = SAMPLE_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    key = (str(SAMPLE_PATH.resolve()), digest)
    with _cache_lock:
        if key != _cache_key:
            tournament = validate_tournament(parse_bytes(data, SAMPLE_FILENAME))
            tournament["provenance"].update(sourceKind=SOURCE_KIND, sha256=digest)
            metadata = _evidence_metadata(tournament, data, digest)
            _cache_tournament, _cache_metadata, _cache_key = tournament, metadata, key
        return _cache_tournament, _cache_metadata


def sample_tournament():
    """Return an isolated validated copy of the original, unrepaired evidence."""
    tournament, _ = _snapshot()
    return deepcopy(tournament)


def sample_metadata():
    """Return content-hashed evidence coverage without modifying the workbook."""
    _, metadata = _snapshot()
    return deepcopy(metadata)
