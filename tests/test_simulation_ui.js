// Exercise replay UI state transitions without a DOM dependency or a live server.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../web/simulation.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness({ offline = false, cached = null, fail = false } = {}) {
  const callbacks = {};
  const calls = [];
  const storage = new Map(cached ? [['savvy-chess-replay-v1', cached]] : []);
  const tournament = { name: '<script>source</script>', rounds: 3, players: [{ id: '1', name: '<img src=x>', results: [{ round: 1, result: 'W' }] }, { id: '2', name: 'Two', results: [] }] };
  const globalState = { tournament: JSON.parse(JSON.stringify(tournament)), rules: { profile: 'us-chess', firstRoundColor: 'W', usColorEqualizationLimit: 200 }, offline };
  const root = { addEventListener: (name, callback) => { callbacks[name] = callback; } };
  const context = { window: {}, localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) }, document: { querySelector: () => root }, Date, console };
  vm.createContext(context);
  vm.runInContext(code, context);
  let renders = 0;
  const mockReplay = body => ({
    throughRound: body.throughRound, targetRound: body.throughRound + 1,
    sourceSummary: { name: tournament.name, players: 2, rounds: 3, warnings: [], assumptions: [], provenance: {} },
    tournament: { players: tournament.players },
    forecast: { status: body.throughRound >= 2 ? 'blocked' : 'ok', blockingReason: body.throughRound >= 2 ? 'history' : null, pairings: [], issues: body.throughRound >= 2 ? ['Missing result for <img src=x>'] : [], requestedByes: [], byes: [], rules: body.rules, scheduleContext: { mode: body.throughRound ? 'merged' : 'separate', mergeRound: 2 }, warnings: [] },
    roundStatus: { issues: [] },
    coverage: [{ round: 1, complete: true, recordedPlayers: 2, expectedPlayers: 2, missingPlayers: [], unresolvedPlayers: [], unknownColorPlayers: [], duplicatePlayers: [] }],
    comparison: { boards: [], observedByes: body.source === 'official' ? [{ playerId: '1', name: '<img src=x>', result: 'ZERO', sourceCode: 'U', points: 0, match: null, evidenceStatus: 'availability-unknown', availabilityUnknown: true }] : [], notes: [], observedBoards: 0, comparableBoards: 0, opponentMatches: 0, opponentCompared: 0, opponentRate: null, colorMatches: 0, colorCompared: 0, colorRate: null }
  });
  const simulation = context.window.SavvySimulation;
  simulation.configure({
    request: async (url, options = {}) => {
      calls.push({ url, options });
      if (fail) throw new Error('Server unavailable');
      if (url === '/api/sample') return { metadata: { filename: 'original.xlsx', name: tournament.name, players: 2, rounds: 3, sha256: 'abc', completedRounds: 1 }, tournament };
      if (url === '/api/official') return { metadata: { filename: 'official.json', name: tournament.name, sourceKind: 'official-frozen-snapshot', players: 2, rounds: 3, sha256: 'official-hash', completedRounds: 3, capturedAt: '2026-11-20T03:39:34.260Z' }, tournament };
      if (url === '/api/import') return { tournament };
      if (url === '/api/simulation') return mockReplay(options.body);
      throw new Error(`Unexpected request: ${url}`);
    },
    getAppState: () => globalState,
    renderApp: () => { renders++; simulation.ensureLoaded(); },
    toast: () => {}, download: () => {}
  });
  function click(action, round) {
    const button = { disabled: false, dataset: round == null ? { simAction: action } : { simRound: String(round) }, hasAttribute: name => name === 'data-sim-round' && round != null };
    callbacks.click({ target: { closest: () => button } });
  }
  function change(id, value, files) { callbacks.change({ target: { id, value, files } }); }
  return { simulation, calls, storage, globalState, click, change, renders: () => renders };
}

