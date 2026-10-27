// iterm-dash page: live grid, enlarged view, hiding, shortcuts and reconnect.
import { chooseLayout } from './layout.js';
import { nextNeedsYou, moveFocus, shouldHandleKey, keepFocus } from './nav.js';
import { createClickRouter } from './clicks.js';
import { formatAgo } from './format.js';
import { renderRunsHTML } from './rows.js';

const GAP = 10;
const LINE = 1.2;
const MAX_FONT = 13;
const FONT_MIN = 4;
const FONT_MAX = 16;
const FONT_STEP = 0.5;
const HEX = /^#[0-9a-fA-F]{6}$/;
const TONES = new Set(['waiting', 'error', 'done', 'working', 'other', 'unknown', 'shell']);
const REPEATABLE = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '-', '=', '+']);

const params = new URLSearchParams(location.search);
const TOKEN = params.get('token') || '';
const DEBUG = params.get('debug') === '1';
const $ = (id) => document.getElementById(id);
const ui = {
  grid: $('grid'), main: $('main'), empty: $('empty'), banner: $('banner'), conn: $('conn'),
  summary: $('summary'), dbgSum: $('dbg-sum'), showAll: $('show-all'), showAllExtra: $('show-all-extra'),
  menuWrap: $('menu-wrap'), hiddenBtn: $('hidden-btn'), hiddenCount: $('hidden-count'), menu: $('hidden-menu'),
  helpBtn: $('help-btn'), help: $('help'), helpFont: $('help-font'), overlay: $('overlay'), scrim: $('scrim'),
  panel: $('panel'), ovJump: $('ov-jump'), ovClose: $('ov-close'), toast: $('toast'), tpl: $('cell-tpl'),
};

const st = {
  ready: false,
  settings: { show_all: false, min_font_px: 7 },
  sessions: [], // every session, in the server's grid order
  visible: [], // the sessions that get a cell
  screens: new Map(), // id -> { cols, rows, fg, bg, lines }
  statuses: new Map(), // id -> status message
  cells: new Map(), // id -> cell parts
  layout: null,
  focusedId: null,
  enlargedId: null,
  lastNId: null,
  pendingSettings: 0, // our settings messages the server has not echoed yet
  pendingHide: new Map(), // id -> hidden state we asked for and have not seen back
};
const dirty = { layout: false, overlayLayout: false, overlay: false, bar: false, cells: new Set() };
const stats = new Map(); // debug only: id -> { n, pendingTs, last, max }
const samples = []; // debug only: every paint delay in ms
if (DEBUG) {
  document.body.classList.add('debug');
  ui.dbgSum.hidden = false;
  window.itermDashDebug = { samples };
}

// -- measuring --------------------------------------------------------------
function measureCharWidth() {
  const ctx = document.createElement('canvas').getContext('2d');
  ctx.font = '100px Menlo, "SF Mono", monospace';
  return ctx.measureText('0'.repeat(50)).width / 50 / 100;
}

// Space a cell spends on things other than preview text: x is per side pair, y includes header and detail.
function measureChrome(root) {
  const sum = (el, ...props) => {
    const cs = getComputedStyle(el);
    return props.reduce((a, p) => a + (parseFloat(cs[p]) || 0), 0);
  };
  const screen = root.querySelector('.screen');
  const x = sum(root, 'borderLeftWidth', 'borderRightWidth') + sum(screen, 'paddingLeft', 'paddingRight');
  const y = sum(root, 'borderTopWidth', 'borderBottomWidth') + sum(screen, 'paddingTop', 'paddingBottom')
    + root.querySelector('.head').getBoundingClientRect().height
    + root.querySelector('.detail').getBoundingClientRect().height;
  return { x, y: Math.ceil(y) + 1 }; // +1 px so sub-pixel track rounding never clips the last row
}

