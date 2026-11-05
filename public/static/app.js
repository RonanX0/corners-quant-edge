/* Corners Quant Edge v4 — browser controller (no external libraries). */
'use strict';

// ------------------------------------------------------------------ utils
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const nf = (n, d = 2) => (n === null || n === undefined || Number.isNaN(n)) ? '—' : Number(n).toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (n, d = 1) => (n === null || n === undefined) ? '—' : nf(n * 100, d) + '%';
const sgn = (n, d = 1) => (n === null || n === undefined) ? '—' : (n > 0 ? '+' : '') + nf(n * 100, d) + '%';
const cls = (n) => n === null || n === undefined ? '' : n > 0 ? 'pos' : n < 0 ? 'neg' : '';
const fmtTime = (iso) => { if (!iso) return '—'; const d = new Date(iso); return isNaN(d) ? esc(iso) : d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }); };
const ago = (iso) => { if (!iso) return ''; const m = (Date.now() - new Date(iso)) / 60000; if (m < 1) return 'agora'; if (m < 60) return `há ${Math.round(m)} min`; if (m < 1440) return `há ${Math.round(m / 60)} h`; return `há ${Math.round(m / 1440)} d`; };
const today = () => new Date().toISOString().slice(0, 10);
const addDays = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const CLASS_CSS = { 'VALOR POTENCIAL': 'c-valor', 'AGUARDAR CONFIRMAÇÃO': 'c-aguardar', 'SEM VANTAGEM': 'c-sem', 'DADOS INSUFICIENTES': 'c-dados' };
const tag = (c) => `<span class="tag ${CLASS_CSS[c] || ''}">${esc(c)}</span>`;

const store = {
  get(k, def) { try { const v = localStorage.getItem('cqe4_' + k); return v === null ? def : JSON.parse(v); } catch { return def; } },
  set(k, v) { try { localStorage.setItem('cqe4_' + k, JSON.stringify(v)); return true; } catch { toast('O navegador não conseguiu salvar localmente (espaço ou modo privado).', true); return false; } },
};

let toastT;
function toast(msg, bad = false) { const t = $('toast'); t.textContent = msg; t.className = 'toast show' + (bad ? ' err' : ''); clearTimeout(toastT); toastT = setTimeout(() => t.className = 'toast', 5000); }

async function api(path, body, method) {
  const opts = { method: method || (body ? 'POST' : 'GET'), headers: {} };
  if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const tok = store.get('token', '');
  if (tok) opts.headers.Authorization = 'Bearer ' + tok;
  let r;
  try { r = await fetch(path, opts); } catch { throw Error('Sem conexão com a API.'); }
  let j = null;
  try { j = await r.json(); } catch { if (!r.ok) throw Error(`Erro ${r.status} no servidor.`); }
  if (!r.ok) { const d = j && j.detail; throw Object.assign(Error(typeof d === 'string' ? d : (d ? JSON.stringify(d) : `Erro ${r.status}`)), { status: r.status }); }
  return j;
}

// ------------------------------------------------------------------ state
const S = {
  status: null, dataset: null, radar: null, backtest: null, match: null,
  spec() {
    const src = store.get('source', 'football_data');
    const spec = { source: src, leagues: store.get('leagues', null) || (S.status ? S.status.default_leagues : ['E0', 'SP1', 'D1', 'I1', 'F1']), seasons: store.get('seasons', 3) };
    if (src === 'csv') spec.matches_csv = store.get('csv_matches', null);
    return spec;
  },
};

