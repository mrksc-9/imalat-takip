// ORDUMAK MES Service Worker (PWA & Background Push)
const CACHE_NAME = 'ordumak-mes-v4';
const ASSETS_TO_CACHE = [
  '/',
  '/manifest.json',
  '/static/manifest.json',
  '/static/img/ordumak_icon_192.png',
  '/static/img/ordumak_icon_512.png',
  '/static/css/style.css'
];

// 1. Service Worker Kurulumu & Güvenli Önbellekleme
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return Promise.allSettled(
        ASSETS_TO_CACHE.map((url) =>
          fetch(url)
            .then((res) => {
              if (res.ok) return cache.put(url, res);
            })
            .catch((e) => {})
        )
      );
    })
  );
  self.skipWaiting();
});

// 2. Aktivasyon & Eski Önbellek Temizleme
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    })
  );
  return self.clients.claim();
});

// 3. Web Push Bildirimlerini Yakalama (Site / Tarayıcı Kapalıyken Gerçek Telefon/Masaüstü Bildirimi)
self.addEventListener('push', (event) => {
  let data = {
    title: 'ORDUMAK DEMİR ÇELİK MES',
    body: 'Yeni bir işlem veya sistem güncellemesi kaydedildi.',
    icon: '/static/img/ordumak_icon_192.png',
    badge: '/static/img/ordumak_icon_192.png',
    url: '/'
  };

  if (event.data) {
    try {
      const payload = event.data.json();
      data = Object.assign(data, payload);
    } catch (e) {
      data.body = event.data.text();
    }
  }

  const options = {
    body: data.body,
    icon: data.icon || '/static/img/ordumak_icon_192.png',
    badge: data.badge || '/static/img/ordumak_icon_192.png',
    vibrate: [300, 150, 300, 150, 300],
    data: {
      url: data.url || '/'
    },
    actions: [
      { action: 'open', title: 'Görüntüle' }
    ],
    requireInteraction: true,
    tag: 'ordumak-mes-' + Date.now(),
    renotify: true
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

// 4. Bildirime Tıklandığında Uygulamayı Açma / Odaklama
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) ? event.notification.data.url : '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          if (targetUrl && targetUrl !== '/') {
            client.navigate(targetUrl);
          }
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(targetUrl);
      }
    })
  );
});

// 5. Sayfa İçi Canlı Mesajlaşma Dinleyicisi (OS Bildirimi Olarak Gösterir)
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SHOW_NOTIFICATION') {
    const d = event.data;
    const options = {
      body: d.body || '',
      icon: d.icon || '/static/img/ordumak_icon_192.png',
      badge: d.badge || '/static/img/ordumak_icon_192.png',
      vibrate: [300, 150, 300, 150, 300],
      data: { url: d.url || '/' },
      requireInteraction: true,
      tag: d.tag || ('ordumak-' + Date.now()),
      renotify: true
    };
    self.registration.showNotification(d.title || 'ORDUMAK DEMİR ÇELİK MES', options);
  }
});
