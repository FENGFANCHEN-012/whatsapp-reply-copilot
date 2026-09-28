// Reply Copilot -- content script for web.whatsapp.com.
//
// Reads the open chat, asks the local server for drafts, puts the one you click
// into the message box. It NEVER sends: you review and press Enter yourself.
// When you do send, what went out (edited or not) is reported back as feedback,
// so the next suggestion learns from it.
//
// All WhatsApp DOM knowledge is in SEL and the three readers below it. When
// WhatsApp ships a new build and something stops working, this is the place.

const SEL = {
  rows: '#main div[role="row"]',
  composer: 'footer [contenteditable="true"]',
  composerAlt: '[contenteditable="true"][data-tab="10"]',
  header: '#main header',
};
const N_MESSAGES = 12;
const FEEDBACK_WINDOW_MS = 5 * 60 * 1000;

const api = (path, method, body) =>
  new Promise((res) => chrome.runtime.sendMessage({ type: 'api', path, method, body }, res));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const squash = (s) => (s || '').replace(/\s+/g, ' ').trim();

// ---------- reading the chat ----------

// A reply-with-quote renders the QUOTED text first, also as span.selectable-text.
// Only the real body sits under .copyable-text[data-pre-plain-text], so that is
// tried first; the fallback takes the LAST span (the body always follows the quote).
function msgText(row) {
  let t = row.querySelector('.copyable-text[data-pre-plain-text] span.selectable-text');
  if (!t) {
    const all = row.querySelectorAll('span.selectable-text');
    t = all.length ? all[all.length - 1] : null;
  }
  if (!t) return '';
  const clone = t.cloneNode(true);
  clone.querySelectorAll('img[alt]').forEach((im) => im.replaceWith(document.createTextNode(im.alt || '')));
  clone.querySelectorAll('br').forEach((br) => br.replaceWith(document.createTextNode('\n')));
  return (clone.textContent || '').trim();
}

// Direction: .message-in / .message-out where present; otherwise the bubble
// "tail" icon, which only the FIRST message of a run carries -- so later ones
// inherit the direction of the last row that had one.
function rowDir(row) {
  if (row.querySelector('.message-out')) return 'out';
  if (row.querySelector('.message-in')) return 'in';
  if (row.querySelector('[data-icon="tail-out"]')) return 'out';
  if (row.querySelector('[data-icon="tail-in"]')) return 'in';
  return null;
}

function readThread(n) {
  const out = [];
  let dir = null;
  for (const row of document.querySelectorAll(SEL.rows)) {
    dir = rowDir(row) || dir;
    const text = msgText(row);
    if (text && dir) out.push({ dir, text });
  }
  return out.slice(-n);
}

function chatTitle() {
  const h = document.querySelector(SEL.header);
  const t = h && h.querySelector('span[title], span[dir="auto"]');
  return t ? (t.getAttribute('title') || t.textContent || '') : '';
}

function getComposer() {
  return document.querySelector(SEL.composer) || document.querySelector(SEL.composerAlt);
}

// ---------- writing the draft ----------

// WhatsApp's composer is a Lexical editor. Measured: execCommand('insertText')
// ignores strings with spaces and appends at the caret; execCommand('delete')
// and paste('') leave it unchanged. The ONE thing that replaces its contents is
// an explicit Range over them followed by a synthetic paste of the new text.
// Success is verified against the composer, never assumed.
async function putInComposer(text) {
  const box = getComposer();
  if (!box) return false;
  box.focus();
  await wait(30);
  for (let attempt = 0; attempt < 2; attempt++) {
    const range = document.createRange();
    range.selectNodeContents(box);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    await wait(60);   // let the editor adopt the selection before the paste
    const dt = new DataTransfer();
    dt.setData('text/plain', text);
    box.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: dt }));
    await wait(150);
    if (squash(box.textContent) === squash(text)) return true;
  }
  console.warn('[Reply Copilot] draft did not land; composer holds', JSON.stringify(box.textContent.slice(0, 80)));
  return false;
}

// ---------- learning from what was sent ----------

// After suggestions are shown, watch for the next message I send in the same
// chat and report it. `lastOut` is my last message when suggestions were made;
// a different last-out message means something new went out.
let pending = null;