function measureCellChrome() {
  const p = partsOf(ui.tpl.content.firstElementChild.cloneNode(true));
  p.root.style.cssText = 'position:absolute;visibility:hidden;width:600px;height:300px';
  for (const [el, text] of [[p.num, '1'], [p.title, 'Probe'], [p.pill, 'Working'], [p.ago, '9s'], [p.detail, 'Probe']]) {
    el.textContent = text;
  }
  ui.main.append(p.root);
  const chrome = measureChrome(p.root);
  p.root.remove();
  return chrome;
}

const CHAR_W = measureCharWidth();
const sane = (...vals) => vals.every((v) => Number.isFinite(v) && v > 0);
const okLayout = (L) => L && L.columns > 0 && sane(L.fontPx, L.visibleRows);

// -- cells and views ----------------------------------------------------------
function partsOf(root) {
  const q = (sel) => root.querySelector(sel);
  return {
    root, num: q('.num'), title: q('.title'), pill: q('.pill'), ago: q('.ago'), detail: q('.detail'), dbg: q('.dbg'),
    tone: '', agoText: '', headDirty: true,
    view: { screenEl: q('.screen'), rowsEl: q('.rows'), els: [], start: -1, full: true, rows: new Set(), bg: '', fg: '' },
  };
}

function makeCell(id) {
  const p = partsOf(ui.tpl.content.firstElementChild.cloneNode(true));
  p.id = id;
  p.root.dataset.id = id;
  if (id === st.focusedId) p.root.classList.add('focused');
  return p;
}

const titleOf = (s) => s.title || s.job || 'session';

function markView(view, full, idxs) {
  if (full) view.full = true;
  else for (const i of idxs) view.rows.add(i);
}

// Paint the bottom n rows of a screen. A rebuild swaps every row in with one DOM call, so the cell never blanks.
function paintView(v, scr, n) {
  const start = scr.rows - n;
  if (v.full || v.start !== start || v.els.length !== n) {
    const els = [];
    for (let i = start; i < scr.rows; i++) {
      const d = document.createElement('div');
      d.innerHTML = renderRunsHTML(scr.lines[i]);
      els.push(d);
    }
    v.rowsEl.replaceChildren(...els);
    v.els = els;
    v.start = start;
    v.full = false;
  } else {
    for (const i of v.rows) if (i >= start && i < scr.rows) v.els[i - start].innerHTML = renderRunsHTML(scr.lines[i]);
  }
  v.rows.clear();
  const bg = HEX.test(scr.bg) ? scr.bg : '';
  const fg = HEX.test(scr.fg) ? scr.fg : '';
  if (v.bg !== bg) v.screenEl.style.background = v.bg = bg;
  if (v.fg !== fg) v.screenEl.style.color = v.fg = fg;
}

