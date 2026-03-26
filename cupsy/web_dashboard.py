"""
web_dashboard.py - Real-time web dashboard for Cupsy paper trading bot.

Serves a single-page HTML dashboard at http://localhost:8080 (default).
API endpoints return JSON data from the paper_tracker singleton.

Endpoints:
  GET /              - Single-page HTML dashboard
  GET /api/stats     - SessionStats as JSON
  GET /api/positions - Open positions as JSON array
  GET /api/trades    - Last 50 closed trades as JSON array
  GET /api/chart     - Cumulative P&L data for chart
"""

import time
from aiohttp import web

from cupsy.paper_tracker import paper_tracker

# ---------------------------------------------------------------------------
# HTML Dashboard (embedded)
# ---------------------------------------------------------------------------

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CUPSY — Paper Trading Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:      #0d1117;
      --card:    #161b22;
      --border:  #30363d;
      --green:   #3fb950;
      --red:     #f85149;
      --cyan:    #79c0ff;
      --text:    #e6edf3;
      --dim:     #8b949e;
      --yellow:  #d29922;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
      font-size: 13px;
      min-height: 100vh;
    }

    /* ── Header / Stats Bar ── */
    header {
      background: var(--card);
      border-bottom: 1px solid var(--border);
      padding: 10px 20px;
    }

    .header-top {
      display: flex;
      align-items: center;
      gap: 16px;
      margin-bottom: 8px;
    }

    .logo {
      font-size: 20px;
      font-weight: 700;
      color: var(--cyan);
      letter-spacing: 2px;
    }

    .badge {
      background: #1f2d1f;
      color: var(--green);
      border: 1px solid var(--green);
      border-radius: 4px;
      padding: 2px 8px;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 1px;
    }

    .live-indicator {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 12px;
      color: var(--dim);
    }

    .live-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--green);
      display: inline-block;
    }

    .live-dot.blink {
      animation: blink 0.4s ease;
    }

    @keyframes blink {
      0%   { opacity: 1; }
      50%  { opacity: 0.1; }
      100% { opacity: 1; }
    }

    .conn-lost {
      color: var(--yellow);
      font-size: 12px;
      display: none;
    }

    .stats-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 6px 20px;
      align-items: center;
    }

    .stat-item {
      display: flex;
      flex-direction: column;
      gap: 1px;
    }

    .stat-label {
      font-size: 10px;
      color: var(--dim);
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .stat-value {
      font-size: 15px;
      font-weight: 700;
      color: var(--text);
    }

    .stat-value.green  { color: var(--green); }
    .stat-value.red    { color: var(--red); }
    .stat-value.cyan   { color: var(--cyan); }
    .stat-value.yellow { color: var(--yellow); }

    /* ── Main Grid ── */
    .main-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      grid-template-rows: auto;
      gap: 12px;
      padding: 12px;
    }

    /* ── Cards ── */
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
    }

    .card-header {
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      font-size: 12px;
      font-weight: 700;
      color: var(--cyan);
      text-transform: uppercase;
      letter-spacing: 1px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .card-count {
      background: #21262d;
      color: var(--dim);
      border-radius: 10px;
      padding: 1px 8px;
      font-size: 11px;
      font-weight: 400;
    }

    /* ── Tables ── */
    .table-wrap {
      overflow-x: auto;
      max-height: 280px;
      overflow-y: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      white-space: nowrap;
    }

    thead th {
      position: sticky;
      top: 0;
      background: #1c2128;
      color: var(--dim);
      font-size: 10px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      padding: 6px 10px;
      text-align: right;
      border-bottom: 1px solid var(--border);
      font-weight: 600;
    }

    thead th:first-child { text-align: left; }

    tbody td {
      padding: 6px 10px;
      text-align: right;
      border-bottom: 1px solid #21262d;
      color: var(--text);
      font-size: 12px;
    }

    tbody td:first-child { text-align: left; }

    tbody tr:last-child td { border-bottom: none; }
    tbody tr:hover td { background: rgba(255,255,255,0.03); }

    .placeholder {
      text-align: center !important;
      color: var(--dim);
      padding: 30px !important;
      font-style: italic;
    }

    /* P&L badge cells */
    .pnl-badge {
      display: inline-block;
      padding: 2px 7px;
      border-radius: 3px;
      font-weight: 700;
      font-size: 11px;
    }

    .pnl-badge.pos { background: rgba(63, 185, 80, 0.18); color: var(--green); }
    .pnl-badge.neg { background: rgba(248, 81, 73, 0.18); color: var(--red); }
    .pnl-badge.neu { background: rgba(139, 148, 158, 0.15); color: var(--dim); }

    /* Trade history row accent */
    .trade-row-win td:first-child { border-left: 3px solid var(--green); padding-left: 7px; }
    .trade-row-loss td:first-child { border-left: 3px solid var(--red); padding-left: 7px; }

    /* ── Chart card (spans full width on second row of grid) ── */
    .chart-card { position: relative; }
    .chart-wrap {
      padding: 10px 14px 14px;
      height: 220px;
      position: relative;
    }

    .chart-placeholder {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--dim);
      font-size: 13px;
      font-style: italic;
    }

    /* ── Trade History (full width) ── */
    .full-width {
      grid-column: 1 / -1;
    }

    .history-wrap {
      overflow-x: auto;
      max-height: 340px;
      overflow-y: auto;
    }

    /* ── Scrollbar ── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--dim); }
  </style>
</head>
<body>

<!-- ── Header ── -->
<header>
  <div class="header-top">
    <span class="logo">CUPSY</span>
    <span class="badge">PAPER TRADING</span>
    <span class="conn-lost" id="connLost">&#9888; Connection lost</span>
    <div class="live-indicator">
      <span class="live-dot" id="liveDot"></span>
      <span id="liveLabel">LIVE</span>
    </div>
  </div>
  <div class="stats-bar">
    <div class="stat-item">
      <span class="stat-label">Uptime</span>
      <span class="stat-value cyan" id="uptime">--:--:--</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Scanned</span>
      <span class="stat-value" id="scanned">0</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Bought</span>
      <span class="stat-value" id="bought">0</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Win Rate</span>
      <span class="stat-value" id="winRate">0%</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Total P&amp;L</span>
      <span class="stat-value" id="totalPnl">0.000000 SOL</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Best Trade</span>
      <span class="stat-value green" id="bestTrade">0.00%</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Worst Trade</span>
      <span class="stat-value red" id="worstTrade">0.00%</span>
    </div>
    <div class="stat-item">
      <span class="stat-label">Open</span>
      <span class="stat-value cyan" id="openCount">0</span>
    </div>
  </div>
</header>

<!-- ── Main Content ── -->
<div class="main-grid">

  <!-- Open Positions -->
  <div class="card">
    <div class="card-header">
      Open Positions
      <span class="card-count" id="posCount">0</span>
    </div>
    <div class="table-wrap">
      <table id="posTable">
        <thead>
          <tr>
            <th>Token</th>
            <th>Entry Price</th>
            <th>Live Price</th>
            <th>P&amp;L %</th>
            <th>P&amp;L SOL</th>
            <th>SOL In</th>
            <th>Age</th>
            <th>Last Updated</th>
          </tr>
        </thead>
        <tbody id="posBody">
          <tr><td class="placeholder" colspan="8">No open positions</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- P&L Chart -->
  <div class="card chart-card">
    <div class="card-header">Cumulative P&amp;L</div>
    <div class="chart-wrap">
      <div class="chart-placeholder" id="chartPlaceholder">Waiting for first trade...</div>
      <canvas id="pnlChart"></canvas>
    </div>
  </div>

  <!-- Trade History -->
  <div class="card full-width">
    <div class="card-header">
      Trade History
      <span class="card-count" id="tradeCount">0</span>
    </div>
    <div class="history-wrap">
      <table id="tradeTable">
        <thead>
          <tr>
            <th>#</th>
            <th>Token</th>
            <th>P&amp;L %</th>
            <th>P&amp;L SOL</th>
            <th>Entry Price</th>
            <th>Exit Price</th>
            <th>SOL In</th>
            <th>SOL Out</th>
            <th>Hold Time</th>
            <th>Exit Reason</th>
          </tr>
        </thead>
        <tbody id="tradeBody">
          <tr><td class="placeholder" colspan="10">No closed trades yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>

</div><!-- /main-grid -->

<script>
// ── State ──────────────────────────────────────────────────────────────────
let sessionStart = null;   // Unix seconds (from server)
let chart = null;
let lastUpdate = Date.now();

// ── Uptime counter (client-side, ticks every second) ──────────────────────
setInterval(() => {
  if (sessionStart === null) return;
  const elapsed = Math.floor(Date.now() / 1000 - sessionStart);
  const h = Math.floor(elapsed / 3600);
  const m = Math.floor((elapsed % 3600) / 60);
  const s = elapsed % 60;
  document.getElementById('uptime').textContent =
    String(h).padStart(2,'0') + ':' +
    String(m).padStart(2,'0') + ':' +
    String(s).padStart(2,'0');
}, 1000);

// ── Helpers ────────────────────────────────────────────────────────────────
function pnlClass(v) {
  if (v > 0) return 'pos';
  if (v < 0) return 'neg';
  return 'neu';
}

function pnlBadge(val, suffix) {
  const cls = pnlClass(val);
  const sign = val >= 0 ? '+' : '';
  return `<span class="pnl-badge ${cls}">${sign}${val.toFixed(2)}${suffix}</span>`;
}

function fmtPrice(v) {
  if (v >= 1) return '$' + v.toFixed(4);
  if (v >= 0.0001) return '$' + v.toFixed(6);
  return '$' + v.toFixed(10);
}

function fmtAge(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m + 'm' + String(s).padStart(2,'0') + 's';
}

function fmtHold(seconds) {
  if (seconds < 60) return Math.floor(seconds) + 's';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m < 60) return m + 'm' + String(s).padStart(2,'0') + 's';
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return h + 'h' + String(rm).padStart(2,'0') + 'm';
}

function fmtSol(v) {
  return v.toFixed(6) + ' SOL';
}

// ── Blink live indicator ───────────────────────────────────────────────────
function blinkLive() {
  const dot = document.getElementById('liveDot');
  dot.classList.remove('blink');
  void dot.offsetWidth;  // reflow to restart animation
  dot.classList.add('blink');
}

// ── Update Stats Bar ───────────────────────────────────────────────────────
function updateStats(data) {
  if (data.session_start) {
    // Parse "YYYY-MM-DD HH:MM:SS" as local time
    if (sessionStart === null) {
      const parts = data.session_start.match(/(\\d+)-(\\d+)-(\\d+) (\\d+):(\\d+):(\\d+)/);
      if (parts) {
        sessionStart = new Date(
          parts[1], parts[2]-1, parts[3], parts[4], parts[5], parts[6]
        ).getTime() / 1000;
      }
    }
  }

  const wr = data.win_rate ?? 0;
  const wrEl = document.getElementById('winRate');
  wrEl.textContent = wr.toFixed(1) + '%';
  wrEl.className = 'stat-value ' + (wr >= 50 ? 'green' : wr >= 40 ? 'yellow' : 'red');

  document.getElementById('scanned').textContent = data.tokens_scanned ?? 0;
  document.getElementById('bought').textContent = data.tokens_bought ?? 0;
  document.getElementById('openCount').textContent = data.open_positions ?? 0;

  const pnl = data.total_pnl_sol ?? 0;
  const pnlEl = document.getElementById('totalPnl');
  const sign = pnl >= 0 ? '+' : '';
  pnlEl.textContent = sign + pnl.toFixed(6) + ' SOL';
  pnlEl.className = 'stat-value ' + (pnl > 0 ? 'green' : pnl < 0 ? 'red' : '');

  document.getElementById('bestTrade').textContent = (data.best_trade_pct ?? 0).toFixed(2) + '%';
  document.getElementById('worstTrade').textContent = (data.worst_trade_pct ?? 0).toFixed(2) + '%';
}

// ── Update Open Positions ──────────────────────────────────────────────────
function updatePositions(positions) {
  document.getElementById('posCount').textContent = positions.length;
  const tbody = document.getElementById('posBody');

  if (positions.length === 0) {
    tbody.innerHTML = '<tr><td class="placeholder" colspan="8">No open positions</td></tr>';
    return;
  }

  // Sort by P&L % desc
  positions.sort((a, b) => (b.pnl_pct ?? 0) - (a.pnl_pct ?? 0));

  const now = Date.now() / 1000;
  tbody.innerHTML = positions.map(p => {
    const ageSec = now - (p.entry_time ?? now);
    const updatedAgo = Math.floor(now - (p.last_updated ?? now));
    const livePrice = p.current_price_usd > 0
      ? fmtPrice(p.current_price_usd)
      : '<span style="color:var(--dim)">fetching...</span>';
    return `
      <tr>
        <td><strong>${p.symbol}</strong></td>
        <td>${fmtPrice(p.entry_price_usd)}</td>
        <td>${livePrice}</td>
        <td>${pnlBadge(p.pnl_pct ?? 0, '%')}</td>
        <td>${pnlBadge(p.pnl_sol ?? 0, ' SOL')}</td>
        <td>${(p.sol_spent ?? 0).toFixed(4)}</td>
        <td>${fmtAge(ageSec)}</td>
        <td>${updatedAgo}s ago</td>
      </tr>`;
  }).join('');
}

// ── Update Trade History ───────────────────────────────────────────────────
function updateTrades(trades) {
  document.getElementById('tradeCount').textContent = trades.length;
  const tbody = document.getElementById('tradeBody');

  if (trades.length === 0) {
    tbody.innerHTML = '<tr><td class="placeholder" colspan="10">No closed trades yet</td></tr>';
    return;
  }

  tbody.innerHTML = trades.map((t, i) => {
    const rowCls = t.won ? 'trade-row-win' : 'trade-row-loss';
    return `
      <tr class="${rowCls}">
        <td><strong>${t.trade_id}</strong></td>
        <td><strong>${t.symbol}</strong></td>
        <td>${pnlBadge(t.pnl_pct ?? 0, '%')}</td>
        <td>${pnlBadge(t.pnl_sol ?? 0, ' SOL')}</td>
        <td>${fmtPrice(t.entry_price_usd)}</td>
        <td>${fmtPrice(t.exit_price_usd)}</td>
        <td>${(t.sol_spent ?? 0).toFixed(6)}</td>
        <td>${(t.sol_received ?? 0).toFixed(6)}</td>
        <td>${fmtHold(t.hold_seconds ?? 0)}</td>
        <td>${t.exit_reason ?? ''}</td>
      </tr>`;
  }).join('');
}

// ── Update P&L Chart ───────────────────────────────────────────────────────
function updateChart(chartData) {
  const placeholder = document.getElementById('chartPlaceholder');
  const canvas = document.getElementById('pnlChart');

  if (!chartData || chartData.length === 0) {
    placeholder.style.display = 'flex';
    canvas.style.display = 'none';
    return;
  }

  placeholder.style.display = 'none';
  canvas.style.display = 'block';

  const labels = chartData.map(p => p.label);
  const values = chartData.map(p => p.cumulative_pnl_sol);
  const lastVal = values[values.length - 1] ?? 0;
  const lineColor = lastVal >= 0 ? '#3fb950' : '#f85149';
  const fillColor = lastVal >= 0 ? 'rgba(63,185,80,0.12)' : 'rgba(248,81,73,0.12)';

  if (chart) {
    chart.data.labels = labels;
    chart.data.datasets[0].data = values;
    chart.data.datasets[0].borderColor = lineColor;
    chart.data.datasets[0].backgroundColor = fillColor;
    chart.data.datasets[0].pointBackgroundColor = lineColor;
    chart.update('none');
    return;
  }

  chart = new Chart(canvas, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Cumulative P&L (SOL)',
        data: values,
        borderColor: lineColor,
        backgroundColor: fillColor,
        borderWidth: 2,
        pointRadius: values.length <= 30 ? 3 : 0,
        pointBackgroundColor: lineColor,
        fill: true,
        tension: 0.3,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#161b22',
          borderColor: '#30363d',
          borderWidth: 1,
          titleColor: '#79c0ff',
          bodyColor: '#e6edf3',
          callbacks: {
            label: ctx => {
              const v = ctx.parsed.y;
              const sign = v >= 0 ? '+' : '';
              return sign + v.toFixed(6) + ' SOL';
            }
          }
        }
      },
      scales: {
        x: {
          ticks: { color: '#8b949e', font: { size: 10 }, maxTicksLimit: 10 },
          grid: { color: '#21262d' },
        },
        y: {
          ticks: {
            color: '#8b949e',
            font: { size: 10 },
            callback: v => (v >= 0 ? '+' : '') + v.toFixed(4),
          },
          grid: { color: '#21262d' },
        }
      }
    }
  });
}

// ── Fetch & refresh loop ───────────────────────────────────────────────────
async function fetchAll() {
  try {
    const [statsRes, posRes, tradesRes, chartRes] = await Promise.all([
      fetch('/api/stats'),
      fetch('/api/positions'),
      fetch('/api/trades'),
      fetch('/api/chart'),
    ]);

    const [stats, positions, trades, chartData] = await Promise.all([
      statsRes.json(),
      posRes.json(),
      tradesRes.json(),
      chartRes.json(),
    ]);

    updateStats(stats);
    updatePositions(positions);
    updateTrades(trades);
    updateChart(chartData);
    blinkLive();

    document.getElementById('connLost').style.display = 'none';
    lastUpdate = Date.now();
  } catch (err) {
    document.getElementById('connLost').style.display = 'inline';
    console.warn('Dashboard fetch error:', err);
  }
}

fetchAll();
setInterval(fetchAll, 4000);
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# CORS helper
# ---------------------------------------------------------------------------

def _cors(response: web.Response) -> web.Response:
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

async def _handle_index(request: web.Request) -> web.Response:
    return web.Response(text=_HTML, content_type='text/html')


async def _handle_stats(request: web.Request) -> web.Response:
    try:
        stats = paper_tracker.get_stats()
        import time as _time
        data = {
            'session_start': _fmt_time(stats.session_start),
            'total_trades': stats.total_trades,
            'winning_trades': stats.winning_trades,
            'losing_trades': stats.losing_trades,
            'win_rate': round(stats.win_rate, 2),
            'total_pnl_sol': round(stats.total_pnl_sol, 6),
            'best_trade_pct': round(stats.best_trade_pct, 2),
            'worst_trade_pct': round(stats.worst_trade_pct, 2),
            'avg_hold_seconds': round(stats.avg_hold_seconds, 0),
            'open_positions': stats.open_positions,
            'tokens_scanned': stats.tokens_scanned,
            'tokens_bought': stats.tokens_bought,
        }
    except Exception as exc:
        data = {'error': str(exc)}
    return _cors(web.json_response(data))


async def _handle_positions(request: web.Request) -> web.Response:
    try:
        now = time.time()
        open_trades = paper_tracker.get_open_trades()
        data = [
            {
                'trade_id': t.trade_id,
                'symbol': t.symbol,
                'mint': t.mint,
                'entry_time': t.entry_time,
                'entry_price_usd': t.entry_price_usd,
                'current_price_usd': t.current_price_usd,
                'sol_spent': round(t.sol_spent, 6),
                'token_amount': t.token_amount,
                'pnl_pct': round(t.pnl_pct, 4),
                'pnl_sol': round(t.pnl_sol, 6),
                'age_seconds': round(now - t.entry_time, 1),
                'last_updated': t.last_updated,
                'price_snapshots': t.price_snapshots,
            }
            for t in open_trades
        ]
    except Exception as exc:
        data = []
    return _cors(web.json_response(data))


async def _handle_trades(request: web.Request) -> web.Response:
    try:
        closed = paper_tracker.get_closed_trades(limit=50)
        data = [
            {
                'trade_id': t.trade_id,
                'symbol': t.symbol,
                'mint': t.mint,
                'entry_time': t.entry_time,
                'exit_time': t.exit_time,
                'entry_price_usd': t.entry_price_usd,
                'exit_price_usd': t.exit_price_usd,
                'sol_spent': round(t.sol_spent, 6),
                'sol_received': round(t.sol_received, 6),
                'token_amount': t.token_amount,
                'pnl_sol': round(t.pnl_sol, 6),
                'pnl_pct': round(t.pnl_pct, 4),
                'exit_reason': t.exit_reason,
                'hold_seconds': round(t.hold_seconds, 1),
                'won': t.won,
            }
            for t in closed
        ]
    except Exception as exc:
        data = []
    return _cors(web.json_response(data))


async def _handle_chart(request: web.Request) -> web.Response:
    try:
        # Return all closed trades in chronological order for cumulative P&L
        all_closed = list(reversed(paper_tracker.get_closed_trades(limit=10000)))
        cumulative = 0.0
        data = []
        for i, t in enumerate(all_closed, 1):
            cumulative += t.pnl_sol
            data.append({
                'label': f'#{i} {t.symbol}',
                'trade_id': t.trade_id,
                'symbol': t.symbol,
                'cumulative_pnl_sol': round(cumulative, 6),
                'pnl_sol': round(t.pnl_sol, 6),
                'exit_time': t.exit_time,
            })
    except Exception as exc:
        data = []
    return _cors(web.json_response(data))


# ---------------------------------------------------------------------------
# App factory & startup
# ---------------------------------------------------------------------------

def _fmt_time(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_get('/', _handle_index)
    app.router.add_get('/api/stats', _handle_stats)
    app.router.add_get('/api/positions', _handle_positions)
    app.router.add_get('/api/trades', _handle_trades)
    app.router.add_get('/api/chart', _handle_chart)
    return app


async def start_server(host: str = '0.0.0.0', port: int = 8080) -> None:
    """
    Start the aiohttp web server. Call this as an asyncio task.
    Runs until the task is cancelled.
    """
    app = _build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    try:
        # Run forever until cancelled
        import asyncio
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
