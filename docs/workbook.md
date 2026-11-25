# Source workbook and import contracts

The original `Louisiana_State_Championship_2026_pairings_no_rematches.xlsx`
is retained unchanged. It contains 13 sheets and a roster of 51 players in
the Open section of a seven-round event. The two schedules are Open 4-Day
and Open 3-Day, with a planned merge in round 2.

## Evidence imported from the workbook

The app reads Players, Results, Settings, and cached Standings scores.
It does not execute formulas or import the PairingEngine, NextRound,
PairingSheet, or NextRoundResults forecasts as historical games.

The spreadsheet's PlayerID is a US Chess membership number. The app uses
the explicit Seed as its pairing ID and preserves membership IDs separately
as `usChessId`. Numeric scientific notation is normalized without treating
the membership ID as a pairing rank. Result names resolve against the roster;
cached helper IDs are used only as a fallback.

There are 105 entered game/bye rows: 24 in round 1, 26 in round 2, 22 in
round 3, and 11 each in rounds 4, 5, and 6. Of those rows, 102 have a game
score, one is a full-point bye, and two have no result. Of the 104 paired games,
49 explicitly mark their colors known and 55 retain unknown colors. Each group
includes one unresolved game, so 54 completed games have unknown colors.
The remaining full-point bye has no opponent and needs no color assignment.

Round 1 covers the field when scheduled byes are included. The app therefore
reports one complete round and round 6 as the latest round with evidence.
It does not report six complete rounds based on the workbook's MAXIFS formula.

The following require review in the source data:

- Round 2: the Etienne–FAID game has no result. Tagorda has both a scheduled
  half-point bye and a recorded game.
- Round 3: the Prince Reyes–Stewart game has no result. Tagorda has both a
  scheduled zero-point bye and a recorded game. Hargrove, Cryer, and Marks
  have no record.
- Round 5: Beavers has both a scheduled half-point bye and a recorded game.
- Rounds 4–6: many active players have no entered game or bye. Missing rows
  are retained as missing evidence, without inventing withdrawals or losses.
- The workbook enables acceleration as a modeling assumption. Its own
  Rules_Reference says acceleration is not confirmed by the event announcement.
  The app does not enable this assumption automatically.

Scheduled byes and game rows are independent evidence. Conflicting records
remain present together so that validation can identify the conflict. Cached
Standings scores are retained as `reportedScore` for reconciliation, rather
than used to replace scores computed from the actual history.

## Supported file formats

All parsers use Python's standard library. App uploads are limited to 8 MB;
XLSX archives are limited to 50 MB of expanded data. Imports operate on one
tournament section at a time.

### The supplied XLSX template

The importer requires sheets named Players, Results, and Settings with the
supplied column layout. Other spreadsheet layouts should be exported as a
supported CSV or TRF instead of guessing the meaning of arbitrary cells.

ColorKnown? must be `Y` for the first and second players to be assigned White
and Black. A blank flag preserves `color: null`, even when the columns happen
to be titled White and Black.

### Player CSV or TSV

The header requires `name`. Optional columns are `id`, `rating`, `score`,
`active`, and `R1`, `R2`, etc. Comma, semicolon, and tab delimiters are detected.
Names containing the delimiter must be quoted.

```csv
id,name,rating,score,active,R1,R2
1,"Alpha, Ada",1900,1.5,true,W2w,HBYE
2,"Beta, Ben",1800,0,true,L1b,ZERO
```

`W2w` means a win against pairing ID 2 with White. `D3b` means a draw
against ID 3 with Black. `L4` means a loss against ID 4 with color unknown.
The first letter always denotes the result. `BYE`, `HBYE`, and `ZERO`
denote full-, half-, and zero-point byes. `UNPLAYED` retains an unresolved
record. An empty round cell adds no result. `score` is a reported score for
reconciliation. `active=false` withdraws the player from future pairings,
without erasing their history.

### FIDE TRF

The importer reads the legacy TRF16 `001` fixed-width player-record subset,
including the conventional positions for names, ratings, reported scores,
opponents, colors, results, and byes. The `142` and `XXR` round-count records
are also recognized. It does not claim complete TRF26 configuration support.