function rgba(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

function paintStatus(p, id) {
  const s = st.statuses.get(id);
  const tone = s && TONES.has(s.tone) ? s.tone : '';
  if (p.tone !== tone) {
    if (p.tone) p.root.classList.remove(`t-${p.tone}`);
    if (tone) p.root.classList.add(`t-${tone}`);
    p.tone = tone;
  }
  const color = s && HEX.test(s.color) ? s.color : '#888888';
  p.pill.textContent = s ? String(s.label || '') : '';
  p.pill.style.background = tone === 'waiting' ? color : rgba(color, 0.15);
  p.pill.style.color = tone === 'waiting' ? '#fff' : color;
  p.detail.textContent = s && s.detail ? String(s.detail) : '';
  p.detail.title = p.detail.textContent;
  paintAgo(p, id, Date.now() / 1000);
}

function paintAgo(p, id, now) {
  const s = st.statuses.get(id);
  const text = s && Number.isFinite(s.changed_at) ? formatAgo(now - s.changed_at) : '';
  if (p.agoText !== text) p.ago.textContent = p.agoText = text;
}

function paintPart(p, id, shown) {
  if (p.headDirty) {
    p.headDirty = false;
    paintStatus(p, id);
  }
  const scr = st.screens.get(id);
  if (!scr || !(shown > 0)) return false;
  paintView(p.view, scr, Math.min(scr.rows, shown));
  return true;
}

// -- grid -----------------------------------------------------------------------
function reconcile() {
  const prevIds = st.visible.map((s) => s.id);
  st.visible = st.sessions.filter((s) => !s.hidden && (st.settings.show_all || s.is_claude));
  const keep = new Set(st.visible.map((s) => s.id));
  for (const [id, c] of st.cells) {
    if (!keep.has(id)) {
      c.root.remove();
      st.cells.delete(id);
    }
  }
  let ref = ui.grid.firstElementChild;
  st.visible.forEach((s, i) => {
    let c = st.cells.get(s.id);
    if (!c) st.cells.set(s.id, (c = makeCell(s.id)));
    if (c.root === ref) ref = ref.nextElementSibling;
    else ui.grid.insertBefore(c.root, ref);
    c.num.textContent = i < 9 ? String(i + 1) : '';
    c.title.textContent = titleOf(s);
    c.headDirty = true;
    dirty.cells.add(s.id);
  });
  setFocus(keepFocus(prevIds, st.visible.map((s) => s.id), st.focusedId)); // cell 1 on first load
  if (st.enlargedId && !keep.has(st.enlargedId)) closeOverlay();
  else if (st.enlargedId) paintOverlayTitle();
  if (!ui.menu.hidden) renderMenu();
  dirty.layout = dirty.bar = true;
  schedule();
}

function applyLayout() {
  let cols = 0;
  let rows = 0;
  for (const s of st.visible) {
    const scr = st.screens.get(s.id);
    const d = scr && sane(scr.cols, scr.rows) ? scr : s;
    if (sane(d.cols, d.rows)) {
      cols = Math.max(cols, d.cols);
      rows = Math.max(rows, d.rows);
    }
  }
  const width = ui.grid.clientWidth;
  const height = ui.grid.clientHeight;
  // Not laid out yet, or no session has reported a size: keep the previous layout.
  if (!st.visible.length || !sane(width, height, cols, rows, CHAR_W)) return;
  const base = {
    height, count: st.visible.length, cols, rows, charWidthPx: CHAR_W, lineHeight: LINE,
    minFontPx: st.settings.min_font_px, maxFontPx: MAX_FONT, headerPx: CELL_CHROME.y, gapPx: GAP,
  };
  // Each column loses CELL_CHROME.x to border and padding. Re-run on the width that is left for the chosen
  // column count; if that changes the count, try once more and otherwise keep the narrower, safe answer.
  const first = chooseLayout({ ...base, width });
  let L = chooseLayout({ ...base, width: width - first.columns * CELL_CHROME.x });
  if (L.columns !== first.columns) {
    const again = chooseLayout({ ...base, width: width - L.columns * CELL_CHROME.x });
    if (again.columns === L.columns) L = again;
  }
  if (!okLayout(L)) return;
  const prev = st.layout;
  st.layout = L;
  if (!prev || prev.columns !== L.columns || prev.cellRows !== L.cellRows) {
    ui.grid.style.gridTemplateColumns = `repeat(${L.columns}, minmax(0, 1fr))`;
    ui.grid.style.gridTemplateRows = `repeat(${L.cellRows}, minmax(0, 1fr))`;
  }
  if (!prev || prev.fontPx !== L.fontPx) ui.grid.style.setProperty('--fs', `${L.fontPx.toFixed(3)}px`);
  if (!prev || prev.visibleRows !== L.visibleRows) for (const s of st.visible) dirty.cells.add(s.id);
}

function setFocus(id) {
  if (st.focusedId === id) return;
  st.cells.get(st.focusedId)?.root.classList.remove('focused');
  st.focusedId = id;
  st.cells.get(id)?.root.classList.add('focused');
}

function paintBar() {
  const counts = {};
  for (const s of st.visible) {
    const tone = st.statuses.get(s.id)?.tone;
    counts[tone] = (counts[tone] || 0) + 1;
  }
  const n = st.visible.length;
  const segs = [[`${n} ${st.settings.show_all ? 'session' : 'Claude session'}${n === 1 ? '' : 's'}`, '']];
  const add = (tone, text) => counts[tone] && segs.push([text(counts[tone]), tone]);
  add('waiting', (c) => `${c} need${c === 1 ? 's' : ''} input`);
  add('error', (c) => `${c} error${c === 1 ? '' : 's'}`);
  add('done', (c) => `${c} done`);
  add('working', (c) => `${c} working`);
  ui.summary.replaceChildren(...segs.flatMap(([text, cls], i) => {
    const el = document.createElement('span');
    el.textContent = text;
    if (cls) el.className = cls;
    return i ? [' · ', el] : [el];
  }));
  const needs = (counts.waiting || 0) + (counts.error || 0) + (counts.done || 0);
  document.title = needs ? `(${needs}) iterm-dash` : 'iterm-dash';
  const extra = st.sessions.filter((s) => !s.is_claude && !s.hidden).length;
  ui.showAll.setAttribute('aria-pressed', String(st.settings.show_all));
  ui.showAllExtra.textContent = extra ? ` (+${extra})` : '';
  const hidden = st.sessions.filter((s) => s.hidden).length;
  ui.hiddenCount.textContent = String(hidden);
  ui.helpFont.textContent = String(st.settings.min_font_px);
  ui.empty.hidden = !st.ready || n > 0;
  if (!ui.empty.hidden) {
    let text = `No ${st.settings.show_all ? '' : 'Claude '}sessions to show.`;
    if (extra && !st.settings.show_all) text += ' Press a to show all sessions.';
    if (hidden) text += ' Press H to bring back a hidden one.';
    ui.empty.textContent = text;
  }
}

// -- frame loop -----------------------------------------------------------------
let raf = 0;
function schedule() {
  if (!raf) raf = requestAnimationFrame(flush);
}

function flush() {
  raf = 0;
  if (dirty.layout) {
    dirty.layout = false;
    applyLayout();
  }
  if (dirty.overlayLayout && st.enlargedId) layoutOverlay();
  dirty.overlayLayout = false;
  const now = Date.now() / 1000;
  for (const id of dirty.cells) {
    const c = st.cells.get(id);
    if (c && paintPart(c, id, st.layout?.visibleRows) && DEBUG) notePaint(id, now);
  }
  dirty.cells.clear();
  if (dirty.overlay && st.enlargedId) paintPart(ov, st.enlargedId, ov.shown);
  dirty.overlay = false;
  if (dirty.bar) {
    dirty.bar = false;
    paintBar();
  }
}

function notePaint(id, now) {
  const d = stats.get(id);
  if (!d || d.pendingTs == null) return;
  const ms = (now - d.pendingTs) * 1000;
  d.pendingTs = null;
  d.last = ms;
  d.max = Math.max(d.max, ms);
  if (samples.push(ms) > 10000) samples.splice(0, 5000);
}

function tick() {
  const now = Date.now() / 1000;
  for (const c of st.cells.values()) paintAgo(c, c.id, now);
  if (st.enlargedId) paintAgo(ov, st.enlargedId, now);
  if (!DEBUG) return;
  let ups = 0;
  let worst = 0;
  for (const c of st.cells.values()) {
    const d = stats.get(c.id) || { n: 0, last: 0, max: 0 };
    c.dbg.textContent = `${d.n} upd/s · paint ${Math.round(d.last)} ms · max ${Math.round(d.max)} ms`;
    ups += d.n;
    worst = Math.max(worst, d.max);
    d.n = 0;
    d.max = 0;
  }
  ui.dbgSum.textContent = `debug: ${ups} upd/s · max paint ${Math.round(worst)} ms`;
}

// -- enlarged view ----------------------------------------------------------------
const ov = partsOf(ui.panel);
ov.shown = 0;
ov.clickAt = -Infinity; // when the click that opened the overlay happened; -Infinity if a key opened it

function paintOverlayTitle() {
  const i = st.visible.findIndex((s) => s.id === st.enlargedId);
  ov.num.textContent = i >= 0 && i < 9 ? String(i + 1) : '';
  ov.title.textContent = i >= 0 ? titleOf(st.visible[i]) : '';
}

function layoutOverlay() {
  const scr = st.screens.get(st.enlargedId);
  const chrome = measureChrome(ui.panel);
  const availW = ui.overlay.clientWidth - 48;
  const availH = ui.overlay.clientHeight - 48;
  if (!scr || !sane(availW - chrome.x, availH, scr.cols, scr.rows, CHAR_W)) return;
  const L = chooseLayout({
    width: availW - chrome.x, height: availH, count: 1, cols: scr.cols, rows: scr.rows, charWidthPx: CHAR_W,
    lineHeight: LINE, minFontPx: 0, maxFontPx: 18, headerPx: chrome.y, gapPx: GAP,
  });
  if (!okLayout(L)) return;
  ui.panel.style.setProperty('--fs', `${L.fontPx.toFixed(3)}px`);
  const textW = Math.ceil(scr.cols * CHAR_W * L.fontPx) + chrome.x + 1;
  ui.panel.style.width = `${Math.min(availW, Math.max(520, textW))}px`;
  ov.shown = L.visibleRows;
}

function openOverlay(id, clickAt = -Infinity) {
  if (!st.cells.has(id)) return;
  st.enlargedId = id;
  ov.clickAt = clickAt;
  setFocus(id);
  ov.view.full = true;
  ov.headDirty = true;
  ui.overlay.hidden = false;
  paintOverlayTitle();
  layoutOverlay();
  paintPart(ov, id, ov.shown); // paint now, so the overlay never shows up empty
}

function closeOverlay() {
  st.enlargedId = null;
  ui.overlay.hidden = true;
  ov.view.rowsEl.replaceChildren();
  ov.view.els = [];
  ov.view.start = -1;
  ov.shown = 0;
}

// -- hidden menu, help and toast ----------------------------------------------------
function renderMenu() {
  const items = st.sessions.filter((s) => s.hidden).map((s) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'menu-item';
    b.setAttribute('role', 'menuitem');
    b.dataset.id = s.id;
    const t = document.createElement('span');
    t.className = 'mt';
    t.textContent = titleOf(s);
    const j = document.createElement('span');
    j.className = 'mj';
    j.textContent = s.is_claude ? 'Claude' : s.job || 'shell';
    b.append(t, j);
    return b;
  });
  if (!items.length) {
    const e = document.createElement('div');
    e.className = 'menu-empty';
    e.textContent = 'Nothing hidden. Press h to hide the focused cell.';
    items.push(e);
  }
  ui.menu.replaceChildren(...items);
}

