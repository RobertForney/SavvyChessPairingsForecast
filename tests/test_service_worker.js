"use strict";

// Run with: node --test tests/test_service_worker.js
// Exercise the real worker script without browser extensions or dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const ORIGIN = "https://pairings.example.org";
const ROOT = path.resolve(__dirname, "..");
const WORKER = fs.readFileSync(path.join(ROOT, "web", "sw.js"), "utf8");
const API_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"];
const WORKBOOK = "/Louisiana_State_Championship_2026_pairings_no_rematches.xlsx";

function cacheKey(request) {
  return new URL(typeof request === "string" ? request : request.url, ORIGIN).href;
}

function workerHarness() {
  const listeners = new Map();
  const storage = new Map();
  const calls = { network: [], writes: [], matches: [], deletedCaches: [], claims: 0 };
  let network = async () => { throw new TypeError("Network unavailable"); };

  function createCache(name) {
    const entries = new Map();
    return {
      entries,
      async addAll(requests) {
        for (const request of requests) {
          await this.put(request, new Response(`shell:${new URL(request, ORIGIN).pathname}`));
        }
      },
      async put(request, response) {
        if (typeof request !== "string" && request.method !== "GET") {
          throw new TypeError("Cache.put accepts GET requests only");
        }
        const key = cacheKey(request);
        calls.writes.push({ cache: name, key });
        entries.set(key, response.clone());
      },
      async match(request) {
        const key = cacheKey(request);
        calls.matches.push({ cache: name, key });
        return entries.get(key)?.clone();
      },
      async delete(request) {
        return entries.delete(cacheKey(request));
      },
    };
  }

  const caches = {
    async open(name) {
      if (!storage.has(name)) storage.set(name, createCache(name));
      return storage.get(name);
    },
    async keys() { return [...storage.keys()]; },
    async delete(name) {
      calls.deletedCaches.push(name);
      return storage.delete(name);
    },
  };
  const context = vm.createContext({
    self: {
      location: { origin: ORIGIN },
      clients: { async claim() { calls.claims += 1; } },
      addEventListener(kind, listener) { listeners.set(kind, listener); },
    },
    caches,
    fetch(request, options) {
      calls.network.push({ request, options });
      return network(request, options);
    },
    Response, URL,
  });
  vm.runInContext(WORKER, context, { filename: "web/sw.js" });

  return {
    caches, storage, calls,
    setNetwork(handler) { network = handler; },
    async lifecycle(kind) {
      let pending;
      listeners.get(kind)({ waitUntil(promise) { pending = promise; } });
      assert(pending, `${kind} must wait for its asynchronous work`);
      await pending;
    },
    async fetch(request) {
      let intercepted = false;
      let pending;
      listeners.get("fetch")({
        request,
        respondWith(promise) { intercepted = true; pending = promise; },
      });
      return { intercepted, response: intercepted ? await pending : null };
    },
    currentCache() {
      const names = [...storage.keys()].filter(name => name.startsWith("savvy-shell-"));
      assert.equal(names.length, 1, "Installation must create one versioned shell cache");
      return storage.get(names[0]);
    },
  };
}

function request(url, method = "GET", authorization = "Bearer test-secret") {
  const headers = { Authorization: authorization, "Content-Type": "application/json" };
  const options = { method, headers };
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) options.body = '{"change":true}';
  return new Request(new URL(url, ORIGIN), options);
}

function navigation(url) {
  // FetchEvent navigation requests are created by the browser; script-created
  // Request objects cannot use mode=navigate. Only these fields are read by SW.
  return { url: new URL(url, ORIGIN).href, method: "GET", mode: "navigate" };
}

test("installation precaches only public app assets, never tournament sources or APIs", async () => {
  const harness = workerHarness();
  await harness.lifecycle("install");
  const paths = [...harness.currentCache().entries.keys()].map(url => new URL(url).pathname);
  for (const required of ["/index.html", "/app.js", "/pwa.js", "/simulation.js",
    "/styles.css", "/offline.html", "/manifest.webmanifest"]) {
    assert(paths.includes(required), `Missing offline app asset: ${required}`);
  }
  assert(paths.some(asset => asset.startsWith("/icons/")));
  for (const asset of paths) {
    assert(!asset.startsWith("/api"), `API in shell cache: ${asset}`);
    assert(!/\.(xlsx?|csv|trf|json)$/i.test(asset), `Tournament source in shell cache: ${asset}`);
    const local = path.join(ROOT, "web", asset === "/" ? "index.html" : asset.slice(1));
    assert(fs.statSync(local).isFile(), `Precached app file does not exist: ${asset}`);
  }
});

