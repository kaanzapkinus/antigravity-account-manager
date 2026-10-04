'use strict';

const CACHE_PREFIX = 'google-hesaplari-public-';
const CACHE_NAME = `${CACHE_PREFIX}v2`;
const OFFLINE_URL = '/offline.html';
const PUBLIC_ASSETS = [
  OFFLINE_URL,
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
];

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await cache.addAll(PUBLIC_ASSETS.map(url => new Request(url, {
      cache: 'reload',
      credentials: 'omit',
    })));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter(name => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
      .map(name => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);

  // Only the app entry and explicitly public assets are handled. APIs,
  // authentication URLs, query strings and all mutations stay network-only.
  if (request.method !== 'GET' || url.origin !== self.location.origin || url.search) return;

  if (request.mode === 'navigate' && url.pathname === '/') {
    event.respondWith((async () => {
      try {
        // Account HTML is never written to Cache Storage or read from HTTP cache.
        return await fetch(request, { cache: 'no-store' });
      } catch {
        const cache = await caches.open(CACHE_NAME);
        return (await cache.match(OFFLINE_URL)) || Response.error();
      }
    })());
    return;
  }

  if (PUBLIC_ASSETS.includes(url.pathname)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME);
      return (await cache.match(url.pathname)) || fetch(request);
    })());
  }
});
