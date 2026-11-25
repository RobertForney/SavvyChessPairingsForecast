"""Read tournament evidence without recalculating spreadsheet forecasts.

Only the standard library is needed. Parsers retain missing colors, incomplete
results, reported scores, and contradictory records for downstream review.
"""
from __future__ import annotations

import csv
import io
import json
import math
import posixpath
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET


class ImportError(ValueError):
    """The source is unsupported or cannot be interpreted safely."""


RESULTS = {"W", "D", "L", "BYE", "HBYE", "ZERO", "UNPLAYED", "FORFEIT_WIN", "FORFEIT_LOSS", "ADJOURNED"}
POINTS = {"W": 1, "D": .5, "L": 0, "BYE": 1, "HBYE": .5, "ZERO": 0,
          "FORFEIT_WIN": 1, "FORFEIT_LOSS": 0, "UNPLAYED": 0, "ADJOURNED": .5}
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _number(value: Any, default: float = 0) -> float:
    try:
        value = float(value)
        if math.isfinite(value):
            return value
    except (TypeError, ValueError):
        pass
    return default


def _identifier(value: Any) -> str:
    value = str(value or "").strip()
    try:
        number = Decimal(value)
        if number.is_finite() and number == number.to_integral_value():
            return str(int(number))
    except InvalidOperation:
        pass
    return value


