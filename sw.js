const CACHE_NAME = 'fuhua-v1';
const CACHE_URLS = ['/h5/', '/h5/css/style.css', '/h5/js/app.js', '/h5/js/api.js', '/h5/js/util.js', '/h5/js/map.js'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(CACHE_URLS)));
});

self.addEventListener('fetch', e => {
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(resp => {
    if (resp.status === 200) {
      const clone = resp.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
    }
    return resp;
  }).catch(() => caches.match('/h5/'))));
});
