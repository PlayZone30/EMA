# EMA Monitor & Divergence Trading System

A comprehensive automated trading system for Indian stock markets that monitors Exponential Moving Averages (EMA) and executes a spot-option divergence strategy on Nifty 50 options with real-time email alerts.

## Overview

This system combines two powerful trading strategies with instant email notifications:

1. **EMA Monitoring**: Real-time tracking of 45-period EMA on multiple symbols (Nifty 50, Reliance) with email alerts when price touches EMA levels
2. **Divergence Strategy**: Automated paper trading of Nifty 50 ATM options based on spot-option price divergence patterns with immediate signal alerts

## Architecture

### Core Components

#### 1. Authentication (`FyersAuthenticator`)
- Automated login to Fyers API using TOTP (Time-based One-Time Password)
- 6-step authentication flow:
  1. Send login OTP
  2. Generate TOTP code
  3. Verify TOTP
  4. Verify PIN
  5. Get authorization code
  6. Validate auth code for final access token
- Handles session management automatically

#### 2. EMA Calculator (`EMACalculator`)
- Calculates and maintains 45-period EMA for configured symbols
- Persists EMA values to `ema_data.json` for continuity across restarts
- Updates EMA with each completed 5-minute candle
- Formula: `EMA = (Close × Multiplier) + (Previous_EMA × (1 - Multiplier))`
- Multiplier: `2 / (Period + 1)`

#### 3. Candle Manager (`CandleManager`)
- Aggregates real-time tick data into 5-minute candles
- Tracks: Open, High, Low, Close (OHLC) for each 5-minute interval
- Handles incomplete candles at market open (skips candles before EMA_CALCULATED_UNTIL time)
- Supports both configured symbols and dynamically subscribed option symbols

#### 4. Market Scheduler (`MarketScheduler`)
- Manages market timing (9:15 AM - 3:30 PM IST)
- Tracks Indian stock market holidays for 2025
- Calculates time until next market open
- Prevents trading on weekends and holidays

#### 5. Email Notifier (`EmailNotifier`)
- Sends real-time alerts via Gmail SMTP
- Implements 5-minute cooldown between alerts per symbol
- **Two types of alerts**:
  1. **EMA Touch Alerts**: When price touches EMA threshold
  2. **Divergence Signal Alerts**: Immediately when divergence pattern is detected (before entry trigger)
- HTML-formatted emails with:
  - Symbol details and timestamps
  - Current LTP and EMA values
  - Divergence pattern details (spot + option candles)
  - Entry trigger levels and stop loss
  - Trading plan and risk management info

#### 6. Divergence Strategy (`DivergenceStrategy`)
- **Signal Detection**: Monitors spot-option divergence patterns
  - **PE Buy Signal**: Spot Green (Close > Open) AND PE Green
  - **CE Buy Signal**: Spot Red (Close < Open) AND CE Green
