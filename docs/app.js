/* Fiscal Dominance Monitor — renderer.
   All logic here is presentational. Scores, stages and signals arrive
   pre-computed from the Python rule engine; nothing on this page may
   recompute or second-guess them. */
'use strict';

const $  = s => document.querySelector(s);
const el = (t, c, h) => { const n = document.createElement(t);
  if (c) n.className = c; if (h !== undefined) n.innerHTML = h; return n; };

const STAGE_COLOR = {0:'#2ea043',1:'#d4a72c',2:'#e8873a',3:'#e5484d',4:'#a457e8'};
const SCORE_COLOR = v => v>=70?'var(--r)': v>=55?'var(--o)': v>=40?'var(--y)':'var(--g)';
const CLS = s => 's-' + String(s||'').toLowerCase().replace(/\s+/g,'-');

const fmtNum = (v,d=2) => v===null||v===undefined||Number.isNaN(v) ? '—' : Number(v).toFixed(d);
const fmtVal = (v,u) => {
  if (v===null||v===undefined) return '—';
  if (u==='$')  return '$' + Number(v).toLocaleString('en-US',{maximumFractionDigits:0});
  if (u==='%')  return Number(v).toFixed(2) + '%';
  if (u==='T$') return '$' + Number(v).toFixed(2) + 'T';
  return Number(v).toLocaleString('en-US',{maximumFractionDigits:2});
};
const fmtChg = (v,u) => {
  if (v===null||v===undefined) return {t:'—',c:'flat'};
  const n = Number(v);
  const cls = Math.abs(n) < 1e-9 ? 'flat' : (n>0 ? 'up' : 'down');
  if (u==='bp') return {t:(n>=0?'+':'') + Math.round(n*100) + 'bp', c:cls};
  if (u==='pp') return {t:(n>=0?'+':'') + n.toFixed(2) + 'pp', c:cls};
  return {t:(n>=0?'+':'') + n.toFixed(1) + '%', c:cls};
};
const chkBox = p => p===true ? '☑' : (p===false ? '☐' : '◌');
const chkCls = p => p===true ? 'on' : (p===false ? 'off' : 'na');

/* ---------------------------------------------------------------- charts */
function spark(vals, dates, color, opts={}) {
  const pts = vals.map((v,i)=>[i,v]).filter(p => p[1]!==null && p[1]!==undefined && !Number.isNaN(p[1]));
  if (pts.length < 2) return el('div','note','数据不足');
  const xs = pts.map(p=>p[0]), ys = pts.map(p=>p[1]);
  const x0=Math.min(...xs), x1=Math.max(...xs);
  let y0=Math.min(...ys), y1=Math.max(...ys);
  if (y1===y0){ y1=y0+1; y0=y0-1; }
  const pad=(y1-y0)*0.12; y0-=pad; y1+=pad;
  const W=100, H=100;
  const X = x => (x-x0)/(x1-x0)*W;
  const Y = y => H - (y-y0)/(y1-y0)*H;
  const d = pts.map((p,i)=>(i?'L':'M')+X(p[0]).toFixed(2)+' '+Y(p[1]).toFixed(2)).join(' ');
  const area = d + ` L ${W} ${H} L 0 ${H} Z`;
  const uid = 'g'+Math.random().toString(36).slice(2,8);

  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('class','spark');
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio','none');
  svg.innerHTML =
    `<defs><linearGradient id="${uid}" x1="0" y1="0" x2="0" y2="1">
       <stop offset="0%" stop-color="${color}" stop-opacity=".30"/>
       <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
     </linearGradient></defs>
     <path d="${area}" fill="url(#${uid})"/>
     <path d="${d}" fill="none" stroke="${color}" stroke-width="1.6"
           vector-effect="non-scaling-stroke" stroke-linejoin="round"/>` +
    (opts.zero!==undefined && opts.zero>y0 && opts.zero<y1
      ? `<line x1="0" y1="${Y(opts.zero).toFixed(2)}" x2="${W}" y2="${Y(opts.zero).toFixed(2)}"
              stroke="var(--line2)" stroke-width="1" stroke-dasharray="3 3"
              vector-effect="non-scaling-stroke"/>` : '') +
    `<circle cx="${X(pts.at(-1)[0]).toFixed(2)}" cy="${Y(pts.at(-1)[1]).toFixed(2)}"
             r="2.4" fill="${color}" vector-effect="non-scaling-stroke"/>`;
  return svg;
}

