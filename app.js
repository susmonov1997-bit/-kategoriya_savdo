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

// pp va % — rangli matn (kartalar ichida)
const sgn = v => (Math.abs(v) < 0.05 ? '' : v > 0 ? 'up' : 'dn');
const sg = v => (Math.abs(v) < 0.05 ? '' : v > 0 ? '+' : '−');
const ppTxt = v => (v == null || !isFinite(v)) ? '' : `<span class="pp ${sgn(v)}">${sg(v)}${nf1.format(Math.abs(v))} pp</span>`;
const dTxt = v => (v == null || !isFinite(v)) ? '' : `<span class="pp ${sgn(v)}">${sgn(v) === 'up' ? '▲' : sgn(v) === 'dn' ? '▼' : ''} ${nf1.format(Math.abs(v))}%</span>`;
const ppPlain = v => (v == null || !isFinite(v)) ? '' : `${sg(v)}${nf1.format(Math.abs(v))} pp`;
const cap = t => String(t || '').replace(/^./, x => x.toUpperCase());
const cell = (k, x, y) => `<span><span class="k">${k}</span><span class="x">${x}</span><span class="y">${y || '&nbsp;'}</span></span>`;
const qtyOrAmt = C => (MQ() ? `savdo <b>${money(C.amount)}</b>` : `<b>${nf0.format(C.qty || 0)}</b> dona`);

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
  cat: null, from: null, to: null, preset: '30', compare: 'mom',
  f: {},                 // dim -> Set(key)   (kalitlar API'dagidek matn)
  mode: 'tree',          // tree (daraxt) | pivot (kesma)
  rows: 'region', cols: 'attr:0', sort: 'amount',
  chartMetric: 'amount', shareView: 'struct', pvMode: 'amount', metric: 'amount',
  aExpand: {}, aOpen: null, topOpen: false, showAll: false,
};
const labels = {};       // "dim|key" -> nom (chip va breadcrumb uchun)
const remember = (d, k, l) => { labels[d + '|' + k] = l; };
const allScopes = () => [...META.categories, ...(META.scopes || [])];
const cat = () => allScopes().find(c => c.key === st.cat) || META.categories[0];
// ilova ochilganda: Barcha kategoriyalar (bo'lmasa — eng ko'p sotilgan kategoriya)
const defaultScope = () => ((META.scopes || []).find(s => s.kind === 'all') || META.categories[0]).key;
// Savdo | Dona: ulush, tartib va asosiy raqam qaysi ko'rsatkich bo'yicha
const MQ = () => st.metric === 'qty';
const mval = k => (MQ() ? k.qty : k.amount) || 0;
const mfmt = k => (MQ() ? `${nf0.format(k.qty || 0)} dona` : money(k.amount));
const mnum = k => (MQ() ? nf0.format(k.qty || 0) : money(k.amount));
const mdelta = d => (d ? (MQ() ? d.qty : d.amount) : null);
// Kompaniya jamisidagi ulush (maxraj — company_daily, «К»siz; faqat joy filtrlari)
const LOC_WORD = {region: 'hududda', cluster: 'klasterda', branch: 'filialda'};
function compShare(curK, prevK, comp) {
  // Ulush = bizning savdo / set jami («К»siz) — tepadagi filtr davri bo'yicha, ikkala tomon bir xil kunlar.
  // Set jami (company_daily) yo'q kunlarda jami = bizning savdo deb olinadi (jami bizning savdodan kam bo'lmaydi),
  // ya'ni maxraj = kompaniya jami (bor kunlar) + bizning savdo (kompaniya jami yo'q kunlar).
  comp = comp || {};
  const key = MQ() ? 'qty' : 'amount';
  const unc = (k, f) => Math.max(0, (k[f] || 0) - (k['cov_' + f] != null ? k['cov_' + f] : (k[f] || 0)));
  const sh = (k, f, cv) => { if (!k) return null; const den = (cv || 0) + unc(k, f); return den > 0 ? (k[f] || 0) / den * 100 : null; };
  const s = sh(curK, key, comp['c_' + key]), ps = sh(prevK, key, comp['p_' + key]);
  const g = sh(curK, 'gross', comp.c_gross), pg = sh(prevK, 'gross', comp.p_gross);
  return {s, pp: s != null && ps != null ? s - ps : null, g, gpp: g != null && pg != null ? g - pg : null,
          den: (comp['c_' + key] || 0) + unc(curK, key)};
}
function locScopeWord() {
  const ids = ['region', 'cluster', 'branch'].filter(d => st.f[d] && st.f[d].size);
  return ids.length ? 'tanlangan joyda' : 'kompaniyada';
}
function compLine(word, cs) {
  if (!cs) return '';
  const hasCmp = !!cmpRange();
  return `<span class="kc">🏢 ${word} <b>${pct(cs.s)}</b>${hasCmp && cs.pp != null ? pill(cs.pp, true) : ''}
    <span class="kb"><i style="width:${Math.min(100, cs.s || 0)}%"></i></span>front marja ${pct(cs.g)}</span>`;
}
const isMulti = () => cat().kind !== 'cat';
const attrDims = () => cat().attributes.map(a => a.dim);
const DIM_NAMES = {region: 'Hudud', cluster: 'Klaster', branch: 'Filial', brand: 'Brend', status: 'Status', sku: 'SKU',
                   category: 'Kategoriya', owner: "Mas'ul"};
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
const NUM_DIMS = {region: 'region_ids', cluster: 'cluster_ids', branch: 'branch_ids', sku: 'product_ids', category: 'category_ids'};

function filters() {
  const F = {scope: st.cat, date_from: st.from, date_to: st.to, compare: st.compare,
             region_ids: [], cluster_ids: [], branch_ids: [], product_ids: [], brands: [], statuses: [], attrs: {},
             category_ids: [], owners: [], share_by: st.metric};
  for (const d in st.f) {
    const vals = [...st.f[d]];
    if (!vals.length) continue;
    if (NUM_DIMS[d]) F[NUM_DIMS[d]] = vals.map(Number);
    else if (d === 'brand') F.brands = vals;
    else if (d === 'status') F.statuses = vals;
    else if (d === 'owner') F.owners = vals;
    else if (d.startsWith('attr:')) F.attrs[d.slice(5)] = vals;
  }
  return F;
}
// k oy orqaga (kun oy oxiriga qisqartiriladi); toEnd — oyning oxirgi kuni
function minusMonths(s, k, toEnd) {
  const [y, m, d] = s.split('-').map(Number);
  const t = y * 12 + (m - 1) - k, ny = Math.floor(t / 12), nm = t % 12 + 1;
  const last = new Date(Date.UTC(ny, nm, 0)).getUTCDate();
  return `${ny}-${String(nm).padStart(2, '0')}-${String(toEnd ? last : Math.min(d, last)).padStart(2, '0')}`;
}
const isMonthEnd = s => addDays(s, 1).slice(8) === '01';
function cmpRange() {
  if (st.compare === 'none') return null;
  const len = diffDays(st.from, st.to) + 1;
  if (st.compare === 'yoy') return [minusMonths(st.from, 12), minusMonths(st.to, 12)];
  if (st.compare === 'mom') {          // o'tgan oyning shu kunlari (API'dagi mom_period bilan bir xil)
    const end = isMonthEnd(st.to);
    let k = 1;
    while (minusMonths(st.to, k, end) >= st.from) k++;
    return [minusMonths(st.from, k), minusMonths(st.to, k, end)];
  }
  return [addDays(st.from, -len), addDays(st.from, -1)];
}

// ---------------------------------------------------------------- yuqori panel (A variant)
const PERIODS = [
  ['1', 'Kecha'], ['7', 'Oxirgi 7 kun'], ['14', 'Oxirgi 14 kun'], ['30', 'Oxirgi 30 kun'],
  ['week', 'Shu hafta'], ['lweek', "O'tgan hafta"], ['month', 'Shu oy'], ['lmonth', "O'tgan oy"]];
const PERIOD_NAME = Object.fromEntries(PERIODS);
const PERIOD_SHORT = {'1': 'Kecha', '7': '7 kun', '14': '14 kun', '30': '30 kun', week: 'Shu hafta',
                      lweek: "O'tgan hafta", month: 'Shu oy', lmonth: "O'tgan oy"};