function setMenu(open) {
  ui.menu.hidden = !open;
  ui.hiddenBtn.setAttribute('aria-expanded', String(open));
  if (open) {
    renderMenu();
    ui.menu.querySelector('.menu-item')?.focus();
  } else if (ui.menu.contains(document.activeElement)) document.activeElement.blur();
}

function unhideItem(item) {
  const items = [...ui.menu.querySelectorAll('.menu-item')];
  const i = items.indexOf(item);
  setHidden(item.dataset.id, false);
  const rest = ui.menu.querySelectorAll('.menu-item');
  if (rest.length) rest[Math.min(i, rest.length - 1)].focus();
}

function menuKey(k) {
  const items = [...ui.menu.querySelectorAll('.menu-item')];
  const i = items.indexOf(document.activeElement);
  if (k === 'ArrowDown' || k === 'ArrowUp') {
    const n = items.length;
    if (n) items[i < 0 ? (k === 'ArrowDown' ? 0 : n - 1) : (i + (k === 'ArrowDown' ? 1 : n - 1)) % n].focus();
    return true;
  }
  if ((k === 'Enter' || k === ' ') && i >= 0) {
    unhideItem(items[i]);
    return true;
  }
  if (k === 'Escape' || k === 'H') {
    setMenu(false);
    return true;
  }
  return false;
}

