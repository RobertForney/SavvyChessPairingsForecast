"""Version-pinned BBP Pairings adapter for the FIDE Dutch rules of February 2026.

The native engine owns Dutch bracket construction and C1–C21. This module owns
faithful input exchange, input limits, isolated invocation and independent output
checks. It never falls back to an approximate engine.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = Path(__file__).with_name("engine-manifest.json")


def manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def availability():
    result = {"available": False, "name": "BBP Pairings", "version": "6.0.0",
              "ruleEdition": "FIDE Dutch 2026-02-01", "setupCommand": "python scripts/setup_dutch_engine.py"}
    try:
        pinned = manifest()
        executable = ROOT / pinned["executable"]
        result.update({key: pinned[key] for key in ("name", "version", "ruleEdition", "sourceCommit", "releaseUrl", "executableSha256")})
        if os.name != "nt":
            result["reason"] = "The verified native engine package currently targets Windows x64."
        elif not executable.is_file():
            result["reason"] = "Pinned Dutch engine is not installed. Run python scripts/setup_dutch_engine.py."
        elif hashlib.sha256(executable.read_bytes()).hexdigest() != pinned["executableSha256"]:
            result["reason"] = "Dutch executable checksum differs from the pinned official build. Run setup again."
        else:
            result.update(available=True, reason="Official version and executable checksum verified.")
    except (OSError, ValueError, KeyError, TypeError):
        result["reason"] = "Pinned Dutch engine metadata or executable could not be verified. Repair the installation with the setup command."
    return result


def _numbering(tournament):
    """Keep definitive TPN order, including its parity. Never sort by standings."""
    players = tournament["players"]
    values = {p["id"]: p.get("pairingNumber", p.get("seed")) for p in players}
    warning = None
    if any(v is None for v in values.values()):
        if all(str(p["id"]).isdecimal() for p in players) and not any(v is not None for v in values.values()):
            values = {p["id"]: int(p["id"]) for p in players}
            warning = "Using numeric player IDs as tournament pairing numbers. Verify they are the original TPNs, not final standings positions."
        elif tournament["completedRounds"] == 0 and not any(v is not None for v in values.values()):
            from .pairing import starting_order_key
            ordered = sorted(players, key=starting_order_key)
            values = {p["id"]: i for i, p in enumerate(ordered, 1)}
            warning = "Initial TPNs generated from the reviewed pairing-rating order. Confirm all title/name tie ordering before official use."
        else:
            raise ValueError("Exact Dutch mode requires original pairingNumber/seed values for the complete roster, or consecutive numeric pairing IDs.")
    if set(values.values()) != set(range(1, len(players) + 1)):
        raise ValueError("Exact Dutch mode requires the full original roster with consecutive TPNs 1 through N. Include withdrawn entries; TPN gaps cannot be compacted because parity affects color allocation.")
    return values, warning


def _acceleration(tournament):
    value = tournament.get("acceleration")
    return value.get("method") if isinstance(value, dict) else value or "none"


def _round_block(record, numbering, bye_points):
    if record is None:
        return "0000 - Z  "
    result = record["result"]
    opponent = record.get("opponentId")
    color = record.get("color")
    played = record.get("played", result in {"W", "D", "L", "ADJOURNED"})
    if result in {"W", "L"} and not played:
        code = "+" if result == "W" else "-"
        color = None
    elif result == "D" and not played:
        raise ValueError("A draw awarded without play against a named opponent cannot be faithfully represented in this adapter. Review the source outcome before exact Dutch pairing.")
    elif result in {"W", "D", "L", "ADJOURNED"}:
        code = {"W": "1", "D": "=", "L": "0", "ADJOURNED": "="}[result]
    elif result == "FORFEIT_WIN":
        code, color = ("+" if opponent else "F"), None
    elif result == "FORFEIT_LOSS":
        code, color = ("-" if opponent else "Z"), None
    elif result == "BYE":
        awarded = float(record.get("points", 1))
        if awarded != bye_points:
            raise ValueError("All historical pairing-allocated byes must use the configured, constant bye-point award in exact Dutch mode.")
        code, color = "U", None
    elif result == "HBYE":
        code, color = "H", None
    elif result == "ZERO":
        code, color = "Z", None
    else:
        raise ValueError("Exact Dutch mode cannot serialize an unresolved result.")
    number = f"{numbering[opponent]:4d}" if opponent else "0000"
    return f"{number} {(color or '-').lower()} {code}  "


def serialize_tournament(tournament, rules):
    """Return (TRF(bx), internal-number map, eligible IDs, requested byes, notes)."""
    from .pairing import pairing_rating, pairing_score, player_score
    if any(p.get("reentryOf") for p in tournament["players"]):
        raise ValueError("US Chess reentry/reset records are not FIDE Dutch entries. Supply a reviewed FIDE-compliant roster before exact pairing.")
    if tournament.get("forcedPairs"):
        raise ValueError("Exact Dutch mode does not support forced pairs or color locks. Use the explicit forecast mode for director overrides.")
    if any(p.get("initialScore", 0) for p in tournament["players"]):
        raise ValueError("Free starting points are not supported by BBP 6.0.0. Record actual byes/results instead of an initialScore adjustment.")
    baku = _acceleration(tournament) == "baku"
    if baku:
        if isinstance(tournament.get("acceleration"), dict) and set(tournament["acceleration"]) != {"method"}:
            raise ValueError("Automatic Baku uses the native standard method with no custom overrides. Use reviewed manual virtual points for a variation.")
        if any(entry["points"] for p in tournament["players"] for entry in p.get("virtualPoints", [])):
            raise ValueError("Automatic Baku cannot be combined with nonzero manual virtual points.")
    numbering, note = _numbering(tournament)
    completed = tournament["completedRounds"]
    next_round = completed + 1
    by_id = {p["id"]: p for p in tournament["players"]}
    eligible, requested = set(), []
    lines = ["012 Savvy Pairings reviewed tournament", f"XXR {tournament['rounds']}",
             "XXC " + ("white1" if rules["firstRoundColor"] == "W" else "black1"),
             "192 FIDE_DUTCH_2025" + ("_BAKU" if baku else ""), f"BBU {rules['byePoints']:4.1f}"]
    for p in sorted(tournament["players"], key=lambda player: numbering[player["id"]]):
        identifier = p["id"]
        history = {r["round"]: r for r in p.get("results", [])}
        scheduled = next((b for b in p.get("scheduledByes", []) if b["round"] == next_round), None)
        participating = p.get("active", True) and p.get("entryRound", 1) <= next_round <= p.get("withdrawnAfterRound", tournament["rounds"])
        extra = None
        if participating and scheduled:
            requested_player = dict(deepcopy(p), score=player_score(p, completed))
            if not baku:
                requested_player["pairingScore"] = pairing_score(p, next_round)
            else:
                requested_player.pop("pairingScore", None)
            requested.append({"player": requested_player, "points": scheduled["points"]})
            extra = {"result": {0: "ZERO", .5: "HBYE", 1: "FORFEIT_WIN"}[scheduled["points"]], "played": False}
        elif participating:
            eligible.add(identifier)
        else:
            extra = {"result": "ZERO", "played": False}
        # Future requested byes count in the TRF's displayed total, but the engine
        # removes those points before pairing. The canonical actual score stays unchanged.
        shown_score = player_score(p, completed) + (scheduled["points"] if participating and scheduled else 0)
        if abs(shown_score * 10 - round(shown_score * 10)) > 1e-7 or shown_score > 99.9:
            raise ValueError("Exact Dutch exchange requires scores in tenths no greater than 99.9.")
        header = list(" " * 91)
        def put(start, end, value):
            header[start:end] = str(value).ljust(end - start)[:end - start]
        put(0, 3, "001")
        put(4, 8, f"{numbering[identifier]:4d}")
        # Source names are not required by the pairing engine; internal labels
        # prevent line breaks or non-BMP text from corrupting fixed-width fields.
        put(14, 47, f"Player {numbering[identifier]}")
        put(48, 52, f"{pairing_rating(p):4d}")
        put(80, 84, f"{shown_score:4.1f}")
        blocks = [_round_block(history.get(rnd), numbering, rules["byePoints"]) for rnd in range(1, completed + 1)]
        if extra:
            blocks.append(_round_block(extra, numbering, rules["byePoints"]))
        lines.append("".join(header) + "".join(blocks))
        virtual = {entry["round"]: entry["points"] for entry in p.get("virtualPoints", [])}
        if virtual and not baku:
            lines.append(f"XXA {numbering[identifier]:4d} " + " ".join(f"{virtual.get(rnd, 0):4.1f}" for rnd in range(1, next_round + 1)))
    forbidden = {frozenset(pair) for pair in tournament.get("prohibitedPairs", [])}
    if rules.get("teamPolicy") == "forbid":
        active = [by_id[i] for i in eligible]
        for i, p in enumerate(active):
            for other in active[i + 1:]:
                if p.get("team") and p.get("team") == other.get("team"):
                    forbidden.add(frozenset((p["id"], other["id"])))
    for pair in sorted(forbidden, key=lambda pair: sorted(numbering[i] for i in pair)):
        first, second = sorted(numbering[i] for i in pair)
        lines.append(f"XXP {first} {second}")
    notes = [note] if note else []
    if baku:
        notes.append("Standard Baku acceleration is calculated by BBP from original TPNs and tournament length. Actual standings are unchanged; native virtual pairing scores are not inferred or displayed as recorded scores.")
    if rules.get("teamPolicy") == "prefer":
        notes.append("Soft team preferences do not alter the official Dutch algorithm. Use a documented prohibited-pair restriction for mandatory separation.")
    return "\n".join(lines) + "\n", {value: by_id[key] for key, value in numbering.items()}, eligible, requested, notes


def _read_output(text, numbered, eligible, tournament, rules):
    from .pairing import POINTS, PLAYED_RESULTS, _colors, pairing_score, player_score
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if not lines or len(lines[0]) != 1:
        raise ValueError("Dutch engine returned an invalid pairing header.")
    try:
        count = int(lines[0][0])
        if count != len(lines) - 1:
            raise ValueError
        rows = [(int(row[0]), int(row[1])) for row in lines[1:] if len(row) == 2]
        if len(rows) != count:
            raise ValueError
    except (ValueError, IndexError):
        raise ValueError("Dutch engine pairing count or row format is invalid.") from None
    completed, seen, output, bye = tournament["completedRounds"], set(), [], None
    baku = _acceleration(tournament) == "baku"
    prohibited = {frozenset(pair) for pair in tournament.get("prohibitedPairs", [])}
    for white_id, black_id in rows:
        if white_id not in numbered or black_id and black_id not in numbered or white_id == black_id:
            raise ValueError("Dutch engine returned an unknown or self-paired player.")
        ids = [numbered[white_id]["id"]] + ([numbered[black_id]["id"]] if black_id else [])
        if any(identifier in seen or identifier not in eligible for identifier in ids):
            raise ValueError("Dutch engine assigned a player twice or included an unavailable player.")
        seen.update(ids)
        def enriched(number):
            p = numbered[number]
            result = dict(deepcopy(p), score=player_score(p, completed))
            if not baku:
                result["pairingScore"] = pairing_score(p, completed + 1)
            else:
                result.pop("pairingScore", None)
            return result
        white = enriched(white_id)
        if black_id == 0:
            if bye is not None:
                raise ValueError("Dutch engine returned more than one pairing-allocated bye.")
            if any(r["result"] == "BYE" or (not r.get("played", r["result"] in PLAYED_RESULTS) and float(r.get("points", POINTS[r["result"]])) >= 1)
                   for r in white.get("results", []) if r["round"] <= completed):
                raise ValueError("Dutch engine selected an ineligible pairing-allocated bye recipient.")
            bye = white
            continue
        black = enriched(black_id)
        pair = frozenset(ids)
        if pair in prohibited or (rules.get("teamPolicy") == "forbid" and white.get("team") and white.get("team") == black.get("team")):
            raise ValueError("Dutch engine violated a prohibited-pair restriction.")
        if any(r.get("opponentId") == black["id"] and r["result"] in PLAYED_RESULTS and r.get("played", True)
               for r in white.get("results", []) if r["round"] <= completed):
            raise ValueError("Dutch engine returned a repeated played encounter.")
        final = completed + 1 == tournament["rounds"]
        top = final and (white["score"] > completed / 2 or black["score"] > completed / 2)
        for p, color in ((white, "W"), (black, "B")):
            history = _colors(p, completed)
            after = history.count("W") - history.count("B") + (1 if color == "W" else -1)
            third = len(history) >= 2 and history[-2:] == [color, color]
            if (abs(after) > 2 or third) and not top:
                raise ValueError("Dutch engine returned a color-limit violation outside the final-round topscorer exception.")
        explanations = ["FIDE Dutch 2026 ordered C1–C21 criteria (BBP 6.0.0)",
                        "No previous played encounter",
                        f"Played colors: {''.join(_colors(white, completed)) or 'none'} / {''.join(_colors(black, completed)) or 'none'}"]
        if baku:
            explanations.append("Standard Baku virtual points and brackets calculated by the native engine; displayed scores are actual points")
        elif white["pairingScore"] != black["pairingScore"]:
            explanations.append(f"Dutch bracket float: {white['pairingScore']:g} versus {black['pairingScore']:g} pairing points")
        output.append({"board": len(output) + 1, "white": white, "black": black,
                       "scoreGap": abs(white["score"] - black["score"]), "explanations": explanations})
    if seen != eligible:
        raise ValueError("Dutch engine did not assign every eligible player exactly once.")
    if bool(bye) != bool(len(eligible) % 2):
        raise ValueError("Dutch engine bye count does not match field parity.")
    return output, bye


def forecast_dutch(tournament, rules):
    from .pairing import audit_history, normalize_rules
    started = time.perf_counter()
    rules = normalize_rules(rules)
    issues, warnings = audit_history(tournament, rules)
    status = availability()
    result = {"round": tournament["completedRounds"] + 1, "status": "blocked", "blockingReason": "history",
              "pairings": [], "bye": None, "requestedByes": [], "candidates": [], "issues": issues, "warnings": warnings,
              "engine": "BBP Pairings 6.0.0 · FIDE Dutch", "rules": rules, "ruleEdition": status["ruleEdition"],
              "scope": "Native FIDE Dutch 2026 pairing calculation using the pinned BBP 6.0.0 build; no certification or agreement with unrecorded tournament settings is implied.",
              "stats": {"nodes": 0, "elapsedMs": 0, "exhaustive": False, "activePlayers": 0, "scoreGroups": 0},
              "objective": None, "objectiveLabels": [], "engineDetails": status}
    try:
        if issues:
            return result
        if not status["available"]:
            issues.append(status["reason"])
            result["blockingReason"] = "engine"
            return result
        pinned = manifest()
        trf, numbered, eligible, requested, notes = serialize_tournament(tournament, rules)
        result["requestedByes"] = requested
        warnings.extend(notes)
        from .pairing import pairing_score
        from .pairing import player_score
        baku = _acceleration(tournament) == "baku"
        result["stats"].update(activePlayers=len(eligible), scoreGroups=len({(player_score(p, tournament['completedRounds']) if baku else pairing_score(p, tournament['completedRounds'] + 1)) for p in numbered.values() if p['id'] in eligible}))
        result["engineDetails"]["acceleration"] = "baku" if baku else "manual" if any(p.get("virtualPoints") for p in tournament["players"]) else "none"
        warnings.append("Native Dutch mode uses official color, float and bye criteria; heuristic US Chess switches, repeat-bye overrides and custom color limits do not alter it.")
        if not eligible:
            if not requested:
                raise ValueError("No eligible players or requested byes are available for the next round.")
            pairings, bye = [], None
            diagnostic = "All participating players have reviewed requested byes."
        else:
            with tempfile.TemporaryDirectory(prefix="savvy-dutch-") as directory:
                source, target = Path(directory) / "input.trf", Path(directory) / "pairings.txt"
                source.write_text(trf, encoding="utf-8", newline="\n")
                command = [str(ROOT / pinned["executable"]), "--dutch", str(source), "-p", str(target)]
                completed = subprocess.run(command, cwd=directory, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                           timeout=max(5, rules.get("searchMillis", 1200) / 1000 + 2),
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, shell=False)
                diagnostic = (completed.stderr or completed.stdout).strip()[:3000]
                if completed.returncode:
                    result["blockingReason"] = "constraints" if completed.returncode == 1 else "engine"
                    issues.append("No complete Dutch pairing exists for these inputs." if completed.returncode == 1 else f"Dutch engine rejected its input (code {completed.returncode}): {diagnostic}")
                    return result
                if not target.is_file() or target.stat().st_size > 100000:
                    raise ValueError("Dutch engine did not produce a bounded pairing output.")
                pairings, bye = _read_output(target.read_text(encoding="utf-8"), numbered, eligible, tournament, rules)
        candidate = {"id": "1", "label": "Dutch engine result", "pairings": pairings, "bye": bye, "byePoints": rules["byePoints"], "objective": None}
        result.update(status="ok", blockingReason=None, pairings=pairings, bye=bye, byePoints=rules["byePoints"], candidates=[candidate])
        result["stats"]["exhaustive"] = True
        result["engineDetails"].update(inputSha256=hashlib.sha256(trf.encode()).hexdigest(), diagnostic=diagnostic)
    except subprocess.TimeoutExpired:
        result["blockingReason"] = "search-limit"
        issues.append("The native Dutch engine exceeded its execution time limit. No approximate fallback was substituted.")
    except (ValueError, OSError) as exc:
        issues.append(str(exc))
        result["blockingReason"] = "engine"
    finally:
        result["stats"]["elapsedMs"] = round((time.perf_counter() - started) * 1000)
    return result
