/* Service worker for SSA Smart Schedule.
 *
 * Two jobs, kept apart on purpose:
 *
 * 1. Every page: make the site installable (Chrome wants a fetch handler) and
 *    keep the icons / manifest. Nothing personal is ever cached -- the account
 *    app needs the server anyway, and a stale week is worse than "offline".
 *
 * 2. Local mode only (pages under /local, and requests those pages make):
 *    work with no internet. Local mode keeps the student's schedule in the
 *    browser already; what it still needs from us is the page itself and the
 *    SCHOOL's data (calendar, bell schedules). Those are fetched network-first
 *    -- online you always get the latest rain day / moved assembly -- and the
 *    last good copy is kept for when there is no network. A copy served from
 *    here carries X-SSA-Saved (when it was saved), so the page can say
 *    "offline, calendar as of ...".
 */
const SHELL = "ssa-shell-v1";
const LOCAL_CACHE = "ssa-local-v1";
const SHELL_FILES = [
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/manifest.webmanifest"
];
/* What a local-mode page needs from us to open with no network. */
const LOCAL_FILES = ["/local", "/local/app", "/local/edit",
                     "/manifest-local.webmanifest", "/api/schedule-shared",
                     "/static/icons/apple-touch-icon.png"];
const NET_TIMEOUT_MS = 4000;      // a captive / dying wifi should not hang the app

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  const keep = [SHELL, LOCAL_CACHE];
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => !keep.includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function offlineable(url) {
  return LOCAL_FILES.includes(url.pathname) || url.pathname.startsWith("/api/calendar/");
}

/* A copy to keep: the same response, plus when it was saved. */
async function stamped(res) {
  const h = new Headers(res.headers);
  h.set("X-SSA-Saved", new Date().toISOString());
  return new Response(await res.clone().blob(), {status: res.status, statusText: res.statusText, headers: h});
}
async function save(req, res) {
  if (!res || !res.ok || res.type !== "basic") return;
  const c = await caches.open(LOCAL_CACHE);
  await c.put(req, await stamped(res));
}

/* Network first; the saved copy when the network fails or is too slow. */
async function networkFirst(e) {
  const req = e.request;
  const net = fetch(req).then(async res => { await save(req, res); return res; });
  const timeout = new Promise(r => setTimeout(() => r("timeout"), NET_TIMEOUT_MS));
  let res;
  try { res = await Promise.race([net, timeout]); } catch (err) { res = null; }
  if (res && res !== "timeout") return res;
  const cached = await caches.match(req, {ignoreSearch: true, cacheName: LOCAL_CACHE});
  if (cached) {
    if (res === "timeout") e.waitUntil(net.catch(() => {}));   // still refresh the copy
    return cached;
  }
  if (res === "timeout") return net;          // nothing saved: keep waiting for the network
  return Response.error();
}

async function fromLocalPage(e, url) {
  if (url.pathname === "/local" || url.pathname.startsWith("/local/")) return true;
  const id = e.clientId || e.resultingClientId;
  if (!id) return false;
  const c = await self.clients.get(id);
  if (!c) return false;
  const p = new URL(c.url).pathname;
  return p === "/local" || p.startsWith("/local/");
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;                 // never touch a POST
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  if (offlineable(url)) {
    e.respondWith(fromLocalPage(e, url).then(local => local ? networkFirst(e) : fetch(req)));
    return;
  }
  // Everything else under /api/ is personal or live: never from a cache.
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

/* A local-mode page asks for everything it may need offline to be saved now
   -- including the pages and years it has not opened yet. Same rules as a
   normal fetch: only the local files and the calendars. */
self.addEventListener("message", e => {
  const d = e.data || {};
  if (d.type !== "warm-local" || !Array.isArray(d.urls)) return;
  e.waitUntil(Promise.allSettled(d.urls.map(async u => {
    const url = new URL(u, self.location.origin);
    if (url.origin !== self.location.origin || !offlineable(url)) return;
    const req = new Request(url.pathname, {cache: "no-store"});
    await save(req, await fetch(req));
  })));
});
