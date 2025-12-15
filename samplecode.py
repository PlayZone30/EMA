import os
import pandas as pd
import threading
import time
from datetime import datetime, timedelta
import pytz
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.dates import DateFormatter, date2num
import plotly.graph_objects as go
import plotly.io as pio
from IPython.display import display

# Configure Plotly for browser display
pio.renderers.default = "browser"

# Initialize Fyers session (assume access_token and client_id are set)
fyers = fyersModel.FyersModel(token=access_token, is_async=False, client_id=client_id, log_path="")
print("Fyers API initialized successfully")

# Timezone setup
ist = pytz.timezone('Asia/Kolkata')

# Global variables for live mode
straddle_candles = []  # List of dicts: {'time': dt, 'open': float, 'high': float, 'low': float, 'close': float}
current_straddle = {'open': 0, 'high': 0, 'low': 0, 'close': 0}
current_minute = None
last_ce_ltp = None
last_pe_ltp = None
ce_symbol = None
pe_symbol = None
lock = threading.Lock()
fyers_ws = None
fig = None
ax = None

# API delay
API_DELAY = 0.5

# From code 1: functions for ATM options
def get_nearest_expiry(symbol_base):
    """Fetch the nearest expiry timestamp for the given symbol."""
    try:
        time.sleep(API_DELAY)
        data = {"symbol": symbol_base, "strikecount": 5, "timestamp": ""}
        response = fyers.optionchain(data=data)
        if response["code"] == 200 and "data" in response and "expiryData" in response["data"]:
            expiry_data = response["data"]["expiryData"]
            expiry_timestamps = expiry_data[0]["expiry"]
            return str(expiry_timestamps) if expiry_timestamps else None
        else:
            print(f"Error fetching expiry data for {symbol_base}: {response}")
            return None
    except Exception as e:
        print(f"Exception in get_nearest_expiry for {symbol_base}: {e}")
        return None

def find_atm_options(symbol_base, current_price):
    """Find At-The-Money (ATM) options using expiry timestamp."""
    try:
        time.sleep(API_DELAY)
        nearest_expiry = get_nearest_expiry(symbol_base)
        if not nearest_expiry:
            print(f"No nearest expiry found for {symbol_base}")
            return None, None, None
        
        data = {"symbol": symbol_base, "strikecount": 30, "timestamp": nearest_expiry}
        response = fyers.optionchain(data=data)
        
        if response["code"] == 200 and "data" in response and "optionsChain" in response["data"]:
            options = response["data"]["optionsChain"]
            strikes = [option["strike_price"] for option in options 
                       if "strike_price" in option and option["strike_price"] > 0]
            if not strikes:
                print(f"No valid strikes found for {symbol_base} with expiry {nearest_expiry}")
                return None, None, None
            
            unique_strikes = list(set(strikes))
            unique_strikes.sort()
            
            closest_strike = min(unique_strikes, key=lambda x: abs(x - current_price))
            
            call_symbol = next((option["symbol"] for option in options 
                                if option.get("option_type") == "CE" 
                                and option.get("strike_price") == closest_strike), None)
            put_symbol = next((option["symbol"] for option in options 
                               if option.get("option_type") == "PE" 
                               and option.get("strike_price") == closest_strike), None)
            
            return closest_strike, call_symbol, put_symbol
        else:
            print(f"Error retrieving option chain for {symbol_base}: {response}")
            return None, None, None
    except Exception as e:
        print(f"Exception finding ATM options for {symbol_base}: {e}")
        return None, None, None

