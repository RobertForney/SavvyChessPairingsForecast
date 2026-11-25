// Run the real director UI state machine in a bounded DOM/HTTP harness.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/director.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };

function harness({ offline = false, blocked = false } = {}) {
  const callbacks = {}, calls = [], downloads = [];
  let renders = 0, accepts = 0, fail = '';
  const root = { addEventListener: (name, callback) => { callbacks[name] = callback; } };
  const app = { view: 'director', offline, tournament: { name: 'Following another event', rounds: 5, completedRounds: 0, players: [] }, rules: { profile: 'us-chess', firstRoundColor: 'W', byePoints: 1 } };
  const makeStatus = () => {
    const m = app.tournament.managed;
    if (!m) return { enabled: false };
    const active = m.rounds.find(round => round.status === 'issued');
    return { enabled: true, revision: m.revision, stage: active ? 'in-progress' : 'ready', canGenerate: !active,
      canIssue: m.draft?.quality.canIssue === true, canClose: active ? Object.keys(active.results).length === active.snapshot.pairings.length : false,
      canReopen: !active && !m.draft, standings: app.tournament.players.map(player => ({ ...player, score: (player.results || []).reduce((sum, result) => sum + result.points, 0) })) };
  };
  const response = () => { app.directorStatus = makeStatus(); return copy(app); };
  const context = { window: {}, document: { querySelector: selector => selector === '#director-workspace' ? root : null }, Date, console,
    FormData: class { constructor(form) { this.values = form.values; } get(name) { return this.values[name] ?? null; } has(name) { return Object.hasOwn(this.values, name) && this.values[name] !== false; } } };
  vm.createContext(context);
  vm.runInContext(source, context);
  const director = context.window.SavvyDirector;
  director.configure({ getAppState: () => app, renderApp: () => { renders++; void director.ensureLoaded(); },
    acceptState: data => { accepts++; Object.assign(app, copy(data)); }, toast: () => {},
    download: async (url, filename) => { downloads.push({ url, filename }); },
    request: async (url, options = {}) => {
      calls.push({ url, options: copy(options) });
      if (url === fail) { const error = new Error('Stale <snapshot>. Refresh first.'); error.status = 409; throw error; }
      if (url === '/api/state') return response();
      if (url === '/api/director/status') return makeStatus();
      const body = options.body || {};
      if (url === '/api/director/new') {
        app.tournament = { name: body.name, section: body.section, rounds: body.rounds, completedRounds: 0, players: [], managed: { enabled: true, eventId: 'local-one', revision: 0, rounds: [], draft: null, audit: [], acknowledgeEngineLimits: body.acknowledgeEngineLimits } };
        app.rules = { profile: body.profile, firstRoundColor: body.firstRoundColor, byePoints: 1 };
        return response();
      }
      if (url === '/api/director/restore') { app.tournament = copy(body.tournament); app.rules = copy(body.rules); return response(); }
      const m = app.tournament.managed;
      assert.equal(body.expectedRevision, m.revision, `${url} must carry the current managed revision`);
      assert.equal(body.expectedEventId, m.eventId, `${url} must identify the current managed event`);
      if (url === '/api/director/players') {
        if (!body.id) app.tournament.players.push({ ...body, id: `p${app.tournament.players.length + 1}`, results: [], active: true });
        else Object.assign(app.tournament.players.find(player => player.id === body.id), body);
      }
      if (url === '/api/director/settings') { app.tournament.name = body.name; app.tournament.rounds = body.rounds; }
      if (url === '/api/director/players/remove') app.tournament.players = app.tournament.players.filter(player => player.id !== body.id);
      if (url === '/api/director/draft') {
        m.draft = { id: 'draft-one', round: app.tournament.completedRounds + 1, inputHash: 'input', rulesHash: 'rules',
          forecast: { status: blocked ? 'blocked' : 'ok', engine: 'Pinned procedure', ruleEdition: 'Fixture edition', issues: blocked ? ['Search <incomplete>'] : [], warnings: ['Review <limits>'], pairings: [{ board: 1, white: copy(app.tournament.players[0]), black: copy(app.tournament.players[1]) }], byes: [], requestedByes: [] },
          quality: { canIssue: !blocked, issues: blocked ? ['A complete calculation is required.'] : [], warnings: [] } };
      }
      if (url === '/api/director/issue') {
        assert.equal(body.draftId, m.draft.id);
        m.rounds.push({ number: app.tournament.completedRounds + 1, status: 'issued', snapshot: copy(m.draft.forecast), results: {}, resultRevision: 0, issuedAt: '2026-11-20T12:00:00Z' });
        m.draft = null;
      }
      if (url === '/api/director/results') {
        const round = m.rounds.find(item => item.number === body.round);
        round.results = Object.fromEntries(body.results.filter(result => result.result).map(result => [String(result.board), result.result]));
        round.resultRevision++;
      }
      if (url === '/api/director/close') {
        const round = m.rounds.find(item => item.number === body.round);
        assert.equal(Object.keys(round.results).length, round.snapshot.pairings.length);
        round.status = 'closed'; round.closedAt = '2026-11-20T13:00:00Z'; app.tournament.completedRounds++;
        app.tournament.players[0].results.push({ round: round.number, points: 1 });
        app.tournament.players[1].results.push({ round: round.number, points: 0 });
      }
      if (url === '/api/director/reopen') {
        assert.ok(body.reason);
        m.rounds.at(-1).status = 'issued'; app.tournament.completedRounds--;
        for (const player of app.tournament.players) player.results = [];
      }
      m.revision++;
      return response();
    }
  });
  director.bind();
  const click = (action, attributes = {}) => callbacks.click({ target: { closest: () => ({ disabled: false, dataset: { directorAction: action, ...attributes } }) } });
  const submit = (id, values = {}) => callbacks.submit({ target: { id, values, reportValidity: () => true }, preventDefault: () => {} });
  const input = (id, value) => callbacks.input({ target: { id, value, closest: () => null } });
  const result = (board, value) => callbacks.change({ target: { value, dataset: { directorBoard: String(board) }, hasAttribute: name => name === 'data-director-board' } });
  const file = value => callbacks.change({ target: { id: 'director-restore-file', files: [value], hasAttribute: () => false } });
  return { director, app, calls, downloads, click, submit, input, result, file, response, renders: () => renders, accepts: () => accepts, fail: path => { fail = path; } };
}

