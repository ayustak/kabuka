// kabuka 静的ダッシュボード（GitHub Pages用）。サーバー不要・データはJSON、ペーパートレードはlocalStorage。
const DATA = "./data/";
let RECO = {}, PRICES = {}, GRID = [];
const yen = v => "¥" + Math.round(v).toLocaleString();
const fmt = v => (v > 0 ? "+" : "") + v + "%";
const card = (l, v, c = "") => `<div class="card"><div class="label">${l}</div><div class="val ${c}">${v}</div></div>`;
const $ = id => document.getElementById(id);

async function getJSON(name) { return (await fetch(DATA + name + "?t=" + Date.now())).json(); }

async function init() {
  try {
    const [strat, acc, reco, px, grid] = await Promise.all([
      getJSON("strategy_summary.json"), getJSON("accuracy.json"),
      getJSON("recommendations.json"), getJSON("prices_latest.json"), getJSON("sim_grid.json"),
    ]);
    RECO = reco; PRICES = px.prices || {}; GRID = grid.scenarios || [];
    $("asof").textContent = "データ基準日: " + (px.date || "");
    renderSummary(strat); renderAccuracy(acc); renderReco("mid"); renderPaper(); runSim();
  } catch (e) {
    $("summary").innerHTML = '<p class="neg">データ読み込みに失敗しました。' + e + '</p>';
  }
}

function renderSummary(s) {
  $("summary").innerHTML = '<div class="cards">' +
    card("戦略 年率", s.ann_return_pct + "%") + card("シャープ", s.sharpe) +
    card("最大下落幅", s.max_dd_pct + "%", "neg") +
    card("市場平均との差(NISA)", fmt(s.excess_nisa_pct), s.excess_nisa_pct > 0 ? "pos" : "neg") +
    card("市場平均との差(課税)", fmt(s.excess_taxable_pct), s.excess_taxable_pct > 0 ? "pos" : "neg") +
    card("TOPIX 年率", s.cagr_topix_pct + "%") + '</div>';
}

function renderAccuracy(a) {
  let h = '<table><thead><tr><th>期間</th><th>信頼度</th><th>平均ﾘﾀｰﾝ</th><th>市場平均との差</th><th>勝率</th></tr></thead><tbody>';
  ["long", "mid", "short"].forEach(k => { const r = a[k]; if (!r || !r.n_periods) return;
    const ex = r.avg_excess_pct;
    h += `<tr><td>${r.label}</td><td class="conf-${r.confidence[0]}">${r.confidence}</td><td>${r.avg_pick_return_pct}%</td><td class="${ex > 0 ? 'pos' : 'neg'}">${fmt(ex)}</td><td>${r.win_rate_vs_topix_pct}%</td></tr>`;
  });
  $("accuracy").innerHTML = h + '</tbody></table>';
}

function renderReco(hz) {
  const r = RECO[hz]; if (!r) return;
  $("recoMeta").innerHTML = `信頼度 <span class="conf-${r.confidence[0]}">${r.confidence}</span>　根拠: ${r.basis}　｜　売り: 損切り${r.sell_rules.stop_loss_pct.toFixed(0)}% / ${r.sell_rules.rank_exit} / ${r.sell_rules.time_stop}　｜　基準 ${r.price_date}`;
  let t = '<table><thead><tr><th>#</th><th>銘柄</th><th>コード</th><th>点数</th><th>株価</th><th>損切り目安</th><th></th></tr></thead><tbody>';
  r.picks.forEach((p, i) => { t += `<tr><td>${i + 1}</td><td>${p.name}</td><td>${p.code}</td><td>${p.score}</td><td>${p.price}</td><td>${p.stop_loss_price}</td><td><button class="btn-buy" onclick="buy('${p.code}','${p.name.replace(/'/g, "")}','${hz}',${p.price},${p.stop_loss_price})">買う</button></td></tr>`; });
  $("reco").innerHTML = t + '</tbody></table>';
}
document.querySelectorAll(".tab").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active")); b.classList.add("active"); renderReco(b.dataset.h);
}));

// ---- ペーパートレード（localStorage） ----
const PKEY = "kabuka_paper_v1";
const loadP = () => JSON.parse(localStorage.getItem(PKEY) || "null");
const saveP = p => localStorage.setItem(PKEY, JSON.stringify(p));
const price = code => PRICES[code];

