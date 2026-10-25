/* spraulron front-end: one script for both pages. Vanilla JS + Leaflet. */
(() => {
  const PAGE = window.PAGE;
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const LS_A = 'spraulron.assumptions.v1';
  const LS_F = `spraulron.filters.${PAGE.kind}.v1`;
  const state = { rows: [], sources: [], rates: {}, warnings: [], assumptions: {}, filters: {}, sort: null, view: 'table', open: new Set(), map: null, layer: null, loading: false, rentTable: null };

  // ---------- formatting
  const money = (n, dec = 0) => (n == null || isNaN(n)) ? '—' : (n < 0 ? '−' : '') + '$' + Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: dec, minimumFractionDigits: dec });
  const num = (n) => (n == null || isNaN(n)) ? '—' : Number(n).toLocaleString('en-US');
  const pct = (n, dec = 2) => (n == null || isNaN(n)) ? '—' : (n * 100).toFixed(dec) + '%';
  const fdate = (iso) => { if (!iso) return '—'; const d = new Date(iso + 'T00:00:00'); return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }); };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const age = (sec) => sec == null ? '' : sec < 90 ? 'just now' : sec < 5400 ? `${Math.round(sec / 60)} min ago` : sec < 172800 ? `${Math.round(sec / 3600)} h ago` : `${Math.round(sec / 86400)} d ago`;
  const todayISO = () => new Date().toISOString().slice(0, 10);
  const plusDays = (n) => { const d = new Date(); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };

  // ---------- persistence
  const loadJSON = (k, def) => { try { return Object.assign({}, def, JSON.parse(localStorage.getItem(k) || '{}')); } catch { return def; } };
  const saveJSON = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { } };

  // ---------- assumptions panel
  const defaultAssumptions = () => ({ rate_pct: '', dpa: false, down_pct_pct: PAGE.fha.down_payment_pct * 100, closing_pct_pct: PAGE.fha.closing_cost_pct * 100, reserve_pct_pct: PAGE.fha.vacancy_repair_reserve_pct * 100, rent_factor: PAGE.fha.market_rent_factor, target: PAGE.fha.target_cashflow, tax_rate_pct: '', insurance: '', utilities: '' });
  function assumptionParams() {
    const a = state.assumptions, p = new URLSearchParams();
    if (a.rate_pct !== '' && a.rate_pct != null) p.set('rate', Number(a.rate_pct) / 100);
    if (a.dpa) p.set('dpa', '1');
    if (a.down_pct_pct !== '') p.set('down_pct', Number(a.down_pct_pct) / 100);
    if (a.closing_pct_pct !== '') p.set('closing_pct', Number(a.closing_pct_pct) / 100);
    if (a.reserve_pct_pct !== '') p.set('reserve_pct', Number(a.reserve_pct_pct) / 100);
    if (a.rent_factor !== '') p.set('rent_factor', a.rent_factor);
    if (a.target !== '') p.set('target', a.target);
    if (a.tax_rate_pct !== '' && a.tax_rate_pct != null) p.set('tax_rate', Number(a.tax_rate_pct) / 100);
    if (a.insurance !== '' && a.insurance != null) p.set('insurance', a.insurance);
    if (a.utilities !== '' && a.utilities != null) p.set('utilities', a.utilities);
    return p;
  }
  function bindAssumptions() {
    state.assumptions = loadJSON(LS_A, defaultAssumptions());
    $$('[data-assume]').forEach(el => {
      const k = el.dataset.assume;
      if (el.type === 'checkbox') el.checked = !!state.assumptions[k]; else el.value = state.assumptions[k] ?? '';
      el.addEventListener('input', () => { state.assumptions[k] = el.type === 'checkbox' ? el.checked : el.value; saveJSON(LS_A, state.assumptions); debouncedLoad(); });
    });
    $('#btn-reset-assumptions').addEventListener('click', () => { state.assumptions = defaultAssumptions(); saveJSON(LS_A, state.assumptions); bindValues(); load(); });
    function bindValues() { $$('[data-assume]').forEach(el => { const k = el.dataset.assume; if (el.type === 'checkbox') el.checked = !!state.assumptions[k]; else el.value = state.assumptions[k] ?? ''; }); }
  }

  // ---------- filters
  function readFilters() {
    const f = {};
    $$('[data-filter]').forEach(el => {
      const k = el.dataset.filter;
      if (el.type === 'checkbox' && el.value && el.value !== 'on') { (f[k] = f[k] || []); if (el.checked) f[k].push(el.value); }
      else if (el.type === 'checkbox') f[k] = el.checked;
      else f[k] = el.value;
    });
    state.filters = f; saveJSON(LS_F, f);
  }
  function bindFilters() {
    const saved = loadJSON(LS_F, {});
    if (PAGE.kind === 'auctions') { saved.date_from = saved.date_from || todayISO(); saved.date_to = saved.date_to || plusDays(PAGE.lookahead_days || 75); }
    $$('[data-filter]').forEach(el => {
      const k = el.dataset.filter;
      if (k in saved) {
        if (el.type === 'checkbox' && el.value && el.value !== 'on') el.checked = saved[k].includes(el.value);
        else if (el.type === 'checkbox') el.checked = !!saved[k];
        else el.value = saved[k];
      }
      el.addEventListener('input', () => { readFilters(); if (k === 'include_completed') load(); else render(); });
    });
    readFilters();
  }

  // ---------- data
  let debounceT = null;
  const debouncedLoad = () => { clearTimeout(debounceT); debounceT = setTimeout(() => load(), 450); };
  async function load({ refresh = false } = {}) {
    state.loading = true; renderSources();
    const p = assumptionParams();
    if (refresh) p.set('refresh', '1');
    if (PAGE.kind === 'auctions' && state.filters.include_completed) p.set('include_completed', '1');
    try {
      const r = await fetch(`/api/${PAGE.kind}?${p.toString()}`);
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      const data = await r.json();
      state.rows = data.rows || []; state.sources = data.sources || []; state.rates = data.rates || {}; state.warnings = data.warnings || []; state.rentTable = data.rent_table || null;
      $('#rate-note').textContent = data.rates?.fha_no_dpa ? `· OHFA FHA ${(data.assumptions.rate * 100).toFixed(3)}%${data.assumptions.with_dpa ? ' (with DPA)' : ''} · updated ${data.rates.updated || 'n/a'}` : '';
    } catch (e) {
      state.warnings = [`Could not load data: ${e.message}`];
    }
    state.loading = false;
    renderSources(); render();
  }
  async function refreshSource(key, btn) {
    btn.disabled = true; btn.textContent = '…';
    try { await fetch(`/api/refresh/${key}`, { method: 'POST' }); } catch { }
    await load();
  }

  // ---------- rendering: sources + warnings
  function renderSources() {
    const el = $('#sources');
    if (state.loading && !state.sources.length) { el.innerHTML = '<span class="src loading"><span class="dot"></span> loading sources…</span>'; return; }
    el.innerHTML = state.sources.map(s => {
      const cls = s.error ? 'error' : s.stale ? 'stale' : '';
      const title = [s.error ? `Error: ${s.error}` : '', s.fetched_at ? `fetched ${s.fetched_at}` : '', `TTL ${Math.round(s.ttl_seconds / 3600)} h`].filter(Boolean).join('\n');
      return `<span class="src ${cls} ${state.loading ? 'loading' : ''}" title="${esc(title)}"><span class="dot"></span>${esc(s.label)}${s.count != null ? ` <b>${s.count}</b>` : ''} <span class="muted">${age(s.age_seconds)}</span><button data-refresh="${esc(s.key)}" title="re-fetch now">⟳</button></span>`;
    }).join('');
    $$('[data-refresh]', el).forEach(b => b.addEventListener('click', () => refreshSource(b.dataset.refresh, b)));
    $('#warnings').innerHTML = state.warnings.map(w => `<div class="warn-item">${esc(w)}</div>`).join('');
  }

  // ---------- filtering + sorting
  function filtered() {
    const f = state.filters, q = (f.q || '').toLowerCase().trim();
    return state.rows.filter(r => {
      if (f.county && !f.county.includes(r.county)) return false;
      if (q) { const hay = [r.address, r.city, r.zip, r.parcel, r.case_number, r.description, r.notes, r.sale_type].join(' ').toLowerCase(); if (!hay.includes(q)) return false; }
      if (PAGE.kind === 'market') {
        if (r.hidden && !f.show_hidden) return false;
        if (f.hide_pending && /pending|contingent/i.test(r.status || '')) return false;
        if (f.starred && !r.starred) return false;
        if (f.max_price && r.price && r.price > Number(f.max_price)) return false;
        if (f.min_price && r.price && r.price < Number(f.min_price)) return false;
        if (f.has_photo && !r.photo) return false;
        if (f.units) { const u = r.units_final; const key = !u ? '?' : u >= 5 ? '5+' : String(u); if (!f.units.includes(key)) return false; }
        if (f.min_cashflow !== '' && f.min_cashflow != null && r.fha && r.fha.cash_flow != null && r.fha.cash_flow < Number(f.min_cashflow)) return false;
      } else {
        if (f.sale_type && !f.sale_type.includes(r.sale_type)) return false;
        if (f.hide_vacant && r.is_vacant_lot) return false;
        if (f.hide_closed && /closed|cancel/i.test(r.status || '')) return false;
        if (f.max_bid && r.opening_bid && r.opening_bid > Number(f.max_bid)) return false;
        if (r.sale_date) { if (f.date_from && r.sale_date < f.date_from) return false; if (f.date_to && r.sale_date > f.date_to) return false; }
        else if (!f.include_undated) return false;
      }
      return true;
    });
  }
  const getters = {
    price: r => r.price, units: r => r.units_final, beds: r => r.beds, sqft: r => r.sqft, year: r => r.year_built, dom: r => r.days_on_market,
    rent: r => r.tenant_rent, piti: r => r.fha?.piti, cash: r => r.fha?.cash_flow, total_cash: r => r.fha?.total_cash_needed, breakeven: r => r.fha?.break_even_price,
    ratio: r => (r.tenant_rent && r.price) ? r.tenant_rent / r.price : null, address: r => r.address, county: r => r.county, city: r => r.city, star: r => r.starred ? 1 : 0,
    type: r => r.sale_type, date: r => r.sale_date || '9999', status: r => r.status, appraised: r => r.appraised, bid: r => r.opening_bid, deposit: r => r.deposit,
    bidratio: r => (r.opening_bid && r.appraised) ? r.opening_bid / r.appraised : null, source: r => r.source_label, parcel: r => r.parcel,
  };
  function sorted(rows) {
    const s = state.sort || (PAGE.kind === 'market' ? { key: 'cash', dir: -1 } : { key: 'date', dir: 1 });
    const g = getters[s.key] || (() => null);
    return rows.slice().sort((a, b) => {
      const va = g(a), vb = g(b);
      if (va == null && vb == null) return 0; if (va == null) return 1; if (vb == null) return -1;
      return (va < vb ? -1 : va > vb ? 1 : 0) * s.dir;
    });
  }

  // ---------- rendering: summary
  function renderSummary(rows) {
    const el = $('#summary');
    if (PAGE.kind === 'market') {
      const pos = rows.filter(r => r.fha?.cash_flow != null && r.fha.cash_flow >= 0).length;
      const unknown = rows.filter(r => !r.units_final).length;
      const med = rows.map(r => r.price).filter(Boolean).sort((a, b) => a - b); const m = med.length ? med[Math.floor(med.length / 2)] : null;
      el.innerHTML = `<span><b>${rows.length}</b> listings shown of ${state.rows.length}</span><span><b class="pos">${pos}</b> cash-flow positive (model)</span><span><b>${unknown}</b> units unknown</span><span>median price <b>${money(m)}</b></span>${state.rentTable ? `<span class="muted">rents: ${esc(state.rentTable.source)}${state.rentTable.year ? ' FY' + state.rentTable.year : ''}</span>` : ''}`;
    } else {
      const byType = {}; rows.forEach(r => { byType[r.sale_type] = (byType[r.sale_type] || 0) + 1; });
      const bids = rows.map(r => r.opening_bid).filter(Boolean).sort((a, b) => a - b); const m = bids.length ? bids[Math.floor(bids.length / 2)] : null;
      el.innerHTML = `<span><b>${rows.length}</b> items shown of ${state.rows.length}</span>` + Object.entries(byType).map(([k, v]) => `<span>${esc(k)} <b>${v}</b></span>`).join('') + `<span>median opening bid <b>${money(m)}</b></span>`;
    }
  }

  // ---------- rendering: tables
  const MARKET_COLS = [
    ['star', '★', ''], ['photo', '', ''], ['address', 'Address', ''], ['price', 'Price', 'num'], ['units', 'Units', 'num'], ['beds', 'Bd/Ba', ''], ['sqft', 'Sqft', 'num'], ['year', 'Yr', 'num'], ['dom', 'DOM', 'num'],
    ['rent', 'Tenant rent/mo', 'num'], ['piti', 'PITI', 'num'], ['cash', 'Cash flow', 'num'], ['total_cash', 'Cash needed', 'num'], ['breakeven', 'Break-even price', 'num'], ['ratio', 'Rent ÷ price', 'num'], ['flags', 'Flags', ''],
  ];
  const AUCTION_COLS = [
    ['county', 'County', ''], ['type', 'Type', ''], ['date', 'Sale date', ''], ['status', 'Status', ''], ['address', 'Address', ''], ['parcel', 'Parcel', ''], ['appraised', 'Appraised', 'num'], ['bid', 'Opening bid', 'num'], ['deposit', 'Deposit', 'num'], ['bidratio', 'Bid ÷ appr.', 'num'], ['flags', 'Flags', ''], ['source', 'Source', ''],
  ];
  function cell(r, key) {
    const q = r.fha;
    switch (key) {
      case 'star': return `<span class="star ${r.starred ? 'on' : ''}" data-star="${esc(r.id)}" title="star">★</span>`;
      case 'photo': return r.photo ? `<img class="thumb" loading="lazy" referrerpolicy="no-referrer" src="${esc(r.photo)}" alt="">` : `<a class="thumb" href="${esc(r.links?.google_maps || '#')}" target="_blank" rel="noopener" title="map"></a>`;
      case 'address': return `<div class="addr"><a href="${esc(r.links?.source || r.url || r.links?.zillow || '#')}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(r.address)}</a><div class="sub">${esc([r.city, r.zip].filter(Boolean).join(' '))} · ${esc(r.county || '')}${PAGE.kind === 'market' ? ` · <span class="badge">${esc(r.source_label)}</span>${r.status && !/for_sale/i.test(r.status) ? `<span class="badge warn">${esc(r.status.toLowerCase())}</span>` : ''}` : ''}</div></div>`;
      case 'price': return money(r.price);
      case 'units': return r.units_final ? `<span class="badge ${r.units_source === 'override' ? 'ok' : r.units_source === 'text' ? 'warn' : ''}" title="units from ${esc(r.units_source)}">${r.units_final}</span>` : '<span class="badge bad">?</span>';
      case 'beds': return `${r.beds ?? '—'} / ${r.baths ?? '—'}`;
      case 'sqft': return num(r.sqft); case 'year': return r.year_built || '—'; case 'dom': return r.days_on_market ?? '—';
      case 'rent': return r.tenant_rent != null ? money(r.tenant_rent) : '—';
      case 'piti': return q ? money(q.piti) : '—';
      case 'cash': return q && q.cash_flow != null ? `<span class="${q.cash_flow >= 0 ? 'pos' : 'neg'}">${money(q.cash_flow)}</span>` : '—';
      case 'total_cash': return q ? money(q.total_cash_needed) : '—';
      case 'breakeven': return q && q.break_even_price != null ? money(q.break_even_price) : '—';
      case 'ratio': { const v = getters.ratio(r); return v != null ? pct(v, 2) : '—'; }
      case 'flags': { const fl = (r.flags || []).slice(); if (r.is_vacant_lot) fl.push('vacant lot'); if (r.photo && PAGE.kind === 'auctions') fl.push('photo'); return fl.map(f => `<span class="badge ${/fails|unknown|exceeds|vacant/.test(f) ? 'bad' : /renovation|mixed|truncated/.test(f) ? 'warn' : ''}">${esc(f)}</span>`).join(''); }
      case 'county': return esc(r.county); case 'type': return esc(r.sale_type); case 'status': return esc(r.status || '—'); case 'source': return `<span class="badge">${esc(r.source_label)}</span>`;
      case 'date': return r.sale_date ? `${fdate(r.sale_date)}${r.sale_time ? `<div class="sub muted">${esc(r.sale_time)}</div>` : ''}` : '<span class="muted">—</span>';
      case 'parcel': return esc(r.parcel || '—');
      case 'appraised': return r.appraised ? `${money(r.appraised)}${r.appraised_label && r.appraised_label !== 'Appraised' ? `<div class="sub muted">${esc(r.appraised_label)}</div>` : ''}` : '—';
      case 'bid': return money(r.opening_bid); case 'deposit': return money(r.deposit);
      case 'bidratio': { const v = getters.bidratio(r); return v != null ? pct(v, 0) : '—'; }
    }
    return '';
  }
  function renderTable(rows) {
    const cols = PAGE.kind === 'market' ? MARKET_COLS : AUCTION_COLS;
    const s = state.sort || (PAGE.kind === 'market' ? { key: 'cash', dir: -1 } : { key: 'date', dir: 1 });
    const head = cols.map(([k, label, cls]) => `<th class="${cls} ${s.key === k ? 'sorted' : ''}" data-sort="${k}">${label}${s.key === k ? (s.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('');
    const body = rows.map(r => `<tr class="row ${state.open.has(r.id) ? 'open' : ''}" data-id="${esc(r.id)}">${cols.map(([k, , cls]) => `<td class="${cls} ${k === 'address' ? 'addr' : ''}">${cell(r, k)}</td>`).join('')}</tr>${state.open.has(r.id) ? `<tr class="detail"><td colspan="${cols.length}">${detail(r)}</td></tr>` : ''}`).join('');
    $('#results').innerHTML = rows.length ? `<table class="grid"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>` : `<div class="empty">Nothing matches the current filters${state.loading ? ' (loading…)' : ''}.</div>`;
    $$('th[data-sort]').forEach(th => th.addEventListener('click', () => { const k = th.dataset.sort; if (k === 'flags' || k === 'photo') return; state.sort = (state.sort?.key === k) ? { key: k, dir: -state.sort.dir } : { key: k, dir: ['address', 'county', 'type', 'date', 'status', 'source', 'parcel', 'city'].includes(k) ? 1 : -1 }; render(); }));
    bindRowEvents();
  }
  function renderCards(rows) {
    $('#results').innerHTML = rows.length ? `<div class="cards">${rows.map(r => `<div class="card" data-id="${esc(r.id)}">${r.photo ? `<img loading="lazy" referrerpolicy="no-referrer" src="${esc(r.photo)}" alt="">` : '<div class="thumb" style="width:100%;height:160px"></div>'}<div class="body"><div class="price">${money(r.price)} <span class="badge">${r.units_final ? r.units_final + ' units' : 'units ?'}</span></div><div>${esc(r.address)}, ${esc(r.city || '')}</div><div class="muted">${r.beds ?? '—'} bd · ${r.baths ?? '—'} ba · ${num(r.sqft)} sqft · ${r.year_built || '—'}</div><div>tenant rent ${money(r.tenant_rent)} · PITI ${money(r.fha?.piti)} · cash flow ${r.fha?.cash_flow != null ? `<span class="${r.fha.cash_flow >= 0 ? 'pos' : 'neg'}">${money(r.fha.cash_flow)}</span>` : '—'}</div></div>${state.open.has(r.id) ? `<div class="body">${detail(r)}</div>` : ''}</div>`).join('')}</div>` : '<div class="empty">Nothing matches the current filters.</div>';
    bindRowEvents();
  }
  function bindRowEvents() {
    $$('[data-id].row, [data-id].card').forEach(el => el.addEventListener('click', (ev) => {
      if (ev.target.closest('a, button, input, textarea, select, .star, iframe')) return;
      const id = el.dataset.id; state.open.has(id) ? state.open.delete(id) : state.open.add(id); render();
      const d = $(`[data-id="${CSS.escape(id)}"]`); if (d) d.scrollIntoView({ block: 'nearest' });
    }));
    $$('[data-star]').forEach(el => el.addEventListener('click', async (ev) => { ev.stopPropagation(); const r = state.rows.find(x => x.id === el.dataset.star); await putOverride(r.id, { starred: !r.starred }); }));
    $$('[data-edit]').forEach(el => el.addEventListener('click', (ev) => { ev.stopPropagation(); openOverride(state.rows.find(x => x.id === el.dataset.edit)); }));
    $$('[data-hide]').forEach(el => el.addEventListener('click', async (ev) => { ev.stopPropagation(); const r = state.rows.find(x => x.id === el.dataset.hide); await putOverride(r.id, { hidden: !r.hidden }); }));
    $$('iframe[data-src]').forEach(f => { f.src = f.dataset.src; f.removeAttribute('data-src'); });
  }

  // ---------- rendering: detail blocks
  function detail(r) {
    const links = Object.entries(r.links || {}).filter(([k]) => k !== 'map_embed').map(([k, v]) => `<a href="${esc(v)}" target="_blank" rel="noopener">${esc(k.replace('_', ' '))}</a>`).join('');
    if (PAGE.kind === 'market') {
      const q = r.fha;
      const fhaTable = q ? `<table class="kv">
        <tr><td>Down payment (${pct(q.down_payment_pct, 1)})</td><td>${money(q.down_payment)}</td></tr>
        <tr><td>Loan incl. UFMIP</td><td>${money(q.loan_amount)}</td></tr>
        <tr><td>Principal & interest @ ${pct(q.rate, 3)}</td><td>${money(q.principal_interest)}</td></tr>
        <tr><td>FHA mortgage insurance</td><td>${money(q.mip_monthly)}</td></tr>
        <tr><td>Property tax (${pct(q.tax_rate_used, 2)}/yr)</td><td>${money(q.tax_monthly)}</td></tr>
        <tr><td>Building insurance</td><td>${money(q.insurance_monthly)}</td></tr>
        <tr class="total"><td>PITI</td><td>${money(q.piti)}</td></tr>
        <tr><td>Shared utilities</td><td>${money(q.utilities_monthly)}</td></tr>
        <tr><td>Vacancy + repair reserve</td><td>${money(q.reserve_allowance)}</td></tr>
        <tr><td>Tenant rent</td><td>${money(q.tenant_rent)}</td></tr>
        <tr class="total"><td>Monthly cash flow</td><td class="${(q.cash_flow ?? 0) >= 0 ? 'pos' : 'neg'}">${money(q.cash_flow)}</td></tr>
        <tr><td>Closing costs</td><td>${money(q.closing_costs)}</td></tr>
        <tr><td>Required reserves after closing</td><td>${money(q.reserves_required)}</td></tr>
        <tr class="total"><td>Total cash needed</td><td>${money(q.total_cash_needed)}</td></tr>
        <tr><td>Break-even purchase price</td><td>${money(q.break_even_price)}</td></tr>
        <tr><td>Price for ${money(q.target_cashflow)}/mo</td><td>${money(q.target_price)}</td></tr>
        <tr><td>Self-sufficiency (75% of ${money(q.all_units_rent)} ≥ PITI)</td><td>${q.self_sufficiency_pass == null ? 'n/a (2 units)' : q.self_sufficiency_pass ? '<span class="pos">pass</span>' : '<span class="neg">fail</span>'}</td></tr>
        <tr><td>Within FHA ${r.units_final || 1}-unit limit</td><td>${q.within_loan_limit == null ? '—' : q.within_loan_limit ? 'yes' : '<span class="neg">no</span>'}</td></tr></table>` : '<div class="muted">No price on this listing.</div>';
      const units = (r.unit_list || []).length ? `<table class="kv">${r.unit_list.map((u, i) => `<tr><td>Unit ${i + 1} · ${u.bedrooms ?? '?'} bd ${u.owner_occupied ? '<span class="badge ok">you live here</span>' : ''}<div class="muted small">${esc(u.rent_source)}</div></td><td>${money(u.rent)}</td></tr>`).join('')}<tr class="total"><td>All units</td><td>${money(r.all_units_rent)}</td></tr></table>` : '<div class="muted">Units unknown: set the unit count to estimate rents.</div>';
      const photos = (r.photos || []).slice(0, 12).map(p => `<a href="${esc(p)}" target="_blank" rel="noopener"><img loading="lazy" referrerpolicy="no-referrer" src="${esc(p)}" alt=""></a>`).join('');
      return `<div class="detail-grid">
        <div><h4>FHA scenario</h4>${fhaTable}</div>
        <div><h4>Rent per unit <button class="btn tiny" data-edit="${esc(r.id)}">✎ edit units / rents / notes</button> <button class="btn tiny" data-hide="${esc(r.id)}">${r.hidden ? 'unhide' : 'hide'}</button></h4>${units}${r.notes ? `<p class="small"><b>Notes:</b> ${esc(r.notes)}</p>` : ''}
          <p class="small muted">${esc([r.property_type, r.status, r.mls ? `${r.mls} #${r.mls_id || ''}` : '', r.agent ? `agent ${r.agent}` : '', r.office].filter(Boolean).join(' · '))}${r.tax_annual ? ` · listed tax ${money(r.tax_annual)}/yr` : ''}${r.assessed_value ? ` · assessed ${money(r.assessed_value)}` : ''}</p></div>
        <div><h4>Links</h4><div class="links">${links}</div>${photos ? `<h4>Photos (from source)</h4><div class="photos">${photos}</div>` : ''}${r.description ? `<h4>Listing text</h4><div class="desc">${esc(r.description)}</div>` : ''}</div>
      </div>`;
    }
    const c = r.cash || {}, f = r.fha_comparison;
    return `<div class="detail-grid">
      <div><h4>Cash reality</h4><table class="kv">
        <tr><td>Opening / minimum bid</td><td>${money(r.opening_bid)}</td></tr>
        <tr><td>Deposit at sale</td><td>${money(c.deposit)}</td></tr>
        <tr><td>Balance due (≈${c.balance_days} days after confirmation)</td><td>${money(c.balance_due)}</td></tr>
        <tr><td>${esc(r.appraised_label || 'Appraised')}</td><td>${money(r.appraised)}</td></tr>
        ${r.high_bid ? `<tr><td>High bid</td><td>${money(r.high_bid)}</td></tr>` : ''}
        <tr><td>Case</td><td>${esc(r.case_number || '—')}</td></tr></table>
        <ul class="small muted">${(c.notes || []).map(n => `<li>${esc(n)}</li>`).join('')}</ul>${r.notes ? `<p class="small">${esc(r.notes)}</p>` : ''}</div>
      <div><h4>If this bid were financed (comparison only)</h4>${f ? `<table class="kv"><tr><td>Price</td><td>${money(f.price)}</td></tr><tr><td>FHA down (3.5%)</td><td>${money(f.down_payment)}</td></tr><tr><td>P&I @ ${pct(f.rate, 3)}</td><td>${money(f.principal_interest)}</td></tr><tr class="total"><td>PITI</td><td>${money(f.piti)}</td></tr><tr><td>Cash to close</td><td>${money(f.cash_to_close)}</td></tr></table><p class="small muted">${esc(f.note)}</p>` : '<div class="muted">No bid amount.</div>'}
        <h4>Links</h4><div class="links">${links}</div>${r.photo ? `<img loading="lazy" referrerpolicy="no-referrer" src="${esc(r.photo)}" style="max-width:100%;border-radius:6px" alt="">` : ''}</div>
      <div><h4>Map</h4>${r.links?.map_embed ? `<iframe class="embed" loading="lazy" data-src="${esc(r.links.map_embed)}" referrerpolicy="no-referrer-when-downgrade"></iframe>` : ''}</div>
    </div>`;
  }

  // ---------- overrides
  async function putOverride(id, body) {
    await fetch(`/api/overrides/${encodeURIComponent(id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    await load();
  }
  function openOverride(r) {
    const dlg = $('#override-dialog');
    const n = r.override?.units || r.units_final || 2;
    const rowsHtml = (units) => Array.from({ length: units }, (_, i) => { const u = r.unit_list?.[i] || {}; return `<span>Unit ${i + 1}</span><input type="number" min="0" max="6" name="beds${i}" placeholder="bd" value="${r.override?.unit_beds?.[i] ?? u.bedrooms ?? ''}"><input type="number" min="0" step="25" name="rent${i}" placeholder="rent $/mo (blank = estimate)" value="${r.override?.rents?.[i] ?? ''}">`; }).join('');
    dlg.innerHTML = `<h3>${esc(r.address)}</h3><form method="dialog">
      <label>Number of units <input type="number" name="units" min="1" max="12" value="${n}"></label>
      <div class="units-editor" id="units-editor"><span></span><b>bedrooms</b><b>actual rent</b>${rowsHtml(n)}</div>
      <label>Notes <textarea name="notes" rows="3">${esc(r.notes || '')}</textarea></label>
      <div class="dialog-actions"><button class="btn" value="clear">clear overrides</button><button class="btn" value="cancel">cancel</button><button class="btn primary" value="save">save</button></div></form>`;
    const form = $('form', dlg), unitsInput = $('[name=units]', form);
    unitsInput.addEventListener('input', () => { $('#units-editor', form).innerHTML = `<span></span><b>bedrooms</b><b>actual rent</b>${rowsHtml(Number(unitsInput.value) || 1)}`; });
    form.addEventListener('submit', async (ev) => {
      const action = ev.submitter?.value;
      if (action === 'cancel') return;
      if (action === 'clear') { await fetch(`/api/overrides/${encodeURIComponent(r.id)}`, { method: 'DELETE' }); await load(); return; }
      const units = Number(unitsInput.value) || null;
      const beds = [], rents = [];
      for (let i = 0; i < (units || 0); i++) { beds.push($(`[name=beds${i}]`, form)?.value || null); rents.push($(`[name=rent${i}]`, form)?.value || null); }
      await putOverride(r.id, { units, unit_beds: beds.some(b => b) ? beds : null, rents: rents.some(x => x) ? rents : null, notes: $('[name=notes]', form).value || null });
    });
    dlg.showModal();
  }

  // ---------- map
  function initMap() {
    state.map = L.map('map', { scrollWheelZoom: false }).setView(PAGE.map.center, PAGE.map.zoom);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(state.map);
    state.layer = L.layerGroup().addTo(state.map);
    $('#btn-map-toggle').addEventListener('click', () => { const w = $('.map-wrap'); w.classList.toggle('collapsed'); $('#btn-map-toggle').textContent = w.classList.contains('collapsed') ? 'show map' : 'hide map'; if (!w.classList.contains('collapsed')) setTimeout(() => state.map.invalidateSize(), 50); });
  }
  const TYPE_COLORS = { 'Delinquent Tax': '#d9480f', 'Mortgage Foreclosure': '#1f6feb', 'Forfeited Land': '#6f42c1', 'Land Bank': '#1a7f37', 'Unknown': '#777' };
  function renderMap(rows) {
    if (!state.map) return;
    state.layer.clearLayers();
    const pts = [];
    rows.forEach(r => {
      if (r.lat == null || r.lon == null) return;
      const color = PAGE.kind === 'market' ? (r.fha?.cash_flow == null ? '#777' : r.fha.cash_flow >= 0 ? '#1a7f37' : '#c62828') : (TYPE_COLORS[r.sale_type] || '#777');
      const m = L.circleMarker([r.lat, r.lon], { radius: 7, color, weight: 1.5, fillColor: color, fillOpacity: .75 });
      const label = PAGE.kind === 'market' ? `${money(r.price)} · ${r.units_final ? r.units_final + ' units' : 'units ?'} · cash flow ${money(r.fha?.cash_flow)}` : `${esc(r.sale_type)} · ${fdate(r.sale_date)} · bid ${money(r.opening_bid)}`;
      m.bindPopup(`<b>${esc(r.address)}</b><br>${esc(r.city || '')} · ${esc(r.county)}<br>${label}<br><a href="#" data-goto="${esc(r.id)}">open row</a>`);
      m.on('popupopen', (e) => { const a = e.popup.getElement().querySelector('[data-goto]'); if (a) a.addEventListener('click', (ev) => { ev.preventDefault(); state.open.add(r.id); render(); const d = $(`[data-id="${CSS.escape(r.id)}"]`); if (d) d.scrollIntoView({ block: 'center' }); }); });
      state.layer.addLayer(m); pts.push([r.lat, r.lon]);
    });
    if (pts.length && !state._fitted) { state.map.fitBounds(pts, { padding: [20, 20], maxZoom: 12 }); state._fitted = true; }
  }

  // ---------- main render
  function render() {
    const rows = sorted(filtered());
    renderSummary(rows);
    if (PAGE.kind === 'market' && state.view === 'cards') renderCards(rows); else renderTable(rows);
    renderMap(rows);
  }

  // ---------- boot
  bindAssumptions(); bindFilters(); initMap();
  $$('[data-view]').forEach(b => b.addEventListener('click', () => { state.view = b.dataset.view; $$('[data-view]').forEach(x => x.classList.toggle('active', x === b)); render(); }));
  $('#btn-refresh-all').addEventListener('click', () => { if (PAGE.kind === 'market' && PAGE.has_rentcast && !confirm('This also spends one RentCast API request. Continue?')) return; load({ refresh: true }); });
  load();
})();
