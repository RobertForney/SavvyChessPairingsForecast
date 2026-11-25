# Independent live workflow verification

The operational browser test used an unrelated four-player **Metro Weekend Open** fixture and an isolated state file. It did not load Louisiana data or later-round observed pairings.

1. Opened Follow with no roster, section, round count, procedure, or opponents. Saved an event name and a player query while other details remained unknown.
2. Previewed and applied a zero-point roster. The preview left the workspace unchanged until application.
3. Confirmed a US Chess Swiss procedure, five rounds and one pairing pool; selected Avery Stone in the imported roster. Original numbers and initial color stayed explicitly unconfirmed.
4. Supplied only round-1 results. The live workspace advanced to one reviewed round and forecast round 2, identifying Blair Chen as Avery's possible opponent before any round-2 results were supplied.
5. Supplied round-2 standings with changed published ranks. Stable identities, earlier opponents and the selected player were preserved. The workspace forecast round 3, identifying Devon Park as Avery's possible opponent without receiving any round-3 pairings.
6. Refreshed the same source to confirm no duplicate players/results or stale roster-only warnings. Confirmed acceleration as none. Browser console checks returned no errors or warnings.

The new Follow layout was also checked at 375 CSS pixels: no page-level horizontal overflow, six navigation targets approximately 57 by 60 pixels, and 16px form controls. The desktop forecast proof is saved in ignored `.local/live-player-forecast.jpg`. These are browser checks, not physical-phone installation tests.

Automated HTTP tests additionally exercise two mocked public HTML snapshots, saved source URLs, stale-preview rejection, missing colors, partial updates, and unsupported formats. Source safety tests retain public DNS validation for general HTTPS publishers. Generic XLSX fixtures verify ordinary standings sheets and multi-section rejection. The browser test used pasted export snapshots; it does not establish compatibility with every live publishing site.

All 261 Python tests passed after the change, together with Follow, workspace and replay JavaScript suites. The service worker's independent tests cover network-only APIs and shell-only caching. The live and replay paths now use the same schedule orchestration, including arbitrary schedule/merge fixtures and native Dutch phase tests.
