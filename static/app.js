const csrf = () => document.cookie.split('; ').find(x => x.startsWith('csrftoken='))?.split('=')[1] || document.querySelector('[name=csrfmiddlewaretoken]')?.value;
async function post(url, data) {
  const res = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json','X-CSRFToken':csrf()}, body:JSON.stringify(data)});
  if (!res.ok) throw new Error('Could not save your notification preferences.');
  return res.json();
}
try { if (localStorage.getItem('theme') === 'dark') document.documentElement.dataset.theme = 'dark'; } catch (_) {}
document.querySelector('#theme-toggle')?.addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('theme', theme); } catch (_) {}
});
document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', event => {
  if (!window.confirm(form.dataset.confirm)) event.preventDefault();
}));
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
document.querySelector('#enable-push')?.addEventListener('click', async event => {
  const status = document.querySelector('#push-status');
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) throw new Error('Push is unavailable here. On iPhone, open OstrichDispatch from your Home Screen.');
    const key = event.currentTarget.dataset.key;
    if (!key) throw new Error('Push has not been configured on the server.');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('Notification permission was not granted.');
    const reg = await navigator.serviceWorker.ready;
    const base = key.replace(/-/g, '+').replace(/_/g, '/');
    const bytes = Uint8Array.from(atob(base + '='.repeat((4-base.length%4)%4)), c => c.charCodeAt(0));
    const subscription = await reg.pushManager.getSubscription() || await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:bytes});
    await post('/push/subscribe/', subscription.toJSON());
    status.textContent = 'Notifications are enabled on this device.';
  } catch (err) { status.textContent = err.message; }
});
document.querySelector('#disable-push')?.addEventListener('click', async () => {
  try {
    await post('/push/unsubscribe/', {});
    if ('serviceWorker' in navigator) {
      const reg = await navigator.serviceWorker.getRegistration();
      const sub = await reg?.pushManager.getSubscription();
      await sub?.unsubscribe();
    }
    document.querySelector('#push-status').textContent = 'Push notifications disabled for all your devices.';
  } catch(err) { document.querySelector('#push-status').textContent = err.message; }
});