def get_current_price_with_retry(symbol, retries=3):
    """Get the current market price with retry logic"""
    for attempt in range(retries):
        try:
            time.sleep(API_DELAY)
            data = {"symbols": symbol}
            response = fyers.quotes(data)
            if response["code"] == 200 and "d" in response and len(response["d"]) > 0:
                return response["d"][0]["v"]["lp"]
            else:
                print(f"Error getting price for {symbol}: {response}")
                if attempt < retries - 1:
                    time.sleep(API_DELAY * 2)
        except Exception as e:
            print(f"Exception getting price for {symbol} (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                time.sleep(API_DELAY * 2)
    return None

def fetch_data(symbol, start, end):
    data = {
        "symbol": symbol,
        "resolution": "1",  # 1 minute
        "date_format": "1",
        "range_from": start,
        "range_to": end,
        "cont_flag": "0"
    }
    response = fyers.history(data)
    if response["code"] != 200 or "candles" not in response:
        print(f"Error fetching data for {symbol}: {response}")
        return pd.DataFrame()
    df = pd.DataFrame(response['candles'], columns=['datetime', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['datetime'], unit='s')
    utc = pytz.UTC
    df['datetime'] = df['datetime'].dt.tz_localize(utc).dt.tz_convert(ist)
    df.set_index('datetime', inplace=True)
    return df

def plot_historical_straddle(straddle_df):
    fig = go.Figure(data=[go.Candlestick(
        x=straddle_df.index,
        open=straddle_df['open'],
        high=straddle_df['high'],
        low=straddle_df['low'],
        close=straddle_df['close'],
        name='ATM Straddle'
    )])

    fig.update_layout(
        title="ATM Straddle (1-min) - IST",
        yaxis_title="Straddle Premium (₹)",
        xaxis_title="Time (IST)",
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        height=600,
        showlegend=True,
        xaxis=dict(
            tickformat='%H:%M',  
            dtick=3600000,       
        )
    )

    print("Opening ATM Straddle chart in browser with IST timezone...")
    fig.show(renderer="browser")

def process_websocket_message(message):
    global current_minute, current_straddle, last_ce_ltp, last_pe_ltp, straddle_candles
    try:
        # Print message for debugging
        print(f"WebSocket message received: {message}")
        
        # Handle different message formats that Fyers API might send
        if isinstance(message, dict):
            # Check for different possible keys in the message
            symbol_key = None
            ltp_key = None
            
            # Common keys used by Fyers API
            possible_symbol_keys = ['symbol', 'fyToken', 'fycode']
            possible_ltp_keys = ['ltp', 'last_traded_price', 'last_price', 'price']
            
            for key in possible_symbol_keys:
                if key in message:
                    symbol_key = key
                    break
                    
            for key in possible_ltp_keys:
                if key in message:
                    ltp_key = key
                    break
            
            if symbol_key and ltp_key:
                symbol = message[symbol_key]
                ltp = message[ltp_key]
                
                with lock:
                    if symbol == ce_symbol:
                        last_ce_ltp = ltp
                        print(f"CE LTP updated: {ltp}")
                    elif symbol == pe_symbol:
                        last_pe_ltp = ltp
                        print(f"PE LTP updated: {ltp}")
                    
                    if last_ce_ltp is None or last_pe_ltp is None:
                        return
                    
                    current_time = datetime.now(ist)
                    this_minute = current_time.replace(second=0, microsecond=0)
                    
                    straddle_price = last_ce_ltp + last_pe_ltp
                    print(f"Straddle price: {straddle_price} (CE: {last_ce_ltp}, PE: {last_pe_ltp})")
                    
                    if current_minute is None or this_minute > current_minute:
                        if current_minute is not None:
                            straddle_candles.append({
                                'time': current_minute,
                                'open': current_straddle['open'],
                                'high': current_straddle['high'],
                                'low': current_straddle['low'],
                                'close': current_straddle['close']
                            })
                            print(f"Candle completed: {straddle_candles[-1]}")
                        current_minute = this_minute
                        current_straddle = {
                            'open': straddle_price,
                            'high': straddle_price,
                            'low': straddle_price,
                            'close': straddle_price
                        }
                    else:
                        current_straddle['high'] = max(current_straddle['high'], straddle_price)
                        current_straddle['low'] = min(current_straddle['low'], straddle_price)
                        current_straddle['close'] = straddle_price
            else:
                print(f"Message format not recognized: {message}")
    except Exception as e:
        print(f"Error processing WebSocket message: {e}")

def onmessage(message):
    process_websocket_message(message)

def onerror(message):
    print(f"WebSocket Error: {message}")

def onclose(message):
    print(f"WebSocket Connection Closed: {message}")

def onopen():
    print("WebSocket connection established")
    symbols_to_subscribe = [ce_symbol, pe_symbol]
    if symbols_to_subscribe:
        print(f"Subscribing to {symbols_to_subscribe}")
        fyers_ws.subscribe(symbols=symbols_to_subscribe, data_type="SymbolUpdate")

def setup_websocket_connection():
    global fyers_ws
    try:
        fyers_ws = data_ws.FyersDataSocket(
            access_token=access_token,
            log_path="",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=onopen,
            on_close=onclose,
            on_error=onerror,
            on_message=onmessage,
            reconnect_retry=10
        )
        fyers_ws.connect()
    except Exception as e:
        print(f"Error setting up WebSocket connection: {e}")

def plot_live_straddle(ax):
    ax.clear()
    
    if not straddle_candles and (current_minute is None or current_straddle['open'] == 0):
        ax.text(0.5, 0.5, 'Waiting for data...', transform=ax.transAxes, 
                ha='center', va='center', fontsize=12)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        return
    
    # Collect all data to plot
    all_candles = straddle_candles.copy()
    
    # Add current incomplete candle if exists
    if current_minute is not None and current_straddle['open'] > 0:
        current_candle = {
            'time': current_minute,
            'open': current_straddle['open'],
            'high': current_straddle['high'],
            'low': current_straddle['low'],
            'close': current_straddle['close'],
            'is_current': True
        }
        all_candles.append(current_candle)
    
    if not all_candles:
        ax.text(0.5, 0.5, 'No data available', transform=ax.transAxes, 
                ha='center', va='center', fontsize=12)
        return
    
    print(f"Plotting {len(all_candles)} candles")  # Debug output
    
    # Extract data for plotting
    times = []
    opens = []
    highs = []
    lows = []
    closes = []
    
    for candle in all_candles:
        times.append(candle['time'])
        opens.append(candle['open'])
        highs.append(candle['high'])
        lows.append(candle['low'])
        closes.append(candle['close'])
    
    # Create a simple line plot first to ensure data is being plotted
    ax.plot(times, closes, 'b-', linewidth=1, alpha=0.7, label='Close Price')
    
    # Plot candlesticks manually
    for i, candle in enumerate(all_candles):
        time = candle['time']
        o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
        
        # Determine colors
        if candle.get('is_current', False):
            color = 'lightgreen' if c >= o else 'lightcoral'
            alpha = 0.7
            edge_color = 'gray'
        else:
            color = 'green' if c >= o else 'red'
            alpha = 0.8
            edge_color = 'black'
        
        # Calculate bar width (adjust based on time range)
        if len(times) > 1:
            # Use 80% of the minimum time interval
            min_interval = min([(times[j+1] - times[j]).total_seconds() for j in range(len(times)-1)])
            bar_width = timedelta(seconds=min_interval * 0.4)
        else:
            bar_width = timedelta(minutes=0.4)  # Default width
        
        # Draw the high-low line (wick)
        ax.plot([time, time], [l, h], color=edge_color, linewidth=1.5, alpha=alpha)
        
        # Draw the open-close rectangle (body)
        body_height = abs(c - o)
        body_bottom = min(o, c)
        
        if body_height > 0:  # Only draw rectangle if there's a body
            rect = plt.Rectangle((time - bar_width/2, body_bottom), bar_width, body_height,
                               facecolor=color, edgecolor=edge_color, alpha=alpha, linewidth=0.5)
            ax.add_patch(rect)
        else:
            # If open == close, draw a horizontal line
            ax.plot([time - bar_width/2, time + bar_width/2], [c, c], 
                   color=edge_color, linewidth=2, alpha=alpha)
    
    ax.set_title(f'Live ATM Straddle (1-min) - IST ({len(all_candles)} candles)')
    ax.set_ylabel('Straddle Premium (₹)')
    ax.set_xlabel('Time (IST)')
    
    # Format x-axis
    if times:
        ax.xaxis.set_major_formatter(DateFormatter('%H:%M'))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
        
        # Set appropriate limits
        time_padding = timedelta(minutes=2)
        ax.set_xlim(min(times) - time_padding, max(times) + time_padding)
        
        # Set y limits with padding
        if highs and lows:
            y_padding = (max(highs) - min(lows)) * 0.05
            ax.set_ylim(min(lows) - y_padding, max(highs) + y_padding)
    
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper left')

def update_plot(frame):
    with lock:
        # Copy data safely under lock
        temp_candles = straddle_candles.copy()
        temp_current = current_straddle.copy() if current_straddle['open'] > 0 else {}
        temp_minute = current_minute
    
    # Update the global variables that plot_live_straddle will access
    global straddle_candles, current_straddle, current_minute
    with lock:
        # Temporarily update for plotting (thread-safe)
        plot_live_straddle(ax)
    
    plt.tight_layout()
    return ax,

def debug_data_status():
    """Print current data status for debugging"""
    with lock:
        print(f"\n=== DEBUG DATA STATUS ===")
        print(f"Historical candles: {len(straddle_candles)}")
        if straddle_candles:
            print(f"First candle: {straddle_candles[0]}")
            print(f"Last candle: {straddle_candles[-1]}")
        
        print(f"Current minute: {current_minute}")
        print(f"Current straddle: {current_straddle}")
        print(f"Last CE LTP: {last_ce_ltp}")
        print(f"Last PE LTP: {last_pe_ltp}")
        print(f"CE Symbol: {ce_symbol}")
        print(f"PE Symbol: {pe_symbol}")
        print("========================\n")
    global fig, ax
    plt.ion()  # Enable interactive mode
    fig, ax = plt.subplots(figsize=(14, 8))
    
    # Set up the plot with initial empty state
    ax.set_title('Live ATM Straddle Monitor - Starting...')
    ax.set_xlabel('Time (IST)')
    ax.set_ylabel('Straddle Premium (₹)')
    ax.grid(True, alpha=0.3)
    
    # Create animation with longer interval for debugging
    ani = FuncAnimation(fig, update_plot, interval=2000, cache_frame_data=False, blit=False)
    
    # Force initial draw
    plt.draw()
    plt.show()
    
    return ani

def load_historical_for_live(ce_sym, pe_sym):
    global straddle_candles, current_minute, last_ce_ltp, last_pe_ltp
    now = datetime.now(ist)
    start_date = now.strftime("%Y-%m-%d")
    
    print(f"Loading historical data for {start_date}")
    ce_df = fetch_data(ce_sym, start_date, start_date)
    pe_df = fetch_data(pe_sym, start_date, start_date)
    
    if ce_df.empty or pe_df.empty:
        print("No historical data loaded. Starting with live data only.")
        return
    
    # Create straddle data
    straddle_df = pd.DataFrame()
    straddle_df['open'] = ce_df['open'] + pe_df['open']
    straddle_df['high'] = ce_df['high'] + pe_df['high']
    straddle_df['low'] = ce_df['low'] + pe_df['low']
    straddle_df['close'] = ce_df['close'] + pe_df['close']
    straddle_df.index = ce_df.index
    
    # Convert to candle list
    for idx, row in straddle_df.iterrows():
        straddle_candles.append({
            'time': idx.to_pydatetime(),
            'open': row['open'],
            'high': row['high'],
            'low': row['low'],
            'close': row['close']
        })
    
    print(f"Loaded {len(straddle_candles)} historical candles")
    
    # Set current_minute to the next minute after last historical
    if straddle_candles:
        last_time = straddle_candles[-1]['time']
        current_minute = (last_time + timedelta(minutes=1)).replace(second=0, microsecond=0)
    
    # Get initial ltps
    last_ce_ltp = get_current_price_with_retry(ce_sym)
    last_pe_ltp = get_current_price_with_retry(pe_sym)
    print(f"Initial LTPs - CE: {last_ce_ltp}, PE: {last_pe_ltp}")

def main():
    global ce_symbol, pe_symbol
    print("\nSelect mode:")
    print("1. Historical analysis (provide CE/PE and date)")
    print("2. Live ATM straddle monitoring")
    choice = input("Enter your choice (1-2): ").strip()
    
    if choice == '1':
        ce_sym = input("Enter CE symbol (e.g., NSE:NIFTY2581424600CE): ").strip()
        pe_sym = input("Enter PE symbol (e.g., NSE:NIFTY2581424600PE): ").strip()
        date = input("Enter date (YYYY-MM-DD): ").strip()
        
        ce_df = fetch_data(ce_sym, date, date)
        pe_df = fetch_data(pe_sym, date, date)
        
        if ce_df.empty or pe_df.empty:
            print("Failed to fetch data.")
            return
        
        straddle_df = pd.DataFrame()
        straddle_df['open'] = ce_df['open'] + pe_df['open']
        straddle_df['high'] = ce_df['high'] + pe_df['high']
        straddle_df['low'] = ce_df['low'] + pe_df['low']
        straddle_df['close'] = ce_df['close'] + pe_df['close']
        straddle_df.index = ce_df.index
        
        plot_historical_straddle(straddle_df)
        
    elif choice == '2':
        index_symbol = "NSE:NIFTY50-INDEX"
        current_price = get_current_price_with_retry(index_symbol)
        
        if current_price is None:
            print("Failed to get current Nifty price.")
            return
        
        print(f"Current Nifty price: {current_price}")
        _, ce_sym, pe_sym = find_atm_options(index_symbol, current_price)
        
        if ce_sym is None or pe_sym is None:
            print("Failed to find ATM options.")
            return
        
        ce_symbol = ce_sym
        pe_symbol = pe_sym
        print(f"Live monitoring ATM: CE={ce_symbol}, PE={pe_symbol}")
        
        # Load historical data first
        load_historical_for_live(ce_symbol, pe_symbol)
        
        # Setup WebSocket connection
        setup_websocket_connection()
        
        # Add a small delay to let WebSocket connect
        time.sleep(2)
        
        # Debug data status
        debug_data_status()
        
        # Initialize live plot
        ani = init_live_plot()
        
        # Keep running until interrupted
        try:
            print("Live monitoring started. Press Ctrl+C to stop.")
            # Print status every 10 seconds
            counter = 0
            while True:
                time.sleep(1)
                counter += 1
                if counter % 10 == 0:
                    debug_data_status()
        except KeyboardInterrupt:
            print("\nExiting live mode...")
            if fyers_ws:
                fyers_ws.close()
    else:
        print("Invalid choice.")

if __name__ == "__main__":
    main()