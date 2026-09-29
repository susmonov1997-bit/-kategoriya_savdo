/* Savdo Mini App — API'ga ulangan ishchi versiya.
   Barcha hisob-kitob serverda (FastAPI); bu fayl faqat holat (filtrlar, davr, qirqim) va chizishni boshqaradi. */
(() => {
'use strict';

const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  try { tg.ready(); tg.expand(); } catch (e) {}
  if (tg.colorScheme) document.documentElement.dataset.theme = tg.colorScheme;
  try { tg.onEvent('themeChanged', () => { document.documentElement.dataset.theme = tg.colorScheme; }); } catch (e) {}
}
const $ = id => document.getElementById(id);

// ---------------------------------------------------------------- formatlar
const nf1 = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1, minimumFractionDigits: 1});
const nf2 = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2, minimumFractionDigits: 2});
const nf0 = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
function money(v) {
  if (v == null) return '—';
  const a = Math.abs(v);
  if (a >= 1e9) return nf2.format(v / 1e9) + ' mlrd';
  if (a >= 1e6) return nf1.format(v / 1e6) + ' mln';
  if (a >= 1e3) return nf0.format(v / 1e3) + ' ming';
  return nf0.format(v);
}
const pct = v => v == null ? '—' : nf1.format(v) + '%';
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
function pill(v, pp) {
  if (v == null || !isFinite(v)) return `<span class="pill flat">—</span>`;
  const cls = Math.abs(v) < 0.05 ? 'flat' : v > 0 ? 'up' : 'down';
  const arrow = cls === 'up' ? '▲' : cls === 'down' ? '▼' : '';
  return `<span class="pill ${cls} num">${arrow}${nf1.format(Math.abs(v))}${pp ? ' pp' : '%'}</span>`;
}

