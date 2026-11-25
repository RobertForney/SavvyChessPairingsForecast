# Following published standings

`app.feeds` parses and previews incoming standings without applying them. A publisher snapshot is evidence at a moment in time. Published points, row positions and absent names cannot establish game history, initial pairing numbers or withdrawal dates.

## Supported source paths

`parse_feed_bytes(data, filename, section=None, source_url=None)` accepts the existing normalized JSON, TRF and workbook formats, plus recognizable static HTML, CSV and TSV standings tables. `parse_feed_rows(rows, filename, section=None)` exposes the same table mapping to generic worksheet readers. Tables need an explicit player/name header. Ratings, points, membership IDs and R1/R2/etc. columns are optional.

Supported round-token examples are `W12w`, `D3b`, `L4`, `W 12 ⚪️`, `12w1`, `3b½`, `BYE`, `HBYE`, `U` and `UNPLAYED`. Unknown layouts fail with a reviewable error. A numeric result in a labelled round column retains the outcome with unknown opponent/color; it is not a complete pairing history. A totals-only table imports the roster and scoped published points without synthesizing any games.

HTML imports require one recognizable section, or an explicit table label/index when multiple sections exist. An explicit Section/Group column is filtered to the chosen section; mixed sections are rejected when no section is selected. Source table labels remain attributed, and a heading that differs from the requested section is flagged for review because a heading may identify the event instead. Hidden script/style content is ignored. A JavaScript-only ratings shell cannot be parsed as if it contained a crosstable: use a publisher export or saved rendered table. This module does not call undocumented publisher APIs or supply an authenticated US Chess API integration.

`fetch_feed(url, section=None)` uses the shared HTTPS downloader with explicit public-source support, public-address validation, pinned TLS resolution, redirect validation and the 8 MB limit. Its cache age is 30 seconds. Fetching is user initiated; this module does not start background polling or write tournament state.

## Identity and merge rules

`preview_feed(current, incoming, source_url=None)` returns a proposed tournament, its base snapshot hash, and a summary. Application is a separate action after review. The base hash lets the server reject a stale proposal if the workspace changed after preview.

Players match through exact US Chess/FIDE membership IDs first, then an exact normalized name only when that name identifies one existing player. Conflicting membership identities, duplicate incoming members and ambiguous names require identity review. Incoming row numbers resolve the incoming snapshot's opponents before remapping them to stable internal IDs; changing final ranks therefore does not rename players or attach results to someone else.

Existing starting numbers, assigned ratings, schedules, teams, dated participation declarations, requested byes, restrictions and tracking preferences remain intact. Unknown setup fields can be populated when an incoming source explicitly provides them. A header called `seed`, `TPN` or `initial pairing number` supplies starting order; a header called `rank`, `#`, `Nr` or `No` does not. Existing reviewed values win if a subsequent source disagrees, and the discrepancy is listed for review. Unknown ratings can be learned from a subsequent source without overwriting assigned pairing ratings.

Partial sources do not delete earlier rounds or remove players missing from the latest roster. A blank color does not erase a previously reviewed color for the same encounter. A pending row does not erase a reviewed final result. A source correction to an existing result is shown with both values in the preview. Duplicated source round records require reconciliation rather than selecting the last row silently.

Completed rounds advance only through contiguous merged final-outcome history, and only after a user applies the preview. A correction that breaks a previously committed cutoff is flagged; the existing cutoff is retained so normal history validation can block the contradictory forecast. Complete outcomes alone do not imply that opponent identities, colors or setup are sufficient to calculate reliable pairings.

Published totals are retained as `sourceReportedScore` with `sourceReportedRound`. They do not become an initial score, inferred result or pairing score. `U` is retained as zero-point unspecified availability and does not create a scheduled bye or withdrawal. Differences between published totals and available history remain visible warnings.

Each reviewed refresh attributes its own warnings under `provenance.lastStandingsRefresh.warningMessages`. A subsequent refresh replaces those generated warnings with current findings while preserving unrelated manual warnings. A roster-only or score-mismatch warning therefore clears once new evidence resolves it.

## Preview contract

The summary includes added/updated players, added/corrected/unchanged results, identity matches, source corrections, unresolved conflicts, missing history/colors, preserved absent players, setup differences and the old/proposed completed-round cutoffs. `requiresReview` is always true. Nothing in the preview function persists a source or changes the active workspace.

Run `python -m unittest discover -s tests -p test_feeds.py -v` for independent changing-rank, partial-feed, membership-conflict, totals-only and missing-evidence fixtures.
