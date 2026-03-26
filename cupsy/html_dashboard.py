"""
html_dashboard.py - Generates a self-contained HTML dashboard file.

Writes data/dashboard.html every WRITE_INTERVAL seconds.
Open this file in any browser — no server required.
The page auto-refreshes every 10 seconds so you always see live data.
"""

import asyncio
import json
import os
import time

from cupsy.paper_tracker import paper_tracker, SessionStats, OpenTrade, TradeRecord

WRITE_INTERVAL = 8   # seconds between HTML regeneration
OUTPUT_PATH = os.path.join("data", "dashboard.html")


def _fmt_pct(pct: float) -> str:
    return f"{pct:+.2f}%"


def _fmt_sol(sol: float) -> str:
    return f"{sol:+.4f}" if sol != 0 else "0.0000"


def _pnl_color(pct: float) -> str:
    if pct >= 20:
        return "#3fb950"
    if pct >= 0:
        return "#7ee787"
    if pct >= -15:
        return "#d29922"
    return "#f85149"


def _age_str(seconds: float) -> str:
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s:02d}s"


def _build_open_rows(open_trades: list) -> str:
    if not open_trades:
        return '<tr><td colspan="8" style="text-align:center;color:#8b949e;padding:24px">No open positions</td></tr>'
    rows = []
    for t in sorted(open_trades, key=lambda x: x.pnl_pct, reverse=True):
        color = _pnl_color(t.pnl_pct)
        updated_ago = int(time.time() - t.last_updated)
        live_price = f"${t.current_price_usd:.8f}" if t.current_price_usd > 0 else "—"
        rows.append(f"""
        <tr>
          <td><b>{t.symbol}</b></td>
          <td>${t.entry_price_usd:.8f}</td>
          <td>{live_price}</td>
          <td style="color:{color};font-weight:700">{_fmt_pct(t.pnl_pct)}</td>
          <td style="color:{color}">{_fmt_sol(t.pnl_sol)}</td>
          <td>{t.sol_spent:.4f}</td>
          <td>{_age_str(t.age_seconds)}</td>
          <td style="color:#8b949e">{updated_ago}s ago</td>
        </tr>""")
    return "".join(rows)


def _build_trade_rows(closed: list) -> str:
    if not closed:
        return '<tr><td colspan="9" style="text-align:center;color:#8b949e;padding:24px">No closed trades yet</td></tr>'
    rows = []
    for t in closed:
        color = _pnl_color(t.pnl_pct)
        border = "#3fb950" if t.won else "#f85149"
        icon = "✓" if t.won else "✗"
        icon_color = "#3fb950" if t.won else "#f85149"
        hold = _age_str(t.hold_seconds)
        rows.append(f"""
        <tr style="border-left:3px solid {border}">
          <td style="color:{icon_color};font-weight:700">{icon} {t.trade_id}</td>
          <td><b>{t.symbol}</b></td>
          <td style="color:{color};font-weight:700">{_fmt_pct(t.pnl_pct)}</td>
          <td style="color:{color}">{_fmt_sol(t.pnl_sol)}</td>
          <td>${t.entry_price_usd:.8f}</td>
          <td>${t.exit_price_usd:.8f}</td>
          <td>{t.sol_spent:.4f}</td>
          <td>{t.sol_received:.4f}</td>
          <td>{hold}</td>
          <td style="color:#8b949e;font-size:12px">{t.exit_reason}</td>
        </tr>""")
    return "".join(rows)


def _build_chart_data(closed: list) -> str:
    if not closed:
        return "[], []"
    labels = []
    values = []
    running = 0.0
    for t in sorted(closed, key=lambda x: x.exit_time):
        running += t.pnl_sol
        labels.append(f'"{t.trade_id} {t.symbol}"')
        values.append(f"{running:.4f}")
    return f"[{','.join(labels)}]", f"[{','.join(values)}]"


