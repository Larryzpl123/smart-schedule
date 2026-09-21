/* Service worker for SSA Smart Schedule.
 *
 * Deliberately network-first and cache-almost-nothing. A schedule app that
 * serves a stale week is worse than one that says it is offline, and the whole
 * point of this file is to make the site installable to a phone's home screen
 * (Chrome requires a fetch handler for that) -- not to build an offline app.
 * Only the static shell pieces are cached, and even those are refreshed from
 * the network whenever it is reachable.
 */
const SHELL = "ssa-shell-v1";
const SHELL_FILES = [
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/manifest.webmanifest"
];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;                 // never touch a POST
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Schedule data must never come from a cache.
  if (url.pathname.startsWith("/api/")) return;

  e.respondWith(
    fetch(req)
      .then(res => {
        if (SHELL_FILES.includes(url.pathname) && res.ok) {
          const copy = res.clone();
          caches.open(SHELL).then(c => c.put(req, copy));
        }
        return res;
      })
      .catch(() => caches.match(req))
  );
});
