/* Offline support: the app shell is cached; job lists come from the network, with the last copy as a fallback
   (useful on slow or cut connections). */
const SHELL = 'laqeet-shell-v45', DATA = 'laqeet-data-v45';
const FILES = ['./', 'index.html', 'app.css', 'ui.js', 'app.js', 'cv.html', 'page.css', 'pulse.html', 'employer.html', 'scam.html', 'sources.html', 'turkey-work.html', 'offline.html', 'manifest.webmanifest', 'icons/icon.svg', 'icons/icon-192.png'];

// Keep only the newest API answers so the phone's storage doesn't fill up with every filter ever tried.
const DATA_MAX = 40;
function trimCache(cache, max) {
  return cache.keys().then((keys) => Promise.all(keys.slice(0, Math.max(0, keys.length - max)).map((k) => cache.delete(k))));
}

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(SHELL)
      .then((c) => Promise.all(FILES.map((f) => fetch(f, { cache: 'reload' }).then((r) => (r.ok ? c.put(f, r) : undefined)).catch(() => undefined))))
      .then(() => self.skipWaiting())
  );
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => ![SHELL, DATA].includes(k)).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;
  if (url.pathname.startsWith('/api/')) {
    e.respondWith(
      fetch(e.request, { cache: 'no-store' })
        .then((r) => {
          if (r.ok) {
            const copy = r.clone();
            caches.open(DATA).then((c) => c.put(e.request, copy).then(() => trimCache(c, DATA_MAX)));
          }
          return r;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }
  if (url.origin === location.origin) {
    e.respondWith(
      fetch(e.request, { cache: 'no-cache' })
        .then((r) => {
          if (r.ok) {
            const copy = r.clone();
            caches.open(SHELL).then((c) => c.put(e.request, copy));
          }
          return r;
        })
        .catch(() => caches.match(e.request, { ignoreSearch: url.pathname === '/' || url.pathname.endsWith('index.html') })
          .then((hit) => hit || (e.request.mode === 'navigate'
            ? (url.pathname === '/' || url.pathname.endsWith('index.html') ? caches.match('index.html') : caches.match('offline.html'))
            : undefined)))
    );
  }
});