function chartCard(title, vals, dates, color, unit, opts) {
  const box = el('div','chart');
  const last = [...vals].reverse().find(v => v!==null && v!==undefined);
  const first = vals.find(v => v!==null && v!==undefined);
  let sub = fmtVal(last, unit);
  if (first && last && unit!=='%') sub += `  (${((last/first-1)*100>=0?'+':'')}${((last/first-1)*100).toFixed(1)}%)`;
  else if (first!==undefined && last!==undefined && unit==='%')
    sub += `  (${((last-first)>=0?'+':'')}${Math.round((last-first)*100)}bp)`;
  box.appendChild(el('div','ct',`<b>${title}</b><span>${sub}</span>`));
  box.appendChild(spark(vals, dates, color, opts||{}));
  return box;
}

/* ------------------------------------------------------------ renderers */
function renderHero(d) {
  const c = STAGE_COLOR[d.stage] || 'var(--y)';
  const h = el('div','hero');
  h.style.setProperty('--stage-color', c);
  const stab = d.stability || {};
  const conf = d.confidence || {};
  h.innerHTML = `
    <div class="stage-badge"><span class="stage-dot"></span>
      <span class="stage-num">STAGE ${d.stage}</span></div>
    <div class="stage-name">${d.stage_name_cn}</div>
    <div class="stage-en">${d.stage_name}</div>
    <div class="composite"><b>${fmtNum(d.composite,0)}</b><span>/ 100 综合</span></div>
    <div class="bar"><i style="width:${Math.max(2,d.composite)}%"></i></div>
    <div class="meta-row">
      <div>持续 <b>${stab.days_in_stage||0}</b> 天</div>
      <div>90日翻转 <b>${stab.flips_90d??'—'}</b> 次</div>
      <div>可信度 <b>${fmtNum(conf.confidence,0)}</b></div>
      ${stab.trustworthy===false?'<div style="color:var(--o)">⚠️ 分类器不稳定</div>':''}
    </div>`;
  $('#hero').replaceChildren(h);
}

function renderDC(d) {
  const dv = d.driver_composite, cv = d.confirmation_composite;
  const gap = (dv!==null && cv!==null) ? dv-cv : null;
  let note = '数据不足。';
  if (gap!==null) {
    if (gap > 25)  note = `宏观驱动强于市场确认 ${Math.abs(gap).toFixed(0)} 分：你的论点具备条件，但价格还没跟上。这意味着要么你早了，要么驱动读错了——两者不可能同时排除。`;
    else if (gap < -25) note = `市场确认强于宏观驱动 ${Math.abs(gap).toFixed(0)} 分：价格在走，但这套框架解释不了它。这波涨幅别记在宏观假设头上。`;
    else note = `两者大体一致（差 ${gap>=0?'+':''}${gap.toFixed(0)} 分）：宏观叙事与市场行为暂时不矛盾。`;
  }
  const box = el('div');
  box.innerHTML = `<div class="dc">
      <div><b style="color:var(--b)">${fmtNum(dv,0)}</b><span>驱动分<br><small style="color:var(--tx3)">财政·利率·政策</small></span></div>
      <div><b style="color:var(--o)">${fmtNum(cv,0)}</b><span>市场确认分<br><small style="color:var(--tx3)">金·BTC·美元价格</small></span></div>
    </div><div class="dc-note">${note}</div>`;
  $('#dc').replaceChildren(box);
}

function renderScores(d) {
  const map = [['fiscal_stress','财政压力','长端收益率·期限溢价·债务'],
               ['financial_repression','金融压抑','实际利率↓ + 通胀↑'],
               ['debasement','贬值交易','美元↓ + 金↑ + 通胀预期↑'],
               ['btc_liquidity','BTC 流动性','净流动性·相对强弱·信用']];
  const box = el('div');
  map.forEach(([k,label,sub]) => {
    const s = d.scores[k]; if (!s) return;
    const r = el('div','srow');
    r.innerHTML = `<div class="lbl">${label}<small>${sub}</small></div>
      <div class="val" style="color:${SCORE_COLOR(s.score)}">${fmtNum(s.score,0)}</div>
      <div class="sb"><i style="width:${Math.max(1,s.score)}%;background:${SCORE_COLOR(s.score)}"></i></div>`;
    box.appendChild(r);
  });
  $('#scores').replaceChildren(box);
}

