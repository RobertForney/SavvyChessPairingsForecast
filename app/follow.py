"""Player-owned event setup and explicit uncertainty for live forecasting."""
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .pairing import audit_history, forecast, normalize_rules, player_score
from .validation import integer, validate_tournament


def _url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if (len(value) > 3000 or parts.scheme != "https" or not parts.hostname
            or parts.username or parts.password or parts.port not in {None, 443}):
        raise ValueError("Use a public HTTPS standings URL without credentials or a custom port.")
    return value


def update_follow(tournament, body):
    if not isinstance(body, dict):
        raise ValueError("Tournament settings must be an object.")
    result = deepcopy(tournament)
    tracking = dict(result.get("tracking", {}), enabled=True)
    if "name" in body:
        name = str(body["name"]).strip()
        if not name:
            raise ValueError("Enter the tournament name.")
        result["name"] = name[:240]
    if "section" in body:
        result["section"] = str(body.get("section") or "").strip()[:120]
    if "rounds" in body:
        known = body["rounds"] not in {None, ""}
        result["rounds"] = integer(body["rounds"], "Tournament rounds", 1, 50) if known else 50
        tracking["roundsKnown"] = known
    for key in ("playerQuery",):
        if key in body:
            tracking[key] = str(body[key] or "").strip()[:160]
    if "sourceUrl" in body:
        tracking["sourceUrl"] = _url(body["sourceUrl"])
    if "profile" in body:
        if body["profile"] not in {"unknown", "us-chess", "fide-dutch", "other"}:
            raise ValueError("Choose a supported procedure or leave it unknown.")
        tracking["profile"] = body["profile"]
        tracking.pop("assumedProfile", None)
    if "firstRoundColor" in body:
        if body["firstRoundColor"] not in {None, "", "W", "B"}:
            raise ValueError("Initial color must be White, Black, or unknown.")
        tracking["firstRoundColor"] = body["firstRoundColor"] or None
    if "startingOrderKnown" in body:
        if not isinstance(body["startingOrderKnown"], bool):
            raise ValueError("Starting order confirmation must be true or false.")
        tracking["startingOrderKnown"] = body["startingOrderKnown"]
        if body["startingOrderKnown"] and (not result["players"] or any(p.get("pairingNumber", p.get("seed")) is None for p in result["players"])):
            raise ValueError("Import or enter every original pairing number before confirming the starting order. Standings ranks are not pairing numbers.")
    if "acceleration" in body:
        if body["acceleration"] not in {"unknown", "none", "baku", "manual"}:
            raise ValueError("Choose the announced acceleration or leave it unknown.")
        tracking["acceleration"] = body["acceleration"]
        if body["acceleration"] == "baku":
            result["acceleration"] = "baku"
        elif body["acceleration"] in {"none", "manual"}:
            if body["acceleration"] == "none" and any(v.get("points", 0) for p in result["players"] for v in p.get("virtualPoints", [])):
                raise ValueError("Existing manual virtual points conflict with no acceleration. Review them in Players & results first.")
            result.pop("acceleration", None)
    if "focusPlayerId" in body:
        selected = str(body["focusPlayerId"] or "")
        if selected and selected not in {p["id"] for p in result["players"]}:
            raise ValueError("Select yourself from the imported roster.")
        tracking["focusPlayerId"] = selected or None
    settings = deepcopy(result.get("sourceSettings", {}))
    if "schedulePolicy" in body:
        if body["schedulePolicy"] not in {"unknown", "single", "separate", "merge"}:
            raise ValueError("Choose how tournament schedules are paired.")
        if body["schedulePolicy"] == "unknown":
            settings.pop("schedulePolicy", None)
            settings.pop("mergeRound", None)
        else:
            settings["schedulePolicy"] = body["schedulePolicy"]
    if "mergeRound" in body:
        if body["mergeRound"] in {None, ""}:
            settings.pop("mergeRound", None)
        else:
            settings["mergeRound"] = integer(body["mergeRound"], "Schedule merge round", 1, result["rounds"] + 1)
    tracking["schedulePolicy"] = settings.get("schedulePolicy", "merge" if settings.get("mergeRound") else "unknown")
    tracking["mergeRound"] = settings.get("mergeRound")
    tracking.setdefault("profile", "unknown")
    tracking.setdefault("roundsKnown", False)
    tracking.setdefault("startingOrderKnown", False)
    tracking.setdefault("firstRoundColor", None)
    tracking.setdefault("sourceUrl", "")
    tracking.setdefault("playerQuery", "")
    tracking.setdefault("acceleration", "unknown")
    result["tracking"], result["sourceSettings"] = tracking, settings
    return validate_tournament(result)