const CMP_NAME = {mom: "O'tgan oy", prev: "Oldingi kunlar", yoy: "O'tgan yil", none: "Yo'q"};
const CMP_SHORT = {mom: "o'tgan oy", prev: 'oldingi kunlar', yoy: "o'tgan yil", none: ''};
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
const prodDims = () => [...attrDims(), ...(cat().extra_dims || []), 'brand', 'status'];
function renderHeader() {
  const nf = Object.values(st.f).filter(s => s.size).length;
  const c = cat();
  $('catBtn').innerHTML = `<span class="tn">${esc(c.name)}</span><span class="car">▾</span>`;
  updMeta();
  const per = (st.preset && PERIOD_SHORT[st.preset]) || periodText();
  $('perBtn').innerHTML = `${esc(per)}${st.compare === 'yoy' ? ' · o\'tgan yil' : ''} <span class="car">▾</span>`;
  $('filBtn').innerHTML = `Filtr${nf ? ` <span class="badge">${nf}</span>` : ''}`;
  document.querySelectorAll('#metricSeg button').forEach(b => {
    b.setAttribute('aria-pressed', b.dataset.m === st.metric);
    b.onclick = () => { if (st.metric === b.dataset.m) return; st.metric = b.dataset.m; resetView(); load(); };
  });
  $('filBtn').classList.toggle('acc', nf > 0);
  const chips = [];
  for (const d of [...LOC_DIMS, ...prodDims()]) {
    const s = st.f[d]; if (!s || !s.size) continue;
    const val = s.size === 1 ? (labels[d + '|' + [...s][0]] || [...s][0]) : `${s.size} ta`;
    chips.push(s.size === 1
      ? `<button class="fchip" data-d="${d}" title="${esc(dimName(d))} — olib tashlash">${esc(val)} ✕</button>`
      : `<button class="fchip" data-d="${d}" title="Olib tashlash"><b>${esc(dimName(d))}:</b> ${esc(val)} ✕</button>`);
  }
  if (chips.length > 1) chips.push(`<button class="fchip clr" id="clrAll">Tozalash</button>`);
  $('fchips').innerHTML = chips.join('');
  $('fchips').hidden = !chips.length;
  $('fchips').querySelectorAll('.fchip[data-d]').forEach(b => b.onclick = () => { delete st.f[b.dataset.d]; resetView(); load(); });
  const ca = $('clrAll'); if (ca) ca.onclick = () => { st.f = {}; resetView(); load(); };
}
function updMeta() {
  if (!META) return;
  const c = VIEW === 'analytics' && st.cat ? cat() : null;
  const parts = c ? (c.kind === 'cat' ? [c.group, c.owner] : [`${c.n_cats} ta kategoriya`]) : [];
  if (META.data_to) parts.push(`ma'lumot ${dm(META.data_to)} gacha`);
  $('tmeta').textContent = parts.filter(Boolean).join(' · ');
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
  const row = (c, sub) => `<button class="optrow ${c.key === st.cat ? 'on' : ''}" data-k="${esc(c.key)}">
      <span class="ol"><span>${esc(c.name)}</span>${sub ? `<span class="os">${esc(sub)}</span>` : ''}</span>
      <span class="a num">${c.kind === 'cat' ? `${c.skus} SKU` : `${c.n_cats} kat.`}</span></button>`;
  const sc = META.scopes || [];
  const tops = sc.filter(c => c.kind === 'all' || c.kind === 'group'), owners = sc.filter(c => c.kind === 'owner');
  const groups = {};
  META.categories.forEach(c => (groups[c.group || '—'] = groups[c.group || '—'] || []).push(c));
  const secs = [
    {id: 'top', title: 'Umumiy', items: tops, sub: () => ''},
    {id: 'own', title: "Mas'ul bo'yicha", items: owners,
     sub: c => META.categories.filter(x => x.owner === c.name).map(x => x.name).join(', ')},
    ...Object.keys(groups).sort().map(g => ({id: 'g:' + g, title: g === '—' ? 'Kategoriyalar' : g, items: groups[g], sub: c => c.owner})),
  ].filter(x => x.items.length);
  const open = new Set();                       // sukut: hamma bo'lim yig'ilgan
  const body = () => secs.map(x => {
    const cur = x.items.find(c => c.key === st.cat), on = open.has(x.id);
    return `<div class="csec ${on ? 'open' : ''}">
      <button class="csec-h" data-s="${esc(x.id)}" aria-expanded="${on}"><span class="c">${on ? '▾' : '▸'}</span>
        <span class="t">${esc(x.title)}</span><span class="n">${cur ? `✓ ${esc(cur.name)} · ` : ''}${x.items.length} ta</span></button>
      ${on ? `<div class="optlist">${x.items.map(c => row(c, x.sub(c))).join('')}</div>` : ''}</div>`;
  }).join('');
  const host = openSheetBox('Kategoriya', body());
  const sb = host.querySelector('.sbody');
  const wire = () => {
    sb.querySelectorAll('.csec-h').forEach(b => b.onclick = () => {
      const id = b.dataset.s; open.has(id) ? open.delete(id) : open.add(id); sb.innerHTML = body(); wire(); });
    sb.querySelectorAll('.optrow').forEach(b => b.onclick = () => {
      const k = b.dataset.k; closeSheet();
      if (k === st.cat) return;
      st.cat = k; st.f = {}; st.aExpand = {}; st.aOpen = null; st.rows = 'region'; st.cols = ''; resetView(); load();
    });
  };
  wire();
}

