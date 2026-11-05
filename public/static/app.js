/* CORNERS QUANT EDGE — Vercel stateless browser controller; no external JS libraries. */
'use strict';
const $=(id)=>document.getElementById(id);
const Q=(sel)=>Array.from(document.querySelectorAll(sel));
const fmt=(n,d=1)=>Number(n).toLocaleString('pt-BR',{minimumFractionDigits:d,maximumFractionDigits:d});
const pct=(n,d=1)=>fmt(n*100,d)+'%';
const clean=(v)=>String(v??'').replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x]));
let DATA=null, BT=null, toastTimer=null;
const TABS={overview:'Visão geral',forecast:'Analisar jogo',backtest:'Backtesting',odds:'Odds & EV',data:'Bases de dados'};

const KEY_MATCHES='cornersqe_matches_v31';
const KEY_ODDS='cornersqe_odds_v31';
const ACTIONS={
  '/api/overview':'overview',
  '/api/teams':'teams',
  '/api/backtest':'backtest',
  '/api/forecast':'forecast',
  '/api/odds/backtest':'odds_backtest'
};
function getSaved(key){try{return window.localStorage.getItem(key);}catch{return null;}}
function saveLocal(key,text){
  try{window.localStorage.setItem(key,text);}catch(err){throw Error('O navegador não conseguiu salvar este arquivo localmente. Libere espaço ou use um CSV menor.');}
}
async function api(path,options={}){
  const url = new URL(path,window.location.origin);
  const action = ACTIONS[url.pathname];
  let target=path,fetchOptions=options;
  if(action){
    const params=Object.fromEntries(url.searchParams.entries());
    if(options.body) Object.assign(params,JSON.parse(options.body));
    fetchOptions={method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      action,params,matches_csv:getSaved(KEY_MATCHES),odds_csv:action==='odds_backtest'||action==='overview'?getSaved(KEY_ODDS):null
    })};
    target='/api/query';
  }
  let r;
  try {r=await fetch(target,fetchOptions);}catch(err){throw Error('Não foi possível conectar à API. Confira sua conexão com a internet.');}
  let obj;
  try{obj=await r.json();}catch(err){throw Error('Resposta inválida do servidor.');}
  if(!r.ok){let msg=obj.detail;throw Error(typeof msg==='string'?msg:JSON.stringify(msg||'Erro na operação'));}
  return obj;
}

function toast(text,bad=false){const n=$('message');n.textContent=text;n.className='toast show'+(bad?' error':'');clearTimeout(toastTimer);toastTimer=setTimeout(()=>n.className='toast',6500);}
function screen(name){if(!TABS[name])name='overview';Q('.screen').forEach(n=>n.classList.toggle('active',n.id==='screen-'+name));Q('.nav-btn').forEach(n=>n.classList.toggle('active',n.dataset.screen===name));$('currentSection').textContent=TABS[name];location.hash='#'+name;window.scrollTo({top:0,behavior:'smooth'});if(name==='backtest'&&!BT)runBacktest();}
function setText(id,text){if($(id))$(id).textContent=text;}
function fillOptions(el,values,selected){el.innerHTML=values.map(v=>`<option value="${clean(v)}">${clean(v)}</option>`).join('');if(selected&&values.includes(selected))el.value=selected;}

