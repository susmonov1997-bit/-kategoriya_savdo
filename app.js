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
  rows: 'region', cols: '', sort: 'amount',
  crumbs: [],            // [{dim, key, label, prevRows, prevSet}]
  chartMetric: 'amount', shareView: 'struct', pvMode: 'amount',
  aExpand: {}, showAll: false,
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

// ---------------------------------------------------------------- yuqori panel
function renderCats() {
  $('cats').innerHTML = META.categories.map(c => `<button data-id="${c.id}" aria-pressed="${c.id === st.cat}">${esc(c.name)}</button>`).join('');
  $('cats').querySelectorAll('button').forEach(b => b.onclick = () => {
    st.cat = +b.dataset.id; st.f = {}; st.crumbs = []; st.rows = 'region'; st.cols = ''; st.showAll = false; st.aExpand = {};
    load();
  });
}
function presetRange(k) {
  const to = META.data_to;
  if (k === 'month') return [to.slice(0, 8) + '01', to];
  return [addDays(to, -(+k - 1)), to];
}
const PRESETS = [['1', 'Kecha'], ['7', '7 kun'], ['14', '14 kun'], ['30', '30 kun'], ['month', 'Oy boshidan']];
function renderPeriods() {
  $('periods').innerHTML = PRESETS.map(([k, t]) => `<button class="chip" data-k="${k}" aria-pressed="${st.preset === k}">${t}</button>`).join('');
  $('periods').querySelectorAll('button').forEach(b => b.onclick = () => {
    st.preset = b.dataset.k; [st.from, st.to] = presetRange(st.preset); load();
  });
  const f = $('dFrom'), t = $('dTo');
  f.min = t.min = META.data_from; f.max = t.max = META.data_to;
  f.value = st.from; t.value = st.to;
  $('cmp').value = st.compare;
}
function renderFilterChips() {
  const dims = ['region', 'cluster', 'branch', ...attrDims(), 'brand', 'status'];
  const any = Object.values(st.f).some(s => s.size);
  $('filters').innerHTML = dims.map(d => {
    const n = st.f[d] ? st.f[d].size : 0;
    return `<button class="chip ${n ? 'active' : ''}" data-d="${d}">${esc(dimName(d))}${n ? ` <span class="cnt">${n}</span>` : ''}</button>`;
  }).join('') + (any ? `<button class="chip" id="clearAll">Tozalash ✕</button>` : '');
  $('filters').querySelectorAll('button[data-d]').forEach(b => b.onclick = () => openSheet(b.dataset.d));
  const ca = $('clearAll'); if (ca) ca.onclick = () => { st.f = {}; st.crumbs = []; load(); };
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
      <div><div class="label">Marja</div><div class="v num${neg(C.margin)}">${money(C.margin)}</div>
        <div class="sub num"><span>${pct(C.margin_pct)}</span>${Pv ? pill(d.margin_pct_pp, true) : ''}</div></div>
    </div>
    <div class="kpi"><div class="label">Valovka</div><div class="v num${neg(C.gross)}">${money(C.gross)}</div>
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

// ---------------------------------------------------------------- qirqim
let BD = null;
const SLOTS = 8;
function dimOptions(sel, includeNone) {
  const dims = [...LOC_PATH, ...attrDims(), 'brand', 'status', 'sku'];
  return (includeNone ? `<option value="">—</option>` : '') + dims.map(d => `<option value="${d}" ${d === sel ? 'selected' : ''}>${esc(dimName(d))}</option>`).join('');
}
function renderCrumbs() {
  const cr = $('crumbs');
  cr.innerHTML = st.crumbs.length ? [`<button data-i="-1">Jami</button>`, ...st.crumbs.map((c, i) =>
    i === st.crumbs.length - 1 ? `<span class="sep">›</span><span class="cur">${esc(c.label)}</span>`
                               : `<span class="sep">›</span><button data-i="${i}">${esc(c.label)}</button>`)].join('') : '';
  cr.querySelectorAll('button').forEach(b => b.onclick = () => popCrumbs(+b.dataset.i));
  if (tg && tg.BackButton) { st.crumbs.length ? tg.BackButton.show() : tg.BackButton.hide(); }
}
function sortedItems() {
  const items = BD.rows.slice();
  if (st.sort === 'delta') items.sort((a, b) => (b.delta.amount ?? -1e9) - (a.delta.amount ?? -1e9));
  return items;
}
function renderBreakdown() {
  $('rowsDim').innerHTML = dimOptions(st.rows, false);
  $('colsDim').innerHTML = dimOptions(st.cols, true);
  $('sortBy').value = st.sort;
  renderCrumbs();
  const host = $('breakdown');
  if (!BD) return;
  BD.rows.forEach(i => remember(st.rows, i.key, i.label));
  $('brHint').textContent = `${BD.total_rows} ta · ${dimName(st.rows).toLowerCase()}`;
  if (st.cols) { renderPivot(host); return; }
  const items = sortedItems(), T = BD.total, hasCmp = !!cmpRange();
  if (!items.length) { host.innerHTML = `<div class="list"><div class="empty">Tanlangan filtrlar bo'yicha savdo yo'q.</div></div>`; return; }
  const canDrill = !!nextDim(st.rows);
  const maxShare = Math.max(...items.map(i => i.share || 0), 1);
  const LIMIT = 12, shown = st.showAll ? items : items.slice(0, LIMIT);
  host.innerHTML = shareCard() + `<div class="list">
    <div class="row total"><div class="nm">Jami</div><div class="amt num">${money(T.amount)}</div>
      <div class="meta num" style="grid-column:1/-1;justify-content:flex-start">dona ${nf0.format(T.qty)} · valovka ${pct(T.gross_pct)} · marja ${pct(T.margin_pct)}</div></div>
    ${shown.map(i => `<button class="row ${canDrill ? 'drill' : ''}" data-k="${esc(i.key)}" ${canDrill ? '' : 'disabled style="cursor:default"'}>
      <div class="nm"><span>${esc(i.label)}${i.sub ? ` <span class="sub2">· ${esc(i.sub)}</span>` : ''}</span>${canDrill ? '<span class="chev">›</span>' : ''}</div>
      <div class="amt num">${money(i.current.amount)}</div>
      <div class="bar" title="Ulush ${pct(i.share)}"><span style="width:${(i.share || 0) / maxShare * 100}%"></span></div>
      <div class="meta num"><span>${pct(i.share)}</span>${hasCmp ? pill(i.delta.amount) : ''}<span class="${i.current.margin_pct < 0 ? 'neg' : ''}">M ${pct(i.current.margin_pct)}</span></div>
    </button>`).join('')}
    ${items.length > LIMIT ? `<button class="more" id="more">${st.showAll ? 'Qisqartirish' : `Yana ${items.length - LIMIT} ta ko'rsatish`}</button>` : ''}
    ${BD.total_rows > items.length ? `<div class="empty">Ro'yxatda birinchi ${items.length} ta (jami ${BD.total_rows})</div>` : ''}
  </div>`;
  host.querySelectorAll('.row.drill, .srow.drill').forEach(b => b.onclick = () => drill(st.rows, b.dataset.k));
  const mo = $('more'); if (mo) mo.onclick = () => { st.showAll = !st.showAll; renderBreakdown(); };
  wireShareCard();
}

// --- ulush kartasi
function shareCard() {
  return `<div class="chartcard sharecard">
    <div class="sh"><span class="label">Savdo ulushi · ${esc(dimName(st.rows))}</span>
      <div class="seg mini" id="shareView">
        <button data-v="struct" aria-pressed="${st.shareView === 'struct'}">Tuzilma</button>
        <button data-v="dyn" aria-pressed="${st.shareView === 'dyn'}">Kunlar bo'yicha</button>
      </div></div>
    <div id="shareBody">${st.shareView === 'struct' ? shareStruct() : '<div class="empty">Yuklanmoqda…</div>'}</div>
  </div>`;
}
function shareStruct() {
  const hasCmp = !!cmpRange();
  const byAmt = BD.rows.slice().sort((a, b) => b.current.amount - a.current.amount);
  const top = byAmt.slice(0, SLOTS), rest = byAmt.slice(SLOTS);
  const rows = top.map((i, n) => ({key: i.key, label: i.label, share: i.share || 0, pshare: i.prev_share, pp: i.share_pp, color: `var(--s${n + 1})`}));
  const restCount = BD.total_rows - top.length;
  if (restCount > 0) {
    const sh = Math.max(0, 100 - top.reduce((s, i) => s + (i.share || 0), 0));
    const ps = hasCmp && top.every(i => i.prev_share != null) ? Math.max(0, 100 - top.reduce((s, i) => s + i.prev_share, 0)) : null;
    rows.push({key: null, label: `Boshqalar (${restCount})`, share: sh, pshare: ps, pp: ps != null ? sh - ps : null, color: 'var(--so)'});
  }
  const max = Math.max(...rows.map(r => Math.max(r.share || 0, hasCmp ? r.pshare || 0 : 0)), 1);
  const canDrill = !!nextDim(st.rows);
  return `<div class="strip" role="img" aria-label="Ulushlar tuzilmasi">${rows.map(r => `<span style="width:${r.share}%;background:${r.color}" title="${esc(r.label)}: ${pct(r.share)}"></span>`).join('')}</div>
    ${hasCmp ? `<div class="legend" style="margin:2px 0 4px"><span><i style="background:var(--ink-2);width:3px;height:12px"></i>o'tgan davrdagi ulush</span></div>` : ''}
    <div class="srows">${rows.map(r => `<button class="srow ${r.key != null && canDrill ? 'drill' : ''}" ${r.key != null ? `data-k="${esc(r.key)}"` : ''} ${r.key != null && canDrill ? '' : 'disabled'}>
      <span class="sw" style="background:${r.color}"></span>
      <span class="sl" title="${esc(r.label)}">${esc(r.label)}</span>
      <span class="sv num">${pct(r.share)}</span>
      <span class="strk"><span class="sf" style="width:${r.share / max * 100}%;background:${r.color}"></span>
        ${hasCmp && r.pshare != null ? `<span class="sp" style="left:${r.pshare / max * 100}%"></span>` : ''}</span>
      <span class="spp">${hasCmp ? pill(r.pp, true) : ''}</span>
    </button>`).join('')}</div>`;
}
async function shareDyn() {
  const body = $('shareBody');
  const req = ++reqSeq.dyn;
  let data;
  try { data = await api('share-daily', {...filters(), rows: st.rows, limit: 5}); }
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
function wireShareCard() {
  document.querySelectorAll('#shareView button').forEach(b => b.onclick = () => {
    st.shareView = b.dataset.v;
    document.querySelectorAll('#shareView button').forEach(x => x.setAttribute('aria-pressed', x === b));
    if (st.shareView === 'struct') {
      $('shareBody').innerHTML = shareStruct();
      $('shareBody').querySelectorAll('.srow.drill').forEach(x => x.onclick = () => drill(st.rows, x.dataset.k));
    } else shareDyn();
  });
  if (st.shareView === 'dyn') shareDyn();
}

// --- kesma
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
        return `<td class="c" style="${heat(v)}" title="${cell ? `marja ${pct(cell.margin_pct)}` : ''}">${fmtCell(v)}</td>`; }).join('')}<td>${lastCol(i)}</td></tr>`).join('')}
      <tr class="tot"><td>Jami</td>${cols.map(c => `<td>${st.pvMode === 'amount' ? mln(c.amount) : st.pvMode === 'row' ? (T.amount ? nf1.format(c.amount / T.amount * 100) + '%' : '·') : '100%'}</td>`).join('')}<td>${st.pvMode === 'amount' ? mln(T.amount) : '100%'}</td></tr>
    </tbody></table></div>
    <div class="foot">${st.pvMode === 'amount' ? "Kataklarda savdo, mln so'm." : st.pvMode === 'row' ? "Har bir qatorda: shu qator savdosining ustunlar bo'yicha taqsimoti." : "Har bir ustunda: shu ustun savdosining qatorlar bo'yicha taqsimoti."}
      ${items.length > 20 && !st.showAll ? ` <button class="more" id="more" style="display:inline;width:auto;border:0;padding:0">Barcha ${items.length} qatorni ko'rsatish</button>` : ''}</div>`;
  const mo = $('more'); if (mo) mo.onclick = () => { st.showAll = true; renderBreakdown(); };
  host.querySelectorAll('#pvMode button').forEach(b => b.onclick = () => { st.pvMode = b.dataset.v; renderBreakdown(); });
}

// --- drill-down
function drill(d, k) {
  const nd = nextDim(d); if (!nd) return;
  const label = labels[d + '|' + k] || k;
  st.crumbs.push({dim: d, key: k, label, prevRows: st.rows, prevSet: st.f[d] ? new Set(st.f[d]) : null});
  st.f[d] = new Set([k]); st.rows = nd; st.showAll = false;
  try { tg && tg.HapticFeedback && tg.HapticFeedback.selectionChanged(); } catch (e) {}
  load();
}
function popCrumbs(i) {
  while (st.crumbs.length > i + 1) {
    const c = st.crumbs.pop();
    if (c.prevSet) st.f[c.dim] = c.prevSet; else delete st.f[c.dim];
    st.rows = c.prevRows;
  }
  st.showAll = false; load();
}
if (tg && tg.BackButton) tg.BackButton.onClick(() => { if (st.crumbs.length) popCrumbs(st.crumbs.length - 2); });

// ---------------------------------------------------------------- xususiyatlar bloklari
let ATTR = null;
const A_TOP = 8;
function attrBlock(b) {
  const d = b.dim, sel = st.f[d] || new Set(), hasCmp = !!cmpRange();
  const items = b.items;
  items.forEach(i => remember(d, i.key, i.label));
  const rank = new Map(items.slice().sort((x, y) => y.current.amount - x.current.amount).map((i, n) => [i.key, n]));
  const color = i => rank.get(i.key) < SLOTS ? `var(--s${rank.get(i.key) + 1})` : 'var(--so)';
  const expanded = !!st.aExpand[d];
  let shown = items, rest = [];
  if (!expanded && items.length > A_TOP + 1) {
    shown = items.filter(i => rank.get(i.key) < A_TOP || sel.has(i.key));
    rest = items.filter(i => !(rank.get(i.key) < A_TOP || sel.has(i.key)));
  }
  const max = Math.max(...items.map(i => Math.max(i.share || 0, i.prev_share || 0)), 1);
  const restShare = rest.reduce((s, i) => s + (i.share || 0), 0);
  return `<div class="ablock">
    <div class="ah"><h3>${esc(b.title)}${b.unit ? ` <span class="u">${esc(b.unit)}</span>` : ''}</h3>
      <span class="hint num">${items.length} ta${sel.size ? ` · tanlangan ${sel.size}` : ''}</span></div>
    ${items.length ? `<div class="strip">${items.map(i => `<span style="width:${i.share || 0}%;background:${color(i)}" title="${esc(i.label)}: ${pct(i.share)}"></span>`).join('')}</div>` : `<div class="empty">Savdo yo'q</div>`}
    <div class="arows">${shown.map(i => `<button class="arow ${sel.has(i.key) ? 'on' : ''}" data-d="${d}" data-k="${esc(i.key)}" aria-pressed="${sel.has(i.key)}">
      <span class="sw" style="background:${color(i)}"></span>
      <span class="al">${esc(i.label)}</span>
      <span class="aa num">${money(i.current.amount)}</span>
      <span class="strk"><span class="sf" style="width:${(i.share || 0) / max * 100}%;background:${color(i)}"></span>
        ${hasCmp && i.prev_share != null ? `<span class="sp" style="left:${i.prev_share / max * 100}%"></span>` : ''}</span>
      <span class="as num"><b>${pct(i.share)}</b>${hasCmp ? pill(i.share_pp, true) : ''}</span>
      <span class="am num">${nf0.format(i.current.qty)} dona${hasCmp ? ` · savdo ${i.delta.amount == null ? '—' : (i.delta.amount > 0 ? '+' : '') + nf1.format(i.delta.amount) + '%'}` : ''} · <span class="${i.current.margin_pct < 0 ? 'neg' : ''}">marja ${pct(i.current.margin_pct)}</span></span>
    </button>`).join('')}
    ${rest.length ? `<button class="more amore" data-d="${d}">Yana ${rest.length} ta (${pct(restShare)}) — ko'rsatish</button>` : ''}
    ${expanded && items.length > A_TOP + 1 ? `<button class="more amore" data-d="${d}">Qisqartirish</button>` : ''}
    </div></div>`;
}
function renderAttrBlocks() {
  if (!ATTR) return;
  const host = $('ablocks');
  host.innerHTML = ATTR.blocks.map(attrBlock).join('');
  host.querySelectorAll('.arow').forEach(b => b.onclick = () => {
    const d = b.dataset.d, k = b.dataset.k;
    const set = new Set(st.f[d] || []); set.has(k) ? set.delete(k) : set.add(k);
    if (set.size) st.f[d] = set; else delete st.f[d];
    st.crumbs = st.crumbs.filter(c => c.dim !== d); st.showAll = false;
    load();
  });
  host.querySelectorAll('.amore').forEach(b => b.onclick = () => { st.aExpand[b.dataset.d] = !st.aExpand[b.dataset.d]; renderAttrBlocks(); });
}

// ---------------------------------------------------------------- TOP-10
function renderTop(t) {
  $('top').innerHTML = t.rows.length ? t.rows.map((i, n) => `<div class="sku">
    <span class="rk num">${n + 1}</span><span class="nm">${esc(i.label)}</span><span class="amt num">${money(i.current.amount)}</span>
    <span class="br">${esc(i.sub || '')} · ${nf0.format(i.current.qty)} dona</span><span class="mt num ${i.current.margin_pct < 0 ? 'neg' : ''}">marja ${pct(i.current.margin_pct)}</span>
  </div>`).join('') : `<div class="empty">Savdo yo'q.</div>`;
}

