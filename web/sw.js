"use strict";

const CACHE_PREFIX = "savvy-shell-";
const CACHE_NAME = `${CACHE_PREFIX}v3`;
const SHELL_ASSETS = [
  "/",
  "/index.html",
  "/app.js",
  "/pwa.js",
  "/simulation.js",
  "/follow.js",
  "/director.js",
  "/styles.css",
  "/offline.html",
  "/manifest.webmanifest",
  "/icons/icon.svg",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-maskable-512.png",
  "/icons/apple-touch-icon.png",
];
const SHELL_PATHS = new Set(SHELL_ASSETS);

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS)));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
      .map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

function unavailableApi() {
  return new Response(JSON.stringify({
    error: "The pairing service is unavailable. Reconnect to create current forecasts, edit results, or replay a tournament.",
    offline: true,
  }), {
    status: 503,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

async function shellResponse(request, pathname) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    // Do not turn server errors or redirects into a cached application shell.
    if (response.ok && !response.redirected) {
      // Storage limits must not prevent a successful online response.
      await cache.put(pathname, response.clone()).catch(() => {});
    }
    return response;
  } catch {
    const saved = await cache.match(pathname);
    if (saved) return saved;
    if (request.mode === "navigate") {
      return await cache.match("/index.html") || await cache.match("/offline.html")
        || new Response("Savvy Pairings is offline. Reconnect and try again.", {
          status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" },
        });
    }
    return new Response("This app asset is unavailable offline.", {
      status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname === "/api" || url.pathname.startsWith("/api/")) {
    // All API methods are network only, including GET health/sample/forecast.
    // A failed mutation must never receive an old success or an HTML fallback.
    event.respondWith(fetch(event.request, { cache: "no-store" }).catch(unavailableApi));
    return;
  }
  if (event.request.method !== "GET") return;
  if (SHELL_PATHS.has(url.pathname)) {
    event.respondWith(shellResponse(event.request, url.pathname));
  }
  // Source downloads, uploads, and all other URLs bypass this worker's cache.
});