// ---------------------------------------------------------------- sanalar (ISO, UTC)
const D = s => new Date(s + 'T00:00:00Z');
const iso = d => d.toISOString().slice(0, 10);
const addDays = (s, n) => { const d = D(s); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
const diffDays = (a, b) => Math.round((D(b) - D(a)) / 86400000);
const dm = s => s ? s.slice(8, 10) + '.' + s.slice(5, 7) : '';

// ---------------------------------------------------------------- API
class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
async function api(path, body) {
  const headers = {'Content-Type': 'application/json'};
  if (tg && tg.initData) headers['Authorization'] = 'tma ' + tg.initData;
  const res = await fetch('/api/' + path, {method: body ? 'POST' : 'GET', headers, body: body ? JSON.stringify(body) : undefined});
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) {}
    throw new ApiError(res.status, typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return res.json();
}

// ---------------------------------------------------------------- holat
let META = null;
const st = {
  cat: null, from: null, to: null, preset: '7', compare: 'prev',
  f: {},                 // dim -> Set(key)   (kalitlar API'dagidek matn)
  mode: 'tree',          // tree (daraxt) | pivot (kesma)
  rows: 'region', cols: 'attr:0', sort: 'amount',
  chartMetric: 'amount', shareView: 'struct', pvMode: 'amount',
  aExpand: {}, aOpen: null, topOpen: false, showAll: false,
};
const labels = {};       // "dim|key" -> nom (chip va breadcrumb uchun)
const remember = (d, k, l) => { labels[d + '|' + k] = l; };
const cat = () => META.categories.find(c => c.id === st.cat);
const attrDims = () => cat().attributes.map(a => a.dim);
const DIM_NAMES = {region: 'Hudud', cluster: 'Klaster', branch: 'Filial', brand: 'Brend', status: 'Status', sku: 'SKU'};
function dimName(d) {
  if (d.startsWith('attr:')) { const a = cat().attributes.find(a => a.dim === d); return a ? a.name : d; }
  return DIM_NAMES[d] || d;
}
const LOC_PATH = ['region', 'cluster', 'branch'];
const prodPath = () => cat().product_path;
const drillPath = () => cat().drill_path;
function nextDim(d) {
  const path = LOC_PATH.includes(d) ? drillPath() : prodPath();
  const i = path.indexOf(d);
  if (i < 0) return null;
  for (let j = i + 1; j < path.length; j++) if (!(st.f[path[j]] && st.f[path[j]].size === 1)) return path[j];
  return null;
}
const NUM_DIMS = {region: 'region_ids', cluster: 'cluster_ids', branch: 'branch_ids', sku: 'product_ids'};

function filters() {
  const F = {category_id: st.cat, date_from: st.from, date_to: st.to, compare: st.compare,
             region_ids: [], cluster_ids: [], branch_ids: [], product_ids: [], brands: [], statuses: [], attrs: {}};
  for (const d in st.f) {
    const vals = [...st.f[d]];
    if (!vals.length) continue;
    if (NUM_DIMS[d]) F[NUM_DIMS[d]] = vals.map(Number);
    else if (d === 'brand') F.brands = vals;
    else if (d === 'status') F.statuses = vals;
    else if (d.startsWith('attr:')) F.attrs[d.slice(5)] = vals;
  }
  return F;
}
function cmpRange() {
  if (st.compare === 'none') return null;
  const len = diffDays(st.from, st.to) + 1;
  if (st.compare === 'yoy') {
    const y = s => (+s.slice(0, 4) - 1) + s.slice(4);
    return [y(st.from), y(st.to)];
  }
  return [addDays(st.from, -len), addDays(st.from, -1)];
}

// ---------------------------------------------------------------- yuqori panel (A variant)
const PERIODS = [
  ['1', 'Kecha'], ['7', 'Oxirgi 7 kun'], ['14', 'Oxirgi 14 kun'], ['30', 'Oxirgi 30 kun'],
  ['week', 'Shu hafta'], ['lweek', "O'tgan hafta"], ['month', 'Shu oy'], ['lmonth', "O'tgan oy"]];
const PERIOD_NAME = Object.fromEntries(PERIODS);
const CMP_NAME = {prev: "O'tgan davr", yoy: "O'tgan yil", none: "Yo'q"};
function presetRange(k) {
  const to = META.data_to, dow = (D(to).getUTCDay() + 6) % 7;          // Du = 0
  const clamp = s => (META.data_from && s < META.data_from ? META.data_from : s);
  if (k === 'week') return [clamp(addDays(to, -dow)), to];
  if (k === 'lweek') { const mon = addDays(to, -dow - 7); return [clamp(mon), addDays(mon, 6)]; }
  if (k === 'month') return [clamp(to.slice(0, 8) + '01'), to];
  if (k === 'lmonth') { const last = addDays(to.slice(0, 8) + '01', -1); return [last.slice(0, 8) + '01', last]; }
  return [clamp(addDays(to, -(+k - 1))), to];
}
const periodText = () => (st.preset && PERIOD_NAME[st.preset]) ? PERIOD_NAME[st.preset]
  : (st.from === st.to ? dm(st.from) : `${dm(st.from)}–${dm(st.to)}`);
const LOC_DIMS = ['region', 'cluster', 'branch'];
const prodDims = () => [...attrDims(), 'brand', 'status'];
function renderHeader() {
  const nf = Object.values(st.f).filter(s => s.size).length;
  $('catBtn').innerHTML = `${esc(cat().name)} <span class="car">▾</span>`;
  $('perBtn').innerHTML = `📅 ${periodText()} <span class="car">▾</span>`;
  $('filBtn').innerHTML = `⚙ Filtr${nf ? ` <span class="badge">${nf}</span>` : ''}`;
  $('filBtn').classList.toggle('acc', nf > 0);
  const chips = [];
  for (const d of [...LOC_DIMS, ...prodDims()]) {
    const s = st.f[d]; if (!s || !s.size) continue;
    const val = s.size === 1 ? (labels[d + '|' + [...s][0]] || [...s][0]) : `${s.size} ta`;
    chips.push(`<button class="fchip" data-d="${d}" title="Olib tashlash"><b>${esc(dimName(d))}:</b> ${esc(val)} ✕</button>`);
  }
  if (chips.length > 1) chips.push(`<button class="fchip clr" id="clrAll">Tozalash</button>`);
  $('fchips').innerHTML = chips.join('');
  $('fchips').hidden = !chips.length;
  $('fchips').querySelectorAll('.fchip[data-d]').forEach(b => b.onclick = () => { delete st.f[b.dataset.d]; resetView(); load(); });
  const ca = $('clrAll'); if (ca) ca.onclick = () => { st.f = {}; resetView(); load(); };
}
function resetView() { st.showAll = false; TREE = null; for (const k of [...ROOTS.keys()]) if (k !== 'm') ROOTS.delete(k); }

// --- pastki oyna (umumiy)
function openSheetBox(title, bodyHtml, footHtml) {
  const host = $('sheetHost');
  host.innerHTML = `<div class="scrim" id="scrim"><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="shT">
    <header><div class="t"><h3 id="shT">${title}</h3><button class="x" id="shX" aria-label="Yopish">✕</button></div></header>
    <div class="sbody">${bodyHtml}</div>${footHtml ? `<footer>${footHtml}</footer>` : ''}</div></div>`;
  host.querySelector('#scrim').onclick = e => { if (e.target.id === 'scrim') closeSheet(); };
  host.querySelector('#shX').onclick = closeSheet;
  syncBack();
  return host;
}
function closeSheet() { $('sheetHost').innerHTML = ''; syncBack(); }
const sheetOpen = () => !!$('sheetHost').firstChild;

function openCatSheet() {
  const host = openSheetBox('Kategoriya', `<div class="optlist">${META.categories.map(c =>
    `<button class="optrow ${c.id === st.cat ? 'on' : ''}" data-id="${c.id}"><span>${esc(c.name)}</span><span class="a num">${c.skus} SKU</span></button>`).join('')}</div>`);
  host.querySelectorAll('.optrow').forEach(b => b.onclick = () => {
    const id = +b.dataset.id; closeSheet();
    if (id === st.cat) return;
    st.cat = id; st.f = {}; st.aExpand = {}; st.aOpen = null; st.rows = 'region'; st.cols = ''; resetView(); load();
  });
}

function openPeriodSheet() {
  let dPreset = st.preset, dFrom = st.from, dTo = st.to, dCmp = st.compare;
  const body = () => `
    <div class="pgrid">${PERIODS.map(([k, t]) => `<button class="opt2 ${dPreset === k ? 'on' : ''}" data-k="${k}">${t}</button>`).join('')}</div>
    <div class="lbl">Ixtiyoriy davr <span class="hint">(ma'lumot: ${dm(META.data_from)}–${dm(META.data_to)})</span></div>
    <div class="dates2"><input type="date" id="pF" value="${dFrom}" min="${META.data_from}" max="${META.data_to}" aria-label="Boshlanish">
      <span>—</span><input type="date" id="pT" value="${dTo}" min="${META.data_from}" max="${META.data_to}" aria-label="Tugash"></div>
    <div class="lbl">Solishtirish</div>
    <div class="pgrid three">${Object.entries(CMP_NAME).map(([k, t]) => `<button class="opt2 cmp ${dCmp === k ? 'on' : ''}" data-c="${k}">${t}</button>`).join('')}</div>`;
  const host = openSheetBox('Davr', body(), `<button class="btn primary" id="pApply">Qo'llash</button>`);
  const wire = () => {
    const sb = host.querySelector('.sbody');
    sb.querySelectorAll('.opt2[data-k]').forEach(b => b.onclick = () => { dPreset = b.dataset.k; [dFrom, dTo] = presetRange(dPreset); sb.innerHTML = body(); wire(); });
    sb.querySelectorAll('.opt2[data-c]').forEach(b => b.onclick = () => { dCmp = b.dataset.c; sb.innerHTML = body(); wire(); });
    sb.querySelector('#pF').onchange = e => { if (e.target.value) { dFrom = e.target.value; dPreset = ''; if (dFrom > dTo) dTo = dFrom; sb.innerHTML = body(); wire(); } };
    sb.querySelector('#pT').onchange = e => { if (e.target.value) { dTo = e.target.value; dPreset = ''; if (dTo < dFrom) dFrom = dTo; sb.innerHTML = body(); wire(); } };
  };
  wire();
  host.querySelector('#pApply').onclick = () => {
    st.preset = dPreset; st.from = dFrom; st.to = dTo; st.compare = dCmp; closeSheet(); resetView(); load();
  };
}

async function openFilterSheet() {
  const host = openSheetBox('Filtr', `<div class="empty">Yuklanmoqda…</div>`);
  let opts;
  try { opts = await api('options', filters()); }
  catch (e) { closeSheet(); showError(e); return; }
  const draft = {}; for (const d in st.f) draft[d] = new Set(st.f[d]);
  const open = new Set(Object.keys(draft).filter(d => draft[d].size));
  if (!open.size) open.add('region');
  for (const d in opts) opts[d].forEach(o => remember(d, o.key, o.label));
  for (const d in draft) for (const k of draft[d]) if (opts[d] && !opts[d].some(o => o.key === k)) opts[d].push({key: k, label: labels[d + '|' + k] || k, amount: 0});
  let q = '';
  const dimBlock = d => {
    const list = (opts[d] || []).filter(o => !q || String(o.label).toLowerCase().includes(q));
    const sel = draft[d] || new Set();
    if (q && !list.length) return '';
    const isOpen = q ? true : open.has(d);
    return `<div class="fgrp">
      <button class="fgrp-h" data-d="${d}"><span>${esc(dimName(d))}</span><span class="s">${sel.size ? `${sel.size} tanlangan · ` : ''}${(opts[d] || []).length} ta ${isOpen ? '▾' : '▸'}</span></button>
      ${isOpen ? `<div class="opts">${list.slice(0, 200).map(o => `<label class="opt ${o.amount ? '' : 'zero'}">
        <input type="checkbox" data-d="${d}" data-k="${esc(o.key)}" ${sel.has(o.key) ? 'checked' : ''}><span>${esc(o.label)}</span><span class="a num">${money(o.amount)}</span></label>`).join('')}
        ${list.length > 200 ? `<div class="hint" style="padding:6px 16px">Yana ${list.length - 200} ta — qidiruvdan foydalaning</div>` : ''}</div>` : ''}
    </div>`;
  };
  const body = () => {
    const loc = LOC_DIMS.map(dimBlock).join(''), prod = prodDims().map(dimBlock).join('');
    return `<input type="search" id="fQ" placeholder="Qidirish: hudud, filial, brend, qiymat…" autocomplete="off" value="${esc(q)}">
      ${loc ? `<div class="fsec">📍 Joy</div>${loc}` : ''}${prod ? `<div class="fsec">📦 Tovar</div>${prod}` : ''}
      ${!loc && !prod ? `<div class="empty">Hech narsa topilmadi</div>` : ''}`;
  };
  host.querySelector('.sbody').innerHTML = body();
  host.querySelector('.sheet').insertAdjacentHTML('beforeend',
    `<footer><button class="btn" id="fClr">Tozalash</button><button class="btn primary" id="fApply">Ko'rsatish</button></footer>`);
  const wire = () => {
    const sb = host.querySelector('.sbody');
    const qi = sb.querySelector('#fQ');
    qi.oninput = () => { q = qi.value.trim().toLowerCase(); sb.innerHTML = body(); wire(); const n = sb.querySelector('#fQ'); n.focus(); n.setSelectionRange(n.value.length, n.value.length); };
    sb.querySelectorAll('.fgrp-h').forEach(b => b.onclick = () => { const d = b.dataset.d; open.has(d) ? open.delete(d) : open.add(d); sb.innerHTML = body(); wire(); });
    sb.querySelectorAll('input[type=checkbox]').forEach(i => i.onchange = () => {
      const d = i.dataset.d, k = i.dataset.k; draft[d] = draft[d] || new Set();
      i.checked ? draft[d].add(k) : draft[d].delete(k);
      const h = i.closest('.fgrp').querySelector('.fgrp-h .s');
      h.textContent = `${draft[d].size ? `${draft[d].size} tanlangan · ` : ''}${(opts[d] || []).length} ta ▾`;
    });
  };
  wire();
  host.querySelector('#fClr').onclick = () => { for (const d in draft) draft[d].clear(); host.querySelector('.sbody').innerHTML = body(); wire(); };
  host.querySelector('#fApply').onclick = () => {
    st.f = {}; for (const d in draft) if (draft[d].size) st.f[d] = new Set(draft[d]);
    closeSheet(); resetView(); load();
  };
}

// ---------------------------------------------------------------- KPI
function renderKpis(s) {
  const C = s.current, Pv = s.previous, d = s.delta || {};
  $('cmpHint').textContent = s.compare_period
    ? `${dm(st.from)}–${dm(st.to)} vs ${dm(s.compare_period.from)}–${dm(s.compare_period.to)}${st.compare === 'yoy' ? ' (o\'tgan yil)' : ''}`
    : `${dm(st.from)}–${dm(st.to)}`;
  const neg = v => v < 0 ? ' neg' : '';
  const incPct = C.amount ? C.income / C.amount * 100 : null;
  $('kpis').innerHTML = `
    <div class="kpi wide">
      <div><div class="label">Savdo</div><div class="v num">${money(C.amount)}</div><div class="sub">${Pv ? pill(d.amount) : ''}</div></div>
      <div><div class="label">Gross marja</div><div class="v num${neg(C.margin)}">${money(C.margin)}</div>
        <div class="sub num"><span>${pct(C.margin_pct)}</span>${Pv ? pill(d.margin_pct_pp, true) : ''}</div></div>
    </div>
    <div class="kpi"><div class="label">Front marja</div><div class="v num${neg(C.gross)}">${money(C.gross)}</div>
      <div class="sub num"><span>${pct(C.gross_pct)}</span>${Pv ? pill(d.gross_pct_pp, true) : ''}</div></div>
    <div class="kpi"><div class="label">Qo'shimcha daromad</div><div class="v num">${money(C.income)}</div>
      <div class="sub num"><span>${pct(incPct)}</span>${Pv ? pill(d.income) : ''}</div></div>
    <div class="kpi"><div class="label">Dona</div><div class="v num">${nf0.format(C.qty)}</div>
      <div class="sub num">${Pv ? pill(d.qty) : ''}${C.bonus_qty ? `<span>bonus ${nf0.format(C.bonus_qty)}</span>` : ''}</div></div>
    <div class="kpi"><div class="label">O'rtacha narx</div><div class="v num">${C.avg_price ? money(C.avg_price) : '—'}</div>
      <div class="sub num">${Pv ? pill(d.avg_price) : ''}<span>${C.skus} SKU · ${C.branches} filial</span></div></div>`;
}

// ---------------------------------------------------------------- kunlik grafik
let DAILY = null;
function niceStep(raw) { if (raw <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(raw))); const f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p; }
function renderChart() {
  if (!DAILY) return;
  const m = st.chartMetric, series = DAILY.series, n = series.length, c = cmpRange();
  const cur = series.map(p => p[m]);
  const pk = 'prev_' + (m === 'amount' ? 'amount' : m === 'margin' ? 'margin' : 'qty');
  const prev = c ? series.map(p => p[pk] || 0) : null;
  $('legend').innerHTML = `<span><i style="background:var(--accent)"></i>Joriy ${dm(st.from)}–${dm(st.to)}</span>` +
    (c ? `<span><i style="background:var(--prev)"></i>${st.compare === 'yoy' ? 'O\'tgan yil' : 'O\'tgan davr'} ${dm(c[0])}–${dm(c[1])}</span>` : '');
  const host = $('chart');
  if (n < 2) { host.innerHTML = `<div class="empty">Bir kunlik davr uchun dinamika chizilmaydi — 7 kun yoki ko'proq tanlang.</div>`; return; }
  const W = Math.max(280, host.clientWidth || 340), H = 190, L = 58, R = 18, T = 12, Bm = 24;
  const all = cur.concat(prev || []);
  let lo = Math.min(0, ...all), hi = Math.max(...all, 1);
  const step = niceStep((hi - lo) / 4); lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  const x = i => L + (W - L - R) * (i / (n - 1));
  const y = v => T + (H - T - Bm) * (1 - (v - lo) / (hi - lo));
  const fmtAx = v => m === 'qty' ? nf0.format(v) : (Math.abs(hi) >= 1e9 ? nf1.format(v / 1e9) + ' mlrd' : nf0.format(v / 1e6) + ' mln');
  let g = '';
  for (let v = lo; v <= hi + step / 2; v += step) {
    g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" stroke-width="1" ${Math.abs(v) < 1e-9 ? '' : 'stroke-dasharray="2 3"'}/>`;
    g += `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="10" fill="var(--muted)">${fmtAx(v)}</text>`;
  }
  const every = Math.ceil(n / 7);
  for (let i = 0; i < n; i += every) g += `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === n - 1 ? 'end' : 'middle'}" font-size="10" fill="var(--muted)">${dm(series[i].date)}</text>`;
  const path = arr => arr.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  const base = y(Math.max(lo, 0));
  host.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Kunlik dinamika">
    ${g}
    <path d="${path(cur)}L${x(n - 1)},${base}L${x(0)},${base}Z" fill="var(--accent)" fill-opacity=".08"/>
    ${prev ? `<path d="${path(prev)}" fill="none" stroke="var(--prev)" stroke-width="2" stroke-linejoin="round"/>` : ''}
    <path d="${path(cur)}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="${x(n - 1)}" cy="${y(cur[n - 1])}" r="4" fill="var(--accent)" stroke="var(--card)" stroke-width="2"/>
    <g id="hov" visibility="hidden"><line id="hl" y1="${T}" y2="${H - Bm}" stroke="var(--muted)" stroke-width="1"/>
      <circle id="hc" r="4" fill="var(--accent)" stroke="var(--card)" stroke-width="2"/>
      ${prev ? '<circle id="hp" r="4" fill="var(--prev)" stroke="var(--card)" stroke-width="2"/>' : ''}</g>
    <rect x="${L}" y="0" width="${W - L - R}" height="${H}" fill="transparent" id="hit"/>
  </svg><div class="tip" hidden id="tip"></div>`;
  const svg = host.querySelector('svg'), tip = host.querySelector('#tip'), hov = svg.querySelector('#hov');
  const fmtV = v => m === 'qty' ? nf0.format(v) + ' dona' : money(v);
  const move = ev => {
    const rect = svg.getBoundingClientRect(); const px = (ev.clientX - rect.left) * W / rect.width;
    const i = Math.max(0, Math.min(n - 1, Math.round((px - L) / (W - L - R) * (n - 1))));
    hov.setAttribute('visibility', 'visible');
    for (const a of ['x1', 'x2']) svg.querySelector('#hl').setAttribute(a, x(i));
    svg.querySelector('#hc').setAttribute('cx', x(i)); svg.querySelector('#hc').setAttribute('cy', y(cur[i]));
    if (prev) { svg.querySelector('#hp').setAttribute('cx', x(i)); svg.querySelector('#hp').setAttribute('cy', y(prev[i])); }
    tip.hidden = false;
    tip.innerHTML = `<b>${dm(series[i].date)}</b> ${fmtV(cur[i])}` + (prev ? `<br>${dm(series[i].prev_date)} ${fmtV(prev[i])}` : '');
    tip.style.left = Math.max(60, Math.min(rect.width - 60, x(i) / W * rect.width)) + 'px';
  };
  const hit = svg.querySelector('#hit');
  hit.addEventListener('pointermove', move); hit.addEventListener('pointerdown', move);
  hit.addEventListener('pointerleave', () => { hov.setAttribute('visibility', 'hidden'); tip.hidden = true; });
}

// ---------------------------------------------------------------- qirqim: daraxt va kesma
let BD = null;          // kesma rejimi uchun
let TREE = null;        // daraxt ildizi (Jami)
let FOCUS = null;       // ulush kartasi ko'rsatadigan tugun
const OPEN = new Set(); // qirqim daraxtining ochiq tugunlari (qayta yuklanganda saqlanadi)
const SLOTS = 8, CHILD_LIMIT = 15;
function dimOptions(sel, includeNone) {
  const dims = [...LOC_PATH, ...attrDims(), 'brand', 'status', 'sku'];
  return (includeNone ? `<option value="">—</option>` : '') + dims.map(d => `<option value="${d}" ${d === sel ? 'selected' : ''}>${esc(dimName(d))}</option>`).join('');
}
// Umumiy daraxt yadrosi: qirqim (m), xususiyat qiymatlari (a:…) va TOP-10 SKU (s:…) uchun bir xil
const psig = path => path.map(p => `${p.dim}=${p.key}`).join('/');
const nsig = n => n.root + '|' + psig(n.path);
const ROOTS = new Map();           // root id -> ildiz tugun
const ORDERS = {drill: () => drillPath(), prod: () => prodPath(), loc: () => LOC_PATH};
function childDimFor(path, order = 'drill') {
  // yo'lda bor yoki filtrda bitta qiymat tanlangan daraja o'tkaziladi
  const used = new Set(path.map(p => p.dim));
  for (const d of ORDERS[order]()) {
    if (used.has(d)) continue;
    if (st.f[d] && st.f[d].size === 1) continue;
    return d;
  }
  return null;
}
function pathFilters(path) {
  const F = filters();
  for (const p of path) {
    if (NUM_DIMS[p.dim]) F[NUM_DIMS[p.dim]] = [Number(p.key)];
    else if (p.dim === 'brand') F.brands = [p.key];
    else if (p.dim === 'status') F.statuses = [p.key];
    else if (p.dim.startsWith('attr:')) F.attrs = {...F.attrs, [p.dim.slice(5)]: [p.key]};
  }
  return F;
}
const apiSort = () => (st.sort === 'delta' ? 'amount' : st.sort);
function sortRows(rows) {
  if (st.sort === 'delta') rows.sort((a, b) => (b.delta.amount ?? -1e9) - (a.delta.amount ?? -1e9));
  return rows;
}
function newRoot(rid, order, path, extra = {}) {
  const last = path[path.length - 1];
  const node = {root: rid, order, path, dim: last ? last.dim : null, key: last ? last.key : null, depth: path.length,
                childDim: childDimFor(path, order), children: null, open: false, showAll: false, ...extra};
  ROOTS.set(rid, node);
  return node;
}
async function fetchChildren(node) {
  const res = await api('breakdown', {...pathFilters(node.path), rows: node.childDim, sort: apiSort(), desc: st.sort !== 'name', limit: 500});
  node.total = res.total; node.total_rows = res.total_rows; node.raw = sortRows(res.rows);
  node.children = node.raw.map(r => {
    remember(node.childDim, r.key, r.label);
    const path = [...node.path, {dim: node.childDim, key: r.key}];
    return {root: node.root, order: node.order, path, dim: node.childDim, key: r.key, label: r.label, sub: r.sub, row: r,
            depth: node.depth + 1, childDim: childDimFor(path, node.order), children: null, open: false, showAll: false};
  });
  return res;
}
async function buildTree() {
  const root = newRoot('m', 'drill', [], {label: 'Jami', depth: 0, open: true});
  if (root.childDim) await fetchChildren(root);
  TREE = root; FOCUS = root;
  const reopen = async node => {            // oldin ochilgan shoxlarni qayta ochish
    for (const ch of node.children || []) {
      if (OPEN.has(nsig(ch)) && ch.childDim) {
        try { await fetchChildren(ch); ch.open = true; FOCUS = ch; await reopen(ch); } catch (e) { OPEN.delete(nsig(ch)); }
      }
    }
  };
  await reopen(root);
}
function findBySig(s) {
  const r = ROOTS.get(s.split('|')[0]); if (!r) return null;
  let hit = null; const walk = n => { if (nsig(n) === s) hit = n; (n.children || []).forEach(walk); }; walk(r); return hit;
}
function findParent(node) {
  const want = node.root + '|' + psig(node.path.slice(0, -1));
  return findBySig(want);
}
function rerender(rid) {
  if (rid === 'm') { renderTree(); renderShare(); }
  else if (rid.startsWith('a:')) renderAttrBlocks();
  else if (rid.startsWith('s:')) renderTopBody();
}
async function toggleNode(node) {
  if (!node.childDim) return;
  const main = node.root === 'm';
  if (node.open) {
    node.open = false;
    if (main) { OPEN.delete(nsig(node)); if (FOCUS && nsig(FOCUS).startsWith(nsig(node))) FOCUS = findParent(node) || TREE; }
    rerender(node.root); return;
  }
  node.loading = true; rerender(node.root);
  try { if (!node.children) await fetchChildren(node); node.open = true; if (main) { OPEN.add(nsig(node)); FOCUS = node; } }
  catch (e) { showError(e); }
  node.loading = false;
  try { tg && tg.HapticFeedback && tg.HapticFeedback.selectionChanged(); } catch (e) {}
  rerender(node.root);
}

function renderBreakdown() {
  $('sortBy').value = st.sort;
  document.querySelectorAll('#brMode button').forEach(b => b.setAttribute('aria-pressed', b.dataset.m === st.mode));
  $('pvCtl').hidden = st.mode !== 'pivot';
  if (st.mode === 'pivot') {
    $('rowsDim').innerHTML = dimOptions(st.rows, false);
    $('colsDim').innerHTML = dimOptions(st.cols, false);
    $('brHint').textContent = BD ? `${BD.total_rows} ta qator` : '';
    $('shareHost').innerHTML = '';
    if (BD) renderPivot($('breakdown'));
    return;
  }
  $('brHint').textContent = 'qatorni bosing — ichi ochiladi';
  renderShare(); renderTree();
}
function rowHtml(node, rootAmount, indBase = 1) {
  const r = node.row, hasCmp = !!cmpRange(), can = !!node.childDim;
  const shareTot = rootAmount ? r.current.amount / rootAmount * 100 : null;
  return `<button class="tr lv${Math.min(node.depth, 6)} ${can ? 'can' : ''} ${node.open ? 'op' : ''}" data-s="${esc(nsig(node))}" ${can ? '' : 'tabindex="-1"'}
      style="--ind:${(node.depth - indBase) * 14}px" title="Umumiy savdodan ${pct(shareTot)}">
    <span class="n"><span class="c">${node.loading ? '…' : can ? (node.open ? '▾' : '▸') : '·'}</span><span class="nl">${esc(node.label)}${node.sub && node.dim === 'sku' ? ` <span class="sub2">· ${esc(node.sub)}</span>` : ''}</span>
      <span class="lvtag">${esc(dimName(node.dim))}</span></span>
    <span class="v num">${money(r.current.amount)}</span>
    <span class="m num"><span class="bar"><span style="width:${Math.min(100, r.share || 0)}%"></span></span><span>${pct(r.share)}</span>${hasCmp ? pill(r.delta.amount) : ''}<span class="${r.current.margin_pct < 0 ? 'neg' : ''}">GM ${pct(r.current.margin_pct)}</span></span>
  </button>`;
}
function subtreeRows(node, out, rootAmt, indBase = 1) {
  if (!node.open || !node.children) return;
  const ind = (node.depth + 1 - indBase) * 14 + 18;
  if (!node.children.length) { out.push(`<div class="tr-empty" style="--ind:${ind}px">Savdo yo'q</div>`); return; }
  const shown = node.showAll ? node.children : node.children.slice(0, CHILD_LIMIT);
  for (const ch of shown) { out.push(rowHtml(ch, rootAmt, indBase)); subtreeRows(ch, out, rootAmt, indBase); }
  const rest = node.children.length - shown.length;
  if (rest > 0) out.push(`<button class="tr-more" data-more="${esc(nsig(node))}" style="--ind:${ind}px">Yana ${rest} ta ${esc(dimName(node.childDim)).toLowerCase()} ko'rsatish</button>`);
  if (node.total_rows > node.children.length) out.push(`<div class="tr-empty" style="--ind:${ind}px">Birinchi ${node.children.length} ta ko'rsatilgan (jami ${node.total_rows})</div>`);
}
function wireTree(host) {
  host.querySelectorAll('.tr.can[data-s]').forEach(b => b.onclick = e => { e.stopPropagation(); const n = findBySig(b.dataset.s); if (n) toggleNode(n); });
  host.querySelectorAll('.tr-more[data-more]').forEach(b => b.onclick = e => { e.stopPropagation(); const n = findBySig(b.dataset.more); if (n) { n.showAll = true; rerender(n.root); } });
}
function renderTree() {
  const host = $('breakdown');
  if (!TREE) { host.innerHTML = ''; return; }
  const T = TREE.total, rootAmt = T ? T.amount : 0;
  if (!TREE.childDim || !T) { host.innerHTML = `<div class="list"><div class="empty">Tanlangan filtrlar bo'yicha savdo yo'q.</div></div>`; return; }
  const out = [`<button class="tr lv0 can ${TREE.open ? 'op' : ''}" data-s="${esc(nsig(TREE))}">
      <span class="n"><span class="c">${TREE.open ? '▾' : '▸'}</span><b>Jami</b></span><span class="v num">${money(T.amount)}</span>
      <span class="m num">${nf0.format(T.qty)} dona · front marja ${pct(T.gross_pct)} · gross marja ${pct(T.margin_pct)}</span></button>`];
  subtreeRows(TREE, out, rootAmt, 1);
  host.innerHTML = `<div class="tw">${out.join('')}</div>`;
  wireTree(host);
}

