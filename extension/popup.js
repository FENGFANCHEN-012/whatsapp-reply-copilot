// Settings + a live view of the server's setup checklist (/health), so a new user
// sees exactly what is still missing without opening a terminal.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function check() {
  $('status').textContent = 'Checking…';
  const r = await new Promise((res) => chrome.runtime.sendMessage({ type: 'api', path: '/health' }, res));
  if (!r || !r.ok) {
    $('status').innerHTML = `<p class="bad">${esc((r && r.error) || 'No response')}</p>`;
    return;
  }
  const rows = r.data.checks.map((c) => {
    const cls = c.ok ? 'ok' : (c.required ? 'bad' : 'warn');
    const mark = c.ok ? '✔' : (c.required ? '✖' : '!');
    return `<li class="${cls}">${mark} <b>${esc(c.name)}</b>: ${esc(c.detail)}`
         + (c.ok ? '' : `<br><small>${esc(c.fix)}</small>`) + '</li>';
  }).join('');
  $('status').innerHTML = `<p class="${r.data.ok ? 'ok' : 'bad'}">${r.data.ok ? 'Ready.' : 'Setup needed:'}</p><ul>${rows}</ul>`;
}

(async () => {
  const s = await chrome.storage.local.get(['server', 'token']);
  $('server').value = s.server || 'http://127.0.0.1:8765';
  $('token').value = s.token || '';
  if (s.token) check();
})();

$('save').onclick = async () => {
  await chrome.storage.local.set({ server: $('server').value.trim() || 'http://127.0.0.1:8765', token: $('token').value.trim() });
  check();
};
