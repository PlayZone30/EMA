# EMA Trading Monitor & Divergence Strategy

A **live trading monitor and backtesting system** for Indian equity markets (NSE) built on the **Fyers API v3**. The system runs two parallel strategies:

1. **EMA Touch Alert** — Monitors Nifty 50 and Reliance for price touching the 45-period 5-min EMA and sends WhatsApp alerts.
2. **Spot-Option Divergence Strategy** — Detects divergence between Nifty 50 spot candles and ATM option candles, then paper-trades CE/PE options with dual 1:1 and 1:3 risk-reward targets.

---

## Project Structure

```
EMA/
├── main.py                  # Core live trading engine (24/7 service)
├── divergence_strategy.py   # Divergence strategy logic (signal detection + trade management)
├── backtest_divergence.py   # Backtesting harness for the divergence strategy
├── samplecode.py            # Standalone ATM straddle monitor (historical + live chart)
├── test_whatsapp.py         # Utility to test Twilio WhatsApp integration
├── requirements.txt         # Python dependencies
├── .env                     # Credentials and configuration (not committed to git)
├── ema_data.json            # Auto-generated: persisted EMA values across sessions
├── divergence_capital.json  # Auto-generated: persisted running capital across sessions
├── divergence_trades.csv    # Auto-generated: live trade log (CSV)
├── backtest_results.csv     # Auto-generated: backtest trade log (CSV)
├── daily_report_YYYY-MM-DD.json  # Auto-generated: end-of-day JSON report
└── ema_monitor.log          # Auto-generated: application log file
```

---

## Architecture Overview

```
main.py (EMAMonitor)
│
├── FyersAuthenticator      → Automated 6-step TOTP login to Fyers API
├── EMACalculator           → Maintains & persists 45-period EMA per symbol
├── CandleManager           → Aggregates real-time ticks into 5-min OHLC candles
├── MarketScheduler         → Tracks market hours, holidays, sleep/wake cycles
├── WhatsAppNotifier        → Sends Twilio WhatsApp alerts on EMA touch
│
└── DivergenceStrategy (divergence_strategy.py)
    ├── Signal Detection    → Checks spot vs option divergence on each 5-min candle
    ├── Trade Execution     → Paper trades with dual 1:1 and 1:3 RR targets
    ├── Strike Rotation     → Dynamically rotates ATM strikes every 30 seconds
    └── Reporting           → CSV trade log + daily JSON report
```

---

## File-by-File Explanation

### `main.py` — Live Trading Engine

The main orchestrator. Runs as a **24/7 service** that:

1. **Authenticates** with Fyers API automatically using TOTP (6-step flow: OTP → TOTP → PIN → Auth Code → Access Token).
2. **Connects WebSocket** to receive real-time tick data for configured symbols.
3. **Builds 5-min candles** from ticks using `CandleManager`.
4. **Updates EMA** on each completed 5-min candle close using `EMACalculator`.
5. **Checks EMA touch** — if LTP is within ±0.035% of EMA, fires a WhatsApp alert.
6. **Feeds candles to `DivergenceStrategy`** for signal detection and trade management.
7. **Handles market open/close** — waits for market open, saves state at close, sleeps overnight.

**Key Classes:**

| Class | Responsibility |
|---|---|
| `FyersAuthenticator` | Automated TOTP-based login, returns `access_token` |
| `WhatsAppNotifier` | Sends formatted WhatsApp alerts via Twilio (5-min cooldown per symbol) |
| `EMACalculator` | Calculates EMA using formula: `EMA = (Close × k) + (Prev_EMA × (1-k))` where `k = 2/(period+1)`. Loads/saves to `ema_data.json` |
| `CandleManager` | Groups ticks into 5-min buckets. Skips the first incomplete candle (before `EMA_CALCULATED_UNTIL` time) |
| `MarketScheduler` | Knows market hours (9:15–15:30 IST), weekends, and 2025 holidays |
| `EMAMonitor` | Wires all components together; runs the WebSocket event loop |

**Symbols Monitored (configured in `main()`):**
- `NSE:RELIANCE-EQ` — Reliance Industries
- `NSE:NIFTY50-INDEX` — Nifty 50 Index

---

### `divergence_strategy.py` — Divergence Strategy

