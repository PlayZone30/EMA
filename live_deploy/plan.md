# Live 5-Min Collector: Bug Fixes + Live Trading Dashboard

## Context

[live_5min_collector.py](live_5min_collector.py) is a live paper-trading engine (Fyers WebSocket, NIFTY options divergence strategy, ₹20k compounding capital). An audit found real bugs, and the user wants a live web dashboard — hosted on their server, opened from phone/laptop — showing capital, live trades with entry trigger/SL/TP timing, TradingView-style spot + option charts with entry markers, and a per-trade journal where every trade is tappable with its own chart.

**User decisions:** FastAPI + lightweight-charts (TradingView's open-source lib) · fix ALL audited bugs · interactive per-trade charts (no screenshots).

## Audit findings (Part A — fix all in live_5min_collector.py)

1. **Missed-signal race (critical).** `check_divergence` runs only when the SPOT candle completes (line 365), and requires the option's candle for the same bucket to already be stored (time-equality at lines 299/310). Which symbol's candle completes first depends on random tick arrival order → ~half of signals silently missed. **Fix:** run the divergence check whenever ANY symbol's candle completes; evaluate each pair (SPOT+PE, SPOT+CE) only when both latest candles share the bucket; dedupe with `self.last_signal_bucket = {}` (skip if a signal for that option symbol was already raised for this bucket).
2. **Expired-signal entry leak.** The breakout entry check (line 344) runs on every tick BEFORE the expiry block (line 360) and never consults expiry → the first tick of candle N+2 can enter on a signal that should have died at candle N+1 close. **Fix:** store `expires_at = signal_candle_time + 600` in the signal; sweep all `pending_signals` for expiry at the top of the trading section in `_on_message`, every tick. Delete the old expiry block (lines 359-363).
3. **`cur_ce and cur_pe` guard (line 360):** if either symbol is None, signals never expire. Removed along with the old block in fix 2.
4. **WS disconnect kills the script.** `_on_close` sets `is_running=False` → main `while self.is_running` loop (line 463) exits even though `reconnect=True` restores the socket. **Fix:** add `self._stop_event = threading.Event()`; main loop and refresh worker loop on `not self._stop_event.is_set()`; `_on_close` only logs (keep `is_running` purely as a "WS connected" flag for the heartbeat); `_handle_eod` sets the stop event. Main loop also checks the clock and triggers EOD itself if ticks stop after 15:30.
5. **Repeated EOD handling.** After 15:30 every tick calls `_handle_eod` (lines 334-336). **Fix:** one-shot `self._eod_done` flag.
6. **Negative SL.** `final_risk` (avg candle size) can exceed a ~₹75 entry → SL < 0, never hit. **Fix in `ActiveTrade5Min.__init__`:** `self.sl = max(entry - final_risk, 0.05)`, then recompute `final_risk = entry - sl` and `tp = entry + 2.5*final_risk` so the 2.5R relationship stays truthful.
7. **Minor:** line 347 `ltp <= sig['low']` → `ltp < sig['low']` to match the documented rule. Status-line `print` vs logger mixing left as-is (cosmetic).
8. **Enabler:** thread `now` explicitly through `CandleManager5Min.update(symbol, ltp, now)` / `_bucket(now)` (currently calls `datetime.now(IST)` internally) — makes the engine deterministic for offline replay testing.
9. **For trade records:** `ActiveTrade5Min` keeps `signal_candle`; `_close()` adds `signal_time/signal_high/signal_low` to the result dict. `_log_trade_csv` accesses keys explicitly, so the existing CSV stays byte-identical in format.

## Part B: Persistence layer — new `dashboard_db.py` (SQLite + WAL)

SQLite chosen over JSON files: single file, atomic, WAL lets the FastAPI process read while the collector writes, stdlib-only, natural multi-day/time-range queries. DB: `live_dashboard.db`.

**Schema** (`PRAGMA journal_mode=WAL; busy_timeout=3000`):
- `candles(symbol, bucket_time INTEGER epoch-UTC, open, high, low, close, is_final, PK(symbol,bucket_time))`
- `state(key, value JSON, updated_at)` — single `engine` row: heartbeat ISO, ws_connected, running_capital, daily_pnl, daily_signals, spot/ce/pe LTPs, ce/pe symbols, active_trade (entry/SL/TP/lots/highest/reason/entry_time) or null, pending_signals list.
- `trades(id, date, type, symbol, lots, entry_time/exit_time full ISO, entry/exit price, sl, tp, risk, highest_reached, pnl_per_unit, pnl_total, capital_after, exit_reason, signal_reason, signal_time, signal_high, signal_low)` + index on date.
- `events(id, ts, kind SIGNAL|SIGNAL_INVALID|SIGNAL_EXPIRED|ENTRY|EXIT|INFO, message, data JSON)` — feeds the live activity log.

**Writer:** `DashboardDB` class, one connection per thread via `threading.local()` (WS callback + refresh + main threads), every method try/except-logged — **a DB error must never break tick processing**. In-progress candle upserts and state writes throttled to ~1/sec; forced writes on entry/exit/EOD.

**Hooks in live_5min_collector.py:** init in `__init__`; final-candle write in `_store_candle`; throttled in-progress candle + state heartbeat at end of `_on_message`; events at signal create/invalidate/expire; `insert_trade` + event + forced state in `_handle_trade_exit`; entry event in `_enter_trade`; EOD event in `_handle_eod`. Existing CSV logging untouched (remains the authoritative record).

## Part C: Dashboard — new `dashboard/server.py` + `dashboard/static/{index.html,app.js,style.css}`

**Server (FastAPI + uvicorn, added to requirements.txt):** read-only SQLite (`file:...?mode=ro`), serves static files at `/`.
- `GET /api/state` → state blob + `{server_time, live: heartbeat_age < 10s}`
- `GET /api/candles?symbol=&from=&to=` (default today) → ordered OHLC list
- `GET /api/trades?date=YYYY-MM-DD|all` (default today) + `GET /api/trades/dates`
- `GET /api/trades/{id}` → trade + spot & option candle windows `[entry−45min, exit+30min]`
- `GET /api/events?after_id=&limit=` → incremental event feed

**Frontend** (plain JS, no framework; **lightweight-charts v4.2.3 pinned UMD from CDN** — use v4 API `addCandlestickSeries` consistently, never mix with v5):
1. **Header:** capital, day PnL (green/red), spot LTP, live dot (grey + "stale Ns" if heartbeat old). Poll `/api/state` every 2.5s.
2. **Active trade card:** type/symbol/entry/SL/TP/lots, live LTP, unrealized PnL `(ltp − entry) × 65 × lots`, time in trade.
3. **Pending signal chips:** "CE_BUY armed, trigger > 78.40, expires hh:mm".
4. **Live charts:** SPOT + focus option (active trade's symbol, else CE/PE toggle), side by side on desktop / stacked on phone (CSS grid + media query). Option chart: `createPriceLine` for SL (red dashed) and TP, `setMarkers` arrowUp ENTRY / arrowDown EXIT / circles for signal candles. Charts pan-synced via `subscribeVisibleLogicalRangeChange` with re-entrancy guard. Refresh candles every 5s (`setData`, ≤75 bars/day so cheap).
5. **Trade log / journal:** date-filterable list (`#id · CE_BUY · 10:35→10:52 · 76.20→82.10 · +₹1,917 · TP`); tap → detail modal with metric grid + that trade's spot & option charts with entry/exit markers and SL/TP lines (`chart.remove()` on close).
6. **Event feed:** reverse-chronological, incremental poll every 3s.

**Timezone:** lightweight-charts renders UTC; shift `+19800` (IST) in ONE client-side helper; DB stays canonical UTC epochs.

**Polling, not WebSockets** — 2.5–5s intervals are trivial load and survive flaky mobile connections.

**Run (two processes on the server):**
```
python live_5min_collector.py
uvicorn dashboard.server:app --host 0.0.0.0 --port 8765
```
Open `http://<server-ip>:8765/` from phone/laptop.

## Verification — new `seed_dashboard_db.py`

Seed from `data_5min/` (matched spot+CE+PE per-date 5-min CSVs — NOT the `april/` folders, which lack spot data):
1. `--seed-day <date>`: load candles, fabricate 2–3 realistic trades per strategy math, matching events, and a state blob → every dashboard view renders without a live market.
2. `--live-sim`: replay candles ~1 per 2s, mutating the in-progress candle/LTPs/heartbeat → watch live behavior.

Checklist:
1. Offline engine tests via crafted `_on_message` dicts with controlled `now`: signal fires regardless of spot-vs-option completion order, no duplicate signal; tick at `signal_time+601` expires without entering; expiry works with a None symbol; `_on_close` doesn't stop the loop; two post-15:30 ticks → one EOD; huge avg size → `sl == 0.05` with consistent TP.
2. `sqlite3 live_dashboard.db` sanity queries after seeding.
3. `curl` each API endpoint; trade detail returns both candle windows.
4. UI on desktop + phone width: header, charts, markers, SL/TP lines, trade modal, event feed, pan-sync.
5. Live-sim: green dot, growing last candle, feed appends.
6. Next market day: run both processes; CSV unchanged in format, DB fills, dashboard tracks live.

## Files

| File | Action |
|---|---|
| `live_5min_collector.py` | Modify — bug fixes A1–A9 + DB hooks |
| `dashboard_db.py` | Create — SQLite schema + thread-safe writer |
| `dashboard/server.py` | Create — FastAPI API + static serving |
| `dashboard/static/index.html`, `app.js`, `style.css` | Create — mobile-first UI |
| `seed_dashboard_db.py` | Create — offline seeding/replay |
| `requirements.txt` | Add `fastapi`, `uvicorn` |

## Risks / notes

- DB writes on the WS thread are single upserts with busy_timeout + try/except; CSV remains authoritative, so a locked DB can never lose a trade record.
- On collector restart, capital resets to ₹20,000 (existing behavior) — optional follow-up: resume from today's last `capital_after`.
- Mid-day option symbol rotation leaves orphan candle rows for old symbols — harmless; UI follows `state.ce_symbol/pe_symbol`.
- SL clamp slightly changes TP when it kicks in (TP recomputed from clamped risk to keep 2.5R truthful).