function renderSignals(d, target) {
  const order = [['gold','黄金'],['btc','BTC'],['ust30','30Y 美债'],['usd','美元']];
  const box = el('div');
  order.forEach(([k,label]) => {
    const s = d.signals[k]; if (!s) return;
    const r = el('div','sig');
    r.innerHTML = `<span class="e">${s.emoji}</span>
      <span class="n">${label}</span>
      <span class="s ${CLS(s.signal)}">${s.signal_cn}</span>
      <span class="r">${s.reason}</span>`;
    box.appendChild(r);
  });
  const t = document.querySelector(target || '#signals');
  if (t) t.replaceChildren(box);
}

function renderChecklist(target, data, titleEl, titleTxt, extra) {
  const box = el('div');
  if (titleEl) $(titleEl).innerHTML = titleTxt;
  box.appendChild(el('div','prog', data.note || ''));
  if (extra) box.appendChild(extra);
  (data.items||[]).forEach(it => {
    const r = el('div','chk ' + chkCls(it.passed));
    r.innerHTML = `<div class="box">${chkBox(it.passed)}</div>
      <div class="body"><div class="t">${it.label_cn||it.label||it.key}</div>
      <div class="d">${it.target||''} ${it.detail? '· '+it.detail : ''}</div></div>`;
    box.appendChild(r);
  });
  $(target).replaceChildren(box);
}

function renderBreakers(d) {
  const b = d.breakers || {breakers:[]};
  const box = el('div');
  box.appendChild(el('div','prog',
    b.active_count > 0
      ? `触发 ${b.active_count} 项，支持性评分已扣减 ${fmtNum(b.thesis_penalty,0)} 分。这些是每天主动去证伪核心假设的检验项。`
      : '当前无反向信号触发。这不等于假设成立，只等于今天没有明显的反证。'));
  (b.breakers||[]).forEach(x => {
    const r = el('div','brk ' + (x.active ? 'active' : 'quiet'));
    r.innerHTML = `<div class="h">${x.active?'🚨':(x.active===false?'○':'◌')} ${x.name_cn}</div>
      <div class="ev">${x.evidence||''}</div>
      ${x.active ? `<div class="im">${x.implication_cn}</div>
        <div class="sevbar"><i style="width:${Math.round((x.severity||0)*100)}%"></i></div>` : ''}`;
    box.appendChild(r);
  });
  $('#breakers').replaceChildren(box);
}

function renderStrategy(d) {
  const order = [['gold','黄金'],['btc','BTC'],['ust30','30Y 美债'],['usd','美元']];
  const box = el('div');
  const note = (d.narrative.strategy && d.narrative.strategy[0])
    ? d.narrative.strategy[0].stage_note : '';
  if (note) box.appendChild(el('div','prog', note));

  order.forEach(([k,label]) => {
    const s = d.signals[k]; if (!s) return;
    const p = s.posture || {};
    const cls = p.arrow==='→' ? 'act-hold' : (p.arrow && p.arrow[0]==='↑' ? 'act-up' : 'act-down');
    const r = el('div','strat');
    r.innerHTML = `<span class="e">${s.emoji}</span>
      <span class="nm">${label}</span>
      <span class="sg ${CLS(s.signal)}">${s.signal_cn}</span>
      <span class="act ${cls}">${p.arrow||''} ${p.action_cn||'—'}
        <small>${p.action_en||''}</small></span>`;
    box.appendChild(r);

    const t = s.trajectory;
    if (t) {
      const chips = ['d5','d20','d60'].map(tag => {
        const x = t[tag];
        if (!x) return '';
        const n = tag==='d5'?'5日':(tag==='d20'?'20日':'60日');
        return `<span class="chip">${n} ${x.arrow}${x.delta?(x.delta>0?'+':'')+x.delta:''}</span>`;
      }).join('');
      box.appendChild(el('div','traj', `${chips}${t.summary_cn||''}`));
    }
  });

  box.appendChild(el('div','note',
    '这些是<b>风险姿态标签</b>，不是交易指令：没有仓位大小、没有价位、没有时点。'
    + '一次数据修正或 API 异常应该能改变一个标签，但绝不该改变一个仓位。<br>'
    + '「未来走势」本工具不提供——回测中该框架对金/BTC 未表现出前瞻预测力，'
    + '详见仓库 ASSESSMENT.md 第 3 节。它能告诉你的是：<b>宏观理由正在增强还是减弱</b>。'));
  $('#strategy').replaceChildren(box);
}

