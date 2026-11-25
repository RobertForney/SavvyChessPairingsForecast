"""Validate editable state at the server boundary."""
from __future__ import annotations

import math
from copy import deepcopy
from .pairing import POINTS, PLAYED_RESULTS


def integer(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or not low <= value <= high:
        raise ValueError(f"{label} must be a whole number from {low} to {high}.")
    return int(value)


def number(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be a finite number from {low} to {high}.")
    return float(value)


def validate_tournament(raw):
    if not isinstance(raw, dict):
        raise ValueError("Tournament must be a JSON object.")
    result = deepcopy(raw)
    result["name"] = str(raw.get("name", "Untitled tournament"))[:240]
    result["section"] = str(raw.get("section", "Open"))[:120]
    result["rounds"] = integer(raw.get("rounds", 7), "Tournament rounds", 1, 50)
    result["completedRounds"] = integer(raw.get("completedRounds", 0), "Completed rounds", 0, result["rounds"])
    players = raw.get("players")
    tracking = raw.get("tracking")
    managed = raw.get("managed")
    draft = (isinstance(tracking, dict) and tracking.get("enabled") is True) or (isinstance(managed, dict) and managed.get("enabled") is True)
    if not isinstance(players, list) or (not players and not draft) or len(players) > 200:
        raise ValueError("A section must contain between 1 and 200 players.")
    ids, clean_players, pairing_numbers = set(), [], set()
    for p in players:
        if not isinstance(p, dict):
            raise ValueError("Every player must be an object.")
        identifier = str(p.get("id", "")).strip()
        if not identifier or len(identifier) > 80 or identifier in ids:
            raise ValueError("Every player needs a unique, non-empty pairing ID (at most 80 characters).")
        ids.add(identifier)
        clean = deepcopy(p)
        clean["id"] = identifier
        if p.get("reentryOf") is not None and str(p["reentryOf"]).strip():
            clean["reentryOf"] = str(p["reentryOf"]).strip()
        else:
            clean.pop("reentryOf", None)
        clean["name"] = str(p.get("name", "")).strip()[:160]
        if not clean["name"]:
            raise ValueError(f"Player {identifier} needs a name.")
        clean["rating"] = integer(p.get("rating", 0), f"{clean['name']} rating", 0, 4000)
        for field in ("pairingNumber", "seed"):
            if p.get(field) is not None:
                clean[field] = integer(p[field], field, 1, 9999)
            else:
                clean.pop(field, None)
        order_number = clean.get("pairingNumber", clean.get("seed"))
        if order_number is not None:
            if order_number in pairing_numbers:
                raise ValueError("Definitive pairing numbers/seeds must be unique within the section.")
            pairing_numbers.add(order_number)
        if p.get("pairingRating") is not None:
            clean["pairingRating"] = integer(p["pairingRating"], "Assigned pairing rating", 0, 4000)
        else:
            clean.pop("pairingRating", None)
        if not isinstance(p.get("active", True), bool):
            raise ValueError("Active must be true or false.")
        clean["active"] = p.get("active", True)
        if "prizeContender" in p and not isinstance(p["prizeContender"], bool):
            raise ValueError("Prize contender must be true or false.")
        if "entryRound" in p:
            clean["entryRound"] = integer(p["entryRound"], "Entry round", 1, result["rounds"] + 1)
        if "withdrawnAfterRound" in p:
            clean["withdrawnAfterRound"] = integer(p["withdrawnAfterRound"], "Withdrawal round", 0, result["rounds"])
        for field in ("initialScore", "reportedScore"):
            if p.get(field) is not None:
                clean[field] = number(p[field], field, 0, 100)
            else:
                clean.pop(field, None)
        if clean.get("entryRound", 1) > clean.get("withdrawnAfterRound", result["rounds"]) + 1:
            raise ValueError("A player's entry round cannot be after their withdrawal boundary.")
        virtual = p.get("virtualPoints", [])
        if not isinstance(virtual, list) or len(virtual) > result["rounds"]:
            raise ValueError("Virtual pairing points must have at most one entry per round.")
        virtual_rounds = set()
        clean["virtualPoints"] = []
        for entry in virtual:
            if not isinstance(entry, dict):
                raise ValueError("Each virtual-points entry must be an object.")
            rnd = integer(entry.get("round"), "Virtual-points round", 1, result["rounds"])
            points = number(entry.get("points"), "Virtual pairing points", 0, 50)
            if rnd in virtual_rounds or not (points * 2).is_integer():
                raise ValueError("Virtual pairing points need unique rounds and half-point increments.")
            virtual_rounds.add(rnd)
            clean["virtualPoints"].append({"round": rnd, "points": points})
        results = p.get("results", [])
        if not isinstance(results, list) or len(results) > 100:
            raise ValueError("Player results must be a list of at most 100 round records.")
        clean["results"] = []
        scheduled = p.get("scheduledByes", [])
        if not isinstance(scheduled, list) or len(scheduled) > result["rounds"]:
            raise ValueError("Scheduled byes must be a list with at most one entry per round.")
        clean["scheduledByes"] = []
        scheduled_rounds = set()
        for scheduled_bye in scheduled:
            if not isinstance(scheduled_bye, dict):
                raise ValueError("Each scheduled bye must be an object.")
            rnd = integer(scheduled_bye.get("round"), "Scheduled bye round", 1, result["rounds"])
            points = number(scheduled_bye.get("points", .5), "Scheduled bye points", 0, 1)
            if points not in {0, .5, 1} or rnd in scheduled_rounds:
                raise ValueError("Use one scheduled bye per round with 0, 0.5 or 1 points.")
            scheduled_rounds.add(rnd)
            clean["scheduledByes"].append({"round": rnd, "points": points})
        for record in results:
            if not isinstance(record, dict):
                raise ValueError("Each result must be an object.")
            r = deepcopy(record)
            r["round"] = integer(r.get("round"), "Result round", 1, result["rounds"])
            if r.get("result") not in POINTS:
                raise ValueError(f"Unknown result code: {r.get('result')}. Use {', '.join(POINTS)}.")
            if r.get("color") not in {None, "W", "B"}:
                raise ValueError("Played color must be W, B or null.")
            r["color"] = r.get("color")
            r["opponentId"] = str(r["opponentId"]) if r.get("opponentId") is not None else None
            if "played" in r and not isinstance(r["played"], bool):
                raise ValueError("Played must be true or false.")
            r["played"] = r.get("played", r["result"] in PLAYED_RESULTS)
            if "points" in r:
                r["points"] = number(r["points"], "Result points", 0, 1)
                if r["result"] != "BYE" and abs(r["points"] - POINTS[r["result"]]) > .0001:
                    raise ValueError("Custom result points are supported only for a pairing-allocated bye.")
            clean["results"].append(r)
        clean_players.append(clean)
    result["players"] = clean_players
    by_id = {p["id"]: p for p in clean_players}
    for player in clean_players:
        seen = {player["id"]}
        current = player
        while current.get("reentryOf"):
            parent = current["reentryOf"]
            if parent not in by_id or parent in seen:
                raise ValueError("Reentry links must reference an earlier entry and cannot form a cycle.")
            seen.add(parent)
            current = by_id[parent]
    for field in ("prohibitedPairs", "forcedPairs"):
        pairs = raw.get(field, [])
        if not isinstance(pairs, list) or len(pairs) > 20000:
            raise ValueError(f"{field} must be a list of player pairs.")
        result[field] = []
        seen_pairs, forced_ids = set(), set()
        for pair in pairs:
            oriented = field == "forcedPairs" and isinstance(pair, dict)
            if oriented:
                if "whiteId" not in pair or "blackId" not in pair:
                    raise ValueError("A color-locked pair requires whiteId and blackId.")
                a, b = str(pair["whiteId"]), str(pair["blackId"])
                clean_pair = {"whiteId": a, "blackId": b}
            else:
                if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                    raise ValueError(f"Each {field} entry must contain two player IDs.")
                a, b = map(str, pair)
                clean_pair = [a, b]
            key = frozenset((a, b))
            if a == b or a not in ids or b not in ids or key in seen_pairs:
                raise ValueError(f"{field} contains a self-pair, unknown player or duplicate pair.")
            if field == "forcedPairs" and (a in forced_ids or b in forced_ids):
                raise ValueError("A player cannot be in more than one forced pairing.")
            seen_pairs.add(key)
            forced_ids.update((a, b))
            result[field].append(clean_pair)
    prohibited = {frozenset(pair) for pair in result["prohibitedPairs"]}
    for pair in result["forcedPairs"]:
        values = (pair["whiteId"], pair["blackId"]) if isinstance(pair, dict) else pair
        if frozenset(values) in prohibited:
            raise ValueError("A pairing cannot be both forced and prohibited.")
    warnings = raw.get("warnings", [])
    if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
        raise ValueError("Warnings must be a list of text messages.")
    result["warnings"] = warnings[:500]
    for field in ("sourceSettings", "scoring"):
        if not isinstance(raw.get(field, {}), dict):
            raise ValueError(f"{field} must be an object.")
    provenance = raw.get("provenance", {})
    result["provenance"] = provenance if isinstance(provenance, dict) else {}
    return result