Regular `1`, `=`, and `0` results are played games. `+` and `-` are unplayed
forfeits. `W`, `D`, and `L` retain the corresponding outcome with `played=false`
because these codes mean games with fewer than one move, rather than ordinary
played games. `H`, `F`, `U`, and `Z` represent the corresponding bye outcomes.
Unrecognized result codes remain unresolved with a warning.

Unsupported pairing-affecting records, including acceleration, prohibited
pairings, scoring, requested-bye configuration, and pairing-system settings,
are rejected. They must be represented explicitly in normalized JSON and
reviewed in the app rather than silently ignored.

Reference: [FIDE TRF16 specification](https://www.fide.com/FIDE/handbook/C04Annex2_TRF16.pdf),
[FIDE Technical Commission TRF 2026 page](https://tec.fide.com/trf-2026/).

### US Chess MSA HTML

The importer accepts a downloaded HTML page containing one classic US Chess
MSA preformatted crosstable. It reads pairing number, name, points, round
results, membership ID, and the pre-event regular rating where present.
MSA result tokens do not establish color. The importer retains that gap.

Pages with multiple crosstable sections are rejected so that identically
numbered players in separate sections cannot be accidentally merged. Select
a single section before downloading. Arbitrary HTML layouts, JavaScript-only
pages, PDFs, screenshots, and OCR are outside this parser's supported formats.

### Normalized JSON

JSON is the lossless interchange and manual correction format. Core shape:

```json
{
  "name": "Example Open",
  "section": "Open",
  "rounds": 5,
  "players": [
    {
      "id": "1",
      "name": "Alpha, Ada",
      "rating": 1900,
      "active": true,
      "results": [
        {
          "round": 1,
          "opponentId": "2",
          "color": "W",
          "result": "W",
          "played": true
        }
      ]
    }
  ]
}
```

Include the complete roster and reciprocal opponent records; the abbreviated
example illustrates only the shape. Allowed results are `W`, `D`, `L`, `BYE`,
`HBYE`, `ZERO`, `UNPLAYED`, `FORFEIT_WIN`, `FORFEIT_LOSS`, and `ADJOURNED`. Missing colors are
`null`. Preserve source metadata in `provenance` and issues in `warnings`.
`latestRound` is derived again on import. `completedRounds` uses the last
consecutive round with complete records; an explicit lower cutoff is retained.
An overstated completed-round count is reduced with a warning. Existing source
provenance and external URLs survive JSON round trips; the new import filename
is recorded separately as `provenance.importedFile`.

Optional `entryRound` and `withdrawnAfterRound` explicitly delimit participation
when that history is known. Without this evidence, a missing historical result
stays missing. Optional `scheduledByes` contains objects with `round` and
numeric `points`; a zero-point requested bye still excludes the player from
that pairing round.

## Additional reviewed pairing inputs

`pairingNumber` is the definitive original tournament pairing number; `pairingRating`
is an explicit assigned pairing rating, separate from the source `rating`. Duplicate
pairing numbers are rejected. Exact Dutch mode requires the complete consecutive
TPN roster, including withdrawn entries, because TPN parity affects colors.

Player `virtualPoints` entries contain `{ "round": 1, "points": 1 }`. These are
round-specific fictitious pairing points, not cumulative additions to standings.
Alternatively, tournament `acceleration: "baku"` selects the native Dutch Baku
procedure. Automatic Baku and nonzero manual virtual points cannot be combined.

Tournament `prohibitedPairs` contains ID pairs such as `[["1", "2"]]`.
`forcedPairs` accepts an ID pair or a color lock such as
`[{ "whiteId": "1", "blackId": "2" }]`. Forced pairs are supported by US/custom
forecasts; Dutch mode rejects them explicitly rather than changing the procedure.

`reentryOf` links an entry to its earlier pairing ID. Choose the carried entry
explicitly using active status and participation boundaries. The app does not
automatically decide the best-score/latest-entry policy. US color history starts
with the carried entry, while encounters against an unchanged opponent remain
relevant. Dutch mode requires a separately reviewed FIDE-compliant player history.

`prizeContender: true` explicitly qualifies a player for the selected US last-round
series exception. Both paired players must qualify; an ordinary strict pairing is
attempted first. `ADJOURNED` counts as a provisional draw for pairing, with played
color and opponent history retained.

An explicit finite `initialScore` is retained as a starting score. It is not
converted into invented game records. Ratings, rounds, scores, and scheduled
bye fields are validated; malformed or nonfinite values are rejected.