// --- ulush kartasi: FOCUS tugunining ichki tarkibi
function renderShare() {
  const host = $('shareHost');
  const node = FOCUS && FOCUS.open && FOCUS.children ? FOCUS : TREE;
  if (!node || !node.children || !node.children.length) { host.innerHTML = ''; return; }
  const ctx = node.path.length ? node.path.map(p => labels[p.dim + '|' + p.key] || p.key).join(' › ') : 'Jami';
  host.innerHTML = `<div class="chartcard sharecard">
    <div class="sh"><span class="label">Savdo ulushi · ${esc(dimName(node.childDim))}</span>
      <div class="seg mini" id="shareView">
        <button data-v="struct" aria-pressed="${st.shareView === 'struct'}">Tuzilma</button>
        <button data-v="dyn" aria-pressed="${st.shareView === 'dyn'}">Kunlar bo'yicha</button>
      </div></div>
    <div class="hint">${esc(ctx)}</div>
    <div id="shareBody">${st.shareView === 'struct' ? shareStruct(node) : '<div class="empty">Yuklanmoqda…</div>'}</div>
  </div>`;
  const wireRows = () => host.querySelectorAll('.srow.drill').forEach(x => x.onclick = () => {
    const ch = node.children.find(c => String(c.key) === x.dataset.k); if (ch) toggleNode(ch); });
  wireRows();
  host.querySelectorAll('#shareView button').forEach(b => b.onclick = () => {
    st.shareView = b.dataset.v;
    host.querySelectorAll('#shareView button').forEach(x => x.setAttribute('aria-pressed', x === b));
    if (st.shareView === 'struct') { $('shareBody').innerHTML = shareStruct(node); wireRows(); } else shareDyn(node);
  });
  if (st.shareView === 'dyn') shareDyn(node);
}
function shareStruct(node) {
  const hasCmp = !!cmpRange();
  const byAmt = node.raw.slice().sort((a, b) => b.current.amount - a.current.amount);
  const top = byAmt.slice(0, SLOTS);
  const rows = top.map((i, n) => ({key: i.key, label: i.label, share: i.share || 0, pshare: i.prev_share, pp: i.share_pp, color: `var(--s${n + 1})`}));
  const restCount = node.total_rows - top.length;
  if (restCount > 0) {
    const sh = Math.max(0, 100 - top.reduce((s, i) => s + (i.share || 0), 0));
    const ps = hasCmp && top.every(i => i.prev_share != null) ? Math.max(0, 100 - top.reduce((s, i) => s + i.prev_share, 0)) : null;
    rows.push({key: null, label: `Boshqalar (${restCount})`, share: sh, pshare: ps, pp: ps != null ? sh - ps : null, color: 'var(--so)'});
  }
  const max = Math.max(...rows.map(r => Math.max(r.share || 0, hasCmp ? r.pshare || 0 : 0)), 1);
  const can = !!childDimFor([...node.path, {dim: node.childDim, key: ''}]);
  return `<div class="strip" role="img" aria-label="Ulushlar tuzilmasi">${rows.map(r => `<span style="width:${r.share}%;background:${r.color}" title="${esc(r.label)}: ${pct(r.share)}"></span>`).join('')}</div>
    ${hasCmp ? `<div class="legend" style="margin:2px 0 4px"><span><i style="background:var(--ink-2);width:3px;height:12px"></i>o'tgan davrdagi ulush</span></div>` : ''}
    <div class="srows">${rows.map(r => `<button class="srow ${r.key != null && can ? 'drill' : ''}" ${r.key != null ? `data-k="${esc(r.key)}"` : ''} ${r.key != null && can ? '' : 'disabled'}>
      <span class="sw" style="background:${r.color}"></span>
      <span class="sl" title="${esc(r.label)}">${esc(r.label)}</span>
      <span class="sv num">${pct(r.share)}</span>
      <span class="strk"><span class="sf" style="width:${r.share / max * 100}%;background:${r.color}"></span>
        ${hasCmp && r.pshare != null ? `<span class="sp" style="left:${r.pshare / max * 100}%"></span>` : ''}</span>
      <span class="spp">${hasCmp ? pill(r.pp, true) : ''}</span>
    </button>`).join('')}</div>`;
}
async function shareDyn(node) {
  const body = $('shareBody');
  const req = ++reqSeq.dyn;
  let data;
  try { data = await api('share-daily', {...pathFilters(node.path), rows: node.childDim, limit: 5}); }
  catch (e) { body.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  if (req !== reqSeq.dyn || !$('shareBody')) return;
  const days = data.days, n = days.length, names = data.series.map(s => s.label);
  const colors = data.series.map((s, j) => j === data.series.length - 1 ? 'var(--so)' : `var(--s${j + 1})`);
  const W = Math.max(280, body.clientWidth || 340), H = 200, L = 34, R = 6, Tt = 8, Bm = 22;
  const cw = (W - L - R) / n, bw = Math.max(2, cw - Math.min(6, cw * .25));
  const y = v => Tt + (H - Tt - Bm) * (1 - v / 100);
  const every = Math.ceil(n / 7);
  let g = '';
  for (const v of [0, 25, 50, 75, 100]) g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" ${v ? 'stroke-dasharray="2 3"' : ''}/><text x="${L - 5}" y="${y(v) + 4}" text-anchor="end" font-size="10" fill="var(--muted)">${v}%</text>`;
  days.forEach((d, di) => {
    const x0 = L + di * cw + (cw - bw) / 2;
    if (di % every === 0) g += `<text x="${x0 + bw / 2}" y="${H - 6}" text-anchor="middle" font-size="10" fill="var(--muted)">${dm(d.date)}</text>`;
    if (d.total) {
      let acc = 0;
      d.shares.forEach((p, j) => { if (!p) return; const y1 = y(acc + p), y2 = y(acc);
        g += `<rect x="${x0}" y="${y1}" width="${bw}" height="${Math.max(0, y2 - y1 - 1)}" fill="${colors[j]}" rx="${n <= 14 ? 2 : 0}"/>`; acc += p; });
    }
    g += `<rect x="${L + di * cw}" y="${Tt}" width="${cw}" height="${H - Tt - Bm}" fill="transparent" data-di="${di}" class="hitc"/>`;
  });
  body.innerHTML = `<div class="legend" style="margin-bottom:4px">${names.map((nm, j) => `<span><i style="background:${colors[j]};height:8px;width:8px;border-radius:2px"></i>${esc(nm)}</span>`).join('')}</div>
    <div class="chartwrap"><svg viewBox="0 0 ${W} ${H}" style="height:${H}px" role="img" aria-label="Kunlar bo'yicha ulush">${g}</svg><div class="tip" hidden></div></div>`;
  const svg = body.querySelector('svg'), tip = body.querySelector('.tip');
  svg.querySelectorAll('.hitc').forEach(el => {
    const show = () => { const d = days[+el.dataset.di]; const rect = svg.getBoundingClientRect();
      tip.hidden = false;
      tip.innerHTML = `<b>${dm(d.date)}</b> · ${money(d.total)}<br>` + d.shares.map((p, j) => `${esc(names[j])}: ${nf1.format(p)}%`).join('<br>');
      tip.style.left = Math.max(80, Math.min(rect.width - 80, (L + (+el.dataset.di) * cw + cw / 2) / W * rect.width)) + 'px'; };
    el.addEventListener('pointerenter', show); el.addEventListener('pointerdown', show);
    el.addEventListener('pointerleave', () => tip.hidden = true);
  });
}