// ---------------------------------------------------------------- filtr oynasi
async function openSheet(d) {
  const host = $('sheetHost');
  host.innerHTML = `<div class="scrim" id="scrim"><div class="sheet"><header><div class="t"><h3>${esc(dimName(d))}</h3></div></header><div class="empty">Yuklanmoqda…</div></div></div>`;
  let opts;
  try { opts = (await api('options', filters()))[d] || []; }
  catch (e) { host.innerHTML = ''; showError(e); return; }
  const sel = new Set(st.f[d] || []);
  opts.forEach(o => remember(d, o.key, o.label));
  for (const k of sel) if (!opts.some(o => o.key === k)) opts.push({key: k, label: labels[d + '|' + k] || k, amount: 0});
  host.innerHTML = `<div class="scrim" id="scrim"><div class="sheet" role="dialog" aria-modal="true" aria-labelledby="shT">
    <header><div class="t"><h3 id="shT">${esc(dimName(d))}</h3><button class="x" id="shX" aria-label="Yopish">✕</button></div>
      ${opts.length > 8 ? `<input type="search" id="shQ" placeholder="Qidirish…" autocomplete="off">` : ''}
      <div class="foot">Joriy davr savdosi, boshqa filtrlar hisobga olingan</div></header>
    <div class="opts">${opts.map((o, i) => `<label class="opt ${o.amount ? '' : 'zero'}" data-l="${esc(String(o.label).toLowerCase())}">
      <input type="checkbox" data-i="${i}" ${sel.has(o.key) ? 'checked' : ''}><span>${esc(o.label)}</span><span class="a num">${money(o.amount)}</span></label>`).join('')}</div>
    <footer><button class="btn" id="shC">Tozalash</button><button class="btn primary" id="shA">Qo'llash</button></footer>
  </div></div>`;
  const close = () => { host.innerHTML = ''; };
  host.querySelector('#scrim').onclick = e => { if (e.target.id === 'scrim') close(); };
  host.querySelector('#shX').onclick = close;
  const q = host.querySelector('#shQ');
  if (q) q.oninput = () => { const v = q.value.toLowerCase(); host.querySelectorAll('.opt').forEach(o => o.hidden = !o.dataset.l.includes(v)); };
  host.querySelector('#shC').onclick = () => host.querySelectorAll('.opt input').forEach(i => i.checked = false);
  host.querySelector('#shA').onclick = () => {
    const s = new Set(); host.querySelectorAll('.opt input').forEach(i => { if (i.checked) s.add(opts[+i.dataset.i].key); });
    if (s.size) st.f[d] = s; else delete st.f[d];
    st.crumbs = st.crumbs.filter(c => c.dim !== d); st.showAll = false;
    close(); load();
  };
}