async function overview(){
  DATA=await api('/api/overview');
  setText('sMatches',DATA.count.toLocaleString('pt-BR'));setText('sLeagues',DATA.league_count);setText('sAverage',fmt(DATA.avg_corners,2));setText('sOdds',DATA.odds_count.toLocaleString('pt-BR'));
  setText('sOddsCaption',DATA.odds_count?'Registros CSV (não autenticados)':'Nenhuma cotação importada');
  setText('sourceText',DATA.source.description);
  setText('artLambda',fmt(DATA.avg_corners,1));
  setText('healthModel',DATA.selected_name);setText('healthBrier',fmt(DATA.test_metrics.brier,4));setText('healthN',DATA.split.test+' jogos');
  let pa=DATA.test_metrics.accuracy;setText('ringValue',pct(pa));$('ring').style.setProperty('--pct',(pa*100).toFixed(3)+'%');
  const leagues=DATA.leagues;
  $('leagueChart').innerHTML=leagues.map(x=>`<div class="bar-row"><span>${clean(x.name)}</span><div class="bar-track"><div class="bar-fill" style="width:${x.over_9_5*100}%"></div></div><strong>${pct(x.over_9_5,0)}</strong></div>`).join('');
  setText('metaSource',DATA.source.description);setText('metaDates',`${DATA.date_first} a ${DATA.date_last}`);setText('metaMatches',DATA.count+' partidas');setText('metaOdds',DATA.odds_count+' registros');
  const leagueNames=leagues.map(x=>x.name);
  fillOptions($('leagueSelect'),leagueNames,$('leagueSelect').value||'Premier League');
  fillOptions($('modelSelect'),['',...Object.keys(DATA.methods)],$('modelSelect').value);
  $('modelSelect').querySelectorAll('option').forEach(o=>o.textContent=o.value?DATA.methods[o.value]:'Automático (seleção pela validação)');
  let day=new Date(DATA.date_last+'T12:00:00');day.setDate(day.getDate()+2);
  $('forecastDate').value=day.toISOString().slice(0,10);
  await updateTeams();
  return DATA;
}
async function updateTeams(){
  const league=$('leagueSelect').value;if(!league)return;
  const r=await api('/api/teams?league='+encodeURIComponent(league));
  const oldH=$('homeSelect').value,oldA=$('awaySelect').value;
  fillOptions($('homeSelect'),r.teams,oldH);
  fillOptions($('awaySelect'),r.teams,oldA);
  if($('homeSelect').value===$('awaySelect').value&&r.teams.length>1)$('awaySelect').selectedIndex=1;
}