// --- kesma (ikki o'lchov)
function sortedItems() { return sortRows(BD.rows.slice()); }
function renderPivot(host) {
  const items = sortedItems(), T = BD.total, cols = BD.cols || [], cells = BD.cells || {};
  const rowsShown = items.slice(0, st.showAll ? items.length : 20);
  const val = (i, c) => { const v = ((cells[i.key] || {})[c.key] || {}).amount || 0;
    if (st.pvMode === 'row') return i.current.amount ? v / i.current.amount * 100 : 0;
    if (st.pvMode === 'col') return c.amount ? v / c.amount * 100 : 0;
    return v; };
  let max = 0; for (const i of rowsShown) for (const c of cols) max = Math.max(max, val(i, c));
  const heat = v => { if (!v || !max) return ''; const t = v / max; const s = t > .8 ? '--h5' : t > .6 ? '--h4' : t > .4 ? '--h3' : t > .2 ? '--h2' : '--h1';
    return `background:var(${s});${t > .6 ? 'color:#fff' : ''}`; };
  const mln = v => v ? nf1.format(v / 1e6) : '·';
  const fmtCell = v => st.pvMode === 'amount' ? mln(v) : (v ? nf1.format(v) + '%' : '·');
  const lastCol = i => st.pvMode === 'amount' ? `<b>${mln(i.current.amount)}</b>` : st.pvMode === 'row' ? '<b>100%</b>' : `<b>${pct(i.share)}</b>`;
  host.innerHTML = `<div class="seg mini" id="pvMode" style="align-self:flex-start;display:inline-flex">
      <button data-v="amount" aria-pressed="${st.pvMode === 'amount'}">Savdo, mln</button>
      <button data-v="row" aria-pressed="${st.pvMode === 'row'}">Qator ichida ulush</button>
      <button data-v="col" aria-pressed="${st.pvMode === 'col'}">Ustun ichida ulush</button></div>
    <div class="pivotwrap"><table class="pv num">
    <thead><tr><th>${esc(dimName(st.rows))} \\ ${esc(dimName(st.cols))}</th>${cols.map(c => `<th>${esc(c.label)}</th>`).join('')}<th>Jami</th></tr></thead>
    <tbody>${rowsShown.map(i => `<tr><td title="${esc(i.label)}">${esc(i.label)}</td>${cols.map(c => { const v = val(i, c);
        const cell = (cells[i.key] || {})[c.key];
        return `<td class="c" style="${heat(v)}" title="${cell ? `gross marja ${pct(cell.margin_pct)}` : ''}">${fmtCell(v)}</td>`; }).join('')}<td>${lastCol(i)}</td></tr>`).join('')}
      <tr class="tot"><td>Jami</td>${cols.map(c => `<td>${st.pvMode === 'amount' ? mln(c.amount) : st.pvMode === 'row' ? (T.amount ? nf1.format(c.amount / T.amount * 100) + '%' : '·') : '100%'}</td>`).join('')}<td>${st.pvMode === 'amount' ? mln(T.amount) : '100%'}</td></tr>
    </tbody></table></div>
    <div class="foot">${st.pvMode === 'amount' ? "Kataklarda savdo, mln so'm." : st.pvMode === 'row' ? "Har bir qatorda: shu qator savdosining ustunlar bo'yicha taqsimoti." : "Har bir ustunda: shu ustun savdosining qatorlar bo'yicha taqsimoti."}
      ${items.length > 20 && !st.showAll ? ` <button class="more" id="more" style="display:inline;width:auto;border:0;padding:0">Barcha ${items.length} qatorni ko'rsatish</button>` : ''}</div>`;
  const mo = $('more'); if (mo) mo.onclick = () => { st.showAll = true; renderBreakdown(); };
  host.querySelectorAll('#pvMode button').forEach(b => b.onclick = () => { st.pvMode = b.dataset.v; renderBreakdown(); });
}

