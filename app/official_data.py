"""Frozen official crosstable evidence with separately attributed workbook setup.

The fixture contains browser-visible text and player links, not page internals.
Final crosstable ranks resolve opponent references; they never become seeds.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from .pairing import POINTS, PLAYED_RESULTS
from .sample_data import SAMPLE_FILENAME, sample_metadata, sample_tournament
from .validation import validate_tournament


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "data" / "us-chess-202609070433.json"
SOURCE_KIND = "official-frozen-snapshot"
_COMPLEMENTS = {"W": "L", "L": "W", "D": "D", "FORFEIT_WIN": "FORFEIT_LOSS", "FORFEIT_LOSS": "FORFEIT_WIN"}
_CODES = {"W": "W", "L": "L", "D": "D", "X": "FORFEIT_WIN", "F": "FORFEIT_LOSS", "H": "HBYE", "B": "BYE", "U": "ZERO"}


def _read_fixture():
    data = FIXTURE_PATH.read_bytes()
    fixture = json.loads(data.decode("utf-8"))
    if fixture.get("schemaVersion") != 1:
        raise ValueError("Unsupported official crosstable fixture version.")
    if fixture.get("columns") != ["#", "Player", "Pts", "R1", "R2", "R3", "R4", "R5", "R6", "R7"]:
        raise ValueError("The official fixture does not contain the expected seven-round table.")
    return fixture, data, hashlib.sha256(data).hexdigest()


def _player(row):
    cells = row["cells"]
    if len(cells) != 10:
        raise ValueError("Every official player row needs ten visible table cells.")
    rank_lines = cells[0].splitlines()
    if not rank_lines or not rank_lines[0].isdigit():
        raise ValueError("The official crosstable row rank is missing.")
    identifiers = {match.group(1) for link in row.get("playerLinks", [])
                   if (match := re.fullmatch(r"/player/(\d{8})", link.get("href", "")))}
    if len(identifiers) != 1:
        raise ValueError("Every official player needs one unambiguous US Chess profile link.")
    identifier = identifiers.pop()
    parts = [part.strip() for part in cells[1].splitlines() if part.strip()]
    if parts.count(identifier) != 1:
        raise ValueError("US Chess profile link and visible member ID disagree.")
    member_index = parts.index(identifier)
    name = " ".join(parts[:member_index])
    pre_rating = None
    if "R" in parts[member_index + 1:]:
        index = parts.index("R", member_index + 1) + 1
        pre_rating = parts[index] if index < len(parts) else None
    if pre_rating not in {None, "----"} and not pre_rating.isdigit():
        raise ValueError(f"Unrecognized pre-event regular rating for {identifier}.")
    return {"id": identifier, "usChessId": identifier, "sourceId": rank_lines[0],
            "sourceRow": row["sourceRow"], "name": name, "usState": rank_lines[1] if len(rank_lines) > 1 else None,
            "rating": int(pre_rating) if pre_rating and pre_rating.isdigit() else 0,
            "ratingKnown": bool(pre_rating and pre_rating.isdigit()), "sourceRatingText": pre_rating,
            "reportedScore": float(cells[2]), "active": True, "results": [], "scheduledByes": []}


def _round_record(text, rnd, ranks, source_row):
    parts = text.split()
    if not parts or parts[0] not in _CODES:
        raise ValueError(f"Unrecognized official result in row {source_row}, round {rnd}: {text!r}.")
    code = parts[0]
    opponent = None
    if code in {"W", "L", "D", "X", "F"}:
        if len(parts) < 2 or parts[1] not in ranks:
            raise ValueError(f"Unknown official opponent in row {source_row}, round {rnd}.")
        opponent = ranks[parts[1]]
    elif len(parts) != 1:
        raise ValueError("A non-playing official result unexpectedly names an opponent.")
    played = code in {"W", "L", "D"}
    if played and (len(parts) != 3 or parts[2].replace("\ufe0f", "") not in {"⚪", "⚫"}):
        raise ValueError("An official played-game token must contain result, opponent rank and one color circle.")
    if code in {"X", "F"} and len(parts) != 2:
        raise ValueError("An official forfeit token must contain result and opponent rank only.")
    white, black = "⚪" in text, "⚫" in text
    if played and white == black:
        raise ValueError(f"Official played game needs one visible color in row {source_row}, round {rnd}.")
    if not played and (white or black):
        raise ValueError("A non-played official result unexpectedly supplies a played color.")
    record = {"round": rnd, "opponentId": opponent, "result": _CODES[code],
              "color": "W" if played and white else "B" if played and black else None,
              "played": played, "points": POINTS[_CODES[code]], "sourceCode": code,
              "sourceRow": source_row, "sourceColumn": rnd + 3, "sourceText": text}
    if code == "U":
        record["availabilityUnknown"] = True
    return record


def _integrity(tournament):
    by_id = {p["id"]: p for p in tournament["players"]}
    rounds = []
    for rnd in range(1, tournament["rounds"] + 1):
        games, played, forfeits = set(), set(), set()
        byes, unspecified = 0, 0
        for player in tournament["players"]:
            records = [r for r in player["results"] if r["round"] == rnd]
            if len(records) != 1:
                raise ValueError(f"Official player {player['id']} does not have exactly one round {rnd} entry.")
            record = records[0]
            opponent = record["opponentId"]
            if not opponent:
                byes += record["result"] in {"BYE", "HBYE"}
                unspecified += record.get("availabilityUnknown", False)
                continue
            if opponent == player["id"] or opponent not in by_id:
                raise ValueError("Invalid official opponent identity.")
            other = next(r for r in by_id[opponent]["results"] if r["round"] == rnd)
            if other["opponentId"] != player["id"] or other["result"] != _COMPLEMENTS[record["result"]]:
                raise ValueError("Official opponent/result reciprocity failed.")
            if record["played"] != other["played"] or record["played"] and record["color"] == other["color"]:
                raise ValueError("Official game colors or played status are not reciprocal.")
            pair = frozenset((player["id"], opponent))
            games.add(pair)
            (played if record["played"] else forfeits).add(pair)
        rounds.append({"round": rnd, "complete": True, "playersExpected": len(by_id), "playersWithRecords": len(by_id),
                       "pairings": len(games), "completedGames": len(played), "forfeitGames": len(forfeits),
                       "byeRecords": byes, "availabilityUnknownRecords": unspecified,
                       "missingPlayers": 0, "unknownPlayedColorRecords": 0})
    totals = [{"playerId": p["id"], "reported": p["reportedScore"],
               "computed": sum(r["points"] for r in p["results"])} for p in tournament["players"]]
    mismatches = [entry for entry in totals if entry["reported"] != entry["computed"]]
    if mismatches:
        raise ValueError(f"Official displayed points disagree with round results: {mismatches}.")
    return {"reciprocal": True, "totalsMatch": True, "totalPoints": sum(p["reportedScore"] for p in tournament["players"]),
            "roundEvidence": rounds, "resultCodes": dict(sorted(Counter(r["sourceCode"] for p in tournament["players"] for r in p["results"]).items())),
            "ratingUnknownPlayers": [p["id"] for p in tournament["players"] if not p["ratingKnown"]]}


def _workbook_comparison(official, workbook):
    by_member = {str(p["usChessId"]): p for p in workbook["players"]}
    workbook_ids = {p["id"]: str(p["usChessId"]) for p in workbook["players"]}
    official_ids = {p["id"] for p in official["players"]}
    ratings, results, colors, added, bye_conflicts, totals = [], [], [], [], [], []
    matching = 0
    for player in official["players"]:
        original = by_member.get(player["id"])
        if not original:
            continue
        identity = {"playerId": player["id"], "name": player["name"], "workbookId": original["id"]}
        if player["rating"] != original["rating"]:
            ratings.append({**identity, "workbook": original["rating"], "officialPreEvent": player["rating"], "pairingRatingUsed": player["pairingRating"]})
        if original.get("reportedScore") != player["reportedScore"]:
            totals.append({**identity, "workbook": original.get("reportedScore"), "official": player["reportedScore"]})
        for record in player["results"]:
            previous = [r for r in original["results"] if r["round"] == record["round"]]
            brief = lambda r, ids=None: {"result": r["result"], "opponentId": ids.get(r.get("opponentId")) if ids else r.get("opponentId"),
                                       "color": r.get("color"), "played": r.get("played", r["result"] in PLAYED_RESULTS), "points": r.get("points", POINTS[r["result"]])}
            current = brief(record)
            prior = [brief(r, workbook_ids) for r in previous]
            detail = {**identity, "round": record["round"], "workbook": prior, "official": current}
            if not prior:
                added.append(detail)
                continue
            comparable = lambda r: {key: value for key, value in r.items() if key != "color"}
            if len(prior) != 1 or comparable(prior[0]) != comparable(current):
                results.append(detail)
            elif prior[0] == current:
                matching += 1
            if any(r["color"] != current["color"] for r in prior):
                colors.append(detail)
        for bye in original.get("scheduledByes", []):
            observed = next(r for r in player["results"] if r["round"] == bye["round"])
            if observed["opponentId"] or observed["points"] != bye["points"]:
                bye_conflicts.append({**identity, "round": bye["round"], "declaredPoints": bye["points"], "officialCode": observed["sourceCode"], "officialPoints": observed["points"]})
    return {"identityMethod": "exact-us-chess-id", "matchedPlayers": len(official_ids & set(by_member)),
            "missingIds": sorted(set(by_member) - official_ids), "extraIds": sorted(official_ids - set(by_member)),
            "ratingsChanged": len(ratings), "resultsChanged": len(results), "colorsChanged": len(colors),
            "officialOnlyRecords": len(added), "matchingRecords": matching, "snapshotTotalsChanged": len(totals),
            "requestedByeConflicts": len(bye_conflicts),
            "details": {"ratings": ratings, "results": results, "colors": colors, "officialOnly": added,
                        "requestedByes": bye_conflicts, "snapshotTotals": totals}}


def _load():
    fixture, raw, digest = _read_fixture()
    workbook = sample_tournament()
    workbook_info = sample_metadata()
    original_by_member = {str(p["usChessId"]): p for p in workbook["players"]}
    players = [_player(row) for row in fixture["rows"]]
    ranks = {p["sourceId"]: p["id"] for p in players}
    if len(ranks) != len(players) or len({p["id"] for p in players}) != len(players):
        raise ValueError("Duplicate official crosstable rank or US Chess ID.")
    if set(original_by_member) != {p["id"] for p in players}:
        raise ValueError("Official roster and workbook setup need an explicit identity review before overlay.")
    for player, row in zip(players, fixture["rows"]):
        original = original_by_member[player["id"]]
        player["pairingNumber"] = original.get("pairingNumber", original.get("seed"))
        player["pairingRating"] = original.get("pairingRating", original["rating"])
        if original.get("schedule"):
            player["schedule"] = original["schedule"]
        player["results"] = [_round_record(text, rnd, ranks, row["sourceRow"]) for rnd, text in enumerate(row["cells"][3:], 1)]
    # List order is also an input: retain the assumed starting order rather than
    # letting final crosstable rank choose even a stable-sort tie or input hash.
    players.sort(key=lambda player: (player["pairingNumber"], player["id"]))
    assumptions = [
        "This is a frozen browser-visible official crosstable snapshot, not a live results connector.",
        "Official crosstable row numbers are final ranks used only to resolve opponent references, never initial pairing numbers.",
        "Initial pairing numbers, schedules, merge round and assigned pairing ratings are overlaid from the original workbook by exact US Chess ID. This setup overlay is an assumption; official pre-event regular ratings remain in rating.",
        "Initial color, pairing variations and acceleration are unconfirmed. The official results cannot establish the director's full pairing setup.",
        "All players remain active. U means unavailable/unspecified with zero points; it does not establish a withdrawal date or a pairing bye.",
        "Observed H, B and U records do not create scheduled byes. The request announcement times are unknown, and conflicting workbook requests are not copied.",
        "Missing pre-event regular ratings remain zero/unknown. Post-event ratings and final points do not enter historical pairing inputs.",
    ]
    settings = {key: deepcopy(workbook.get("sourceSettings", {})[key]) for key in
                ("pairingSystem", "win", "draw", "loss", "mergeRound") if key in workbook.get("sourceSettings", {})}
    settings["accelerationConfirmed"] = False
    tournament = validate_tournament({"name": fixture["name"], "section": fixture["section"], "rounds": 7,
        "completedRounds": 7, "latestRound": 7, "players": players, "warnings": assumptions, "sourceSettings": settings,
        "provenance": {"source": fixture["source"]["url"], "format": "US Chess visible crosstable snapshot", "sourceKind": SOURCE_KIND,
                       "capturedAt": fixture["source"]["capturedAt"], "sha256": digest, "live": False,
                       "setupOverlay": {"source": SAMPLE_FILENAME, "sha256": workbook_info["sha256"], "identityMethod": "exact-us-chess-id"}}})
    integrity = _integrity(tournament)
    comparison = _workbook_comparison(tournament, workbook)
    metadata = {"filename": FIXTURE_PATH.name, "sourceKind": SOURCE_KIND, "sourceUrl": fixture["source"]["url"],
                "capturedAt": fixture["source"]["capturedAt"], "sha256": digest, "bytes": len(raw), "live": False,
                "name": tournament["name"], "section": tournament["section"], "players": len(players), "rounds": 7,
                "completedRounds": 7, "latestRound": 7, "historicalRecords": sum(len(p["results"]) for p in players),
                "sourceResultRows": len(fixture["rows"]), "scheduledByeDeclarations": 0,
                "schedules": dict(Counter(p.get("schedule", "Unspecified") for p in players)), "mergeRound": settings.get("mergeRound"),
                "setupOverlay": deepcopy(tournament["provenance"]["setupOverlay"]), "warnings": assumptions,
                "workbookComparison": comparison, **integrity}
    return tournament, metadata


def official_tournament():
    """Return independent validated official evidence plus attributed workbook setup."""
    return deepcopy(_load()[0])


def official_metadata():
    """Return evidence integrity and exact-USID differences from the original workbook."""
    return deepcopy(_load()[1])