async function start(h, settings = {}) {
  h.submit('director-new', { name: 'Club <Open>', section: 'Local', rounds: '3', profile: 'fide-dutch', firstRoundColor: 'W', ...settings });
  await settle();
  h.submit('director-player', { name: 'Ada <One>', rating: '1900', team: '' }); await settle();
  h.submit('director-player', { name: 'Ben', rating: '1800', team: '' }); await settle();
}

(async () => {
  const h = harness();
  let html = h.director.render();
  assert.match(html, /Run your tournament/);
  assert.match(html, /value="fide-dutch" selected/);
  assert.match(html, /does not submit/);
  assert.match(html, /replaces the active workspace/);
  assert.equal(h.calls.length, 0, 'rendering must never create an event');
  await start(h);
  html = h.director.render();
  assert.match(html, /Club &lt;Open&gt;/);
  assert.match(html, /Ada &lt;One&gt;/);
  assert.doesNotMatch(html, /Ada <One>/);
  h.click('draft'); h.click('draft'); await settle();
  assert.equal(h.calls.filter(call => call.url === '/api/director/draft').length, 1, 'rapid repeat draft actions must be ignored');
  assert.equal(h.calls.filter(call => call.url === '/api/director/issue').length, 0, 'draft generation must not issue a round');
  assert.match(h.director.render(), /Draft ready for director review/);
  assert.match(h.director.render(), /Review &lt;limits&gt;/);
  h.app.directorStatus.canIssue = false;
  assert.match(h.director.render(), /data-director-action="issue" disabled/, 'fresh server quality must override a once-issuable stored draft');
  h.app.directorStatus.canIssue = true;
  h.click('issue'); await settle();
  assert.match(h.director.render(), /Round 1 · issued/);
  assert.match(h.director.render(), /value="DOUBLE_FORFEIT">0–0 · Both players forfeit/);
  assert.doesNotMatch(h.director.render(), /Generate pairing draft/);
  const issued = JSON.stringify(h.app.tournament.managed.rounds[0].snapshot);
  h.result(1, 'W');
  assert.match(h.director.render(), /Unsaved board results/);
  const beforeClose = h.calls.length;
  h.click('close'); await settle();
  assert.equal(h.calls.length, beforeClose, 'unsaved results must prevent closing');
  assert.deepEqual(h.app.tournament.managed.rounds[0].results, {}, 'typing results must not change server state');
  h.submit('director-results'); await settle();
  assert.equal(h.calls.at(-1).options.body.results[0].result, 'W');
  assert.equal(JSON.stringify(h.app.tournament.managed.rounds[0].snapshot), issued, 'result entry must preserve issued snapshot');
  assert.equal(h.app.tournament.completedRounds, 0, 'saving results is separate from closing the round');
  h.click('download', { format: 'print', round: '1' }); await settle();
  assert.deepEqual(h.downloads[0], { url: '/api/director/print?round=1', filename: 'round-1-pairings.html' });
  h.click('close'); await settle();
  assert.equal(h.app.tournament.completedRounds, 1);
  assert.match(h.director.render(), /Issued round history/);
  assert.match(h.director.render(), /Reopen round 1/);
  h.submit('director-reopen', { reason: 'Correct a mistaken result' }); await settle();
  assert.equal(h.calls.at(-1).options.body.reason, 'Correct a mistaken result');
  assert.equal(h.app.tournament.completedRounds, 0);
  assert.equal(JSON.stringify(h.app.tournament.managed.rounds[0].snapshot), issued);

  const blocked = harness({ blocked: true }); await start(blocked);
  blocked.click('draft'); await settle();
  html = blocked.director.render();
  assert.match(html, /Draft cannot be issued/);
  assert.match(html, /Search &lt;incomplete&gt;/);
  assert.match(html, /data-director-action="issue" disabled/);

  const backup = { schemaVersion: 1, kind: 'savvy-managed-event', tournament: copy(h.app.tournament), rules: copy(h.app.rules) };
  h.file({ name: 'club-backup.json', size: 1500, text: async () => JSON.stringify(backup) }); await settle();
  assert.match(h.director.render(), /Review backup: Club &lt;Open&gt;/);
  assert.match(h.director.render(), /<details class="panel separated-panel" open><summary class="panel-body">Restore a saved local-event backup/, 'choosing a backup must reveal its review without reopening the disclosure');
  assert.equal(h.calls.filter(call => call.url === '/api/director/restore').length, 0, 'selecting a backup must not restore it');
  h.click('restore'); await settle();
  assert.equal(h.calls.at(-1).url, '/api/director/restore');
  assert.ok(Number.isInteger(h.calls.at(-1).options.body.expectedRevision), 'replacing a managed event must be revision checked');
  assert.equal(h.calls.at(-1).options.body.expectedEventId, h.app.tournament.managed.eventId, 'replacing an event must protect against same-revision event changes');
  assert.doesNotMatch(h.director.render(), /Review backup:/);
  h.file({ name: 'club-backup.json', size: 1500, text: async () => JSON.stringify(backup) }); await settle();
  h.file({ name: 'wrong.json', size: 10, text: async () => '{bad json' }); await settle();
  assert.doesNotMatch(h.director.render(), /Review backup:/, 'a failed replacement-file selection must not leave an old restore preview active');
  assert.match(h.director.render(), /not a valid JSON event backup/);

  const offline = harness({ offline: true });
  offline.submit('director-new', { name: 'Offline', rounds: '3' });
  offline.click('draft'); await settle();
  assert.equal(offline.calls.length, 0, 'offline actions must not be queued or sent');
  assert.match(offline.director.render(), /Saved event · read only/);

  const failed = harness(); await start(failed); failed.fail('/api/director/draft'); failed.click('draft'); await settle();
  assert.match(failed.director.render(), /Stale &lt;snapshot&gt;/);
  assert.doesNotMatch(failed.director.render(), /Stale <snapshot>/);
  assert.match(failed.director.render(), /Reload saved event/);
  assert.match(failed.director.render(), /If another action or tab changed this event/);
  failed.fail(''); failed.click('reload'); await settle();
  assert.equal(failed.calls.at(-1).url, '/api/state');
  assert.doesNotMatch(failed.director.render(), /Stale &lt;snapshot&gt;/);

  const lockedSettings = harness(); await start(lockedSettings, { profile: 'us-chess', firstRoundColor: 'B', acknowledgeEngineLimits: true });
  lockedSettings.click('draft'); await settle(); lockedSettings.click('issue'); await settle();
  lockedSettings.result(1, 'D'); lockedSettings.submit('director-results'); await settle(); lockedSettings.click('close'); await settle();
  // Disabled selects are omitted by browser FormData; editing event length must
  // preserve the confirmed US procedure and initial Black color draw.
  lockedSettings.submit('director-settings', { name: 'Longer local event', section: 'Local', rounds: '4', acknowledgeEngineLimits: true }); await settle();
  const settingsCall = lockedSettings.calls.findLast(call => call.url === '/api/director/settings');
  assert.equal(settingsCall.options.body.profile, 'us-chess');
  assert.equal(settingsCall.options.body.firstRoundColor, 'B');
  assert.equal(settingsCall.options.body.rounds, 4);
  lockedSettings.app.tournament.rounds = lockedSettings.app.tournament.completedRounds;
  let completedHtml = lockedSettings.director.render();
  assert.match(completedHtml, /<td>Event complete<\/td>/, 'finishing the scheduled event does not withdraw its active players');
  assert.doesNotMatch(completedHtml, /<td>Withdrawn<\/td>/);
  assert.match(completedHtml, /id="director-player-name"[^>]* disabled/);
  assert.match(completedHtml, /data-director-action="edit-player"[^>]* disabled/);
  assert.match(completedHtml, /Extend the number of rounds in event settings/);
  lockedSettings.app.tournament.players[1].active = false;
  completedHtml = lockedSettings.director.render();
  assert.match(completedHtml, /<td>Withdrawn<\/td>/, 'an actual withdrawal stays distinct from event completion');
  lockedSettings.app.tournament.rounds++;
  assert.doesNotMatch(lockedSettings.director.render(), /id="director-player-name"[^>]* disabled/, 'extending the event enables the next-round roster controls');

  const forfeits = harness(); await start(forfeits);
  forfeits.click('draft'); await settle(); forfeits.click('issue'); await settle();
  forfeits.result(1, 'DOUBLE_FORFEIT'); forfeits.submit('director-results'); await settle();
  assert.equal(forfeits.calls.at(-1).options.body.results[0].result, 'DOUBLE_FORFEIT');
  assert.equal(forfeits.app.tournament.managed.rounds[0].results['1'], 'DOUBLE_FORFEIT');
  console.log('Director UI: creation, drafts, fixed issuance, results, close/reopen, backups, revision guards and offline checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