async function makeForecast(evt){
  evt.preventDefault();const btn=$('forecastButton');btn.disabled=true;btn.textContent='Calculando...';
  try{
    const req={date:$('forecastDate').value,league:$('leagueSelect').value,home:$('homeSelect').value,
      away:$('awaySelect').value,line:+$('lineSelect').value,model:$('modelSelect').value||null};
    if($('overOdds').value&&$('underOdds').value){req.over_odds=+$('overOdds').value;req.under_odds=+$('underOdds').value;}
    const p=await api('/api/forecast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(req)});
    $('forecastEmpty').classList.add('hidden');$('forecastResult').classList.remove('hidden');
    setText('matchLabel',`${p.match.home} × ${p.match.away}  ·  ${p.match.league}  ·  ${p.line.toFixed(1)}`);
    setText('projectedCorners',fmt(p.mu,2));setText('pOver',pct(p.p_over));setText('pUnder',pct(p.p_under));
    $('pOverBar').style.width=(p.p_over*100)+'%';setText('selectedForecastModel',p.model_name);
    setText('forecastAlpha',fmt(p.alpha,4));setText('forecastAsOf',p.source_date_last);
    $('marketResult').innerHTML=p.market?['over','under'].map(side=>{
      const x=p.market[side];const good=x.ev_conservative>0&&x.ev>=.03;
      return `<div class="market-box ${x.ev>=0?'positive':'negative'}"><header><b>${clean(side.toUpperCase())} ${p.line.toFixed(1)}</b><span>Odd ${fmt(x.odd,2)}</span></header><strong>${x.ev>=0?'+':''}${pct(x.ev)}</strong><small>EV central estimado</small><small>EV conservador: ${x.ev_conservative>0?'+':''}${pct(x.ev_conservative)}</small><small>${clean(x.decision)}</small></div>`;
    }).join(''):'<div class="notice muted" style="grid-column:1/-1;margin-top:0"><span>ⓘ</span> Insira as duas odds para calcular margem da casa, preço justo e EV estimado.</div>';
    toast('Projeção concluída. Modelo baseado em informações anteriores à data informada.');
  }catch(err){toast(err.message,true);}finally{btn.disabled=false;btn.innerHTML='Calcular probabilidades <span>→</span>';}
}
function pill(s,t){return `<span class="pill ${s}">${clean(t)}</span>`;}
function renderBacktest(result){
  BT=result;$('backtestResults').classList.remove('hidden');
  const selected=result.models.find(x=>x.selected);const m=selected.test;
  setText('btModel',result.selected_name);setText('btAccuracy',pct(m.accuracy,2));setText('btHits',m.hits+' de '+m.n+' partidas');setText('btBrier',fmt(m.brier,5));
  setText('btPeriod',result.split.test_first+' → '+result.split.test_last);
  if(result.uncertainty){
    const u=result.uncertainty;
    setText('btDelta',(u.delta_brier>=0?'+':'')+fmt(u.delta_brier,5));
    setText('btCI','['+u.ci95.map(v=>(v>=0?'+':'')+fmt(v,4)).join(' ; ')+']');
    setText('btDateBlocks',u.date_blocks+' datas');
    $('btInference').textContent=u.interpretation+' — não representa retorno financeiro.';
  }
  const split=result.split,n=split.train+split.validation+split.test;
  $('splitBar').innerHTML=[split.train,split.validation,split.test].map(x=>`<div style="flex:${x}"></div>`).join('');
  $('splitLegend').innerHTML=[['Treino',split.train],['Validação',split.validation],['Teste final',split.test]].map(([a,b])=>`<div><i></i>${a}<br/><b>${b} jogos</b></div>`).join('');
  const sorted=result.models.slice().sort((a,b)=>a.test.brier-b.test.brier);
  $('modelTable').querySelector('tbody').innerHTML=sorted.map(x=>`<tr><td>${clean(x.name)}</td><td>${x.test.hits}/${x.test.n} · ${pct(x.test.accuracy,1)}</td><td>${fmt(x.test.brier,5)}</td><td>${fmt(x.test.mae,3)}</td><td>${fmt(x.test.nll,3)}</td><td>${x.selected?pill('green','Selecionado na validação'):'—'}</td></tr>`).join('');
  const rows=result.predictions.slice(-40).reverse();
  $('predictionTable').querySelector('tbody').innerHTML=rows.map(x=>`<tr><td>${clean(x.date)}</td><td>${clean(x.home)} × ${clean(x.away)}</td><td>${x.actual}</td><td>${fmt(x.mu,2)}</td><td>${pct(x.p_over)}</td><td>${clean(x.prediction)}</td><td>${x.correct?pill('green','Acertou'):pill('red','Errou')}</td></tr>`).join('');
  $('exportBacktest').href='#download';
  setText('backtestLoading',`Modelo selecionado: ${result.selected_name} · ${result.split.test} jogos fora da amostra · Sem odds, ROI não disponível.`);
}
async function runBacktest(){const btn=$('runBacktest');btn.disabled=true;btn.textContent='Calculando...';
 try{const l=+$('backtestLine').value;const r=await api('/api/backtest?line='+encodeURIComponent(l));renderBacktest(r);}
 catch(err){toast(err.message,true);setText('backtestLoading',err.message);}finally{btn.disabled=false;btn.innerHTML='Executar backtest <span>→</span>';}}

async function uploadFile(input,url,button){
  const file=input.files[0];if(!file){toast('Selecione um arquivo CSV primeiro.',true);return;}
  const isMatches=url.includes('matches'),limit=isMatches?1_000_000:300_000;
  if(file.size>limit){toast('Arquivo excede o limite de '+(isMatches?'1 MB':'300 KB')+'.',true);return;}
  button.disabled=true;const old=button.innerHTML;button.textContent='Validando...';
  try{
    const csv=await file.text();
    const dest=isMatches?'/api/validate/matches':'/api/validate/odds';
    const r=await api(dest,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csv})});
    saveLocal(isMatches?KEY_MATCHES:KEY_ODDS,csv);
    toast(r.message||'Arquivo importado no navegador.');
    if(isMatches){BT=null;await overview();}
    else{DATA.odds_count=r.count;setText('sOdds',r.count);setText('metaOdds',r.count+' registros');}
  }catch(err){toast(err.message,true);}finally{button.disabled=false;button.innerHTML=old;}
}

