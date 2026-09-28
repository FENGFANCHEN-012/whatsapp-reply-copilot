// All server calls go through here, not the content script.
// A content script's fetch runs under WhatsApp's page origin and its CSP; the
// service worker has the extension's host permission for 127.0.0.1 instead, so
// the local server never has to send CORS headers to any web origin.

const DEFAULTS = { server: 'http://127.0.0.1:8765', token: '' };

async function settings() {
  return Object.assign({}, DEFAULTS, await chrome.storage.local.get(['server', 'token']));
}

async function call(path, method, body) {
  const s = await settings();
  if (!s.token) {
    return { ok: false, status: 0, error: 'No server token set. Click the Reply Copilot icon and paste SERVER_TOKEN from your .env.' };
  }
  try {
    const r = await fetch(s.server.replace(/\/$/, '') + path, {
      method: method || 'GET',
      headers: { 'Content-Type': 'application/json', 'X-Copilot-Token': s.token },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data: data, error: r.ok ? null : (data.detail || 'HTTP ' + r.status) };
  } catch (e) {
    return { ok: false, status: 0, error: 'Server not reachable at ' + s.server + '. Start it with: python -m copilot serve' };
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg && msg.type === 'api') {
    call(msg.path, msg.method, msg.body).then(reply);
    return true;   // async reply
  }
});
