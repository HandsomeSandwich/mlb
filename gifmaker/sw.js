/*
 * Service worker: keep the app itself available offline.
 *
 * Editing photos already on the phone needs no network at all, so the shell is
 * cached up front and served cache-first. Image search obviously still needs a
 * connection, and /api/ calls are never cached.
 *
 * Bump CACHE when any shell file changes, or phones will keep the old copy.
 */
var CACHE = 'gifmaker-v1';

var SHELL = [
  './',
  'index.html',
  'app.css',
  'app.js',
  'search.js',
  'gif-encoder.js',
  'gif-worker.js',
  'manifest.webmanifest',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'icons/apple-touch-icon.png',
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(CACHE)
      .then(function (cache) { return cache.addAll(SHELL); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names.map(function (name) {
        return name === CACHE ? null : caches.delete(name);
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') return;

  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // remote pictures
  if (url.pathname.indexOf('/api/') !== -1) return; // search and proxy

  event.respondWith(
    caches.match(request).then(function (hit) {
      if (hit) return hit;
      return fetch(request).then(function (response) {
        // Tuck away anything else same-origin we end up needing.
        if (response && response.ok && response.type === 'basic') {
          var copy = response.clone();
          caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
        }
        return response;
      }).catch(function () {
        // Offline and not cached: fall back to the app shell for navigations.
        if (request.mode === 'navigate') return caches.match('index.html');
        throw new Error('offline');
      });
    })
  );
});