// ---------------------------------------------------------------- yuklash
const reqSeq = {main: 0, dyn: 0};
function showError(e) {
  if (e.status === 401 || e.status === 403) {
    $('app').hidden = true;
    const id = tg && tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : null;
    $('gate').hidden = false;
    $('gate').innerHTML = `<div class="gate"><h2>${e.status === 403 ? 'Ruxsat yo\'q' : 'Kirish tasdiqlanmadi'}</h2>
      <p>${esc(e.message)}</p>${id ? `<p>Telegram ID: <code>${id}</code> — administratorga yuboring.</p>` : '<p>Ilovani Telegram bot orqali oching.</p>'}</div>`;
    return;
  }
  const box = document.createElement('div');
  box.className = 'errbox'; box.textContent = 'Xato: ' + e.message;
  $('kpis').replaceChildren(box);
}
async function load() {
  renderCats(); renderPeriods(); renderFilterChips(); renderCrumbs();
  const F = filters(), seq = ++reqSeq.main;
  const secs = ['kpis', 'chart', 'breakdown', 'ablocks', 'top'].map($);
  secs.forEach(s => s.classList.add('loading'));
  const apiSort = st.sort === 'delta' ? 'amount' : st.sort;
  try {
    const [sum, daily, bd, attrs, top] = await Promise.all([
      api('summary', F),
      api('daily', F),
      api('breakdown', {...F, rows: st.rows, cols: st.cols || null, sort: apiSort, desc: st.sort !== 'name', limit: 500}),
      api('attributes', F),
      api('breakdown', {...F, rows: 'sku', sort: 'amount', limit: 10}),
    ]);
    if (seq !== reqSeq.main) return;          // eskirgan javob
    DAILY = daily; BD = bd; ATTR = attrs;
    renderKpis(sum); renderChart(); renderBreakdown(); renderAttrBlocks(); renderTop(top);
    renderFilterChips(); renderCrumbs();
  } catch (e) {
    if (seq === reqSeq.main) showError(e);
  } finally {
    if (seq === reqSeq.main) secs.forEach(s => s.classList.remove('loading'));
  }
}

