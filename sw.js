// Dead Time Machine service worker
const VER = 'dtm-v1';
const CORE = ['./', 'index.html', 'shows-data.json', 'manifest.webmanifest', 'icon-192.png', 'icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VER).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== VER).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;

  // Same-origin: stale-while-revalidate (instant open, silent refresh)
  if (url.origin === location.origin) {
    e.respondWith(
      caches.match(e.request).then(cached => {
        const net = fetch(e.request).then(res => {
          if (res && res.ok) caches.open(VER).then(c => c.put(e.request, res.clone()));
          return res;
        }).catch(() => cached);
        return cached || net;
      })
    );
    return;
  }

  // Fonts: cache-first (they never change)
  if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
        if (res && res.ok) caches.open(VER).then(c => c.put(e.request, res.clone()));
        return res;
      }))
    );
  }
  // Everything else (archive.org, wikipedia): straight to network
});