def _integer(value: Any, label: str, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ImportError(f"{label} must be a whole number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ImportError(f"{label} must be a whole number.") from exc
    if not math.isfinite(number) or number != int(number):
        raise ImportError(f"{label} must be a finite whole number.")
    return int(number)


def _finite_number(value: Any, label: str, default: float = 0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise ImportError(f"{label} must be numeric.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ImportError(f"{label} must be numeric.") from exc
    if not math.isfinite(number):
        raise ImportError(f"{label} must be finite.")
    return number


def _entry(round_: int, opponent: str | None, result: str,
           color: str | None = None, **extras: Any) -> dict:
    return {"round": round_, "opponentId": opponent, "color": color,
            "result": result, "played": result in {"W", "D", "L", "ADJOURNED"}, **extras}


def normalize_tournament(source: dict, filename: str = "tournament.json", format_: str = "JSON") -> dict:
    """Validate the interchange shape while retaining evidence contradictions."""
    if not isinstance(source, dict) or not isinstance(source.get("players"), list):
        raise ImportError("A tournament must contain a players array.")
    if not 1 <= len(source["players"]) <= 1000:
        raise ImportError("Import between 1 and 1,000 players in a single section.")
    if not isinstance(source.get("warnings", []), list):
        raise ImportError("Tournament warnings must be an array.")
    if not isinstance(source.get("provenance", {}), dict):
        raise ImportError("Tournament provenance must be an object.")
    players, ids = [], set()
    warnings = [str(x) for x in source.get("warnings", [])]
    latest = _integer(source.get("latestRound"), "Latest round")
    if not 0 <= latest <= 99:
        raise ImportError("Latest round must be between 0 and 99.")
    for index, original in enumerate(source["players"], 1):
        if not isinstance(original, dict):
            raise ImportError(f"Player {index} must be an object.")
        player = dict(original)
        player["id"] = _identifier(player.get("id", index))
        if not isinstance(player.get("name"), str):
            raise ImportError(f"Player {index} needs a text name.")
        player["name"] = player["name"].strip()
        if not player["id"] or player["id"] in ids:
            raise ImportError(f"Duplicate or empty player ID: {player['id']}.")
        if not player["name"]:
            raise ImportError(f"Player {player['id']} has no name.")
        ids.add(player["id"])
        player["rating"] = _integer(player.get("rating"), f"Rating for {player['name']}")
        if not 0 <= player["rating"] <= 4000:
            raise ImportError(f"Invalid rating for {player['name']}.")
        for field in ("reportedScore", "initialScore"):
            if field in player:
                player[field] = _finite_number(player[field], f"{field} for {player['name']}")
        for field in ("entryRound", "withdrawnAfterRound"):
            if field in player:
                player[field] = _integer(player[field], f"{field} for {player['name']}")
                if not (1 if field == "entryRound" else 0) <= player[field] <= 99:
                    raise ImportError(f"Invalid {field} for {player['name']}.")
        if "scheduledByes" in player:
            if not isinstance(player["scheduledByes"], list):
                raise ImportError(f"Scheduled byes for {player['name']} must be an array.")
            byes = []
            for bye in player["scheduledByes"]:
                if not isinstance(bye, dict):
                    raise ImportError(f"Invalid scheduled bye for {player['name']}.")
                round_ = _integer(bye.get("round"), f"Scheduled bye round for {player['name']}")
                points = _finite_number(bye.get("points"), f"Scheduled bye points for {player['name']}")
                if not 1 <= round_ <= 99 or points not in {0, .5, 1}:
                    raise ImportError(f"Invalid scheduled bye for {player['name']}.")
                byes.append({**bye, "round": round_, "points": points})
            player["scheduledByes"] = byes
        active = player.get("active", True)
        player["active"] = active if isinstance(active, bool) else str(active).lower() not in {"false", "0", "withdrawn", "inactive", "no"}
        player["results"] = []
        if not isinstance(original.get("results", []), list):
            raise ImportError(f"Results for {player['name']} must be an array.")
        for result in original.get("results", []):
            if not isinstance(result, dict):
                raise ImportError(f"Invalid result for {player['name']}.")
            result = dict(result)
            round_ = _integer(result.get("round"), f"Round for {player['name']}")
            if not 1 <= round_ <= 99:
                raise ImportError(f"Invalid round for {player['name']}.")
            result["round"] = round_
            latest = max(latest, round_)
            result["result"] = str(result.get("result", "UNPLAYED")).upper()
            if result["result"] not in RESULTS:
                raise ImportError(f"Unknown result {result['result']} for {player['name']}.")
            result["opponentId"] = _identifier(result.get("opponentId")) or None
            color = str(result.get("color") or "").upper()
            if color not in {"", "W", "B"}:
                raise ImportError(f"Unknown color {color} for {player['name']}.")
            result["color"] = color or None
            if "played" in result and not isinstance(result["played"], bool):
                raise ImportError(f"The played flag for {player['name']} must be true or false.")
            result["played"] = result.get("played", result["result"] in {"W", "D", "L", "ADJOURNED"})
            player["results"].append(result)
        player["results"].sort(key=lambda x: x["round"])
        players.append(player)
    for player in players:
        for result in player["results"]:
            if result["opponentId"] and result["opponentId"] not in ids:
                warnings.append(f"{player['name']}, round {result['round']}: opponent {result['opponentId']} is missing from this section.")
    rounds = _integer(source.get("rounds"), "Configured round count", max(7, latest))
    if not 1 <= rounds <= 99 or latest > rounds:
        raise ImportError("The configured round count must cover all imported rounds (maximum 99).")
    for player in players:
        if player.get("entryRound", 1) > rounds + 1 or player.get("withdrawnAfterRound", rounds) > rounds:
            raise ImportError(f"Participation range exceeds the event rounds for {player['name']}.")
        if player.get("entryRound", 1) > player.get("withdrawnAfterRound", rounds) + 1:
            raise ImportError(f"Participation range is reversed for {player['name']}.")
        if any(bye["round"] > rounds for bye in player.get("scheduledByes", [])):
            raise ImportError(f"Scheduled bye exceeds the event rounds for {player['name']}.")
    completed = 0
    # A source can explicitly mark late entry/withdrawal ranges; absent that evidence,
    # missing rounds stay missing rather than being interpreted as zero-point byes.
    for round_ in range(1, latest + 1):
        complete = True
        for player in players:
            if round_ < int(player.get("entryRound", 1)) or round_ > int(player.get("withdrawnAfterRound", rounds)):
                continue
            records = [x for x in player["results"] if x["round"] == round_]
            if len(records) != 1 or records[0]["result"] == "UNPLAYED":
                complete = False
                break
        if not complete:
            break
        completed = round_
    if "completedRounds" in source:
        requested = _integer(source["completedRounds"], "Completed rounds")
        if not 0 <= requested <= rounds:
            raise ImportError("Completed rounds must be within the event round count.")
        if requested > completed:
            warnings.append(f"Source declared {requested} completed rounds, but only {completed} have complete records; the import uses {completed}.")
        completed = min(completed, requested)
    provenance = dict(source.get("provenance", {}))
    provenance.setdefault("source", filename)
    provenance.setdefault("format", format_)
    provenance.setdefault("importedAt", datetime.now(timezone.utc).isoformat())
    provenance["importedFile"] = filename
    provenance["lastImportedAt"] = datetime.now(timezone.utc).isoformat()
    tournament = {**source, "name": str(source.get("name") or Path(filename).stem),
                  "section": str(source.get("section") or "Open"), "rounds": rounds,
                  "completedRounds": completed, "latestRound": latest, "players": players,
                  "provenance": provenance,
                  "warnings": list(dict.fromkeys(warnings))}
    return tournament


def _read_xlsx(data: bytes, filename: str) -> dict:
    try:
        archive = ZipFile(io.BytesIO(data))
    except BadZipFile as exc:
        raise ImportError("This is not a valid XLSX workbook.") from exc
    with archive:
        if sum(item.file_size for item in archive.infolist()) > 50_000_000:
            raise ImportError("Workbook exceeds the 50 MB expanded size limit.")
        try:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            paths = {rel.get("Id"): posixpath.normpath(posixpath.join("xl", rel.get("Target", ""))).lstrip("/") for rel in rels}
            # Absolute OOXML targets are relative to the package, not xl/.
            for rel in rels:
                if rel.get("Target", "").startswith("/"):
                    paths[rel.get("Id")] = rel.get("Target").lstrip("/")
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared = ["".join(item.itertext()) for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
            sheets = {}
            for sheet in workbook.findall("s:sheets/s:sheet", NS):
                rel_id = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                rows = {}
                for row in ET.fromstring(archive.read(paths[rel_id])).findall("s:sheetData/s:row", NS):
                    values = {}
                    for cell in row:
                        value = cell.find("s:v", NS)
                        inline = cell.find("s:is", NS)
                        text = value.text if value is not None else "".join(inline.itertext()) if inline is not None else ""
                        if not text:
                            continue
                        if cell.get("t") == "s":
                            text = shared[int(text)]
                        values[re.sub(r"\d", "", cell.get("r", ""))] = text
                    if values:
                        rows[int(row.get("r"))] = values
                sheets[sheet.get("name")] = rows
        except (KeyError, ET.ParseError, ValueError, IndexError) as exc:
            raise ImportError("Workbook XML or relationships could not be read.") from exc
    if not {"Players", "Results", "Settings"}.issubset(sheets):
        from .feeds import parse_feed_rows
        candidates = []
        for sheet_name, sheet_rows in sheets.items():
            def column_number(label):
                value = 0
                for char in label:
                    value = value * 26 + ord(char) - ord("A") + 1
                return value
            width = max((column_number(col) for row in sheet_rows.values() for col in row), default=0)
            if width > 200:
                continue
            table = []
            for _, values in sorted(sheet_rows.items()):
                row = [""] * width
                for col, value in values.items():
                    row[column_number(col) - 1] = value
                table.append(row)
            try:
                candidate = parse_feed_rows(table, filename=f"{filename} / {sheet_name}")
                candidates.append(candidate)
            except ValueError:
                continue
        if len(candidates) != 1:
            raise ImportError("Choose one standings worksheet with recognizable Player/Name headers and optional Rating, Points and R1/R2 result columns. Multiple sections should be exported separately.")
        return candidates[0]
    players, by_name, by_member = [], {}, {}
    warnings = []
    settings = sheets["Settings"]
    get_setting = lambda row, fallback="": settings.get(row, {}).get("B", fallback)
    rating_source = get_setting(24, "USCF").upper()
    if rating_source == "DUAL":
        rating_source = get_setting(25, "USCF").upper()
    rating_column = "D" if rating_source == "FIDE" else "C"
    for rowno, row in sheets["Players"].items():
        if rowno == 1 or not row.get("A") or not row.get("B"):
            continue
        member_id = _identifier(row["A"])
        seed = str(_integer(row.get("H"), f"Seed for {row['B']}", len(players) + 1))
        player = {"id": seed, "name": row["B"], "rating": _integer(row.get(rating_column), f"Rating for {row['B']}"),
                  "fideRating": _integer(row.get("D"), f"FIDE rating for {row['B']}"),
                  "usChessRating": _integer(row.get("C"), f"US Chess rating for {row['B']}"), "usChessId": member_id,
                  "seed": int(_number(seed)), "schedule": row.get("F", ""),
                  "active": row.get("G", "Active").lower() == "active", "results": [],
                  "scheduledByes": [{"round": round_, "points": _finite_number(row[col], f"Scheduled bye for {row['B']}")}
                                    for round_, col in enumerate("JKLMNOPQR", 1) if col in row]}
        players.append(player)
        by_name.setdefault(row["B"].strip(), []).append(player)
        by_member[member_id] = player
    if not players:
        raise ImportError("No populated players found in the workbook.")
    latest = 0
    unknown_colors = 0
    game_map = {"1-0": ("W", "L"), "0-1": ("L", "W"),
                "0.5-0.5": ("D", "D"), "½-½": ("D", "D"),
                "F-0": ("FORFEIT_WIN", "FORFEIT_LOSS"),
                "0-F": ("FORFEIT_LOSS", "FORFEIT_WIN"),
                "F-F": ("FORFEIT_LOSS", "FORFEIT_LOSS")}
    def resolve(name: str, member: str) -> dict | None:
        matches = by_name.get(name.strip(), [])
        if len(matches) == 1:
            return matches[0]
        return by_member.get(_identifier(member))
    for rowno, row in sheets["Results"].items():
        if rowno == 1:
            continue
        if not row.get("A"):
            if any(row.get(col) for col in "BCD"):
                raise ImportError(f"Results row {rowno} has a player or result but no round number.")
            continue
        round_ = _integer(row.get("A"), f"Results row {rowno} round")
        if not 1 <= round_ <= 99:
            raise ImportError(f"Results row {rowno} has an invalid round number.")
        if not row.get("B"):
            if row.get("C") or row.get("D"):
                raise ImportError(f"Results row {rowno} needs the first player name.")
            continue
        latest = max(latest, round_)
        first = resolve(row["B"], row.get("O", ""))
        second = resolve(row.get("C", ""), row.get("P", ""))
        if first is None or (row.get("C") and second is None):
            warnings.append(f"Results row {rowno} contains an unresolved player name or ID.")
            continue
        result = row.get("D", "").strip()
        known = row.get("Q", "").upper() == "Y"
        if second is not None and not known:
            unknown_colors += 1
        if result == "BYE" and second is None:
            first["results"].append(_entry(round_, None, "BYE", sourceRow=rowno))
            continue
        if second is None:
            warnings.append(f"Results row {rowno} has no opponent and is not marked BYE.")
            first["results"].append(_entry(round_, None, "UNPLAYED", sourceRow=rowno))
            continue
        if result not in game_map and result:
            warnings.append(f"Results row {rowno}: unsupported result {result!r}; retained as unresolved.")
        pair = game_map.get(result, ("UNPLAYED", "UNPLAYED"))
        first["results"].append(_entry(round_, second["id"], pair[0], "W" if known else None, sourceRow=rowno))
        second["results"].append(_entry(round_, first["id"], pair[1], "B" if known else None, sourceRow=rowno))
    # Scheduled byes are distinct evidence. Retain a duplicate when a game is also
    # recorded instead of quietly discarding one side of the contradiction.
    for player in players:
        for bye in player["scheduledByes"]:
            round_, points = bye["round"], bye["points"]
            if round_ <= latest:
                code = {1: "BYE", .5: "HBYE", 0: "ZERO"}.get(points)
                if code is None:
                    warnings.append(f"{player['name']}: unsupported scheduled bye points {points} in round {round_}.")
                    continue
                existing = [r for r in player["results"] if r["round"] == round_]
                if not any(r["opponentId"] is None and r["result"] == code for r in existing):
                    player["results"].append(_entry(round_, None, code, source="scheduled bye"))
                if any(r["opponentId"] for r in existing):
                    warnings.append(f"{player['name']}, round {round_}: scheduled bye conflicts with a recorded game.")
    for rowno, row in sheets.get("Standings", {}).items():
        player = by_member.get(_identifier(row.get("A")))
        if player is not None and "G" in row:
            player["reportedScore"] = _finite_number(row["G"], f"Reported score for {player['name']}")
    if unknown_colors:
        warnings.append(f"{unknown_colors} recorded pairings have unknown colors; column order was not treated as color evidence.")
    warnings.append("Workbook formula forecasts and cached standings were not used as game results. Cached standings are retained only as reportedScore for reconciliation.")
    if get_setting(27).upper() == "Y":
        warnings.append("Workbook acceleration is an unverified modeling assumption, not a confirmed event rule; it is not enabled automatically.")
    for round_ in range(1, latest + 1):
        missing = sum(not any(r["round"] == round_ for r in p["results"]) for p in players)
        pending = sum(any(r["round"] == round_ and r["result"] == "UNPLAYED" for r in p["results"]) for p in players)
        if missing or pending:
            warnings.append(f"Round {round_}: {missing} players have no record; {pending} players have an unresolved result.")
    title = sheets.get("README", {}).get(1, {}).get("A", Path(filename).stem)
    title = title.replace(" — Open Pairing Predictor", "")
    tournament = {"name": title, "section": "Open", "rounds": _integer(get_setting(4), "Workbook round count", 7),
                  "latestRound": latest, "players": players, "warnings": warnings,
                  "sourceSettings": {"pairingSystem": get_setting(3), "win": _finite_number(get_setting(8), "Workbook win score", 1),
                                     "draw": _finite_number(get_setting(9), "Workbook draw score", .5), "loss": _finite_number(get_setting(10), "Workbook loss score"),
                                     "mergeRound": 2, "accelerationConfirmed": False}}
    return normalize_tournament(tournament, filename, "XLSX source template")


def _round_token(token: str, round_: int) -> dict | None:
    token = token.strip().replace("½", ".5")
    if not token or token in {"-", "--"}:
        return None
    special = {"BYE": "BYE", "B": "BYE", "HBYE": "HBYE", "H": "HBYE", "ZERO": "ZERO", "U": "ZERO", "UNPLAYED": "UNPLAYED"}
    if token.upper() in special:
        return _entry(round_, None, special[token.upper()])
    match = re.fullmatch(r"([WDL])\s*(\d+)(?:\s*([wb]))?", token, re.I)
    if not match:
        raise ImportError(f"Unrecognized round {round_} token {token!r}. Use W12w, D3b, L4, BYE, HBYE, or ZERO.")
    result, opponent, color = match.groups()
    return _entry(round_, _identifier(opponent), result.upper(), color.upper() if color else None)


def _read_csv(text: str, filename: str) -> dict:
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ImportError("CSV needs a header row with id, name, and rating.")
    fields = {key.strip().lower(): key for key in reader.fieldnames}
    if "name" not in fields:
        raise ImportError("CSV needs a name column. Optional columns: id, rating, score, active, R1, R2, …")
    round_fields = [(int(match.group(1)), key) for key in reader.fieldnames if (match := re.fullmatch(r"(?:r|round\s*)(\d+)", key.strip(), re.I))]
    players = []
    for index, row in enumerate(reader, 1):
        if not any(row.values()):
            continue
        if None in row:
            raise ImportError(f"CSV row {index + 1} has more fields than its header; quote names containing commas.")
        get = lambda key, default="": row.get(fields.get(key, ""), default) or default
        player = {"id": get("id", str(index)), "name": get("name"), "rating": get("rating"),
                  "active": get("active", "true").lower() not in {"false", "0", "withdrawn", "inactive", "no"},
                  "results": [entry for round_, key in round_fields if (entry := _round_token(row.get(key) or "", round_))]}
        if "score" in fields and get("score"):
            player["reportedScore"] = _finite_number(get("score"), f"Reported score on CSV row {index + 1}")
        players.append(player)
    missing_colors = any(result["played"] and result["color"] is None
                         for player in players for result in player["results"])
    warnings = ["Round tokens without a w/b suffix retain unknown colors."] if missing_colors else []
    return normalize_tournament({"players": players, "rounds": max(7, max((r for r, key in round_fields), default=0)),
                                 "warnings": warnings}, filename, "CSV crosstable")


def _read_trf(text: str, filename: str) -> dict:
    players, metadata, warnings = [], {}, []
    result_map = {"1": "W", "=": "D", "0": "L", "+": "FORFEIT_WIN", "-": "FORFEIT_LOSS"}
    round_count = 0
    for line in text.splitlines():
        code = line[:3]
        supported_metadata = {"012", "022", "032", "042", "052", "062", "072", "082", "092", "102", "112", "122", "132", "142", "XXR"}
        if line.strip() and code not in supported_metadata | {"001"}:
            raise ImportError(f"TRF record {code} is unsupported and may contain pairing configuration. Convert it to normalized JSON with explicit settings first.")
        if code in supported_metadata:
            metadata[code] = line[4:].strip()
        if code != "001":
            continue
        if len(line) < 84:
            raise ImportError("TRF player records must use the FIDE fixed-width layout.")
        player = {"id": line[4:8].strip(), "name": line[14:47].strip(),
                  "rating": line[48:52].strip(), "federation": line[53:56].strip(),
                  "fideId": line[57:68].strip(), "reportedScore": _finite_number(line[80:84], "TRF reported score"),
                  "active": True, "results": []}
        for round_, offset in enumerate(range(91, len(line), 10), 1):
            block = line[offset:offset + 10].ljust(10)
            opponent, color, result = block[:4].strip(), block[5].lower(), block[7].strip().upper()
            if not block.strip():
                continue
            round_count = max(round_count, round_)
            no_opponent = not opponent or opponent == "0000"
            if no_opponent:
                code_ = {"1": "BYE", "+": "BYE", "U": "BYE", "H": "HBYE", "=": "HBYE", "F": "BYE", "Z": "ZERO", "0": "ZERO", "-": "ZERO", "": "ZERO"}.get(result)
            else:
                code_ = result_map.get(result) or {"W": "W", "D": "D", "L": "L"}.get(result)
            if code_ is None:
                code_ = "UNPLAYED"
                warnings.append(f"{player['name']}, round {round_}: TRF result {result!r} requires review.")
            entry = _entry(round_, None if no_opponent else _identifier(opponent), code_, color.upper() if color in {"w", "b"} else None)
            if result in {"W", "D", "L"}:
                entry["played"] = False
                entry["sourceResult"] = result
            player["results"].append(entry)
        players.append(player)
    if not players:
        raise ImportError("No 001 player records found in this TRF.")
    if _integer(metadata.get("082"), "TRF team count"):
        raise ImportError("Team tournaments are not supported by this individual-event importer.")
    if metadata.get("092"):
        warnings.append(f"TRF tournament type {metadata['092']!r} is retained as source context; select the matching pairing rules in the app.")
    configured_rounds = _integer(metadata.get("142", metadata.get("XXR")), "TRF round count", max(7, round_count))
    return normalize_tournament({"name": metadata.get("012", Path(filename).stem),
                                 "rounds": max(configured_rounds, round_count), "players": players,
                                 "warnings": warnings, "sourceSettings": {"tournamentType": metadata.get("092", "")}}, filename, "FIDE TRF16 player-record subset")


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pre_blocks: list[str] = []
        self._pre: list[str] | None = None
        self.title = ""
        self._title = False
    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "pre": self._pre = []
        if tag == "title": self._title = True
        if tag == "br" and self._pre is not None: self._pre.append("\n")
    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self._pre is not None:
            self.pre_blocks.append("".join(self._pre))
            self._pre = None
        if tag == "title": self._title = False
    def handle_data(self, data: str) -> None:
        if self._pre is not None: self._pre.append(data)
        if self._title: self.title += data


def _read_html(text: str, filename: str) -> dict:
    parser = _HTMLText()
    parser.feed(text)
    sections = []
    for pre in parser.pre_blocks:
        players = []
        current = None
        for line in pre.splitlines():
            cells = line.split("|")
            if len(cells) >= 4 and re.fullmatch(r"\s*\d+\s*", cells[0]) and cells[1].strip():
                # Classic US Chess MSA crosstable: Pair | Player Name | Pts | R1 | ...
                try:
                    points = float(cells[2].strip())
                except ValueError:
                    continue
                current = {"id": cells[0].strip(), "name": cells[1].strip(), "rating": 0,
                           "reportedScore": points, "active": True, "results": []}
                for round_, token in enumerate(cells[3:], 1):
                    token = token.strip()
                    if not token:
                        continue
                    entry = _round_token(token, round_)
                    if entry:
                        current["results"].append(entry)
                players.append(current)
            elif current is not None and len(cells) >= 2:
                member = re.search(r"(\d{7,9})\s*/", cells[1])
                rating = re.search(r"R:\s*(\d+)", cells[1])
                if member:
                    current["usChessId"] = member.group(1)
                if rating:
                    current["rating"] = int(rating.group(1))
        if players:
            sections.append(players)
    if len(sections) != 1:
        raise ImportError("HTML support requires one classic US Chess MSA <pre> crosstable section. Select a single section or export CSV/TRF; arbitrary web layouts are not inferred.")
    latest = max((r["round"] for p in sections[0] for r in p["results"]), default=0)
    return normalize_tournament({"name": parser.title.strip() or Path(filename).stem,
                                 "rounds": max(7, latest), "players": sections[0],
                                 "warnings": ["US Chess MSA crosstables do not provide colors. Missing colors remain unknown."]}, filename, "US Chess MSA HTML crosstable")


def parse_text(text: str, filename: str = "results.csv") -> dict:
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise ImportError("The source file is empty.")
    if len(text) > 10_000_000:
        raise ImportError("Text import exceeds the 10 MB limit.")
    suffix = Path(filename).suffix.lower()
    if suffix == ".json" or text.lstrip().startswith("{"):
        try:
            return normalize_tournament(json.loads(text), filename)
        except json.JSONDecodeError as exc:
            raise ImportError(f"Invalid tournament JSON: {exc.msg}.") from exc
    if suffix in {".html", ".htm"} or re.search(r"<(?:!doctype|html|pre)\b", text[:1000], re.I):
        return _read_html(text, filename)
    if suffix in {".trf", ".trf16", ".trfx", ".txt"} and re.search(r"^001\s", text, re.M):
        return _read_trf(text, filename)
    return _read_csv(text, filename)


def parse_bytes(data: bytes, filename: str) -> dict:
    if len(data) > 10_000_000:
        raise ImportError("Import exceeds the 10 MB upload limit.")
    if Path(filename).suffix.lower() == ".xlsx" or data.startswith(b"PK\x03\x04"):
        return _read_xlsx(data, filename)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    return parse_text(text, filename)


def load_tournament(path: Path | str) -> dict:
    path = Path(path)
    return parse_bytes(path.read_bytes(), path.name)