// ---------------------------------------------------------------- xususiyatlar bloklari
let ATTR = null;
const A_TOP = 8;
function attrNode(d, item) {
  const rid = `a:${d}:${item.key}`;
  return ROOTS.get(rid) || newRoot(rid, 'prod', [{dim: d, key: item.key}], {label: item.label, row: item});
}
function attrBlock(b) {
  const d = b.dim, sel = st.f[d] || new Set(), hasCmp = !!cmpRange();
  const items = b.items;
  items.forEach(i => remember(d, i.key, i.label));
  const rank = new Map(items.slice().sort((x, y) => y.current.amount - x.current.amount).map((i, n) => [i.key, n]));
  const color = i => rank.get(i.key) < SLOTS ? `var(--s${rank.get(i.key) + 1})` : 'var(--so)';
  const isOpen = st.aOpen.has(d);
  const expanded = !!st.aExpand[d];
  let shown = items, rest = [];
  if (!expanded && items.length > A_TOP + 1) {
    shown = items.filter(i => rank.get(i.key) < A_TOP || sel.has(i.key));
    rest = items.filter(i => !(rank.get(i.key) < A_TOP || sel.has(i.key)));
  }
  const max = Math.max(...items.map(i => Math.max(i.share || 0, i.prev_share || 0)), 1);
  const restShare = rest.reduce((s, i) => s + (i.share || 0), 0);
  const total = items.reduce((s, i) => s + i.current.amount, 0);
  const rows = isOpen ? shown.map(i => {
    const node = attrNode(d, i); node.row = i;
    const can = !!node.childDim;
    const sub = []; subtreeRows(node, sub, total, 1);
    return `<div class="arow ${sel.has(i.key) ? 'on' : ''} ${can ? 'can' : ''} ${node.open ? 'op' : ''}" role="button" tabindex="0" data-s="${esc(nsig(node))}">
      <span class="c">${node.loading ? '…' : can ? (node.open ? '▾' : '▸') : '·'}</span>
      <span class="al"><span class="sw" style="background:${color(i)}"></span>${esc(i.label)}</span>
      <span class="aa num">${money(i.current.amount)}</span>
      <span class="strk"><span class="sf" style="width:${(i.share || 0) / max * 100}%;background:${color(i)}"></span>
        ${hasCmp && i.prev_share != null ? `<span class="sp" style="left:${i.prev_share / max * 100}%"></span>` : ''}</span>
      <span class="as num"><b>${pct(i.share)}</b>${hasCmp ? pill(i.share_pp, true) : ''}</span>
      <span class="am num"><span>${nf0.format(i.current.qty)} dona${hasCmp ? ` · savdo ${i.delta.amount == null ? '—' : (i.delta.amount > 0 ? '+' : '') + nf1.format(i.delta.amount) + '%'}` : ''} · <span class="${i.current.margin_pct < 0 ? 'neg' : ''}">GM ${pct(i.current.margin_pct)}</span></span>
        <button class="fbtn ${sel.has(i.key) ? 'on' : ''}" data-d="${d}" data-k="${esc(i.key)}" title="Filtrga qo'shish / olib tashlash">${sel.has(i.key) ? '✓ filtrda' : '+ filtr'}</button></span>
    </div>${sub.length ? `<div class="tw sub">${sub.join('')}</div>` : ''}`;
  }).join('') : '';
  return `<div class="ablock ${isOpen ? 'open' : ''}">
    <button class="ah" data-d="${d}" aria-expanded="${isOpen}"><h3><span class="c">${isOpen ? '▾' : '▸'}</span>${esc(b.title)}${b.unit ? ` <span class="u">${esc(b.unit)}</span>` : ''}</h3>
      <span class="hint num">${items.length} ta${sel.size ? ` · tanlangan ${sel.size}` : ''}</span></button>
    ${items.length ? `<div class="strip">${items.map(i => `<span style="width:${i.share || 0}%;background:${color(i)}" title="${esc(i.label)}: ${pct(i.share)}"></span>`).join('')}</div>` : `<div class="empty">Savdo yo'q</div>`}
    ${isOpen ? `<div class="arows">${rows}
    ${rest.length ? `<button class="more amore" data-d="${d}">Yana ${rest.length} ta (${pct(restShare)}) — ko'rsatish</button>` : ''}
    ${expanded && items.length > A_TOP + 1 ? `<button class="more amore" data-d="${d}">Qisqartirish</button>` : ''}</div>` : ''}
    </div>`;
}
function renderAttrBlocks() {
  if (!ATTR) return;
  if (!st.aOpen) st.aOpen = new Set(ATTR.blocks.length ? [ATTR.blocks[0].dim] : []);
  const host = $('ablocks');
  host.innerHTML = ATTR.blocks.map(attrBlock).join('');
  host.querySelectorAll('.ah').forEach(b => b.onclick = () => { const d = b.dataset.d; st.aOpen.has(d) ? st.aOpen.delete(d) : st.aOpen.add(d); renderAttrBlocks(); });
  host.querySelectorAll('.arow.can').forEach(r => {
    const go = () => { const n = findBySig(r.dataset.s); if (n) toggleNode(n); };
    r.onclick = go; r.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
  });
  host.querySelectorAll('.fbtn').forEach(b => b.onclick = e => {
    e.stopPropagation();
    const d = b.dataset.d, k = b.dataset.k;
    const set = new Set(st.f[d] || []); set.has(k) ? set.delete(k) : set.add(k);
    if (set.size) st.f[d] = set; else delete st.f[d];
    resetView(); load();
  });
  host.querySelectorAll('.amore').forEach(b => b.onclick = () => { st.aExpand[b.dataset.d] = !st.aExpand[b.dataset.d]; renderAttrBlocks(); });
  wireTree(host);
}

// ---------------------------------------------------------------- TOP-10 SKU (bosilsa: karta + joylar bo'yicha daraxt)
let TOP = null;
function renderTop(t) { TOP = t; renderTopBody(); }
function sparkline(series) {
  const vals = series.map(p => p.amount), n = vals.length;
  if (n < 2) return '';
  const W = 300, H = 48, max = Math.max(...vals, 1);
  const x = i => 2 + (W - 4) * i / (n - 1), y = v => H - 4 - (H - 10) * v / max;
  const d = vals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  return `<svg viewBox="0 0 ${W} ${H}" class="spark" preserveAspectRatio="none" role="img" aria-label="Kunlik savdo">
    <path d="${d}L${x(n - 1)},${H}L${x(0)},${H}Z" fill="var(--accent)" fill-opacity=".1"/>
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
    <div class="spark-ax hint num"><span>${dm(series[0].date)}</span><span>kunlik savdo</span><span>${dm(series[n - 1].date)}</span></div>`;
}
function skuCard(node) {
  const c = node.card;
  if (!c) return `<div class="skucard"><div class="empty">Yuklanmoqda…</div></div>`;
  if (c.error) return `<div class="skucard"><div class="errbox">${esc(c.error)}</div></div>`;
  const C = c.sum.current, d = c.sum.delta || {}, hasCmp = !!c.sum.previous;
  const kv = (l, v, p) => `<div class="kv"><span class="l">${l}</span><span class="v num">${v}</span>${p || ''}</div>`;
  return `<div class="skucard">
    ${c.prod ? `<div class="tags">${[`<span class="tag">${esc(c.prod.brand)}</span>`, c.prod.status ? `<span class="tag st">${esc(c.prod.status)}</span>` : '',
      ...(c.prod.attrs || []).map(a => `<span class="tag"><b>${esc(a.name)}:</b> ${esc(a.value)}</span>`)].join('')}</div>` : ''}
    <div class="kvs">
      ${kv('Savdo', money(C.amount), hasCmp ? pill(d.amount) : '')}
      ${kv('Dona', nf0.format(C.qty) + (C.bonus_qty ? ` <span class="hint">(bonus ${nf0.format(C.bonus_qty)})</span>` : ''), hasCmp ? pill(d.qty) : '')}
      ${kv("O'rtacha narx", C.avg_price ? money(C.avg_price) : '—', hasCmp ? pill(d.avg_price) : '')}
      ${kv('Front marja', pct(C.gross_pct), hasCmp ? pill(d.gross_pct_pp, true) : '')}
      ${kv('Gross marja', pct(C.margin_pct), hasCmp ? pill(d.margin_pct_pp, true) : '')}
      ${kv('Filiallar', nf0.format(C.branches))}
    </div>
    ${sparkline(c.daily.series)}
    <div class="lbl" style="margin-top:4px">📍 Qayerda sotilgan</div>
    ${c.childErr ? `<div class="errbox">${esc(c.childErr)}</div>` : ''}
  </div>`;
}
async function openSku(node) {
  node.open = true; node.loading = true; renderTopBody();
  const F = pathFilters(node.path);
  try {
    // har bir qism alohida: bittasi xato bersa ham qolgani ko'rinadi
    const [prod, sum, daily, ch] = await Promise.allSettled([api('product/' + node.key), api('summary', F), api('daily', F),
      node.childDim && !node.children ? fetchChildren(node) : Promise.resolve()]);
    if (sum.status !== 'fulfilled') throw sum.reason;
    node.card = {prod: prod.status === 'fulfilled' ? prod.value : null, sum: sum.value,
                 daily: daily.status === 'fulfilled' ? daily.value : {series: []},
                 childErr: ch.status === 'rejected' ? ch.reason.message : null};
  } catch (e) { node.card = {error: (e && e.message) || String(e)}; }
  node.loading = false; renderTopBody();
}
function renderTopBody() {
  const t = TOP; if (!t) return;
  const host = $('top'), head = $('topHead');
  const sumTop = t.rows.reduce((s, i) => s + i.current.amount, 0), tot = t.total ? t.total.amount : 0;
  $('topHint').textContent = t.rows.length ? `${t.rows.length} ta · ${money(sumTop)}${tot ? ' · ' + pct(sumTop / tot * 100) : ''}` : "savdo yo'q";
  head.querySelector('.c').textContent = st.topOpen ? '▾' : '▸';
  head.setAttribute('aria-expanded', st.topOpen);
  $('topBlock').classList.toggle('open', st.topOpen);
  head.onclick = () => { st.topOpen = !st.topOpen; renderTopBody(); };
  host.hidden = !st.topOpen;
  if (!st.topOpen) { host.innerHTML = ''; return; }
  host.innerHTML = t.rows.length ? t.rows.map((i, n) => {
    const rid = `s:${i.key}`;
    const node = ROOTS.get(rid) || newRoot(rid, 'loc', [{dim: 'sku', key: i.key}], {label: i.label, row: i});
    const sub = []; if (node.open && node.card && !node.card.error) subtreeRows(node, sub, i.current.amount, 1);
    return `<button type="button" class="sku can ${node.open ? 'op' : ''}" aria-expanded="${!!node.open}" data-rid="${esc(rid)}">
      <span class="rk num">${n + 1}</span><span class="nm"><span class="c">${node.loading ? '…' : node.open ? '▾' : '▸'}</span>${esc(i.label)}</span><span class="amt num">${money(i.current.amount)}</span>
      <span class="br">${esc(i.sub || '')} · ${nf0.format(i.current.qty)} dona</span><span class="mt num ${i.current.margin_pct < 0 ? 'neg' : ''}">GM ${pct(i.current.margin_pct)}</span>
    </button>${node.open ? skuCard(node) + (sub.length ? `<div class="tw sub">${sub.join('')}</div>` : '') : ''}`;
  }).join('') : `<div class="empty">Savdo yo'q.</div>`;
  // bosishni konteyner ushlaydi (delegatsiya) — qayta chizilganda ham ishonchli ishlaydi
  host.onclick = e => {
    const r = e.target.closest('.sku.can'); if (!r || !host.contains(r)) return;
    const node = ROOTS.get(r.dataset.rid); if (!node) return;
    if (node.open) { node.open = false; renderTopBody(); } else openSku(node);
  };
  wireTree(host);
}

// ---------------------------------------------------------------- yuklash
const reqSeq = {main: 0, dyn: 0};
function showError(e) {
  if (e.status === 401 || e.status === 403) {
    $('app').hidden = true;
    const id = tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : null;
    $('gate').hidden = false;
    $('gate').innerHTML = `<div class="gate"><h2>${e.status === 403 ? 'Ruxsat yo\'q' : 'Kirish tasdiqlanmadi'}</h2>
      <p>${esc(e.message)}</p>${id ? `<p>Botga qaytib <b>/start</b> bosing va «🔑 Kirish so'rash» tugmasini bosing — admin tasdiqlagach shu yerda ma'lumot ochiladi.</p><p class="hint">Telegram ID: <code>${id}</code></p>` : '<p>Ilovani Telegram bot orqali oching.</p>'}</div>`;
    return;
  }
  const box = document.createElement('div');
  box.className = 'errbox'; box.textContent = 'Xato: ' + e.message;
  $('kpis').replaceChildren(box);
}
async function load() {
  renderHeader();
  const F = filters(), seq = ++reqSeq.main;
  const secs = ['kpis', 'chart', 'breakdown', 'ablocks', 'top'].map($);
  secs.forEach(s => s.classList.add('loading'));
  try {
    const tasks = [api('summary', F), api('daily', F), api('attributes', F), api('breakdown', {...F, rows: 'sku', sort: 'amount', limit: 10})];
    if (st.mode === 'pivot') {
      if (!st.cols || st.cols === st.rows) st.cols = attrDims().find(d => d !== st.rows) || (st.rows === 'brand' ? 'region' : 'brand');
      tasks.push(api('breakdown', {...F, rows: st.rows, cols: st.cols, sort: apiSort(), desc: st.sort !== 'name', limit: 500}));
    } else tasks.push(buildTree());
    const [sum, daily, attrs, top, bd] = await Promise.all(tasks);
    if (seq !== reqSeq.main) return;          // eskirgan javob
    DAILY = daily; ATTR = attrs; if (st.mode === 'pivot') BD = bd;
    renderKpis(sum); renderChart(); renderBreakdown(); renderAttrBlocks(); renderTop(top);
    renderHeader();
  } catch (e) {
    if (seq === reqSeq.main) showError(e);
  } finally {
    if (seq === reqSeq.main) secs.forEach(s => s.classList.remove('loading'));
  }
}

// ---------------------------------------------------------------- boshqaruv
$('catBtn').onclick = openCatSheet;
$('perBtn').onclick = openPeriodSheet;
$('filBtn').onclick = openFilterSheet;
$('rowsDim').onchange = e => { st.rows = e.target.value; if (st.cols === st.rows) st.cols = ''; st.showAll = false; load(); };
$('colsDim').onchange = e => { st.cols = e.target.value === st.rows ? '' : e.target.value; st.showAll = false; load(); };
$('sortBy').onchange = e => { st.sort = e.target.value; resetView(); load(); };
document.querySelectorAll('#brMode button').forEach(b => b.onclick = () => { if (st.mode === b.dataset.m) return; st.mode = b.dataset.m; resetView(); load(); });
function syncBack() {
  if (!(tg && tg.BackButton)) return;
  (sheetOpen() || VIEW === 'upload') ? tg.BackButton.show() : tg.BackButton.hide();
}
if (tg && tg.BackButton) tg.BackButton.onClick(() => { if (sheetOpen()) closeSheet(); else if (VIEW === 'upload') setView('analytics'); });
document.querySelectorAll('#chartMetric button').forEach(b => b.onclick = () => {
  st.chartMetric = b.dataset.m;
  document.querySelectorAll('#chartMetric button').forEach(x => x.setAttribute('aria-pressed', x === b));
  renderChart();
});
let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { renderChart(); if (st.shareView === 'dyn' && st.mode === 'tree') renderShare(); }, 150); });