- **Immediate Email Alerts**: Sends alert as soon as divergence candle is formed (doesn't wait for entry)
- **Entry Logic**: Price must break signal candle's high to trigger entry
- **Risk Management**:
  - Stop Loss: Signal candle low - ₹0.25
  - Dual Targets: 1:1 and 1:3 Risk:Reward ratios
- **Capital Management**: ₹10,000 per trade (paper trading)
- **ATM Strike Selection**: Automatically selects At-The-Money options
- **Strike Rotation**: Monitors and rotates to new ATM strikes as spot moves (every 30 seconds)
- **Trade Tracking**: Logs all trades to `divergence_trades.csv`
- **Daily Reporting**: Generates comprehensive JSON reports at market close

### WebSocket Integration

The system uses Fyers WebSocket API for real-time data streaming:

```python
FyersDataSocket(
    access_token=token,
    on_connect=_on_open,
    on_close=_on_close,
    on_error=_on_error,
    on_message=_on_message,
    reconnect=True
)
```

**WebSocket Flow**:
1. **Connection**: Establishes on market open
2. **Subscription**: Subscribes to configured symbols + ATM options
3. **Message Handling**: Processes each tick:
   - Updates candles
   - Checks EMA touches
   - Manages divergence strategy trades
   - Handles strike rotation
4. **Disconnection**: Gracefully closes at market close

**Data Flow**:
```
Tick Data → Candle Aggregation → EMA Calculation → Alert Check (Email)
                                ↓
                         Divergence Strategy
                                ↓
                    Signal Detection → Email Alert (Immediate)
                                ↓
                    Wait for Breakout → Entry/Exit Logic
```

## Trading Strategies

### 1. EMA Touch Strategy

**Concept**: Alert when price touches the 45-period EMA (±0.035% threshold)

**Configuration**:
```python
{
    "EMA_PERIOD": 45,
    "CANDLE_INTERVAL": 5,  # minutes
    "TOUCH_THRESHOLD": 0.035,  # 0.035%
}
```

**Use Case**: Identifies potential support/resistance levels for manual trading decisions

### 2. Divergence Strategy (Automated Paper Trading)

**Concept**: Exploit divergence between spot and option price movements

**Alert Timing**: Email sent immediately when divergence candle forms (not waiting for entry trigger)

**Signal Rules**:

| Signal Type | Spot Condition | Option Condition | Action |
|------------|----------------|------------------|--------|
| PE Buy | Green Candle (Close > Open) | PE Green | Buy PE at breakout |
| CE Buy | Red Candle (Close < Open) | CE Green | Buy CE at breakout |

**Entry**: Price breaks above signal candle high

**Exit**:
- **Stop Loss**: Signal candle low - ₹0.25
- **Target 1**: Entry + (1 × Risk) [1:1 RR]
- **Target 2**: Entry + (3 × Risk) [1:3 RR]

**Example Trade**:
```
Signal Candle: High=65, Low=60
Entry: 65.05 (breakout)
SL: 59.75 (60 - 0.25)
Risk: 5.30 (65.05 - 59.75)
Target 1:1: 70.35 (65.05 + 5.30)
Target 1:3: 81.95 (65.05 + 15.90)
```

**Dual Journaling**: Each signal creates TWO trades (1:1 and 1:3) for performance comparison

## File Structure

```
.
├── main.py                      # Main application entry point
├── divergence_strategy.py       # Divergence trading logic
├── backtest_divergence.py       # Historical backtesting script
├── samplecode.py                # Live straddle monitoring example
├── test_gmail.py                # Gmail notification tester (NEW)
├── test_whatsapp.py             # WhatsApp tester (DEPRECATED)
├── requirements.txt             # Python dependencies
├── .env                         # Environment variables (not in repo)
├── .gitignore                   # Git ignore rules
├── ema_data.json               # Persisted EMA values (auto-generated)
├── divergence_trades.csv       # Trade log (auto-generated)
├── divergence_capital.json     # Capital tracking (auto-generated)
├── daily_report_YYYY-MM-DD.json # Daily reports (auto-generated)
└── ema_monitor.log             # Application logs (auto-generated)
```

## Setup & Installation

### Prerequisites
- Python 3.8+
- Fyers trading account
- Gmail account with App Password enabled

### Installation

1. **Clone the repository**
```bash
git clone <repository-url>
cd <repository-name>
```

2. **Install dependencies**
```bash
pip install -r requirements.txt
```

3. **Configure environment variables**

Create a `.env` file:
```env
# Fyers API Credentials
CLIENT_ID=your_client_id-APP_TYPE
SECRET_KEY=your_secret_key
USERNAME=your_fyers_username
PIN=your_pin
TOTP_KEY=your_totp_secret_key

# Gmail Configuration
GMAIL_USER=your_email@gmail.com
GMAIL_APP_PASSWORD=your_16_char_app_password
ALERT_EMAIL=recipient@email.com

# Optional: Manual EMA values (for first run)
MANUAL_CURRENT_EMA_RE=1377.54
MANUAL_CURRENT_EMA_N50=25166.57
EMA_CALCULATED_UNTIL=09:35
```

4. **Setup Gmail App Password**

To send emails via Gmail, you need to generate an App Password:

1. Enable 2-Step Verification on your Google Account:
   - Go to: https://myaccount.google.com/security
   - Enable "2-Step Verification"

2. Generate App Password:
   - Go to: https://myaccount.google.com/apppasswords
   - Select "Mail" and your device
   - Click "Generate"
   - Copy the 16-character password (remove spaces)
   - Use this as `GMAIL_APP_PASSWORD` in your `.env` file

**Important**: Use the App Password, NOT your regular Gmail password!

5. **Test email notifications**
```bash
python test_gmail.py
```

## Usage

### Running the Main System

```bash
python main.py
```

**What happens**:
1. Authenticates with Fyers API
2. Loads saved EMA values (or uses manual values)
3. Waits for market open (if closed)
4. Connects WebSocket and subscribes to symbols
5. Monitors EMA touches and sends email alerts
6. Detects divergence signals and sends immediate email alerts
7. Executes divergence strategy on Nifty options (waits for breakout)
8. Saves EMAs and generates reports at market close
9. Sleeps until next trading day

### Running Backtests

```bash
python backtest_divergence.py
```

**Features**:
- Tests divergence strategy on historical data
- Uses real 1-minute option data for execution simulation
- Generates `backtest_results.csv` with trade details

### Live Straddle Monitoring

```bash
python samplecode.py
```

**Options**:
1. Historical analysis (provide CE/PE symbols and date)
2. Live ATM straddle monitoring with real-time charts

## Configuration

### Adding/Modifying Symbols

Edit `SYMBOLS_CONFIG` in `main.py`:

```python
SYMBOLS_CONFIG = {
    "NSE:SYMBOL-EQ": {
        "name": "Display Name",
        "EMA_PERIOD": 45,
        "CANDLE_INTERVAL": 5,
        "TOUCH_THRESHOLD": 0.035,
        "MANUAL_CURRENT_EMA": 1234.56,
        "EMA_CALCULATED_UNTIL": "09:35",
    }
}
```

### Adjusting Strategy Parameters

In `divergence_strategy.py`:

```python
self.sl_buffer = 0.25        # Stop loss buffer
self.capital = 10000         # Capital per trade
```

## Output Files

### 1. `ema_data.json`
Persisted EMA values for continuity:
```json
{
    "NSE:NIFTY50-INDEX": {
        "ema": 25166.57,
        "timestamp": "2025-11-24 15:30:00"
    }
}
```

### 2. `divergence_trades.csv`
Complete trade log:
```csv
Date,Time,Symbol,Type,Entry Price,SL,Target,Status,Exit Price,Exit Time,PnL,Reason,Strategy
2025-11-24,10:25:00,NSE:NIFTY25NOV25950PE,BUY,65.05,59.75,70.35,CLOSED,70.40,10:28:00,535.00,DIVERGENCE: Spot GREEN + PE GREEN,1:1
```

### 3. `daily_report_YYYY-MM-DD.json`
End-of-day summary:
```json
{
    "date": "2025-11-24",
    "summary": {
        "total_signals_detected": 5,
        "total_trades_taken": 8,
        "winning_trades": 6,
        "losing_trades": 2,
        "win_rate": "75.0%",
        "daily_pnl": 2450.00,
        "running_capital": 12450.00
    },
    "strategy_breakdown": {
        "1:1": {"trades": 4, "pnl": 1200.00},
        "1:3": {"trades": 4, "pnl": 1250.00}
    },
    "trades": [...]
}
```

### 4. `ema_monitor.log`
Application logs with timestamps and severity levels

## Key Features

### 24/7 Operation
- Runs continuously
- Automatically waits for market open
- Handles weekends and holidays
- Graceful shutdown with data persistence

### Real-Time Monitoring
- Live tick-by-tick data processing
- Single-line status updates (no log spam)
- Instant email alerts on EMA touches
- Immediate email alerts on divergence signal detection (before entry)
- Automatic strike rotation for options

### Risk Management
- Fixed capital per trade (₹10,000)
- Automatic stop loss execution
- Dual target tracking (1:1 and 1:3)
- Trade invalidation if price breaks signal low

### Data Persistence
- EMA values saved at market close
- Capital state tracked across sessions
- Complete trade history logging
- Daily performance reports

### Error Handling
- Automatic WebSocket reconnection
- API retry logic with delays
- Graceful degradation on failures
- Comprehensive error logging

## Monitoring & Alerts

### Console Output

**During Market Hours**:
```
[2025-11-24 10:25:30] RELIANCE: ₹1377.54 (EMA: ₹1377.54) | NIFTY 50: ₹25166.57 (EMA: ₹25166.57) | CE(25950): ₹65.05 | PE(25950): ₹60.25
```

**On EMA Touch**:
```
============================================================
🔔 ALERT: [NIFTY 50] LTP (₹25166.57) TOUCHED EMA (₹25166.57)
============================================================
```

**On Divergence Signal Detection**:
```
Signal Detected (Pending): PE Buy on NSE:NIFTY25NOV25950PE at 2025-11-24 10:25:00. High: 65, Low: 60
  Reason: DIVERGENCE: Spot GREEN (O:25150.00 C:25170.00) + PE GREEN (O:60.00 C:62.00)
[NSE:NIFTY25NOV25950PE] Sending divergence email alert...
[NSE:NIFTY25NOV25950PE] Divergence email alert sent successfully
```

### Email Alerts

#### 1. EMA Touch Alert

**Subject**: 🔔 EMA Alert: NIFTY 50 touched 45 EMA

**Content**: HTML-formatted email with:
- Time and symbol details
- Current LTP and EMA values
- Difference (absolute and percentage)
- Color-coded table layout

#### 2. Divergence Signal Alert (NEW!)

**Subject**: 🎯 Divergence Signal: PE/CE Buy Opportunity Detected

**Content**: Comprehensive HTML email with:
- **Signal Type**: PE or CE buy opportunity
- **Entry Trigger**: Exact price level to watch for breakout
- **Stop Loss**: Calculated SL level
- **Spot Candle Details**: OHLC with color indication
- **Option Candle Details**: OHLC with color indication
- **Divergence Pattern**: Explanation of the signal
- **Trading Plan**: 
  - Wait for confirmation (breakout)
  - Risk management guidelines
  - Target levels (1:1 and 1:3)
  - Invalidation criteria

**Key Feature**: Alert is sent IMMEDIATELY when the divergence candle forms, giving you time to prepare for the potential trade. You don't have to wait for the entry trigger to be notified!

## Technical Details

### WebSocket Message Processing

1. **Tick Reception**: Raw tick data from Fyers
2. **Candle Aggregation**: Groups ticks into 5-minute buckets
3. **EMA Update**: Recalculates EMA on candle close
4. **Strategy Update**: Passes tick to divergence strategy for:
   - Trade management (SL/Target checks)
   - Signal detection (on candle close) → **Sends email alert immediately**
   - Entry trigger (on breakout)
5. **Strike Rotation**: Checks for ATM changes (throttled to 30s)

### Thread Safety
- Uses locks for shared data access
- WebSocket runs in separate thread
- Main thread handles scheduling and lifecycle

### API Rate Limiting
- 0.5-second delay between API calls
- Retry logic with exponential backoff
- Throttled strike rotation checks

## Troubleshooting

### Authentication Issues
- Verify TOTP_KEY is correct (from Fyers app)
- Check CLIENT_ID format: `APP_ID-APP_TYPE`
- Ensure PIN is correct

### Gmail Not Working
1. **Enable 2-Step Verification**: Required for App Passwords
2. **Generate App Password**: 
   - Visit: https://myaccount.google.com/apppasswords
   - Select "Mail" and your device
   - Copy the 16-character password
3. **Check credentials in .env**:
   - `GMAIL_USER`: Your full Gmail address
   - `GMAIL_APP_PASSWORD`: The 16-char app password (no spaces)
   - `ALERT_EMAIL`: Recipient email address
4. **Run test**: `python test_gmail.py` to diagnose
5. **Check spam folder**: First emails might land in spam

### Email Not Received
- Check spam/junk folder
- Verify ALERT_EMAIL is correct
- Check Gmail account has not hit sending limits
- Review logs in `ema_monitor.log` for errors

### WebSocket Disconnections
- Check internet connectivity
- Verify access token is valid
- Review logs in `ema_monitor.log`
- System auto-reconnects on transient failures

### Missing EMA Data
- First run uses `MANUAL_CURRENT_EMA` values
- Subsequent runs load from `ema_data.json`
- Update manual values if starting fresh

## Performance Considerations

- **Memory**: Keeps only last 10 candles per symbol
- **CPU**: Minimal (event-driven architecture)
- **Network**: Persistent WebSocket connection
- **Disk**: Logs rotate automatically (configure in logging setup)

## Future Enhancements

- [ ] Multi-timeframe EMA support
- [ ] Advanced order types (trailing SL, bracket orders)
- [ ] Machine learning for signal optimization
- [ ] Web dashboard for monitoring
- [ ] Telegram bot integration
- [ ] SMS alerts as backup notification channel
- [ ] Real money trading mode (currently paper only)
- [ ] Portfolio-level risk management
- [ ] Mobile app for alerts and monitoring

## Disclaimer

**This system is for educational and paper trading purposes only.**

- No guarantee of profits
- Past performance ≠ future results
- Test thoroughly before live trading
- Understand risks before deploying capital
- Consult a financial advisor

## License

[Specify your license here]

## Support

For issues, questions, or contributions, please [open an issue/contact details].

---

**Built with**: Python, Fyers API, Gmail SMTP, WebSockets, Real-time Data Processing

**Last Updated**: 2025-03-03
