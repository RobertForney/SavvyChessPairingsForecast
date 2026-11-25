// Verify real app.js transport/cache behavior in a small browser-like VM.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
const SNAPSHOT_KEY = 'savvy.workspace.snapshot.v1';
const ACCESS_KEY = 'savvy.workspace.access-key';
const settle = () => new Promise(resolve => setImmediate(resolve));

function response(status, body, headers = {}) {
  const entries = new Map(Object.entries({ 'Content-Type': 'application/json', ...headers }).map(([name, value]) => [name.toLowerCase(), value]));
  return { status, ok: status >= 200 && status < 300,
    headers: { get: name => entries.get(name.toLowerCase()) ?? null, has: name => entries.has(name.toLowerCase()) },
    json: async () => JSON.parse(JSON.stringify(body)), text: async () => JSON.stringify(body) };
}

async function harness() {
  const calls = [], events = [], reads = [];
  const secret = 'session-secret-never-cache-1234567890';
  const cachedName = 'Private cached tournament';
  const storage = new Map([[SNAPSHOT_KEY, JSON.stringify({ version: 1, savedAt: '2026-01-01T00:00:00Z', data: { tournament: { name: cachedName, players: [], rounds: 7 } } })]]);
  const session = new Map([[ACCESS_KEY, secret]]);
  const control = { disabled: false, hasAttribute: () => false };
  const reconnect = { disabled: false, hasAttribute: name => name === 'data-offline-allowed' };
  const main = { innerHTML: '', querySelectorAll: () => [control, reconnect], querySelector: () => null,
    insertAdjacentHTML: (_where, html) => { main.innerHTML = html + main.innerHTML; } };
  const elements = new Map([['#main', main], ['#event-label', { textContent: '' }], ['#toast', { textContent: '', className: '' }], ['#workspace-access-form', {}], ['#workspace-access-key', { value: secret }], ['#retry-load', {}]]);
  const document = {
    querySelector: selector => selector === '#workspace-offline-notice' ? (main.innerHTML.includes('id="workspace-offline-notice"') ? { remove: () => { main.innerHTML = ''; } } : null) : elements.get(selector) || null,
    querySelectorAll: () => [], addEventListener: () => {}, dispatchEvent: event => { events.push(event); }
  };
  const queue = [response(401, { error: 'Access key required.' }, { 'X-Savvy-Auth-Required': 'true' })];
  const context = {
    console, document, window: { addEventListener: () => {}, scrollTo: () => {} }, location: { hash: '#simulation' },
    sessionStorage: { getItem: key => session.get(key) || null, setItem: (key, value) => session.set(key, value) },
    localStorage: { getItem: key => { reads.push(key); return storage.get(key) || null; }, setItem: (key, value) => storage.set(key, value) },
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
    ArrayBuffer, Blob, Date, setTimeout: () => 1, clearTimeout: () => {},
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (!queue.length) throw new Error(`Unexpected request: ${url}`);
      const item = queue.shift();
      if (item instanceof Error) throw item;
      return item;
    }
  };
  vm.createContext(context);
  // Append an observer in the same lexical scope; the production code is intact.
  vm.runInContext(`${source}\n;globalThis.workspaceTest = { state, fetchResponse, api, cacheWorkspace, loadWorkspace, generate };`, context);
  await settle();
  return { app: context.workspaceTest, calls, events, reads, queue, storage, session, main, control, reconnect, secret, cachedName };
}

