/* Service worker for SSA Smart Schedule.
 *
 * Two jobs, kept apart on purpose:
 *
 * 1. Every page: make the site installable (Chrome wants a fetch handler) and
 *    keep the icons / manifest. Nothing personal is ever cached -- the account
 *    app needs the server anyway, and a stale week is worse than "offline".
 *
 * 3. Account mode, ONLY if the student turned on "Keep a copy for offline"
 *    on this device (Settings): the same network-first copy of /app, their
 *    own schedule and the school's data, in its own cache (ACCT_CACHE). That
 *    cache existing IS the switch -- the worker cannot read localStorage --
 *    so turning it off, or logging out, deletes the cache and with it every
 *    copy of the student's schedule on this device. Read-only offline:
 *    changes go to the server, so they need the network.
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
const ACCT_CACHE = "ssa-acct-v1";       // exists only while the account opt-in is on
/* What the account app needs offline: the page, the student's own schedule,
   and (via offlineable) the school's calendar and bell schedules. */
const ACCT_FILES = ["/app", "/api/my-schedule", "/manifest.webmanifest"];
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
  const keep = [SHELL, LOCAL_CACHE, ACCT_CACHE];
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
/* Never keep a redirect: an expired login turns /app and /api/my-schedule
   into the login page, and that must not become "your schedule, offline". */
async function save(req, res, cacheName) {
  if (!res || !res.ok || res.type !== "basic" || res.redirected) return;
  // the account copy only while the switch is on (it may have been turned off
  // while this request was in flight: do not quietly turn it back on)
  if (cacheName === ACCT_CACHE && !(await caches.has(ACCT_CACHE))) return;
  const c = await caches.open(cacheName);
  await c.put(req, await stamped(res));
}

/* Network first; the saved copy when the network fails or is too slow. */
async function networkFirst(e, cacheName) {
  const req = e.request;
  const net = fetch(req).then(async res => { await save(req, res, cacheName); return res; });
  const timeout = new Promise(r => setTimeout(() => r("timeout"), NET_TIMEOUT_MS));
  let res;
  try { res = await Promise.race([net, timeout]); } catch (err) { res = null; }
  if (res && res !== "timeout") return res;
  const cached = await caches.match(req, {ignoreSearch: true, cacheName: cacheName});
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

  // Logging out deletes the account copy first, on any device and any page.
  if (url.pathname === "/logout") {
    e.respondWith(caches.delete(ACCT_CACHE).then(() => fetch(req)));
    return;
  }
  if (offlineable(url) || ACCT_FILES.includes(url.pathname)) {
    e.respondWith((async () => {
      if (offlineable(url) && await fromLocalPage(e, url)) return networkFirst(e, LOCAL_CACHE);
      if (await caches.has(ACCT_CACHE)) return networkFirst(e, ACCT_CACHE);
      return fetch(req);
    })());
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
  const acct = d.type === "warm-acct";
  if ((d.type !== "warm-local" && !acct) || !Array.isArray(d.urls)) return;
  e.waitUntil(Promise.allSettled(d.urls.map(async u => {
    const url = new URL(u, self.location.origin);
    if (url.origin !== self.location.origin) return;
    if (!(offlineable(url) || (acct && ACCT_FILES.includes(url.pathname)))) return;
    const req = new Request(url.pathname, {cache: "no-store", credentials: "same-origin"});
    await save(req, await fetch(req), acct ? ACCT_CACHE : LOCAL_CACHE);
  })));
});
