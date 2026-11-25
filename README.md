# Savvy Pairings Forecast

A tournament companion for browser, Apple and Android. Follow an event and forecast possible opponents, or run your own local Swiss event with reviewed pairings, issued rounds and recorded results.

## Run

The application and US Chess engine require **Python 3.10 or later**. No Python packages, API keys, JavaScript build or external account are required. FIDE Dutch uses a separately installed, pinned native engine.

```powershell
python -m app.server
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Use `--port 8001` if that port is occupied. Stop with Ctrl+C. The server listens only on loopback.

Choose **Run tournament** to create a casual local event, register players manually or from a CSV roster, review a pairing draft, issue fixed pairings, enter board results and close each round. Completed results drive the next pairing. The mode includes requested byes, between-round withdrawals/late entries, points standings, audited corrections, CSV/printable sheets and JSON backup/restore. It does not submit tournaments or rating reports. See [the local-event workflow](docs/run-tournament.md).

Casual administration does not relax pairing checks. The default is the pinned FIDE Dutch engine. US Chess mode requires review of its documented engine scope. Issuance independently checks current inputs, full rules, engine identity, completed search, board/player coverage, bye parity, opponents, restrictions and applicable colors. Issued assignments, actual inputs, rules and engine provenance are retained. Generic imports and forecast editors cannot rewrite managed rounds. See [the pairing quality boundary](docs/pairing-quality.md).

The local event workflow was exercised through three real native Dutch rounds in the browser, including corrections, backup restore, double forfeits and restart recovery. See [the verification record](docs/local-event-validation.md).

Start in **Follow tournament** with the event name. Section, your identity, standings URL, round count, procedure, starting order and initial color can remain unknown. Add a roster when it appears, select yourself, and confirm settings as the tournament begins. New installations start with an empty event; existing saved tournaments are preserved.

**Check published standings** prepares a reviewable update from a public HTTPS results table or supported export. Apply it to advance the recorded history while keeping your identity and confirmed setup. Changing standings rank does not change player identity or starting numbers. Optional checks every two minutes run only while Follow is open and prepare previews without applying them. Files and pasted tables are alternatives for sites that require exports or authentication. See [the player workflow](docs/player-workflow.md) and [supported live standings](docs/live-standings.md).

**Simulation** is a secondary testing tool for replaying recorded evidence. The original XLSX and a dated official US Chess snapshot remain available there. No later games or final standings generate an earlier pairing. See [replay methodology](docs/simulation.md) and [sample validation](docs/official-data.md).

The responsive interface can be installed as a **PWA** on iPhone, iPad, Mac and Android. Phone access requires a reachable HTTPS deployment and the pairing backend. Cached views are explicitly read only; fresh forecasts require a connection. This build does not include native app-store binaries. See [platform support, installation and deployment](docs/platforms.md).

To enable the FIDE Dutch profile, install the official BBP Pairings 6.0.0 dependency once:

```powershell
python scripts/setup_dutch_engine.py
```

Forecasts run locally after installation. The current pinned package targets Windows x64. The application checks the pinned engine rather than downloading an executable when generating pairings. This workspace already has the verified engine installed in ignored `.local/engines/`.

**Try a complete demo** opens a separate synthetic 12-player tournament. **Restore workbook** explicitly loads the original test source. Both replace the active workspace only when selected.

## What works

- Source XLSX, normalized JSON, CSV/TSV, legacy TRF16 player records and single-section classic US Chess MSA HTML imports.
- Generic standings XLSX sheets, static HTML tables and CSV/TSV with recognizable name, rating, points and round headers; explicit section selection for multi-section sources.
- Player-first event setup, stable focus-player identity, reviewable incremental standings refreshes, explicit missing information and visible forecast assumptions.
- Local event management with separate drafts, immutable issued pairings, pending board results, atomic round closure and audited latest-round reopening.
- Managed-event CSV and printable HTML pairing sheets, points standings with shared ranks, and validated JSON backups with fresh restore identities.
- File, pasted text and explicit source URL imports with a review step before replacing the workspace.
- Player edits, reciprocal game corrections, withdrawals, requested byes, tournament length and round cutoff.
- Separate **US Chess procedural**, **FIDE Dutch**, and **custom matching** engines.
- US Chess descending brackets, natural split-half pairings, odd-player selection, opponent repairs, and the 80/200-point color-switch procedures, including switches involving an odd player's board.
- Chronological color entitlement, conditional half-bye eligibility, reentry identities, and explicit final-round prize-contender exceptions.
- Definitive pairing numbers, assigned pairing ratings, manual per-round virtual points and native FIDE Baku acceleration. Actual standings remain separate from pairing scores.
- Prohibited opponents, locked pairs/colors, requested byes, and soft or hard team restrictions.
- Up to five US/custom candidates, board/procedure explanations and search diagnostics. Dutch returns the engine's deterministic candidate. Candidate ranks are **not probabilities**.
- JSON tournament export, CSV pairing export and local persistence in ignored `.local/state.json`. JSON exports can be imported again.
- XLSX tournament replay with independent pre-merge schedules, merged rounds, provenance, evidence coverage, and opponent/color agreement with explicit denominators.
- Installable mobile web shell, safe-area layouts, saved read-only views, and optional access-key protection for an explicitly configured HTTPS deployment.

## Accuracy and current limits

The **US Chess profile** follows ordered Swiss procedures and is checked against published numerical rulebook examples. It distinguishes mandatory opponent repairs from optional color changes and compares exchanged players' ratings, rather than eventual opponents' rating gaps. Both look-ahead and top-down options are exposed. Complex optional exchange cycles and director discretion can still produce a different official result. See [the implemented US Chess procedure](docs/us-chess-algorithm.md).

The **FIDE Dutch profile** invokes pinned BBP Pairings 6.0.0, whose release implements the Dutch rules effective February 1, 2026. It does not substitute a heuristic when the executable, inputs or supported features are unavailable. See [engine installation, provenance and limits](docs/dutch-engine.md).

The **custom matching profile** retains the earlier additive optimizer for hypothetical restrictions. It is not an implementation of either official pairing procedure. None of these modes constitutes certification of this application or guarantees prediction of undisclosed director decisions.

Manual virtual points support explicit acceleration schedules. FIDE Baku can be selected explicitly in tournament details and is calculated by the native Dutch engine; it cannot be combined with nonzero manual virtual points. The app does not guess the event's accelerator. For reentries, the reviewed active entry determines the carried score. Original-entry colors are not inherited; encounters with an unchanged original opponent remain relevant. Selecting the best-score entry, reconstructing merged schedules, and nonstandard win/draw/loss scoring still require explicit review or additional implementation.

Missing played colors, unresolved games, duplicate round records, contradictory reciprocal results, missing historical participation and unreconciled reported scores block forecasts. Disabling future color limits does not invent historical colors. Unplayed games do not contribute to colors or played-opponent history. Explicitly adjourned games count as draws for pairing and remain provisional. Custom awarded points are supported for pairing-allocated byes; other nonstandard scoring is rejected.

Standings totals alone cannot reconstruct opponents, colors, or game history. An ongoing event with points but no usable history remains trackable and cannot generate a fictitious round-one forecast. Unknown initial colors and procedures are labelled as hypotheses when explicitly used for a forecast. Pending games currently require completion/reconciliation; outcome-scenario probabilities are not implemented. Round robin, knockout and team pairing systems can be tracked with the unsupported-system setting but require additional engines for forecasts.

The supplied workbook has **51 players**, one completely entered round, and partial evidence through round 6. Some scheduled byes conflict with game rows. Many colors and later results are absent. The app retains these gaps and does not treat spreadsheet formula predictions as actual games. See [the workbook audit and import schemas](docs/workbook.md).

The app handles one section of up to 200 players and 50 rounds. US/custom search is bounded at 5 seconds / 500,000 nodes maximum; constrained fields may return a complete candidate with truncated-search diagnostics, or no candidate before the limit. Exhausting the procedural search does not prove a universal optimum over every director-permitted US pairing. Saved forecasts include normalized-input and rules hashes, engine identity, rule edition and generation time.

## Official results onboarding

The Follow workflow can fetch a user-supplied public HTTPS standings page or export. It uses public-address validation, pinned TLS connections, checked redirects, an 8 MB limit and a 30-second freshness cache. Optional two-minute checks run in the open Follow view; there is no unattended server crawler. The older replacement-import tool retains its publisher allowlist and ten-minute cache. Unsupported or JavaScript-only pages require a supported export or connector; records are not guessed.

US Chess API v2 access and crosstable permissions need to be provisioned separately. The federation reduced API limits in November 2026. A future authenticated connector should use cached event-level refreshes, backoff and a documented agreement. Full TRF26 configuration, DBF exports, modern HTML adapters and PDF/OCR mapping remain planned extensions. See [the rule editions, official sources and integration roadmap](docs/rules-and-imports.md).

## Verify

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
node tests/test_simulation_ui.js
node tests/test_workspace_ui.js
node tests/test_follow_ui.js
node tests/test_director_ui.js
node --test tests/test_service_worker.js
```

The tests cover source extraction, incomplete evidence, parser rejection, published US Chess color-switch and odd-player examples, reentries, bye exceptions, virtual points, engine fixtures, a small independently enumerated custom optimum, bounded search, HTTP import/review/export and persistence. Native Dutch integration tests identify when the optional engine is unavailable rather than substituting mocked success.

## Code map

`app/importers.py` normalizes source evidence; `app/validation.py` checks edits; `app/pairing.py` audits history and dispatches engines; `app/us_swiss.py` implements US procedures; `app/dutch.py` adapts the pinned Dutch engine; `app/director.py` owns managed rounds and issuance checks; `app/replay.py` isolates historical forecasts; `app/sources.py` fetches public results; `app/server.py` owns HTTP and persistence. `web/` is a buildless browser/PWA interface. The original XLSX is never overwritten.