async function runOdds(){const btn=$('runOddsBacktest');btn.disabled=true;btn.textContent='Calculando...';
 try{const req={line:+$('oddsLine').value,min_ev:+$('minEV').value/100};const r=await api('/api/odds/backtest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(req)});
  const box=$('roiOutput');if(r.roi===null){box.className='roi-empty';box.innerHTML=`<span>—</span><h3>ROI não calculável</h3><p>${clean(r.reason||'Nenhuma aposta passou nos requisitos com odds e timestamps.')}</p>`;$('roiTablePanel').style.display='none';}
  else{box.className='roi-live';box.innerHTML=`<div><small>ROI retrospectivo</small><strong>${r.roi>=0?'+':''}${pct(r.roi,2)}</strong></div><div><small>Lucro em unidades (flat)</small><strong>${r.profit>=0?'+':''}${fmt(r.profit,2)} u</strong></div><div><small>Seleções elegíveis</small><strong>${r.n}</strong></div><div><small>Acertos</small><strong>${r.wins}/${r.n}</strong></div>`;
    $('roiTablePanel').style.display='block';$('roiTable').querySelector('tbody').innerHTML=r.bets.map(x=>`<tr><td>${clean(x.date)}</td><td>${clean(x.home)} × ${clean(x.away)}</td><td>${clean(x.side)} ${x.line}</td><td>${fmt(x.odd,2)}</td><td>${pct(x.ev)}</td><td>${x.profit>=0?'+':''}${fmt(x.profit,2)}</td></tr>`).join('');
    if(r.sample_warning)toast('Atenção: amostra financeira pequena; resultado não é estável.',true);
  }
 }catch(err){toast(err.message,true);}finally{btn.disabled=false;btn.innerHTML='Calcular ROI das odds importadas <span>→</span>';}}
async function loadPremier(){
  if(!window.confirm('Importar 10 temporadas e substituir sua base local no navegador?'))return;
  const b=$('loadPremier');b.disabled=true;b.textContent='Baixando histórico...';
  try{
    const r=await api('/api/import/premier10',{method:'POST'});
    saveLocal(KEY_MATCHES,r.csv);BT=null;await overview();toast(r.message);screen('backtest');
  }catch(err){toast('Falha na importação: '+err.message,true);}
  finally{b.disabled=false;b.innerHTML='Importar 10 temporadas <span>↗</span>';}
}
async function reset(){
  if(!window.confirm('Remover dados deste navegador e voltar à base inicial?'))return;
  window.localStorage.removeItem(KEY_MATCHES);window.localStorage.removeItem(KEY_ODDS);
  BT=null;await overview();toast('Amostra original restaurada neste navegador.');
}
function exportCsv(rows){
  if(!rows.length){toast('Não há previsões para exportar.',true);return;}
  const keys=Object.keys(rows[0]);
  const esc=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
  const lines=[keys.map(esc).join(','),...rows.map(row=>keys.map(k=>esc(row[k])).join(','))];
  const blob=new Blob([lines.join('\r\n')],{type:'text/csv;charset=utf-8;'});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='corners_backtest_holdout.csv';
  document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}
async function downloadPredictions(e){
  e.preventDefault();
  try{const line=+$('backtestLine').value;const res=await api('/api/backtest?line='+line+'&limit=0');exportCsv(res.predictions);}
  catch(err){toast(err.message,true);}
}

async function init(){
 Q('[data-screen]').forEach(el=>el.addEventListener('click',()=>screen(el.dataset.screen)));
 Q('[data-goto]').forEach(el=>el.addEventListener('click',()=>screen(el.dataset.goto)));
 $('forecastForm').addEventListener('submit',makeForecast);
 $('leagueSelect').addEventListener('change',()=>updateTeams().catch(e=>toast(e.message,true)));
 $('runBacktest').addEventListener('click',runBacktest);
 $('uploadMatches').addEventListener('click',()=>uploadFile($('matchesFile'),'/api/validate/matches',$('uploadMatches')));
 $('uploadOdds').addEventListener('click',()=>uploadFile($('oddsFile'),'/api/validate/odds',$('uploadOdds')));
 $('runOddsBacktest').addEventListener('click',runOdds);
 $('loadPremier').addEventListener('click',loadPremier);$('restoreSample').addEventListener('click',reset);
 $('exportBacktest').addEventListener('click',downloadPredictions);
 try{await overview();screen(location.hash.replace('#','')||'overview');}catch(err){toast(err.message,true);}
}
document.addEventListener('DOMContentLoaded',init);
