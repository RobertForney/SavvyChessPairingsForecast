# Pairing rules and import research

Sources checked on **2026-11-19**. This document distinguishes implemented procedures from remaining work. Tournament directors may select different variations, software, ratings, and event restrictions; reproducing their next round requires those inputs as well as results.

## Current implementation

The application offers three distinct engines. **US Chess** follows ordered Swiss brackets, natural pairings, odd-player selection, chronological color entitlement and bounded 80/200-point switches. **FIDE Dutch** invokes pinned BBP Pairings 6.0.0, the release implementing the Dutch rules effective February 2026. **Custom matching** retains the earlier additive optimizer for exploratory restrictions. See [US procedure and limits](us-chess-algorithm.md) and [native Dutch provenance and limits](dutch-engine.md).

The interface and exports preserve that distinction. A custom matching is not evidence that it is an official Swiss result. The US procedure permits director choices; the Dutch result depends on faithful original pairing numbers and event inputs. No certification or agreement with undisclosed settings is claimed. Candidate ranks are not calibrated probabilities. Forecasts retain input/rule hashes, engine identity and rule edition.

Before forecasting, validate the whole section, including players who might float into another score group. Block on contradictory or missing results in a completed round, unknown opponents, duplicate player numbers, inconsistent reciprocal games, invalid scores, or absent pairing inputs required by the selected profile. Missing colors remain unknown; a win or loss never determines color.

If a source supports only an opponent forecast, that would require a separately labeled mode that carries color uncertainty through every relevant constraint. The initial version should require completed played-game colors instead of silently filling them.

## Researched rule editions

### US Chess

The available official rulebook is the seventh edition free download, revised **January 1, 2026**. Its Swiss priorities begin with avoiding repeat opponents, then equal scores, upper-half versus lower-half pairings, color equalization, and alternation. There are explicit variations and exceptions, including re-pairing after an earlier no-show forfeit. Rules 29E5a/b distinguish 80-point color-alternation switches and 200-point switches to avoid significant color imbalance; evaluating a switch is more specific than comparing the two eventual opponents' ratings. Unplayed games do not count for color. Bye selection considers score, rating, unrated status, previous full-point byes/no-show wins, and earlier or committed half-point byes. Director choices and named variations can affect legal outputs. [US Chess official rules, chapter 2, rules 27–29](https://new.uschess.org/sites/default/files/media/documents/us-chess-rule-book-online-2026.pdf).

Implementation decisions:

- Save a profile ID and rule edition with every forecast.
- Expose only implemented controls. Label general forecast weights as preferences, not exact implementations of numbered rules.
- Keep initial pairing rank distinct from displayed standings, rating, and external membership ID.
- Keep requested byes, pairing-allocated byes, withdrawals, reentries, and no-show forfeits as different facts.
- The implemented 80/200-point controls compare exchanged players, including equivalent upper/lower transpositions and linked odd-player boards. Published numerical examples are regression fixtures.
- Manual round-specific virtual points, explicit reentry identities and prohibited/locked pairs are supported. Automatic reentry score selection, merged schedules, class-pairing procedures and unusual scoring are not inferred.

### FIDE

Current Basic and Dutch Swiss rules apply from **February 1, 2026**. Dutch pairings use score and tournament pairing number, descending score brackets, prescribed candidate generation, and ordered criteria C1–C21. Absolute criteria address repeat opponents, bye eligibility, and incompatible absolute color preferences among non-topscorers. Completion and the bye recipient's score have priority before lower quality criteria. Float histories influence candidate ranking. Final-round topscorer exceptions require the exact definition and color allocation procedure, rather than a global permission to disregard color. [FIDE Dutch system](https://handbook.fide.com/chapter/C0403202602), [FIDE announcement confirming the effective date](https://www.fide.com/fide-reminds-organizers-and-arbiters-of-updated-swiss-rules-effective-from-february-1-2026/).

