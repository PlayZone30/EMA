from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws
import pyotp
import requests
import json
import hashlib
import os
import pytz
import time
from urllib.parse import urlparse, parse_qs
from datetime import datetime, time as dt_time, timedelta
import dotenv
import logging
from twilio.rest import Client as TwilioClient
from divergence_strategy import DivergenceStrategy

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('ema_monitor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

dotenv.load_dotenv()


class FyersAuthenticator:
    """Handles Fyers API authentication using TOTP."""
    
    BASE_URL = "https://api-t2.fyers.in/vagator/v2"
    BASE_URL_3 = "https://api-t1.fyers.in/api/v3"
    SUCCESS = 1
    ERROR = -1
    
    def __init__(self, client_id, secret_key, redirect_uri, username, pin, totp_key):
        self.client_id = client_id
        self.secret_key = secret_key
        self.redirect_uri = redirect_uri
        self.username = username
        self.pin = pin
        self.totp_key = totp_key
        
        # Parse APP_ID and APP_TYPE
        if "-" in client_id:
            self.app_id = client_id.split("-")[0]
            self.app_type = client_id.split("-")[1]
        else:
            raise ValueError("Invalid client_id format. Expected format: APP_ID-APP_TYPE")
        
        self.app_id_hash = hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()
    
    def _send_login_otp(self, app_id_type="2"):
        """Step 1: Send login OTP."""
        try:
            payload = {"fy_id": self.username, "app_id": app_id_type}
            result = requests.post(
                url=f"{self.BASE_URL}/send_login_otp",
                json=payload
            )
            
            if result.status_code != 200:
                return [self.ERROR, result.text]
            
            result_json = json.loads(result.text)
            if result_json.get("s") != "ok":
                return [self.ERROR, result_json]
                
            return [self.SUCCESS, result_json["request_key"]]
        except Exception as e:
            return [self.ERROR, str(e)]
    
    def _generate_totp(self):
        """Generate TOTP code."""
        try:
            totp_code = pyotp.TOTP(self.totp_key).now()
            return [self.SUCCESS, totp_code]
        except Exception as e:
            return [self.ERROR, str(e)]
    
    def _verify_totp(self, request_key, totp):
        """Step 2: Verify TOTP."""
        try:
            payload = {"request_key": request_key, "otp": totp}
            result = requests.post(
                url=f"{self.BASE_URL}/verify_otp",
                json=payload
            )
            
            if result.status_code != 200:
                return [self.ERROR, result.text]
            
            result_json = json.loads(result.text)
            if result_json.get("s") != "ok":
                return [self.ERROR, result_json]
                
            return [self.SUCCESS, result_json["request_key"]]
        except Exception as e:
            return [self.ERROR, str(e)]
    
    def _verify_pin(self, request_key):
        """Step 3: Verify PIN."""
        try:
            payload = {
                "request_key": request_key,
                "identity_type": "pin",
                "identifier": str(self.pin)
            }
            result = requests.post(
                url=f"{self.BASE_URL}/verify_pin",
                json=payload
            )
            
            if result.status_code != 200:
                return [self.ERROR, result.text]
            
            result_json = json.loads(result.text)
            if result_json.get("s") != "ok":
                return [self.ERROR, result_json]
                
            return [self.SUCCESS, result_json["data"]["access_token"]]
        except Exception as e:
            return [self.ERROR, str(e)]
    
    def _get_auth_code(self, access_token):
        """Step 4: Get auth code using access token."""
        try:
            payload = {
                "fyers_id": self.username,
                "app_id": self.app_id,
                "redirect_uri": self.redirect_uri,
                "appType": self.app_type,
                "code_challenge": "",
                "state": "sample_state",
                "scope": "",
                "nonce": "",
                "response_type": "code",
                "create_cookie": True
            }
            
            headers = {'Authorization': f'Bearer {access_token}'}
            result = requests.post(
                url=f"{self.BASE_URL_3}/token",
                json=payload,
                headers=headers
            )
            
            if result.status_code != 308:
                return [self.ERROR, f"Expected 308, got {result.status_code}: {result.text}"]
            
            result_json = json.loads(result.text)
            parsed_url = urlparse(result_json["Url"])
            auth_code = parse_qs(parsed_url.query)['auth_code'][0]
            
            return [self.SUCCESS, auth_code]
        except Exception as e:
            return [self.ERROR, str(e)]
    
    def _validate_auth_code(self, auth_code):
        """Step 5: Validate auth code to get final access token."""
        try:
            payload = {
                "grant_type": "authorization_code",
                "appIdHash": self.app_id_hash,
                "code": auth_code
            }
            
            result = requests.post(
                url=f"{self.BASE_URL_3}/validate-authcode",
                json=payload
            )
            
            if result.status_code != 200:
                return [self.ERROR, result.text]
            
            result_json = json.loads(result.text)
            if result_json.get("s") != "ok":
                return [self.ERROR, result_json]
                
            return [self.SUCCESS, result_json["access_token"]]
        except Exception as e:
            return [self.ERROR, str(e)]
    
    def get_access_token(self):
        """Main method to get access token automatically."""
        logger.info("=" * 70)
        logger.info("FYERS API - AUTOMATED LOGIN WITH TOTP")
        logger.info("=" * 70)
        logger.info(f"Client ID: {self.client_id}")
        logger.info(f"Username: {self.username}")
        
        # Step 1: Send login OTP
        logger.info("[1/6] Sending login OTP...")
        status, request_key = self._send_login_otp()
        if status == self.ERROR:
            logger.error(f"Error sending OTP: {request_key}")
            return None, f"Error sending OTP: {request_key}"
        logger.info("OTP sent successfully")
        
        # Step 2: Generate TOTP
        logger.info("[2/6] Generating TOTP...")
        status, totp = self._generate_totp()
        if status == self.ERROR:
            logger.error(f"Error generating TOTP: {totp}")
            return None, f"Error generating TOTP: {totp}"
        logger.info(f"TOTP generated: {totp}")
        
        # Step 3: Verify TOTP
        logger.info("[3/6] Verifying TOTP...")
        status, request_key = self._verify_totp(request_key, totp)
        if status == self.ERROR:
            logger.error(f"Error verifying TOTP: {request_key}")
            return None, f"Error verifying TOTP: {request_key}"
        logger.info("TOTP verified successfully")
        
        # Step 4: Verify PIN
        logger.info("[4/6] Verifying PIN...")
        status, temp_access_token = self._verify_pin(request_key)
        if status == self.ERROR:
            logger.error(f"Error verifying PIN: {temp_access_token}")
            return None, f"Error verifying PIN: {temp_access_token}"
        logger.info("PIN verified successfully")
        
        # Step 5: Get auth code
        logger.info("[5/6] Getting auth code...")
        status, auth_code = self._get_auth_code(temp_access_token)
        if status == self.ERROR:
            logger.error(f"Error getting auth code: {auth_code}")
            return None, f"Error getting auth code: {auth_code}"
        logger.info("Auth code obtained")
        
        # Step 6: Validate auth code
        logger.info("[6/6] Validating auth code...")
        status, access_token = self._validate_auth_code(auth_code)
        if status == self.ERROR:
            logger.error(f"Error validating auth code: {access_token}")
            return None, f"Error validating auth code: {access_token}"
        
        logger.info("Access token generated successfully!")
        return access_token, None


class WhatsAppNotifier:
    """Handles WhatsApp notifications for EMA alerts using Twilio."""
    
    def __init__(self, account_sid, auth_token, from_number, to_number):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number  # e.g., "whatsapp:+14155238886"
        self.to_number = to_number      # e.g., "whatsapp:+917989356894"
        self.last_alert_times = {}
        self.alert_cooldown = 300  # 5 minutes
        self.logger = logging.getLogger(f"{__name__}.WhatsAppNotifier")
        
        # Initialize Twilio client
        try:
            self.client = TwilioClient(account_sid, auth_token)
            self.logger.info("Twilio WhatsApp client initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize Twilio client: {e}")
            self.client = None
    
    def send_alert(self, symbol, symbol_name, ltp, ema_value, ema_period, timestamp):
        """Send WhatsApp alert when LTP touches EMA."""
        if symbol in self.last_alert_times:
            elapsed = (datetime.now() - self.last_alert_times[symbol]).total_seconds()
            if elapsed < self.alert_cooldown:
                self.logger.info(f"[{symbol_name}] Alert cooldown active. Next alert in {self.alert_cooldown - elapsed:.0f} seconds")
                return False
        
        if not self.client:
            self.logger.error("Twilio client not initialized. Cannot send WhatsApp message.")
            return False
        
        try:
            diff_amount = ltp - ema_value
            diff_percent = (diff_amount / ema_value) * 100
            
            # Format message for WhatsApp
            message_body = (
                f"🔔 *{symbol_name} EMA Alert*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"📅 *Time:* {timestamp}\n"
                f"📊 *Symbol:* {symbol}\n\n"
                f"💰 *LTP:* ₹{ltp:.2f}\n"
                f"📈 *{ema_period} EMA:* ₹{ema_value:.2f}\n"
                f"📉 *Difference:* ₹{abs(diff_amount):.2f} ({abs(diff_percent):.2f}%)\n\n"
                f"✅ LTP has touched the {ema_period} EMA threshold!"
            )
            
            self.logger.info(f"[{symbol_name}] Sending WhatsApp alert...")
            
            message = self.client.messages.create(
                body=message_body,
                from_=self.from_number,
                to=self.to_number
            )
            
            self.logger.info(f"[{symbol_name}] WhatsApp alert sent successfully. SID: {message.sid}")
            self.last_alert_times[symbol] = datetime.now()
            return True
            
        except Exception as e:
            self.logger.error(f"[{symbol_name}] Error sending WhatsApp message: {e}")
            return False


class EMACalculator:
    """Handles EMA calculation and persistence."""
    
    def __init__(self, symbols_config, data_file="ema_data.json"):
        self.symbols_config = symbols_config
        self.data_file = data_file
        self.current_emas = {}
        self.ema_multipliers = {}
        self.end_of_day_saved = False
        self.logger = logging.getLogger(f"{__name__}.EMACalculator")
        
        # Initialize multipliers
        for symbol, config in symbols_config.items():
            self.ema_multipliers[symbol] = 2 / (config['EMA_PERIOD'] + 1)
        
        self.load_emas()
    
    def load_emas(self):
        """Load saved EMAs from file."""
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r') as f:
                    saved_data = json.load(f)
                
                self.logger.info("Loading saved EMAs from previous session...")
                for symbol in self.symbols_config:
                    if symbol in saved_data:
                        self.current_emas[symbol] = saved_data[symbol]['ema']
                        self.logger.info(f"  {self.symbols_config[symbol]['name']}: ₹{self.current_emas[symbol]:.2f} (Last saved: {saved_data[symbol].get('timestamp', 'N/A')})")
                    else:
                        self.current_emas[symbol] = self.symbols_config[symbol]['MANUAL_CURRENT_EMA']
                        self.logger.warning(f"  {self.symbols_config[symbol]['name']} using manual EMA: ₹{self.current_emas[symbol]:.2f}")
            except json.JSONDecodeError:
                self.logger.warning(f"Could not read {self.data_file}. Using manual values.")
                self._use_manual_emas()
        else:
            self.logger.info(f"{self.data_file} not found. Using manual EMA values for first run.")
            self._use_manual_emas()
    
    def _use_manual_emas(self):
        """Use manual EMA values from config."""
        for symbol in self.symbols_config:
            self.current_emas[symbol] = self.symbols_config[symbol]['MANUAL_CURRENT_EMA']
    
    def save_emas(self, force=False):
        """Save current EMAs to file with timestamp."""
        if self.end_of_day_saved and not force:
            return
        
        self.logger.info(f"Saving EMAs to {self.data_file}...")
        
        emas_to_save = {}
        timestamp = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')
        
        for symbol, ema in self.current_emas.items():
            if ema is not None:
                emas_to_save[symbol] = {
                    'ema': ema,
                    'timestamp': timestamp
                }
                self.logger.info(f"  {self.symbols_config[symbol]['name']}: ₹{ema:.2f} at {timestamp}")
        
        with open(self.data_file, 'w') as f:
            json.dump(emas_to_save, f, indent=4)
        
        self.logger.info("EMAs saved successfully.")
        self.end_of_day_saved = True
    
    def update_ema(self, symbol, close_price):
        """Update EMA with new close price."""
        previous_ema = self.current_emas[symbol]
        multiplier = self.ema_multipliers[symbol]
        self.current_emas[symbol] = (close_price * multiplier) + (previous_ema * (1 - multiplier))
        return previous_ema, self.current_emas[symbol]
    
    def get_ema(self, symbol):
        """Get current EMA for a symbol."""
        return self.current_emas.get(symbol)
    
    def reset_end_of_day_flag(self):
        """Reset the end of day saved flag for new trading day."""
        self.end_of_day_saved = False


class CandleManager:
    """Manages 5-minute candle formation and tracking."""
    
    def __init__(self, symbols_config, timezone):
        self.symbols_config = symbols_config
        self.timezone = timezone
        self.current_candles = {}
        self.skip_first_incomplete = {}
        self.first_complete_candle_times = {}
        self.logger = logging.getLogger(f"{__name__}.CandleManager")
        
        # Initialize tracking for each symbol
        for symbol in symbols_config:
            self.skip_first_incomplete[symbol] = True
            self.first_complete_candle_times[symbol] = None
    
    def get_current_bucket(self):
        """Get the current 5-minute bucket start time."""
        now = datetime.now(self.timezone)
        minute = (now.minute // 5) * 5
        return now.replace(minute=minute, second=0, microsecond=0)
    
    def should_skip_candle(self, symbol, bucket_time):
        """Check if we should skip this candle based on EMA_CALCULATED_UNTIL."""
        if not self.skip_first_incomplete.get(symbol, True):
            return False
        
        if self.first_complete_candle_times.get(symbol) is not None:
            return False
        
        if symbol not in self.symbols_config:
            # For non-config symbols (Options), we don't skip. Start tracking immediately.
            return False
            
        config = self.symbols_config[symbol]
        try:
            ema_calc_hour, ema_calc_minute = map(int, config['EMA_CALCULATED_UNTIL'].split(":"))
            now = datetime.now(self.timezone)
            ema_calculated_time = now.replace(
                hour=ema_calc_hour,
                minute=ema_calc_minute,
                second=0,
                microsecond=0
            )
            
            if bucket_time <= ema_calculated_time:
                return True
            else:
                self.logger.info(f"[{config['name']}] FIRST COMPLETE CANDLE DETECTED")
                self.logger.info(f"  Previous EMA valid until: {config['EMA_CALCULATED_UNTIL']}")
                self.logger.info(f"  Starting EMA updates from: {bucket_time.strftime('%H:%M')}")
                self.skip_first_incomplete[symbol] = False
                self.first_complete_candle_times[symbol] = bucket_time
                return False
        except Exception as e:
            self.logger.warning(f"Error parsing EMA_CALCULATED_UNTIL for {config['name']}: {e}")
            self.skip_first_incomplete[symbol] = False
            return False
    
    def update_candle(self, symbol, ltp):
        """Update the 5-minute candle with new tick data."""
        bucket_time = self.get_current_bucket()
        current_candle = self.current_candles.get(symbol)
        
        if self.should_skip_candle(symbol, bucket_time):
            if current_candle is None or current_candle['bucket_time'] != bucket_time:
                self.current_candles[symbol] = {
                    'bucket_time': bucket_time,
                    'open': ltp,
                    'high': ltp,
                    'low': ltp,
                    'close': ltp,
                    'skip': True
                }
            else:
                current_candle.update(
                    high=max(current_candle['high'], ltp),
                    low=min(current_candle['low'], ltp),
                    close=ltp
                )
            return None  # No completed candle
        
        # Check if we need to close the previous candle
        if current_candle is None or current_candle['bucket_time'] != bucket_time:
            completed_candle = None
            
            if current_candle is not None and not current_candle.get('skip', False):
                completed_candle = current_candle.copy()
                # Rename bucket_time to time for strategy compatibility
                completed_candle['time'] = completed_candle.pop('bucket_time')
            
            # Start new candle
            self.current_candles[symbol] = {
                'bucket_time': bucket_time,
                'open': ltp,
                'high': ltp,
                'low': ltp,
                'close': ltp,
                'skip': False
            }
            
            return completed_candle
        else:
            # Update current candle
            current_candle.update(
                high=max(current_candle['high'], ltp),
                low=min(current_candle['low'], ltp),
                close=ltp
            )
            return None
    
    def reset_for_new_day(self):
        """Reset candle manager state for new trading day."""
        self.current_candles = {}
        for symbol in self.symbols_config:
            self.skip_first_incomplete[symbol] = True
            self.first_complete_candle_times[symbol] = None


class MarketScheduler:
    """Handles market timing and scheduling."""
    
    def __init__(self, timezone):
        self.timezone = timezone
        self.market_open_time = dt_time(9, 15)
        self.market_close_time = dt_time(15, 30)
        self.logger = logging.getLogger(f"{__name__}.MarketScheduler")
        # Indian stock market holidays 2025 (update this list annually)
        self.holidays = [
            datetime(2025, 1, 26),  # Republic Day
            datetime(2025, 3, 14),  # Holi
            datetime(2025, 3, 31),  # Id-Ul-Fitr
            datetime(2025, 4, 10),  # Mahavir Jayanti
            datetime(2025, 4, 14),  # Ambedkar Jayanti
            datetime(2025, 4, 18),  # Good Friday
            datetime(2025, 5, 1),   # Maharashtra Day
            datetime(2025, 8, 15),  # Independence Day
            datetime(2025, 8, 27),  # Ganesh Chaturthi
            datetime(2025, 10, 2),  # Gandhi Jayanti
            datetime(2025, 10, 21), # Dussehra
            datetime(2025, 11, 5),  # Diwali/Laxmi Pujan
            datetime(2025, 11, 24), # Gurunanak Jayanti
            datetime(2025, 12, 25), # Christmas
        ]
    
    def is_market_holiday(self, date=None):
        """Check if the given date is a market holiday."""
        if date is None:
            date = datetime.now(self.timezone).date()
        
        # Check if it's weekend (Saturday=5, Sunday=6)
        if date.weekday() >= 5:
            return True
        
        # Check if it's a holiday
        for holiday in self.holidays:
            if holiday.date() == date:
                return True
        
        return False
    
    def is_market_open(self):
        """Check if market is currently open."""
        now = datetime.now(self.timezone)
        current_time = now.time()
        current_date = now.date()
        
        # Check if it's a holiday or weekend
        if self.is_market_holiday(current_date):
            return False
        
        # Check if current time is within market hours
        return self.market_open_time <= current_time <= self.market_close_time
    
    def seconds_until_market_open(self):
        """Calculate seconds until next market open."""
        now = datetime.now(self.timezone)
        current_date = now.date()
        current_time = now.time()
        
        # If market is currently open, return 0
        if self.is_market_open():
            return 0
        
        # Find the next market open time
        days_ahead = 0
        
        # If today is a trading day but market hasn't opened yet, use today
        # If today is a trading day but market already closed, start from tomorrow
        if not self.is_market_holiday(current_date) and current_time < self.market_open_time:
            # Market hasn't opened today yet
            days_ahead = 0
        else:
            # Market already closed today or today is holiday - start from tomorrow
            days_ahead = 1
        
        while days_ahead < 7:
            check_date = (now + timedelta(days=days_ahead)).date()
            
            if not self.is_market_holiday(check_date):
                market_open = datetime.combine(
                    check_date,
                    self.market_open_time,
                    tzinfo=self.timezone
                )
                
                if market_open > now:
                    seconds = (market_open - now).total_seconds()
                    return seconds
            
            days_ahead += 1
        
        return 86400  # Default to 24 hours if no market day found
    
    def get_market_close_time_today(self):
        """Get today's market close time as datetime object."""
        now = datetime.now(self.timezone)
        return datetime.combine(
            now.date(),
            self.market_close_time,
            tzinfo=self.timezone
        )


class EMAMonitor:
    """Main class that coordinates all components for EMA monitoring."""
    
    def __init__(self, access_token, client_id, symbols_config, whatsapp_config):
        self.access_token = access_token
        self.client_id = client_id
        self.symbols_config = symbols_config
        self.timezone = pytz.timezone('Asia/Kolkata')
        self.logger = logging.getLogger(f"{__name__}.EMAMonitor")
        
        # Initialize components
        self.whatsapp_notifier = WhatsAppNotifier(**whatsapp_config)
        self.ema_calculator = EMACalculator(symbols_config)
        self.candle_manager = CandleManager(symbols_config, self.timezone)
        self.market_scheduler = MarketScheduler(self.timezone)
        
        # Initialize Fyers Model for API calls (needed for Option Chain)
        self.fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")
        
        # Initialize Divergence Strategy
        self.divergence_strategy = DivergenceStrategy(self.fyers)
        self.divergence_strategy.load_capital_state()  # Load saved capital
        self.option_symbols = set() # Track currently subscribed option symbols
        
        # Live data tracking
        self.live_symbol_data = {}
        
        # WebSocket
        self.fyers_ws = None
        self.is_websocket_active = False
        self.should_stop = False
        self.daily_cycle_completed = False  # Track if today's cycle is done
    
    def check_ema_touch(self, symbol, ltp, ema_value):
        """Check if LTP has touched the EMA."""
        if ema_value is None:
            return False
        
        threshold = self.symbols_config[symbol]['TOUCH_THRESHOLD']
        percentage_diff = abs((ltp - ema_value) / ema_value * 100)
        return percentage_diff <= threshold
    
    def _on_message(self, message):
        """Handle incoming WebSocket messages."""
        try:
            if not isinstance(message, dict) or "ltp" not in message or "symbol" not in message:
                return
            
            symbol = message["symbol"]
            ltp = message["ltp"]
            
            # Skip unknown symbols (not in config and not in options)
            if symbol not in self.symbols_config and symbol not in self.option_symbols:
                return
            
            # Check if market is still open
            if not self.market_scheduler.is_market_open():
                self.logger.warning("Market closed. Stopping WebSocket...")
                self.stop_websocket()
                return
            
            timestamp = datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S")
            current_dt = datetime.now(self.timezone)
            
            # --- DIVERGENCE STRATEGY INTEGRATION ---
            # Pass tick to strategy for trade management (SL/Target)
            self.divergence_strategy.update_ltp(symbol, ltp, current_dt)
            
            # Check for strike rotation on NIFTY spot ticks only (throttled in strategy)
            if symbol == "NSE:NIFTY50-INDEX":
                ce_sym = self.divergence_strategy.current_ce_symbol
                pe_sym = self.divergence_strategy.current_pe_symbol
                
                # Check for strike rotation (throttled to every 30 seconds inside strategy)
                new_ce, new_pe = self.divergence_strategy.check_strike_rotation(ltp)
                
                if new_ce and new_pe:
                    # Rotation happened - unsubscribe old, subscribe new
                    old_symbols = [s for s in [ce_sym, pe_sym] if s]
                    if old_symbols:
                        self.fyers_ws.unsubscribe(symbols=old_symbols)
                        for s in old_symbols:
                            self.option_symbols.discard(s)
                            if s in self.live_symbol_data:
                                del self.live_symbol_data[s]
                    
                    # Subscribe new
                    new_symbols = [new_ce, new_pe]
                    self.fyers_ws.subscribe(symbols=new_symbols, data_type="SymbolUpdate")
                    for s in new_symbols:
                        self.option_symbols.add(s)
            
            # Pass candle data to strategy if it's a completed candle
            # We need to track candles for Options too.
            # CandleManager currently only tracks symbols in symbols_config.
            # We need to extend CandleManager or handle Option candles separately.
            # For simplicity, let's add dynamic symbols to CandleManager?
            # Or just handle option candles here locally since CandleManager is coupled with Config.
            
            # Let's use a simple local candle tracker for options or add to CandleManager dynamically.
            # Adding to CandleManager is cleaner but requires modifying it to handle symbols without config.
            # Let's modify CandleManager.update_candle to handle unknown symbols gracefully (defaulting to 5 min).
            
            # For now, let's assume CandleManager can handle it if we don't crash on missing config.
            # We need to modify CandleManager.should_skip_candle to handle missing config.
            
            # Update candle and check for completion
            completed_candle = self.candle_manager.update_candle(symbol, ltp)
            
            if completed_candle:
                # Pass to Divergence Strategy
                self.divergence_strategy.process_candle(symbol, completed_candle)
                
                # Existing EMA Logic (Only for configured symbols)
                if symbol in self.symbols_config:
                    config = self.symbols_config[symbol]
                    close_price = completed_candle['close']
                    previous_ema, new_ema = self.ema_calculator.update_ema(symbol, close_price)
                    
                    print()  # New line before candle log
                    self.logger.info("="*60)
                    self.logger.info(f"[{config['name']}] 5-MIN CANDLE COMPLETED")
                    self.logger.info(f"  Time: {completed_candle['time'].strftime('%Y-%m-%d %H:%M')}")
                    self.logger.info(f"  Close: ₹{close_price:.2f}")
                    self.logger.info(f"  Previous EMA: ₹{previous_ema:.2f} | New EMA: ₹{new_ema:.2f}")
                    self.logger.info("="*60)
            
            # Update live data
            ema_value = self.ema_calculator.get_ema(symbol) # Returns None for options
            self.live_symbol_data[symbol] = {'ltp': ltp, 'ema': ema_value}
                
            # Build status line for spot symbols
            status_parts = []
            for s, config in self.symbols_config.items():
                data = self.live_symbol_data.get(s)
                if data:
                    status_parts.append(f"{config['name']}: ₹{data['ltp']:.2f} (EMA: ₹{data['ema']:.2f})")
                else:
                    status_parts.append(f"{config['name']}: Waiting...")
            
            # Add option prices to status line
            ce_sym = self.divergence_strategy.current_ce_symbol
            pe_sym = self.divergence_strategy.current_pe_symbol
            if ce_sym:
                ce_data = self.live_symbol_data.get(ce_sym)
                ce_strike = ce_sym.split('NIFTY')[1][:7] if 'NIFTY' in ce_sym else ce_sym[-10:]
                if ce_data:
                    status_parts.append(f"CE({ce_strike}): ₹{ce_data['ltp']:.2f}")
                else:
                    status_parts.append(f"CE({ce_strike}): Waiting...")
            if pe_sym:
                pe_data = self.live_symbol_data.get(pe_sym)
                pe_strike = pe_sym.split('NIFTY')[1][:7] if 'NIFTY' in pe_sym else pe_sym[-10:]
                if pe_data:
                    status_parts.append(f"PE({pe_strike}): ₹{pe_data['ltp']:.2f}")
                else:
                    status_parts.append(f"PE({pe_strike}): Waiting...")
            
            # Print tick data on single updating line (no newline, carriage return overwrites)
            # Add padding to ensure line is fully cleared when content is shorter
            status_line = f"[{timestamp}] {' | '.join(status_parts)}"
            # Pad to 150 chars to clear previous content, then carriage return
            print(f"\r{status_line:<150}", end='', flush=True)
            
            # Check for EMA touch
            if symbol in self.symbols_config and self.check_ema_touch(symbol, ltp, ema_value):
                config = self.symbols_config[symbol]
                print()  # New line before alert
                self.logger.warning("="*60)
                self.logger.warning(f"🔔 ALERT: [{config['name']}] LTP (₹{ltp:.2f}) TOUCHED EMA (₹{ema_value:.2f})")
                self.logger.warning("="*60)
                self.whatsapp_notifier.send_alert(
                    symbol, config['name'], ltp, ema_value,
                    config['EMA_PERIOD'], timestamp
                )
            
            # Check for market close time
            now = datetime.now(self.timezone)
            market_close = self.market_scheduler.get_market_close_time_today()
            
            if now >= market_close and not self.ema_calculator.end_of_day_saved:
                self.logger.info(f"Market closed at {market_close.strftime('%H:%M')}. Saving EMAs...")
                self.ema_calculator.save_emas()
                # Generate divergence strategy daily report
                self.divergence_strategy.generate_daily_report()
                self.divergence_strategy.save_capital_state()
                self.stop_websocket()
        
        except Exception as e:
            self.logger.error(f"Error in message handler: {e}", exc_info=True)
    
    def _on_error(self, message):
        self.logger.error(f"WebSocket Error: {message}")
    
    def _on_close(self, message):
        self.logger.info(f"WebSocket Connection Closed: {message}")
        self.is_websocket_active = False
    
    def _on_open(self):
        """Subscribe to symbols when WebSocket opens."""
        self.logger.info("="*60)
        self.logger.info("WebSocket Connected Successfully")
        self.logger.info("="*60)
        
        symbols_to_subscribe = list(self.symbols_config.keys())
        self.logger.info(f"Subscribing to: {', '.join(symbols_to_subscribe)}")
        
        for symbol in symbols_to_subscribe:
            config = self.symbols_config[symbol]
            self.logger.info(f"--- {config['name']} ({symbol}) ---")
            self.logger.info(f"  EMA Period: {config['EMA_PERIOD']}")
            self.logger.info(f"  Touch Threshold: ±{config['TOUCH_THRESHOLD']}%")
            self.logger.info(f"  Starting EMA: ₹{self.ema_calculator.get_ema(symbol):.2f}")
        
        self.logger.info("="*60)
        self.logger.info("Live monitoring started...")
        
        # Subscribe to EMA symbols
        self.fyers_ws.subscribe(symbols=symbols_to_subscribe, data_type="SymbolUpdate")
        
        # --- DIVERGENCE STRATEGY STARTUP ---
        self.logger.info("Initializing Divergence Strategy Options...")
        # Get initial strikes
        ce, pe = self.divergence_strategy.get_best_strikes(None)
        if ce and pe:
            self.logger.info(f"Initial Options Selected: CE={ce}, PE={pe}")
            self.divergence_strategy.current_ce_symbol = ce
            self.divergence_strategy.current_pe_symbol = pe
            
            option_symbols = [ce, pe]
            self.fyers_ws.subscribe(symbols=option_symbols, data_type="SymbolUpdate")
            for s in option_symbols:
                self.option_symbols.add(s)
        else:
            self.logger.warning("Failed to select initial options for Divergence Strategy")
            
        self.is_websocket_active = True
        self.fyers_ws.keep_running()
    
    def start_websocket(self):
        """Start the WebSocket connection."""
        if self.is_websocket_active:
            self.logger.warning("WebSocket is already active")
            return
        
        # Don't start if market is closed or past close time
        now = datetime.now(self.timezone)
        market_close = self.market_scheduler.get_market_close_time_today()
        if now >= market_close:
            self.logger.warning(f"Cannot start WebSocket - market already closed at {market_close.strftime('%H:%M')}")
            return
        
        if not self.market_scheduler.is_market_open():
            self.logger.warning("Cannot start WebSocket - market is not open")
            return
        
        try:
            self.logger.info("Initializing WebSocket connection...")
            self.fyers_ws = data_ws.FyersDataSocket(
                access_token=self.access_token,
                log_path="",
                litemode=False,
                write_to_file=False,
                reconnect=True,
                on_connect=self._on_open,
                on_close=self._on_close,
                on_error=self._on_error,
                on_message=self._on_message
            )
            self.fyers_ws.connect()
        
        except Exception as e:
            self.logger.error(f"Error starting WebSocket: {e}", exc_info=True)
            self.is_websocket_active = False
    
    def stop_websocket(self):
        """Stop the WebSocket connection."""
        if self.fyers_ws and self.is_websocket_active:
            try:
                self.logger.info("Stopping WebSocket connection...")
                # Unsubscribe from all symbols
                symbols_to_unsubscribe = list(self.symbols_config.keys())
                self.fyers_ws.unsubscribe(symbols=symbols_to_unsubscribe)
                self.is_websocket_active = False
                self.logger.info("WebSocket stopped successfully")
            except Exception as e:
                self.logger.error(f"Error stopping WebSocket: {e}")
                # Force mark as inactive even if unsubscribe fails
                self.is_websocket_active = False
    
    def run_daily_cycle(self):
        """Run one complete daily cycle of monitoring."""
        # Check if cycle already completed for today
        now = datetime.now(self.timezone)
        market_close = self.market_scheduler.get_market_close_time_today()
        
        if self.daily_cycle_completed and now < market_close + timedelta(hours=12):
            # Cycle already completed and we're still on the same day
            # Don't run again until next trading day
            return
        
        # Check if today is a trading day
        if self.market_scheduler.is_market_holiday():
            self.logger.info(f"{now.strftime('%A, %B %d, %Y')}")
            self.logger.info("Market is closed today (Weekend/Holiday)")
            self.logger.info("Waiting for next trading day...")
            return
        
        # If it's past market close time, don't start a new cycle
        if now >= market_close:
            if not self.daily_cycle_completed:
                self.logger.info(f"Market already closed at {market_close.strftime('%H:%M')}. Waiting for next trading day.")
                self.daily_cycle_completed = True
            return
        
        # Wait until market opens
        if not self.market_scheduler.is_market_open():
            seconds_until_open = self.market_scheduler.seconds_until_market_open()
            hours = int(seconds_until_open // 3600)
            minutes = int((seconds_until_open % 3600) // 60)
            
            self.logger.info("Market is not open yet.")
            self.logger.info(f"  Market opens at 09:15 AM IST")
            self.logger.info(f"  Time until market open: {hours}h {minutes}m")
            self.logger.info("  Waiting...")
            
            # Sleep until 5 minutes before market opens
            sleep_time = max(0, seconds_until_open - 300)  # Wake up 5 min early
            if sleep_time > 0:
                time.sleep(sleep_time)
            
            # Final wait and check
            while not self.market_scheduler.is_market_open() and not self.should_stop:
                time.sleep(10)  # Check every 10 seconds
        
        if self.should_stop:
            return
        
        # Reset daily cycle flag for new trading day
        self.daily_cycle_completed = False
        
        # Reset for new trading day
        self.logger.info("="*60)
        self.logger.info(f"NEW TRADING DAY: {datetime.now(self.timezone).strftime('%A, %B %d, %Y')}")
        self.logger.info("="*60)
        
        self.ema_calculator.reset_end_of_day_flag()
        self.candle_manager.reset_for_new_day()
        self.divergence_strategy.reset_for_new_day()  # Reset divergence strategy
        
        # Start monitoring
        self.logger.info("Starting market monitoring...")
        self.start_websocket()
        
        # Keep running until market closes or error
        while self.market_scheduler.is_market_open() and not self.should_stop:
            time.sleep(30)  # Check every 30 seconds
            
            # Additional check to stop at market close
            now = datetime.now(self.timezone)
            market_close = self.market_scheduler.get_market_close_time_today()
            
            if now >= market_close:
                self.logger.info(f"Market closed at {market_close.strftime('%H:%M')}.")
                if not self.ema_calculator.end_of_day_saved:
                    self.ema_calculator.save_emas()
                    # Generate divergence strategy daily report
                    self.divergence_strategy.generate_daily_report()
                    self.divergence_strategy.save_capital_state()
                self.stop_websocket()
                self.daily_cycle_completed = True
                break
        
        # Ensure WebSocket is stopped
        if self.is_websocket_active:
            self.stop_websocket()
        
        # Mark cycle as completed
        self.daily_cycle_completed = True
        self.logger.info("Daily cycle completed.")
    
    def run(self):
        """Main run loop - runs 24/7 and handles daily cycles."""
        self.logger.info("="*70)
        self.logger.info("Multi-Symbol EMA Monitor - 24/7 Service")
        self.logger.info("="*70)
        self.logger.info(f"Service started at: {datetime.now(self.timezone).strftime('%Y-%m-%d %H:%M:%S IST')}")
        self.logger.info(f"Monitoring symbols: {', '.join([c['name'] for c in self.symbols_config.values()])}")
        self.logger.info(f"Market hours: 09:15 AM - 03:30 PM IST")
        self.logger.info(f"Market closed on: Weekends & Holidays")
        self.logger.info("="*70)
        
        try:
            while not self.should_stop:
                self.run_daily_cycle()
                
                # After daily cycle, wait until next trading day
                if not self.should_stop:
                    seconds_until_next = self.market_scheduler.seconds_until_market_open()
                    hours = int(seconds_until_next // 3600)
                    minutes = int((seconds_until_next % 3600) // 60)
                    
                    self.logger.info("Sleeping until next trading session...")
                    self.logger.info(f"  Next market open in: {hours}h {minutes}m")
                    
                    # Sleep in chunks to allow graceful shutdown
                    sleep_chunks = int(seconds_until_next / 60)  # 1-minute chunks
                    for _ in range(sleep_chunks):
                        if self.should_stop:
                            break
                        time.sleep(60)
        
        except KeyboardInterrupt:
            self.logger.info("="*60)
            self.logger.info("Received shutdown signal...")
            self.shutdown()
        
        except Exception as e:
            self.logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
            self.shutdown()
    
    def shutdown(self):
        """Gracefully shutdown the monitor."""
        self.logger.info("Shutting down gracefully...")
        self.should_stop = True
        
        # Stop WebSocket if active
        if self.is_websocket_active:
            self.stop_websocket()
        
        # Save EMAs if market was open
        if self.market_scheduler.is_market_open() or not self.ema_calculator.end_of_day_saved:
            self.logger.info("Saving current EMAs before shutdown...")
            self.ema_calculator.save_emas(force=True)
        
        self.logger.info("Goodbye! Service stopped.")


def main():
    """Main entry point."""
    # Configuration from environment variables
    CLIENT_ID = os.getenv("CLIENT_ID")
    SECRET_KEY = os.getenv("SECRET_KEY")
    REDIRECT_URI = "https://www.google.com"
    USERNAME = os.getenv("USERNAME")
    PIN = os.getenv("PIN")
    TOTP_KEY = os.getenv("TOTP_KEY")
    
    # Symbols configuration - UPDATE THESE VALUES AS NEEDED
    SYMBOLS_CONFIG = {
        "NSE:RELIANCE-EQ": {
            "name": "RELIANCE",
            "EMA_PERIOD": 45,
            "CANDLE_INTERVAL": 5,
            "TOUCH_THRESHOLD": 0.035,
            "MANUAL_CURRENT_EMA": float(os.getenv("MANUAL_CURRENT_EMA_RE", "1377.54")),
            "EMA_CALCULATED_UNTIL": os.getenv("EMA_CALCULATED_UNTIL", "09:35"),
        },
        "NSE:NIFTY50-INDEX": {
            "name": "NIFTY 50",
            "EMA_PERIOD": 45,
            "CANDLE_INTERVAL": 5,
            "TOUCH_THRESHOLD": 0.035,
            "MANUAL_CURRENT_EMA": float(os.getenv("MANUAL_CURRENT_EMA_N50", "25166.57")),
            "EMA_CALCULATED_UNTIL": os.getenv("EMA_CALCULATED_UNTIL", "09:35"),
        }
    }
    

    # WhatsApp configuration - Twilio
    WHATSAPP_CONFIG = {
        "account_sid": os.getenv("TWILIO_ACCOUNT_SID"),
        "auth_token": os.getenv("TWILIO_AUTH_TOKEN"),
        "from_number": os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"),
        "to_number": os.getenv("WHATSAPP_TO", "whatsapp:+917989356894")
    }
    
    # Validate configuration
    if not all([CLIENT_ID, SECRET_KEY, USERNAME, PIN, TOTP_KEY]):
        logger.error("Missing required environment variables!")
        logger.error("Please set: CLIENT_ID, SECRET_KEY, USERNAME, PIN, TOTP_KEY")
        return
    
    if not all([WHATSAPP_CONFIG['account_sid'], WHATSAPP_CONFIG['auth_token']]):
        logger.error("Missing Twilio WhatsApp configuration!")
        logger.error("Please set: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN")
        return
    
    logger.info("="*70)
    logger.info("INITIALIZING EMA MONITOR SERVICE")
    logger.info("="*70)
    
    # Get access token
    try:
        authenticator = FyersAuthenticator(
            client_id=CLIENT_ID,
            secret_key=SECRET_KEY,
            redirect_uri=REDIRECT_URI,
            username=USERNAME,
            pin=PIN,
            totp_key=TOTP_KEY
        )
        
        access_token, error = authenticator.get_access_token()
        
        if not access_token:
            logger.error(f"Failed to get access token: {error}")
            return
    
    except Exception as e:
        logger.error(f"Authentication error: {e}", exc_info=True)
        return
    
    # Create and start monitor
    try:
        monitor = EMAMonitor(
            access_token=access_token,
            client_id=CLIENT_ID,
            symbols_config=SYMBOLS_CONFIG,
            whatsapp_config=WHATSAPP_CONFIG
        )
        
        # Run the 24/7 monitoring service
        monitor.run()
    
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)


if __name__ == "__main__":
    main()
