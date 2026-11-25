# Browser verification

The original XLSX and the separately captured official US Chess source were tested through the browser UI on November 19, 2026 (America/Chicago). The test used an isolated server state file, not the user's saved workspace:

```powershell
python -m app.server --state-file .local/browser-simulation-test.json
```

The original built-in workbook and an upload of that same XLSX both produced 24 round-1 boards in separate schedules. Revealing round 1 produced 25 boards in the merged round-2 pool. Revealing round 2 blocked round 3 on the actual pending/conflicting records. No simulation changed the workspace or the original file.

The pinned native Dutch profile was also exercised through the browser, before and after the schedule merge, without substituting the US engine.

The official validation source advanced through every cutoff from zero through seven. Every round had 51 records and complete played colors. Under the displayed US Chess procedure, initial White, workbook setup overlay, and unknown future availability, observed opponent agreement was:

| Predicted round | Matched / comparable boards |
| --- | ---: |
| 1 | 16 / 24 |
| 2 | 17 / 25 |
| 3 | 16 / 23 |
| 4 | 12 / 25 |
| 5 | 11 / 23 |
| 6 | 13 / 24 |
| 7 | 6 / 20 |

These are historical agreement counts under explicit assumptions, not calibrated probabilities or proof of exact reproduction of director decisions. Round 2 had 16 matched played boards with comparable colors, all oriented correctly; the seventeenth matched board was a forfeit with no played colors. See [the source, discrepancies and assumptions](official-data.md).

The browser showed the event-complete view after round 7, without offering round 8. Installation guidance opened successfully. A narrow mobile layout had no page-level horizontal overflow, 16px form controls, and five 60px navigation targets; wide result tables scroll inside their containers. This checks responsive behavior, not physical iOS/Android installation.

With the owned backend stopped, reloading restored the cached app shell and the last saved replay. A visible read-only notice appeared, and the profile and advance controls were disabled. Restarting the backend and using Reconnect restored online controls; reloading a replay source recalculated it. API responses and source workbooks were not served from the shell cache. Separate automated tests cover cache boundaries and private-access behavior.

The browser proof image is an ignored local artifact at `.local/browser-tournament-simulation.jpg`. The test server was stopped after verification. Apple/Android installation on a reachable HTTPS deployment and physical-device behavior remain unverified.