Only played games count when interpreting color sequences. Withdrawn players and participants known to miss the next round are not paired. Adjourned games are treated as draws for pairing purposes; late entries and result corrections have defined handling. [FIDE general handling rules](https://handbook.fide.com/chapter/GeneralHandlingRulesForSwissTournaments202602).

Implementation decisions:

- Use the native **FIDE Dutch** profile for Dutch calculations. The earlier additive matching mode is explicitly labeled custom matching.
- Keep no-repeat and bye-eligibility checks independent of preference penalties. A search timeout must not turn prohibited edges into allowed ones.
- Preserve initial color, definitive pairing numbers, actual scoring, total rounds, acceleration inputs, and the history needed by the native engine.
- Detect unsupported event settings and return a useful reason instead of an apparently authoritative result.
- Retain the pinned engine version, rule edition, normalized input hash, native-input hash and diagnostic output.

## Pinned Dutch engine adapter

**BBP Pairings 6.0.0** is installed and invoked by the Dutch profile. The official release implements the rules whose effective date was delayed to 2026. The app uses the documented TRF(bx) compatibility format; this does not establish full TRF26 support in the separate upload parser. Burstein is not exposed. These facts do not establish endorsement of this application. [Pinned release](https://github.com/BieremaBoyzProgramming/bbpPairings/releases/tag/v6.0.0), [maintainer documentation](https://github.com/BieremaBoyzProgramming/bbpPairings).

The installed source license is **Apache License 2.0**, and the official Windows build's MinGW notices are preserved alongside it. Historical releases used a different license. [Pinned license](https://github.com/BieremaBoyzProgramming/bbpPairings/blob/v6.0.0/LICENSE.txt), [release history](https://github.com/BieremaBoyzProgramming/bbpPairings/releases).

Implemented adapter controls and validation targets:

1. Pin release 6.0.0 and source commit `16a000f9811de322b0e835d5643198226165b5a9`, with the official archive and executable SHA-256 values in `app/engine-manifest.json`. The Windows executable has been downloaded, verified and run.
2. Pin binary or build provenance and SHA-256. Do not download an unpinned latest executable when a forecast runs.
3. Serialize a validated tournament using the engine's documented format. Reject unsupported flags or scoring rather than dropping them.
4. Invoke the executable with argument arrays, a bounded runtime, and isolated temporary files. Retain sanitized diagnostics and input/output hashes.
5. Validate its result independently: exactly one assignment per eligible player, reciprocal opponents, legal byes, played-opponent exclusions, and color requirements for the applicable system.
6. Native regression fixtures include the release's 2026 C5/C9 cases, requested byes, withdrawals, repeat-bye exclusion, manual acceleration, prohibited pairs, forfeits and infeasible input. Further backtesting across real event histories remains valuable.
7. Make exact-mode failures visible. A fallback forecast must be explicitly selected and labeled rather than substituted as an equivalent result.

This app should not claim certification from an engine name or an older software endorsement list. Current FIDE technical regulations describe tournament-handler testing, import/export and validation tooling. [FIDE technical regulations, section 7](https://handbook.fide.com/chapter/ChessEquipmentWithElectronicComponenets032026).

## Import strategy

Prefer structured tournament files, followed by deterministic format adapters. Heterogeneous HTML, PDF, screenshots, and free text should enter a **review and reconciliation** step before becoming authoritative pairing input. An AI extraction layer could propose records and source locations; it should never invent colors, reconcile contradictory opponents silently, or decide that absent rows represent withdrawals.

### Structured formats

FIDE's current **TRF26** extends the exchange format for tournament results, testing, in-tournament exchange, and national ratings. Retain support for legacy TRF16/TRF06 as separate format versions. New records and result codes must not be assumed to mean the same thing across all generations. [FIDE Technical Commission TRF26 resources](https://tec.fide.com/trf-2026/).

The legacy specification documents fixed-width player and round records. A TRF16-compatible player-record parser alone must not be advertised as full TRF26 support. [Official TRF16 specification](https://www.fide.com/FIDE/handbook/C04Annex2_TRF16.pdf).

US Chess rating exports use three DBase files: `Thexport.dbf`, `Tsexport.dbf`, and `Tdexport.dbf`, containing tournament, section, and player details. Published crosstables can show ratings or ordering that differ from the original pairing inputs. An import must not substitute crosstable row order for the original starting order. [US Chess TD and affiliate FAQ](https://new.uschess.org/tournament-director-and-affiliate-frequently-asked-questions).

Suggested delivery sequence:

| Adapter | First implementation boundary | Remaining work |
| --- | --- | --- |
| Repository XLSX | Recognize this workbook's sheets and explicit input cells; read values without altering the original | General column mapping and workbook variants |
| CSV/TSV | Document a canonical schema; preserve stable player numbers and typed results | Configurable field mapping, locale/delimiter variants |
| TRF16 | Parse supported fixed-width individual player records and round codes; reject unsupported records that affect pairing | Full legacy extensions and export |
| TRF26 | Detect and retain version information; report unsupported features | Implement current records and codes from the official specification before claiming support |
| US Chess DBF | Research and document the three-file relationship | Parser, fixtures and verified field mapping |
| Organizer HTML | Parse supported table shapes into a preview | Site-specific adapters, pagination, layout drift fixtures |
| PDF/image/text | Upload and review workflow | Extraction, row evidence, identity reconciliation and quality checks |

This table is an implementation plan, not a claim that every adapter already exists. The README and UI should list only what the shipped parser actually supports.

### Official-source automation

US Chess API v2 is available with API-key access. The official developer discussion describes an initial focus on published ratings and membership; tournament/crosstable access may require higher-tier access, an agreement, and fees. Old MSA and API v1 access should not be treated as permanent. [Official API discussion](https://forum.uschess.org/t/api-2-0-is-now-available/61581).

US Chess announced on **November 11, 2026** that API limits were temporarily lowered because of third-party load, with a data usage policy and agreement forthcoming. Build explicit fetches, caching, throttling, retry/backoff, and uploaded-export fallback. Do not promise unrestricted bulk crawling. [US Chess API limits announcement](https://new.uschess.org/news/us-chess-api-limits-lowered).

FIDE rating reports are submitted after events by arbiters and federation rating officers. Their existence does not establish a live next-round-results feed or unrestricted public TRF download endpoint. Automatic onboarding should distinguish an official rated-event archive from an organizer's in-progress results page. [FIDE rating-report procedures, section 9](https://handbook.fide.com/chapter/B022024).

For URL fetching, keep approved provider hosts and supported paths explicit; validate redirects, bound response size and time, and reject local/private-network destinations. Preserve the exact URL and fetched document before parsing. Do not automatically fetch arbitrary links discovered inside untrusted result pages.

### Canonical records and reconciliation

Store the following independently:

- **Tournament:** name, section, total rounds, scoring scheme, pairing system/edition, initial color, acceleration and director settings.
- **Player:** stable internal ID, original pairing number, external federation IDs, entered pairing rating, name, status, entry round, and requested byes.
- **Round result:** round number, opponent ID or absence reason, assigned color if known, actually-played flag, outcome type, and awarded points.
- **Provenance:** source URL/file, format and parser version, retrieval timestamp, original record/cell locator, raw document hash, and reviewed corrections.

Do not collapse a played win, no-show win, full-point pairing bye, requested half-point bye, and zero-point absence into the same result. Awarded points affect standings; played status affects color history and potentially opponent/bye eligibility. Keep actual and virtual pairing points separate if acceleration is later supported.

Check totals against per-round results, reciprocal games against both players' records, round cutoffs against future or partial results, and external-ID collisions against section membership. A missing color, pairing number, or pre-event rating is an input issue, not permission to infer one from standings. Reviewable source corrections should survive subsequent imports through stable identity keys.

## Validation target

To establish useful prediction accuracy, backtest against published next-round pairings while truncating each event to the results and settings known before that round. Avoid using post-event ratings, future withdrawals, final standings order, or later corrections as historical input. Report opponent accuracy separately from color accuracy, excluded events, missing-input rates, search-limit events, and rule/profile mismatches. No numeric accuracy claim is supported by this research alone.