function renderScenarios(d) {
  const sc = d.scenarios; if (!sc) return;
  const box = el('div');
  box.appendChild(el('div','prog',
    `当前权重最高：<b>${sc.leader_cn}</b>。点各条可展开构成条件。`));

  sc.ranked.forEach(r => {
    const n = el('div','scen');
    n.innerHTML = `<div class="top"><span class="nm">${r.name_cn}</span>
        <span class="pc" style="color:${r.color}">${r.pct.toFixed(1)}%</span></div>
      <div class="bar"><i style="width:${Math.max(1,r.pct)}%;background:${r.color}"></i></div>
      <div class="toggle">展开构成 ▾</div>
      <div class="conds">${r.conditions.map(c =>
        `<div class="cond"><span class="cb"><i style="width:${Math.round(c.value*100)}%"></i></span>
         <span style="flex:1">${c.label_cn}</span><span>${c.detail}</span></div>`).join('')}</div>`;
    n.querySelector('.toggle').addEventListener('click', e => {
      n.classList.toggle('open');
      e.target.textContent = n.classList.contains('open') ? '收起 ▴' : '展开构成 ▾';
    });
    box.appendChild(n);
  });
  box.appendChild(el('div','note', sc.note));
  $('#scenarios').replaceChildren(box);
}

function renderTriggers(d) {
  const t = d.nearest_triggers || [];
  if (!t.length) return null;
  const n = el('div','trig');
  n.innerHTML = `<div class="th">最近的触发点</div>` + t.map(x =>
    `<div class="row ${x.primary_met?'compound':''}">
       <span>${x.label_cn}</span><b>${x.need_cn}</b></div>`).join('');
  return n;
}

function renderMetrics(d) {
  const box = el('div');
  (d.key_metrics||[]).forEach(m => {
    const c = fmtChg(m.change, m.change_unit);
    const stale = (m.stale_days!==null && m.stale_days!==undefined && m.stale_days > 5)
      ? ` <span class="stale" title="滞后 ${m.stale_days} 天"></span>` : '';
    const a = el('a');
    if (m.source_url) { a.href = m.source_url; a.target='_blank'; a.rel='noopener'; }
    a.innerHTML = `<div class="k">${m.label}${stale}</div>
      <div class="v">${fmtVal(m.value,m.unit)}</div>
      <div class="c ${c.c}">${c.t}</div>`;
    box.appendChild(a);
  });
  $('#metrics').replaceChildren(box);
}

function renderDecomp(d) {
  const y = d.yield_decomposition||{};
  const rows = [['实际利率','real_60d'],['通胀预期','breakeven_60d'],['期限溢价','termprem_60d']];
  const box = el('div');
  box.appendChild(el('div','prog',
    `10Y 名义 60 日变化 <b>${y.d10y_60d!==null&&y.d10y_60d!==undefined?((y.d10y_60d>=0?'+':'')+Math.round(y.d10y_60d*100)+'bp'):'—'}</b>，分解如下。<br>
     这是整个工具存在的理由：长端上行到底是<b>普通的实际利率重定价</b>，还是<b>市场在向财政部索要风险溢价</b>。两者对黄金/BTC 的含义完全相反。`));
  rows.forEach(([label,k]) => {
    const v = y[k];
    const r = el('div','srow');
    const c = fmtChg(v,'bp');
    r.innerHTML = `<div class="lbl">${label}</div>
      <div class="val ${c.c}" style="font-size:16px">${c.t}</div>`;
    box.appendChild(r);
  });
  if (y.driver_cn) box.appendChild(el('div','dc-note', '判定：' + y.driver_cn));
  $('#decomp').replaceChildren(box);
}

function renderRegime(d, h) {
  const box = el('div');
  const g = (d.key_metrics||[]).find(m=>m.key==='gold');
  const b = (d.key_metrics||[]).find(m=>m.key==='btc');
  const bg = (d.key_metrics||[]).find(m=>m.key==='btcgold');
  const sig = d.signals||{};
  box.appendChild(el('div','prog',
    `真正要回答的不是「金/BTC 会不会涨」，而是<b>现在该由黄金领先，还是 BTC 开始接棒</b>。
     BTC/黄金比价走强 = 市场从「避险 / 货币信用」切向「流动性 + 贬值 Beta」。`));
  [[g,'黄金'],[b,'BTC'],[bg,'BTC/黄金 比价']].forEach(([m,label]) => {
    if (!m) return;
    const c = fmtChg(m.change, m.change_unit);
    const r = el('div','srow');
    r.innerHTML = `<div class="lbl">${label}</div>
      <div class="val">${fmtVal(m.value,m.unit)} <span class="${c.c}" style="font-size:13px">${c.t}</span></div>`;
    box.appendChild(r);
  });
  const s1 = sig.gold||{}, s2 = sig.btc||{};
  box.appendChild(el('div','dc-note',
    `黄金 <b class="${CLS(s1.signal)}">${s1.signal_cn||'—'}</b> ·
     BTC <b class="${CLS(s2.signal)}">${s2.signal_cn||'—'}</b>`));
  $('#regime').replaceChildren(box);
}

