// Never cache authenticated pages, order data, or provider credentials.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('push', event => {
  let data = {title: 'OstrichDispatch', body: 'An order has an update.', url: '/notifications/'};
  try { data = {...data, ...event.data.json()}; } catch (_) {}
  const url = typeof data.url === 'string' && /^\/orders\/\d+\/$/.test(data.url) ? data.url : '/notifications/';
  event.waitUntil(self.registration.showNotification(data.title, {body: data.body, icon: '/static/icon.svg', data: {url}}));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(self.clients.openWindow(event.notification.data.url));
});
