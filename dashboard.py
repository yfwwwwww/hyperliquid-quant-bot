"""Real-time web dashboard (mobile-first, card layout).

Serves:
  GET /            -> HTML
  GET /api/state   -> account / positions / equity / calendar / trades
  GET /api/scan    -> latest scan.json
"""
from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

from .client import HyperClient
from .config import Config

HTML = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>HL 交易看板</title>
<style>
:root{--bg:#070d18;--card:#0f1828;--card2:#111d31;--line:#1e2c45;--txt:#eef4ff;--mut:#8ba0c0;--green:#2ee6a6;--red:#ff6b7a;--blue:#4da3ff;--gold:#f5b942}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,sans-serif}
.app{max-width:760px;margin:0 auto;padding:16px 14px 40px}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:14px}
.brand{font-size:18px;font-weight:700}
.sub{color:var(--mut);font-size:12px}
.hero{background:linear-gradient(160deg,#12233d,#0c1526);border:1px solid var(--line);border-radius:20px;padding:18px;position:relative;overflow:hidden}
.hero:after{content:"";position:absolute;right:-60px;top:-60px;width:180px;height:180px;background:radial-gradient(circle,#1d4d7a55,transparent 70%)}
.hero .label{color:var(--mut);font-size:12px;letter-spacing:.5px}
.hero .equity{font-size:40px;font-weight:800;letter-spacing:-1px;margin:6px 0 10px}
.hero .row{display:flex;gap:10px;flex-wrap:wrap}
.pill{padding:5px 10px;border-radius:999px;font-size:12px;background:#0b1424;border:1px solid var(--line)}
.pill b{font-weight:700}
.pos-c{color:var(--green)}.neg-c{color:var(--red)}
.hero-right{position:absolute;right:18px;top:18px;text-align:right;font-size:12px;color:var(--mut)}
.hero-right b{display:block;font-size:16px;color:var(--txt);margin-top:2px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:12px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px}
.card .k{color:var(--mut);font-size:12px}
.card .v{font-size:22px;font-weight:700;margin-top:4px;letter-spacing:-.5px}
.card .k2{color:var(--mut);font-size:11px;margin-top:2px}
.bar{height:6px;border-radius:6px;background:#0a1322;margin-top:10px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:6px;background:var(--green)}
h2{font-size:14px;color:var(--mut);font-weight:600;margin:20px 0 10px;letter-spacing:.3px}
.pos{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:14px;margin-bottom:10px}
.pos-head{display:flex;align-items:center;gap:8px}
.coin{font-size:18px;font-weight:800}
.side{padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}
.side.long{background:#0e3a2c;color:var(--green)}
.side.short{background:#3d1420;color:var(--red)}
.lev{font-size:11px;color:var(--mut);border:1px solid var(--line);padding:2px 6px;border-radius:6px}
.r{margin-left:auto;font-weight:700}
.pos .upnl{font-size:26px;font-weight:800;margin:8px 0 2px}
.pos .sub{color:var(--mut);font-size:12px}
.levels{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 6px}
.levels span{font-size:11px;padding:3px 7px;border-radius:6px;border:1px solid var(--line);color:var(--mut)}
.levels .sl{color:var(--red);border-color:#5a2430}
.levels .tp{color:var(--green);border-color:#1d5142}
.progress{height:5px;background:#0a1322;border-radius:5px;overflow:hidden}
.progress i{display:block;height:100%;background:linear-gradient(90deg,#1f8f6b,#2ee6a6)}
svg{width:100%;height:200px;background:var(--card);border:1px solid var(--line);border-radius:18px}
.calendar{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.cal-head{color:var(--mut);font-size:11px;text-align:center;padding:4px 0}
.day{background:var(--card);border:1px solid var(--line);border-radius:10px;min-height:54px;padding:6px;font-size:11px;color:var(--mut)}
.day .d{font-size:11px}
.day .p{font-size:12px;font-weight:700;margin-top:4px}
.day.pos{background:#0d2a22;border-color:#1d5142}
.day.neg{background:#2a1118;border-color:#5a2430}
.trades{background:var(--card);border:1px solid var(--line);border-radius:16px;overflow:hidden}
.tr{display:flex;gap:8px;padding:10px 12px;border-bottom:1px solid var(--line);font-size:12px}
.tr:last-child{border-bottom:none}
.tr .t{color:var(--mut);width:74px;flex:none}
.tr .c{width:52px;font-weight:700}
.tr .a{width:44px;color:var(--mut)}
.tr .p{margin-left:auto;font-weight:700}
footer{color:var(--mut);font-size:11px;text-align:right;margin-top:14px}
@media(max-width:380px){.hero .equity{font-size:32px}.card .v{font-size:18px}}
</style>
</head>
<body>
<div class="app">
  <header>
    <div>
      <div class="brand">HL 交易看板</div>
      <div class="sub" id="acct">只读 · 更新中</div>
    </div>
    <div class="sub" id="asof">--</div>
  </header>

  <section class="hero">
    <div class="label">账户净值 · UNIFIED</div>
    <div class="equity" id="equity">--</div>
    <div class="row">
      <span class="pill">今日 <b id="today">--</b></span>
      <span class="pill">浮动 <b id="upnl">--</b></span>
    </div>
    <div class="hero-right">
      可用保证金<b id="avail">--</b>
      使用率<b id="margin">--</b>
    </div>
  </section>

  <section class="grid">
    <div class="card"><div class="k">可用保证金</div><div class="v" id="avail2">--</div><div class="k2" id="availnote">--</div></div>
    <div class="card"><div class="k">保证金使用率</div><div class="v" id="margin2">--</div><div class="bar"><i id="marginbar" style="width:0%"></i></div></div>
    <div class="card"><div class="k">组合风险</div><div class="v" id="risk">--</div><div class="bar"><i id="riskbar" style="width:0%;background:#4da3ff"></i></div></div>
    <div class="card"><div class="k">总名义敞口</div><div class="v" id="notional">--</div><div class="k2" id="lev">--</div></div>
  </section>

  <section>
    <h2>当前持仓 <span id="poscount" style="color:var(--mut);font-weight:400"></span></h2>
    <div id="positions"></div>
  </section>

  <section>
    <h2>今日权益曲线</h2>
    <svg id="chart" viewBox="0 0 1000 200" preserveAspectRatio="none"></svg>
  </section>

  <section>
    <h2>市场学习（funding / 基差 / 价差）</h2>
    <div class="trades" id="scan"></div>
  </section>

  <section>
    <h2>盈亏日历（UTC）</h2>
    <div id="calendar" class="calendar"></div>
  </section>

  <section>
    <h2>最近成交</h2>
    <div class="trades" id="trades"></div>
  </section>

  <section>
    <h2>交易日志（开仓时的盘口）</h2>
    <div class="trades" id="journal"></div>
  </section>

  <footer id="foot">--</footer>
</div>

<script>
const $ = id => document.getElementById(id);
const fmt = (v,d=2) => (v===null||v===undefined||isNaN(v)) ? '--' : Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const money = v => (v===null||v===undefined) ? '--' : '$' + fmt(v,2);
const pct = v => (v===null||v===undefined) ? '--' : (v*100).toFixed(2) + '%';
const cls = v => v>0 ? 'pos-c' : (v<0 ? 'neg-c' : '');
function fetchJSON(url, ms){
  return new Promise(function(resolve, reject){
    var ctrl = new AbortController();
    var t = setTimeout(function(){ try{ctrl.abort();}catch(e){} reject(new Error('timeout')); }, ms);
    fetch(url,{cache:'no-store',signal:ctrl.signal}).then(r=>r.json()).then(d=>{clearTimeout(t);resolve(d);}).catch(e=>{clearTimeout(t);reject(e);});
  });
}

function renderPositions(rows){
  const box = $('positions'); box.innerHTML = '';
  $('poscount').textContent = rows && rows.length ? `· ${rows.length} 个` : '';
  if(!rows || !rows.length){ box.innerHTML = '<div class="card" style="color:var(--mut)">无持仓</div>'; return; }
  for(const p of rows){
    const div = document.createElement('div'); div.className = 'pos';
    const sideCls = p.side === 'long' ? 'long' : 'short';
    const sideTxt = p.side === 'long' ? 'LONG' : 'SHORT';
    const rTxt = p.r_multiple === null || p.r_multiple === undefined ? '--' : (p.r_multiple>=0?'+':'') + fmt(p.r_multiple,2) + 'R';
    const roeTxt = p.roe === null ? '--' : (p.roe>=0?'+':'') + pct(p.roe);
    const lv = p.leverage ? p.leverage.toFixed(0)+'x' : '';
    const prog = p.progress === null || p.progress === undefined ? 0 : Math.max(0, Math.min(100, p.progress*100));
    div.innerHTML = `
      <div class="pos-head">
        <span class="coin">${p.coin.replace('xyz:','')}</span>
        <span class="side ${sideCls}">${sideTxt}</span>
        <span class="lev">${lv}</span>
        <span class="r ${cls(p.upnl)}">${rTxt}</span>
      </div>
      <div class="upnl ${cls(p.upnl)}">${p.upnl>=0?'+':''}${fmt(p.upnl,2)}</div>
      <div class="sub">数量 ${fmt(p.size,4)} · 名义 ${money(p.notional)} · ROE <span class="${cls(p.roe)}">${roeTxt}</span></div>
      <div class="levels">
        ${p.stop?`<span class="sl">SL ${fmt(p.stop,2)}</span>`:''}
        <span>入场 ${fmt(p.entry,2)}</span>
        ${p.tp1?`<span class="tp">TP1 ${fmt(p.tp1,2)}</span>`:''}
        ${p.tp2?`<span class="tp">TP2 ${fmt(p.tp2,2)}</span>`:''}
        <span>标记 ${fmt(p.mark,2)}</span>
      </div>
      <div class="progress"><i style="width:${prog}%"></i></div>
    `;
    box.appendChild(div);
  }
}

function renderChart(curve){
  const svg = $('chart'); svg.innerHTML = '';
  if(!curve || curve.length < 2) return;
  const ys = curve.map(p=>p.equity);
  const min = Math.min(...ys), max = Math.max(...ys);
  const span = (max-min) || 1;
  const n = curve.length;
  const pts = curve.map((p,i)=>`${(i/(n-1)*1000).toFixed(1)},${(190-((p.equity-min)/span)*170).toFixed(1)}`).join(' ');
  const poly = document.createElementNS('http://www.w3.org/2000/svg','polyline');
  poly.setAttribute('points', pts);
  poly.setAttribute('fill','none');
  poly.setAttribute('stroke','#2ee6a6');
  poly.setAttribute('stroke-width','2');
  svg.appendChild(poly);
}

function renderCalendar(days){
  const box = $('calendar'); box.innerHTML = '';
  const heads = ['一','二','三','四','五','六','日'];
  for(const h of heads){ const d=document.createElement('div'); d.className='cal-head'; d.textContent=h; box.appendChild(d); }
  const map = {};
  (days||[]).forEach(x=>{ map[x.date]=x.pnl; });
  const now = new Date();
  const y = now.getUTCFullYear(), m = now.getUTCMonth();
  const first = new Date(Date.UTC(y,m,1));
  const startDow = (first.getUTCDay()+6)%7;
  const daysInMonth = new Date(Date.UTC(y,m+1,0)).getUTCDate();
  for(let i=0;i<startDow;i++){ const d=document.createElement('div'); d.className='day'; box.appendChild(d); }
  for(let d=1; d<=daysInMonth; d++){
    const key = `${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const pnl = map[key];
    const el = document.createElement('div');
    el.className = 'day' + (pnl>0?' pos':(pnl<0?' neg':''));
    el.innerHTML = `<div class="d">${d}</div>` + (pnl!==undefined?`<div class="p ${cls(pnl)}">${pnl>=0?'+':''}${fmt(pnl,0)}</div>`:'');
    box.appendChild(el);
  }
}

function renderTrades(rows){
  const box = $('trades'); box.innerHTML = '';
  if(!rows || !rows.length){ box.innerHTML = '<div class="tr" style="color:var(--mut)">暂无成交</div>'; return; }
  for(const t of rows){
    const d = new Date(t.ts*1000);
    const ts = d.toLocaleString('zh-CN',{hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
    const div = document.createElement('div'); div.className='tr';
    div.innerHTML = `<span class="t">${ts}</span><span class="c">${(t.coin||'').replace('xyz:','')}</span>
      <span class="a">${t.side||''}</span><span class="${cls(t.pnl)} p">${t.pnl===null||t.pnl===undefined?'':(t.pnl>=0?'+':'')+fmt(t.pnl,2)}</span>`;
    box.appendChild(div);
  }
}

function renderScan(rows){
  const box = $('scan'); box.innerHTML = '';
  if(!rows || !rows.length){ box.innerHTML = '<div class="tr" style="color:var(--mut)">扫描未启动</div>'; return; }
  for(const r of rows){
    if(r.error) continue;
    const div = document.createElement('div'); div.className = 'tr';
    div.innerHTML = `<span class="c">${(r.coin||'').replace('xyz:','')}</span>
      <span class="t">24h ${fmt(r.chg_24h,2)}%</span>
      <span class="t">fund ${fmt(r.funding_apr,1)}%</span>
      <span class="t">基差 ${fmt(r.basis_pct,2)}%</span>
      <span class="t">sp ${fmt(r.spread_bps,1)}</span>
      <span class="p ${cls(r.score)}">${r.score>0?'+':''}${r.score}</span>`;
    box.appendChild(div);
  }
}

function renderJournal(rows){
  const box = $('journal'); box.innerHTML = '';
  if(!rows || !rows.length){ box.innerHTML = '<div class="tr" style="color:var(--mut)">暂无记录</div>'; return; }
  for(const j of rows){
    const d = new Date(j.ts*1000);
    const ts = d.toLocaleString('zh-CN',{hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
    const div = document.createElement('div'); div.className = 'tr';
    div.innerHTML = `<span class="t">${ts}</span><span class="c">${(j.coin||'').replace('xyz:','')}</span>
      <span class="a">${j.kind==='open'?'开':'平'}</span><span class="t">${j.side||''}</span>
      <span class="p ${cls(j.pnl)}">${j.pnl===null||j.pnl===undefined?'':(j.pnl>=0?'+':'')+fmt(j.pnl,2)}</span>`;
    box.appendChild(div);
  }
}

async function refresh(){
  try{
    const s = await fetchJSON('/api/state', 15000);
    if(s.error){ $('asof').textContent = '错误: '+s.error; return; }
    $('equity').textContent = money(s.equity);
    $('today').textContent = (s.today_pnl>=0?'+':'') + fmt(s.today_pnl,2);
    $('today').className = cls(s.today_pnl);
    $('upnl').textContent = (s.floating_pnl>=0?'+':'') + fmt(s.floating_pnl,2);
    $('upnl').className = cls(s.floating_pnl);
    $('avail').textContent = money(s.available);
    $('avail2').textContent = money(s.available);
    $('availnote').textContent = s.margin_used ? `已用 ${money(s.margin_used)}` : '--';
    $('margin').textContent = s.margin_usage===null?'--':pct(s.margin_usage);
    $('margin2').textContent = s.margin_usage===null?'--':pct(s.margin_usage);
    $('marginbar').style.width = Math.min(100,(s.margin_usage||0)*100) + '%';
    $('risk').textContent = s.portfolio_risk===null?'--':pct(s.portfolio_risk);
    $('riskbar').style.width = Math.min(100,(s.portfolio_risk||0)*100/0.36) + '%';
    $('notional').textContent = money(s.notional);
    $('lev').textContent = s.leverage ? `整体杠杆 ${s.leverage.toFixed(2)}x` : '--';
    $('acct').textContent = '账户 ' + (s.address||'--').slice(0,6) + '...' + (s.address||'').slice(-4) + ' · 只读';
    renderPositions(s.positions);
    renderChart(s.equity_today);
    renderCalendar(s.calendar);
    renderTrades(s.trades);
    try{ const sc = await fetchJSON('/api/scan', 8000); renderScan(sc.rows || []); }catch(e){ renderScan([]); }
    try{ const jr = await fetchJSON('/api/journal', 8000); renderJournal(jr.rows || []); }catch(e){ renderJournal([]); }
    $('asof').textContent = s.as_of || '--';
  }catch(e){ $('asof').textContent = '连接失败: ' + e.message; }
}
refresh();
setInterval(refresh, 5000);
</script>
</body>
</html>
"""


def _read_ledgers() -> List[Dict[str, Any]]:
    recs: List[Dict[str, Any]] = []
    for fn in os.listdir("."):
        if fn.startswith("ledger") and fn.endswith(".jsonl"):
            try:
                with open(fn, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                recs.append(json.loads(line))
                            except Exception:
                                pass
            except Exception:
                pass
    return recs


def _state_positions() -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for fn in os.listdir("."):
        if fn.startswith("state") and fn.endswith(".json"):
            try:
                with open(fn, "r", encoding="utf-8") as f:
                    d = json.load(f)
                for key in ("positions", "live_positions"):
                    for c, p in (d.get(key) or {}).items():
                        if p.get("side"):
                            out[c] = p
            except Exception:
                pass
    return out


_LATEST: Dict[str, Any] = {"data": None, "ts": 0.0}


def build_state(cfg: Config, client: HyperClient) -> Dict[str, Any]:
    data = _LATEST.get("data")
    if data is not None:
        return data
    return {"address": "", "equity": 0.0, "today_pnl": 0.0, "floating_pnl": 0.0,
            "margin_usage": None, "available": None, "margin_used": None,
            "portfolio_risk": None, "notional": 0.0, "leverage": 0.0,
            "positions": [], "equity_today": [], "calendar": [], "trades": [],
            "as_of": "warming up"}


def _build_state_uncached(cfg: Config, client: HyperClient) -> Dict[str, Any]:
    try:
        equity = client.get_equity()
    except Exception:
        equity = 0.0
    try:
        st = client.get_user_state("")
        mm = st.get("marginSummary", {})
        used = float(mm.get("totalMarginUsed", 0) or 0)
        acct_value = float(mm.get("accountValue", 0) or 0)
        available = max(equity - used, 0.0)
        margin_usage = (used / equity) if equity > 0 else None
    except Exception:
        used, available, margin_usage = 0.0, None, None

    try:
        positions = client.get_positions_all()
        mids = client.get_mids_all()
    except Exception:
        positions, mids = {}, {}

    meta = _state_positions()
    pos_rows: List[Dict[str, Any]] = []
    floating = 0.0
    notional = 0.0
    risk_sum = 0.0
    for coin, p in positions.items():
        mark = mids.get(coin, p["entry"])
        sign = 1 if p["side"] == "long" else -1
        upnl = (mark - p["entry"]) * p["size"] * sign
        floating += upnl
        notional += mark * p["size"]
        m = meta.get(coin, {})
        stop = m.get("stop")
        tp1 = m.get("tp1")
        tp2 = m.get("tp2")
        pos_risk = p["size"] * abs(p["entry"] - float(stop)) if stop else 0.0
        risk_sum += pos_risk
        r_mult = upnl / pos_risk if pos_risk > 0 else None
        roe = upnl / (p["entry"] * p["size"]) if p["entry"] and p["size"] else None
        progress = None
        if tp2:
            span = abs(float(tp2) - p["entry"])
            if span > 0:
                progress = ((mark - p["entry"]) * sign) / span
        pos_rows.append({
            "coin": coin, "side": p["side"], "size": p["size"], "entry": p["entry"],
            "mark": mark, "upnl": upnl, "roe": roe, "stop": stop, "tp1": tp1, "tp2": tp2,
            "r_multiple": r_mult, "notional": mark * p["size"],
            "progress": progress, "leverage": (mark * p["size"] / equity) if equity > 0 else None,
        })
    pos_rows.sort(key=lambda r: abs(r["upnl"]), reverse=True)

    try:
        port = client.get_portfolio()
    except Exception:
        port = []

    def _hist(period: str) -> List[List[Any]]:
        for name, data in port:
            if name == period and isinstance(data, dict):
                return data.get("accountValueHistory") or []
        return []

    day_hist = _hist("day")
    equity_today = [{"ts": int(int(ts) / 1000), "equity": float(v)} for ts, v in day_hist]
    if equity > 0:
        equity_today.append({"ts": int(time.time()), "equity": equity})

    today = time.strftime("%Y-%m-%d", time.gmtime())
    today_start = None
    for ts, v in day_hist:
        if time.strftime("%Y-%m-%d", time.gmtime(int(ts) / 1000)) == today:
            today_start = float(v)
            break
    if today_start is None and day_hist:
        today_start = float(day_hist[0][1])
    today_pnl = (equity - today_start) if (today_start is not None and equity > 0) else 0.0

    month_hist = _hist("month")
    daily: Dict[str, List[float]] = {}
    for ts, v in month_hist:
        d = time.strftime("%Y-%m-%d", time.gmtime(int(ts) / 1000))
        daily.setdefault(d, []).append(float(v))
    calendar = [{"date": d, "pnl": vals[-1] - vals[0]} for d, vals in sorted(daily.items())][-31:]
    # today must use the live equity, not the possibly-stale history tail
    if today in daily:
        cal = {x["date"]: x["pnl"] for x in calendar}
        cal[today] = today_pnl
        calendar = [{"date": d, "pnl": p} for d, p in sorted(cal.items())][-31:]

    recs = _read_ledgers()
    trades = [r for r in recs if r.get("kind") == "trade"]
    trades.sort(key=lambda r: r.get("ts", 0), reverse=True)


    return {
        "address": client.address(),
        "equity": equity,
        "today_pnl": today_pnl,
        "floating_pnl": floating,
        "margin_usage": margin_usage,
        "available": available,
        "margin_used": used,
        "portfolio_risk": (risk_sum / equity) if equity > 0 else None,
        "notional": notional,
        "leverage": (notional / equity) if equity > 0 else 0.0,
        "positions": pos_rows,
        "equity_today": equity_today,
        "calendar": calendar,
        "trades": trades[:30],
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }


def _refresher(cfg: Config, client: HyperClient, interval: float = 10.0) -> None:
    while True:
        try:
            _LATEST["data"] = _build_state_uncached(cfg, client)
            _LATEST["ts"] = time.time()
        except Exception:
            pass
        time.sleep(interval)


def run_dashboard(cfg: Config) -> None:
    from .bot import setup_logging

    setup_logging(cfg.log_level)
    client = HyperClient(cfg, trading=False)
    host, port = cfg.dashboard_host, cfg.dashboard_port
    import threading
    threading.Thread(target=_refresher, args=(cfg, client), daemon=True).start()
    print(f"Dashboard: http://{'127.0.0.1' if host in ('0.0.0.0','') else host}:{port}")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, body: bytes, ctype: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/api/scan"):
                try:
                    with open("scan.json", "r", encoding="utf-8") as f:
                        self._send(f.read().encode("utf-8"), "application/json; charset=utf-8")
                except Exception:
                    self._send(b'{"rows": []}', "application/json; charset=utf-8")
            elif self.path.startswith("/api/journal"):
                try:
                    from .journal import Journal
                    rows = Journal("journal_hf.jsonl").read(50)
                    self._send(json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8"),
                               "application/json; charset=utf-8")
                except Exception:
                    self._send(b'{"rows": []}', "application/json; charset=utf-8")
            elif self.path.startswith("/api/state"):
                try:
                    body = json.dumps(build_state(cfg, client), ensure_ascii=False).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8")
                except Exception as exc:
                    body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                    self._send(body, "application/json; charset=utf-8", 500)
            else:
                self._send(HTML.encode("utf-8"), "text/html; charset=utf-8")

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