(async () => {
  const h = await harness();
  const { app } = h;
  assert.match(h.main.innerHTML, /workspace-access-form/, '401 must render the access-key form');
  assert.doesNotMatch(h.main.innerHTML, /Private cached tournament/, 'an authorization failure must not reveal cached tournament data');
  assert.equal(app.state.tournament, null);
  assert.equal(app.state.offline, false, '401 is an access failure, not a network outage');
  assert.equal(h.reads.length, 0, '401 must not attempt to restore a cached workspace');

  h.queue.push(response(502, { error: 'Official source could not be reached.' }, { 'X-Savvy-Auth-Required': 'false' }));
  await assert.rejects(app.api('/api/import/url', { method: 'POST', body: { url: 'https://example.invalid/results' } }), error => error.status === 502);
  assert.equal(app.state.offline, false, 'a backend 502 with its identifying header must keep the workspace online');
  assert.equal(h.events.at(-1).detail.available, true);
  assert.equal(h.calls.at(-1).options.headers.Authorization, `Bearer ${h.secret}`);
  assert.equal(h.calls.at(-1).options.headers['Content-Type'], 'application/json');
  assert.equal(h.calls.at(-1).options.cache, 'no-store');
  assert.deepEqual(JSON.parse(h.calls.at(-1).options.body), { url: 'https://example.invalid/results' });

  app.state.tournament = { name: 'Current event', rounds: 7, players: [] };
  h.queue.push(response(503, { error: 'Offline. Reconnect to calculate new pairings.' }));
  await assert.rejects(app.api('/api/state'), error => error.status === 503);
  assert.equal(app.state.offline, true, 'a service-worker 503 without the backend header must enable read-only mode');
  assert.equal(h.events.at(-1).detail.available, false);
  assert.equal(h.control.disabled, true);
  assert.equal(h.reconnect.disabled, false, 'the reconnect control stays usable in read-only mode');
  assert.match(h.main.innerHTML, /Saved view · connection required/);
  const requestCount = h.calls.length;
  await assert.rejects(app.fetchResponse('/api/forecast', { method: 'POST', body: {} }), /cached read-only view/);
  await assert.rejects(app.fetchResponse('/api/tournament', { method: 'put', body: {} }), /cached read-only view/);
  assert.equal(h.calls.length, requestCount, 'offline mutations must be rejected before reaching fetch');
  h.queue.push(response(200, {}, { 'X-Savvy-Auth-Required': 'false' }));
  await app.fetchResponse('/api/state', { method: 'HEAD' });
  assert.equal(h.calls.at(-1).options.method, 'HEAD', 'read-only checks remain available for reconnection');

  app.state.offline = false;
  const newestForecast = { status: 'ok', round: 2, inputHash: 'latest-generated-input', pairings: [], issues: [], warnings: [], candidates: [], rules: { profile: 'us-chess', firstRoundColor: 'B' }, stats: { nodes: 1, elapsedMs: 1 } };
  app.state.forecast = { inputHash: 'older-cached-input' };
  app.state.accessKey = 'accidental-state-secret';
  app.state.credentials = { Authorization: 'must-not-persist' };
  h.queue.push(response(200, newestForecast, { 'X-Savvy-Auth-Required': 'false' }));
  await app.generate();
  const serialized = h.storage.get(SNAPSHOT_KEY);
  const saved = JSON.parse(serialized);
  assert.equal(saved.version, 1);
  assert.ok(Number.isFinite(Date.parse(saved.savedAt)));
  assert.equal(saved.data.forecast.inputHash, 'latest-generated-input', 'generating must cache the newest forecast, not the previous response');
  assert.equal(saved.data.rules.firstRoundColor, 'B');
  assert.equal(saved.data.tournament.name, 'Current event');
  assert.deepEqual(Object.keys(saved.data).sort(), ['directorStatus', 'engines', 'followStatus', 'forecast', 'rules', 'sources', 'tournament']);
  for (const credential of [h.secret, 'accidental-state-secret', 'must-not-persist']) assert.ok(!serialized.includes(credential), 'credentials must not enter the persistent workspace snapshot');
  assert.equal(h.session.get(ACCESS_KEY), h.secret, 'the key remains in tab session storage');

  h.queue.push(new Error('Network disconnected'));
  await assert.rejects(app.fetchResponse('/api/state'), /server is unavailable/);
  assert.equal(app.state.offline, true);
  assert.equal(h.events.at(-1).detail.available, false);
  assert.equal(typeof h.main.onclick, 'function', 'an existing workspace has its normal click handler');
  const readsBeforeReconnect = h.reads.length;
  h.queue.push(response(401, { error: 'Access key expired.' }, { 'X-Savvy-Auth-Required': 'true' }));
  await app.loadWorkspace();
  assert.match(h.main.innerHTML, /workspace-access-form/);
  assert.match(h.main.innerHTML, /Access key expired/);
  assert.doesNotMatch(h.main.innerHTML, /Current event|latest-generated-input/);
  assert.equal(h.reads.length, readsBeforeReconnect, 'reconnect 401 must not restore cached private data');
  assert.equal(app.state.offline, false, 'access-key entry must be usable after an offline reconnect');
  assert.equal(h.main.onclick, null, 'the old read-only click handler must not block the access form');
  console.log('Workspace UI: backend errors, worker outages, read-only mutations, Bearer auth, fresh cache and 401 checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
