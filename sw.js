const CACHE_NAME = "radar-bourse-v1";
const APP_SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    try {
      await cache.addAll(APP_SHELL);
    } catch (error) {
      // The service worker can still activate if one optional shell item fails.
      console.warn("Could not cache every app shell file", error);
    }
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  const isRadarData = url.pathname.endsWith("/data/radar_dashboard.json");
  const isDocument = request.mode === "navigate" || url.pathname.endsWith("/index.html");

  if (isRadarData || isDocument) {
    // Network-first for the app and JSON so refreshing the page picks up new radar data.
    event.respondWith((async () => {
      try {
        const response = await fetch(request, { cache: "no-store" });
        if (response && response.ok) {
          const cache = await caches.open(CACHE_NAME);
          const key = isRadarData ? "./data/radar_dashboard.json" : "./index.html";
          await cache.put(key, response.clone());
        }
        return response;
      } catch (error) {
        const cached = await caches.match(isRadarData ? "./data/radar_dashboard.json" : "./index.html");
        if (cached) return cached;
        throw error;
      }
    })());
    return;
  }

  event.respondWith((async () => {
    const cached = await caches.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    if (response && response.ok) {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(request, response.clone());
    }
    return response;
  })());
});