// ---------------------------------------------------------------- bo'limlar: Tahlil / Yuklash
let VIEW = 'analytics';
let NEED_REFRESH = false;   // yangi fayl yuklangach tahlil qayta o'qiladi
function setView(v) {
  VIEW = v;
  $('upBtn').textContent = v === 'upload' ? '📊' : '📥';
  $('upBtn').title = v === 'upload' ? 'Tahlilga qaytish' : 'Fayl yuklash';
  $('topAnalytics').hidden = v !== 'analytics';
  $('viewAnalytics').hidden = v !== 'analytics';
  $('viewUpload').hidden = v !== 'upload';
  if (v === 'upload') loadHistory();
  if (v === 'analytics' && NEED_REFRESH) { NEED_REFRESH = false; refreshMeta(); }
  syncBack();
  window.scrollTo(0, 0);
}
$('upBtn').onclick = () => setView(VIEW === 'upload' ? 'analytics' : 'upload');

// ---------------------------------------------------------------- yuklash
let UP = null;          // joriy job
let upTimer = null;
const STEP_NAMES = [['upload', 'Yuklash'], ['checking', 'Tekshiruv'], ['checked', 'Tasdiqlash'], ['loading', 'Bazaga yozish'], ['done', 'Tayyor']];
function stepsHtml(cur, failed) {
  const order = STEP_NAMES.map(s => s[0]);
  const ci = order.indexOf(cur);
  return `<div class="steps">${STEP_NAMES.map(([k, t], i) =>
    `<span class="step ${failed && i === ci ? 'err' : i < ci || cur === 'done' ? 'ok' : i === ci ? 'on' : ''}">${i + 1}. ${t}</span>`).join('')}</div>`;
}
const reportHtml = t => `<div class="report">${String(t || '').replace(/\n/g, '<br>')}</div>`;
function renderUp(state, extra) {
  const host = $('upState');
  if (!state) { host.innerHTML = ''; return; }
  const j = UP || {};
  const mbv = j.size_mb ?? extra?.size; const mbs = mbv == null ? '' : mbv < 0.1 ? '<0,1' : nf1.format(mbv);
  const head = `<div class="uphead"><b>${esc(j.file_name || extra?.name || '')}</b><span class="hint num">${mbs} MB${j.kind_name ? ' · ' + esc(j.kind_name) : ''}</span></div>`;
  let body = '';
  if (state === 'upload') body = `<div class="pbar"><span style="width:${extra.pct}%"></span></div><div class="hint num">Serverga yuborilmoqda… ${extra.pct}%</div>`;
  else if (state === 'checking') body = `<div class="pbar indet"><span></span></div><div class="hint">Fayl tekshirilmoqda — katta fayllarda 20–60 soniya…</div>`;
  else if (state === 'checked') body = reportHtml(j.report) +
    `<div class="upbtns"><button class="btn" id="upCancel">Bekor qilish</button><button class="btn primary" id="upConfirm">Tasdiqlash va yuklash</button></div>`;
  else if (state === 'loading') body = `<div class="pbar indet"><span></span></div><div class="hint">Bazaga yozilmoqda…</div>`;
  else if (state === 'done') body = reportHtml(j.report) + `<div class="hint num">${j.elapsed ?? ''} s · hisobot botga ham yuborildi</div>
    <div class="upbtns"><button class="btn" id="upAgain">Yana fayl yuklash</button><button class="btn primary" id="upGo">Tahlilga o'tish</button></div>`;
  else if (state === 'failed') body = `<div class="errbox">${esc(j.error || extra?.error || 'Xato')}</div>
    <div class="upbtns"><button class="btn primary" id="upAgain">Boshqa fayl tanlash</button></div>`;
  host.innerHTML = `<div class="upcard">${head}${stepsHtml(state === 'failed' ? (j.status_before || 'checking') : state, state === 'failed')}${body}</div>`;
  const c = $('upConfirm'); if (c) c.onclick = confirmUpload;
  const x = $('upCancel'); if (x) x.onclick = cancelUpload;
  const a = $('upAgain'); if (a) a.onclick = () => { UP = null; renderUp(null); $('drop').hidden = false; };
  const g = $('upGo'); if (g) g.onclick = () => setView('analytics');
  $('drop').hidden = !['failed', 'done'].includes(state) && !!state;
  if (state === 'done' || state === 'failed') $('drop').hidden = true;
}
function uploadFile(file) {
  if (!file) return;
  if (!/\.(xlsx|xlsm)$/i.test(file.name)) { UP = null; renderUp('failed', {error: 'Faqat .xlsx fayl qabul qilinadi', name: file.name}); return; }
  const maxMb = META.max_upload_mb || 100;
  const sizeMb = +(file.size / 1048576).toFixed(1);
  if (sizeMb > maxMb) { UP = null; renderUp('failed', {error: `Fayl ${maxMb} MB dan katta (${sizeMb} MB)`, name: file.name, size: sizeMb}); return; }
  UP = null;
  renderUp('upload', {pct: 0, name: file.name, size: sizeMb});
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  if (tg && tg.initData) xhr.setRequestHeader('Authorization', 'tma ' + tg.initData);
  xhr.upload.onprogress = e => { if (e.lengthComputable) renderUp('upload', {pct: Math.round(e.loaded / e.total * 100), name: file.name, size: sizeMb}); };
  xhr.onload = () => {
    let res = {}; try { res = JSON.parse(xhr.responseText); } catch (e) {}
    if (xhr.status >= 200 && xhr.status < 300) { UP = res; renderUp('checking'); poll(); }
    else { UP = {file_name: file.name, size_mb: sizeMb, status_before: 'upload'}; renderUp('failed', {error: typeof res.detail === 'string' ? res.detail : `Xato ${xhr.status}`}); }
  };
  xhr.onerror = () => { UP = {file_name: file.name, size_mb: sizeMb, status_before: 'upload'}; renderUp('failed', {error: 'Tarmoq xatosi — internetni tekshirib, qayta urinib ko\'ring'}); };
  xhr.send((() => { const fd = new FormData(); fd.append('file', file); return fd; })());
}
function poll() {
  clearTimeout(upTimer);
  upTimer = setTimeout(async () => {
    if (!UP) return;
    try {
      const prev = UP.status;
      UP = await api('upload/' + UP.id);
      if (UP.status === 'failed') UP.status_before = prev;
      renderUp(UP.status);
      if (UP.status === 'checking' || UP.status === 'loading') poll();
      if (UP.status === 'done') { NEED_REFRESH = true; loadHistory(); try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred('success'); } catch (e) {} }
    } catch (e) { UP.status_before = UP.status; UP.error = e.message; renderUp('failed'); }
  }, 1500);
}
async function confirmUpload() {
  const b = $('upConfirm'); if (b) b.disabled = true;
  try { UP = await api('upload/' + UP.id + '/confirm', {}); renderUp('loading'); poll(); }
  catch (e) { UP.status_before = 'checked'; UP.error = e.message; renderUp('failed'); }
}
async function cancelUpload() {
  try { await fetch('/api/upload/' + UP.id, {method: 'DELETE', headers: tg && tg.initData ? {'Authorization': 'tma ' + tg.initData} : {}}); } catch (e) {}
  UP = null; renderUp(null); $('drop').hidden = false;
}
async function loadHistory() {
  const host = $('upHist');
  let data;
  try { data = await api('uploads?limit=30'); } catch (e) { host.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  const fmt = s => { if (!s) return ''; const d = new Date(s); return d.toLocaleString('ru-RU', {day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit'}); };
  const stName = {done: 'yuklandi', failed: 'xato', started: 'jarayonda'};
  host.innerHTML = data.uploads.length ? data.uploads.map(u => `<button class="hrow" data-id="${u.id}">
      <span class="hk">${esc(u.kind_name)}${u.period ? ` · <span class="num">${esc(u.period)}</span>` : ''}</span>
      <span class="st ${u.status}">${stName[u.status] || u.status}</span>
      <span class="hm num">#${u.id} · ${fmt(u.started_at)} · ${esc(u.file_name || '')}${u.rows_loaded != null ? ` · ${nf0.format(u.rows_loaded)} qator` : ''}</span>
      ${u.text || u.error ? `<span class="report" hidden>${u.text ? String(u.text).replace(/\n/g, '<br>') : esc(u.error)}</span>` : ''}
    </button>`).join('') : `<div class="empty">Hali yuklash bo'lmagan.</div>`;
  host.querySelectorAll('.hrow').forEach(b => b.onclick = () => { const r = b.querySelector('.report'); if (r) r.hidden = !r.hidden; });
}
async function refreshMeta() {
  try { META = await api('meta'); $('dataInfo').textContent = `ma'lumot: ${dm(META.data_from)}–${dm(META.data_to)}`; } catch (e) {}
  if (!st.cat && META.categories.length) st.cat = META.categories[0].id;
  resetView();
  if (META.data_to) { if (st.preset) [st.from, st.to] = presetRange(st.preset); load(); }
}
(() => {
  const drop = $('drop'), inp = $('fileIn');
  inp.onchange = () => { uploadFile(inp.files[0]); inp.value = ''; };
  ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', e => { const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) uploadFile(f); });
  $('upRefresh').onclick = loadHistory;
})();

async function init() {
  try { META = await api('meta'); }
  catch (e) { showError(e); return; }
  if (META.can_upload) { $('upBtn').hidden = false; $('upLimit').textContent = `.xlsx, ${META.max_upload_mb} MB gacha`; }
  if (!META.categories.length || !META.data_to) {
    $('kpis').innerHTML = `<div class="errbox">${!META.categories.length ? "Hali kategoriya yo'q" : "Bazada hali savdo yo'q"} — ${META.can_upload ? "«📥 Yuklash» bo'limidan fayllarni yuklang: tovar spravochnigi, filial spravochnigi, keyin savdo." : "administrator ma'lumot yuklashini kuting."}</div>`;
    if (META.can_upload) setView('upload');
    return;
  }
  $('dataInfo').textContent = `ma'lumot: ${dm(META.data_from)}–${dm(META.data_to)}`;
  st.cat = META.categories[0].id;
  [st.from, st.to] = presetRange(st.preset);
  load();
}
init();
})();
