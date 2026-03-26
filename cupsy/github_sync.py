"""
github_sync.py - Pushes live paper trading data to GitHub every 60 seconds.

The bot writes docs/data.json to the GitHub repo via the API so the
hosted dashboard can read it without needing a server.

Required env vars:
  GITHUB_TOKEN  - Personal access token with repo write access
  GITHUB_OWNER  - Repo owner (default: jslack1202)
  GITHUB_REPO   - Repo name (default: cupsy)
  GITHUB_BRANCH - Branch to push to (default: claude/ai-memecoin-trader-idlFa)
"""

import asyncio
import base64
import json
import time
from datetime import datetime

import aiohttp

from config import config
from cupsy import logger
from cupsy.paper_tracker import paper_tracker


class GitHubSync:
    """Pushes live paper trading data to GitHub as docs/data.json every 60 seconds."""

    def __init__(self) -> None:
        self.token = config.github_token
        self.owner = config.github_owner
        self.repo = config.github_repo
        self.branch = config.github_branch
        self._file_sha: str | None = None
        self._api_base = "https://api.github.com"

    def _build_payload(self) -> dict:
        """Serialize current paper_tracker state into the dashboard JSON schema."""
        now = time.time()
        now_str = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")

        stats = paper_tracker.get_stats()
        open_trades = paper_tracker.get_open_trades()
        closed_trades = paper_tracker.get_closed_trades(limit=200)

        open_positions = [
            {
                "symbol": t.symbol,
                "mint": t.mint,
                "entry_price_usd": t.entry_price_usd,
                "current_price_usd": t.current_price_usd,
                "sol_spent": t.sol_spent,
                "pnl_pct": round(t.pnl_pct, 2),
                "pnl_sol": round(t.pnl_sol, 6),
                "age_seconds": round(t.age_seconds, 1),
                "last_updated": t.last_updated,
            }
            for t in open_trades
        ]

        closed_records = [
            {
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "entry_price_usd": t.entry_price_usd,
                "exit_price_usd": t.exit_price_usd,
                "sol_spent": round(t.sol_spent, 6),
                "sol_received": round(t.sol_received, 6),
                "pnl_sol": round(t.pnl_sol, 6),
                "pnl_pct": round(t.pnl_pct, 2),
                "exit_reason": t.exit_reason,
                "hold_seconds": round(t.hold_seconds, 1),
                "won": t.won,
            }
            for t in closed_trades
        ]

        return {
            "last_updated": now,
            "last_updated_str": now_str,
            "stats": {
                "session_start": stats.session_start,
                "total_trades": stats.total_trades,
                "winning_trades": stats.winning_trades,
                "losing_trades": stats.losing_trades,
                "win_rate": round(stats.win_rate, 1),
                "total_pnl_sol": round(stats.total_pnl_sol, 6),
                "best_trade_pct": round(stats.best_trade_pct, 2),
                "worst_trade_pct": round(stats.worst_trade_pct, 2),
                "tokens_scanned": stats.tokens_scanned,
                "tokens_bought": stats.tokens_bought,
                "open_positions": stats.open_positions,
            },
            "open_positions": open_positions,
            "closed_trades": closed_records,
        }

    async def push_data(self) -> None:
        """Serialize paper_tracker data to JSON, base64-encode it, and PUT to GitHub API."""
        if not self.token:
            return

        payload = self._build_payload()
        json_bytes = json.dumps(payload, indent=2).encode("utf-8")
        content_b64 = base64.b64encode(json_bytes).decode("utf-8")

        url = f"{self._api_base}/repos/{self.owner}/{self.repo}/contents/docs/data.json"
        headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }

        # On first run, try to fetch the existing file's SHA
        if self._file_sha is None:
            await self._fetch_sha(url, headers)

        body: dict = {
            "message": "chore: update live trading data",
            "content": content_b64,
            "branch": self.branch,
        }
        if self._file_sha:
            body["sha"] = self._file_sha

        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=headers, json=body) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    self._file_sha = data.get("content", {}).get("sha")
                    logger.info(
                        f"[GitHubSync] Pushed data.json (status={resp.status}, sha={self._file_sha[:7] if self._file_sha else 'none'})"
                    )
                else:
                    text = await resp.text()
                    logger.warning(f"[GitHubSync] Push failed: HTTP {resp.status} — {text[:200]}")

    async def _fetch_sha(self, url: str, headers: dict) -> None:
        """Fetch the current SHA of docs/data.json so we can update (not create) it."""
        params = {"ref": self.branch}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self._file_sha = data.get("sha")
                        logger.info(f"[GitHubSync] Fetched existing file SHA: {self._file_sha[:7] if self._file_sha else 'none'}")
                    # 404 means file doesn't exist yet — that's fine, we'll create it
        except Exception as exc:
            logger.warning(f"[GitHubSync] Could not fetch file SHA: {exc}")

    async def sync_loop(self) -> None:
        """Push live data every 60 seconds. Logs success/failure without crashing."""
        logger.info("[GitHubSync] Sync loop started — pushing every 60 seconds.")
        while True:
            try:
                await self.push_data()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"[GitHubSync] Push error: {exc}")
            await asyncio.sleep(60)