(async () => {
  const h = harness();
  const original = JSON.stringify(h.globalState);
  assert.equal(h.calls.length, 0, 'configure must not initiate requests');
  await h.simulation.ensureLoaded();
  h.simulation.bind();
  assert.deepEqual(h.calls.map(call => call.url), ['/api/sample', '/api/simulation']);
  assert.equal(h.calls[1].options.body.throughRound, 0);
  assert.equal(h.calls[1].options.body.source, 'sample');
  assert.equal(h.renders(), 2, 'render-triggered ensureLoaded must not loop');
  let html = h.simulation.render();
  assert.match(html, /Observed opponent agreement/);
  assert.match(html, /Color agreement on matched boards/);
  assert.match(html, /0 \/ 0 comparable boards/);
  assert.doesNotMatch(html, /0%|100%/);
  assert.match(html, /&lt;script&gt;source&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>source/);
  assert.match(html, /Schedules are paired separately until round 2/);

  h.click('next');
  h.click('next'); // Rapid second action is ignored while awaiting the first request.
  await settle();
  assert.equal(h.calls.filter(call => call.url === '/api/simulation').length, 2);
  assert.equal(h.calls.at(-1).options.body.throughRound, 1);
  assert.match(h.simulation.render(), /Schedules are merged from round 2/);
  h.click('next');
  await settle();
  html = h.simulation.render();
  assert.match(html, /Forecast stopped for review/);
  assert.match(html, /Missing result for &lt;img src=x&gt;/);
  assert.doesNotMatch(html, /0%|100%/);
  assert.equal(JSON.stringify(h.globalState), original, 'replay must not mutate the active tournament or rules');

  h.change('sim-source', 'official');
  await settle();
  assert.equal(h.calls.at(-2).url, '/api/official');
  assert.equal(h.calls.at(-1).options.body.source, 'official');
  assert.equal(h.calls.at(-1).options.body.throughRound, 0);
  assert.ok(!Object.hasOwn(h.calls.at(-1).options.body, 'tournament'), 'the official fixture is selected on the server, not submitted as workspace data');
  html = h.simulation.render();
  assert.match(html, /Official validation snapshot/);
  assert.match(html, /Source snapshot SHA-256/);
  assert.match(html, /saved validation source, not a live refresh/);
  assert.match(html, /U · unavailable\/unspecified/);
  assert.doesNotMatch(html, /ZERO · 0 points/);
  assert.match(html, /&lt;img src=x&gt;/);
  h.click('', 3);
  await settle();
  html = h.simulation.render();
  assert.match(html, /Event replay complete/);
  assert.match(html, /3 rounds revealed/);
  assert.doesNotMatch(html, /Round 4 forecast vs observed pairings|Forecast stopped for review/);
  const officialSaved = harness({ offline: true, cached: h.storage.get('savvy-chess-replay-v1') });
  await officialSaved.simulation.ensureLoaded();
  assert.equal(officialSaved.calls.length, 0);
  assert.match(officialSaved.simulation.render(), /Official validation snapshot/);
  assert.match(officialSaved.simulation.render(), /Read-only saved replay/);

  h.change('sim-source', 'workspace');
  await settle();
  const snapshot = h.calls.at(-1).options.body.tournament;
  h.globalState.tournament.players[0].name = 'Changed later';
  h.click('next');
  await settle();
  assert.equal(h.calls.at(-1).options.body.tournament.players[0].name, snapshot.players[0].name, 'workspace source must remain a frozen snapshot');
  h.change('sim-source', 'upload');
  await settle();
  h.change('sim-file', '', [{ name: 'preview.xlsx', size: 20, arrayBuffer: async () => new ArrayBuffer(20) }]);
  await settle();
  assert.equal(h.calls.at(-1).options.body.source, 'upload');
  assert.equal(h.calls.filter(call => call.url === '/api/import').length, 1);
  assert.ok(h.calls.every(call => !call.url.startsWith('/api/tournament')), 'preview must never save the active workspace');

  const cached = h.storage.get('savvy-chess-replay-v1');
  const saved = harness({ offline: true, cached });
  await saved.simulation.ensureLoaded();
  saved.simulation.bind();
  assert.equal(saved.calls.length, 0);
  assert.match(saved.simulation.render(), /Read-only saved replay/);
  saved.click('next');
  await settle();
  assert.equal(saved.calls.length, 0, 'offline replay cannot advance or calculate');
  saved.globalState.offline = false;
  saved.click('', 2);
  await settle();
  assert.equal(saved.calls.length, 0, 'a restored result cannot fabricate a missing full source');
  assert.match(saved.simulation.render(), /has not been recalculated/);

  const failed = harness({ fail: true });
  await failed.simulation.ensureLoaded();
  assert.match(failed.simulation.render(), /Server unavailable/);
  assert.doesNotMatch(failed.simulation.render(), /aria-busy="true"/);
  console.log('Replay UI: source isolation, cutoffs, honest metrics, escaping, cache and error checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
