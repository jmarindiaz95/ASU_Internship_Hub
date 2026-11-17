'use strict';

(async function openHub() {
  const target = new URL('http://127.0.0.1:8080/');
  target.search = window.location.search;
  target.hash = window.location.hash;
  document.querySelector('#open-hub').href = target.href;
  const status = document.querySelector('#connection-status');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    await fetch(new URL('/api/health', target), {
      mode: 'no-cors', credentials: 'omit', cache: 'no-store', signal: controller.signal,
    });
    status.textContent = 'Your hub is running. Opening jobs and map…';
    window.location.replace(target.href);
  } catch {
    status.textContent = 'Start the hub using Start Hub.command, then click “Open jobs and map.”';
  } finally {
    clearTimeout(timer);
  }
})();
