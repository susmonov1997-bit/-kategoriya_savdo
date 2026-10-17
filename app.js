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
  topN: 10, topSort: 'm',   // TOP SKU: 10 | 20 | 50; saralash: m (dona/summa — tanlangan ko'rsatkich) | amount | margin | delta
};
const NAV = [];          // kategoriyaga kirish tarixi: «‹ Orqaga» uchun {cat, f, y}
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
function compShare(curK, prevK, comp, baseCur, basePrev) {
  // Ulush = bizning savdo / set jami («К»siz) — tepadagi filtr davri bo'yicha, ikkala tomon bir xil kunlar.
  // Set jami (company_daily) yo'q kunlarda jami = bizning savdo deb olinadi (jami bizning savdodan kam bo'lmaydi),
  // ya'ni maxraj = kompaniya jami (bor kunlar) + bizning savdo (kompaniya jami yo'q kunlar).
  comp = comp || {};
  const key = MQ() ? 'qty' : 'amount';
  const unc = (k, f) => Math.max(0, (k[f] || 0) - (k['cov_' + f] != null ? k['cov_' + f] : (k[f] || 0)));
  // baseCur/basePrev — maxrajdagi "set jami yo'q kunlar" uchun bizning JAMI savdo (kategoriya/brend qatorida
  // o'zining savdosi emas, barcha kategoriyalarimiz savdosi olinadi); berilmasa — qatorning o'zi
  const sh = (k, f, cv, b) => { if (!k) return null; const den = (cv || 0) + unc(b || k, f); return den > 0 ? (k[f] || 0) / den * 100 : null; };
  const s = sh(curK, key, comp['c_' + key], baseCur), ps = sh(prevK, key, comp['p_' + key], basePrev);
  const g = sh(curK, 'gross', comp.c_gross, baseCur), pg = sh(prevK, 'gross', comp.p_gross, basePrev);
  return {s, pp: s != null && ps != null ? s - ps : null, g, gpp: g != null && pg != null ? g - pg : null,
          den: (comp['c_' + key] || 0) + unc(baseCur || curK, key)};
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
      NAV.length = 0; st.cat = k; st.f = {}; st.aExpand = {}; st.aOpen = null; st.rows = 'region'; st.cols = ''; resetView(); load(); syncBack();
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
const G_OPEN = new Set();          // ochiq guruhlar (KBT/MBT) — seans davomida saqlanadi
const KPI_KEYS = ['amount', 'qty', 'bonus_qty', 'gross', 'income', 'margin', 'cov_amount', 'cov_qty', 'cov_gross'];
function sumK(list) {
  const o = Object.fromEntries(KPI_KEYS.map(k => [k, 0]));
  list.forEach(k => KPI_KEYS.forEach(f => { o[f] += (k && k[f]) || 0; }));
  o.margin_pct = o.amount ? o.margin / o.amount * 100 : null;
  o.gross_pct = o.amount ? o.gross / o.amount * 100 : null;
  return o;
}
const rel = (a, b) => (b ? (a - b) / Math.abs(b) * 100 : null);
function listCard(i, o) {
  // o: {cls, ctl, grpShare, grpPP, sel}
  const C = i.current, hasCmp = !!cmpRange();
  const cs = SUM ? compShare(C, i.previous, SUM.company, SUM.current, SUM.previous) : null;
  return `<button class="li can ${o.cls || ''}" ${o.attr || ''}>
    <span class="r1"><span class="n">${o.ctl || ''}${esc(i.label)}${o.tag ? `<span class="tag">${o.tag}</span>` : ''}${o.sel ? ' ✓' : ''}</span><span class="v">${mnum(C)}</span></span>
    ${o.bar != null ? `<span class="bar"><i style="width:${o.bar}%"></i></span>` : ''}
    <span class="m">${o.grpShare != null ? `<span>guruhda <b>${pct(o.grpShare)}</b>${hasCmp ? ' ' + ppTxt(o.grpPP) : ''}</span>` : ''}
      <span>${o.grpShare != null ? 'jamida' : 'ulush'} <b>${pct(i.share)}</b>${hasCmp ? ' ' + ppTxt(i.share_pp) : ''}</span>${hasCmp ? dTxt(mdelta(i.delta)) : ''}
      ${cs && cs.s != null ? `<span class="setsh">set jamida <b>${pct(cs.s)}</b>${hasCmp && cs.pp != null ? ' ' + ppPlain(cs.pp) : ''}</span>` : ''}</span>
    <span class="m"><span>gross <b class="${C.margin < 0 ? 'neg' : ''}">${money(C.margin)}</b> · ${pct(C.margin_pct)}</span>
      <span>front <b class="${C.gross < 0 ? 'neg' : ''}">${money(C.gross)}</b> · ${pct(C.gross_pct)}</span><span>${qtyOrAmt(C)}</span></span>${o.stock || ''}</button>`;
}
function goCategory(c) {
  NAV.push({cat: st.cat, f: Object.fromEntries(Object.entries(st.f).map(([d, v]) => [d, new Set(v)])), y: window.scrollY});
  st.cat = c.key; st.f = {}; st.aExpand = {}; st.aOpen = null; st.rows = 'region'; st.cols = ''; LIST_ALL = false;
  resetView(); load(); window.scrollTo(0, 0); syncBack();
}
function renderCatList() {
  if (!LIST) return;
  const multi = isMulti(), host = $('catList');
  const items = LIST.rows.slice().sort((x, y) => mval(y.current) - mval(x.current));
  $('listH').textContent = multi ? 'Kategoriyalar' : 'Brendlar';
  if (!items.length) { host.innerHTML = `<div class="empty">Savdo yo'q</div>`; $('listHint').textContent = ''; return; }
  items.forEach(i => remember(LIST.dim, i.key, i.label));
  const hasCmp = !!cmpRange();
  if (!multi) {                                     // bitta kategoriya — brendlar (bosilsa filtr)
    const sel = st.f.brand || new Set();
    $('listHint').textContent = "bosing — filtrga qo'shiladi";
    const shown = LIST_ALL ? items : items.slice(0, 8);
    const max = Math.max(...items.map(i => i.share || 0), 1);
    host.innerHTML = shown.map(i => listCard(i, {cls: sel.has(i.key) ? 'op' : '', attr: `data-k="${esc(i.key)}"`, sel: sel.has(i.key),
                                                 bar: (i.share || 0) / max * 100, stock: stockLine(STK.list && STK.list[i.key])})).join('')
      + (items.length > 8 ? `<button class="more" id="listMore">${LIST_ALL ? 'Qisqartirish' : `Hammasi (${items.length} ta)`}</button>` : '');
    host.querySelectorAll('.li[data-k]').forEach(el => el.onclick = () => {
      const k = el.dataset.k, set = new Set(st.f.brand || []); set.has(k) ? set.delete(k) : set.add(k);
      if (set.size) st.f.brand = set; else delete st.f.brand;
      resetView(); load();
    });
    const m = $('listMore'); if (m) m.onclick = () => { LIST_ALL = !LIST_ALL; renderCatList(); };
    return;
  }
  // bir nechta kategoriya — KBT / MBT guruhlariga yig'ilgan
  const grpOf = k => { const c = META.categories.find(c => String(c.id) === String(k)); return (c && c.group) || 'Boshqa'; };
  const groups = {};
  items.forEach(i => (groups[grpOf(i.key)] = groups[grpOf(i.key)] || []).push(i));
  const names = Object.keys(groups).sort((a, b) => sumK(groups[b].map(i => i.current)).amount - sumK(groups[a].map(i => i.current)).amount);
  if (names.length === 1) G_OPEN.add(names[0]);
  const totCur = mval(sumK(items.map(i => i.current))), totPrev = hasCmp ? mval(sumK(items.map(i => i.previous))) : 0;
  $('listHint').textContent = 'guruhni bosing — ochiladi';
  const out = [];
  for (const g of names) {
    const rows = groups[g], cur = sumK(rows.map(i => i.current)), prev = hasCmp ? sumK(rows.map(i => i.previous)) : null;
    const gs = totCur ? mval(cur) / totCur * 100 : null, gps = hasCmp && totPrev ? mval(prev) / totPrev * 100 : null;
    const gi = {label: g, current: cur, previous: prev, share: gs, share_pp: gs != null && gps != null ? gs - gps : null,
                delta: hasCmp ? {amount: rel(cur.amount, prev.amount), qty: rel(cur.qty, prev.qty)} : {}};
    const op = G_OPEN.has(g);
    out.push(listCard(gi, {cls: `grp ${op ? 'op' : ''}`, attr: `data-g="${esc(g)}" aria-expanded="${op}"`,
                           ctl: `<span class="c">${op ? '▾' : '▸'}</span>`, tag: `${rows.length} kategoriya`, bar: gs || 0}));
    if (!op) continue;
    const gCur = mval(cur), gPrev = prev ? mval(prev) : 0, gmax = Math.max(...rows.map(i => mval(i.current)), 1);
    for (const i of rows) {
      const s1 = gCur ? mval(i.current) / gCur * 100 : null, s0 = hasCmp && gPrev && i.previous ? mval(i.previous) / gPrev * 100 : null;
      out.push(listCard(i, {cls: 'sub', attr: `data-k="${esc(i.key)}"`, grpShare: s1, grpPP: s1 != null && s0 != null ? s1 - s0 : null,
                            bar: mval(i.current) / gmax * 100, stock: stockLine(STK.list && STK.list[i.key])}));
    }
  }
  host.innerHTML = out.join('');
  host.querySelectorAll('.li[data-g]').forEach(el => el.onclick = () => {
    const g = el.dataset.g; G_OPEN.has(g) ? G_OPEN.delete(g) : G_OPEN.add(g); renderCatList(); });
  host.querySelectorAll('.li[data-k]').forEach(el => el.onclick = () => {
    const c = META.categories.find(c => String(c.id) === String(el.dataset.k)); if (c) goCategory(c); });
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
  stockForNode(node);                        // «📦 qoldiq» qatori — fonda keladi
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
  { const o = $('sortBy').querySelector('option[value="amount"]'); if (o) o.textContent = MQ() ? 'dona' : 'summa';
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
  $('brHint').textContent = '';
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
    ${stockLine(node.stock, node.dim === 'sku')}
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
      ${stockLine(TREE.stock)}
      <span class="ft">${TREE.open ? '▾' : '▸'} ${esc(dimName(TREE.childDim))} bo'yicha ${TREE.open ? 'yopish' : 'ochish'}${MQ() ? '' : ` · ${nf0.format(T.qty)} dona`}</span></button>`];
  subtreeRows(TREE, out, rootAmt, 1);
  host.innerHTML = `<div class="tw">${out.join('')}</div>`;
  wireTree(host);
}
// --- ulush kartasi: FOCUS tugunining ichki tarkibi (Tuzilma | O'zgarish)
function renderShare() {
  const host = $('shareHost');
  const node = FOCUS && FOCUS.open && FOCUS.children ? FOCUS : TREE;
  if (!node || !node.children || !node.children.length) { host.innerHTML = ''; return; }
  const hasCmp = !!cmpRange();
  if (!hasCmp) st.shareView = 'struct';
  const ctx = node.path.length ? node.path.map(p => labels[p.dim + '|' + p.key] || p.key).join(' › ') : 'Jami';
  host.innerHTML = `<div class="card sharecard">
    <div class="sh"><h2>${esc(dimName(node.childDim))} ulushi</h2>
      ${hasCmp ? `<div class="seg mini" id="shareView">
        <button data-v="struct" aria-pressed="${st.shareView === 'struct'}">Tuzilma</button>
        <button data-v="chg" aria-pressed="${st.shareView === 'chg'}">O'zgarish</button>
      </div>` : ''}</div>
    <div class="hint">${esc(ctx)} · ${MQ() ? 'dona' : 'summa'}${hasCmp ? ' · pp — o\'tgan davrga nisbatan' : ''}</div>
    <div id="shareBody"></div>
  </div>`;
  const can = !!childDimFor([...node.path, {dim: node.childDim, key: ''}]);
  const paint = () => {
    $('shareBody').innerHTML = st.shareView === 'chg' ? shareChange(node, can) : shareStruct(node);
    host.querySelectorAll('.srow.drill, .crow.drill').forEach(x => x.onclick = () => {
      const ch = node.children.find(c => String(c.key) === x.dataset.k); if (ch) toggleNode(ch); });
  };
  paint();
  host.querySelectorAll('#shareView button').forEach(b => b.onclick = () => {
    if (st.shareView === b.dataset.v) return;
    st.shareView = b.dataset.v;
    host.querySelectorAll('#shareView button').forEach(x => x.setAttribute('aria-pressed', x === b));
    paint();
  });
}
function shareChange(node, can) {
  const items = node.raw.filter(i => i.share_pp != null && Math.abs(i.share_pp) >= 0.05);
  const up = items.filter(i => i.share_pp > 0).sort((a, b) => b.share_pp - a.share_pp).slice(0, 5);
  const dn = items.filter(i => i.share_pp < 0).sort((a, b) => a.share_pp - b.share_pp).slice(0, 5);
  if (!up.length && !dn.length) return `<div class="empty">Ulushlarda sezilarli o'zgarish yo'q.</div>`;
  const max = Math.max(...[...up, ...dn].map(i => Math.abs(i.share_pp)), 0.1);
  const row = (i, cls) => `<button class="crow ${can ? 'drill' : ''}" data-k="${esc(i.key)}" ${can ? '' : 'disabled'}>
      <span class="sl" title="${esc(i.label)}">${esc(i.label)}</span>
      <span class="ctrk"><span class="cf ${cls}" style="width:${Math.abs(i.share_pp) / max * 100}%"></span></span>
      <span class="cpp">${pill(i.share_pp, true)}</span>
      <span class="cs num">${pct(i.share)}</span></button>`;
  const more = node.total_rows > node.raw.length ? `<div class="hint">Birinchi ${node.raw.length} ta ${esc(dimName(node.childDim)).toLowerCase()} orasidan</div>` : '';
  return `${up.length ? `<div class="chd up">▲ Ulushi o'sganlar</div>${up.map(i => row(i, 'up')).join('')}` : ''}
    ${dn.length ? `<div class="chd dn">▼ Ulushi tushganlar</div>${dn.map(i => row(i, 'dn')).join('')}` : ''}${more}`;
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
      <button data-v="amount" aria-pressed="${st.pvMode === 'amount'}">${MQ() ? 'Dona' : 'Summa, mln'}</button>
      <button data-v="row" aria-pressed="${st.pvMode === 'row'}">Qator ichida %</button>
      <button data-v="col" aria-pressed="${st.pvMode === 'col'}">Ustun ichida %</button></div>
    <div class="pivotwrap"><table class="pv num">
    <thead><tr><th>${esc(dimName(st.rows))} \\ ${esc(dimName(st.cols))}</th>${cols.map(c => `<th>${esc(c.label)}</th>`).join('')}<th>Jami</th></tr></thead>
    <tbody>${rowsShown.map(i => `<tr><td title="${esc(i.label)}">${esc(i.label)}</td>${cols.map(c => { const v = val(i, c);
        const cell = (cells[i.key] || {})[c.key];
        return `<td class="c" style="${heat(v)}" title="${cell ? (MQ() ? `savdo ${money(cell.amount)} · ` : `${nf0.format(cell.qty)} dona · `) + `gross marja ${pct(cell.margin_pct)}` : ''}">${fmtCell(v)}</td>`; }).join('')}<td>${lastCol(i)}</td></tr>`).join('')}
      <tr class="pvtot"><td>Jami</td>${cols.map(c => `<td>${st.pvMode === 'amount' ? mln(c[mk]) : st.pvMode === 'row' ? (mval(T) ? nf1.format((c[mk] || 0) / mval(T) * 100) + '%' : '·') : '100%'}</td>`).join('')}<td>${st.pvMode === 'amount' ? mln(mval(T)) : '100%'}</td></tr>
    </tbody></table></div>
    <div class="foot">${st.pvMode === 'amount' ? (MQ() ? 'Kataklarda dona (bonus bilan).' : "Kataklarda summa, mln so'm.") : st.pvMode === 'row' ? `Har bir qatorda: shu qator ${MQ() ? 'donasining' : 'savdosining'} ustunlar bo'yicha taqsimoti.` : `Har bir ustunda: shu ustun ${MQ() ? 'donasining' : 'savdosining'} qatorlar bo'yicha taqsimoti.`}
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
function renderTop(t) { TOP = t; STK.top = null; renderTopBody(); stockForTop(t.rows); }
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
// TOP SKU — ixcham ro'yxat (A variant): nom (brend yarim qalin) · o'ngda dona/summa + o'zgarish;
// ostida summa (yoki dona) · gross summa · gross % · front %. TOP 10/20/50 va saralash.
const TOP_SORTS = () => [['m', MQ() ? 'Dona' : 'Summa'], [MQ() ? 'amount' : 'qty', MQ() ? 'Summa' : 'Dona'], ['margin', 'Gross'], ['delta', "O'zg."]];
function topApiSort() { return st.topSort === 'm' || st.topSort === 'delta' ? st.metric : st.topSort; }
async function loadTop() {
  const F = filters(), seq = reqSeq.main;
  $('top').classList.add('loading');
  try {
    const t = await api('breakdown', {...F, rows: 'sku', sort: topApiSort(), limit: st.topN});
    if (seq !== reqSeq.main) return;
    renderTop(t);
  } catch (e) { showError(e); }
  finally { $('top').classList.remove('loading'); }
}
function skuName(label, brand, strip) {
  let rest = String(label || '');
  if (strip && rest.toLowerCase().startsWith(strip)) rest = rest.slice(strip.length).trimStart();
  const b = String(brand || '').trim();
  if (b && rest.toLowerCase().startsWith(b.toLowerCase())) return `<b>${esc(rest.slice(0, b.length))}</b>${esc(rest.slice(b.length))}`;
  if (b && !rest.toLowerCase().includes(b.toLowerCase())) return `<b>${esc(b)}</b> ${esc(rest)}`;
  return esc(rest);
}
function renderTopBody() {
  const t = TOP; if (!t) return;
  const host = $('top'), head = $('topHead'), tools = $('topTools');
  let rows = t.rows.slice();
  if (st.topSort === 'delta') rows.sort((a, b) => (mdelta(b.delta) ?? -1e9) - (mdelta(a.delta) ?? -1e9));
  const sumTop = rows.reduce((s, i) => s + mval(i.current), 0), tot = t.total ? mval(t.total) : 0;
  $('topH').innerHTML = `<span class="c">${st.topOpen ? '▾' : '▸'}</span>TOP SKU`;
  $('topHint').textContent = rows.length ? `${rows.length} ta · ${MQ() ? nf0.format(sumTop) + ' dona' : money(sumTop)}${tot ? ' · ' + pct(sumTop / tot * 100) : ''}` : "savdo yo'q";
  head.setAttribute('aria-expanded', st.topOpen);
  $('topBlock').classList.toggle('open', st.topOpen);
  head.onclick = () => { st.topOpen = !st.topOpen; renderTopBody(); };
  host.hidden = tools.hidden = !st.topOpen;
  if (!st.topOpen) { host.innerHTML = ''; return; }
  tools.innerHTML = `<span class="tseg">${[10, 20, 50].map(n => `<button data-n="${n}" aria-pressed="${st.topN === n}">TOP ${n}</button>`).join('')}</span>
    <span class="tsort"><span class="lbl2">Saralash:</span>${TOP_SORTS().map(([k, l]) => `<button data-s="${k}" aria-pressed="${st.topSort === k}">${l}${st.topSort === k ? ' ▼' : ''}</button>`).join('')}</span>`;
  tools.querySelectorAll('[data-n]').forEach(b => b.onclick = () => { st.topN = +b.dataset.n; loadTop(); });
  tools.querySelectorAll('[data-s]').forEach(b => b.onclick = () => { st.topSort = b.dataset.s; if (st.topSort === 'delta') renderTopBody(); else loadTop(); });
  // bitta kategoriyada nomlarning umumiy birinchi so'zi (masalan «Кондиционер») olib tashlanadi
  let strip = '';
  if (!isMulti() && rows.length > 1) {
    const w = rows.map(i => (String(i.label || '').split(/\s+/)[0] || '').toLowerCase());
    if (w[0].length > 2 && w.every(x => x === w[0])) strip = w[0];
  }
  const hasCmp = !!cmpRange();
  host.innerHTML = rows.length ? rows.map((i, n) => {
    const rid = `s:${i.key}`;
    const node = ROOTS.get(rid) || newRoot(rid, 'loc', [{dim: 'sku', key: i.key}], {label: i.label, row: i});
    const sub = []; if (node.open && node.card && !node.card.error) subtreeRows(node, sub, mval(i.current), 1);
    const C = i.current;
    return `<button type="button" class="sk can ${node.open ? 'op' : ''}" aria-expanded="${!!node.open}" data-rid="${esc(rid)}" title="${esc(i.label)}">
      <span class="rk">${node.loading ? '…' : n + 1}</span>
      <span class="mid"><span class="nm">${skuName(i.label, i.sub, strip)}</span>
        <span class="sub">${MQ() ? money(C.amount) : nf0.format(C.qty || 0) + ' dona'} · gross <b class="${C.margin < 0 ? 'neg' : ''}">${money(C.margin)}</b> · ${pct(C.margin_pct)} · front <span class="${C.gross < 0 ? 'neg' : ''}">${pct(C.gross_pct)}</span></span>
        ${stockLine(STK.top && STK.top[i.key], true)}</span>
      <span class="rt"><span class="v">${mnum(C)}</span>${hasCmp ? `<span class="dd">${dTxt(mdelta(i.delta))}</span>` : ''}</span>
    </button>${node.open ? skuCard(node) + (sub.length ? `<div class="tw sub">${sub.join('')}</div>` : '') : ''}`;
  }).join('') + (st.topN < 50 && t.total_rows > rows.length ? `<button type="button" class="more" id="topMore">TOP ${st.topN === 10 ? 20 : 50} ni ko'rsatish ▾</button>` : '')
    : `<div class="empty">Savdo yo'q.</div>`;
  const mo = $('topMore'); if (mo) mo.onclick = e => { e.stopPropagation(); st.topN = st.topN === 10 ? 20 : 50; loadTop(); };
  // bosishni konteyner ushlaydi (delegatsiya) — qayta chizilganda ham ishonchli ishlaydi
  host.onclick = e => {
    const r = e.target.closest('.sk.can'); if (!r || !host.contains(r)) return;
    const node = ROOTS.get(r.dataset.rid); if (!node) return;
    if (node.open) { node.open = false; renderTopBody(); } else openSku(node);
  };
  wireTree(host);
}

// ---------------------------------------------------------------- yuklash
const reqSeq = {main: 0};
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
const STALE = {Qirqim: true, Tovarlar: true, Qoldiq: true};
let LIST = null;
async function load() {
  renderHeader();
  STALE.Qirqim = STALE.Tovarlar = STALE.Qoldiq = true;
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
    stockForList(LF, listDim);                 // kartalardagi «📦 qoldiq» qatori (fonda)
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
  } else if (t === 'Qoldiq') {
    loadStockTab();
  } else if (t === 'Tovarlar') {
    const secs = ['ablocks', 'top'].map($); secs.forEach(s => s.classList.add('loading'));
    if (!$('ablocks').firstChild) $('ablocks').innerHTML = `<div class="empty">Yuklanmoqda…</div>`;
    try {
      const [attrs, top] = await Promise.all([api('attributes', F), api('breakdown', {...F, rows: 'sku', sort: topApiSort(), limit: st.topN})]);
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
  const nb = $('navBack');
  if (nb) {
    const prev = NAV.length ? allScopes().find(c => c.key === NAV[NAV.length - 1].cat) : null;
    nb.hidden = !prev || VIEW !== 'analytics';
    if (prev) nb.textContent = `‹ ${prev.name}`;
  }
  if (!(tg && tg.BackButton)) return;
  const qd = typeof QS !== 'undefined' && QS.detail && TAB === 'Qoldiq';
  (sheetOpen() || VIEW === 'upload' || qd || (NAV.length && VIEW === 'analytics')) ? tg.BackButton.show() : tg.BackButton.hide();
}
function navBack() {
  const p = NAV.pop(); if (!p) return;
  st.cat = p.cat; st.f = p.f; st.aExpand = {}; st.aOpen = null; st.rows = 'region'; st.cols = ''; LIST_ALL = false;
  resetView(); syncBack(); load().then(() => window.scrollTo(0, p.y || 0));
}
$('navBack').onclick = navBack;
if (tg && tg.BackButton) tg.BackButton.onClick(() => {
  if (sheetOpen()) closeSheet(); else if (VIEW === 'upload') setView('analytics');
  else if (TAB === 'Qoldiq' && QS.detail) closeDetail(); else if (NAV.length) navBack(); });
document.querySelectorAll('#chartMetric button').forEach(b => b.onclick = () => {
  st.chartMetric = b.dataset.m;
  document.querySelectorAll('#chartMetric button').forEach(x => x.setAttribute('aria-pressed', x === b));
  renderChart();
});
let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { if (TAB === 'Umumiy') renderChart(); }, 150); });


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
  stockPlanCard(host);                       // qoldiq rejaga yetadimi (joriy oy, savdo)
  host.querySelectorAll('button[data-cid]').forEach(b => b.onclick = () => {
    const c = META.categories.find(x => String(x.id) === b.dataset.cid); if (!c) return;
    goCategory(c); setTab('Umumiy');
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
  ['Umumiy', 'Qirqim', 'Tovarlar', 'Qoldiq', 'Reja'].forEach(x => { $('pane' + x).hidden = x !== t; });
  const rj = t === 'Reja';                            // reja — kategoriya/davr/filtrga bog'liq emas
  $('catBtn').hidden = rj; $('topAnalytics').hidden = rj; $('upTitle').hidden = !rj;
  if (rj) { $('upTitle').textContent = 'Reja bajarilishi'; $('tmeta').textContent = META.data_to ? `ma'lumot ${dm(META.data_to)} gacha` : ''; }
  else updMeta();
  if (rj && !PL.data && !PL.loading) loadPlan();
  document.querySelectorAll('#tabbar button').forEach(b => b.setAttribute('aria-pressed', b.dataset.t === t));
  window.scrollTo(0, 0);
  if (t === 'Umumiy') renderChart();                 // yashirin paytda kenglik 0 bo'ladi — qayta chizamiz
  if (STALE[t]) loadTab(t);
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
// ============================================================ QOLDIQ
// «Qoldiq» tabi (⚠ Signallar · ▦ Xarita · 📦 Qoldiq) va boshqa tablardagi «📦 qoldiq» qatorlari.

const QS = {view: 'sig', level: null, loc: 'all', limit: 30, rows: 'w', wid: null, mapCat: null,
            detail: null, sig: null, map: null, ov: null, seq: 0, scrollY: 0};
const STK = {list: null, top: null};
const LV = [{n: 'Tugadi', s: '< 3 kun', i: '🔴'}, {n: 'Tugayapti', s: '< 7 kun', i: '🟠'}, {n: 'Kamaymoqda', s: 'savdo tezlashgan', i: '🟡'}];
const STOCK_DIMS = {region: 1, cluster: 1, branch: 1, brand: 1, sku: 1, category: 1};

const dTxtK = d => (d == null ? 'sotilmayapti' : `${d >= 100 ? nf0.format(d) : nf0.format(Math.round(d))} kunga`);
const dCls = d => (d == null ? 'hi' : d < 3 ? 'lo' : d < 7 ? 'or' : d < 15 ? 'ye' : d > 90 ? 'hi' : 'ok');
const dPill = d => `<span class="qp ${dCls(d)}">${dTxtK(d)}</span>`;
const lvPill = (lv, d) => `<span class="qp l${lv}">${d == null ? '—' : nf0.format(Math.round(d))} kun · ${LV[lv].n.toLowerCase()}</span>`;

// ---------------------------------------------------------------- boshqa tablardagi qator
function stockLine(x, sku) {
  if (!x) return '';
  const val = sku ? `<b>${nf0.format(x.stock)}</b> dona` : `<b>${money(x.value)}</b>`;
  const oos = x.oos ? (sku ? `<span>${nf0.format(x.oos)} filialda yo'q</span>` : `<span>OOS <b>${nf0.format(x.oos)}</b></span>`) : '';
  return `<span class="qline"><span>📦 qoldiq ${val}</span>${dPill(x.days)}${oos}</span>`;
}
async function stockForList(F, dim) {
  const seq = reqSeq.main;
  try {
    const r = await api('stock/by', {...F, dim});
    if (seq !== reqSeq.main) return;
    STK.list = r.items || {}; renderCatList();
  } catch (e) { STK.list = null; }
}
async function stockForNode(node) {
  if (!STOCK_DIMS[node.childDim] || !node.children) return;
  try {
    const r = await api('stock/by', {...pathFilters(node.path), dim: node.childDim});
    const it = r.items || {};
    node.children.forEach(ch => { ch.stock = it[ch.key] || null; });
    if (node.depth === 0 && node.root === 'm') {
      const a = await api('stock/by', {...pathFilters([]), dim: 'all'});
      node.stock = (a.items || {}).all || null;
    }
    rerender(node.root);
  } catch (e) { /* qoldiq yo'q — jim */ }
}
async function stockForTop(rows) {
  if (!rows || !rows.length) { STK.top = null; return; }
  try {
    const r = await api('stock/by', {...filters(), product_ids: rows.map(i => Number(i.key)), dim: 'sku'});
    STK.top = r.items || {}; renderTopBody();
  } catch (e) { STK.top = null; }
}
async function stockPlanCard(host) {
  if (PL.mode !== 'month' || PL.metric !== 'amount' || !PL.data || !PL.data.data_to) return;
  const d = PL.data, m = PL.month;
  if (+d.data_to.slice(0, 4) !== d.year || +d.data_to.slice(5, 7) - 1 !== m) return;   // faqat joriy oy
  let r;
  try { r = await api('stock/by', {scope: 'a:', date_from: addDays(d.data_to, -29), date_to: d.data_to, compare: 'none', dim: 'category'}); }
  catch (e) { return; }
  const it = r.items || {};
  if (!Object.keys(it).length || PL.data !== d || PL.month !== m || !host.isConnected) return;
  let need = 0, have = 0; const short = [];
  for (const c of d.cats.filter(x => x.in_db)) {
    const full = c.plan_amount[m]; if (full == null) continue;
    const rest = Math.max(0, full - (c.fact_amount[m] || 0)), s = it[String(c.id)];
    const sell = s ? s.sell : 0;
    need += rest; have += sell;
    if (rest > 0 && sell < rest) short.push({name: c.name, gap: rest - sell});
  }
  if (!need) return;
  short.sort((a, b) => b.gap - a.gap);
  const gap = need - have;
  const card = document.createElement('div');
  card.className = 'card qplan';
  card.innerHTML = `<div class="sh"><span class="label">📦 Qoldiq rejani bajarishga yetadimi?</span><span class="hint">${dm(r.snap && (r.snap.b || r.snap.w))} holati</span></div>
    <div class="m2">Oy oxirigacha kerakli savdo — <b>${money(need)}</b> · filial + ombor qoldig'i (sotish narxida) — <b>${money(have)}</b></div>
    <div class="m2">${gap > 0 ? `<span class="qp lo">${money(gap)} yetmaydi</span>` : `<span class="qp ok">yetadi · zaxira ${money(-gap)}</span>`}
      ${short.length ? `<span class="hint">yetmaydi: ${short.slice(0, 4).map(x => esc(x.name)).join(', ')}${short.length > 4 ? ` +${short.length - 4}` : ''}</span>` : ''}</div>`;
  host.querySelector('.qplan')?.remove();
  host.insertBefore(card, host.children[1] || null);
}

// ---------------------------------------------------------------- Qoldiq tabi
function stockFilters() { return {...filters()}; }
function snapNote(s) {
  if (!s || (!s.b && !s.w)) return '';
  const parts = [];
  if (s.b) parts.push(`filial ${dm(s.b)}`);
  if (s.w) parts.push(`ombor ${dm(s.w)}`);
  return `qoldiq: ${parts.join(' · ')} holati · savdo — 30 va 7 kunlik sur'at`;
}
function noStock() {
  return `<div class="card"><div class="empty">Qoldiq hali yuklanmagan.<br>${META.can_upload
    ? "«📥» bo'limidan filial qoldig'i (Filial, Tovar Id, Qoldiq) va ombor qoldig'i (ID, Омбор, Мижоз учун қолдиқ) fayllarini yuklang — sana fayl nomida bo'lsin (…_17.10.2026.xlsx)."
    : "Administrator qoldiq fayllarini yuklashini kuting."}</div></div>`;
}
async function loadStockTab() {
  STALE.Qoldiq = false;
  QS.sig = QS.map = QS.ov = null;
  renderStock();
}
function renderStock() {
  const host = $('qBody');
  document.querySelectorAll('#qSeg button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === QS.view && !QS.detail));
  $('qSeg').hidden = !!QS.detail;
  if (QS.detail) return renderDetail();
  if (QS.view === 'sig') return renderSignals(host);
  if (QS.view === 'map') return renderMap(host);
  return renderOverview(host);
}
function counters(d, sel) {
  return `<div class="qsum">${[0, 1, 2].map(k => `<button class="l${k} ${sel === k ? 'on' : ''}" data-lv="${k}">
    <span>${LV[k].i} ${LV[k].n}</span><b>${nf0.format(d.counts[k] || 0)}</b><span>${k === 2 && d.new && d.new[2] ? `${d.new[2]} tasi yangi` : LV[k].s}</span></button>`).join('')}</div>`;
}
function sigCard(x) {
  const ic = x.t === 'w' ? '🏬' : '🏪';
  const lines = [];
  if (x.t === 'w') {
    lines.push(`omborda <b>${nf0.format(x.stock)}</b>`);
    lines.push(`filiallarda ${x.br_days == null ? nf0.format(x.br_stock) : dTxtK(x.br_days)}`);
  } else lines.push(`qoldiq <b>${nf0.format(x.stock)}</b> dona`);
  if (x.accel && x.accel > 1) lines.push(`savdo ▲ ${nf0.format(Math.round((x.accel - 1) * 100))}% (7 kun)`);
  if (x.prev != null) lines.push(`o'tgan hafta ${nf0.format(Math.round(x.prev))} kun`);
  if (x.oos) lines.push(`${nf0.format(x.oos)} filial×SKU OOS`);
  return `<button class="al l${x.level}" data-t="${x.t}" data-id="${x.id}" data-c="${x.cat_id}" data-b="${esc(x.brand)}">
    <span class="p">${ic} <b>${esc(x.name)}</b>${x.sub ? ` · ${esc(x.sub)}` : ''} · ${esc(x.cat || '')}${x.new ? '<span class="nw2">yangi</span>' : ''}</span>
    <span class="w"><b>${esc(x.brand)}</b>${lvPill(x.level, x.days)}</span>
    <span class="k">${lines.map(l => `<span>${l}</span>`).join('')}<span><b>≈ ${money(x.risk)}</b>/kun xavf</span></span></button>`;
}
async function renderSignals(host, more) {
  const seq = ++QS.seq;
  if (!QS.sig || more) {
    if (!more) host.innerHTML = `<div class="empty">Yuklanmoqda…</div>`;
    try {
      const d = await api('stock/signals', {...stockFilters(), loc: QS.loc, level: QS.level, limit: QS.limit});
      if (seq !== QS.seq) return;
      QS.sig = d;
    } catch (e) { host.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  }
  const d = QS.sig;
  if (!d.snap || (!d.snap.b && !d.snap.w)) { host.innerHTML = noStock(); return; }
  const chips = [['all', 'Hammasi'], ['w', '🏬 Omborlar'], ['b', '🏪 Filiallar']];
  host.innerHTML = `<div class="hint">${snapNote(d.snap)}</div>
    ${counters(d, QS.level)}
    <div class="qchips">${chips.map(([k, l]) => `<button data-loc="${k}" aria-pressed="${QS.loc === k}">${l}</button>`).join('')}</div>
    ${d.items.length ? d.items.map(sigCard).join('') : `<div class="card"><div class="empty">${QS.level == null ? "Yetishmovchilik signali yo'q 👍" : "Bu darajada signal yo'q"}</div></div>`}
    ${d.total > d.items.length ? `<button class="more" id="qMore">Yana ${nf0.format(d.total - d.items.length)} ta signal ▾</button>` : ''}
    <div class="qbtns"><button class="btn ghost" id="qXl">⬇ Excel</button><button class="btn ghost" id="qToMap">▦ Xaritada ko'rish</button></div>`;
  host.querySelectorAll('[data-lv]').forEach(b => b.onclick = () => { const k = +b.dataset.lv; QS.level = QS.level === k ? null : k; QS.limit = 30; QS.sig = null; renderSignals(host); });
  host.querySelectorAll('[data-loc]').forEach(b => b.onclick = () => { QS.loc = b.dataset.loc; QS.limit = 30; QS.sig = null; renderSignals(host); });
  const mo = $('qMore'); if (mo) mo.onclick = () => { QS.limit += 30; renderSignals(host, true); };
  $('qToMap').onclick = () => { QS.view = 'map'; renderStock(); };
  $('qXl').onclick = e => stockExport(e.currentTarget);
  host.querySelectorAll('.al[data-t]').forEach(b => b.onclick = () => openDetail({
    loc_type: b.dataset.t, loc_id: +b.dataset.id, map_category: +b.dataset.c, brand: b.dataset.b}));
}
const hmCls = v => (v == null ? 'h4' : v < 0 ? 'h5' : v < 3 ? 'h0' : v < 7 ? 'h1' : v < 15 ? 'h2' : 'h3');
const hmTxt = v => (v == null ? '—' : v < 0 ? '∞' : nf0.format(Math.round(v)));
async function renderMap(host) {
  const seq = ++QS.seq;
  if (!QS.map) {
    host.innerHTML = `<div class="empty">Yuklanmoqda…</div>`;
    try {
      const d = await api('stock/map', {...stockFilters(), rows: QS.rows, wid: QS.wid, map_category: QS.mapCat});
      if (seq !== QS.seq) return;
      QS.map = d; QS.mapCat = d.category;
    } catch (e) { host.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  }
  const d = QS.map;
  if (!d.snap || (!d.snap.b && !d.snap.w)) { host.innerHTML = noStock(); return; }
  const rk = [['w', 'Omborlar'], ['region', 'Hududlar'], ['b', 'Filiallar']];
  const cats = d.categories.length > 1 ? `<label class="sortl">Kategoriya <select id="qCat">${d.categories.map(c =>
    `<option value="${c.id}" ${c.id === d.category ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>` : `<b>${esc(d.category_name || '')}</b>`;
  const head = `<tr><th></th>${d.brands.map(b => `<th title="${esc(b)}">${esc(b)}</th>`).join('')}</tr>`;
  const rows = d.rows.map(r => `<tr><td class="n">${d.rows_kind === 'w' ? `<button class="lnk" data-wid="${r.id}">${esc((r.name || '').replace(/ омбори$/, ''))} ›</button>` : esc(r.name)}</td>
    ${d.brands.map(b => `<td class="${hmCls(r.cells[b])}" data-r="${r.id}" data-b="${esc(b)}">${hmTxt(r.cells[b])}</td>`).join('')}</tr>`).join('');
  const tot = `<tr class="tot2"><td class="n"><b>Jami</b></td>${d.brands.map(b => `<td class="${hmCls(d.total.cells[b])}">${hmTxt(d.total.cells[b])}</td>`).join('')}</tr>`;
  host.innerHTML = `<div class="hint">${snapNote(d.snap)}</div>
    <div class="qchips">${rk.map(([k, l]) => `<button data-rows="${k}" aria-pressed="${QS.rows === k && !(k === 'b' && QS.wid)}">${l}</button>`).join('')}</div>
    <div class="ctl">${cats}</div>
    ${QS.wid ? `<div class="bc2"><button class="tchip back" id="qWidBack">‹ Omborlar</button><span>${esc(d.wh_name || '')} filiallari</span></div>` : ''}
    ${d.rows.length && d.brands.length ? `<div class="card hmw"><table class="hm">${head}${rows}${tot}</table></div>` : `<div class="card"><div class="empty">Bu kategoriyada qoldiq yo'q</div></div>`}
    <div class="lg"><span><i class="h0"></i>&lt;3 kun</span><span><i class="h1"></i>3–7</span><span><i class="h2"></i>8–14</span><span><i class="h3"></i>me'yorda</span><span><i class="h5"></i>sotilmaydi</span></div>
    <p class="foot">Katakda — necha kunga yetadi: qoldiq ÷ kunlik savdo (30 kunlik va oxirgi 7 kunlik sur'atdan qaysi biri tez bo'lsa). Ombor qatorida — ombor qoldig'i ÷ u ta'minlaydigan filiallar savdosi. Ombor nomini bossangiz — filiallari; katakni bossangiz — SKU'lar.</p>`;
  host.querySelectorAll('[data-rows]').forEach(b => b.onclick = () => { QS.rows = b.dataset.rows; QS.wid = null; QS.map = null; renderMap(host); });
  const qc = $('qCat'); if (qc) qc.onchange = () => { QS.mapCat = +qc.value; QS.map = null; renderMap(host); };
  host.querySelectorAll('[data-wid]').forEach(b => b.onclick = () => { QS.rows = 'b'; QS.wid = +b.dataset.wid; QS.map = null; renderMap(host); });
  const wb = $('qWidBack'); if (wb) wb.onclick = () => { QS.rows = 'w'; QS.wid = null; QS.map = null; renderMap(host); };
  const lt = {w: 'w', region: 'region', b: 'b'}[d.rows_kind];
  host.querySelectorAll('td[data-r]').forEach(td => td.onclick = () => openCellSheet(
    {loc_type: lt, loc_id: +td.dataset.r, map_category: d.category, brand: td.dataset.b},
    `${esc(d.rows.find(r => String(r.id) === td.dataset.r)?.name || '')} × ${esc(td.dataset.b)}`));
}
function skuRows(items, locType) {
  return items.map(x => {
    const lv = x.days == null ? null : x.days < 3 ? 0 : x.days < 7 ? 1 : x.days < 15 ? 2 : null;
    const alt = x.other && x.other.length ? `<span>boshqa omborda: ${x.other.map(o => `${esc(o.name.replace(' омбори', ''))} ${nf0.format(o.qty)}`).join(', ')}</span>` : '';
    return `<div class="al ${lv == null ? 'l9' : 'l' + lv}"><span class="w"><b>${esc(x.name)}</b>${dPill(x.days)}</span>
      <span class="k"><span>${locType === 'w' ? 'omborda' : 'qoldiq'} <b>${nf0.format(x.stock)}</b></span>${locType === 'w' ? `<span>filiallarda ${nf0.format(x.br_stock)}</span>` : (x.wh_stock ? `<span>omborida ${nf0.format(x.wh_stock)}</span>` : '')}
      ${x.oos ? `<span>${locType === 'b' ? 'tugagan' : `${nf0.format(x.oos)} filialda yo'q`}</span>` : ''}${x.risk ? `<span><b>${money(x.risk)}</b>/kun</span>` : ''}${alt}</span></div>`;
  }).join('');
}
async function openCellSheet(q, title) {
  const host = openSheetBox(title, `<div class="empty">Yuklanmoqda…</div>`,
    `<button class="btn primary" id="qFull">To'liq tafsilot →</button>`);
  host.querySelector('#qFull').onclick = () => { closeSheet(); openDetail(q); };
  try {
    const d = await api('stock/detail', {...stockFilters(), ...q});
    const b = host.querySelector('.sbody'); if (!b) return;
    const t = d.total;
    b.innerHTML = `<div class="hint">${q.loc_type === 'w' ? `omborda <b>${nf0.format(t.wh_stock)}</b> · filiallarda ${nf0.format(t.br_stock)}` : `qoldiq <b>${nf0.format(t.br_stock)}</b>`} · ${nf1.format(t.sales_day || 0)} dona/kun · ${dTxtK(t.days)}</div>
      ${d.items.length ? skuRows(d.items.slice(0, 15), q.loc_type) : `<div class="empty">SKU yo'q</div>`}`;
  } catch (e) { const b = host.querySelector('.sbody'); if (b) b.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; }
}
function histChart(h) {
  if (!h || h.length < 2) return `<div class="hint">Grafik uchun kamida 2 ta qoldiq sanasi kerak.</div>`;
  const W = 320, H = 90, P = 6, vals = h.map(x => x.days == null ? null : Math.min(x.days, 60));
  const mx = Math.max(15, ...vals.filter(v => v != null));
  const x = i => P + i * (W - 2 * P) / (h.length - 1), y = v => H - 16 - (v / mx) * (H - 26);
  const pts = vals.map((v, i) => v == null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`).filter(Boolean).join(' ');
  const y7 = y(7);
  return `<svg viewBox="0 0 ${W} ${H}" class="qhist" role="img" aria-label="Qoldiq necha kunga yetishi dinamikasi">
    <rect x="0" y="${y7.toFixed(1)}" width="${W}" height="${(H - 16 - y7).toFixed(1)}" fill="var(--bad-soft)"/>
    <polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2.5"/>
    ${vals.map((v, i) => v == null ? '' : `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="3" fill="var(--accent)"/>`).join('')}
    ${h.map((p, i) => (i === 0 || i === h.length - 1 || h.length <= 6) ? `<text x="${x(i).toFixed(1)}" y="${H - 3}" font-size="10" text-anchor="${i === 0 ? 'start' : i === h.length - 1 ? 'end' : 'middle'}" fill="var(--muted)">${dm(p.date)} · ${p.days == null ? '∞' : nf0.format(Math.round(p.days))}</text>` : '').join('')}
  </svg>`;
}
function openDetail(q) {
  QS.scrollY = window.scrollY;
  QS.detail = {q, data: null};
  renderStock(); syncBack(); window.scrollTo(0, 0);
}
function closeDetail() {
  QS.detail = null; renderStock(); syncBack();
  requestAnimationFrame(() => window.scrollTo(0, QS.scrollY || 0));
}
async function renderDetail() {
  const host = $('qBody'), D_ = QS.detail;
  if (D_.q.list) return renderListView(host, D_);
  if (!D_.data) {
    host.innerHTML = `<button class="tchip back" id="qBack">‹ Orqaga</button><div class="empty">Yuklanmoqda…</div>`;
    $('qBack').onclick = closeDetail;
    try { D_.data = await api('stock/detail', {...stockFilters(), ...D_.q}); }
    catch (e) { host.innerHTML = `<button class="tchip back" id="qBack">‹ Orqaga</button><div class="errbox">${esc(e.message)}</div>`; $('qBack').onclick = closeDetail; return; }
    if (QS.detail !== D_) return;
  }
  const d = D_.data, t = d.total, isW = d.loc_type === 'w';
  const lv = d.level != null ? `<span class="pl">${LV[d.level].i} ${LV[d.level].n.toLowerCase()}</span>` : '';
  const prev = d.history.length > 1 ? d.history[d.history.length - 2] : null;
  const gc = (k, x, y) => `<div><small>${k}</small><b>${x}</b><small>${y || '&nbsp;'}</small></div>`;
  const start = (() => {   // signal qachon boshlangan: kunlar 15 dan pastga tushgan birinchi sana
    const h = d.history; let s = null;
    for (let i = h.length - 1; i >= 0; i--) { if (h[i].days != null && h[i].days < 15) s = h[i].date; else break; }
    return s && s !== h[0].date ? s : null;
  })();
  const alts = {};
  d.items.forEach(x => (x.other || []).forEach(o => { alts[o.name] = (alts[o.name] || 0) + o.qty; }));
  const altList = Object.entries(alts).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const short = d.items.filter(x => x.days != null && x.days < 7);
  host.innerHTML = `<button class="tchip back" id="qBack">‹ Orqaga</button>
    <div class="qdh"><h2>${esc(d.name)}${d.brand ? ` · ${esc(d.brand)}` : ''}</h2><div class="hint">${esc(d.category || '')} · ${snapNote(d.snap)}</div></div>
    <div class="qtot"><div class="r1"><span>${isW ? `Ombor${d.branches ? ` + uning ${d.branches} filiali` : ''}` : d.loc_type === 'b' ? 'Filial' : 'Hudud filiallari'}</span>${lv}</div>
      <div class="big">${dTxtK(t.days)}${isW ? ' omborda' : ''}</div>
      <div class="g3">${isW ? gc('Ombor', `${nf0.format(t.wh_stock)} dona`, prev ? `oldin ${nf0.format(prev.stock)}` : '')
        + gc('Filiallar', `${nf0.format(t.br_stock)} dona`, dTxtK(t.br_days))
        : gc('Qoldiq', `${nf0.format(t.br_stock)} dona`, prev ? `oldin ${nf0.format(prev.stock)}` : '')
          + gc('Omborida', `${nf0.format(t.wh_stock)} dona`, '')}
        ${gc('Savdo', `${nf1.format(t.sales_day || 0)} dona/kun`, d.accel && Math.abs(d.accel - 1) >= 0.1 ? `7 kunda ${d.accel > 1 ? '▲' : '▼'} ${nf0.format(Math.abs(Math.round((d.accel - 1) * 100)))}% · ≈ ${money(t.risk)}/kun` : `≈ ${money(t.risk)}/kun`)}</div></div>
    <div class="sec"><h2>Qoldiq qanday kamaydi</h2><span class="hint">necha kunga yetadi</span></div>
    <div class="card">${histChart(d.history)}${start ? `<div class="hint">🟡 ${dm(start)} dan 15 kundan pastga tushgan</div>` : ''}</div>
    <div class="sec"><h2>SKU'lar</h2><span class="hint">${d.items.length} ta · kam qolgani tepada</span></div>
    ${d.items.length ? skuRows(d.items, d.loc_type) : `<div class="card"><div class="empty">SKU yo'q</div></div>`}
    ${short.length ? `<div class="card"><b>Nima qilish mumkin</b><div class="m2">${altList.length
      ? altList.map(([n, q]) => `• ${esc(n)}da shu SKU'lardan ${nf0.format(q)} dona bor → ko'chirish`).join('<br>')
      : "• Boshqa omborlarda ham yo'q → xarid buyurtmasi kerak"}</div></div>` : ''}`;
  $('qBack').onclick = closeDetail;
}
async function renderOverview(host) {
  const seq = ++QS.seq;
  if (!QS.ov) {
    host.innerHTML = `<div class="empty">Yuklanmoqda…</div>`;
    try {
      const d = await api('stock/overview', stockFilters());
      if (seq !== QS.seq) return;
      QS.ov = d;
    } catch (e) { host.innerHTML = `<div class="errbox">${esc(e.message)}</div>`; return; }
  }
  const d = QS.ov, t = d.total;
  if (!d.snap || (!d.snap.b && !d.snap.w)) { host.innerHTML = noStock(); return; }
  const gc = (k, x, y) => `<div><small>${k}</small><b>${x}</b><small>${y || '&nbsp;'}</small></div>`;
  const bar = days => `<span class="dbar"><i style="left:${Math.min(100, (days == null ? 100 : days / 60 * 100)).toFixed(0)}%"></i></span>`;
  host.innerHTML = `<div class="hint">${snapNote(d.snap)}</div>
    <div class="qtot"><div class="r1"><span>Jami qoldiq</span><span class="pl">${dTxtK(t.days)}</span></div>
      <div class="big">${money(t.br_value + t.wh_value)}</div>
      <div class="g3">${gc('Filiallar', money(t.br_value), `${nf0.format(t.br_stock)} dona · tannarx`)}${gc('Omborlar', money(t.wh_value), `${nf0.format(t.wh_stock)} dona · A narx×kurs`)}${gc('OOS', nf0.format(t.oos), 'filial×SKU')}</div></div>
    ${listCards(d.lists)}
    <div class="sec"><h2>Omborlar</h2><span class="hint">o'z filiallari savdosiga</span></div>
    ${d.warehouses.map(w => `<button class="li can qw" data-w="${w.id}"><span class="r1"><span class="n">🏬 ${esc(w.name)}</span><span class="v">${money(w.value)}</span></span>
      <span class="m">${dPill(w.days)}<span>omborda <b>${nf0.format(w.stock)}</b> dona</span><span>filiallarda ${dTxtK(w.br_days)}</span><span>OOS ${nf0.format(w.oos)}</span></span>
      <span class="m"><span>${esc(w.regions.join(', '))}</span></span>${bar(w.days)}</button>`).join('')}
    <div class="sec"><h2>Hududlar</h2><span class="hint">filial qoldig'i · kam qolgani tepada</span></div>
    ${d.regions.map(r => `<button class="li can qr" data-r="${r.id}"><span class="r1"><span class="n">${esc(r.name)}</span><span class="v">${money(r.value)}</span></span>
      <span class="m">${dPill(r.days)}<span><b>${nf0.format(r.stock)}</b> dona</span><span>${r.branches} filial</span><span>OOS ${nf0.format(r.oos)}</span></span>${bar(r.days)}</button>`).join('')}
    <p class="foot">Filial qoldig'i — tannarxda (Kirim So'm), ombor qoldig'i — «A сектор» narxi × kurs (${nf0.format(d.rate || 0)}). Necha kunga yetadi — qoldiq ÷ 30 kunlik o'rtacha savdo.</p>`;
  host.querySelectorAll('[data-list]').forEach(b => b.onclick = () => openDetail({list: b.dataset.list}));
  host.querySelectorAll('.qw').forEach(b => b.onclick = () => openDetail({loc_type: 'w', loc_id: +b.dataset.w}));
  host.querySelectorAll('.qr').forEach(b => b.onclick = () => openDetail({loc_type: 'region', loc_id: +b.dataset.r}));
}
// ---------------------------------------------------------------- ro'yxatlar: yuborish · ortiqcha · harakatsiz
function listCards(l) {
  if (!l) return '';
  return `<div class="qlc">
    <button class="qlc-s" data-list="send"><span class="t">🚚 Ombordan yuborish</span><b>${nf0.format(l.send.positions)}</b><span>poz · ${nf0.format(l.send.qty)} dona</span></button>
    <button class="qlc-o" data-list="over"><span class="t">📈 Ortiqcha</span><b>${money(l.over.value)}</b><span>&gt; 90 kun · ${nf0.format(l.over.positions)} poz</span></button>
    <button class="qlc-i" data-list="idle"><span class="t">💤 Harakatsiz</span><b>${money(l.idle.value)}</b><span>60 kun sotuvsiz · ${nf0.format(l.idle.positions)}</span></button></div>`;
}
async function stockExport(btn) {
  const t = btn.textContent; btn.disabled = true; btn.textContent = 'Tayyorlanmoqda…';
  try {
    await api('stock/export', stockFilters());
    btn.textContent = '✅ Botga yuborildi';
    try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred('success'); } catch (e) {}
  } catch (e) { btn.textContent = '❌ ' + e.message; }
  setTimeout(() => { btn.textContent = t; btn.disabled = false; }, 4000);
}
const LIST_T = {send: '🚚 Ombordan yuborish', over: '📈 Ortiqcha qoldiq', idle: '💤 Harakatsiz qoldiq'};
async function renderListView(host, D_) {
  const q = D_.q;
  if (!D_.data || D_.kind !== q.list) {
    host.innerHTML = `<button class="tchip back" id="qBack">‹ Orqaga</button><div class="empty">Yuklanmoqda…</div>`;
    $('qBack').onclick = closeDetail;
    try { D_.data = await api('stock/lists', {...stockFilters(), kind: q.list, limit: q.list === 'send' ? 300 : 300}); D_.kind = q.list; }
    catch (e) { host.innerHTML = `<button class="tchip back" id="qBack">‹ Orqaga</button><div class="errbox">${esc(e.message)}</div>`; $('qBack').onclick = closeDetail; return; }
    if (QS.detail !== D_) return;
  }
  const d = D_.data;
  D_.open = D_.open || new Set();
  D_.show = D_.show || {};
  const segs = q.list === 'send'
    ? [['w', "Ombor bo'yicha"], ['b', "Filial bo'yicha"]].map(([k, l]) => `<button data-g="${k}" aria-pressed="${(D_.by || 'w') === k}">${l}</button>`).join('')
    : [['over', '📈 Ortiqcha'], ['idle', '💤 Harakatsiz']].map(([k, l]) => `<button data-k="${k}" aria-pressed="${q.list === k}">${l}</button>`).join('');
  let body = '';
  const shortW = n => (n || '').replace(/ омбори$/, '');
  if (q.list === 'send') {
    let groups = d.groups || [];
    if ((D_.by || 'w') === 'b') {
      const m = {};
      groups.forEach(g => g.items.forEach(x => { const k = x.branch; (m[k] = m[k] || {id: k, name: k, sub: x.region, positions: 0, qty: 0, items: []});
        m[k].positions++; m[k].qty += x.send; m[k].items.push({...x, wname: g.name}); }));
      groups = Object.values(m).sort((a, b) => b.qty - a.qty);
    }
    if (!D_.open.size && groups.length) D_.open.add(String(groups[0].id));
    body = groups.length ? groups.map(g => {
      const open = D_.open.has(String(g.id)), n = D_.show[g.id] || 10;
      const items = open ? g.items.slice(0, n).map(x => `<div class="li"><span class="r1"><span class="n">${esc((D_.by || 'w') === 'b' ? x.name : x.branch)}</span><span class="v">+${nf0.format(x.send)} dona</span></span>
        <span class="m"><span>${esc((D_.by || 'w') === 'b' ? `${shortW(x.wname)} omboridan` : x.name)}</span></span>
        <span class="m">${dPill(x.days)}<span>bor <b>${nf0.format(x.have)}</b></span><span>me'yor <b>${nf0.format(x.norm)}</b></span><span>omborda <b>${nf0.format(x.wh)}</b></span>${x.risk ? `<span>savdo ≈ ${money(x.risk)}/kun</span>` : ''}</span></div>`).join('') : '';
      const more = open && g.items.length > n ? `<button class="more" data-more="${esc(g.id)}">Yana ${nf0.format(g.items.length - n)} ta ▾</button>` : '';
      return `<button class="whh" data-open="${esc(g.id)}"><b>${open ? '▾' : '▸'} ${(D_.by || 'w') === 'b' ? '🏪' : '🏬'} ${esc(g.name)}</b><span>${nf0.format(g.positions)} poz · ${nf0.format(g.qty)} dona</span></button>${items}${more}`;
    }).join('') : `<div class="card"><div class="empty">Yuboriladigan tovar yo'q — filiallarda yetarli yoki omborda ham yo'q 👍</div></div>`;
    body += `<p class="foot">Filialda ${d.send_days} kundan kamga yetadigan SKU, uning omborida bor. Me'yor — ${esc(d.norm)} (30 kunlik savdo × 2). Yuboriladi = me'yor − bor, ombordagidan oshmaydi; ombor qoldig'i eng kam qolgan filiallarga birinchi taqsimlanadi.</p>`;
  } else {
    const n = D_.show.list || 30, items = d.items || [];
    body = items.length ? items.slice(0, n).map(x => `<div class="li"><span class="r1"><span class="n">${esc(x.name)}</span><span class="v">${money(x.value)}</span></span>
      <span class="m">${q.list === 'over' ? `<span class="qp hi">${nf0.format(x.days)} kunga</span>` : `<span class="qp hi">${d.idle_days}+ kun sotuvsiz</span>`}<span><b>${nf0.format(x.stock)}</b> dona</span>
        <span>${esc(shortW(x.zone))}${x.wh_stock ? ` · omborda ${nf0.format(x.wh_stock)}` : ''}${x.branches ? ` · ${x.branches} filialda ${nf0.format(x.br_stock)}` : ''}</span></span>
      ${x.top.length ? `<span class="m"><span>eng ko'p: ${x.top.map(t => `${esc(t.name.replace(/ filiali$/, ''))} — ${nf0.format(t.qty)}`).join(', ')}${x.branches > x.top.length ? ' …' : ''}</span></span>` : ''}</div>`).join('')
      + (items.length > n ? `<button class="more" data-more="list">Yana ${nf0.format(items.length - n)} ta ▾</button>` : '')
      : `<div class="card"><div class="empty">${q.list === 'over' ? "90 kundan ko'pga yetadigan qoldiq yo'q 👍" : "Harakatsiz qoldiq yo'q 👍"}</div></div>`;
    body += `<p class="foot">Zona — ombor va u ta'minlaydigan filiallar birga. ${q.list === 'over' ? `Ortiqcha — zona qoldig'i ${d.over_days} kundan ko'pga yetadi (30 kunlik savdo bo'yicha).` : `Harakatsiz — qoldig'i bor, ${d.idle_days} kunda zonada bitta ham sotilmagan.`} Qiymati: filialda tannarx, omborda A narx × kurs.</p>`;
  }
  const tot = q.list === 'send' ? `${nf0.format(d.summary.send.positions)} poz · ${nf0.format(d.summary.send.qty)} dona`
    : q.list === 'over' ? `${nf0.format(d.summary.over.positions)} poz · ${money(d.summary.over.value)}` : `${nf0.format(d.summary.idle.positions)} poz · ${money(d.summary.idle.value)}`;
  host.innerHTML = `<button class="tchip back" id="qBack">‹ Orqaga</button>
    <div class="qdh"><h2>${LIST_T[q.list]}</h2><div class="hint">${tot} · ${snapNote(d.snap)}</div></div>
    <div class="seg big">${segs}</div>${body}
    <button class="btn ghost" id="qXl">⬇ Excel</button>`;
  $('qBack').onclick = closeDetail;
  $('qXl').onclick = e => stockExport(e.currentTarget);
  host.querySelectorAll('[data-g]').forEach(b => b.onclick = () => { D_.by = b.dataset.g; D_.open = new Set(); D_.show = {}; renderListView(host, D_); });
  host.querySelectorAll('[data-k]').forEach(b => b.onclick = () => { if (q.list === b.dataset.k) return; q.list = b.dataset.k; D_.show = {}; renderListView(host, D_); });
  host.querySelectorAll('[data-open]').forEach(b => b.onclick = () => { const k = b.dataset.open; D_.open.has(k) ? D_.open.delete(k) : D_.open.add(k); renderListView(host, D_); });
  host.querySelectorAll('[data-more]').forEach(b => b.onclick = () => { const k = b.dataset.more; D_.show[k] = (D_.show[k] || (k === 'list' ? 30 : 10)) + (k === 'list' ? 30 : 20); renderListView(host, D_); });
}
document.querySelectorAll('#qSeg button').forEach(b => b.onclick = () => {
  if (QS.view === b.dataset.v && !QS.detail) return;
  QS.view = b.dataset.v; QS.detail = null; renderStock(); syncBack();
});

init();
})();
