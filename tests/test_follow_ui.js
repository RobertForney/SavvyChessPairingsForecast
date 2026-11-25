// Follow the real UI state machine without requiring a browser dependency.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../web/follow.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };

function harness({ offline = false, sourceUrl = '', followStatus = false } = {}) {
  const callbacks = {}, windowCallbacks = {}, calls = [], timers = new Map();
  const root = { addEventListener: (name, callback) => { callbacks[name] = callback; } };
  const liveNodes = { '#follow-background-status': { textContent: '' }, '[data-follow-action="review-update"]': { hidden: true } };
  const initial = { name: '<script>Event</script>', section: '', rounds: 50, completedRounds: 0, players: [],
    tracking: { createdAt: '2026-11-20T05:00:00Z', profile: 'unknown', roundsKnown: false, sourceUrl, playerQuery: '', firstRoundColor: null, startingOrderKnown: false, schedulePolicy: 'unknown' } };
  const globalState = { tournament: copy(initial), rules: { profile: 'us-chess' }, view: 'follow', offline, forecast: { status: 'blocked', pairings: [] } };
  if (followStatus) globalState.followStatus = { readiness: { ready: false, issues: ['Confirm the pairing system.'] } };
  const responseTournament = { ...copy(initial), completedRounds: 1, players: [{ id: 'one', name: 'Player <One>', results: [{ round: 1, result: 'W', opponentId: 'two', color: 'W' }] }, { id: 'two', name: 'Other', results: [{ round: 1, result: 'L', opponentId: 'one', color: 'B' }] }] };
  let renders = 0, accepts = 0, failPath = '', failStatus, timerId = 0;
  class TestFormData {
    constructor(form) { this.values = form.values; }
    get(key) { return this.values[key] ?? null; }
    has(key) { return Object.hasOwn(this.values, key) && this.values[key] !== false; }
  }
  const context = {
    window: { location: { hash: '#follow' }, addEventListener: (name, callback) => { windowCallbacks[name] = callback; } },
    document: { querySelector: selector => selector === '#follow-workspace' ? root : liveNodes[selector] },
    FormData: TestFormData, Date, console, setInterval: callback => { const id = ++timerId; timers.set(id, callback); return id; }, clearInterval: id => timers.delete(id)
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  const follow = context.window.SavvyFollow;
  follow.configure({
    getAppState: () => globalState,
    renderApp: () => { renders++; void follow.ensureLoaded(); },
    acceptState: data => { accepts++; Object.assign(globalState, copy(data)); }, toast: () => {},
    request: async (url, options = {}) => {
      calls.push({ url, options });
      if (url === failPath) { const error = new Error('Server says <retry>'); error.status = failStatus; throw error; }
      if (url === '/api/follow/status') return { tracking: globalState.tournament.tracking, readiness: { ready: false, issues: ['Confirm <system>'], completedRounds: globalState.tournament.completedRounds } };
      if (url === '/api/import') return { tournament: copy(responseTournament) };
      if (url === '/api/follow/preview') return { previewId: 'preview-1', baseHash: 'before', summary: { addedPlayers: 2, addedResults: 2, updatedResults: 0, corrections: [{ name: 'A <player>', round: 1 }], conflicts: [], warnings: ['Check <colors>'] }, tournament: copy(responseTournament) };
      if (url === '/api/follow/apply') return { tournament: { ...copy(responseTournament), tracking: copy(globalState.tournament.tracking) }, forecast: { status: 'ok', pairings: [] } };
      if (url === '/api/follow/new') return { tournament: { ...copy(initial), name: options.body.name, section: options.body.section, tracking: { ...copy(initial.tracking), ...options.body } }, forecast: { status: 'blocked', pairings: [] } };
      if (url === '/api/follow/settings') return { tournament: { ...copy(globalState.tournament), name: options.body.name, tracking: { ...copy(globalState.tournament.tracking), ...options.body } } };
      throw new Error(`Unexpected URL: ${url}`);
    }
  });
  follow.bind();
  const form = (id, values, kind) => ({ id, values, dataset: { followForm: kind }, reportValidity: () => true });
  const submit = (id, values, kind) => callbacks.submit({ target: form(id, values, kind), preventDefault: () => {} });
  const click = action => callbacks.click({ target: { closest: () => ({ disabled: false, dataset: { followAction: action } }) } });
  const change = (id, value, checked, files) => callbacks.change({ target: { id, value, checked, files, closest: () => null } });
  const input = (id, value) => callbacks.input({ target: { id, value, closest: () => null } });
  return { follow, globalState, calls, timers, click, change, input, submit, context, liveNodes, renders: () => renders, accepts: () => accepts, fail: (url, status) => { failPath = url; failStatus = status; }, responseTournament };
}

(async () => {
  const h = harness();
  let html = h.follow.render();
  assert.match(html, /Follow the next pairing/);
  assert.match(html, /Not known yet/);
  assert.match(html, /No opponent list is needed to start following/);
  assert.match(html, /&lt;script&gt;Event&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>Event/);
  assert.match(html, /<strong>Unknown<\/strong><span>Total rounds/);
  assert.doesNotMatch(html, /value="us-chess" selected/);
  assert.doesNotMatch(html, /value="W" selected/);
  assert.match(html, /name="acceleration"[^>]*><option value="unknown" selected>Not known yet/);
  await h.follow.ensureLoaded();
  await settle();
  assert.equal(h.calls.filter(c => c.url === '/api/follow/status').length, 1, 'status render must not loop');
  assert.match(h.follow.render(), /Confirm &lt;system&gt;/);

  h.click('new');
  h.submit('follow-new', { name: 'Any club event', section: '', playerQuery: '12345678', sourceUrl: '', rounds: '', profile: 'unknown', firstRoundColor: '', schedulePolicy: 'unknown', mergeRound: '' }, 'new');
  await settle();
  const creation = h.calls.find(c => c.url === '/api/follow/new');
  assert.equal(creation.options.body.rounds, null);
  assert.equal(creation.options.body.firstRoundColor, null);
  assert.equal(creation.options.body.profile, 'unknown');
  assert.equal(creation.options.body.startingOrderKnown, false);
  assert.equal(creation.options.body.schedulePolicy, 'unknown');
  assert.equal(creation.options.body.acceleration, 'unknown');
  assert.equal(creation.options.body.playerQuery, '12345678');
  assert.ok(!Object.hasOwn(creation.options.body, 'opponentId'));
  assert.equal(h.accepts(), 1);

  h.input('follow-check-url', 'https://ratings.uschess.org/event/example');
  h.submit('follow-source-form', { url: 'https://ratings.uschess.org/event/example' });
  await settle();
  assert.equal(h.accepts(), 1, 'checking results must never apply them');
  html = h.follow.render();
  assert.match(html, /Review the new standings/);
  assert.match(html, /Check &lt;colors&gt;/);
  assert.match(html, /A &lt;player&gt;: round 1 changes/);
  assert.match(html, /2<\/strong><span>New \/ updated round records/);
  assert.equal(h.calls.filter(c => c.url === '/api/follow/apply').length, 0);
  h.click('apply');
  await settle();
  assert.equal(h.accepts(), 2);
  assert.equal(h.calls.filter(c => c.url === '/api/follow/apply').length, 1);
  assert.equal(h.calls.find(c => c.url === '/api/follow/apply').options.body.previewId, 'preview-1');
  assert.equal(h.globalState.tournament.tracking.playerQuery, '12345678');

  h.submit('follow-paste-form', { text: 'name,rating\nA,1800' });
  await settle();
  assert.equal(h.calls.at(-1).options.body.filename, 'standings.csv');
  h.click('discard');
  h.change('follow-file', '', false, [{ name: 'standings.csv', size: 18, arrayBuffer: async () => new ArrayBuffer(18) }]);
  await settle();
  const imported = h.calls.find(c => c.url === '/api/import');
  assert.equal(imported.options.headers['X-Filename'], 'standings.csv');
  assert.equal(h.calls.at(-1).url, '/api/follow/preview');
  assert.equal(h.accepts(), 2, 'uploads must stay in review until applied');
  h.fail('/api/follow/apply', 409);
  h.click('apply');
  await settle();
  assert.match(h.follow.render(), /Server says &lt;retry&gt;/);
  assert.doesNotMatch(h.follow.render(), /Review the new standings/);

  const auto = harness({ sourceUrl: 'https://ratings.uschess.org/event/example', followStatus: true });
  auto.change('follow-auto-check', '', true);
  assert.equal(auto.timers.size, 1);
  const rendersBefore = auto.renders();
  [...auto.timers.values()][0]();
  await settle();
  assert.equal(auto.calls.filter(c => c.url === '/api/follow/preview').length, 1);
  assert.equal(auto.calls.filter(c => c.url === '/api/follow/apply').length, 0);
  assert.equal(auto.renders(), rendersBefore, 'background refresh must not replace unsaved form DOM');
  assert.equal(auto.liveNodes['[data-follow-action="review-update"]'].hidden, false);
  [...auto.timers.values()][0]();
  await settle();
  assert.equal(auto.calls.length, 1, 'an existing review must not be replaced by polling');
  auto.globalState.offline = true;
  [...auto.timers.values()][0]();
  assert.equal(auto.timers.size, 0);
  assert.equal(auto.calls.length, 1);

  const leave = harness({ sourceUrl: 'https://ratings.uschess.org/event/example', followStatus: true });
  leave.change('follow-auto-check', '', true);
  leave.context.window.location.hash = '#forecast';
  leave.globalState.view = 'forecast';
  leave.follow.onViewChange('forecast');
  assert.equal(leave.timers.size, 0);

  const stopped = harness({ offline: true });
  stopped.click('new');
  stopped.submit('follow-source-form', { url: 'https://ratings.uschess.org/event/example' });
  await stopped.follow.ensureLoaded();
  await settle();
  assert.equal(stopped.calls.length, 0);
  assert.match(stopped.follow.render(), /Saved view · connection required/);

  const focus = harness({ followStatus: true });
  focus.globalState.tournament.players = copy(focus.responseTournament.players);
  focus.globalState.tournament.tracking.focusPlayerId = 'one';
  focus.globalState.forecast = { status: 'ok', pairings: [{ white: { id: 'one', name: 'Player <One>' }, black: { id: 'two', name: 'Other' } }] };
  assert.match(focus.follow.render(), /current candidate pairs you with Other as White/);
  focus.globalState.forecast.orientationUnconfirmed = true;
  assert.match(focus.follow.render(), /under the selected White color hypothesis/);
  assert.doesNotMatch(focus.follow.render(), /Other as White/);
  focus.globalState.stale = true;
  assert.doesNotMatch(focus.follow.render(), /current candidate pairs you/);

  const acceleration = harness({ followStatus: true });
  acceleration.submit('follow-settings', { name: 'Declared Baku event', rounds: '7', profile: 'fide-dutch', acceleration: 'baku', schedulePolicy: 'single' }, 'settings');
  await settle();
  const savedAcceleration = acceleration.calls.find(call => call.url === '/api/follow/settings');
  assert.equal(savedAcceleration.options.body.acceleration, 'baku');
  assert.match(acceleration.follow.render(), /value="baku" selected>FIDE Baku/);
  assert.match(acceleration.follow.render(), /Baku requires confirmed total rounds and the FIDE Dutch procedure/);

  console.log('Follow UI: unknown setup, source review, explicit apply, polling, offline and focus checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