function renderPolicy(d) {
  const p = d.policy||{};
  const warn = $('#ledgerwarn');
  warn.innerHTML = `政策事件<b>不从新闻自动抓取</b>。Stage 4（QE/YCC）是本系统后果最重的输出，
    交给模型每天读标题去判断，等于给它一个把「官员讨论资产负债表」读成「货币体制转换」的机会。
    因此台账由人工维护，每条带来源链接与 fact/inference 标记，<b>只有 fact 计分</b>，
    且 Stage 4 还必须同时满足「联储资产负债表确实在扩张」。<br><br>
    ⚠️ 当前 ${p.ledger_size||0} 条种子事件均标记为 <b>verified=false</b>，
    是为了让回测跑起来而预填的，<b>你需要逐条核对来源后再信任 Stage 4</b>。`;

  const box = el('div');
  box.appendChild(el('div','prog',
    `干预分 <b>${fmtNum(p.fiscal_intervention_score,1)}</b> ·
     压抑分 <b>${fmtNum(p.repression_score,1)}</b>（均按 ${45} 天半衰期衰减）`));
  const facts = p.recent_facts||[];
  if (!facts.length) box.appendChild(el('div','note','近一年无 fact 类事件。'));
  facts.slice().reverse().forEach(e => {
    const r = el('div','pev');
    r.innerHTML = `<div class="top">
        <span class="tag date">${e.date}</span>
        <span class="tag fact">FACT</span>
        ${e.verified===false?'<span class="tag unverified">未核验</span>':''}
        <span class="tag date">${e.event_type}</span>
        <span class="tag date">${e.age_days}天前 · ${e.decayed_points}分</span>
      </div>
      <div class="ti">${e.title||''}</div>
      ${e.source_url?`<a href="${e.source_url}" target="_blank" rel="noopener">来源 ↗</a>`:''}`;
    box.appendChild(r);
  });
  $('#policyfacts').replaceChildren(box);

  const ibox = el('div');
  const infs = p.recent_inferences||[];
  if (!infs.length) ibox.appendChild(el('div','note','无推测条目。推测永远不计分、不触发阶段。'));
  infs.slice().reverse().forEach(e => {
    const r = el('div','pev');
    r.innerHTML = `<div class="top"><span class="tag date">${e.date}</span>
        <span class="tag inference">INFERENCE</span></div>
      <div class="ti">${e.title||''}</div>
      ${e.source_url?`<a href="${e.source_url}" target="_blank" rel="noopener">来源 ↗</a>`:''}`;
    ibox.appendChild(r);
  });
  $('#policyinf').replaceChildren(ibox);

  const t = el('tbody');
  (d.sources||[]).forEach(s => {
    const tr = el('tr');
    tr.innerHTML = `<td>${s.series}</td>
      <td>${s.label}<br><span style="color:var(--tx3)">${s.source} · ${s.freq} · 发布滞后 ${s.release_lag_days}d</span></td>
      <td>${s.url?`<a href="${s.url}" target="_blank" rel="noopener">↗</a>`:''}</td>`;
    t.appendChild(tr);
  });
  $('#sources').replaceChildren(...t.childNodes);
}

