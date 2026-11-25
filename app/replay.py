"""Replay recorded tournament evidence without leaking later results into pairing.

This is historical replay, not a generator of invented results. Partial and
contradictory source rounds remain partial and contradictory.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import json

from .pairing import (POINTS, PLAYED_RESULTS, audit_history, forecast,
                      normalize_rules)
from .validation import validate_tournament


_TOURNAMENT_INPUTS = ("name", "section", "rounds", "scoring", "acceleration", "pairingSystem")
_PLAYER_INPUTS = ("id", "name", "rating", "pairingNumber", "seed", "pairingRating",
                  "schedulePairingNumber", "fideRating", "usChessId", "fideId", "schedule", "team", "initialScore",
                  "entryRound", "withdrawnAfterRound", "reentryOf", "hasPriorRatedGames", "lateEntry")
_SETUP_SETTINGS = ("pairingSystem", "win", "draw", "loss", "acceleration", "accelerationConfirmed", "mergeRound", "schedulePolicy")


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _id_key(identifier):
    identifier = str(identifier)
    return (0, int(identifier)) if identifier.isdecimal() else (1, identifier)


def _participates(player, rnd, total):
    return player.get("entryRound", 1) <= rnd <= player.get("withdrawnAfterRound", total)


def _cutoff_view(source, through):
    """Whitelist pairing inputs; source snapshot metadata stays outside the view."""
    view = {key: deepcopy(source[key]) for key in _TOURNAMENT_INPUTS if key in source}
    view["completedRounds"] = through
    view["latestRound"] = max((r["round"] for p in source["players"] for r in p.get("results", [])
                               if r["round"] <= through), default=0)
    view["sourceSettings"] = {key: deepcopy(source.get("sourceSettings", {})[key]) for key in _SETUP_SETTINGS
                              if key in source.get("sourceSettings", {})}
    view["players"] = []
    undated_inactive = []
    for original in source["players"]:
        p = {key: deepcopy(original[key]) for key in _PLAYER_INPUTS if key in original}
        p["results"] = [deepcopy(r) for r in original.get("results", []) if r["round"] <= through]
        p["scheduledByes"] = deepcopy(original.get("scheduledByes", []))
        # The next round's virtual points are declared configuration, not future
        # results. Later unused entries cannot influence this replay fingerprint.
        p["virtualPoints"] = [deepcopy(v) for v in original.get("virtualPoints", []) if v["round"] <= through + 1]
        p["active"] = _participates(original, through + 1, source["rounds"])
        if not original.get("active", True) and "withdrawnAfterRound" not in original:
            undated_inactive.append(original["id"])
        if through + 1 == source["rounds"] and "prizeContender" in original:
            p["prizeContender"] = original["prizeContender"]
        view["players"].append(p)
    view["warnings"] = ["Historical replay excludes later games and source snapshot totals from the forecast input.",
                        "Historical availability follows declared participation boundaries; undated snapshot inactive flags are not used."]
    assumptions = ["Declared starting ratings, entry/withdrawal rounds, and requested byes are treated as known event setup. Their announcement times are not supplied by this source."]
    if undated_inactive:
        note = "Undated source inactive flags are not used as historical withdrawals; add withdrawnAfterRound to model when those players left."
        assumptions.append(note)
    if source.get("replayKnownRestrictions") is True:
        for key in ("prohibitedPairs", "forcedPairs"):
            view[key] = deepcopy(source.get(key, []))
        assumptions.append("Director restrictions are explicitly declared known for this replay.")
    else:
        view["prohibitedPairs"], view["forcedPairs"] = [], []
        if source.get("prohibitedPairs") or source.get("forcedPairs"):
            assumptions.append("Undated next-round director locks are excluded. Set replayKnownRestrictions=true only when they were known at the simulated cutoff.")
    provenance = source.get("provenance", {})
    view["provenance"] = {"source": provenance.get("source", "Reviewed tournament source"),
                          "format": provenance.get("format", "Normalized tournament"),
                          "replay": True, "throughRound": through}
    return validate_tournament(view), assumptions


def _round_integrity(source, rnd, rules):
    """Use the existing history audit on a single remapped round of evidence."""
    probe_players = []
    missing, unresolved, unknown_colors, duplicate = [], [], [], []
    evidence_players = 0
    for original in source["players"]:
        records = [r for r in original.get("results", []) if r["round"] == rnd]
        expected = _participates(original, rnd, source["rounds"])
        p = {"id": original["id"], "name": original["name"], "rating": original["rating"],
             "active": False, "entryRound": 1 if expected else 2, "withdrawnAfterRound": 1 if expected else 2,
             "results": [{**deepcopy(r), "round": 1} for r in records],
             "scheduledByes": [{"round": 1, "points": b["points"]} for b in original.get("scheduledByes", []) if b["round"] == rnd]}
        for key in ("seed", "pairingNumber", "pairingRating"):
            if key in original:
                p[key] = original[key]
        probe_players.append(p)
        if records:
            evidence_players += 1
        if expected and not records:
            missing.append(original["id"])
        if len(records) > 1:
            duplicate.append(original["id"])
        if any(r["result"] == "UNPLAYED" for r in records):
            unresolved.append(original["id"])
        if any(r["result"] in PLAYED_RESULTS and r.get("played", True) and not r.get("color") for r in records):
            unknown_colors.append(original["id"])
    probe = {"name": "Round integrity", "section": source.get("section", "Open"), "rounds": 2,
             "completedRounds": 1, "players": probe_players, "warnings": []}
    # Coverage describes the recorded round, independently of whether a chosen
    # future procedure also needs a complete definitive starting-number field.
    issues, _ = audit_history(probe, {**rules, "pairingOrder": "rating"})
    issues = [issue.replace("round 1", f"round {rnd}") for issue in issues]
    complete = not issues
    return {"round": rnd, "complete": complete,
            "status": "complete" if complete else "incomplete" if evidence_players else "not-entered",
            "issues": issues, "recordedPlayers": evidence_players,
            "expectedPlayers": sum(_participates(p, rnd, source["rounds"]) for p in source["players"]),
            "missingPlayers": missing, "unresolvedPlayers": unresolved,
            "unknownColorPlayers": unknown_colors, "duplicatePlayers": duplicate,
            "provisional": any(r["result"] == "ADJOURNED" for p in source["players"] for r in p.get("results", []) if r["round"] == rnd)}


def _compare(source, prediction, target_round):
    result = {"targetRound": target_round, "status": "finished" if target_round > source["rounds"] else "unavailable",
              "observedBoards": 0, "comparableBoards": 0, "opponentMatches": 0, "opponentCompared": 0, "opponentRate": None,
              "colorMatches": 0, "colorCompared": 0, "colorRate": None, "colorScope": "matched-opponent boards with known colors",
              "playerColorMatches": 0, "playerColorCompared": 0, "playerColorRate": None,
              "byeMatches": 0, "byeCompared": 0, "byeRate": None,
              "missingColorBoards": 0, "conflictingColorBoards": 0, "conflictingBoards": 0, "nonReciprocalBoards": 0, "invalidAssignmentBoards": 0, "outsideParticipationBoards": 0,
              "unobservedPlayers": [], "missingOpponentPlayers": [], "unknownOpponentPlayers": [], "selfPairedPlayers": [], "availabilityUnknownPlayers": [],
              "boards": [], "observedByes": [],
              "notes": ["Rates use only the first forecast candidate, never the best-fitting alternative selected after seeing the actual round.",
                        "Opponent rates exclude contradictory/nonreciprocal observed boards. Color rates are conditional on a matched opponent board with known colors.",
                        "Bye agreement compares the recorded player and awarded points; zero records outside declared participation are not graded as pairing byes."]}
    if target_round > source["rounds"]:
        return result
    by_id = {p["id"]: p for p in source["players"]}
    histories = {p["id"]: [r for r in p.get("results", []) if r["round"] == target_round] for p in source["players"]}
    observed = defaultdict(lambda: defaultdict(list))
    for identifier, records in histories.items():
        has_assignment = False
        for record in records:
            if record.get("availabilityUnknown"):
                result["availabilityUnknownPlayers"].append(identifier)
                continue
            opponent = record.get("opponentId")
            if opponent in by_id and opponent != identifier:
                observed[frozenset((identifier, opponent))][identifier].append(record)
                has_assignment = True
            elif not opponent and record["result"] in {"BYE", "HBYE", "ZERO"}:
                has_assignment = True
            elif opponent == identifier:
                result["selfPairedPlayers"].append(identifier)
            elif opponent:
                result["unknownOpponentPlayers"].append(identifier)
            else:
                result["missingOpponentPlayers"].append(identifier)
        if not has_assignment and _participates(by_id[identifier], target_round, source["rounds"]):
            result["unobservedPlayers"].append(identifier)
    for field in ("missingOpponentPlayers", "unknownOpponentPlayers", "selfPairedPlayers", "availabilityUnknownPlayers"):
        result[field] = list(dict.fromkeys(result[field]))
    predicted_pairs, predicted_colors = {}, {}
    for pair in prediction.get("pairings", []):
        white, black = pair["white"]["id"], pair["black"]["id"]
        predicted_pairs[frozenset((white, black))] = (white, black)
        predicted_colors[white], predicted_colors[black] = "W", "B"
    available = prediction["status"] == "ok"
    for key, evidence in sorted(observed.items(), key=lambda item: tuple(sorted((_id_key(i) for i in item[0])))):
        a, b = sorted(key, key=_id_key)
        reciprocal = len(evidence.get(a, [])) == len(evidence.get(b, [])) == 1
        conflict = len(histories[a]) != 1 or len(histories[b]) != 1
        invalid_assignment = any(record["result"] in {"BYE", "HBYE", "ZERO"} for records in evidence.values() for record in records)
        outside_participation = not all(_participates(by_id[i], target_round, source["rounds"]) for i in key)
        comparable = reciprocal and not conflict and not invalid_assignment and not outside_participation
        ca = evidence[a][0].get("color") if evidence.get(a) else None
        cb = evidence[b][0].get("color") if evidence.get(b) else None
        known_colors = ca in {"W", "B"} and cb in {"W", "B"} and ca != cb
        predicted = predicted_pairs.get(key)
        matched = bool(available and comparable and predicted)
        color_match = matched and known_colors and predicted == ((a, b) if ca == "W" else (b, a))
        row = {"player1": {"id": a, "name": by_id[a]["name"]}, "player2": {"id": b, "name": by_id[b]["name"]},
               "whiteId": a if ca == "W" else b if cb == "W" else None,
               "blackId": a if ca == "B" else b if cb == "B" else None,
               "reciprocal": reciprocal, "conflicted": conflict, "comparable": comparable, "colorsKnown": known_colors, "invalidAssignment": invalid_assignment, "outsideParticipation": outside_participation,
               "opponentMatch": matched if available and comparable else None,
               "colorMatch": color_match if matched and known_colors else None,
               "status": "excluded" if not comparable else "not-forecast" if not available else "match" if matched else "different"}
        result["boards"].append(row)
        result["observedBoards"] += 1
        result["comparableBoards"] += int(comparable)
        result["conflictingBoards"] += int(conflict)
        result["nonReciprocalBoards"] += int(not reciprocal)
        result["invalidAssignmentBoards"] += int(invalid_assignment)
        result["outsideParticipationBoards"] += int(outside_participation)
        result["conflictingColorBoards"] += int(ca in {"W", "B"} and cb == ca)
        result["missingColorBoards"] += int(comparable and not known_colors)
        if available and comparable:
            result["opponentCompared"] += 1
            result["opponentMatches"] += int(matched)
            if matched and known_colors:
                result["colorCompared"] += 1
                result["colorMatches"] += int(color_match)
            if known_colors:
                for identifier, color in ((a, ca), (b, cb)):
                    if identifier in predicted_colors:
                        result["playerColorCompared"] += 1
                        result["playerColorMatches"] += int(predicted_colors[identifier] == color)
    predicted_byes = {b["player"]["id"]: b["points"] for b in prediction.get("byes", [])}
    if prediction.get("bye"):
        predicted_byes[prediction["bye"]["id"]] = prediction.get("byePoints", 1)
    predicted_byes.update({b["player"]["id"]: b["points"] for b in prediction.get("requestedByes", [])})
    for identifier, records in histories.items():
        if len(records) == 1 and records[0]["result"] in {"BYE", "HBYE", "ZERO"} and not records[0].get("opponentId"):
            record = records[0]
            points = float(record.get("points", POINTS[record["result"]]))
            matched = identifier in predicted_byes and predicted_byes[identifier] == points
            unknown_availability = bool(record.get("availabilityUnknown"))
            comparable = _participates(by_id[identifier], target_round, source["rounds"]) and not unknown_availability
            result["observedByes"].append({"playerId": identifier, "name": by_id[identifier]["name"], "result": record["result"],
                                           "sourceCode": record.get("sourceCode"), "availabilityUnknown": unknown_availability,
                                           "evidenceStatus": "availability-unknown" if unknown_availability else "recorded",
                                           "points": points, "comparable": comparable, "match": matched if available and comparable else None})
            if available and comparable:
                result["byeCompared"] += 1
                result["byeMatches"] += int(matched)
    for prefix in ("opponent", "color", "playerColor", "bye"):
        denominator = result[prefix + "Compared"]
        result[prefix + "Rate"] = result[prefix + "Matches"] / denominator if denominator else None
    result["status"] = "compared" if available and (result["opponentCompared"] or result["byeCompared"]) else "forecast-blocked" if not available else "no-observed-pairings"
    if result["unobservedPlayers"]:
        result["notes"].append("Some next-round players lack a usable opponent or bye assignment; their opponents are not inferred.")
    if result["missingOpponentPlayers"]:
        result["notes"].append("Some next-round game/result records do not identify an opponent; they cannot establish observed boards.")
    if result["unknownOpponentPlayers"]:
        result["notes"].append("Some next-round records name opponents missing from this section; those assignments cannot be compared.")
    if result["selfPairedPlayers"]:
        result["notes"].append("Self-pairing records are invalid and cannot be compared with a forecast board.")
    if result["availabilityUnknownPlayers"]:
        result["notes"].append("Some non-playing source records have unspecified availability or assignment (such as US Chess U). Their recorded zero scores do not establish a pairing bye or a withdrawal and are excluded from bye agreement.")
    if result["conflictingBoards"]:
        result["notes"].append("Conflicting next-round evidence is shown but excluded from accuracy denominators.")
    if result["invalidAssignmentBoards"]:
        result["notes"].append("A source bye/absence record names an opponent; that invalid assignment is excluded from comparison.")
    if result["outsideParticipationBoards"]:
        result["notes"].append("Played pairings outside a declared entry/withdrawal interval contradict the participation setup and are excluded from comparison.")
    if not available:
        result["notes"].append("No accuracy percentage is assigned when the selected cutoff cannot produce a valid forecast.")
    return result


def replay(tournament, through_round, rules=None):
    """Return a non-mutating cutoff view, its next forecast and observed comparison."""
    source = validate_tournament(tournament)
    if isinstance(through_round, bool) or not isinstance(through_round, int) or not 0 <= through_round <= source["rounds"]:
        raise ValueError(f"Replay cutoff must be a whole round from 0 through {source['rounds']}.")
    normalized_rules = normalize_rules(rules)
    view, assumptions = _cutoff_view(source, through_round)
    prediction = forecast(view, normalized_rules)
    coverage = [_round_integrity(source, rnd, normalized_rules) for rnd in range(1, source["rounds"] + 1)]
    selected = deepcopy(coverage[through_round - 1]) if through_round else {
        "round": 0, "complete": True, "status": "before-first-round", "issues": [], "recordedPlayers": 0,
        "expectedPlayers": len(source["players"]), "missingPlayers": [], "unresolvedPlayers": [], "unknownColorPlayers": [], "duplicatePlayers": [], "provisional": False}
    selected["historyComplete"] = all(r["complete"] for r in coverage[:through_round])
    selected["forecastReady"] = prediction["status"] == "ok"
    last_complete = 0
    for status in coverage:
        if not status["complete"]:
            break
        last_complete = status["round"]
    latest = max((r["round"] for p in source["players"] for r in p.get("results", [])), default=0)
    summary = {"name": source["name"], "section": source["section"], "players": len(source["players"]), "rounds": source["rounds"],
               "latestEvidenceRound": latest, "lastCompleteRound": last_complete,
               "reportedTotals": [{"playerId": p["id"], "name": p["name"], "points": p["reportedScore"], "scope": "source snapshot, excluded from replay standings"}
                                  for p in source["players"] if p.get("reportedScore") is not None],
               "warnings": deepcopy(source.get("warnings", [])), "provenance": deepcopy(source.get("provenance", {})),
               "sourceHash": _digest(source), "assumptions": assumptions,
               "partialRounds": [r["round"] for r in coverage if r["round"] <= latest and not r["complete"]]}
    return {"throughRound": through_round, "targetRound": through_round + 1, "tournament": view,
            "forecast": prediction, "roundStatus": selected, "coverage": coverage,
            "comparison": _compare(source, prediction, through_round + 1), "sourceSummary": summary}
