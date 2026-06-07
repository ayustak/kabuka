// kabuka 静的ダッシュボード。保有銘柄中心UI。データはJSON、ペーパートレードはlocalStorage。
const DATA = "./data/";
let RECO = {}, PRICES = {}, GRID = [], METRICS = {};
const yen = v => "¥" + Math.round(v).toLocaleString();
const fmt = v => (v > 0 ? "+" : "") + v + "%";
const card = (l, v, c = "") => `<div class="card"><div class="label">${l}</div><div class="val ${c}">${v}</div></div>`;
const $ = id => document.getElementById(id);
const esc = s => (s || "").replace(/'/g, "");
async function getJSON(n) { return (await fetch(DATA + n + "?t=" + Date.now())).json(); }

async function init() {
  try {
    const [strat, acc, reco, px, grid, met] = await Promise.all([
      getJSON("strategy_summary.json"), getJSON("accuracy.json"), getJSON("recommendations.json"),
      getJSON("prices_latest.json"), getJSON("sim_grid.json"), getJSON("stock_metrics.json"),
    ]);
    RECO = reco; PRICES = px.prices || {}; GRID = grid.scenarios || []; METRICS = met.stocks || {};
    $("asof").textContent = "データ基準日: " + (px.date || "");
    renderSummary(strat); renderAccuracy(acc); renderReco("mid"); renderPaper(); runSim();
  } catch (e) { $("holdings").innerHTML = '<p class="neg">データ読み込みに失敗: ' + e + '</p>'; }
}

// ===== マイポートフォリオ =====
const PKEY = "kabuka_paper_v1";
const loadP = () => JSON.parse(localStorage.getItem(PKEY) || "null");
const saveP = p => localStorage.setItem(PKEY, JSON.stringify(p));
const price = c => PRICES[c];

function newsLinks(code4, name) {
  const q = encodeURIComponent(name);
  return `<div class="news">
    <a href="https://kabutan.jp/stock/?code=${code4}" target="_blank" rel="noopener">株探</a>
    <a href="https://finance.yahoo.co.jp/quote/${code4}.T" target="_blank" rel="noopener">Yahoo!ファイナンス</a>
    <a href="https://minkabu.jp/stock/${code4}" target="_blank" rel="noopener">みんかぶ</a>
    <a href="https://news.google.com/search?q=${q}&hl=ja&gl=JP" target="_blank" rel="noopener">Googleニュース</a>
  </div>`;
}
function metricsGrid(code, pos) {
  const m = METRICS[code] || {};
  const cell = (k, v) => `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  const pct = v => v == null ? "—" : v + "%";
  return `<div class="mgrid">
    ${cell("総合スコア", m.score_pct != null ? "上位 " + (100 - m.score_pct) + "%" : "—")}
    ${cell("割安度(資産)", m.value_bp ?? "—")}
    ${cell("割安度(利益)", m.value_ep ?? "—")}
    ${cell("収益性 ROE", m.roe != null ? (m.roe * 100).toFixed(1) + "%" : "—")}
    ${cell("直近1ヶ月", m.ret_1m != null ? fmt((m.ret_1m * 100).toFixed(1)) : "—")}
    ${cell("値動きの荒さ", pct(m.vol_pct))}
    ${cell("取得単価", pos.entry)}
    ${cell("損切り目安", pos.stop ?? "—")}
  </div>`;
}

function renderPaper() {
  const p = loadP();
  if (!p) {
    $("paperHead").innerHTML = '<p class="note">まだ始めていません。仮想資金を設定して、下の「買い候補」から買ってみましょう。</p>';
    $("holdings").innerHTML = "";
    $("paperCtl").innerHTML = '初期資金 <select id="icap"><option value="1000000">100万</option><option value="3000000" selected>300万</option><option value="5000000">500万</option></select> <button class="btn-buy" onclick="initP()">はじめる</button>';
    return;
  }
  let mkt = 0; const rows = [];
  const cur = {}; ["short", "mid", "long"].forEach(h => cur[h] = new Set((RECO[h]?.picks || []).map(x => x.code)));
  p.positions.forEach(pos => {
    const cp = price(pos.code) ?? pos.entry, val = cp * pos.shares; mkt += val;
    const pnl = (cp - pos.entry) * pos.shares, pct = (cp / pos.entry - 1) * 100;
    const sig = [];
    if (pos.stop && cp <= pos.stop) sig.push("損切り");
    if (cur[pos.horizon] && !cur[pos.horizon].has(pos.code)) sig.push("順位脱落");
    rows.push({ ...pos, cp, val, pnl, pct, sig });
  });
  const total = p.cash + mkt, tpnl = total - p.initial;
  $("paperHead").innerHTML = '<div class="cards">' +
    card("総資産", yen(total)) + card("損益", (tpnl >= 0 ? "+" : "") + yen(tpnl) + " (" + (tpnl / p.initial * 100).toFixed(1) + "%)", tpnl >= 0 ? "pos" : "neg") +
    card("現金", yen(p.cash)) + card("評価額", yen(mkt)) + '</div>';

  if (!rows.length) { $("holdings").innerHTML = '<p class="note">保有なし。下の「買い候補」から「買う」を押すと、ここに追加されます。</p>'; }
  else {
    let t = '<table><thead><tr><th>銘柄</th><th>株数</th><th>取得→現在</th><th>損益</th><th>サイン</th><th></th></tr></thead><tbody>';
    rows.forEach((r, i) => {
      const sigHtml = r.sig.length ? r.sig.map(s => `<span class="tag tag-sell">${s}</span>`).join(" ") : '<span class="tag tag-hold">保有継続</span>';
      t += `<tr class="hold-row" onclick="toggle(${i})"><td>${r.name}<br><span class="note">${r.code} ▾</span></td>
        <td>${r.shares}株</td><td>${r.entry}→${r.cp}</td>
        <td class="${r.pnl >= 0 ? 'pos' : 'neg'}">${r.pnl >= 0 ? '+' : ''}${yen(r.pnl)}<br>${r.pct.toFixed(1)}%</td>
        <td>${sigHtml}</td><td><button class="btn-sell" onclick="event.stopPropagation();sell('${r.code}')">売る</button></td></tr>
        <tr class="detail" id="d${i}" style="display:none"><td colspan="6">
          <div class="note" style="margin-bottom:4px">売買判断の指標（基準日 ${METRICS[r.code]?.code4 ? '' : ''}${($("asof").textContent || '')}）</div>
          ${metricsGrid(r.code, r)}
          <div class="note" style="margin:8px 0 4px">📰 ニュース（外部サイト・別タブで開く）</div>
          ${newsLinks((METRICS[r.code]?.code4) || r.code.slice(0, 4), r.name)}
        </td></tr>`;
    });
    $("holdings").innerHTML = t + '</tbody></table>';
  }
  $("paperCtl").innerHTML = '<span class="note">約定・評価はデータ基準日の終値。記録はこの端末のみ。</span> <button class="btn-sell" onclick="resetP()">リセット</button>';
}
function toggle(i) { const d = $("d" + i); if (d) d.style.display = d.style.display === "none" ? "" : "none"; }
function initP() { const c = +$("icap").value; saveP({ initial: c, cash: c, positions: [] }); renderPaper(); }
function resetP() { if (confirm("ペーパートレードをリセットしますか？")) { localStorage.removeItem(PKEY); renderPaper(); } }
function buy(code, name, hz, p, stop) {
  let port = loadP(); if (!port) { alert("先に「はじめる」で仮想資金を設定してください。"); return; }
  const sh = +prompt(`${name}(${code}) を何株？（100株単位 / 株価約${p}円）`, "100"); if (!sh) return;
  if (p * sh > port.cash) { alert("資金不足"); return; }
  port.cash -= p * sh; port.positions.push({ code, name, shares: sh, entry: p, horizon: hz, stop });
  saveP(port); renderPaper(); window.scrollTo({ top: 0, behavior: "smooth" });
}
function sell(code) {
  let port = loadP(); const pos = port.positions.find(x => x.code === code); if (!pos) return;
  if (!confirm(pos.name + " を売却しますか？")) return;
  const cp = price(code) ?? pos.entry; port.cash += cp * pos.shares;
  port.positions = port.positions.filter(x => x.code !== code); saveP(port); renderPaper();
}

// ===== 買い候補 =====
function renderReco(hz) {
  const r = RECO[hz]; if (!r) return;
  $("recoMeta").innerHTML = `信頼度 <span class="conf-${r.confidence[0]}">${r.confidence}</span>　${r.basis}　｜　売り目安: 損切り${r.sell_rules.stop_loss_pct.toFixed(0)}% / ${r.sell_rules.rank_exit}`;
  let t = '<table><thead><tr><th>#</th><th>銘柄</th><th>点数</th><th>株価</th><th>損切り目安</th><th></th></tr></thead><tbody>';
  r.picks.forEach((p, i) => { t += `<tr><td>${i + 1}</td><td>${p.name}<br><span class="note">${p.code}</span></td><td>${p.score}</td><td>${p.price}</td><td>${p.stop_loss_price}</td><td><button class="btn-buy" onclick="buy('${p.code}','${esc(p.name)}','${hz}',${p.price},${p.stop_loss_price})">買う</button></td></tr>`; });
  $("reco").innerHTML = t + '</tbody></table>';
}
document.querySelectorAll(".tab").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("active")); b.classList.add("active"); renderReco(b.dataset.h);
}));

// ===== 分析・検証 =====
function renderSummary(s) {
  $("summary").innerHTML = '<div class="cards">' +
    card("戦略 年率", s.ann_return_pct + "%") + card("シャープ", s.sharpe) + card("最大下落幅", s.max_dd_pct + "%", "neg") +
    card("市場平均との差(NISA)", fmt(s.excess_nisa_pct), s.excess_nisa_pct > 0 ? "pos" : "neg") +
    card("TOPIX 年率", s.cagr_topix_pct + "%") + '</div>';
}
function renderAccuracy(a) {
  let h = '<table><thead><tr><th>期間</th><th>信頼度</th><th>平均ﾘﾀｰﾝ</th><th>市場平均との差</th><th>勝率</th></tr></thead><tbody>';
  ["long", "mid", "short"].forEach(k => { const r = a[k]; if (!r || !r.n_periods) return; const ex = r.avg_excess_pct;
    h += `<tr><td>${r.label}</td><td class="conf-${r.confidence[0]}">${r.confidence}</td><td>${r.avg_pick_return_pct}%</td><td class="${ex > 0 ? 'pos' : 'neg'}">${fmt(ex)}</td><td>${r.win_rate_vs_topix_pct}%</td></tr>`; });
  $("accuracy").innerHTML = h + '</tbody></table>';
}
function runSim() {
  const key = [$("s_cap").value, $("s_nh").value, $("s_reb").value, $("s_acc").value].join("_");
  const r = GRID.find(x => x.key === key); if (!r) { $("simResult").innerHTML = '<p class="note">該当なし</p>'; return; }
  const ex = r.excess_pct;
  $("simResult").innerHTML = '<div class="cards">' + card("最終資産", yen(r.final_value), r.profit > 0 ? "pos" : "neg") +
    card("損益", (r.profit >= 0 ? "+" : "") + yen(r.profit), r.profit >= 0 ? "pos" : "neg") + card("年率", r.cagr_pct + "%") +
    card("市場平均との差", fmt(ex), ex > 0 ? "pos" : "neg") + card("最大下落幅", r.max_dd_pct + "%", "neg") +
    card("支払った税", yen(r.total_tax), r.total_tax > 0 ? "neg" : "") + '</div><p class="legend"><b>━ 戦略</b> <i>━ TOPIX</i> ' + r.period + '</p>';
  drawChart(r.equity_curve);
}
["s_cap", "s_nh", "s_reb", "s_acc"].forEach(id => $(id).addEventListener("change", runSim));
function drawChart(c) {
  if (!c || !c.length) return; const W = 1000, H = 220, pad = 10, vals = c.flatMap(x => [x.portfolio, x.topix]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const X = i => pad + (W - 2 * pad) * i / (c.length - 1), Y = v => H - pad - (H - 2 * pad) * (v - lo) / (hi - lo || 1);
  const ln = (k, col) => `<path d="${c.map((p, i) => (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(p[k]).toFixed(1)).join(" ")}" fill="none" stroke="${col}" stroke-width="2"/>`;
  $("simChart").innerHTML = ln("topix", "#8a93a6") + ln("portfolio", "#4a7cff");
}
init();
