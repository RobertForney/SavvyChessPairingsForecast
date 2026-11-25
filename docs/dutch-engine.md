# Native FIDE Dutch pairing

The `fide-dutch` profile executes **BBP Pairings 6.0.0**, released February 1,
2026. This release implements the Dutch rules whose effective date moved from
2025 to February 2026. Its bracket and color procedures replace the application's
generic optimizer in this mode. Selecting this mode never silently falls back to
an approximate forecast.

## Reproduce the engine installation

```powershell
python scripts/setup_dutch_engine.py
```

The installer uses the existing verified archive when available. Otherwise it
downloads the exact official Windows x64 release into the ignored
`.local/engines/bbp-6.0.0/` directory. It installs no global software. The archive
and executable must match the sizes and SHA-256 hashes pinned in
`app/engine-manifest.json`. Every availability check verifies the executable
again before it can be used. An unavailable or modified executable blocks native
Dutch mode while leaving the other profiles usable.

Pinned provenance:

| Item | Value |
| --- | --- |
| Official release | [v6.0.0](https://github.com/BieremaBoyzProgramming/bbpPairings/releases/tag/v6.0.0) |
| Full source commit | `16a000f9811de322b0e835d5643198226165b5a9` |
| Windows x64 archive SHA-256 | `d2bfc61cbd291a5458f18adccb43b19b6f1be40a9c0cc86a5142b605298dc0d7` |
| Native executable SHA-256 | `1c9ce6a8538559c10d8b953399b3c712bf2b780756af8af9881bec0769cca9ac` |
| Rule edition | FIDE Dutch, effective February 1, 2026 |

The source is Apache-2.0. The official Windows build adds its compiler/runtime
notices in `LICENSE.txt`. The setup retains that file, `Apache-2.0.txt` and
`README.txt` alongside the executable. Copies of the relevant notices are in
`docs/licenses/`. No certification claim follows from using this engine.

## Input fidelity

The adapter first runs the shared history audit. It then writes the native
engine's supported TRF(bx) exchange form and invokes an argument array with
`--dutch input.trf -p pairings.txt` in an isolated temporary directory. Execution
has a bounded timeout. Names are replaced with internal labels in the temporary
file; IDs, original TPN order, pairing ratings and actual history are preserved.

- Original **tournament pairing numbers** take priority over current rating or
  standings order. An explicit `pairingNumber`, otherwise `seed`, is used.
  Consecutive numeric player IDs are accepted with a visible verification note.
  A completely new event with no numbers can initialize TPNs from reviewed
  pairing-rating order, also with a visible note.
- The full original roster must provide consecutive TPNs `1..N`, including
  withdrawn players. Gaps are rejected rather than compacted: parity affects the
  final color-allocation tiebreak. Non-numeric IDs after play begins require
  explicit TPNs.
- Initial color is always explicit through `XXC white1` or `XXC black1`; it is
  never inferred from an arbitrary latest round.
- Completed games, adjourned draws, forfeits, double forfeits, half-point byes,
  zero-point absences and pairing-allocated byes retain their different meanings.
  Unplayed wins/losses use the documented forfeit codes so they do not create
  played color or repeat-opponent history.
- Requested next-round byes and withdrawals are represented as future absence
  records. Future requested points are included only in the exchange file's
  displayed score, as required by BBP; canonical actual scores remain unchanged.
- The configured pairing-allocated bye award must be constant across the event,
  including past pairing-allocated byes. A recipient of any earlier PAB remains
  ineligible for another even if the award was zero or half a point.
- Reviewed per-player `virtualPoints` are exported through documented `XXA`
  records. They affect pairing scores, never actual standings.
- Standard **Baku acceleration** is supported through `acceleration: "baku"`
  or `acceleration: {"method": "baku"}`. The adapter writes the native
  `192 FIDE_DUTCH_2025_BAKU` selection and lets BBP calculate its round-dependent
  virtual points from original TPNs and tournament length. It does not imitate
  that algorithm in the application. Combining Baku with nonzero manual virtual
  points or extra Baku configuration overrides is rejected. Explicit zero-only
  manual entries are omitted so they cannot accidentally disable native Baku.
  The selected method is retained in `engineDetails.acceleration`. Displayed
  player scores remain actual standings; a native Baku `pairingScore` is omitted
  because the adapter does not obtain those internal virtual scores.
- Explicit prohibited pairs and mandatory same-team separation use documented
  `XXP` restrictions. They are director restrictions, not implied FIDE defaults.
  A soft team preference does not alter the official Dutch priorities.

Native Dutch mode always uses the engine's official color, float and bye
criteria, including the final-round topscorer rules. US Chess color-switch
limits, heuristic color limits, permission to repeat a bye or opponent, and
alternative-candidate counts do not change the native calculation.

## Explicitly unsupported inputs

The adapter rejects forced pairings/color locks, US Chess reentry/reset links,
free `initialScore` adjustments, automatic acceleration other than standard Baku and
nonstandard win/draw/loss scoring. The native release itself does not support
TRF26 free points or adjustable adjourned-game awards.

A draw awarded without play against a named opponent is also rejected. The
verified BBP 6.0.0 parser treats a colored `D` record as played and rejects `D`
with no color against a named opponent. The adapter will not change such a draw
into a played game or invent a color to make the file pass. Supply the correctly
reviewed outcome or use the explicitly approximate profile.

These are exchange boundaries of this integration, rather than claims that
FIDE rules prohibit every such tournament arrangement.

## Independent checks and regression evidence

Native output is checked for complete one-time coverage, field parity, unknown
IDs, self-pairings, unavailable participants, prohibited pairs, repeat played
encounters, ineligible bye recipients and color-limit violations outside the
final-round topscorer exception. Only then is a single native candidate exposed.
The result records native version, source commit, executable digest and input
TRF SHA-256, making a prediction reproducible for the recorded inputs.

`tests/test_dutch.py` runs the real executable when installed. Native cases
include even first rounds in both initial colors, withdrawals/requested byes,
half-point pairing byes and repeat eligibility, manual acceleration, standard
Baku across initial and later rounds with unchanged actual standings, prohibited
pairs, double forfeits and genuine infeasibility. It also reproduces the upstream
**2026 C5 and C9 fixtures**, including their exact ordered pairings and bye
recipients. These cases distinguish the current Dutch rules from simply giving
the bye to the lowest-rated eligible player.

Tests are skipped explicitly when the pinned native build is absent, so a test
run on another computer cannot imply native verification it did not perform.
Exchange validation and unavailable-engine tests still run without it.

Sources:

- [Pinned BBP README](https://github.com/BieremaBoyzProgramming/bbpPairings/blob/v6.0.0/README.txt).
- [Pinned TRF parser](https://github.com/BieremaBoyzProgramming/bbpPairings/blob/v6.0.0/src/fileformats/trf.cpp).
- [Upstream C5 and C9 fixtures](https://github.com/BieremaBoyzProgramming/bbpPairings/tree/v6.0.0/test/tests).
- [Current FIDE Dutch rules](https://handbook.fide.com/chapter/C0403202602).