function watchForSend(ctx) {
  pending = ctx;
  const started = Date.now();
  const timer = setInterval(async () => {
    if (pending !== ctx || Date.now() - started > FEEDBACK_WINDOW_MS || chatTitle() !== ctx.chat) {
      clearInterval(timer);
      if (pending === ctx) pending = null;
      return;
    }
    const thread = readThread(N_MESSAGES);
    const last = thread[thread.length - 1];
    if (last && last.dir === 'out' && last.text !== ctx.lastOut) {
      clearInterval(timer);
      pending = null;
      await api('/feedback', 'POST', {
        messages: ctx.messages, suggestions: ctx.suggestions, chosen: ctx.chosen, sent: last.text,
      });
    }
  }, 1500);
}

// ---------- UI ----------

let panel = null;

function ensureButton() {
  const footer = document.querySelector('footer');
  if (!footer || footer.querySelector('#rc-btn')) return;
  const b = document.createElement('button');
  b.id = 'rc-btn';
  b.type = 'button';
  b.title = 'Suggest replies (Alt+S)';
  b.textContent = '✨';
  b.addEventListener('click', onSuggest);
  footer.appendChild(b);
}

function showPanel(html) {
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'rc-panel';
    document.body.appendChild(panel);
  }
  const f = document.querySelector('footer');
  const r = f ? f.getBoundingClientRect() : { left: 20, top: window.innerHeight - 60, width: 400 };
  panel.style.left = r.left + 12 + 'px';
  panel.style.bottom = window.innerHeight - r.top + 8 + 'px';
  panel.style.maxWidth = Math.max(280, r.width - 24) + 'px';
  panel.innerHTML = html;
  panel.hidden = false;
  return panel;
}

function hidePanel() { if (panel) panel.hidden = true; }

const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function onSuggest() {
  const messages = readThread(N_MESSAGES);
  if (!messages.length) return showPanel('<div class="rc-note">Open a chat first.</div>');
  showPanel('<div class="rc-note">Thinking…</div>');

  const r = await api('/suggest', 'POST', { messages });
  if (!r || !r.ok) return showPanel(`<div class="rc-err">${esc((r && r.error) || 'No response')}</div>`);

  const d = r.data;
  const items = d.suggestions || [];
  const note = [
    d.provider ? `via ${d.provider}` : '',
    d.examples ? `${d.examples} of your past replies` : '',
    d.ms ? `${d.ms} ms` : '',
  ].filter(Boolean).join(' · ');
  const p = showPanel(
    (items.length ? items.map((s, i) => `<button class="rc-sugg" data-i="${i}">${esc(s.text)}</button>`).join('')
                  : '<div class="rc-note">No suggestions.</div>')
    + (d.warning ? `<div class="rc-warn">${esc(d.warning)}</div>` : '')
    + `<div class="rc-meta">${esc(note)} <button class="rc-x" title="Close">×</button></div>`);

  const outs = messages.filter((m) => m.dir === 'out');
  const ctx = {
    chat: chatTitle(), messages, suggestions: items.map((s) => s.text), chosen: null,
    lastOut: outs.length ? outs[outs.length - 1].text : null,
  };
  watchForSend(ctx);

  p.querySelector('.rc-x').onclick = hidePanel;
  p.querySelectorAll('.rc-sugg').forEach((btn) => {
    btn.onclick = async () => {
      const text = items[+btn.dataset.i].text;
      if (await putInComposer(text)) {
        ctx.chosen = text;
        hidePanel();
      } else {
        showPanel('<div class="rc-err">Could not write into the message box. The text was copied — paste it with Ctrl+V.</div>');
        navigator.clipboard.writeText(text).catch(() => {});
      }
    };
  });
}

document.addEventListener('keydown', (e) => {
  if (e.altKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyS') { e.preventDefault(); onSuggest(); }
  if (e.key === 'Escape') hidePanel();
}, true);

// WhatsApp re-renders the footer on every chat switch; re-attach the button.
new MutationObserver(() => { ensureButton(); }).observe(document.body, { childList: true, subtree: true });
ensureButton();