// ------------------------------------------------------------------ charts (SVG)
const tip = (() => { const d = document.createElement('div'); d.className = 'tooltip'; document.body.appendChild(d); return d; })();
function bindTips(root) {
  root.querySelectorAll('[data-tip]').forEach((el) => {
    el.addEventListener('mousemove', (e) => { tip.textContent = el.dataset.tip; tip.style.display = 'block'; tip.style.left = (e.clientX + 12) + 'px'; tip.style.top = (e.clientY - 28) + 'px'; });
    el.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
  });
}
function barChart(el, values, labels, opts = {}) {
  const W = 640, H = 200, P = { l: 34, r: 8, t: 10, b: 24 };
  const max = Math.max(...values, 1e-9) * 1.1, n = values.length, bw = (W - P.l - P.r) / n;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || 'gráfico de barras')}">`;
  for (let i = 0; i <= 4; i++) { const y = P.t + (H - P.t - P.b) * i / 4; s += `<line class="grid-l" x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}"/><text x="${P.l - 4}" y="${y + 3}" text-anchor="end">${opts.pct ? Math.round(max * (1 - i / 4) * 100) + '%' : nf(max * (1 - i / 4), 1)}</text>`; }
  values.forEach((v, i) => {
    const h = (H - P.t - P.b) * v / max, x = P.l + i * bw + 1.5, y = H - P.b - h;
    const hl = opts.highlight && opts.highlight(i) ? ' hl' : '';
    s += `<rect class="bar-r${hl}" x="${x}" y="${y}" width="${Math.max(bw - 3, 1)}" height="${Math.max(h, 0)}" data-tip="${esc(labels[i])}: ${opts.pct ? pct(v) : nf(v)}"/>`;
    if (n <= 24) s += `<text x="${x + bw / 2 - 1.5}" y="${H - 8}" text-anchor="middle">${esc(labels[i])}</text>`;
  });
  el.innerHTML = s + '</svg>'; bindTips(el);
}
function lineChart(el, pts, opts = {}) {
  if (!pts.length) { el.innerHTML = '<div class="empty">Sem dados para o gráfico.</div>'; return; }
  const W = 640, H = 200, P = { l: 48, r: 10, t: 10, b: 22 };
  const ys = pts.map((p) => p.y).concat(opts.zero !== undefined ? [opts.zero] : []);
  let lo = Math.min(...ys), hi = Math.max(...ys); if (hi === lo) { hi += 1; lo -= 1; }
  const X = (i) => P.l + (W - P.l - P.r) * (pts.length === 1 ? .5 : i / (pts.length - 1));
  const Y = (v) => P.t + (H - P.t - P.b) * (1 - (v - lo) / (hi - lo));
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opts.label || 'gráfico de linha')}">`;
  for (let i = 0; i <= 4; i++) { const v = lo + (hi - lo) * i / 4, y = Y(v); s += `<line class="grid-l" x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}"/><text x="${P.l - 4}" y="${y + 3}" text-anchor="end">${nf(v, opts.dec ?? 0)}</text>`; }
  if (opts.zero !== undefined) s += `<line class="zero" x1="${P.l}" x2="${W - P.r}" y1="${Y(opts.zero)}" y2="${Y(opts.zero)}"/>`;
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join('');
  if (opts.area) s += `<path class="area-neg" d="${d}L${X(pts.length - 1)},${Y(opts.zero ?? lo)}L${X(0)},${Y(opts.zero ?? lo)}Z"/>`;
  const last = pts[pts.length - 1].y, first = opts.zero ?? pts[0].y;
  s += `<path class="line-p ${opts.color === 'sign' ? (last >= first ? 'line-pos' : 'line-neg') : ''}" d="${d}"/>`;
  pts.forEach((p, i) => { s += `<circle class="pt" cx="${X(i)}" cy="${Y(p.y)}" r="${pts.length > 80 ? 1.2 : 2.6}" data-tip="${esc(p.label)}"/>`; });
  el.innerHTML = s + '</svg>'; bindTips(el);
}
function calibChart(el, bins) {
  const W = 300, H = 230, P = { l: 34, r: 10, t: 10, b: 26 };
  const X = (v) => P.l + (W - P.l - P.r) * v, Y = (v) => H - P.b - (H - P.t - P.b) * v;
  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="calibração">`;
  for (let i = 0; i <= 4; i++) { const v = i / 4; s += `<line class="grid-l" x1="${P.l}" x2="${W - P.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${P.l - 4}" y="${Y(v) + 3}" text-anchor="end">${v * 100}%</text><text x="${X(v)}" y="${H - 8}" text-anchor="middle">${v * 100}%</text>`; }
  s += `<line class="diag" x1="${X(0)}" y1="${Y(0)}" x2="${X(1)}" y2="${Y(1)}"/>`;
  bins.filter((b) => b.n > 0).forEach((b) => { s += `<circle class="pt" cx="${X(b.predicted)}" cy="${Y(b.observed)}" r="${Math.min(3 + Math.sqrt(b.n) / 3, 10)}" data-tip="${b.range}: previsto ${pct(b.predicted)}, observado ${pct(b.observed)}, n=${b.n}"/>`; });
  el.innerHTML = s + '</svg>'; bindTips(el);
}

// ------------------------------------------------------------------ navigation
const TITLES = { painel: 'Painel', radar: 'Radar de Oportunidades', confronto: 'Análise de confronto', backtest: 'Backtest científico', apostas: 'Gestão de apostas', dados: 'Dados e fontes' };
function show(view) {
  if (!TITLES[view]) view = 'painel';
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
  document.querySelectorAll('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $('viewTitle').textContent = TITLES[view];
  $('sidebar').classList.remove('open');
  if (location.hash !== '#' + view) history.replaceState(null, '', '#' + view);
  if (view === 'radar' && S.radar) renderRadar();
  else if (view !== 'painel') alerts([]);
  if (view === 'apostas') renderBets();
  if (!S.booting && view === 'backtest' && !S.backtest) runBacktest();
  if (!S.booting && view === 'radar' && !S.radar) runRadar();
  window.scrollTo({ top: 0 });
}

function alerts(list) {
  $('alerts').innerHTML = (list || []).map((a) => `<div class="alert ${a.kind || 'warn'}">${esc(a.text)}</div>`).join('');
}

// ------------------------------------------------------------------ status & dataset
const STATE_LABEL = { conectado: ['ok', 'conectado'], cache: ['ok', 'cache'], desconectado: ['off', 'desconectado'], erro: ['err', 'erro'], nao_consultado: ['off', 'não consultado'] };
function statePill(p) { const [c, t] = STATE_LABEL[p.state] || ['off', p.state]; return `<span class="pill ${c}">${t}</span>`; }

async function loadStatus() {
  S.status = await api('/api/v4/status');
  const odds = S.status.providers.find((p) => p.key === 'the_odds_api');
  const dbp = S.status.providers.find((p) => p.key === 'database');
  $('topStatus').innerHTML = `<span class="pill ${odds.configured ? 'ok' : 'off'}">odds: ${odds.configured ? 'API configurada' : 'desconectadas'}</span><span class="pill ${dbp.configured ? 'ok' : 'off'}">banco: ${dbp.configured ? 'ativo' : 'navegador'}</span><span class="pill">v${esc(S.status.version)}</span>`;
  renderProviders();
  return S.status;
}

function renderProviders() {
  if (!S.status) return;
  const rows = S.status.providers.map((p) => `<tr><td><strong>${esc(p.name)}</strong><div class="muted">${esc(p.kind)}</div></td><td>${statePill(p)}</td><td>${esc(p.message)}${p.credits_remaining ? `<div class="muted">créditos restantes: ${esc(p.credits_remaining)}</div>` : ''}</td><td class="num">${p.last_fetch ? fmtTime(p.last_fetch) + '<div class="muted">' + ago(p.last_fetch) + '</div>' : '—'}</td><td>${p.docs ? `<a href="${esc(p.docs)}" target="_blank" rel="noopener">documentação</a>` : ''}<div class="muted">${esc(p.terms || '')}</div></td></tr>`).join('');
  $('provTable').innerHTML = `<thead><tr><th>Fonte</th><th>Estado</th><th>Situação</th><th class="num">Última leitura</th><th>Termos</th></tr></thead><tbody>${rows}</tbody>`;
  $('dashSources').innerHTML = `<table class="tbl"><tbody>${S.status.providers.map((p) => `<tr><td>${esc(p.name)}</td><td>${statePill(p)}</td><td class="muted">${esc(p.message)}</td><td class="num muted">${p.last_fetch ? ago(p.last_fetch) : ''}</td></tr>`).join('')}</tbody></table>`;
  $('dbInfo').textContent = S.status.database
    ? (S.status.auth_configured ? 'Banco ativo. Informe o token de acesso para gravar e ler seus registros no servidor.' : 'Banco ativo, mas APP_ACCESS_TOKEN não está configurado: o servidor recusa dados privados.')
    : 'Banco não configurado (DATABASE_URL). Apostas e oportunidades ficam apenas neste navegador.';
}

async function loadDataset() {
  const spec = S.spec();
  if (spec.source === 'csv' && !spec.matches_csv) throw Error('Fonte CSV selecionada, mas nenhum arquivo de resultados foi importado (Dados e fontes).');
  S.dataset = await api('/api/v4/dataset', spec);
  const info = S.dataset.info;
  const b = $('sourceBadge');
  b.className = 'source-badge ' + (info.source === 'demo' ? 'demo' : info.live ? 'live' : '');
  b.textContent = info.source === 'demo' ? 'MODO DEMONSTRAÇÃO' : `${info.label} · ${S.dataset.count} jogos`;
  fillLeagueSelects();
  return S.dataset;
}

function fillLeagueSelects() {
  const leagues = S.dataset.leagues.map((l) => l.name);
  const keep = $('rLeague').value;
  $('rLeague').innerHTML = '<option value="">Todos</option>' + leagues.map((l) => `<option>${esc(l)}</option>`).join('');
  $('rLeague').value = leagues.includes(keep) ? keep : '';
  const mk = $('mLeague').value;
  $('mLeague').innerHTML = leagues.map((l) => `<option>${esc(l)}</option>`).join('');
  if (leagues.includes(mk)) $('mLeague').value = mk;
  fillTeams();
}
function fillTeams() {
  const lg = S.dataset && S.dataset.leagues.find((l) => l.name === $('mLeague').value);
  const teams = lg ? lg.teams : [];
  const h = $('mHome').value, a = $('mAway').value;
  $('mHome').innerHTML = teams.map((t) => `<option>${esc(t)}</option>`).join('');
  $('mAway').innerHTML = teams.map((t) => `<option>${esc(t)}</option>`).join('');
  if (teams.includes(h)) $('mHome').value = h;
  $('mAway').value = teams.includes(a) ? a : (teams[1] || '');
}

function fillStaticSelects() {
  const models = S.status.models;
  ['rModel', 'mModel'].forEach((id) => { $(id).innerHTML = '<option value="">Selecionado na validação</option>' + Object.entries(models).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join(''); });
  $('rLine').innerHTML = '<option value="">Todas oferecidas</option>' + S.status.reference_lines.map((l) => `<option value="${l}">${nf(l, 1)}</option>`).join('');
  $('bLine').innerHTML = S.status.reference_lines.map((l) => `<option value="${l}" ${l === 9.5 ? 'selected' : ''}>${nf(l, 1)}</option>`).join('');
  $('rClass').innerHTML = '<option value="">Todas</option>' + S.status.classes.map((c) => `<option>${esc(c)}</option>`).join('');
  const sel = new Set(store.get('leagues', null) || S.status.default_leagues);
  $('leagueBoxes').innerHTML = S.status.leagues.map((l) => `<label><input type="checkbox" value="${l.code}" ${sel.has(l.code) ? 'checked' : ''}/>${esc(l.label)}</label>`).join('');
  document.querySelectorAll('input[name=src]').forEach((r) => { r.checked = r.value === store.get('source', 'football_data'); });
  $('seasons').value = String(store.get('seasons', 3));
  $('fdOptions').style.display = store.get('source', 'football_data') === 'football_data' ? '' : 'none';
}

// ------------------------------------------------------------------ radar
function radarBody() {
  const num = (id) => { const v = parseFloat($(id).value); return Number.isFinite(v) ? v : undefined; };
  return {
    data: S.spec(), fixtures_csv: store.get('csv_fixtures', null), odds_csv: store.get('csv_odds', null), live_odds: true,
    model: $('rModel').value || null, line: $('rLine').value ? parseFloat($('rLine').value) : null,
    league: $('rLeague').value || null, date_from: $('rFrom').value || null, date_to: $('rTo').value || null,
    min_ev: (num('rMinEv') ?? 3) / 100, min_odd: num('rMinOdd') ?? 1.01, max_odd: num('rMaxOdd') ?? 100,
  };
}
let radarSort = { key: 'ev', dir: -1 };
async function runRadar() {
  $('rTable').innerHTML = '<tbody><tr><td class="loading">Calculando probabilidades e cruzando odds…</td></tr></tbody>';
  $('rRun').disabled = true;
  try {
    S.radar = await api('/api/v4/radar', radarBody());
    renderRadar(); renderDashboard();
  } catch (e) {
    $('rTable').innerHTML = `<tbody><tr><td class="neg">${esc(e.message)}</td></tr></tbody>`; toast(e.message, true);
  } finally { $('rRun').disabled = false; }
}
function radarRows() {
  if (!S.radar) return [];
  let rows = S.radar.rows.slice();
  const c = $('rClass').value; if (c) rows = rows.filter((r) => r.classification === c);
  if ($('rBest').checked) {
    const best = new Map();
    rows.forEach((r) => { const k = [r.date, r.home, r.away, r.line, r.side].join('|'); const b = best.get(k); if (!b || (r.odd || 0) > (b.odd || 0)) best.set(k, r); });
    rows = [...best.values()];
  }
  const { key, dir } = radarSort;
  rows.sort((a, b) => { const x = a[key], y = b[key]; if (x === null || x === undefined) return 1; if (y === null || y === undefined) return -1; return (x > y ? 1 : x < y ? -1 : 0) * dir; });
  return rows;
}
function renderRadar() {
  const R = S.radar; if (!R) return;
  $('rCounts').innerHTML = Object.entries(R.counts).map(([k, v]) => `<span class="tag ${CLASS_CSS[k]}">${esc(k)} · ${v}</span>`).join('');
  $('rMeta').textContent = `${R.fixtures} jogo(s) · ${R.odds_rows} cotação(ões) · modelo: ${R.model_name} · gerado ${fmtTime(R.generated_at)}`;
  const warn = (R.warnings || []).map((t) => ({ text: t }));
  if (!R.odds_status.configured) warn.unshift({ kind: 'info', text: 'Odds ao vivo desconectadas: configure THE_ODDS_API_KEY ou importe um CSV de odds. Nenhuma odd é simulada.' });
  alerts(warn);
  const rows = radarRows();
  const th = (k, t, n) => `<th class="sort ${n ? 'num' : ''}" data-k="${k}">${t}${radarSort.key === k ? (radarSort.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`;
  $('rTable').innerHTML = `<thead><tr>${th('home', 'Jogo')}<th>Mercado</th>${th('line', 'Linha', 1)}${th('odd', 'Odd', 1)}<th>Casa</th>${th('p_implied', 'P. implícita', 1)}${th('p_model', 'P. estimada', 1)}${th('fair_odd', 'Odd justa', 1)}${th('ev', 'EV', 1)}${th('ev_conservative', 'EV cons.', 1)}<th>Classificação</th>${th('observed_at', 'Horário dos dados', 1)}<th></th></tr></thead><tbody>${rows.map((r) => `
    <tr>
      <td><strong>${esc(r.home)}</strong> x <strong>${esc(r.away)}</strong><div class="muted">${esc(r.league)} · ${esc(r.date)}${r.kickoff_utc ? ' · ' + fmtTime(r.kickoff_utc) : ''}</div></td>
      <td>${esc(r.market)}${r.side ? ' · ' + esc(r.side) : ''}</td>
      <td class="num">${nf(r.line, 2)}</td>
      <td class="num">${r.odd ? nf(r.odd, 2) : '<span class="dim">sem odd</span>'}</td>
      <td>${esc(r.bookmaker || '—')}<div class="muted">${esc(r.odds_source || '')}</div></td>
      <td class="num">${pct(r.p_implied)}${r.p_novig ? `<div class="muted">s/ margem ${pct(r.p_novig)}</div>` : ''}</td>
      <td class="num">${pct(r.p_model)}</td>
      <td class="num">${r.fair_odd ? nf(r.fair_odd, 2) : '—'}</td>
      <td class="num ${cls(r.ev)}">${sgn(r.ev)}</td>
      <td class="num ${cls(r.ev_conservative)}">${sgn(r.ev_conservative)}</td>
      <td>${tag(r.classification)}<ul class="reasons">${(r.reasons || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul></td>
      <td class="num">${r.observed_at ? fmtTime(r.observed_at) + `<div class="muted">${ago(r.observed_at)}</div>` : '—'}</td>
      <td><div class="row-actions"><button class="btn small" data-act="analyze" data-id="${r.id}">Analisar</button>${r.odd ? `<button class="btn small" data-act="track" data-id="${r.id}">Acompanhar</button><button class="btn small" data-act="bet" data-id="${r.id}">Apostar…</button>` : ''}</div></td>
    </tr>`).join('') || '<tr><td colspan="13" class="empty">Nenhum mercado para os filtros atuais.</td></tr>'}</tbody>`;
}
function rowById(id) { return S.radar && S.radar.rows.find((r) => r.id === id); }

// ------------------------------------------------------------------ dashboard
function kpi(label, value, note, na) { return `<div class="kpi ${na ? 'na' : ''}"><small>${esc(label)}</small><b>${value}</b><span>${esc(note || '')}</span></div>`; }
function renderDashboard() {
  const D = S.dataset, R = S.radar, B = S.backtest;
  const k = [];
  if (D) {
    k.push(kpi('Partidas no histórico', D.count.toLocaleString('pt-BR'), `${D.date_first} a ${D.date_last}`));
    k.push(kpi('Campeonatos', D.leagues.length, D.seasons.length ? D.seasons.join(', ') : D.info.label));
    k.push(kpi('Média de escanteios', nf(D.avg_corners, 2), `Over 9,5: ${pct(D.over_frequency['9.5'])} (frequência)`));
  }
  if (R) {
    const withOdds = R.rows.filter((r) => r.odd);
    const markets = new Set(withOdds.map((r) => [r.date, r.home, r.away, r.line].join('|')));
    k.push(kpi('Jogos para análise', R.fixtures, 'calendário real ou importado'));
    k.push(kpi('Mercados monitorados', withOdds.length ? markets.size : '—', withOdds.length ? `${R.odds_rows} cotações` : 'sem odds conectadas', !withOdds.length));
    k.push(kpi('Valor potencial', R.counts['VALOR POTENCIAL'], `${R.counts['AGUARDAR CONFIRMAÇÃO']} aguardando confirmação`));
  } else k.push(kpi('Radar', '…', 'carregando', true));
  if (B) {
    const sel = B.models.find((m) => m.selected);
    k.push(kpi('Modelo selecionado', esc(sel.code), sel.skill[String(B.main_line)].verdict));
    k.push(kpi(`Brier teste (${nf(B.main_line, 1)})`, nf(sel.test.lines[String(B.main_line)].brier, 4), `base: ${nf(B.models.find((m) => m.code === B.baseline).test.lines[String(B.main_line)].brier, 4)} · n=${sel.test.n}`));
  } else k.push(kpi('Validação', '…', 'executando backtest', true));
  $('kpis').innerHTML = k.join('');

  if (R) {
    const fx = new Map();
    R.rows.forEach((r) => { const key = [r.date, r.home, r.away].join('|'); if (!fx.has(key)) fx.set(key, { ...r, nOdds: 0 }); if (r.odd) fx.get(key).nOdds++; });
    const list = [...fx.values()].slice(0, 30);
    $('fixturesMeta').textContent = `${fx.size} jogo(s)`;
    $('dashFixtures').innerHTML = list.length ? `<thead><tr><th>Jogo</th><th>Data</th><th class="num">Proj.</th><th class="num">Cotações</th><th></th></tr></thead><tbody>${list.map((r) => `<tr><td>${esc(r.home)} x ${esc(r.away)}<div class="muted">${esc(r.league)}</div></td><td class="num">${esc(r.date)}${r.kickoff_utc ? '<div class="muted">' + fmtTime(r.kickoff_utc) + '</div>' : ''}</td><td class="num">${r.mu ? nf(r.mu, 1) : '—'}</td><td class="num">${r.nOdds || '<span class="dim">0</span>'}</td><td><button class="btn small" data-act="analyze" data-id="${r.id}">Analisar</button></td></tr>`).join('')}</tbody>`
      : `<tbody><tr><td class="empty">${S.dataset && S.dataset.info.source === 'football_data'
        ? 'O calendário semanal do football-data.co.uk não tem jogos destes campeonatos no período (a lista é publicada às sextas e terças). Amplie as datas, inclua outros campeonatos ou importe um CSV de jogos futuros.'
        : 'Sem calendário nesta fonte: importe um CSV de jogos futuros em Dados e fontes.'}</td></tr></tbody>`;
    const opps = R.rows.filter((r) => r.classification === 'VALOR POTENCIAL' || r.classification === 'AGUARDAR CONFIRMAÇÃO').sort((a, b) => b.ev - a.ev).slice(0, 10);
    $('dashOpps').innerHTML = opps.length ? `<thead><tr><th>Jogo</th><th>Seleção</th><th class="num">Odd</th><th class="num">EV</th><th>Classe</th></tr></thead><tbody>${opps.map((r) => `<tr><td>${esc(r.home)} x ${esc(r.away)}<div class="muted">${esc(r.bookmaker)}</div></td><td>${esc(r.side)} ${nf(r.line, 1)}</td><td class="num">${nf(r.odd, 2)}</td><td class="num ${cls(r.ev)}">${sgn(r.ev)}</td><td>${tag(r.classification)}</td></tr>`).join('')}</tbody>`
      : `<tbody><tr><td class="empty">${R.odds_rows ? 'Nenhuma cotação com EV positivo nos filtros atuais.' : 'Sem cotações: não há como identificar oportunidades. Conecte uma fonte de odds ou importe um CSV.'}</td></tr></tbody>`;
  }
  if (B) {
    $('perfMeta').textContent = `teste ${B.n_test} jogos · ${B.folds.filter((f) => f.period === 'teste')[0]?.start || ''} a ${B.date_last}`;
    const L = String(B.main_line);
    $('dashPerf').innerHTML = `<thead><tr><th>Modelo</th><th class="num">NLL</th><th class="num">Brier ${nf(B.main_line, 1)}</th><th class="num">Acerto</th><th>vs. base</th></tr></thead><tbody>${B.models.map((m) => `<tr><td>${m.selected ? '★ ' : ''}${esc(m.name)}</td><td class="num">${nf(m.test.nll, 4)}</td><td class="num">${nf(m.test.lines[L].brier, 4)}</td><td class="num">${pct(m.test.lines[L].accuracy)}</td><td class="muted">${esc(m.skill[L].verdict)}</td></tr>`).join('')}</tbody>`;
    const preds = B.predictions.slice(-40).reverse();
    const hit = B.predictions.filter((p) => p.correct).length / Math.max(B.predictions.length, 1);
    $('predMeta').textContent = `últimas ${B.predictions.length}: acerto ${pct(hit)}`;
    $('dashPreds').innerHTML = `<thead><tr><th>Data</th><th>Jogo</th><th class="num">Proj.</th><th class="num">Real</th><th class="num">P(Over)</th><th></th></tr></thead><tbody>${preds.map((p) => `<tr><td class="num">${p.date}</td><td>${esc(p.home)} x ${esc(p.away)}</td><td class="num">${nf(p.mu, 1)}</td><td class="num">${p.actual}</td><td class="num">${pct(p.p_over)}</td><td class="${p.correct ? 'pos' : 'neg'}">${p.correct ? '✓' : '✗'}</td></tr>`).join('')}</tbody>`;
  }
  renderTrackedDash();
}

// ------------------------------------------------------------------ match analysis
async function runMatch(params) {
  if (params) { $('mLeague').value = params.league; fillTeams(); $('mHome').value = params.home; $('mAway').value = params.away; $('mDate').value = params.date; }
  const body = { data: S.spec(), league: $('mLeague').value, home: $('mHome').value, away: $('mAway').value, date: $('mDate').value || addDays(today(), 1), model: $('mModel').value || null, odds_csv: store.get('csv_odds', null), live_odds: true };
  if (!body.league || !body.home || !body.away) { toast('Escolha campeonato e equipes.', true); return; }
  if (body.home === body.away) { toast('Escolha equipes diferentes.', true); return; }
  $('mOut').innerHTML = '<div class="loading">Calculando distribuição de escanteios…</div>';
  try { S.match = await api('/api/v4/match', body); renderMatch(); } catch (e) { $('mOut').innerHTML = `<div class="alert err">${esc(e.message)}</div>`; }
}
function profileTable(p) {
  if (!p || !p.games) return '<div class="empty">Sem jogos dessa equipe na base.</div>';
  const w = (x, t) => `<tr><td>${t}</td><td class="num">${x ? x.n : 0}</td><td class="num">${nf(x && x.for, 2)}</td><td class="num">${nf(x && x.against, 2)}</td><td class="num">${nf(x && x.total, 2)}</td></tr>`;
  const tr = p.trend || {};
  const sh = p.shots_profile || {};
  return `<table class="tbl"><thead><tr><th>Janela</th><th class="num">Jogos</th><th class="num">A favor</th><th class="num">Contra</th><th class="num">Total</th></tr></thead><tbody>
    ${w(p.last5, 'Últimos 5')}${w(p.last10, 'Últimos 10')}${w(p.last20, 'Últimos 20')}${w(p.home10, 'Em casa (10)')}${w(p.away10, 'Fora (10)')}${p.season ? w(p.season, 'Temporada ' + esc(p.season.label)) : ''}</tbody></table>
    <p class="muted">Mudança recente (L5 − L20): a favor <span class="${cls(tr.for)}">${tr.for === null || tr.for === undefined ? '—' : (tr.for > 0 ? '+' : '') + nf(tr.for, 2)}</span> · contra <span class="${cls(-tr.against)}">${tr.against === null || tr.against === undefined ? '—' : (tr.against > 0 ? '+' : '') + nf(tr.against, 2)}</span></p>
    ${Object.keys(sh).length ? `<table class="tbl"><thead><tr><th>Últimos 10</th><th class="num">A favor</th><th class="num">Contra</th></tr></thead><tbody>${sh.shots ? `<tr><td>Finalizações</td><td class="num">${nf(sh.shots.for, 1)}</td><td class="num">${nf(sh.shots.against, 1)}</td></tr>` : ''}${sh.sot ? `<tr><td>No alvo</td><td class="num">${nf(sh.sot.for, 1)}</td><td class="num">${nf(sh.sot.against, 1)}</td></tr>` : ''}${sh.xg ? `<tr><td>xG</td><td class="num">${nf(sh.xg.for, 2)}</td><td class="num">${nf(sh.xg.against, 2)}</td></tr>` : ''}</tbody></table>` : '<p class="muted">Finalizações não disponíveis nesta fonte.</p>'}
    <p class="muted">Frequência histórica (últimos ${p.over_frequency[0].n} jogos, descritiva): ${p.over_frequency.map((o) => `O${nf(o.line, 1)} ${pct(o.freq, 0)}`).join(' · ')}</p>
    <table class="tbl"><thead><tr><th>Data</th><th>Local</th><th>Adversário</th><th class="num">A favor</th><th class="num">Contra</th></tr></thead><tbody>${p.recent.map((r) => `<tr><td class="num">${r.date}</td><td>${r.venue}</td><td>${esc(r.opponent)}</td><td class="num">${r.for}</td><td class="num">${r.against}</td></tr>`).join('')}</tbody></table>`;
}
function renderMatch() {
  const M = S.match, P = M.projection, m = M.model, f = M.fixture;
  const comps = m.components;
  const factor = (name, v) => { const w = Math.min(Math.abs(v) * 250, 50); return `<div class="factor"><span>${name}</span><div class="bar"><i style="left:${v >= 0 ? 50 : 50 - w}%;width:${w}%;background:${v >= 0 ? 'var(--pos)' : 'var(--neg)'}"></i></div><span class="num ${cls(v)}">${sgn(v)}</span></div>`; };
  alerts((M.warnings || []).map((t) => ({ text: t })));
  $('mOut').innerHTML = `
  <div class="panel"><div class="match-head">
    <div><div class="muted">${esc(f.league)} · ${esc(f.date)}</div><h3>${esc(f.home)} x ${esc(f.away)}</h3><div class="muted">Modelo: ${esc(m.model_name)} · <code>${esc(m.version)}</code> · dados até ${esc(M.data_last_date)} · fonte: ${esc(M.data_source.label)}</div></div>
    <div style="text-align:right"><div class="muted">Projeção central de escanteios</div><div class="big">${nf(P.mean, 2)}</div><div class="muted">intervalo preditivo 80%: ${P.interval80[0]}–${P.interval80[1]} · 95%: ${P.interval95[0]}–${P.interval95[1]}${m.mu_home ? ` · ${esc(f.home)} ${nf(m.mu_home, 2)} / ${esc(f.away)} ${nf(m.mu_away, 2)}` : ''}</div></div>
  </div></div>
  <div class="grid two">
    <div class="panel"><div class="panel-h"><h2>Distribuição de probabilidade (0 a 20+)</h2><span class="muted">dispersão α=${nf(m.alpha, 3)}</span></div><div class="chart" id="distChart"></div></div>
    <div class="panel"><div class="panel-h"><h2>Probabilidades por linha</h2><span class="muted">${esc(m.uncertainty)}</span></div><div class="tbl-wrap"><table class="tbl"><thead><tr><th class="num">Linha</th><th class="num">P(Over)</th><th class="num">Faixa</th><th class="num">P(Under)</th><th class="num">Justa Over</th><th class="num">Justa Under</th><th>Validação</th></tr></thead><tbody>${P.lines.map((l) => `<tr><td class="num">${nf(l.line, 1)}</td><td class="num">${pct(l.p_over)}</td><td class="num muted">${pct(l.p_over_low, 0)}–${pct(l.p_over_high, 0)}</td><td class="num">${pct(l.p_under)}</td><td class="num">${l.fair_over ? nf(l.fair_over, 2) : '—'}</td><td class="num">${l.fair_under ? nf(l.fair_under, 2) : '—'}</td><td class="muted">${esc(l.validation)}</td></tr>`).join('')}</tbody></table></div></div>
  </div>
  <div class="panel"><div class="panel-h"><h2>Odds e vantagem calculada</h2><span class="muted">${M.markets.length} cotação(ões)</span></div>${M.markets.length ? `<div class="tbl-wrap"><table class="tbl dense"><thead><tr><th>Seleção</th><th>Casa</th><th class="num">Odd</th><th class="num">Implícita</th><th class="num">Sem margem</th><th class="num">Estimada</th><th class="num">Justa</th><th class="num">EV</th><th class="num">EV cons.</th><th>Classe</th><th class="num">Horário</th></tr></thead><tbody>${M.markets.map((r) => `<tr><td>${esc(r.side)} ${nf(r.line, 2)}</td><td>${esc(r.bookmaker)}<div class="muted">${esc(r.odds_source)}</div></td><td class="num">${nf(r.odd, 2)}</td><td class="num">${pct(r.p_implied)}</td><td class="num">${pct(r.p_novig)}</td><td class="num">${pct(r.p_model)}</td><td class="num">${r.fair_odd ? nf(r.fair_odd, 2) : '—'}</td><td class="num ${cls(r.ev)}">${sgn(r.ev)}</td><td class="num ${cls(r.ev_conservative)}">${sgn(r.ev_conservative)}</td><td>${tag(r.classification)}<ul class="reasons">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join('')}</ul></td><td class="num">${fmtTime(r.observed_at)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">Nenhuma odd real para este jogo nas fontes configuradas. As odds justas acima são apenas o preço implícito do modelo.</div>'}</div>
  <div class="grid two">
    <div class="panel"><div class="panel-h"><h2>Fatores do modelo</h2><span class="muted">componentes estatísticos, não causalidade</span></div>${comps ? `
      <p class="muted">Nível da liga: ${nf(comps.league_level, 2)} escanteios por jogo. Efeitos multiplicativos sobre a média da liga:</p>
      ${factor('Vantagem de mando (liga)', comps.home_advantage)}${factor(`Ataque ${esc(f.home)}`, comps.attack_home)}${factor(`Defesa ${esc(f.away)} (concede)`, comps.defense_away)}${factor(`Ataque ${esc(f.away)}`, comps.attack_away)}${factor(`Defesa ${esc(f.home)} (concede)`, comps.defense_home)}
      <p class="foot">Estimativas com encolhimento hierárquico e peso temporal; equipes com pouco histórico ficam próximas da média da liga.</p>` : '<p class="muted">Este modelo usa médias móveis regularizadas; não há decomposição por equipe. Escolha um modelo “Força ataque/defesa” para ver os fatores.</p>'}</div>
    <div class="panel"><div class="panel-h"><h2>Principais riscos</h2></div><ul class="plain">${M.risks.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
      <div class="panel-h" style="margin-top:12px"><h2>Confrontos diretos</h2></div>${M.h2h.length ? `<table class="tbl"><tbody>${M.h2h.map((h) => `<tr><td class="num">${h.date}</td><td>${esc(h.home)} ${h.home_corners} x ${h.away_corners} ${esc(h.away)}</td><td class="num">${h.home_corners + h.away_corners}</td></tr>`).join('')}</tbody></table>` : '<p class="muted">Sem confrontos na base.</p>'}</div>
  </div>
  <div class="grid two">
    <div class="panel"><div class="panel-h"><h2>${esc(f.home)} — escanteios</h2></div>${profileTable(M.home_profile)}</div>
    <div class="panel"><div class="panel-h"><h2>${esc(f.away)} — escanteios</h2></div>${profileTable(M.away_profile)}</div>
  </div>`;
  const labels = P.distribution.map((_, i) => i === 21 ? '21+' : String(i));
  barChart($('distChart'), P.distribution, labels, { pct: true, label: 'distribuição de escanteios', highlight: (i) => i >= P.interval80[0] && i <= P.interval80[1] });
}

// ------------------------------------------------------------------ backtest
async function runBacktest() {
  $('bOut').innerHTML = '<div class="loading">Executando walk-forward (re-treino a cada bloco, sem dados futuros)…</div>';
  $('bRun').disabled = true;
  try {
    S.backtest = await api('/api/v4/backtest', { data: S.spec(), n_folds: parseInt($('bFolds').value, 10) || 10, main_line: parseFloat($('bLine').value) || 9.5 });
    renderBacktest(); renderDashboard();
  } catch (e) { $('bOut').innerHTML = `<div class="alert err">${esc(e.message)}</div>`; } finally { $('bRun').disabled = false; }
}
function renderBacktest() {
  const B = S.backtest, L = String(B.main_line);
  const sel = B.models.find((m) => m.selected);
  $('bMeta').textContent = `${B.total_matches} jogos · ${B.date_first} a ${B.date_last} · só treino até ${B.warmup_until}`;
  const fold = (f) => `<span class="tag ${f.period === 'teste' ? 'c-valor' : 'c-aguardar'}" title="treino: ${f.train} jogos">${f.period === 'teste' ? 'T' : 'V'}${f.fold} · ${f.start} → ${f.end} · ${f.n}</span>`;
  const brk = (rows, title) => `<div class="panel"><div class="panel-h"><h2>${title}</h2><span class="muted">teste · linha ${nf(B.main_line, 1)}</span></div><div class="tbl-wrap short"><table class="tbl"><thead><tr><th>Grupo</th><th class="num">n</th><th class="num">Brier</th><th class="num">Brier base</th><th class="num">Acerto</th><th class="num">Over obs.</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r.key || '—')}</td><td class="num">${r.n}</td><td class="num ${r.brier < r.brier_base ? 'pos' : 'neg'}">${nf(r.brier, 4)}</td><td class="num">${nf(r.brier_base, 4)}</td><td class="num">${pct(r.accuracy)}</td><td class="num">${pct(r.over_obs)}</td></tr>`).join('')}</tbody></table></div></div>`;
  $('bOut').innerHTML = `
    <div class="panel"><div class="panel-h"><h2>Separação cronológica</h2><span class="muted">V = validação (escolha do modelo) · T = teste (só relatório)</span></div><div class="class-counts">${B.folds.map(fold).join('')}</div>
      <p class="muted">Validação: ${B.n_validation} jogos · Teste: ${B.n_test} jogos · Modelo selecionado na validação: <strong>${esc(B.selected_name)}</strong> <code>${esc(B.selected_version)}</code></p></div>
    <div class="panel"><div class="panel-h"><h2>Comparação de modelos (teste)</h2><span class="muted">Δ log loss vs. modelo-base, IC 95% por blocos de data</span></div><div class="tbl-wrap"><table class="tbl dense"><thead><tr><th>Modelo</th><th class="num">NLL valid.</th><th class="num">NLL teste</th><th class="num">MAE</th><th class="num">Brier ${nf(B.main_line, 1)}</th><th class="num">Log loss</th><th class="num">Acerto</th><th class="num">Δ log loss</th><th class="num">IC 95%</th><th>Veredito</th></tr></thead><tbody>${B.models.map((m) => { const s = m.skill[L]; return `<tr><td>${m.selected ? '★ ' : ''}${esc(m.name)}</td><td class="num">${nf(m.validation.nll, 4)}</td><td class="num">${nf(m.test.nll, 4)}</td><td class="num">${nf(m.test.mae, 3)}</td><td class="num">${nf(m.test.lines[L].brier, 4)}</td><td class="num">${nf(m.test.lines[L].logloss, 4)}</td><td class="num">${pct(m.test.lines[L].accuracy)}</td><td class="num ${cls(-s.delta_logloss)}">${nf(s.delta_logloss, 4)}</td><td class="num muted">${s.ci95[0] === null ? '—' : nf(s.ci95[0], 4) + ' a ' + nf(s.ci95[1], 4)}</td><td>${esc(s.verdict)}</td></tr>`; }).join('')}</tbody></table></div>
      <p class="foot">${B.notes.map(esc).join(' ')}</p></div>
    <div class="grid two">
      <div class="panel"><div class="panel-h"><h2>Calibração (linha ${nf(B.main_line, 1)})</h2><span class="muted">tamanho do ponto ∝ n</span></div><div class="chart" id="calChart" style="max-width:420px"></div></div>
      <div class="panel"><div class="panel-h"><h2>Modelo selecionado por linha</h2></div><div class="tbl-wrap"><table class="tbl"><thead><tr><th class="num">Linha</th><th class="num">Brier</th><th class="num">Base</th><th class="num">Acerto</th><th class="num">Over obs.</th><th class="num">P média</th><th>Veredito</th></tr></thead><tbody>${B.by_line.map((r) => `<tr><td class="num">${nf(+r.key, 1)}</td><td class="num ${r.brier < r.brier_base ? 'pos' : 'neg'}">${nf(r.brier, 4)}</td><td class="num">${nf(r.brier_base, 4)}</td><td class="num">${pct(r.accuracy)}</td><td class="num">${pct(r.over_obs)}</td><td class="num">${pct(r.p_mean)}</td><td class="muted">${esc(sel.skill[r.key].verdict)}</td></tr>`).join('')}</tbody></table></div></div>
    </div>
    <div class="grid two">${brk(B.by_league, 'Por campeonato')}${brk(B.by_season, 'Por temporada')}</div>
    <div class="panel"><div class="panel-h"><h2>Previsões do período de teste</h2><span class="muted">últimas ${B.predictions.length}</span></div><div class="tbl-wrap short"><table class="tbl dense"><thead><tr><th>Data</th><th>Jogo</th><th class="num">Proj.</th><th class="num">Real</th><th class="num">P(Over ${nf(B.main_line, 1)})</th><th>Palpite</th><th></th></tr></thead><tbody>${B.predictions.slice().reverse().map((p) => `<tr><td class="num">${p.date}</td><td>${esc(p.home)} x ${esc(p.away)}<div class="muted">${esc(p.league)}</div></td><td class="num">${nf(p.mu, 2)}</td><td class="num">${p.actual}</td><td class="num">${pct(p.p_over)}</td><td>${p.prediction}</td><td class="${p.correct ? 'pos' : 'neg'}">${p.correct ? '✓' : '✗'}</td></tr>`).join('')}</tbody></table></div></div>`;
  calibChart($('calChart'), B.calibration);
  const oc = store.get('csv_odds', null);
  $('bOddsInfo').textContent = oc ? 'Usando o CSV de odds importado.' : 'Nenhum CSV de odds importado (Dados e fontes): ROI indisponível.';
}
async function runRoi() {
  const oc = store.get('csv_odds', null);
  if (!oc) { $('bRoiOut').innerHTML = '<div class="alert info">Sem odds históricas verificáveis não existe ROI. Importe um CSV de odds pré-jogo datadas em Dados e fontes.</div>'; return; }
  $('bRoiOut').innerHTML = '<div class="loading">Cruzando odds com as previsões do período de teste…</div>';
  try {
    const r = await api('/api/v4/backtest/odds', { data: S.spec(), n_folds: parseInt($('bFolds').value, 10) || 10, main_line: parseFloat($('bLine').value) || 9.5, odds_csv: oc, min_ev: (parseFloat($('bMinEv').value) || 0) / 100, commission: (parseFloat($('bComm').value) || 0) / 100 });
    const rj = Object.entries(r.rejected).map(([k, v]) => `${k.replace(/_/g, ' ')}: ${v}`).join(' · ');
    $('bRoiOut').innerHTML = `<div class="kpis">${kpi('Apostas simuladas', r.n, r.sample_warning ? 'amostra pequena (<200)' : '')}${kpi('ROI líquido', r.roi === null ? '—' : `<span class="${cls(r.roi)}">${sgn(r.roi)}</span>`, r.roi_ci95[0] === null ? 'IC indisponível' : `IC 95%: ${sgn(r.roi_ci95[0])} a ${sgn(r.roi_ci95[1])}`)}${kpi('Lucro (unidades)', `<span class="${cls(r.profit)}">${nf(r.profit, 2)}</span>`, `${r.wins} vitórias`)}${kpi('Drawdown máximo', nf(r.max_drawdown, 2) + ' u', '')}${kpi('CLV médio', r.clv_mean === null ? '—' : sgn(r.clv_mean), r.clv_available ? `${r.clv_n} apostas com odd de fechamento` : 'sem odds de fechamento no CSV')}</div>
      <div class="chart" id="roiChart"></div><p class="muted">Descartes: ${esc(rj)}</p><p class="foot">${esc(r.notes)}</p>
      <div class="tbl-wrap short"><table class="tbl dense"><thead><tr><th>Data</th><th>Jogo</th><th>Seleção</th><th class="num">Odd</th><th class="num">EV</th><th class="num">Real</th><th class="num">Lucro</th><th class="num">CLV</th></tr></thead><tbody>${r.bets.map((b) => `<tr><td class="num">${b.date}</td><td>${esc(b.home)} x ${esc(b.away)}</td><td>${b.side} ${nf(b.line, 2)}</td><td class="num">${nf(b.odd, 2)}</td><td class="num">${sgn(b.ev)}</td><td class="num">${b.actual}</td><td class="num ${cls(b.profit)}">${nf(b.profit, 2)}</td><td class="num ${cls(b.clv)}">${sgn(b.clv)}</td></tr>`).join('')}</tbody></table></div>`;
    lineChart($('roiChart'), r.bets.map((b) => ({ y: b.cumulative, label: `${b.date} ${b.home} x ${b.away}: ${nf(b.cumulative, 2)} u` })), { zero: 0, color: 'sign', dec: 1, label: 'lucro acumulado' });
  } catch (e) { $('bRoiOut').innerHTML = `<div class="alert err">${esc(e.message)}</div>`; }
}

// ------------------------------------------------------------------ bets & tracked (local or server)
const serverMode = () => S.status && S.status.database && store.get('token', '');
async function getBets() { if (serverMode()) { try { return (await api('/api/v4/bets')).bets; } catch (e) { toast(e.message, true); return []; } } return store.get('bets', []); }
async function getTracked() { if (serverMode()) { try { return (await api('/api/v4/tracked')).items; } catch (e) { return []; } } return store.get('tracked', []); }
const SETTLE = { ganha: (o, s) => s * (o - 1), perdida: (o, s) => -s, devolvida: () => 0, meio_ganha: (o, s) => s * (o - 1) / 2, meio_perdida: (o, s) => -s / 2 };
const RESULT_LABEL = { pendente: 'Pendente', ganha: 'Ganha', perdida: 'Perdida', devolvida: 'Devolvida', meio_ganha: 'Meio ganha', meio_perdida: 'Meio perdida' };
function settle(b) { const f = SETTLE[b.result]; return f ? f(+b.odd, +b.stake) : null; }

async function saveBet(bet) {
  bet.profit = settle(bet);
  if (serverMode()) { await api('/api/v4/bets', bet); }
  else {
    const list = store.get('bets', []);
    if (!bet.id) bet.id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
    const i = list.findIndex((b) => b.id === bet.id);
    if (i >= 0) list[i] = bet; else list.push({ ...bet, created_at: new Date().toISOString() });
    store.set('bets', list);
  }
}
async function deleteBet(id) {
  if (serverMode()) await api('/api/v4/bets/' + encodeURIComponent(id), null, 'DELETE');
  else store.set('bets', store.get('bets', []).filter((b) => b.id !== id));
}
function ledgerStats(list, start) {
  const settled = list.filter((b) => b.result !== 'pendente').sort((a, b) => (a.date + (a.created_at || '')).localeCompare(b.date + (b.created_at || '')));
  let bank = start, peak = start, maxDd = 0, staked = 0, profit = 0, wins = 0;
  const curve = [], dd = [];
  settled.forEach((b) => {
    const p = settle(b) || 0; staked += +b.stake; profit += p; bank += p; if (p > 0) wins++;
    peak = Math.max(peak, bank); const d = peak - bank; maxDd = Math.max(maxDd, d);
    curve.push({ y: bank, label: `${b.date} ${b.match}: banca ${nf(bank, 2)}` }); dd.push({ y: -d, label: `${b.date}: drawdown ${nf(d, 2)}` });
  });
  return { settled: settled.length, pending: list.length - settled.length, staked, profit, roi: staked ? profit / staked : null, hit: settled.length ? wins / settled.length : null, bank, maxDd, maxDdPct: peak ? maxDd / peak : null, curve, dd };
}
async function renderBets() {
  $('betStore').textContent = serverMode() ? 'gravando no servidor (banco de dados)' : 'gravando neste navegador';
  const list = await getBets();
  const start = parseFloat($('bankStart').value) || 0;
  const st = ledgerStats(list, start);
  $('betKpis').innerHTML = [kpi('Lucro / prejuízo', `<span class="${cls(st.profit)}">${nf(st.profit, 2)}</span>`, `${st.settled} liquidadas · ${st.pending} pendentes`),
    kpi('ROI', st.roi === null ? '—' : `<span class="${cls(st.roi)}">${sgn(st.roi)}</span>`, `apostado ${nf(st.staked, 2)}`),
    kpi('Banca atual', nf(st.bank, 2), `inicial ${nf(start, 2)}`), kpi('Drawdown máximo', nf(st.maxDd, 2), st.maxDdPct === null ? '' : pct(st.maxDdPct) + ' do pico'),
    kpi('Taxa de acerto', pct(st.hit), 'liquidadas com lucro'), kpi('Apostas', list.length, '')].join('');
  lineChart($('bankChart'), st.curve, { zero: start, color: 'sign', dec: 0, label: 'evolução da banca' });
  lineChart($('ddChart'), st.dd, { zero: 0, area: true, dec: 0, label: 'drawdown' });
  const rows = list.slice().sort((a, b) => b.date.localeCompare(a.date));
  $('betTable').innerHTML = `<thead><tr><th>Data</th><th>Jogo</th><th>Mercado</th><th class="num">Linha</th><th>Lado</th><th class="num">Odd</th><th>Casa</th><th class="num">Valor</th><th>Resultado</th><th class="num">Lucro</th><th>Justificativa</th><th>Modelo</th><th></th></tr></thead><tbody>${rows.map((b) => `<tr><td class="num">${esc(b.date)}</td><td>${esc(b.match)}</td><td>${esc(b.market)}</td><td class="num">${b.line === null || b.line === undefined || b.line === '' ? '—' : nf(b.line, 2)}</td><td>${esc(b.side || '')}</td><td class="num">${nf(b.odd, 2)}</td><td>${esc(b.bookmaker || '')}</td><td class="num">${nf(b.stake, 2)}</td><td><select data-res="${esc(b.id)}">${Object.entries(RESULT_LABEL).map(([k, v]) => `<option value="${k}" ${b.result === k ? 'selected' : ''}>${v}</option>`).join('')}</select></td><td class="num ${cls(settle(b))}">${settle(b) === null ? '—' : nf(settle(b), 2)}</td><td class="muted">${esc(b.rationale || '')}</td><td class="muted">${esc(b.model_version || '')}</td><td><div class="row-actions"><button class="btn small" data-edit="${esc(b.id)}">Editar</button><button class="btn small" data-del="${esc(b.id)}">Excluir</button></div></td></tr>`).join('') || '<tr><td colspan="13" class="empty">Nenhuma aposta registrada.</td></tr>'}</tbody>`;
  renderTracked();
}
async function renderTracked() {
  const items = await getTracked();
  $('trackTable').innerHTML = `<thead><tr><th>Registrado</th><th>Jogo</th><th>Seleção</th><th class="num">Odd</th><th>Casa</th><th class="num">P. estimada</th><th class="num">EV</th><th>Classe</th><th>Modelo</th><th></th></tr></thead><tbody>${items.map((t) => `<tr><td class="num">${fmtTime(t.created_at)}</td><td>${esc(t.home)} x ${esc(t.away)}<div class="muted">${esc(t.league)} · ${esc(t.date)}</div></td><td>${esc(t.side)} ${nf(t.line, 2)}</td><td class="num">${nf(t.odd, 2)}</td><td>${esc(t.bookmaker || '')}</td><td class="num">${pct(t.p_model)}</td><td class="num ${cls(t.ev)}">${sgn(t.ev)}</td><td>${tag(t.classification)}</td><td class="muted">${esc(t.model_version || '')}</td><td><button class="btn small" data-untrack="${esc(t.id)}">Remover</button></td></tr>`).join('') || '<tr><td colspan="10" class="empty">Nada acompanhado. Use “Acompanhar” no radar.</td></tr>'}</tbody>`;
}
async function renderTrackedDash() {
  const items = (await getTracked()).slice(0, 12);
  $('dashTracked').innerHTML = items.length ? `<thead><tr><th>Jogo</th><th>Seleção</th><th class="num">Odd</th><th class="num">EV no registro</th><th>Classe</th></tr></thead><tbody>${items.map((t) => `<tr><td>${esc(t.home)} x ${esc(t.away)}<div class="muted">${esc(t.date)}</div></td><td>${esc(t.side)} ${nf(t.line, 1)}</td><td class="num">${nf(t.odd, 2)}</td><td class="num ${cls(t.ev)}">${sgn(t.ev)}</td><td>${tag(t.classification)}</td></tr>`).join('')}</tbody>` : '<tbody><tr><td class="empty">Nenhuma oportunidade acompanhada.</td></tr></tbody>';
}
async function trackRow(r) {
  const payload = { date: r.date, league: r.league, home: r.home, away: r.away, market: r.market, line: r.line, side: r.side, odd: r.odd, bookmaker: r.bookmaker, p_model: r.p_model, p_novig: r.p_novig, ev: r.ev, ev_conservative: r.ev_conservative, classification: r.classification, reasons: r.reasons, observed_at: r.observed_at, odds_source: r.odds_source, model_version: r.model_version };
  if (serverMode()) await api('/api/v4/tracked', payload);
  else { const l = store.get('tracked', []); l.unshift({ id: String(Date.now()), created_at: new Date().toISOString(), ...payload }); store.set('tracked', l.slice(0, 500)); }
  toast('Oportunidade registrada para acompanhamento.'); renderTrackedDash();
}
function fillBetForm(b) {
  $('betId').value = b.id || ''; $('betDate').value = b.date || today(); $('betMatch').value = b.match || '';
  $('betMarket').value = b.market || 'Total de escanteios'; $('betLine').value = b.line ?? ''; $('betSide').value = b.side || '';
  $('betOdd').value = b.odd || ''; $('betBook').value = b.bookmaker || ''; $('betStake').value = b.stake || '';
  $('betResult').value = b.result || 'pendente'; $('betModel').value = b.model_version || ''; $('betWhy').value = b.rationale || '';
}
function csvDownload(name, rows, cols) {
  const q = (v) => { const s = String(v ?? ''); return /[",\n;]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const text = [cols.join(','), ...rows.map((r) => cols.map((c) => q(Array.isArray(r[c]) ? r[c].join(' | ') : r[c])).join(','))].join('\n');
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(['﻿' + text], { type: 'text/csv;charset=utf-8' })); a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ------------------------------------------------------------------ data & files
async function readFile(input, key, infoId, validate) {
  const f = input.files[0]; if (!f) return;
  if (f.size > 8_000_000) { toast('Arquivo maior que 8 MB.', true); return; }
  const text = await f.text();
  try { const msg = await validate(text); if (store.set('csv_' + key, text)) { toast(msg); csvInfo(); } } catch (e) { toast(e.message, true); }
  input.value = '';
}
function csvInfo() {
  const sz = (k) => { const t = store.get('csv_' + k, null); return t ? `${(t.split('\n').length - 1).toLocaleString('pt-BR')} linhas · ${(t.length / 1024).toFixed(0)} KB` : 'nenhum arquivo'; };
  $('csvMatchesInfo').textContent = sz('matches'); $('csvFixturesInfo').textContent = sz('fixtures'); $('csvOddsInfo').textContent = sz('odds');
}

async function reloadAll() {
  S.radar = null; S.backtest = null; S.match = null;
  $('kpis').innerHTML = '<div class="loading">Carregando dados…</div>';
  try { await loadDataset(); } catch (e) { alerts([{ kind: 'err', text: 'Fonte de dados indisponível: ' + e.message + ' Você pode trocar para o modo demonstração em Dados e fontes.' }]); $('kpis').innerHTML = ''; await loadStatus().catch(() => {}); return; }
  renderDashboard();
  await Promise.allSettled([runRadar(), runBacktest()]);
  await loadStatus().catch(() => {});
}

// ------------------------------------------------------------------ events
document.addEventListener('click', async (e) => {
  const t = e.target.closest('button, th.sort'); if (!t) return;
  if (t.dataset.view) return show(t.dataset.view);
  if (t.dataset.goto) return show(t.dataset.goto);
  if (t.matches('th.sort')) { const k = t.dataset.k; radarSort = { key: k, dir: radarSort.key === k ? -radarSort.dir : -1 }; return renderRadar(); }
  const act = t.dataset.act;
  if (act) {
    const r = rowById(t.dataset.id); if (!r) return;
    if (act === 'analyze') { show('confronto'); runMatch({ league: r.league, home: r.home, away: r.away, date: r.date }); }
    if (act === 'track') trackRow(r).catch((er) => toast(er.message, true));
    if (act === 'bet') { show('apostas'); fillBetForm({ date: r.date, match: `${r.home} x ${r.away}`, market: r.market, line: r.line, side: r.side, odd: r.odd, bookmaker: r.bookmaker, model_version: r.model_version, rationale: `${r.classification}: P. estimada ${pct(r.p_model)} vs. implícita ${pct(r.p_implied)}; EV ${sgn(r.ev)} (conservador ${sgn(r.ev_conservative)}). ${(r.reasons || []).join(' ')}` }); $('betStake').focus(); }
    return;
  }
  if (t.dataset.del) { if (confirm('Excluir esta aposta?')) { await deleteBet(t.dataset.del).catch((er) => toast(er.message, true)); renderBets(); } return; }
  if (t.dataset.edit) { const b = (await getBets()).find((x) => x.id === t.dataset.edit); if (b) { fillBetForm(b); window.scrollTo({ top: 0, behavior: 'smooth' }); } return; }
  if (t.dataset.untrack) {
    if (serverMode()) await api('/api/v4/tracked/' + encodeURIComponent(t.dataset.untrack), null, 'DELETE').catch((er) => toast(er.message, true));
    else store.set('tracked', store.get('tracked', []).filter((x) => x.id !== t.dataset.untrack));
    renderTracked(); renderTrackedDash(); return;
  }
  if (t.dataset.clear) { try { localStorage.removeItem('cqe4_csv_' + t.dataset.clear); } catch { /* ignore */ } csvInfo(); toast('Arquivo apagado deste navegador.'); return; }
});
document.addEventListener('change', async (e) => {
  const t = e.target;
  if (t.dataset && t.dataset.res) {
    const list = await getBets(); const b = list.find((x) => x.id === t.dataset.res);
    if (b) { b.result = t.value; await saveBet(b).catch((er) => toast(er.message, true)); renderBets(); }
  }
});
$('menuBtn').addEventListener('click', () => $('sidebar').classList.toggle('open'));
$('rRun').addEventListener('click', runRadar);
['rClass', 'rBest'].forEach((id) => $(id).addEventListener('change', renderRadar));
$('mLeague').addEventListener('change', fillTeams);
$('mRun').addEventListener('click', () => runMatch());
$('bRun').addEventListener('click', runBacktest);
$('bRoi').addEventListener('click', runRoi);
$('bankStart').addEventListener('change', () => { store.set('bank_start', parseFloat($('bankStart').value) || 0); renderBets(); });
$('betClear').addEventListener('click', () => fillBetForm({}));
$('betForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const bet = { id: $('betId').value || undefined, date: $('betDate').value, match: $('betMatch').value.trim(), market: $('betMarket').value.trim() || 'Total de escanteios', line: $('betLine').value === '' ? null : parseFloat($('betLine').value), side: $('betSide').value || null, odd: parseFloat($('betOdd').value), bookmaker: $('betBook').value.trim() || null, stake: parseFloat($('betStake').value), result: $('betResult').value, rationale: $('betWhy').value.trim() || null, model_version: $('betModel').value.trim() || null };
  if (!(bet.odd >= 1.01) || !(bet.stake > 0)) { toast('Informe odd (≥ 1,01) e valor apostado.', true); return; }
  try { await saveBet(bet); toast('Aposta salva.'); fillBetForm({}); renderBets(); } catch (er) { toast(er.message, true); }
});
$('betExport').addEventListener('click', async () => { const l = await getBets(); csvDownload('apostas_cqe.csv', l.map((b) => ({ ...b, profit: settle(b) })), ['date', 'match', 'market', 'line', 'side', 'odd', 'bookmaker', 'stake', 'result', 'profit', 'rationale', 'model_version']); });
$('trackExport').addEventListener('click', async () => { const l = await getTracked(); csvDownload('oportunidades_cqe.csv', l, ['created_at', 'date', 'league', 'home', 'away', 'market', 'line', 'side', 'odd', 'bookmaker', 'p_model', 'p_novig', 'ev', 'ev_conservative', 'classification', 'reasons', 'observed_at', 'odds_source', 'model_version']); });
document.querySelectorAll('input[name=src]').forEach((r) => r.addEventListener('change', () => { $('fdOptions').style.display = r.value === 'football_data' && r.checked ? '' : (document.querySelector('input[name=src]:checked').value === 'football_data' ? '' : 'none'); }));
$('applySource').addEventListener('click', async () => {
  const src = document.querySelector('input[name=src]:checked').value;
  const leagues = [...document.querySelectorAll('#leagueBoxes input:checked')].map((i) => i.value);
  if (src === 'football_data' && (!leagues.length || leagues.length > 8)) { toast('Escolha de 1 a 8 campeonatos.', true); return; }
  if (src === 'csv' && !store.get('csv_matches', null)) { toast('Importe primeiro um CSV de resultados.', true); return; }
  store.set('source', src); store.set('leagues', leagues); store.set('seasons', parseInt($('seasons').value, 10));
  toast('Fonte aplicada. Recalculando…'); show('painel'); await reloadAll();
});
$('fileMatches').addEventListener('change', (e) => readFile(e.target, 'matches', 'csvMatchesInfo', async (text) => { const r = await api('/api/v4/dataset', { source: 'csv', matches_csv: text }); return `${r.count} partidas válidas. Selecione “CSV importado” e aplique a fonte.`; }));
$('fileFixtures').addEventListener('change', (e) => readFile(e.target, 'fixtures', 'csvFixturesInfo', async (text) => { if (!/date/i.test(text.split('\n')[0]) || !/home/i.test(text.split('\n')[0])) throw Error('CSV de jogos futuros precisa das colunas date, home, away.'); return 'Jogos futuros importados.'; }));
$('fileOdds').addEventListener('change', (e) => readFile(e.target, 'odds', 'csvOddsInfo', async (text) => { const r = await api('/api/validate/odds', { csv: text }); return r.message; }));
$('refreshStatus').addEventListener('click', () => loadStatus().catch((e) => toast(e.message, true)));
$('saveToken').addEventListener('click', () => { store.set('token', $('token').value.trim()); $('token').value = ''; toast('Token salvo neste navegador.'); renderBets(); });
window.addEventListener('hashchange', () => show(location.hash.slice(1)));

// ------------------------------------------------------------------ boot
(async function boot() {
  $('rFrom').value = today(); $('rTo').value = addDays(today(), 7); $('mDate').value = addDays(today(), 1);
  $('bankStart').value = store.get('bank_start', 1000); $('betDate').value = today();
  csvInfo();
  try { await loadStatus(); fillStaticSelects(); } catch (e) { alerts([{ kind: 'err', text: 'API indisponível: ' + e.message }]); return; }
  S.booting = true; show(location.hash.slice(1) || 'painel'); S.booting = false;
  if (store.get('source', 'football_data') === 'demo') { $('rFrom').value = ''; $('rTo').value = ''; }
  await reloadAll();
})();
