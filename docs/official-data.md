# Official US Chess validation snapshot

The app includes a frozen copy of the visible Open crosstable at [US Chess event 202609070433](https://ratings.uschess.org/event/202609070433), captured at **2026-11-20 03:39:34.260 UTC**. It contains the displayed cell text and US Chess player links. Hidden page data and HTML styling were discarded. This is a reproducible test fixture, not a live results connection.

`data/us-chess-202609070433.json` uses schema version 1. Its SHA-256 is `afddccba172f2377eefc2edc4a6c724f9c83a7c39a425c8cb2cac92c84cbcd4c`; it is 23,817 bytes. This hash identifies the captured JSON fixture, not a workbook or a hash of the live web page. The original workbook is preserved separately.

## Evidence and validation

All **51 players** match the original workbook by exact US Chess member ID; no name-based identity substitutions were necessary. Member IDs identify players. The official table’s row numbers are final ranks and only resolve the displayed opponent references. They never become initial pairing numbers.

The snapshot has **357 player-round records**, covering seven rounds:

| Round | Played games | Forfeited games | Half/full-bye records | Unavailable/unspecified records |
| --- | ---: | ---: | ---: | ---: |
| 1 | 24 | 0 | 3 | 0 |
| 2 | 24 | 1 | 1 | 0 |
| 3 | 23 | 0 | 5 | 0 |
| 4 | 24 | 1 | 1 | 0 |
| 5 | 23 | 0 | 2 | 3 |
| 6 | 24 | 0 | 1 | 2 |
| 7 | 20 | 0 | 1 | 10 |

Every named opponent, outcome and played color is reciprocal. All 51 computed scores equal the displayed final scores, totaling **172 points**. The source contains 143 `W`, 143 `L`, 38 `D`, two `X`, two `F`, 12 `H`, two `B` and 15 `U` records.

`W/L/D` retain the visible white/black circle as played color. `X/F` become a forfeit win/loss with a named opponent, no played color and `played=false`. `H` is a half-point bye; `B` is a full-point bye. `U` retains zero points and an explicit `availabilityUnknown` flag: it means unavailable/unspecified, does not establish a withdrawal date, and is excluded from pairing-bye agreement.

## Setup assumptions

The official crosstable supplies results and pre-event regular ratings, but does not supply the director’s complete historical pairing setup. The app overlays the original workbook’s initial pairing numbers, schedule memberships, merge at round 2, and assigned pairing ratings through the exact member-ID match. The provenance records the workbook filename and hash. This overlay is explicitly an assumption; it is not attributed to the official crosstable.

The official pre-event `R` value remains `rating`; the original workbook’s assigned value becomes `pairingRating`. Post-event ratings are not substituted. Ian Fuselier, Hector Roland Reeves and Gamal Belal FAID Jr. have no numeric pre-event regular rating in the capture and remain zero/unknown.

No withdrawal dates, late-entry dates or scheduled byes are inferred from the final results. All players remain active until an explicit availability declaration is supplied. Observed `H/B/U` records are history, not evidence of when a request was announced. The workbook’s conflicting requested byes are not copied into this source. Initial color, acceleration and director variations remain unconfirmed. These limitations explain why agreement with actual pairings is not expected to be perfect even with complete results.

Historical replay excludes final scores, final ranks and later-round results from the forecast input. The first forecast candidate is compared with the recorded next round; alternatives are not selected after observing the answer.

## Differences from the original workbook

Counts below refer to player-round records, so a changed game result normally affects two records. The field comparisons preserve the workbook’s contradictory rows rather than silently discarding them.

- **138 official-only player-round records** fill previously absent workbook evidence.
- **25 existing player-round result/conflict differences:** 16 records across eight round-1 games reverse the winner; two round-2 records resolve Etienne–FAID as a forfeit; two round-3 records resolve Stewart–Prince Reyes as a draw; two round-6 records reverse the Riley–Ner winner; three records remove conflicting duplicate outcomes for Tagorda (R2/R3) and Beavers (R5).
- **117 color-field differences:** 110 records supply a previously unknown played color, four records reverse two known round-1 board color allocations, two remove assigned colors from the unplayed Etienne–FAID forfeit, and one removes a conflicting duplicate bye’s empty color.
- **13 pre-event regular rating differences** and **45 final snapshot-score differences**. The workbook totals are a partial-source snapshot and are not treated as final results.
- **Four requested-bye conflicts:** Beavers R5 and R7, Tagorda R2 and R3. A final zero-point `U` is not substituted for a declared half-point bye.

The eight round-1 winner disagreements are Stewart–Lutz, Hargrove–Breaux, Baxter–Rodriguez, Brack–Marks, Dela Bajan–Murru, Yuan–Reeves, Bennett–Jude Braband, and Bryce Braband–Minvielle. The two round-1 color disagreements are Rowe-Watters–Brooks and Serice–Anderson.

| Player | Workbook rating / assigned pairing rating | Official pre-event regular rating |
| --- | ---: | ---: |
| Chauncey Rowe-Watters | 1900 | 1901 |
| Marcel Eugene Wilder | 1713 | 1688 |
| Dex Webster | 2317 | 2253 |
| Mitchell Costanza | 1943 | 1924 |
| James Eryk Hargrove | 1916 | 1900 |
| Nathaniel Riley | 1900 | 1910 |
| Vaughn Daemian Dela Bajan | 1753 | 1751 |
| Roy Yuan | 1781 | 1830 |
| Ramon Tagorda | 1677 | 1706 |
| Hannah Ren | 1454 | 1522 |
| Gregory Natal | 1613 | 1615 |
| Sara Brooks | 1047 | 1120 |
| Kevin Kabachnik | 974 | 1114 |

`official_metadata().workbookComparison.details` contains every rating, result, color, additional record, requested-bye and snapshot-score comparison with both source values. No discrepancy rewrites the original workbook.

Run `python -m unittest discover -s tests -p test_official_data.py -v` to verify fixture integrity, reciprocity, totals, setup attribution, unknown availability and historical replay boundaries.