function openPeriodSheet() {
  let dPreset = st.preset, dFrom = st.from, dTo = st.to, dCmp = st.compare;
  const body = () => `
    <div class="pgrid">${PERIODS.map(([k, t]) => `<button class="opt2 ${dPreset === k ? 'on' : ''}" data-k="${k}">${t}</button>`).join('')}</div>
    <div class="lbl">Ixtiyoriy davr <span class="hint">(ma'lumot: ${dm(META.data_from)}–${dm(META.data_to)})</span></div>
    <div class="dates2"><input type="date" id="pF" value="${dFrom}" min="${META.data_from}" max="${META.data_to}" aria-label="Boshlanish">
      <span>—</span><input type="date" id="pT" value="${dTo}" min="${META.data_from}" max="${META.data_to}" aria-label="Tugash"></div>
    <div class="lbl">Solishtirish</div>
    <div class="pgrid">${Object.entries(CMP_NAME).map(([k, t]) => `<button class="opt2 cmp ${dCmp === k ? 'on' : ''}" data-c="${k}">${t}</button>`).join('')}</div>`;
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
  const open = new Set();                     // sukut: barcha bo'limlar yig'ilgan
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
function companyCard(s) {
  const comp = s.company || {}, C = s.current, Pv = s.previous;
  const locs = ['region', 'cluster', 'branch'].filter(d => st.f[d] && st.f[d].size);
  const one = locs.length === 1 && st.f[locs[0]].size === 1 ? locs[0] : null;
  const place = !locs.length ? 'Set' : one ? (labels[one + '|' + [...st.f[one]][0]] || 'Tanlangan joy') : 'Tanlangan joylar';
  const title = `${esc(place)} jami savdosidagi ulush («К»siz)`;
  const cs = compShare(C, Pv, comp);
  const unit = MQ() ? v => `${nf0.format(v)} dona` : money;
  const days = s.days ? s.days.c : 0, cd = comp.c_days || 0, miss = Math.max(0, days - cd);
  return `<div class="kpi wide comp">
    <span class="l">${title}</span>
    <div class="cv"><div><span class="v">${pct(cs.s)}</span> ${Pv && cs.pp != null ? pill(cs.pp, true) : ''}
        <div class="p">${MQ() ? 'donada' : 'savdoda'}: ${unit(MQ() ? C.qty : C.amount)} / ${unit(cs.den)}</div></div>
      <div style="text-align:right"><span class="l">front marjada</span>
        <div class="v" style="font-size:17px">${pct(cs.g)} ${Pv && cs.gpp != null ? pill(cs.gpp, true) : ''}</div></div></div>
    <div class="cbar"><i style="width:${Math.min(100, cs.s || 0)}%"></i></div>
    ${miss ? `<span class="hint">⚠ ${days} kundan ${miss} kunida set jami yuklanmagan — o'sha kunlarda jami = bizning savdo deb olindi.</span>` : ''}
  </div>`;
}
let SUM = null;
function heroSpark() {
  if (!DAILY || DAILY.series.length < 2) return '';
  const s = DAILY.series, n = s.length, W = 330, H = 46;
  const cur = s.map(p => p.amount || 0), prev = cmpRange() ? s.map(p => p.prev_amount || 0) : null;
  const max = Math.max(...cur, ...(prev || []), 1);
  const pts = a => a.map((v, i) => `${(i / (n - 1) * W).toFixed(1)},${(H - 3 - (H - 8) * v / max).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">
    ${prev ? `<polyline fill="none" stroke="var(--prev)" stroke-width="2" vector-effect="non-scaling-stroke" points="${pts(prev)}"/>` : ''}
    <polyline fill="none" stroke="var(--accent)" stroke-width="2.5" vector-effect="non-scaling-stroke" points="${pts(cur)}"/></svg>`;
}
function renderKpis(s) {
  SUM = s;
  const C = s.current, Pv = s.previous, d = s.delta || {}, cw = CMP_SHORT[st.compare];
  $('cmpHint').textContent = s.compare_period
    ? `${dm(st.from)}–${dm(st.to)} · solishtirish: ${cw} ${dm(s.compare_period.from)}–${dm(s.compare_period.to)}`
    : `${dm(st.from)}–${dm(st.to)}`;
  const neg = v => (v < 0 ? ' neg' : '');
  const incPct = C.amount ? C.income / C.amount * 100 : null;
  const pincPct = Pv && Pv.amount ? Pv.income / Pv.amount * 100 : null;
  const card = (l, v, p, pl, cls = '') => `<div class="kpi"><span class="l">${l}</span><span class="v${cls}">${v}</span>
    <span class="p">${p}</span>${pl ? `<span class="pl">${pl}</span>` : ''}</div>`;
  $('kpis').innerHTML = `
    <div class="kpi wide hero"><div class="top2"><span class="l">Savdo</span>
      ${Pv ? `<span>${pill(d.amount)} <span class="hint">${cw}: ${money(Pv.amount)}</span></span>` : ''}</div>
      <span class="v">${money(C.amount)}</span>${heroSpark()}</div>
    ${card('Gross marja', money(C.margin), `${pct(C.margin_pct)} savdodan`, Pv ? pill(d.margin_pct_pp, true) + pill(d.margin) : '', neg(C.margin))}
    ${card('Front marja', money(C.gross), `${pct(C.gross_pct)} savdodan`, Pv ? pill(d.gross_pct_pp, true) + pill(d.gross) : '', neg(C.gross))}
    ${card("Qo'shimcha daromad", money(C.income), `${pct(incPct)} savdodan`,
           Pv ? (incPct != null && pincPct != null ? pill(incPct - pincPct, true) : '') + pill(d.income) : '')}
    ${card('Dona', nf0.format(C.qty), C.bonus_qty ? `shundan bonus ${nf0.format(C.bonus_qty)}` : (Pv ? `${cw}: ${nf0.format(Pv.qty)}` : '&nbsp;'), Pv ? pill(d.qty) : '')}
    ${card("O'rtacha narx", C.avg_price ? money(C.avg_price) : '—', Pv && Pv.avg_price ? `${cw}: ${money(Pv.avg_price)}` : 'bonussiz', Pv ? pill(d.avg_price) : '')}
    ${card('Qamrov', `${nf0.format(C.skus)} SKU`, `${nf0.format(C.branches)} filial`,
           Pv ? `<span class="hint">${cw}: ${nf0.format(Pv.skus)} SKU · ${nf0.format(Pv.branches)} filial</span>` : '')}
    ${companyCard(s)}`;
}

// --- Umumiy: kategoriyalar (bir nechta kategoriya) yoki brendlar (bitta kategoriya) ro'yxati
let LIST_ALL = false;
function renderCatList() {
  if (!LIST) return;
  const multi = isMulti(), host = $('catList');
  const b = {dim: LIST.dim, items: LIST.rows};
  $('listH').textContent = multi ? 'Kategoriyalar' : 'Brendlar';
  if (!b || !b.items.length) { host.innerHTML = `<div class="empty">Savdo yo'q</div>`; $('listHint').textContent = ''; return; }
  const items = b.items.slice().sort((x, y) => mval(y.current) - mval(x.current));
  items.forEach(i => remember(b.dim, i.key, i.label));
  const sel = st.f[b.dim] || new Set();
  $('listHint').textContent = multi ? 'bosing — kategoriyaga o\'tadi' : 'bosing — filtrga qo\'shiladi';
  const shown = LIST_ALL ? items : items.slice(0, 8);
  const max = Math.max(...items.map(i => i.share || 0), 1), hasCmp = !!cmpRange();
  host.innerHTML = shown.map(i => { const C = i.current;
    return `<button class="li can ${sel.has(i.key) ? 'op' : ''}" data-k="${esc(i.key)}">
      <span class="r1"><span class="n">${esc(i.label)}${sel.has(i.key) ? ' ✓' : ''}</span><span class="v">${mnum(C)}</span></span>
      <span class="bar"><i style="width:${(i.share || 0) / max * 100}%"></i></span>
      <span class="m"><span>ulush <b>${pct(i.share)}</b>${hasCmp ? ' ' + ppTxt(i.share_pp) : ''}</span>${hasCmp ? dTxt(mdelta(i.delta)) : ''}
        <span>gross <b>${money(C.margin)}</b> · ${pct(C.margin_pct)}</span><span>front <b>${money(C.gross)}</b> · ${pct(C.gross_pct)}</span>
        <span>${qtyOrAmt(C)}</span></span></button>`; }).join('')
    + (items.length > 8 ? `<button class="more" id="listMore">${LIST_ALL ? 'Qisqartirish' : `Hammasi (${items.length} ta)`}</button>` : '');
  host.querySelectorAll('.li[data-k]').forEach(el => el.onclick = () => {
    const k = el.dataset.k;
    if (multi) {
      const c = META.categories.find(c => String(c.id) === String(k)); if (!c) return;
      st.cat = c.key; st.f = {}; st.aExpand = {}; st.aOpen = null; st.rows = 'region'; st.cols = ''; LIST_ALL = false;
      resetView(); load(); window.scrollTo(0, 0); return;
    }
    const set = new Set(st.f.brand || []); set.has(k) ? set.delete(k) : set.add(k);
    if (set.size) st.f.brand = set; else delete st.f.brand;
    resetView(); load();
  });
  const m = $('listMore'); if (m) m.onclick = () => { LIST_ALL = !LIST_ALL; renderCatList(); };
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
    (c ? `<span><i style="background:var(--prev)"></i>${CMP_SHORT[st.compare].replace(/^./, x => x.toUpperCase())} ${dm(c[0])}–${dm(c[1])}</span>` : '');
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
  const dims = [...LOC_PATH, ...(cat().extra_dims || []), ...attrDims(), 'brand', 'status', 'sku'];
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
    else if (p.dim === 'owner') F.owners = [p.key];
    else if (p.dim.startsWith('attr:')) F.attrs = {...F.attrs, [p.dim.slice(5)]: [p.key]};
  }
  return F;
}
const apiSort = () => (st.sort === 'delta' ? st.metric : st.sort === 'amount' && MQ() ? 'qty' : st.sort);
function sortRows(rows) {
  if (st.sort === 'delta') rows.sort((a, b) => (mdelta(b.delta) ?? -1e9) - (mdelta(a.delta) ?? -1e9));
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
  node.total = res.total; node.total_rows = res.total_rows; node.raw = sortRows(res.rows); node.company = res.company_total;
  node.children = node.raw.map(r => {
    remember(node.childDim, r.key, r.label);
    const path = [...node.path, {dim: node.childDim, key: r.key}];
    return {root: node.root, order: node.order, path, dim: node.childDim, key: r.key, label: r.label, sub: r.sub, row: r,
            depth: node.depth + 1, childDim: childDimFor(path, node.order), children: null, open: false, showAll: false};
  });
  return res;
}
async function buildTree() {
  // ilova ochilganda hammasi yig'ilgan; foydalanuvchi ochgan bo'lsa (shu seansda) ochiq qoladi
  const root = newRoot('m', 'drill', [], {label: 'Jami', depth: 0, open: false});
  root.open = OPEN.has(nsig(root));
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
  { const o = $('sortBy').querySelector('option[value="amount"]'); if (o) o.textContent = MQ() ? 'dona' : 'savdo';
    const q = $('sortBy').querySelector('option[value="qty"]'); if (q) q.hidden = MQ(); }
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
  const r = node.row, C = r.current, dd = r.delta || {}, hasCmp = !!cmpRange(), can = !!node.childDim;
  const ind = Math.min(Math.max(node.depth - indBase, 0), 4) * 12;
  const cs = LOC_WORD[node.dim] ? compShare(C, r.previous, r.company) : null;
  const third = cs
    ? cell(`${cap(LOC_WORD[node.dim])} ulush`, pct(cs.s), `${hasCmp && cs.pp != null ? ppTxt(cs.pp) + ' · ' : ''}front ${pct(cs.g)}`)
    : cell("O'rt. narx", C.avg_price ? money(C.avg_price) : '—', hasCmp ? dTxt(dd.avg_price) : '');
  return `<button class="li nd ${can ? 'can' : ''} ${node.open ? 'op' : ''} ${node.depth - indBase > 0 ? 'deep' : ''}" data-s="${esc(nsig(node))}" ${can ? '' : 'tabindex="-1"'}
      style="--ind:${ind}px" aria-expanded="${!!node.open}">
    <span class="r1"><span class="n"><span class="c">${node.loading ? '…' : can ? (node.open ? '▾' : '▸') : '·'}</span>
      <span>${esc(node.label)}${node.sub && node.dim === 'sku' ? ` <span class="sub2">· ${esc(node.sub)}</span>` : ''} <span class="lvtag">${esc(dimName(node.dim))}</span></span></span>
      <span class="v">${mnum(C)}</span></span>
    <span class="m"><span>ulush <b>${pct(r.share)}</b>${hasCmp ? ' ' + ppTxt(r.share_pp) : ''}</span>${hasCmp ? pill(mdelta(dd)) : ''}<span>${qtyOrAmt(C)}</span></span>
    <span class="bar"><i style="width:${Math.min(100, r.share || 0)}%"></i></span>
    <span class="mx">${cell('Gross marja', `<span class="${C.margin < 0 ? 'neg' : ''}">${money(C.margin)}</span>`, `${pct(C.margin_pct)}${hasCmp && dd.margin_pct_pp != null ? ' · ' + ppTxt(dd.margin_pct_pp) : ''}`)}${cell('Front marja', `<span class="${C.gross < 0 ? 'neg' : ''}">${money(C.gross)}</span>`, `${pct(C.gross_pct)}${hasCmp && dd.gross_pct_pp != null ? ' · ' + ppTxt(dd.gross_pct_pp) : ''}`)}${third}</span>
  </button>`;
}
function subtreeRows(node, out, rootAmt, indBase = 1) {
  if (!node.open || !node.children) return;
  const ind = Math.min(Math.max(node.depth + 1 - indBase, 0), 4) * 12;
  if (!node.children.length) { out.push(`<div class="tr-empty" style="--ind:${ind}px">Savdo yo'q</div>`); return; }
  const shown = node.showAll ? node.children : node.children.slice(0, CHILD_LIMIT);
  for (const ch of shown) { out.push(rowHtml(ch, rootAmt, indBase)); subtreeRows(ch, out, rootAmt, indBase); }
  const rest = node.children.length - shown.length;
  if (rest > 0) out.push(`<button class="tr-more" data-more="${esc(nsig(node))}" style="--ind:${ind}px">Yana ${rest} ta ${esc(dimName(node.childDim)).toLowerCase()} ko'rsatish</button>`);
  if (node.total_rows > node.children.length) out.push(`<div class="tr-empty" style="--ind:${ind}px">Birinchi ${node.children.length} ta ko'rsatilgan (jami ${node.total_rows})</div>`);
}
function wireTree(host) {
  host.querySelectorAll('.nd.can[data-s], .tot[data-s]').forEach(b => b.onclick = e => { e.stopPropagation(); const n = findBySig(b.dataset.s); if (n) toggleNode(n); });
  host.querySelectorAll('.tr-more[data-more]').forEach(b => b.onclick = e => { e.stopPropagation(); const n = findBySig(b.dataset.more); if (n) { n.showAll = true; rerender(n.root); } });
}
function sumPrev(rows) {
  if (!rows || !rows.length || !rows[0].previous) return null;
  const ks = ['amount', 'qty', 'gross', 'cov_amount', 'cov_qty', 'cov_gross'];
  return rows.reduce((a, r) => { ks.forEach(k => { a[k] += r.previous[k] || 0; }); return a; },
                     Object.fromEntries(ks.map(k => [k, 0])));
}
function renderTree() {
  const host = $('breakdown');
  if (!TREE) { host.innerHTML = ''; return; }
  const T = TREE.total, rootAmt = T ? mval(T) : 0;
  if (!TREE.childDim || !T) { host.innerHTML = `<div class="list"><div class="empty">Tanlangan filtrlar bo'yicha savdo yo'q.</div></div>`; return; }
  const d = (SUM && SUM.delta) || {}, hasCmp = !!cmpRange();
  const cs = compShare(T, sumPrev(TREE.raw), TREE.company);
  const tc = (k, x, y) => `<span><span class="k">${k}</span><span class="x">${x}</span><span class="y">${y || '&nbsp;'}</span></span>`;
  const out = [`<button class="tot" data-s="${esc(nsig(TREE))}" aria-expanded="${!!TREE.open}">
      <span class="r1"><span class="l">Jami ${MQ() ? 'dona' : 'savdo'}</span>${hasCmp ? pill(mdelta(d)) : ''}</span>
      <span class="v">${mfmt(T)}</span>
      <span class="g">${tc('Gross marja', money(T.margin), `${pct(T.margin_pct)}${hasCmp && d.margin_pct_pp != null ? ' · ' + ppPlain(d.margin_pct_pp) : ''}`)}${tc('Front marja', money(T.gross), `${pct(T.gross_pct)}${hasCmp && d.gross_pct_pp != null ? ' · ' + ppPlain(d.gross_pct_pp) : ''}`)}${cs
        ? tc(cap(locScopeWord()), pct(cs.s), `${hasCmp && cs.pp != null ? ppPlain(cs.pp) + ' · ' : ''}front ${pct(cs.g)}`)
        : tc(MQ() ? 'Savdo' : 'Dona', MQ() ? money(T.amount) : nf0.format(T.qty), T.avg_price ? `o'rt. ${money(T.avg_price)}` : '')}</span>
      <span class="ft">${TREE.open ? '▾' : '▸'} ${esc(dimName(TREE.childDim))} bo'yicha ${TREE.open ? 'yopish' : 'ochish'}${MQ() ? '' : ` · ${nf0.format(T.qty)} dona`}</span></button>`];
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
  host.innerHTML = `<div class="card sharecard">
    <div class="sh"><h2>${MQ() ? 'Dona' : 'Savdo'} tuzilmasi · ${esc(dimName(node.childDim))}</h2>
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
  const byAmt = node.raw.slice().sort((a, b) => mval(b.current) - mval(a.current));
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
      tip.innerHTML = `<b>${dm(d.date)}</b> · ${MQ() ? nf0.format(d.total) + ' dona' : money(d.total)}<br>` + d.shares.map((p, j) => `${esc(names[j])}: ${nf1.format(p)}%`).join('<br>');
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
  const mk = MQ() ? 'qty' : 'amount';
  const val = (i, c) => { const v = ((cells[i.key] || {})[c.key] || {})[mk] || 0;
    if (st.pvMode === 'row') return mval(i.current) ? v / mval(i.current) * 100 : 0;
    if (st.pvMode === 'col') return c[mk] ? v / c[mk] * 100 : 0;
    return v; };
  let max = 0; for (const i of rowsShown) for (const c of cols) max = Math.max(max, val(i, c));
  const heat = v => { if (!v || !max) return ''; const t = v / max; const s = t > .8 ? '--h5' : t > .6 ? '--h4' : t > .4 ? '--h3' : t > .2 ? '--h2' : '--h1';
    return `background:var(${s});${t > .6 ? 'color:#fff' : ''}`; };
  const mln = v => (v ? (MQ() ? nf0.format(v) : nf1.format(v / 1e6)) : '·');
  const fmtCell = v => st.pvMode === 'amount' ? mln(v) : (v ? nf1.format(v) + '%' : '·');
  const lastCol = i => st.pvMode === 'amount' ? `<b>${mln(mval(i.current))}</b>` : st.pvMode === 'row' ? '<b>100%</b>' : `<b>${pct(i.share)}</b>`;
  host.innerHTML = `<div class="seg mini" id="pvMode" style="align-self:flex-start;display:inline-flex">
      <button data-v="amount" aria-pressed="${st.pvMode === 'amount'}">${MQ() ? 'Dona' : 'Savdo, mln'}</button>
      <button data-v="row" aria-pressed="${st.pvMode === 'row'}">Qator ichida %</button>
      <button data-v="col" aria-pressed="${st.pvMode === 'col'}">Ustun ichida %</button></div>
    <div class="pivotwrap"><table class="pv num">
    <thead><tr><th>${esc(dimName(st.rows))} \\ ${esc(dimName(st.cols))}</th>${cols.map(c => `<th>${esc(c.label)}</th>`).join('')}<th>Jami</th></tr></thead>
    <tbody>${rowsShown.map(i => `<tr><td title="${esc(i.label)}">${esc(i.label)}</td>${cols.map(c => { const v = val(i, c);
        const cell = (cells[i.key] || {})[c.key];
        return `<td class="c" style="${heat(v)}" title="${cell ? (MQ() ? `savdo ${money(cell.amount)} · ` : `${nf0.format(cell.qty)} dona · `) + `gross marja ${pct(cell.margin_pct)}` : ''}">${fmtCell(v)}</td>`; }).join('')}<td>${lastCol(i)}</td></tr>`).join('')}
      <tr class="tot"><td>Jami</td>${cols.map(c => `<td>${st.pvMode === 'amount' ? mln(c[mk]) : st.pvMode === 'row' ? (mval(T) ? nf1.format((c[mk] || 0) / mval(T) * 100) + '%' : '·') : '100%'}</td>`).join('')}<td>${st.pvMode === 'amount' ? mln(mval(T)) : '100%'}</td></tr>
    </tbody></table></div>
    <div class="foot">${st.pvMode === 'amount' ? (MQ() ? 'Kataklarda dona (bonus bilan).' : "Kataklarda savdo, mln so'm.") : st.pvMode === 'row' ? `Har bir qatorda: shu qator ${MQ() ? 'donasining' : 'savdosining'} ustunlar bo'yicha taqsimoti.` : `Har bir ustunda: shu ustun ${MQ() ? 'donasining' : 'savdosining'} qatorlar bo'yicha taqsimoti.`}
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
  const byVal = items.slice().sort((x, y) => mval(y.current) - mval(x.current));
  const rank = new Map(byVal.map((i, n) => [i.key, n]));
  const color = i => rank.get(i.key) < SLOTS ? `var(--s${rank.get(i.key) + 1})` : 'var(--so)';
  const isOpen = st.aOpen.has(d);
  const expanded = !!st.aExpand[d];
  let shown = items, rest = [];
  if (!expanded && items.length > A_TOP + 1) {
    shown = items.filter(i => rank.get(i.key) < A_TOP || sel.has(i.key));
    rest = items.filter(i => !(rank.get(i.key) < A_TOP || sel.has(i.key)));
  }
  const max = Math.max(...items.map(i => i.share || 0), 1);
  const restShare = rest.reduce((s, i) => s + (i.share || 0), 0);
  const total = items.reduce((s, i) => s + mval(i.current), 0);
  const rows = isOpen ? shown.map(i => {
    const node = attrNode(d, i); node.row = i;
    const can = !!node.childDim, C = i.current, on = sel.has(i.key);
    const sub = []; subtreeRows(node, sub, total, 1);
    return `<div class="it ${on ? 'on' : ''} ${can ? 'can' : ''} ${node.open ? 'op' : ''}" role="button" tabindex="0" data-s="${esc(nsig(node))}" aria-expanded="${!!node.open}">
      <span class="n"><span class="c">${node.loading ? '…' : can ? (node.open ? '▾' : '▸') : '·'}</span><span class="sw" style="background:${color(i)}"></span><span>${esc(i.label)}</span></span>
      <span class="v">${mnum(C)}</span>
      <button class="fbtn ${on ? 'on' : ''}" data-d="${d}" data-k="${esc(i.key)}" title="${on ? 'Filtrdan olib tashlash' : "Filtrga qo'shish"}" aria-label="${on ? 'Filtrdan olib tashlash' : "Filtrga qo'shish"}">${on ? '✓' : '+'}</button>
      <span class="bar"><i style="width:${(i.share || 0) / max * 100}%;background:${color(i)}"></i></span>
      <span class="m"><span>ulush <b>${pct(i.share)}</b>${hasCmp ? ' ' + ppTxt(i.share_pp) : ''}</span>${hasCmp ? dTxt(mdelta(i.delta)) : ''}
        <span>gross <b class="${C.margin < 0 ? 'neg' : ''}">${money(C.margin)}</b> · ${pct(C.margin_pct)}</span>
        <span>front <b class="${C.gross < 0 ? 'neg' : ''}">${money(C.gross)}</b> · ${pct(C.gross_pct)}</span><span>${qtyOrAmt(C)}</span></span>
    </div>${sub.length ? `<div class="tw sub">${sub.join('')}</div>` : ''}`;
  }).join('') : '';
  const lead = byVal[0];
  return `<div class="ablock ${isOpen ? 'open' : ''}">
    <button class="ah" data-d="${d}" aria-expanded="${isOpen}"><h3><span class="c">${isOpen ? '▾' : '▸'}</span>${esc(b.title)}${b.unit ? ` <span class="u">${esc(b.unit)}</span>` : ''}</h3>
      <span class="hint num">${items.length} ta${sel.size ? ` · tanlangan ${sel.size}` : !isOpen && lead ? ` · ${esc(lead.label)} ${pct(lead.share)}` : ''}</span></button>
    ${items.length ? `<div class="strip">${byVal.map(i => `<span style="width:${i.share || 0}%;background:${color(i)}" title="${esc(i.label)}: ${pct(i.share)}"></span>`).join('')}</div>` : `<div class="empty">Savdo yo'q</div>`}
    ${isOpen ? `<div class="arows">${rows}
    ${rest.length ? `<button class="more amore" data-d="${d}">Yana ${rest.length} ta (${pct(restShare)}) — ko'rsatish</button>` : ''}
    ${expanded && items.length > A_TOP + 1 ? `<button class="more amore" data-d="${d}">Qisqartirish</button>` : ''}</div>` : ''}
    </div>`;
}
function renderAttrBlocks() {
  if (!ATTR) return;
  if (!st.aOpen) st.aOpen = new Set();          // sukut: barcha bloklar yig'ilgan
  $('attrH').textContent = isMulti() ? "Kategoriya va brend bo'yicha" : "Xususiyatlar bo'yicha";
  const host = $('ablocks');
  host.innerHTML = ATTR.blocks.map(attrBlock).join('');
  host.querySelectorAll('.ah').forEach(b => b.onclick = () => { const d = b.dataset.d; if (!st.aOpen) st.aOpen = new Set(); st.aOpen.has(d) ? st.aOpen.delete(d) : st.aOpen.add(d); renderAttrBlocks(); });
  host.querySelectorAll('.it.can').forEach(r => {
    const go = () => { const n = findBySig(r.dataset.s); if (n) toggleNode(n); };
    r.onclick = go; r.onkeydown = e => { if (e.target !== r) return; if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } };
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
  const C = c.sum.current, P = c.sum.previous, d = c.sum.delta || {}, hasCmp = !!P;
  const kv = (l, v, y) => `<div class="kv"><span class="l">${l}</span><span class="v">${v}</span><span class="y">${y || '&nbsp;'}</span></div>`;
  return `<div class="skucard">
    ${c.prod ? `<div class="tags">${[`<span class="tag">${esc(c.prod.brand)}</span>`, c.prod.status ? `<span class="tag st">${esc(c.prod.status)}</span>` : '',
      ...(c.prod.attrs || []).map(a => `<span class="tag"><b>${esc(a.name)}:</b> ${esc(a.value)}</span>`)].join('')}</div>` : ''}
    <div class="kvs">
      ${kv('Savdo', money(C.amount), hasCmp ? dTxt(d.amount) : '')}
      ${kv('Gross marja', `<span class="${C.margin < 0 ? 'neg' : ''}">${money(C.margin)}</span>`, `${pct(C.margin_pct)}${hasCmp && d.margin_pct_pp != null ? ' · ' + ppTxt(d.margin_pct_pp) : ''}`)}
      ${kv('Front marja', `<span class="${C.gross < 0 ? 'neg' : ''}">${money(C.gross)}</span>`, `${pct(C.gross_pct)}${hasCmp && d.gross_pct_pp != null ? ' · ' + ppTxt(d.gross_pct_pp) : ''}`)}
      ${kv('Dona', nf0.format(C.qty), C.bonus_qty ? `bonus ${nf0.format(C.bonus_qty)}` : hasCmp ? dTxt(d.qty) : '')}
      ${kv("O'rt. narx", C.avg_price ? money(C.avg_price) : '—', hasCmp ? dTxt(d.avg_price) : '')}
      ${hasCmp ? kv(cap(CMP_SHORT[st.compare]), money(P.amount), `${nf0.format(P.qty)} dona`) : kv('Filiallar', nf0.format(C.branches), 'ta')}
    </div>
    ${sparkline(c.daily.series)}
    <div class="lbl" style="margin-top:2px">📍 Qayerda sotilgan · ${nf0.format(C.branches)} filial</div>
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
  const sumTop = t.rows.reduce((s, i) => s + mval(i.current), 0), tot = t.total ? mval(t.total) : 0;
  $('topHint').textContent = t.rows.length ? `${t.rows.length} ta · ${MQ() ? nf0.format(sumTop) + ' dona' : money(sumTop)}${tot ? ' · ' + pct(sumTop / tot * 100) : ''}` : "savdo yo'q";
  head.querySelector('.c').textContent = st.topOpen ? '▾' : '▸';
  head.setAttribute('aria-expanded', st.topOpen);
  $('topBlock').classList.toggle('open', st.topOpen);
  head.onclick = () => { st.topOpen = !st.topOpen; renderTopBody(); };
  host.hidden = !st.topOpen;
  if (!st.topOpen) { host.innerHTML = ''; return; }
  host.innerHTML = t.rows.length ? t.rows.map((i, n) => {
    const rid = `s:${i.key}`;
    const node = ROOTS.get(rid) || newRoot(rid, 'loc', [{dim: 'sku', key: i.key}], {label: i.label, row: i});
    const sub = []; if (node.open && node.card && !node.card.error) subtreeRows(node, sub, mval(i.current), 1);
    const C = i.current, hasCmp = !!cmpRange();
    return `<button type="button" class="sk can ${node.open ? 'op' : ''}" aria-expanded="${!!node.open}" data-rid="${esc(rid)}">
      <span class="r1"><span class="n"><span class="c">${node.loading ? '…' : node.open ? '▾' : '▸'}</span><span class="rk">${n + 1}</span>${esc(i.label)}</span><span class="v">${mnum(C)}</span></span>
      <span class="m"><span>${esc(i.sub || '')}</span><span>${qtyOrAmt(C)}</span>${hasCmp ? dTxt(mdelta(i.delta)) : ''}
        <span>gross <b class="${C.margin < 0 ? 'neg' : ''}">${money(C.margin)}</b> · ${pct(C.margin_pct)}</span><span>front ${pct(C.gross_pct)}</span></span>
    </button>${node.open ? skuCard(node) + (sub.length ? `<div class="tw sub">${sub.join('')}</div>` : '') : ''}`;
  }).join('') : `<div class="empty">Savdo yo'q.</div>`;
  // bosishni konteyner ushlaydi (delegatsiya) — qayta chizilganda ham ishonchli ishlaydi
  host.onclick = e => {
    const r = e.target.closest('.sk.can'); if (!r || !host.contains(r)) return;
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
  $('errHost').replaceChildren(box);
}
// Tablar bo'yicha yuklash: Umumiy (KPI, grafik, kategoriyalar) har doim; Qirqim va Tovarlar — faqat ochilganda.
// Filtr/davr o'zgarsa ikkalasi "eskirgan" deb belgilanadi va ochilganda qayta olinadi.
const STALE = {Qirqim: true, Tovarlar: true};
let LIST = null;
async function load() {
  renderHeader();
  STALE.Qirqim = STALE.Tovarlar = true;
  const F = filters(), seq = ++reqSeq.main;
  const secs = ['kpis', 'chart', 'catList'].map($);
  secs.forEach(s => s.classList.add('loading'));
  try {
    // kategoriyalar (yoki bitta kategoriyada — brendlar) ro'yxati: o'z filtrisiz (faceted)
    const listDim = isMulti() ? 'category' : 'brand';
    const LF = {...F, ...(listDim === 'category' ? {category_ids: []} : {brands: []})};
    const [sum, daily, list] = await Promise.all([api('summary', F), api('daily', F),
      api('breakdown', {...LF, rows: listDim, sort: st.metric, limit: 500})]);
    if (seq !== reqSeq.main) return;          // eskirgan javob
    $('errHost').innerHTML = '';
    DAILY = daily; LIST = {dim: listDim, rows: list.rows};
    renderKpis(sum); renderChart(); renderCatList();
    if (TREE && st.mode === 'tree' && !STALE.Qirqim) renderTree();   // Jami kartasidagi o'zgarish % (SUM)
    renderHeader();
  } catch (e) {
    if (seq === reqSeq.main) showError(e);
  } finally {
    if (seq === reqSeq.main) secs.forEach(s => s.classList.remove('loading'));
  }
  if (seq === reqSeq.main && STALE[TAB]) loadTab(TAB);
}
async function loadTab(t) {
  if (!STALE[t]) return;
  STALE[t] = false;
  const F = filters(), seq = reqSeq.main;
  if (t === 'Qirqim') {
    const host = $('breakdown'); host.classList.add('loading');
    if (!host.firstChild) host.innerHTML = `<div class="empty">Yuklanmoqda…</div>`;
    try {
      if (st.mode === 'pivot') {
        const avail = [...LOC_PATH, ...(cat().extra_dims || []), ...attrDims(), 'brand', 'status', 'sku'];
        if (!avail.includes(st.rows)) st.rows = 'region';
        if (!st.cols || st.cols === st.rows || !avail.includes(st.cols))
          st.cols = attrDims().find(d => d !== st.rows) || (cat().extra_dims || []).find(d => d !== st.rows) || (st.rows === 'brand' ? 'region' : 'brand');
        const bd = await api('breakdown', {...F, rows: st.rows, cols: st.cols, sort: apiSort(), desc: st.sort !== 'name', limit: 500});
        if (seq !== reqSeq.main) return;
        BD = bd;
      } else {
        await buildTree();
        if (seq !== reqSeq.main) return;
      }
      renderBreakdown();
    } catch (e) { if (seq === reqSeq.main) { STALE[t] = true; showError(e); } }
    finally { host.classList.remove('loading'); }
  } else if (t === 'Tovarlar') {
    const secs = ['ablocks', 'top'].map($); secs.forEach(s => s.classList.add('loading'));
    if (!$('ablocks').firstChild) $('ablocks').innerHTML = `<div class="empty">Yuklanmoqda…</div>`;
    try {
      const [attrs, top] = await Promise.all([api('attributes', F), api('breakdown', {...F, rows: 'sku', sort: st.metric, limit: 10})]);
      if (seq !== reqSeq.main) return;
      ATTR = attrs; renderAttrBlocks(); renderTop(top);
    } catch (e) { if (seq === reqSeq.main) { STALE[t] = true; showError(e); } }
    finally { secs.forEach(s => s.classList.remove('loading')); }
  }
}

// ---------------------------------------------------------------- boshqaruv
$('catBtn').onclick = openCatSheet;
$('perBtn').onclick = openPeriodSheet;
$('filBtn').onclick = openFilterSheet;
const reloadQirqim = () => { STALE.Qirqim = true; loadTab('Qirqim'); };
$('rowsDim').onchange = e => { st.rows = e.target.value; if (st.cols === st.rows) st.cols = ''; st.showAll = false; reloadQirqim(); };
$('colsDim').onchange = e => { st.cols = e.target.value === st.rows ? '' : e.target.value; st.showAll = false; reloadQirqim(); };
$('sortBy').onchange = e => { st.sort = e.target.value; resetView(); reloadQirqim(); };
document.querySelectorAll('#brMode button').forEach(b => b.onclick = () => { if (st.mode === b.dataset.m) return; st.mode = b.dataset.m; resetView(); renderBreakdown(); reloadQirqim(); });
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
let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { if (TAB === 'Umumiy') renderChart(); if (st.shareView === 'dyn' && st.mode === 'tree') renderShare(); }, 150); });


// ---------------------------------------------------------------- REJA: yillik reja va bajarilishi
const MONTHS = ['Yanvar', 'Fevral', 'Mart', 'Aprel', 'May', 'Iyun', 'Iyul', 'Avgust', 'Sentabr', 'Oktabr', 'Noyabr', 'Dekabr'];
const MSHORT = ['Yan', 'Fev', 'Mar', 'Apr', 'May', 'Iyn', 'Iyl', 'Avg', 'Sen', 'Okt', 'Noy', 'Dek'];
const PL = {data: null, year: null, mode: 'month', metric: 'amount', month: null, loading: false};
const dim = (y, m) => new Date(Date.UTC(y, m + 1, 0)).getUTCDate();          // m: 0..11
const plCls = p => (p == null ? 'flat' : p >= 100 ? 'up' : p >= 95 ? 'warn' : 'down');
const plCol = p => (p == null ? 'var(--so)' : p >= 100 ? 'var(--good)' : p >= 95 ? 'var(--s4)' : 'var(--bad)');
const plPill = p => `<span class="pill ${plCls(p)}">${p == null ? '—' : pct(p)}</span>`;
const sum = a => a.reduce((s, v) => s + (v || 0), 0);
// o'tgan kunlar: oyning nechta kuni uchun fakt bor — [data_from, data_to] oralig'ida
// (bazada ma'lumot yil o'rtasidan boshlansa, undan oldingi oylar rejasi hisobga olinmaydi)
function elapsed(y, m) {
  const to = PL.data.data_to, fr = PL.data.data_from; if (!to) return 0;
  const a = `${y}-${String(m + 1).padStart(2, '0')}-01`, b = `${y}-${String(m + 1).padStart(2, '0')}-${String(dim(y, m)).padStart(2, '0')}`;
  const lo = fr && fr > a ? fr : a, hi = to < b ? to : b;
  return hi < lo ? 0 : diffDays(lo, hi) + 1;
}
// bitta qator (kategoriya / guruh / jami) uchun tanlangan rejimdagi hisob
function plCalc(c) {
  const y = PL.year, pk = PL.metric === 'gross' ? 'plan_gross' : 'plan_amount', fk = PL.metric === 'gross' ? 'fact_gross' : 'fact_amount';
  const P = c[pk], F = c[fk];
  const hasPlan = P.some(v => v != null);
  if (PL.mode === 'month') {
    const m = PL.month, e = elapsed(y, m), D = dim(y, m), full = P[m];
    const per = full != null ? full * e / D : null, fact = F[m] || 0;
    return {hasPlan: full != null, full, per, fact, pct: per ? fact / per * 100 : null, fullPct: full ? fact / full * 100 : null,
            expPct: e / D * 100, forecast: e ? fact / e * D : null, need: e < D && full != null ? Math.max(0, full - fact) / (D - e) : null,
            nowDaily: e ? fact / e : null, factAmt: c.fact_amount[m] || 0, planAmt: c.plan_amount[m],
            factG: c.fact_gross[m] || 0, planG: c.plan_gross[m]};
  }
  let per = 0, fact = 0, elD = 0, perA = 0, perG = 0, fA = 0, fG = 0;
  for (let m = 0; m < 12; m++) {
    const e = elapsed(y, m), D = dim(y, m);
    if (!e) continue;
    per += (P[m] || 0) * e / D; fact += F[m] || 0; elD += e;
    perA += (c.plan_amount[m] || 0) * e / D; perG += (c.plan_gross[m] || 0) * e / D;
    fA += c.fact_amount[m] || 0; fG += c.fact_gross[m] || 0;
  }
  const full = hasPlan ? sum(P) : null, yd = (y % 4 === 0 ? 366 : 365);
  const base = {hasPlan, full, per: hasPlan ? per : null, fact, factAmt: fA, planAmt: perA, factG: fG, planG: perG,
                forecast: elD ? fact / elD * yd : null, expPct: full ? per / full * 100 : null, fullPct: full ? fact / full * 100 : null};
  base.pct = PL.mode === 'year' ? base.fullPct : (base.per ? fact / base.per * 100 : null);
  return base;
}
function plSumRows(rows, name) {
  const z = () => Array(12).fill(null);
  const o = {name, plan_amount: z(), plan_gross: z(), fact_amount: Array(12).fill(0), fact_gross: Array(12).fill(0)};
  for (const c of rows) for (let m = 0; m < 12; m++) {
    for (const k of ['plan_amount', 'plan_gross']) if (c[k][m] != null) o[k][m] = (o[k][m] || 0) + c[k][m];
    o.fact_amount[m] += c.fact_amount[m] || 0; o.fact_gross[m] += c.fact_gross[m] || 0;
  }
  return o;
}
function ring(p, size = 84, sub = '') {
  const r = size / 2 - 7, C = 2 * Math.PI * r, d = C * Math.min(p || 0, 100) / 100, c = size / 2;
  return `<svg class="ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="Bajarilish ${pct(p)}">
    <circle cx="${c}" cy="${c}" r="${r}" stroke="var(--card-2)" stroke-width="9" fill="none"/>
    <circle cx="${c}" cy="${c}" r="${r}" stroke="${plCol(p)}" stroke-width="9" fill="none" stroke-linecap="round" stroke-dasharray="${d.toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 ${c} ${c})"/>
    <text class="rt" x="${c}" y="${c + (sub ? 2 : 5)}" text-anchor="middle" style="font-size:${(size * (p != null && p >= 100 ? .15 : .165)).toFixed(1)}px">${p == null ? '—' : nf1.format(p) + '%'}</text>
    ${sub ? `<text class="rs" x="${c}" y="${c + 15}" text-anchor="middle">${sub}</text>` : ''}</svg>`;
}
function periodLabel() {
  const y = PL.year;
  if (PL.mode === 'month') return `${MONTHS[PL.month]} ${y}`;
  const to = PL.data.data_to;
  const last = to && +to.slice(0, 4) === y ? dm(to) : '31.12';
  return PL.mode === 'ytd' ? `01.01–${last} ${y}` : `${y} yil`;
}
function plChart(total) {
  const y = PL.year, pk = PL.metric === 'gross' ? 'plan_gross' : 'plan_amount', fk = PL.metric === 'gross' ? 'fact_gross' : 'fact_amount';
  const P = total[pk], F = total[fk];
  const W = 360, H = 170, L = 30, B = 22, T = 8;
  const max = Math.max(...P.map(v => v || 0), ...F, 1);
  const step = niceStep(max / 3), top = Math.ceil(max / step) * step;
  const yy = v => T + (H - T - B) * (1 - v / top), bw = (W - L) / 12;
  let g = '';
  for (let v = 0; v <= top + 1; v += step) g += `<line x1="${L}" x2="${W}" y1="${yy(v)}" y2="${yy(v)}" stroke="var(--line)" ${v ? 'stroke-dasharray="2 3"' : ''}/><text class="ax" x="${L - 4}" y="${yy(v) + 3}" text-anchor="end">${nf0.format(v / 1e9)}</text>`;
  for (let m = 0; m < 12; m++) {
    const x = L + m * bw, e = elapsed(y, m), D = dim(y, m);
    if (P[m]) g += `<rect x="${x + 3}" y="${yy(P[m])}" width="${bw - 6}" height="${yy(0) - yy(P[m])}" rx="3" fill="var(--card-2)" stroke="var(--line)"/>`;
    if (F[m]) { const pp = P[m] ? F[m] / (P[m] * e / D) * 100 : null;
      g += `<rect x="${x + 7}" y="${yy(F[m])}" width="${bw - 14}" height="${yy(0) - yy(F[m])}" rx="2" fill="${P[m] ? plCol(pp) : 'var(--accent)'}"><title>${MONTHS[m]}: fakt ${money(F[m])}${P[m] ? ` · reja ${money(P[m])} · ${pct(pp)}` : ''}</title></rect>`; }
    g += `<text class="ax" x="${x + bw / 2}" y="${H - 7}" text-anchor="middle" ${PL.mode === 'month' && m === PL.month ? 'style="font-weight:700;fill:var(--ink)"' : ''}>${MSHORT[m]}</text>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Oylar bo'yicha reja va fakt">${g}</svg>`;
}
function renderPlanCtl() {
  const d = PL.data;
  const yrs = d.years.length > 1 ? `<select id="plYear" aria-label="Yil">${d.years.map(y => `<option ${y === PL.year ? 'selected' : ''}>${y}</option>`).join('')}</select>` : `<span class="tchip">${PL.year}</span>`;
  const mon = PL.mode === 'month' ? `<select id="plMon" aria-label="Oy">${MONTHS.map((n, i) => `<option value="${i}" ${i === PL.month ? 'selected' : ''}>${n}</option>`).join('')}</select>` : '';
  $('plCtl').innerHTML = `${yrs}<span class="mseg" id="plMode"><button data-v="month" aria-pressed="${PL.mode === 'month'}">Oy</button><button data-v="ytd" aria-pressed="${PL.mode === 'ytd'}">Yil boshidan</button><button data-v="year" aria-pressed="${PL.mode === 'year'}">Yil</button></span>${mon}
    <span class="mseg" id="plMet"><button data-v="amount" aria-pressed="${PL.metric === 'amount'}">Savdo</button><button data-v="gross" aria-pressed="${PL.metric === 'gross'}">Gross</button></span>`;
  const y = $('plYear'); if (y) y.onchange = () => loadPlan(+y.value);
  const m = $('plMon'); if (m) m.onchange = () => { PL.month = +m.value; renderPlan(); };
  $('plMode').querySelectorAll('button').forEach(b => b.onclick = () => { PL.mode = b.dataset.v; renderPlan(); });
  $('plMet').querySelectorAll('button').forEach(b => b.onclick = () => { PL.metric = b.dataset.v; renderPlan(); });
}
function renderPlan() {
  const host = $('plBody'), d = PL.data;
  if (!d) return;
  if (!d.cats.length) {
    $('plCtl').innerHTML = '';
    host.innerHTML = `<div class="card"><div class="empty">Reja hali yuklanmagan.<br>${META.can_upload ? "«📥» bo'limidan reja faylini yuklang: Гурух | Категория | январь … декабрь | Жами, sarlavha ustida yil. Gross marja rejasi — shu formatda «Маржа» nomli ikkinchi varaqda." : 'Administrator reja faylini yuklashini kuting.'}</div></div>`;
    return;
  }
  renderPlanCtl();
  const isG = PL.metric === 'gross';
  if (isG && !d.cats.some(c => c.plan_gross.some(v => v != null))) {
    host.innerHTML = `<div class="card"><div class="empty">Gross marja rejasi yuklanmagan — reja faylida «Маржа» nomli varaq qo'shing (format xuddi savdo varag'idek).</div></div>`;
    return;
  }
  // spravochnikda yo'q kategoriyalarning fakti yo'q — jami va guruh bajarilishiga qo'shilmaydi, alohida ko'rsatiladi
  const live = d.cats.filter(c => c.in_db), orphan = d.cats.filter(c => !c.in_db);
  if (!live.length) { host.innerHTML = `<div class="card"><div class="empty">Rejadagi kategoriyalar tovar spravochnigida hali yo'q — fakt chiqishi uchun spravochnikni yuklang.</div></div>`; return; }
  const groups = {};
  live.forEach(c => (groups[c.grp] = groups[c.grp] || []).push(c));
  const gnames = Object.keys(groups).sort();
  const total = plSumRows(live, 'Jami'), T = plCalc(total);
  const mode = PL.mode, perWord = mode === 'year' ? 'yillik reja' : 'davr rejasi';
  const fullWord = mode === 'month' ? 'oylik' : 'yillik';
  const grossLine = c => {
    const fp = c.factAmt ? c.factG / c.factAmt * 100 : null, pp = c.planAmt && c.planG ? c.planG / c.planAmt * 100 : null;
    return fp == null && pp == null ? '' : `gross ${pct(fp)}${pp != null ? ` · reja ${pct(pp)}` : ''}`;
  };
  const head = `<div class="card"><div class="sh"><span class="label">${esc(periodLabel())} · ${gnames.join(' + ')} · ${isG ? 'gross marja' : 'savdo'}</span>
      ${T.expPct != null && mode !== 'ytd' ? `<span class="pill warn">kutilgan ${pct(T.expPct)}</span>` : ''}</div>
    <div class="plh">${ring(T.pct, 88, mode === 'year' ? 'yillikdan' : 'bajarildi')}
      <div class="tx"><span class="v">${money(T.fact)}</span>
        <span class="p">${mode === 'year' ? `yillik reja <b>${money(T.full)}</b> · davrgacha ${money(T.per)}` : `${perWord} <b>${money(T.per)}</b> · ${fullWord} ${money(T.full)}`}</span>
        ${T.forecast != null ? `<span class="p">prognoz <b>${money(T.forecast)}</b>${T.full ? ` (${pct(T.forecast / T.full * 100)})` : ''}</span>` : ''}
        ${mode === 'month' && T.need != null ? `<span class="p">kunlik kerak <b>${money(T.need)}</b> · hozir ${money(T.nowDaily)}</span>` : ''}
        ${mode !== 'month' && mode !== 'year' && T.full ? `<span class="p">yillik rejaning <b>${pct(T.fullPct)}</b> · kutilgan ${pct(T.expPct)}</span>` : ''}
        ${isG ? `<span class="p">${grossLine(T)}</span>` : ''}
        ${mode !== 'month' && d.data_from && d.data_from > `${PL.year}-01-01` && d.data_from.slice(0, 4) == PL.year ? `<span class="hint">⚠ bazada savdo ${dm(d.data_from)} dan bor — davr rejasi shu kundan hisoblandi</span>` : ''}</div></div></div>`;
  const gcards = `<div class="plgs">${gnames.map(g => { const c = plCalc(plSumRows(groups[g], g));
    return `<div class="plg ${gnames.length === 1 ? 'solo' : ''}">${ring(c.pct, 80)}<b>${esc(g)}</b>
      <span class="p">${money(c.fact)} / ${money(mode === 'year' ? c.full : c.per)}</span>
      <span class="p">${isG ? grossLine(c) : `gross ${pct(c.factAmt ? c.factG / c.factAmt * 100 : null)}${c.planAmt && c.planG ? ` · reja ${pct(c.planG / c.planAmt * 100)}` : ''}`}</span></div>`; }).join('')}</div>`;
  const chart = `<div class="card plc"><div class="sh"><span class="label">Oylar bo'yicha · mlrd so'm</span><span class="hint">kulrang — reja</span></div>${plChart(total)}
    <div class="legend"><span><i style="background:var(--good)"></i>≥100%</span><span><i style="background:var(--s4)"></i>95–100%</span><span><i style="background:var(--bad)"></i>&lt;95%</span></div></div>`;
  const col3 = mode === 'year' ? 'Yillik reja' : 'Davr reja';
  const bn = v => (v == null ? '—' : nf2.format(v / 1e9));      // jadvalda — mlrd so'm
  const row = (c, cls, nameHtml) => `${nameHtml}<span class="${cls}">${c.hasPlan ? bn(mode === 'year' ? c.full : c.per) : '—'}</span><span class="${cls}"><b>${bn(c.fact)}</b>${isG && c.factAmt ? `<span class="s2">${pct(c.factG / c.factAmt * 100)}</span>` : ''}</span><span class="${cls}">${c.hasPlan ? plPill(c.pct) : '—'}</span>`;
  let rows = '';
  for (const g of gnames) {
    rows += row(plCalc(plSumRows(groups[g], g)), 'g', `<span class="n g">${esc(g)}</span>`);
    for (const c of groups[g])
      rows += row(plCalc(c), '', `<button class="n" data-cid="${c.id}" title="Kategoriya ko'rsatkichlariga o'tish">${esc(c.name)}</button>`);
  }
  rows += row(T, 'g', `<span class="n g">Jami</span>`);
  const table = `<div class="card"><div class="sh"><span class="label">Kategoriyalar · ${isG ? 'gross marja' : 'savdo'}</span><span class="hint">bosilsa — kategoriyaga o'tadi</span></div>
    <div class="plt"><span class="n h">Kategoriya</span><span class="h">${col3}</span><span class="h">Fakt</span><span class="h">%</span>${rows}</div>
    <span class="hint">summalar — mlrd so'm</span>
    ${orphan.length ? `<span class="hint">⚠ Rejada bor, tovar spravochnigida yo'q (fakt yo'q, jamiga qo'shilmadi): ${orphan.map(c => `${esc(c.name)} (${esc(c.grp)}, ${PL.mode === 'month' ? MONTHS[PL.month].toLowerCase() : 'yillik'} reja ${money(PL.mode === 'month' ? c[isG ? 'plan_gross' : 'plan_amount'][PL.month] : sum(c[isG ? 'plan_gross' : 'plan_amount']))})`).join(', ')}</span>` : ''}</div>`;
  const note = `<p class="foot">Davr rejasi = oylik reja × o'tgan kunlar / oy kunlari (ma'lumot ${dm(d.data_to)} gacha). Bajarilish = fakt / davr rejasi.
    Prognoz — hozirgi o'rtacha kunlik sur'at bilan. Reja oy × kategoriya bo'yicha, shuning uchun hudud/brend filtrlari bu yerda qo'llanmaydi.</p>`;
  host.innerHTML = head + gcards + chart + table + note;
  host.querySelectorAll('button[data-cid]').forEach(b => b.onclick = () => {
    const c = META.categories.find(x => String(x.id) === b.dataset.cid); if (!c) return;
    st.cat = c.key; st.f = {}; st.aExpand = {}; st.aOpen = null; st.rows = 'region'; st.cols = '';
    resetView(); load(); setTab('Umumiy');
  });
}
async function loadPlan(year) {
  PL.loading = true;
  $('plBody').classList.add('loading');
  try {
    const d = await api('plan' + (year ? `?year=${year}` : ''));
    PL.data = d; PL.year = d.year;
    if (PL.month == null || year) {
      const to = d.data_to;
      PL.month = to && +to.slice(0, 4) === d.year ? +to.slice(5, 7) - 1 : 11;
    }
    renderPlan();
  } catch (e) { $('plBody').innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
  finally { PL.loading = false; $('plBody').classList.remove('loading'); }
}

// ---------------------------------------------------------------- tablar: Umumiy / Qirqim / Tovarlar
let TAB = 'Umumiy';
function setTab(t) {
  TAB = t;
  ['Umumiy', 'Qirqim', 'Tovarlar', 'Reja'].forEach(x => { $('pane' + x).hidden = x !== t; });
  const rj = t === 'Reja';                            // reja — kategoriya/davr/filtrga bog'liq emas
  $('catBtn').hidden = rj; $('topAnalytics').hidden = rj; $('upTitle').hidden = !rj;
  if (rj) { $('upTitle').textContent = 'Reja bajarilishi'; $('tmeta').textContent = META.data_to ? `ma'lumot ${dm(META.data_to)} gacha` : ''; }
  else updMeta();
  if (rj && !PL.data && !PL.loading) loadPlan();
  document.querySelectorAll('#tabbar button').forEach(b => b.setAttribute('aria-pressed', b.dataset.t === t));
  window.scrollTo(0, 0);
  if (t === 'Umumiy') renderChart();                 // yashirin paytda kenglik 0 bo'ladi — qayta chizamiz
  if (STALE[t]) loadTab(t);
  if (t === 'Qirqim' && st.mode === 'tree' && st.shareView === 'dyn') renderShare();
  try { tg && tg.HapticFeedback && tg.HapticFeedback.selectionChanged(); } catch (e) {}
}
document.querySelectorAll('#tabbar button').forEach(b => b.onclick = () => { if (TAB !== b.dataset.t) setTab(b.dataset.t); });

// ---------------------------------------------------------------- bo'limlar: Tahlil / Yuklash
let VIEW = 'analytics';
let NEED_REFRESH = false;   // yangi fayl yuklangach tahlil qayta o'qiladi
function setView(v) {
  VIEW = v;
  $('upBtn').textContent = v === 'upload' ? '📊' : '📥';
  $('upBtn').title = v === 'upload' ? 'Tahlilga qaytish' : 'Fayl yuklash';
  $('topAnalytics').hidden = v !== 'analytics';
  $('catBtn').hidden = v !== 'analytics';
  $('upTitle').hidden = v === 'analytics';
  $('upTitle').textContent = 'Fayl yuklash';
  updMeta();
  $('viewAnalytics').hidden = v !== 'analytics';
  $('viewUpload').hidden = v !== 'upload';
  $('tabbar').hidden = v !== 'analytics';
  if (v === 'upload') loadHistory();
  if (v === 'analytics' && NEED_REFRESH) { NEED_REFRESH = false; PL.data = null; refreshMeta(); }
  if (v === 'analytics' && TAB === 'Reja') setTab('Reja');
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
  const xl = j.missing ? `<button class="btn xl" id="upMissing">📎 Spravochnikda yo'qlar — Excel'ni botga yuborish</button>` : '';
  if (state === 'checked') body = reportHtml(j.report) + xl +
    `<div class="upbtns"><button class="btn" id="upCancel">Bekor qilish</button><button class="btn primary" id="upConfirm">Tasdiqlash va yuklash</button></div>`;
  else if (state === 'loading') body = `<div class="pbar indet"><span></span></div><div class="hint">Bazaga yozilmoqda…</div>`;
  else if (state === 'done') body = reportHtml(j.report) + `<div class="hint num">${j.elapsed ?? ''} s · hisobot botga ham yuborildi${j.missing ? " · spravochnikda yo'qlar Excel'i ham botda" : ''}</div>${xl}
    <div class="upbtns"><button class="btn" id="upAgain">Yana fayl yuklash</button><button class="btn primary" id="upGo">Tahlilga o'tish</button></div>`;
  else if (state === 'failed') body = `<div class="errbox">${esc(j.error || extra?.error || 'Xato')}</div>
    <div class="upbtns"><button class="btn primary" id="upAgain">Boshqa fayl tanlash</button></div>`;
  host.innerHTML = `<div class="upcard">${head}${stepsHtml(state === 'failed' ? (j.status_before || 'checking') : state, state === 'failed')}${body}</div>`;
  const c = $('upConfirm'); if (c) c.onclick = confirmUpload;
  const x = $('upCancel'); if (x) x.onclick = cancelUpload;
  const a = $('upAgain'); if (a) a.onclick = () => { UP = null; renderUp(null); $('drop').hidden = false; };
  const g = $('upGo'); if (g) g.onclick = () => setView('analytics');
  const xb = $('upMissing'); if (xb) xb.onclick = () => sendMissing(xb, `upload/${j.id}/missing`);
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
async function sendMissing(btn, path) {
  const t = btn.textContent; btn.disabled = true; btn.textContent = 'Yuborilmoqda…';
  try { await api(path, {}); btn.textContent = '✅ Botga yuborildi — chatni oching'; try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred('success'); } catch (e) {} }
  catch (e) { btn.textContent = '❌ ' + e.message; btn.disabled = false; setTimeout(() => { btn.textContent = t; }, 4000); }
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
      ${u.missing ? `<span class="xlbtn" role="button" tabindex="0" data-u="${u.id}">📎 Spravochnikda yo'q: ${u.missing} ta — Excel'ni botga yuborish</span>` : ''}
      ${u.text || u.error ? `<span class="report" hidden>${u.text ? String(u.text).replace(/\n/g, '<br>') : esc(u.error)}</span>` : ''}
    </button>`).join('') : `<div class="empty">Hali yuklash bo'lmagan.</div>`;
  host.querySelectorAll('.hrow').forEach(b => b.onclick = () => { const r = b.querySelector('.report'); if (r) r.hidden = !r.hidden; });
  host.querySelectorAll('.xlbtn').forEach(x => x.onclick = e => { e.stopPropagation(); sendMissing(x, `uploads/${x.dataset.u}/missing`); });
}
async function refreshMeta() {
  try { META = await api('meta'); updMeta(); } catch (e) {}
  if ((!st.cat || !allScopes().some(c => c.key === st.cat)) && META.categories.length) st.cat = defaultScope();
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
  updMeta();
  if (META.fx_rate) $('fxInfo').textContent = ` Dollar kursi: ${nf0.format(META.fx_rate)} so'm (tannarx = soni × kirim narxi × kurs).`;
  st.cat = defaultScope();
  [st.from, st.to] = presetRange(st.preset);
  load();
}
init();
})();