def generate_html() -> str:
    stats = paper_tracker.get_stats()
    open_trades = paper_tracker.get_open_trades()
    closed_trades = paper_tracker.get_closed_trades(limit=100)

    uptime_s = int(time.time() - stats.session_start)
    pnl_color = _pnl_color(0 if stats.total_pnl_sol >= 0 else -100)
    pnl_color = "#3fb950" if stats.total_pnl_sol >= 0 else "#f85149"
    wr_color = "#3fb950" if stats.win_rate >= 50 else "#f85149"

    open_rows = _build_open_rows(open_trades)
    trade_rows = _build_trade_rows(closed_trades)
    chart_result = _build_chart_data(closed_trades)
    chart_labels, chart_values = chart_result if isinstance(chart_result, tuple) else ("[]", "[]")

    last_updated = time.strftime("%H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta http-equiv="refresh" content="10"/>
<title>Cupsy Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0d1117; color: #e6edf3; font-family: 'Segoe UI', monospace; font-size: 14px; }}
  h2 {{ color: #79c0ff; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }}

  /* Header */
  .header {{ background: #161b22; border-bottom: 1px solid #30363d; padding: 14px 24px; display: flex; align-items: center; justify-content: space-between; }}
  .logo {{ font-size: 22px; font-weight: 800; color: #79c0ff; letter-spacing: 2px; }}
  .badge {{ background: #388bfd22; color: #79c0ff; border: 1px solid #388bfd; border-radius: 4px; padding: 2px 10px; font-size: 12px; margin-left: 10px; }}
  .updated {{ color: #8b949e; font-size: 12px; }}
  .live-dot {{ display: inline-block; width: 8px; height: 8px; background: #3fb950; border-radius: 50%; margin-right: 6px; animation: blink 2s infinite; }}
  @keyframes blink {{ 0%,100%{{opacity:1}} 50%{{opacity:0.2}} }}

  /* Stats bar */
  .stats-bar {{ background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 24px; display: flex; gap: 32px; flex-wrap: wrap; }}
  .stat {{ display: flex; flex-direction: column; }}
  .stat-label {{ color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .stat-value {{ font-size: 18px; font-weight: 700; margin-top: 2px; }}

  /* Grid */
  .grid {{ display: grid; grid-template-columns: 1fr 420px; gap: 16px; padding: 16px 24px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }}
  .full {{ grid-column: 1 / -1; }}

  /* Tables */
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; padding: 8px 12px; text-align: left; border-bottom: 1px solid #30363d; }}
  td {{ padding: 10px 12px; border-bottom: 1px solid #21262d; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #1c2128; }}

  /* Chart */
  .chart-wrap {{ position: relative; height: 220px; }}

  /* Scrollable trade history */
  .scroll-wrap {{ max-height: 380px; overflow-y: auto; }}
  .scroll-wrap::-webkit-scrollbar {{ width: 4px; }}
  .scroll-wrap::-webkit-scrollbar-track {{ background: #161b22; }}
  .scroll-wrap::-webkit-scrollbar-thumb {{ background: #30363d; border-radius: 2px; }}
</style>
</head>
<body>

<div class="header">
  <div style="display:flex;align-items:center;gap:12px">
    <span class="logo">CUPSY</span>
    <span class="badge">PAPER TRADING</span>
  </div>
  <div class="updated">
    <span class="live-dot"></span>
    Last updated: {last_updated} &nbsp;·&nbsp; Auto-refreshes every 10s
  </div>
</div>

<div class="stats-bar">
  <div class="stat">
    <span class="stat-label">Uptime</span>
    <span class="stat-value" id="uptime" style="color:#79c0ff">—</span>
  </div>
  <div class="stat">
    <span class="stat-label">Scanned</span>
    <span class="stat-value">{stats.tokens_scanned}</span>
  </div>
  <div class="stat">
    <span class="stat-label">Bought</span>
    <span class="stat-value">{stats.tokens_bought}</span>
  </div>
  <div class="stat">
    <span class="stat-label">Closed Trades</span>
    <span class="stat-value">{stats.total_trades}</span>
  </div>
  <div class="stat">
    <span class="stat-label">Win Rate</span>
    <span class="stat-value" style="color:{wr_color}">{stats.win_rate:.0f}%</span>
  </div>
  <div class="stat">
    <span class="stat-label">Total P&amp;L</span>
    <span class="stat-value" style="color:{pnl_color}">{stats.total_pnl_sol:+.4f} SOL</span>
  </div>
  <div class="stat">
    <span class="stat-label">Best Trade</span>
    <span class="stat-value" style="color:#3fb950">{stats.best_trade_pct:+.1f}%</span>
  </div>
  <div class="stat">
    <span class="stat-label">Worst Trade</span>
    <span class="stat-value" style="color:#f85149">{stats.worst_trade_pct:+.1f}%</span>
  </div>
  <div class="stat">
    <span class="stat-label">Open Now</span>
    <span class="stat-value">{len(open_trades)}</span>
  </div>
</div>

<div class="grid">

  <!-- Open Positions -->
  <div class="card">
    <h2>Open Positions ({len(open_trades)})</h2>
    <table>
      <thead>
        <tr>
          <th>Token</th><th>Entry $</th><th>Live $</th>
          <th>P&amp;L %</th><th>P&amp;L SOL</th><th>SOL In</th><th>Age</th><th>Updated</th>
        </tr>
      </thead>
      <tbody>
        {open_rows}
      </tbody>
    </table>
  </div>

  <!-- P&L Chart -->
  <div class="card">
    <h2>Cumulative P&amp;L (SOL)</h2>
    <div class="chart-wrap">
      <canvas id="pnlChart"></canvas>
    </div>
    {"<p style='color:#8b949e;text-align:center;margin-top:60px;font-size:13px'>Waiting for first closed trade...</p>" if not closed_trades else ""}
  </div>

  <!-- Trade History -->
  <div class="card full">
    <h2>Trade History ({len(closed_trades)} trades)</h2>
    <div class="scroll-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th><th>Token</th><th>P&amp;L %</th><th>P&amp;L SOL</th>
            <th>Entry $</th><th>Exit $</th><th>SOL In</th><th>SOL Out</th>
            <th>Hold</th><th>Exit Reason</th>
          </tr>
        </thead>
        <tbody>
          {trade_rows}
        </tbody>
      </table>
    </div>
  </div>

</div>

<script>
// Uptime counter
const sessionStart = {int(stats.session_start)};
function updateUptime() {{
  const s = Math.floor(Date.now()/1000) - sessionStart;
  const h = Math.floor(s/3600).toString().padStart(2,'0');
  const m = Math.floor((s%3600)/60).toString().padStart(2,'0');
  const sec = (s%60).toString().padStart(2,'0');
  document.getElementById('uptime').textContent = h+':'+m+':'+sec;
}}
updateUptime();
setInterval(updateUptime, 1000);

// P&L Chart
const labels = {chart_labels};
const values = {chart_values};
if (labels.length > 0) {{
  const finalVal = values[values.length-1];
  const lineColor = finalVal >= 0 ? '#3fb950' : '#f85149';
  const ctx = document.getElementById('pnlChart').getContext('2d');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: labels,
      datasets: [{{
        label: 'Cumulative SOL P&L',
        data: values.map(Number),
        borderColor: lineColor,
        backgroundColor: lineColor + '22',
        borderWidth: 2,
        fill: true,
        tension: 0.3,
        pointRadius: 3,
        pointBackgroundColor: lineColor,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ display: false }},
        y: {{
          ticks: {{ color: '#8b949e', font: {{ size: 11 }} }},
          grid: {{ color: '#21262d' }},
        }}
      }}
    }}
  }});
}}
</script>
</body>
</html>"""


def write_dashboard() -> None:
    os.makedirs("data", exist_ok=True)
    html = generate_html()
    with open(OUTPUT_PATH, "w") as f:
        f.write(html)


async def run_html_dashboard() -> None:
    """Background asyncio task — regenerates dashboard.html every WRITE_INTERVAL seconds."""
    while True:
        try:
            write_dashboard()
        except Exception:
            pass
        await asyncio.sleep(WRITE_INTERVAL)