function renderPaper() {
  const p = loadP();
  if (!p) {
    $("paperHead").innerHTML = '<p class="note">まだ始めていません。仮想資金で開始しましょう。</p>';
    $("paperPositions").innerHTML = "";
    $("paperCtl").innerHTML = '初期資金 <select id="icap"><option value="1000000">100万</option><option value="3000000" selected>300万</option><option value="5000000">500万</option></select> <button class="btn-buy" onclick="initP()">開始</button>';
    return;
  }
  let mkt = 0; const rows = [];
  const curPicks = {}; ["short","mid","long"].forEach(h => curPicks[h] = new Set((RECO[h]?.picks || []).map(x => x.code)));
  p.positions.forEach(pos => {
    const cur = price(pos.code) ?? pos.entry; const val = cur * pos.shares; mkt += val;
    const pnl = (cur - pos.entry) * pos.shares, pct = (cur / pos.entry - 1) * 100;
    const sig = [];
    if (pos.stop && cur <= pos.stop) sig.push("損切り");
    if (curPicks[pos.horizon] && !curPicks[pos.horizon].has(pos.code)) sig.push("順位脱落");
    rows.push({ ...pos, cur, pnl, pct, sig });
  });
  const total = p.cash + mkt, tpnl = total - p.initial;
  $("paperHead").innerHTML = '<div class="cards">' +
    card("総資産", yen(total)) + card("損益", (tpnl >= 0 ? "+" : "") + yen(tpnl) + " (" + (tpnl / p.initial * 100).toFixed(1) + "%)", tpnl >= 0 ? "pos" : "neg") +
    card("現金", yen(p.cash)) + card("評価額", yen(mkt)) + '</div>';
  let t = '<table><thead><tr><th>銘柄</th><th>期間</th><th>株数</th><th>取得</th><th>現在</th><th>損益</th><th>サイン</th><th></th></tr></thead><tbody>';
  if (!rows.length) t += '<tr><td colspan="8" class="note">保有なし。「買い候補」から追加できます。</td></tr>';
  rows.forEach(r => { t += `<tr><td>${r.name}<br><span class="note">${r.code}</span></td><td>${r.horizon}</td><td>${r.shares}</td><td>${r.entry}</td><td>${r.cur}</td><td class="${r.pnl >= 0 ? 'pos' : 'neg'}">${r.pct.toFixed(1)}%</td><td>${r.sig.map(s => `<span class="badge">${s}</span>`).join("") || '<span class="note">—</span>'}</td><td><button class="btn-sell" onclick="sell('${r.code}')">売る</button></td></tr>`; });
  $("paperPositions").innerHTML = t + '</tbody></table>';
  $("paperCtl").innerHTML = '<span class="note">約定・評価はデータ基準日の終値。記録はこの端末のみ。</span> <button class="btn-sell" onclick="resetP()">リセット</button>';
}
function initP() { const c = +$("icap").value; saveP({ initial: c, cash: c, positions: [] }); renderPaper(); }
function resetP() { if (confirm("ペーパートレードをリセットしますか？")) { localStorage.removeItem(PKEY); renderPaper(); } }
function buy(code, name, hz, p, stop) {
  let port = loadP(); if (!port) { alert("先に「開始」で仮想資金を設定してください。"); return; }
  const sh = +prompt(`${name}(${code}) を何株？（100株単位 / 株価約${p}円）`, "100"); if (!sh) return;
  const cost = p * sh; if (cost > port.cash) { alert("資金不足"); return; }
  port.cash -= cost; port.positions.push({ code, name, shares: sh, entry: p, horizon: hz, stop });
  saveP(port); renderPaper();
}
function sell(code) {
  let port = loadP(); const pos = port.positions.find(x => x.code === code); if (!pos) return;
  const cur = price(code) ?? pos.entry; port.cash += cur * pos.shares;
  port.positions = port.positions.filter(x => x.code !== code); saveP(port); renderPaper();
}

// ---- シミュレーター（事前計算グリッド） ----
function runSim() {
  const key = [$("s_cap").value, $("s_nh").value, $("s_reb").value, $("s_acc").value].join("_");
  const r = GRID.find(x => x.key === key);
  if (!r) { $("simResult").innerHTML = '<p class="note">該当シナリオなし</p>'; return; }
  const ex = r.excess_pct;
  $("simResult").innerHTML = '<div class="cards">' +
    card("最終資産", yen(r.final_value), r.profit > 0 ? "pos" : "neg") +
    card("損益", (r.profit >= 0 ? "+" : "") + yen(r.profit), r.profit >= 0 ? "pos" : "neg") +
    card("年率", r.cagr_pct + "%") + card("市場平均との差", fmt(ex), ex > 0 ? "pos" : "neg") +
    card("最大下落幅", r.max_dd_pct + "%", "neg") + card("支払った税", yen(r.total_tax), r.total_tax > 0 ? "neg" : "") +
    '</div><p class="legend"><b>━ あなたの戦略</b>　<i>━ TOPIX</i>　' + r.period + '</p>';
  drawChart(r.equity_curve);
}
["s_cap", "s_nh", "s_reb", "s_acc"].forEach(id => $(id).addEventListener("change", runSim));
function drawChart(curve) {
  if (!curve || !curve.length) return;
  const W = 1000, H = 230, pad = 10, vals = curve.flatMap(c => [c.portfolio, c.topix]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const X = i => pad + (W - 2 * pad) * i / (curve.length - 1), Y = v => H - pad - (H - 2 * pad) * (v - lo) / (hi - lo || 1);
  const line = (k, c) => `<path d="${curve.map((p, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(p[k]).toFixed(1)).join(" ")}" fill="none" stroke="${c}" stroke-width="2"/>`;
  $("simChart").innerHTML = line("topix", "#8a93a6") + line("portfolio", "#4a7cff");
}
init();