def new_follow(body):
    if not body.get("name"):
        raise ValueError("Enter the tournament name.")
    return update_follow({"name": "", "section": "", "rounds": 50, "completedRounds": 0,
                          "players": [], "tracking": {"enabled": True, "createdAt": datetime.now(timezone.utc).isoformat()},
                          "warnings": [], "provenance": {"source": "Player-created event", "format": "Live tournament"}}, body)


def follow_status(tournament, rules=None):
    tracking = deepcopy(tournament.get("tracking", {}))
    players = tournament.get("players", [])
    issues, missing = [], []
    if not players:
        issues.append("Add the tournament roster from published standings or an export. Opponents are not needed to start following an event.")
    profile = tracking.get("profile", "unknown")
    if profile == "unknown" and not tracking.get("assumedProfile"):
        issues.append("Choose the announced pairing procedure, or generate a clearly labelled forecast under an assumed procedure.")
    if profile == "other":
        issues.append("This event uses an unsupported pairing format. US Chess Swiss and FIDE Dutch Swiss are currently supported; round robin, knockout, and team systems need separate engines.")
    if profile == "unknown":
        missing.append("Pairing procedure is unconfirmed" + (f"; forecasting assumes {tracking['assumedProfile']}." if tracking.get("assumedProfile") else "."))
    if not tracking.get("roundsKnown"):
        missing.append("Total rounds are unknown; ordinary non-final-round procedures are assumed. Confirm the event length before a final-round forecast or acceleration.")
    if not tracking.get("startingOrderKnown"):
        missing.append("The original pairing order is unconfirmed. Published standings rank is not the original pairing number.")
    if not tracking.get("firstRoundColor"):
        missing.append("The initial color draw is unknown; forecast orientation uses the selected initial-color hypothesis.")
    if tracking.get("acceleration", "unknown") == "unknown":
        missing.append("Acceleration is unconfirmed; this forecast uses the currently configured acceleration/virtual points, or ordinary unaccelerated pairings when none are entered.")
    if not tracking.get("focusPlayerId"):
        missing.append("Select yourself after the roster arrives to see your predicted opponents.")
    current_rules = normalize_rules(rules)
    if profile in {"us-chess", "fide-dutch"} and profile != current_rules["profile"]:
        missing.append(f"Event setup lists {profile}; this forecast uses {current_rules['profile']} as an alternative rule hypothesis.")
    if tracking.get("firstRoundColor") and tracking["firstRoundColor"] != current_rules["firstRoundColor"]:
        missing.append(f"The confirmed initial color is {tracking['firstRoundColor']}; this forecast uses {current_rules['firstRoundColor']} as an alternative color hypothesis.")
    if not tracking.get("roundsKnown") and (tournament.get("acceleration") or tournament.get("scoring")):
        issues.append("Confirm the number of rounds before using automatic acceleration or custom event scoring.")
    if any(p.get("sourceReportedScore", 0) > player_score(p, tournament.get("completedRounds", 0)) for p in players):
        issues.append("Published standings contain points beyond the reviewed game history. Import the round results and opponents; totals alone cannot establish the next pairing.")
    setup_issues = list(issues)
    if players:
        history_issues, _ = audit_history(tournament, current_rules)
        issues.extend(history_issues)
    missing_colors = sum(r.get("played", True) and r.get("result") in {"W", "L", "D", "ADJOURNED"} and not r.get("color")
                         for p in players for r in p.get("results", []) if r["round"] <= tournament.get("completedRounds", 0))
    return {"tracking": tracking, "readiness": {"ready": not issues, "issues": list(dict.fromkeys(issues)),
            "setupIssues": setup_issues,
            "missingDetails": missing, "completedRounds": tournament.get("completedRounds", 0),
            "lastUpdatedAt": tracking.get("lastAppliedAt"), "rosterPlayers": len(players), "missingColors": missing_colors}}


def workspace_forecast(tournament, rules):
    if not tournament.get("tracking", {}).get("enabled"):
        return forecast(tournament, rules)
    status = follow_status(tournament, rules)
    tracking = status["tracking"]
    setup_issues = status["readiness"]["setupIssues"]
    if setup_issues:
        return {"round": tournament.get("completedRounds", 0) + 1, "status": "blocked", "blockingReason": "setup",
                "engine": "Live tournament setup", "issues": setup_issues, "warnings": status["readiness"]["missingDetails"],
                "pairings": [], "candidates": [], "bye": None, "rules": normalize_rules(rules), "stats": {"nodes": 0, "elapsedMs": 0}}
    result = forecast(tournament, rules)
    result["assumptions"] = status["readiness"]["missingDetails"]
    result["warnings"] = list(dict.fromkeys(result.get("warnings", []) + result["assumptions"]))
    result["orientationUnconfirmed"] = tracking.get("firstRoundColor") != normalize_rules(rules)["firstRoundColor"]
    return result
