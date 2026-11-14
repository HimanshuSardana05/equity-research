// Minimal service worker so Chrome treats the site as an installable app (proper home-screen icon).
// It never caches: every request goes to the network, so data is always the latest daily build.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => { e.respondWith(fetch(e.request)); });