// ---------------------------------------------------------------- boshqaruv
$('dFrom').onchange = e => { if (e.target.value) { st.from = e.target.value > st.to ? st.to : e.target.value; st.preset = ''; load(); } };
$('dTo').onchange = e => { if (e.target.value) { st.to = e.target.value < st.from ? st.from : e.target.value; st.preset = ''; load(); } };
$('cmp').onchange = e => { st.compare = e.target.value; load(); };
$('rowsDim').onchange = e => { st.rows = e.target.value; if (st.cols === st.rows) st.cols = ''; st.showAll = false; load(); };
$('colsDim').onchange = e => { st.cols = e.target.value === st.rows ? '' : e.target.value; st.showAll = false; load(); };
$('sortBy').onchange = e => { st.sort = e.target.value; load(); };
document.querySelectorAll('#chartMetric button').forEach(b => b.onclick = () => {
  st.chartMetric = b.dataset.m;
  document.querySelectorAll('#chartMetric button').forEach(x => x.setAttribute('aria-pressed', x === b));
  renderChart();
});
let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { renderChart(); if (st.shareView === 'dyn' && !st.cols) shareDyn(); }, 150); });

async function init() {
  try { META = await api('meta'); }
  catch (e) { showError(e); return; }
  if (!META.categories.length) { $('kpis').innerHTML = `<div class="errbox">Sizga ochiq kategoriya yo'q.</div>`; return; }
  if (!META.data_to) { $('kpis').innerHTML = `<div class="errbox">Bazada hali savdo yo'q — botga savdo faylini yuklang.</div>`; return; }
  $('dataInfo').textContent = `ma'lumot: ${dm(META.data_from)}–${dm(META.data_to)}`;
  st.cat = META.categories[0].id;
  [st.from, st.to] = presetRange(st.preset);
  load();
}
init();
})();
