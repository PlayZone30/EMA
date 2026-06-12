# Walkthrough: Live 5-Min Collector — Bug Fixes + Dashboard + Deployment

## Summary

All changes from the [audit plan](file:///Users/pavanreddy/.claude/plans/live-5min-collector-py-audit-this-code-tranquil-locket.md) have been implemented in a clean, self-contained deployment folder at `live_deploy/`.

## Files Created

```
EMA/live_deploy/
├── live_5min_collector.py    — Fixed engine (A1-A9 bugs + DB hooks)
├── auth_helper.py            — Standalone Fyers authenticator
├── dashboard_db.py           — SQLite WAL persistence layer
├── dashboard/
│   ├── __init__.py
│   ├── server.py             — FastAPI API server
│   └── static/
│       ├── index.html        — Dashboard UI
│       ├── app.js            — Frontend logic (lightweight-charts v4)
│       └── style.css         — Premium dark theme
├── seed_dashboard_db.py      — Seeding/replay from data_5min/
├── requirements.txt          — All dependencies
├── run.sh                    — Launch both processes
└── .env                      — Copied from root
```

---

## Part A — Bug Fixes Applied

| Fix | Description | Location |
|-----|-------------|----------|
| A1 | **Missed-signal race** — `check_divergence()` runs on ANY candle completion, not just SPOT. Deduplication via `last_signal_bucket` dict | [check_divergence](file:///Users/pavanreddy/EMA/live_deploy/live_5min_collector.py#L220-L271) |
| A2 | **Expired-signal entry leak** — Signals store `expires_at = candle_time + 600`. Sweep ALL pending signals for expiry at the TOP of `_on_message`, before entry check | [expiry sweep](file:///Users/pavanreddy/EMA/live_deploy/live_5min_collector.py#L283-L288) |
| A3 | **cur_ce/cur_pe guard removed** — Old expiry block deleted entirely | Part of A2 |
| A4 | **WS disconnect resilience** — `_stop_event = threading.Event()`. Main loop checks `_stop_event`, not `is_running`. `_on_close` only logs. Clock-based EOD trigger if ticks stop | [_on_close](file:///Users/pavanreddy/EMA/live_deploy/live_5min_collector.py#L345-L349), [main loop](file:///Users/pavanreddy/EMA/live_deploy/live_5min_collector.py#L368-L377) |
| A5 | **Repeated EOD** — `_eod_done` one-shot flag | [_handle_eod](file:///Users/pavanreddy/EMA/live_deploy/live_5min_collector.py#L319-L332) |
| A6 | **Negative SL** — `sl = max(entry - risk, 0.05)`, then recompute `final_risk` and `tp` from clamped SL | [ActiveTrade5Min.__init__](file:///Users/pavanreddy/EMA/live_deploy/live_5min_collector.py#L104-L112) |
| A7 | **Entry trigger** — `ltp < sig['low']` (strict `<`, not `<=`) | [_on_message](file:///Users/pavanreddy/EMA/live_deploy/live_5min_collector.py#L293) |
| A8 | **Deterministic time** — `now` threaded through `CandleManager5Min.update(symbol, ltp, now)` and `_bucket(now)` | [CandleManager5Min](file:///Users/pavanreddy/EMA/live_deploy/live_5min_collector.py#L64-L94) |
| A9 | **Signal candle tracking** — `ActiveTrade5Min` stores `signal_candle`; `_close()` adds `signal_time/signal_high/signal_low` to result dict | [_close()](file:///Users/pavanreddy/EMA/live_deploy/live_5min_collector.py#L131-L148) |

---

## Part B — SQLite Persistence

[dashboard_db.py](file:///Users/pavanreddy/EMA/live_deploy/dashboard_db.py) — Thread-safe SQLite writer with WAL mode:

- **Tables**: `candles`, `state`, `trades`, `events`
- **Thread safety**: `threading.local()` for per-thread connections
- **Safety**: Every method wrapped in try/except — DB errors never break tick processing
- **Throttling**: In-progress candle + state writes limited to ~1/sec; forced on entry/exit/EOD

---

## Part C — Dashboard

### API ([server.py](file:///Users/pavanreddy/EMA/live_deploy/dashboard/server.py))
- `GET /api/state` — engine state + liveness (heartbeat age < 10s)
- `GET /api/candles?symbol=&from=&to=` — OHLC data (defaults to today)
- `GET /api/trades?date=` + `GET /api/trades/dates`
- `GET /api/trades/{id}` — trade detail + surrounding candle windows
- `GET /api/events?after_id=&limit=` — incremental event feed

### Frontend
- **lightweight-charts v4.2.3** pinned UMD from CDN
- Poll-based: state 2.5s, candles 5s, events 3s
- IST timezone shift via single `toIST()` helper (+19800)
- Chart pan-sync via `subscribeVisibleLogicalRangeChange`

---

## Verification Results

### DB Seeding ✅
```
225 candles, 3 trades, 10 events seeded from 2026-03-04 data
```

### API Endpoints ✅
All endpoints return correct data. Trade detail returns surrounding candle windows.

### Dashboard UI ✅

**Main dashboard** — header with capital/PnL/spot, charts, trade journal, event feed:

![Dashboard main view](/Users/pavanreddy/.gemini/antigravity-ide/brain/762efde6-001d-4dc3-9597-8567249fc1cd/dashboard_main.png)

**Trade detail modal** — metrics grid + spot/option charts with SL/TP lines and entry/exit/signal markers:

![Trade detail modal](/Users/pavanreddy/.gemini/antigravity-ide/brain/762efde6-001d-4dc3-9597-8567249fc1cd/trade_detail.png)

---

## How to Deploy

```bash
cd live_deploy/

# Install dependencies (in your conda env)
conda activate fyers
pip install -r requirements.txt

# Seed test data (optional)
python seed_dashboard_db.py --seed-day 2026-03-04

# Run live
bash run.sh
# or individually:
python live_5min_collector.py &
uvicorn dashboard.server:app --host 0.0.0.0 --port 8765 &
```

Open `http://<server-ip>:8765/` from phone or laptop.

---

## What's NOT Changed

- Original `live_5min_collector.py` in root — untouched
- CSV logging format — byte-identical
- `main.py` — untouched (auth extracted into standalone `auth_helper.py`)
- `data_5min/` NOT copied into live_deploy (seeder references `../data_5min` by default)
