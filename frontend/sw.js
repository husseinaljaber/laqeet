/* Offline support: the app shell is cached; job lists come from the network, with the last copy as a fallback
   (useful on slow or cut connections). */
const SHELL = 'laqeet-shell-v38', DATA = 'laqeet-data-v38';
const FILES = ['./', 'index.html', 'app.css', 'ui.js', 'app.js', 'cv.html', 'page.css', 'pulse.html', 'employer.html', 'scam.html', 'manifest.webmanifest', 'icons/icon.svg', 'icons/icon-192.png'];

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
            caches.open(DATA).then((c) => c.put(e.request, copy));
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
        .catch(() => caches.match(e.request).then((hit) => hit || (e.request.mode === 'navigate' ? caches.match('index.html') : undefined)))
    );
  }
});