The core strategy logic. Implements a **Spot-Option Divergence** approach:

#### Signal Logic

| Signal | Condition |
|---|---|
| **PE Buy** | Spot 5-min candle is **Green** (Close > Open) **AND** ATM PE 5-min candle is **Green** |
| **CE Buy** | Spot 5-min candle is **Red** (Close < Open) **AND** ATM CE 5-min candle is **Green** |

> **Divergence Intuition:** When the spot moves up (green) but the put option also goes up (green), it signals unusual demand for puts — a bearish divergence. Similarly, when spot falls (red) but the call goes up (green), it signals call buying despite spot weakness — a bullish divergence.

#### Entry & Exit Rules

- **Entry:** Price must **break above the High** of the signal candle (breakout confirmation).
- **Stop Loss:** Low of the signal candle − ₹0.25 buffer.
- **Targets:** Two simultaneous trades are placed per signal:
  - **1:1 RR** — Target = Entry + (Entry − SL)
  - **1:3 RR** — Target = Entry + 3 × (Entry − SL)
- **Signal Invalidation:** If price breaks below the Low of the signal candle before triggering, the signal is discarded.
- **Capital:** Fixed ₹10,000 per trade. Quantity = `floor(10000 / Entry Price)`.

#### ATM Strike Selection & Rotation

- On startup, fetches the nearest expiry ATM CE and PE from the Fyers option chain API.
- Every **30 seconds**, checks if the ATM strike has shifted (due to spot price movement). If so, unsubscribes old option symbols and subscribes to new ones via WebSocket.

#### State Persistence

- `divergence_capital.json` — Saves `running_capital` so cumulative P&L survives restarts.
- `divergence_trades.csv` — Appends every trade entry and exit with full details.
- `daily_report_YYYY-MM-DD.json` — Generated at market close with full day summary.

---

### `backtest_divergence.py` — Backtesting Harness

Replays historical data through the `DivergenceStrategy` to validate performance.

#### How Backtesting Works

1. **Authenticates** using the same `FyersAuthenticator` from `main.py`.
2. **Fetches 5-min spot data** (Nifty 50) for the test date.
3. **Fetches 1-min option data** (CE and PE) for the test date.
4. **Resamples** 1-min option data to 5-min for signal generation.
5. **Iterates 1-min timestamps** in a loop:
   - When a new 5-min bucket starts, feeds the **completed previous 5-min candle** to `strategy.process_candle()` for signal detection.
   - Within each 1-min bar, simulates tick-level execution by calling `strategy.update_ltp()` with Open → Low → High → Close prices.
   - Includes **breakout interpolation**: if a pending signal's trigger level falls between the 1-min Low and High, injects a precise tick at `signal_high + 0.05` to simulate realistic entry.
6. Results are saved to `backtest_results.csv`.

**Test Configuration (hardcoded in script):**
```python
test_date = "2025-11-24"
ce_symbol = "NSE:NIFTY25NOV25950CE"
pe_symbol = "NSE:NIFTY25NOV25950PE"
```

---

### `samplecode.py` — ATM Straddle Monitor

A **standalone script** (separate from the main system) for visualizing the ATM straddle premium. Supports two modes:

1. **Historical Mode** — Fetches CE + PE 1-min data for a given date, sums them to create a straddle OHLC series, and plots an interactive Plotly candlestick chart in the browser.
2. **Live Mode** — Finds the current ATM strike via option chain API, subscribes to CE + PE via WebSocket, builds live 1-min straddle candles, and displays a real-time matplotlib candlestick chart with 2-second refresh.

> **Note:** This file is a prototype/utility and is not integrated into the main trading engine.

---

### `test_whatsapp.py` — WhatsApp Test Utility

A simple script to verify that the Twilio WhatsApp integration is working. Sends a test message using credentials from `.env`. Run this before starting the main system to confirm alerts will work.

---

## Strategy Data Flow (Live Mode)