function renderHistory(d, h) {
  const strip = el('div');
  const st = el('div','stagestrip');
  const step = Math.max(1, Math.floor(h.stage.length/180));
  for (let i=0;i<h.stage.length;i+=step) {
    const i2 = el('i');
    i2.style.background = STAGE_COLOR[h.stage[i]] || 'var(--line)';
    i2.title = `${h.dates[i]} Stage ${h.stage[i]}`;
    st.appendChild(i2);
  }
  strip.appendChild(st);
  strip.appendChild(el('div','striplbl',`<span>${h.dates[0]}</span><span>${h.dates.at(-1)}</span>`));
  $('#stagestrip').replaceChildren(strip);

  const box = el('div');
  box.appendChild(chartCard('综合评分', h.composite, h.dates, 'var(--o)'));
  box.appendChild(chartCard('财政压力', h.fiscal_stress, h.dates, 'var(--r)'));
  box.appendChild(chartCard('金融压抑', h.financial_repression, h.dates, 'var(--p)'));
  box.appendChild(chartCard('贬值交易', h.debasement, h.dates, 'var(--y)'));
  box.appendChild(chartCard('BTC 流动性', h.btc_liquidity, h.dates, 'var(--b)'));
  box.appendChild(chartCard('驱动分（蓝）vs 市场确认分', h.driver, h.dates, 'var(--b)'));
  box.appendChild(chartCard('市场确认分', h.confirmation, h.dates, 'var(--o)'));
  $('#histcharts').replaceChildren(box);

  const c = d.confidence||{};
  const cb = el('div');
  cb.appendChild(el('div','prog',
    `可信度 <b>${fmtNum(c.confidence,0)}</b> / 100（扣减 ${fmtNum(c.penalty,0)}）。
     扣减来自数据滞后——月频的 CPI 本来就有约两周发布延迟，
     用六周前的 CPI 去喊「金融压抑」值得打一个星号。`));
  (c.notes||[]).forEach(n => cb.appendChild(el('div','movers',`<div>${n}</div>`)));
  if (!(c.notes||[]).length) cb.appendChild(el('div','note','所有序列均在正常发布节奏内。'));
  $('#confidence').replaceChildren(cb);
}

function renderMacroCharts(h) {
  const box = el('div');
  box.appendChild(chartCard('30Y 美债 (%)', h.DGS30, h.dates, 'var(--r)', '%'));
  box.appendChild(chartCard('10Y 实际利率 (%)', h.DFII10, h.dates, 'var(--p)', '%', {zero:0}));
  box.appendChild(chartCard('10Y 期限溢价 (%)', h.THREEFYTP10, h.dates, 'var(--o)', '%', {zero:0}));
  box.appendChild(chartCard('美元 DXY', h.DXY, h.dates, 'var(--b)'));
  $('#macrocharts').replaceChildren(box);
}

function renderAssetCharts(h) {
  const box = el('div');
  box.appendChild(chartCard('黄金 ($)', h.GOLD, h.dates, 'var(--y)', '$'));
  box.appendChild(chartCard('BTC ($)', h.BTC, h.dates, 'var(--o)', '$'));
  box.appendChild(chartCard('BTC / 黄金 比价', h.BTC_GOLD, h.dates, 'var(--g)'));
  $('#assetcharts').replaceChildren(box);
}

/* ------------------------------------------------------------------ boot */
async function load() {
  const bust = '?t=' + Math.floor(Date.now()/60000);
  const [d, h] = await Promise.all([
    fetch('data/latest.json'+bust).then(r=>r.json()),
    fetch('data/history.json'+bust).then(r=>r.json()),
  ]);

  const gen = new Date(d.generated_at);
  $('#asof').textContent = `数据 ${d.as_of} · 更新 ${gen.toLocaleString('zh-CN',
    {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}`;

  renderHero(d);
  $('#verdict').textContent = d.narrative.verdict;
  const mv = d.narrative.movers||[];
  $('#movers').replaceChildren(...(mv.length
    ? [el('div','', '<div style="color:var(--tx3);margin-bottom:4px">较昨日变动最大的分项</div>'),
       ...mv.map(m=>el('div','',`<div>${m}</div>`))]
    : []));
  renderStrategy(d);
  renderScenarios(d);
  renderDC(d);
  renderScores(d);
  renderChecklist('#nextstage', d.next_stage, '#nsTitle',
    `距离 Stage ${d.next_stage.target ?? '—'} · ${d.next_stage.target_name||''}`,
    renderTriggers(d));
  renderChecklist('#btcchk', d.btc_checklist);
  renderBreakers(d);
  renderMetrics(d);
  renderDecomp(d);
  renderRegime(d, h);
  renderPolicy(d);
  renderHistory(d, h);
  renderMacroCharts(h);
  renderAssetCharts(h);
}

document.querySelectorAll('nav button').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('nav button').forEach(x=>x.classList.toggle('on', x===b));
    document.querySelectorAll('.page').forEach(p =>
      p.classList.toggle('on', p.id === 'p'+'-'+b.dataset.p));
    window.scrollTo({top:0, behavior:'instant'});
  });
});

load().catch(err => {
  console.error(err);
  document.querySelector('.wrap').insertAdjacentHTML('beforeend',
    `<div class="warn">数据载入失败：${err.message}<br>
     若首次部署，请确认 GitHub Actions 已成功跑过一次并生成 docs/data/latest.json。</div>`);
});

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(()=>{}));
}