test("every API method forwards bearer headers, request body, and a network-only policy", async (t) => {
  for (const method of API_METHODS) {
    await t.test(method, async () => {
      const harness = workerHarness();
      await harness.lifecycle("install");
      const writes = harness.calls.writes.length;
      const incoming = request("/api/forecast?fresh=1", method);
      harness.setNetwork(async (forwarded, options) => {
        assert.equal(forwarded, incoming, "Forward the original request with its credentials and body");
        assert.equal(forwarded.headers.get("Authorization"), "Bearer test-secret");
        assert.equal(forwarded.method, method);
        assert.equal(options.cache, "no-store");
        if (["POST", "PUT", "PATCH", "DELETE"].includes(method)) {
          assert.equal(await forwarded.text(), '{"change":true}');
        }
        return new Response('{"current":true}', { headers: { "Content-Type": "application/json" } });
      });
      const result = await harness.fetch(incoming);
      assert(result.intercepted);
      assert.equal(result.response.status, 200);
      assert.equal((await result.response.json()).current, true);
      assert.equal(harness.calls.network.length, 1);
      assert.equal(harness.calls.writes.length, writes, "An API success must never enter Cache Storage");
      assert.equal(harness.calls.matches.length, 0, "An API request must never look for a cached result");
    });
  }
});

test("offline API calls return JSON503 for every method, even with an old cached success", async (t) => {
  for (const method of API_METHODS) {
    await t.test(method, async () => {
      const harness = workerHarness();
      await harness.lifecycle("install");
      await harness.currentCache().put("/api/forecast", new Response('{"obsoleteSuccess":true}'));
      const writes = harness.calls.writes.length;
      const result = await harness.fetch(request("/api/forecast", method));
      assert(result.intercepted);
      assert.equal(result.response.status, 503);
      assert.match(result.response.headers.get("Content-Type"), /^application\/json\b/i);
      assert.equal(result.response.headers.get("Cache-Control"), "no-store");
      const body = await result.response.json();
      assert.equal(body.offline, true);
      assert.equal(typeof body.error, "string");
      assert.equal(body.obsoleteSuccess, undefined);
      assert.equal(harness.calls.matches.length, 0);
      assert.equal(harness.calls.writes.length, writes);
    });
  }
});

test("HTTP authentication failures are preserved instead of replaced by cached success", async () => {
  const harness = workerHarness();
  await harness.lifecycle("install");
  await harness.currentCache().put("/api/state", new Response('{"privateCachedState":true}'));
  const writes = harness.calls.writes.length;
  harness.setNetwork(async () => new Response('{"code":"AUTH_REQUIRED"}', {
    status: 401, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  }));
  const { response } = await harness.fetch(request("/api/state", "GET", "Bearer expired"));
  assert.equal(response.status, 401);
  assert.equal((await response.json()).code, "AUTH_REQUIRED");
  assert.equal(harness.calls.matches.length, 0);
  assert.equal(harness.calls.writes.length, writes);
});

test("API root, health, and workbook download never use the application-shell fallback", async () => {
  const harness = workerHarness();
  await harness.lifecycle("install");
  const writes = harness.calls.writes.length;
  for (const endpoint of ["/api", "/api/health", "/api/sample/source"]) {
    const { response } = await harness.fetch(request(endpoint));
    assert.equal(response.status, 503, endpoint);
    assert.equal((await response.json()).offline, true);
  }
  assert.equal(harness.calls.matches.length, 0);
  assert.equal(harness.calls.writes.length, writes);
});

test("offline app navigation and scripts use saved public shell assets", async () => {
  const harness = workerHarness();
  await harness.lifecycle("install");
  const page = await harness.fetch(navigation("/"));
  assert.equal(page.response.status, 200);
  assert.equal(await page.response.text(), "shell:/");
  const script = await harness.fetch(request("/app.js"));
  assert.equal(await script.response.text(), "shell:/app.js");

  await harness.currentCache().delete("/");
  const fallback = await harness.fetch(navigation("/"));
  assert.equal(await fallback.response.text(), "shell:/index.html");
  await harness.currentCache().delete("/index.html");
  const help = await harness.fetch(navigation("/"));
  assert.equal(await help.response.text(), "shell:/offline.html");
});

test("workbook files, arbitrary downloads, cross-origin URLs, and non-API mutations bypass the cache", async () => {
  const harness = workerHarness();
  await harness.lifecycle("install");
  const writes = harness.calls.writes.length;
  for (const url of [WORKBOOK, "/downloads/event.json", "/sample.csv", "/api-similar/file.xlsx",
    "https://another.example.org/app.js"]) {
    const result = await harness.fetch(request(url));
    assert.equal(result.intercepted, false, url);
  }
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    assert.equal((await harness.fetch(request("/index.html", method))).intercepted, false, method);
  }
  assert.equal(harness.calls.matches.length, 0);
  assert.equal(harness.calls.writes.length, writes);
});

test("activation removes only older Savvy shell caches and claims app clients", async () => {
  const harness = workerHarness();
  await harness.lifecycle("install");
  const currentName = [...harness.storage.keys()][0];
  for (const name of ["savvy-shell-old-version", "unrelated-app-cache", "savvy-other-feature"]) {
    await harness.caches.open(name);
  }
  await harness.lifecycle("activate");
  assert.deepEqual(harness.calls.deletedCaches, ["savvy-shell-old-version"]);
  assert(harness.storage.has(currentName));
  assert(harness.storage.has("unrelated-app-cache"));
  assert(harness.storage.has("savvy-other-feature"));
  assert.equal(harness.calls.claims, 1);
});