let toastTimer = 0;
function toast(text) {
  ui.toast.hidden = true;
  void ui.toast.offsetWidth; // restart the fade animation
  ui.toast.textContent = text;
  ui.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (ui.toast.hidden = true), 1800);
}

// -- actions ------------------------------------------------------------------------
function jump(id) {
  if (!id || !send({ type: 'focus', id })) return;
  setFocus(id);
  if (st.enlargedId) closeOverlay();
}

function setHidden(id, hidden) {
  const s = st.sessions.find((x) => x.id === id);
  if (!s || !send({ type: hidden ? 'hide' : 'unhide', id })) return;
  st.pendingHide.set(id, hidden);
  s.hidden = hidden;
  reconcile();
  if (hidden) toast(`Hid “${titleOf(s)}”. Press H to bring it back.`);
}

function applySettings(patch) {
  const s = st.settings;
  const showAll = typeof patch.show_all === 'boolean' ? patch.show_all : s.show_all;
  const font = sane(patch.min_font_px) ? Math.min(FONT_MAX, Math.max(FONT_MIN, patch.min_font_px)) : s.min_font_px;
  const showChanged = showAll !== s.show_all;
  const fontChanged = font !== s.min_font_px;
  st.settings = { show_all: showAll, min_font_px: font };
  if (showChanged) reconcile();
  if (fontChanged) dirty.layout = true;
  dirty.bar = true;
  schedule();
}