```
Fyers WebSocket Tick
        │
        ▼
CandleManager.update_candle()
        │
        ├─── [Tick within candle] → Update OHLC in memory
        │
        └─── [New 5-min bucket] → Return completed candle
                    │
                    ▼
        ┌───────────────────────────────────┐
        │  EMACalculator.update_ema()       │  ← Updates 45-EMA for spot symbols
        │  WhatsAppNotifier.send_alert()    │  ← If LTP within ±0.035% of EMA
        └───────────────────────────────────┘
                    │
                    ▼
        DivergenceStrategy.process_candle()
                    │
                    ▼
        _check_signals() → Detects PE Buy / CE Buy divergence
                    │
                    ▼
        pending_signals{} ← Stores signal (high, low, candle)
                    │
        [Next tick via update_ltp()]
                    │
                    ├─── Price > signal.high → _place_dummy_order() → 2 trades (1:1 + 1:3)
                    └─── Price < signal.low  → Signal invalidated, discarded
```

---

## Results Storage

| File | Format | Contents |
|---|---|---|
| `ema_data.json` | JSON | Last known EMA value + timestamp per symbol |
| `divergence_capital.json` | JSON | Running capital balance |
| `divergence_trades.csv` | CSV | All trade entries/exits: Date, Time, Symbol, Type, Entry, SL, Target, Status, Exit Price, Exit Time, PnL, Reason, Strategy (1:1 or 1:3) |
| `backtest_results.csv` | CSV | Same format as `divergence_trades.csv` but for backtest runs |
| `daily_report_YYYY-MM-DD.json` | JSON | End-of-day summary: total signals, trades, win rate, daily PnL, running capital, strategy breakdown (1:1 vs 1:3), full trade list |
| `ema_monitor.log` | Log | Timestamped application logs (also printed to console) |

---

## Configuration (`.env`)

```env
# Fyers API Credentials
CLIENT_ID=<APP_ID>-<APP_TYPE>
SECRET_KEY=<your_secret>
USERNAME=<fyers_user_id>
PIN=<4_digit_pin>
TOTP_KEY=<base32_totp_secret>

# EMA Seed Values (used on first run before ema_data.json exists)
MANUAL_CURRENT_EMA_Re=<reliance_ema_value>
MANUAL_CURRENT_EMA_N50=<nifty_ema_value>
EMA_CALCULATED_UNTIL=<HH:MM>   # Time up to which EMA was pre-calculated (skip candles before this)

# Twilio WhatsApp
TWILIO_ACCOUNT_SID=<sid>
TWILIO_AUTH_TOKEN=<token>
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
WHATSAPP_TO=whatsapp:+91XXXXXXXXXX
```

---

## Setup & Running

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure `.env`
Fill in your Fyers API credentials and Twilio WhatsApp details.

### 3. Test WhatsApp (Optional)
```bash
python test_whatsapp.py
```

### 4. Run Live Monitor
```bash
python main.py
```
The service will:
- Auto-authenticate with Fyers using TOTP
- Wait for market open (9:15 AM IST)
- Start monitoring and run until market close (3:30 PM IST)
- Save EMA state and generate daily report at close
- Sleep overnight and repeat the next trading day

### 5. Run Backtest
Edit the `test_date`, `ce_symbol`, and `pe_symbol` in `backtest_divergence.py`, then:
```bash
python backtest_divergence.py
```
Results will be in `backtest_results.csv`.

### 6. ATM Straddle Chart (Standalone)
```bash
python samplecode.py
```
Choose mode 1 (historical) or 2 (live).

---

## Key Design Decisions

- **EMA Seeding:** On first run (no `ema_data.json`), the EMA is seeded from `MANUAL_CURRENT_EMA` values in `.env`. Subsequent runs load the last saved EMA from the JSON file, ensuring continuity across restarts.
- **Incomplete Candle Skipping:** The first 5-min candle after startup is skipped if it falls before `EMA_CALCULATED_UNTIL` time. This prevents updating the EMA with a partial candle from before the bot started.
- **Dual Trade Journaling:** Every signal creates **two simultaneous paper trades** — one targeting 1:1 RR and one targeting 1:3 RR — to compare strategy performance across different exit strategies.
- **Strike Rotation:** ATM strikes are checked every 30 seconds via the option chain API. When the spot price moves enough to shift the ATM, old option WebSocket subscriptions are replaced with new ones automatically.
- **Paper Trading Only:** All trades are simulated (`_place_dummy_order`). No real orders are placed via the Fyers order API.