function sendSettings(patch) {
  if (!send({ type: 'settings', ...patch })) return false;
  st.pendingSettings++;
  applySettings(patch);
  return true;
}

function stepFont(dir) {
  const cur = st.settings.min_font_px;
  const next = Math.min(FONT_MAX, Math.max(FONT_MIN, Math.round((cur + dir * FONT_STEP) * 2) / 2));
  if (next === cur || sendSettings({ min_font_px: next })) toast(`Minimum text size: ${next} px`);
}

function arrow(key) {
  const n = st.visible.length;
  if (!n) return;
  const i = st.visible.findIndex((s) => s.id === st.focusedId);
  const id = st.visible[i < 0 ? 0 : moveFocus(i, key, st.layout ? st.layout.columns : 1, n)].id;
  setFocus(id);
  if (st.enlargedId && st.enlargedId !== id) openOverlay(id);
}

function nextNeeded() {
  const cells = st.visible.map((s) => {
    const x = st.statuses.get(s.id);
    return { id: s.id, tone: x?.tone, changedAt: x?.changed_at ?? 0 };
  });
  const id = nextNeedsYou(cells, st.lastNId);
  if (!id) return toast('Nothing needs you right now.');
  st.lastNId = id;
  jump(id);
}

function closeTop() {
  if (!ui.menu.hidden) setMenu(false);
  else if (!ui.help.hidden) ui.help.hidden = true;
  else if (st.enlargedId) closeOverlay();
  else return false;
  return true;
}

function handleKey(k) {
  if (!ui.menu.hidden && menuKey(k)) return true;
  switch (k) {
    case 'Escape': return closeTop();
    case ' ':
      if (st.enlargedId) closeOverlay();
      else if (st.focusedId) openOverlay(st.focusedId);
      return true;
    case 'Enter': {
      const id = st.enlargedId || st.focusedId;
      if (id) jump(id);
      return Boolean(id);
    }
    case 'ArrowLeft': case 'ArrowRight': case 'ArrowUp': case 'ArrowDown': arrow(k); return true;
    case 'n': nextNeeded(); return true;
    case 'h': if (st.focusedId) setHidden(st.focusedId, true); return true;
    case 'H': setMenu(ui.menu.hidden); return true;
    case 'a': sendSettings({ show_all: !st.settings.show_all }); return true;
    case '?': ui.help.hidden = !ui.help.hidden; return true;
    case '-': stepFont(-1); return true;
    case '=': case '+': stepFont(1); return true;
    default:
      if (!/^[1-9]$/.test(k)) return false;
      jump(st.visible[Number(k) - 1]?.id);
      return true;
  }
}

// -- connection ---------------------------------------------------------------------
let ws = null;
let failures = 0;

function send(msg) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(msg));
  return true;
}

function setOnline(on) {
  const down = !on && failures >= 3;
  ui.conn.hidden = on || down || !st.ready;
  ui.banner.hidden = !down;
  document.body.classList.toggle('offline', down);
}

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const sock = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(TOKEN)}`);
  let opened = false;
  ws = sock;
  sock.onopen = () => {
    opened = true;
    failures = 0;
    st.pendingSettings = 0;
    st.pendingHide.clear();
    setOnline(true);
  };
  sock.onmessage = (ev) => {
    try {
      onMessage(JSON.parse(ev.data));
    } catch (err) {
      console.warn('iterm-dash: skipped a bad message', err);
    }
  };
  sock.onclose = () => {
    if (ws === sock) ws = null;
    if (!opened) failures++;
    setOnline(false);
    setTimeout(connect, Math.min(5000, 500 * 2 ** failures)); // 0.5 s, 1 s, 2 s, 4 s, then every 5 s
  };
}

function onMessage(m) {
  if (m.type === 'settings') {
    if (st.pendingSettings > 0 && --st.pendingSettings > 0) return; // an older echo of our own change
    applySettings(m);
  } else if (m.type === 'sessions') onSessions(m);
  else if (m.type === 'screen') onScreen(m);
  else if (m.type === 'status') onStatus(m);
}

function onSessions(m) {
  const list = (Array.isArray(m.sessions) ? m.sessions : []).filter((s) => s && typeof s.id === 'string');
  for (const s of list) {
    if (!st.pendingHide.has(s.id)) continue;
    const want = st.pendingHide.get(s.id);
    if (Boolean(s.hidden) === want) st.pendingHide.delete(s.id);
    else s.hidden = want; // the server has not caught up with our latest hide or unhide yet
  }
  st.sessions = list;
  st.ready = true;
  const live = new Set(list.map((s) => s.id));
  for (const map of [st.screens, st.statuses, stats]) for (const id of [...map.keys()]) if (!live.has(id)) map.delete(id);
  reconcile();
}

function onScreen(m) {
  const cols = Number(m.cols);
  const rows = Number(m.rows);
  if (typeof m.id !== 'string' || !Number.isInteger(rows) || !Number.isInteger(cols) || rows < 0 || cols < 0) return;
  let scr = st.screens.get(m.id);
  const resized = !scr || scr.rows !== rows || scr.cols !== cols;
  const full = Boolean(m.full) || resized;
  if (full) {
    scr = { cols, rows, fg: '', bg: '', lines: Array.from({ length: rows }, () => []) };
    st.screens.set(m.id, scr);
  }
  scr.fg = m.fg;
  scr.bg = m.bg;
  const changed = [];
  for (const [k, row] of Object.entries(m.lines || {})) {
    const i = Number(k);
    if (Number.isInteger(i) && i >= 0 && i < rows && Array.isArray(row) && row.every(Array.isArray)) {
      scr.lines[i] = row;
      changed.push(i);
    }
  }
  const cell = st.cells.get(m.id);
  if (cell) {
    markView(cell.view, full, changed);
    dirty.cells.add(m.id);
    if (resized) dirty.layout = true;
  }
  if (st.enlargedId === m.id) {
    markView(ov.view, full, changed);
    dirty.overlay = true;
    if (resized) dirty.overlayLayout = true;
  }
  if (DEBUG && cell) {
    const d = stats.get(m.id) || { n: 0, pendingTs: null, last: 0, max: 0 };
    stats.set(m.id, d);
    d.n++;
    if (d.pendingTs == null && Number.isFinite(m.ts)) d.pendingTs = m.ts;
  }
  schedule();
}

function onStatus(m) {
  if (typeof m.id !== 'string') return;
  st.statuses.set(m.id, m);
  const cell = st.cells.get(m.id);
  if (cell) {
    cell.headDirty = true;
    dirty.cells.add(m.id);
  }
  if (st.enlargedId === m.id) {
    ov.headDirty = true;
    dirty.overlay = true;
  }
  dirty.bar = true;
  schedule();
}

// -- wiring ---------------------------------------------------------------------------
const CELL_CHROME = measureCellChrome();
let lastCellClickAt = -Infinity;
const routeClick = createClickRouter({ onSingle: (id) => openOverlay(id, lastCellClickAt), onDouble: jump });

// A click that closes something (overlay, list, menu, a hidden cell) must not let the second click of a
// double-click act on whatever is now under the pointer, so clicks are swallowed for a moment afterwards.
let ignoreClicksUntil = 0;
const swallowClicks = () => (ignoreClicksUntil = performance.now() + 500);
document.addEventListener('click', (e) => {
  if (performance.now() < ignoreClicksUntil) {
    e.stopPropagation();
    e.preventDefault();
  }
}, true);

ui.grid.addEventListener('click', (e) => {
  const el = e.target.closest('.cell');
  if (!el) return;
  if (e.target.closest('.hide')) {
    swallowClicks();
    setHidden(el.dataset.id, true);
  } else {
    lastCellClickAt = performance.now();
    routeClick(el.dataset.id);
  }
});
document.addEventListener('mousedown', (e) => {
  if (e.target.closest('button')) e.preventDefault(); // keep focus on the page so shortcuts keep working
  if (!ui.menu.hidden && !ui.menuWrap.contains(e.target)) {
    setMenu(false);
    swallowClicks();
  }
});
ui.showAll.addEventListener('click', () => sendSettings({ show_all: !st.settings.show_all }));
ui.hiddenBtn.addEventListener('click', () => setMenu(ui.menu.hidden));
ui.menu.addEventListener('click', (e) => {
  const item = e.target.closest('.menu-item');
  if (item) unhideItem(item);
});
ui.helpBtn.addEventListener('click', () => (ui.help.hidden = !ui.help.hidden));
ui.help.addEventListener('click', (e) => {
  if (e.target !== ui.help) return;
  ui.help.hidden = true;
  swallowClicks();
});
ui.overlay.addEventListener('click', (e) => {
  const id = st.enlargedId;
  if (!id) return;
  const onClose = ui.ovClose.contains(e.target);
  // A double-click slower than the router's window opens the overlay on its first click, and its second click
  // lands somewhere on the overlay. Wherever that is (except Close), it is the jump the user meant.
  const lateDouble = e.detail > 1 && !onClose && performance.now() - ov.clickAt <= 500;
  if (lateDouble || ui.ovJump.contains(e.target)) jump(id);
  else if (onClose || e.target === ui.scrim) closeOverlay();
  else return;
  swallowClicks();
});
window.addEventListener('keydown', (e) => {
  if (!shouldHandleKey(e) || e.isComposing) return;
  if (e.repeat && !REPEATABLE.has(e.key)) return; // holding n, h or 1-9 must not fire it again and again
  if (handleKey(e.key)) e.preventDefault();
});
new ResizeObserver(() => {
  dirty.layout = true;
  if (st.enlargedId) dirty.overlayLayout = dirty.overlay = true;
  schedule();
}).observe(ui.main);
setInterval(tick, 1000);
connect();
