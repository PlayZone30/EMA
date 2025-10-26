# from fyers_apiv3 import fyersModel
# from fyers_apiv3.FyersWebsocket import data_ws
# import pyotp
# import requests
# import json
# import hashlib
# import smtplib
# import os
# import pytz
# from urllib.parse import urlparse, parse_qs
# from email.mime.text import MIMEText
# from email.mime.multipart import MIMEMultipart
# from datetime import datetime, time as dt_time
# import dotenv

# dotenv.load_dotenv()


# class FyersAuthenticator:
#     """Handles Fyers API authentication using TOTP."""
    
#     BASE_URL = "https://api-t2.fyers.in/vagator/v2"
#     BASE_URL_3 = "https://api-t1.fyers.in/api/v3"
#     SUCCESS = 1
#     ERROR = -1
    
#     def __init__(self, client_id, secret_key, redirect_uri, username, pin, totp_key):
#         self.client_id = client_id
#         self.secret_key = secret_key
#         self.redirect_uri = redirect_uri
#         self.username = username
#         self.pin = pin
#         self.totp_key = totp_key
        
#         # Parse APP_ID and APP_TYPE
#         if "-" in client_id:
#             self.app_id = client_id.split("-")[0]
#             self.app_type = client_id.split("-")[1]
#         else:
#             raise ValueError("Invalid client_id format. Expected format: APP_ID-APP_TYPE")
        
#         self.app_id_hash = hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()
    
#     def _send_login_otp(self, app_id_type="2"):
#         """Step 1: Send login OTP."""
#         try:
#             payload = {"fy_id": self.username, "app_id": app_id_type}
#             result = requests.post(
#                 url=f"{self.BASE_URL}/send_login_otp",
#                 json=payload
#             )
            
#             if result.status_code != 200:
#                 return [self.ERROR, result.text]
            
#             result_json = json.loads(result.text)
#             if result_json.get("s") != "ok":
#                 return [self.ERROR, result_json]
                
#             return [self.SUCCESS, result_json["request_key"]]
#         except Exception as e:
#             return [self.ERROR, str(e)]
    
#     def _generate_totp(self):
#         """Generate TOTP code."""
#         try:
#             totp_code = pyotp.TOTP(self.totp_key).now()
#             return [self.SUCCESS, totp_code]
#         except Exception as e:
#             return [self.ERROR, str(e)]
    
#     def _verify_totp(self, request_key, totp):
#         """Step 2: Verify TOTP."""
#         try:
#             payload = {"request_key": request_key, "otp": totp}
#             result = requests.post(
#                 url=f"{self.BASE_URL}/verify_otp",
#                 json=payload
#             )
            
#             if result.status_code != 200:
#                 return [self.ERROR, result.text]
            
#             result_json = json.loads(result.text)
#             if result_json.get("s") != "ok":
#                 return [self.ERROR, result_json]
                
#             return [self.SUCCESS, result_json["request_key"]]
#         except Exception as e:
#             return [self.ERROR, str(e)]
    
#     def _verify_pin(self, request_key):
#         """Step 3: Verify PIN."""
#         try:
#             payload = {
#                 "request_key": request_key,
#                 "identity_type": "pin",
#                 "identifier": str(self.pin)
#             }
#             result = requests.post(
#                 url=f"{self.BASE_URL}/verify_pin",
#                 json=payload
#             )
            
#             if result.status_code != 200:
#                 return [self.ERROR, result.text]
            
#             result_json = json.loads(result.text)
#             if result_json.get("s") != "ok":
#                 return [self.ERROR, result_json]
                
#             return [self.SUCCESS, result_json["data"]["access_token"]]
#         except Exception as e:
#             return [self.ERROR, str(e)]
    
#     def _get_auth_code(self, access_token):
#         """Step 4: Get auth code using access token."""
#         try:
#             payload = {
#                 "fyers_id": self.username,
#                 "app_id": self.app_id,
#                 "redirect_uri": self.redirect_uri,
#                 "appType": self.app_type,
#                 "code_challenge": "",
#                 "state": "sample_state",
#                 "scope": "",
#                 "nonce": "",
#                 "response_type": "code",
#                 "create_cookie": True
#             }
            
#             headers = {'Authorization': f'Bearer {access_token}'}
#             result = requests.post(
#                 url=f"{self.BASE_URL_3}/token",
#                 json=payload,
#                 headers=headers
#             )
            
#             if result.status_code != 308:
#                 return [self.ERROR, f"Expected 308, got {result.status_code}: {result.text}"]
            
#             result_json = json.loads(result.text)
#             parsed_url = urlparse(result_json["Url"])
#             auth_code = parse_qs(parsed_url.query)['auth_code'][0]
            
#             return [self.SUCCESS, auth_code]
#         except Exception as e:
#             return [self.ERROR, str(e)]
    
#     def _validate_auth_code(self, auth_code):
#         """Step 5: Validate auth code to get final access token."""
#         try:
#             payload = {
#                 "grant_type": "authorization_code",
#                 "appIdHash": self.app_id_hash,
#                 "code": auth_code
#             }
            
#             result = requests.post(
#                 url=f"{self.BASE_URL_3}/validate-authcode",
#                 json=payload
#             )
            
#             if result.status_code != 200:
#                 return [self.ERROR, result.text]
            
#             result_json = json.loads(result.text)
#             if result_json.get("s") != "ok":
#                 return [self.ERROR, result_json]
                
#             return [self.SUCCESS, result_json["access_token"]]
#         except Exception as e:
#             return [self.ERROR, str(e)]
    
#     def get_access_token(self):
#         """Main method to get access token automatically."""
#         print("=" * 70)
#         print("FYERS API - AUTOMATED LOGIN WITH TOTP")
#         print("=" * 70)
#         print(f"\n📌 Configuration:")
#         print(f"   Client ID: {self.client_id}")
#         print(f"   Username: {self.username}")
        
#         # Step 1: Send login OTP
#         print("\n[1/6] Sending login OTP...")
#         status, request_key = self._send_login_otp()
#         if status == self.ERROR:
#             return None, f"Error sending OTP: {request_key}"
#         print("✓ OTP sent successfully")
        
#         # Step 2: Generate TOTP
#         print("\n[2/6] Generating TOTP...")
#         status, totp = self._generate_totp()
#         if status == self.ERROR:
#             return None, f"Error generating TOTP: {totp}"
#         print(f"✓ TOTP generated: {totp}")
        
#         # Step 3: Verify TOTP
#         print("\n[3/6] Verifying TOTP...")
#         status, request_key = self._verify_totp(request_key, totp)
#         if status == self.ERROR:
#             return None, f"Error verifying TOTP: {request_key}"
#         print("✓ TOTP verified successfully")
        
#         # Step 4: Verify PIN
#         print("\n[4/6] Verifying PIN...")
#         status, temp_access_token = self._verify_pin(request_key)
#         if status == self.ERROR:
#             return None, f"Error verifying PIN: {temp_access_token}"
#         print("✓ PIN verified successfully")
        
#         # Step 5: Get auth code
#         print("\n[5/6] Getting auth code...")
#         status, auth_code = self._get_auth_code(temp_access_token)
#         if status == self.ERROR:
#             return None, f"Error getting auth code: {auth_code}"
#         print(f"✓ Auth code obtained")
        
#         # Step 6: Validate auth code
#         print("\n[6/6] Validating auth code...")
#         status, access_token = self._validate_auth_code(auth_code)
#         if status == self.ERROR:
#             return None, f"Error validating auth code: {access_token}"
        
#         print("\n✅ Access token generated successfully!")
#         return access_token, None


# class EmailNotifier:
#     """Handles email notifications for EMA alerts."""
    
#     def __init__(self, sender_email, sender_password, recipient_email):
#         self.sender_email = sender_email
#         self.sender_password = sender_password
#         self.recipient_email = recipient_email
#         self.last_alert_times = {}
#         self.alert_cooldown = 300  # 5 minutes
    
#     def send_alert(self, symbol, symbol_name, ltp, ema_value, ema_period, timestamp):
#         """Send email alert when LTP touches EMA."""
#         if symbol in self.last_alert_times:
#             elapsed = (datetime.now() - self.last_alert_times[symbol]).total_seconds()
#             if elapsed < self.alert_cooldown:
#                 print(f"⏳ [{symbol_name}] Alert cooldown active. Next alert in {self.alert_cooldown - elapsed:.0f} seconds")
#                 return False
        
#         try:
#             msg = MIMEMultipart()
#             msg['From'] = self.sender_email
#             msg['To'] = self.recipient_email
#             msg['Subject'] = f"🔔 {symbol_name} Alert: LTP Touched {ema_period} EMA"
            
#             diff_amount = ltp - ema_value
#             diff_percent = (diff_amount / ema_value) * 100
            
#             body = f"""
#             <html><body>
#                 <h2 style="color: #764ba2;">🔔 {symbol_name} EMA Alert</h2>
#                 <p><strong>Time:</strong> {timestamp}</p>
#                 <p><strong>Symbol:</strong> {symbol}</p>
#                 <hr>
#                 <p><strong>Last Traded Price:</strong> ₹{ltp:.2f}</p>
#                 <p><strong>{ema_period} EMA (5-min):</strong> ₹{ema_value:.2f}</p>
#                 <p><strong>Difference:</strong> ₹{abs(diff_amount):.2f} ({abs(diff_percent):.2f}%)</p>
#                 <hr>
#                 <p>✓ LTP has touched the {ema_period} EMA threshold</p>
#             </body></html>
#             """
#             msg.attach(MIMEText(body, 'html'))
            
#             print(f"📧 [{symbol_name}] Sending email alert...")
#             with smtplib.SMTP('smtp.gmail.com', 587) as server:
#                 server.starttls()
#                 server.login(self.sender_email, self.sender_password)
#                 server.send_message(msg)
            
#             print(f"✓ [{symbol_name}] Email alert sent successfully")
#             self.last_alert_times[symbol] = datetime.now()
#             return True
            
#         except Exception as e:
#             print(f"✗ [{symbol_name}] Error sending email: {e}")
#             return False


# class EMACalculator:
#     """Handles EMA calculation and persistence."""
    
#     def __init__(self, symbols_config, data_file="ema_data.json"):
#         self.symbols_config = symbols_config
#         self.data_file = data_file
#         self.current_emas = {}
#         self.ema_multipliers = {}
#         self.end_of_day_saved = False
        
#         # Initialize multipliers
#         for symbol, config in symbols_config.items():
#             self.ema_multipliers[symbol] = 2 / (config['EMA_PERIOD'] + 1)
        
#         self.load_emas()
    
#     def load_emas(self):
#         """Load saved EMAs from file."""
#         if os.path.exists(self.data_file):
#             try:
#                 with open(self.data_file, 'r') as f:
#                     saved_emas = json.load(f)
#                 print("\n[INFO] Loading saved EMAs from yesterday...")
#                 for symbol in self.symbols_config:
#                     if symbol in saved_emas:
#                         self.current_emas[symbol] = saved_emas[symbol]
#                         print(f"   ✓ {self.symbols_config[symbol]['name']}: ₹{self.current_emas[symbol]:.2f}")
#                     else:
#                         self.current_emas[symbol] = self.symbols_config[symbol]['MANUAL_CURRENT_EMA']
#                         print(f"   ! {self.symbols_config[symbol]['name']} using manual EMA: ₹{self.current_emas[symbol]:.2f}")
#             except json.JSONDecodeError:
#                 print(f"\n[WARNING] Could not read {self.data_file}. Using manual values.")
#                 self._use_manual_emas()
#         else:
#             print(f"\n[INFO] {self.data_file} not found. Using manual EMA values.")
#             self._use_manual_emas()
    
#     def _use_manual_emas(self):
#         """Use manual EMA values from config."""
#         for symbol in self.symbols_config:
#             self.current_emas[symbol] = self.symbols_config[symbol]['MANUAL_CURRENT_EMA']
    
#     def save_emas(self):
#         """Save current EMAs to file."""
#         if self.end_of_day_saved:
#             return
        
#         print(f"\n\n{'='*60}")
#         print(f"[END OF DAY] Saving final EMAs to {self.data_file}...")
        
#         emas_to_save = {}
#         for symbol, ema in self.current_emas.items():
#             if ema is not None:
#                 emas_to_save[symbol] = ema
#                 print(f"   - {self.symbols_config[symbol]['name']}: ₹{ema:.2f}")
        
#         with open(self.data_file, 'w') as f:
#             json.dump(emas_to_save, f, indent=4)
        
#         print(f"✓ EMAs saved successfully.")
#         print(f"{'='*60}\n")
#         self.end_of_day_saved = True
    
#     def update_ema(self, symbol, close_price):
#         """Update EMA with new close price."""
#         previous_ema = self.current_emas[symbol]
#         multiplier = self.ema_multipliers[symbol]
#         self.current_emas[symbol] = (close_price * multiplier) + (previous_ema * (1 - multiplier))
#         return previous_ema, self.current_emas[symbol]
    
#     def get_ema(self, symbol):
#         """Get current EMA for a symbol."""
#         return self.current_emas.get(symbol)


# class CandleManager:
#     """Manages 5-minute candle formation and tracking."""
    
#     def __init__(self, symbols_config, timezone):
#         self.symbols_config = symbols_config
#         self.timezone = timezone
#         self.current_candles = {}
#         self.skip_first_incomplete = {}
#         self.first_complete_candle_times = {}
        
#         # Initialize tracking for each symbol
#         for symbol in symbols_config:
#             self.skip_first_incomplete[symbol] = True
#             self.first_complete_candle_times[symbol] = None
    
#     def get_current_bucket(self):
#         """Get the current 5-minute bucket start time."""
#         now = datetime.now(self.timezone)
#         minute = (now.minute // 5) * 5
#         return now.replace(minute=minute, second=0, microsecond=0)
    
#     def should_skip_candle(self, symbol, bucket_time):
#         """Check if we should skip this candle based on EMA_CALCULATED_UNTIL."""
#         if not self.skip_first_incomplete.get(symbol, True):
#             return False
        
#         if self.first_complete_candle_times.get(symbol) is not None:
#             return False
        
#         config = self.symbols_config[symbol]
#         try:
#             ema_calc_hour, ema_calc_minute = map(int, config['EMA_CALCULATED_UNTIL'].split(":"))
#             now = datetime.now(self.timezone)
#             ema_calculated_time = now.replace(
#                 hour=ema_calc_hour,
#                 minute=ema_calc_minute,
#                 second=0,
#                 microsecond=0
#             )
            
#             if bucket_time <= ema_calculated_time:
#                 return True
#             else:
#                 print(f"\n{'='*60}")
#                 print(f"[{config['name']}] FIRST COMPLETE CANDLE DETECTED")
#                 print(f"   Previous EMA valid until: {config['EMA_CALCULATED_UNTIL']}")
#                 print(f"   Starting EMA updates from: {bucket_time.strftime('%H:%M')}")
#                 print(f"{'='*60}\n")
#                 self.skip_first_incomplete[symbol] = False
#                 self.first_complete_candle_times[symbol] = bucket_time
#                 return False
#         except Exception as e:
#             print(f"\n⚠ Error parsing EMA_CALCULATED_UNTIL for {config['name']}: {e}")
#             self.skip_first_incomplete[symbol] = False
#             return False
    
#     def update_candle(self, symbol, ltp):
#         """Update the 5-minute candle with new tick data."""
#         bucket_time = self.get_current_bucket()
#         current_candle = self.current_candles.get(symbol)
        
#         if self.should_skip_candle(symbol, bucket_time):
#             if current_candle is None or current_candle['bucket_time'] != bucket_time:
#                 self.current_candles[symbol] = {
#                     'bucket_time': bucket_time,
#                     'open': ltp,
#                     'high': ltp,
#                     'low': ltp,
#                     'close': ltp,
#                     'skip': True
#                 }
#             else:
#                 current_candle.update(
#                     high=max(current_candle['high'], ltp),
#                     low=min(current_candle['low'], ltp),
#                     close=ltp
#                 )
#             return None  # No completed candle
        
#         # Check if we need to close the previous candle
#         if current_candle is None or current_candle['bucket_time'] != bucket_time:
#             completed_candle = None
            
#             if current_candle is not None and not current_candle.get('skip', False):
#                 completed_candle = current_candle.copy()
            
#             # Start new candle
#             self.current_candles[symbol] = {
#                 'bucket_time': bucket_time,
#                 'open': ltp,
#                 'high': ltp,
#                 'low': ltp,
#                 'close': ltp,
#                 'skip': False
#             }
            
#             return completed_candle
#         else:
#             # Update current candle
#             current_candle.update(
#                 high=max(current_candle['high'], ltp),
#                 low=min(current_candle['low'], ltp),
#                 close=ltp
#             )
#             return None


# class EMAMonitor:
#     """Main class that coordinates all components for EMA monitoring."""
    
#     def __init__(self, access_token, client_id, symbols_config, email_config):
#         self.access_token = access_token
#         self.client_id = client_id
#         self.symbols_config = symbols_config
#         self.timezone = pytz.timezone('Asia/Kolkata')
        
#         # Initialize components
#         self.email_notifier = EmailNotifier(**email_config)
#         self.ema_calculator = EMACalculator(symbols_config)
#         self.candle_manager = CandleManager(symbols_config, self.timezone)
        
#         # Live data tracking
#         self.live_symbol_data = {}
        
#         # WebSocket
#         self.fyers_ws = None
    
#     def check_ema_touch(self, symbol, ltp, ema_value):
#         """Check if LTP has touched the EMA."""
#         if ema_value is None:
#             return False
        
#         threshold = self.symbols_config[symbol]['TOUCH_THRESHOLD']
#         percentage_diff = abs((ltp - ema_value) / ema_value * 100)
#         return percentage_diff <= threshold
    
#     def _on_message(self, message):
#         """Handle incoming WebSocket messages."""
#         try:
#             if not isinstance(message, dict) or "ltp" not in message or "symbol" not in message:
#                 return
            
#             symbol = message["symbol"]
#             ltp = message["ltp"]
            
#             if symbol not in self.symbols_config:
#                 return
            
#             timestamp = datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S")
            
#             # Update candle and check for completion
#             completed_candle = self.candle_manager.update_candle(symbol, ltp)
            
#             if completed_candle:
#                 config = self.symbols_config[symbol]
#                 close_price = completed_candle['close']
#                 previous_ema, new_ema = self.ema_calculator.update_ema(symbol, close_price)
                
#                 print(f"\n\n{'='*60}")
#                 print(f"[{config['name']}] 5-MIN CANDLE COMPLETED")
#                 print(f"   Time: {completed_candle['bucket_time'].strftime('%Y-%m-%d %H:%M')}")
#                 print(f"   Close: ₹{close_price:.2f}")
#                 print(f"   Previous EMA: ₹{previous_ema:.2f} | New EMA: ₹{new_ema:.2f}")
#                 print(f"{'='*60}\n")
            
#             # Update live data
#             ema_value = self.ema_calculator.get_ema(symbol)
#             if ema_value is not None:
#                 self.live_symbol_data[symbol] = {'ltp': ltp, 'ema': ema_value}
                
#                 # Build status line for all symbols
#                 status_line = f"[{timestamp}] "
#                 for s, config in self.symbols_config.items():
#                     data = self.live_symbol_data.get(s)
#                     if data:
#                         status_line += f"| {config['name']} LTP: ₹{data['ltp']:.2f} (EMA: ₹{data['ema']:.2f}) "
#                     else:
#                         status_line += f"| {config['name']} LTP: Waiting... "
                
#                 print(status_line, end='\r')
                
#                 # Check for EMA touch
#                 if self.check_ema_touch(symbol, ltp, ema_value):
#                     config = self.symbols_config[symbol]
#                     print(f"\n\n{'='*60}")
#                     print(f"🔔 ALERT: [{config['name']}] LTP (₹{ltp:.2f}) TOUCHED EMA (₹{ema_value:.2f})")
#                     print(f"{'='*60}\n")
#                     self.email_notifier.send_alert(
#                         symbol, config['name'], ltp, ema_value,
#                         config['EMA_PERIOD'], timestamp
#                     )
            
#             # Check for end of day
#             now_time = datetime.now(self.timezone).time()
#             if now_time >= dt_time(15, 30) and not self.ema_calculator.end_of_day_saved:
#                 self.ema_calculator.save_emas()
        
#         except Exception as e:
#             print(f"\n✗ Error in message handler: {e}")
    
#     def _on_error(self, message):
#         print(f"\n✗ WebSocket Error: {message}")
    
#     def _on_close(self, message):
#         print(f"\n⚠ WebSocket Connection Closed: {message}")
    
#     def _on_open(self):
#         """Subscribe to symbols when WebSocket opens."""
#         print("\n" + "="*60)
#         print("✓ WebSocket Connected Successfully")
#         print("="*60)
        
#         symbols_to_subscribe = list(self.symbols_config.keys())
#         print(f"📡 Subscribing to: {', '.join(symbols_to_subscribe)}")
        
#         for symbol in symbols_to_subscribe:
#             config = self.symbols_config[symbol]
#             print(f"\n--- {config['name']} ({symbol}) ---")
#             print(f"   📈 EMA Period: {config['EMA_PERIOD']}")
#             print(f"   🎯 Touch Threshold: ±{config['TOUCH_THRESHOLD']}%")
#             print(f"   ⏰ Starting EMA: ₹{self.ema_calculator.get_ema(symbol):.2f}")
        
#         print("="*60)
#         print("\nLive monitoring started...\n")
        
#         self.fyers_ws.subscribe(symbols=symbols_to_subscribe, data_type="SymbolUpdate")
#         self.fyers_ws.keep_running()
    
#     def start(self):
#         """Start the monitoring system."""
#         print("\n" + "="*60)
#         print("Multi-Symbol EMA Monitor (5-MIN TIMEFRAME)")
#         print("="*60)
        
#         try:
#             print("\n🔧 Initializing WebSocket connection...")
#             self.fyers_ws = data_ws.FyersDataSocket(
#                 access_token=self.access_token,
#                 log_path="",
#                 litemode=False,
#                 write_to_file=False,
#                 reconnect=True,
#                 on_connect=self._on_open,
#                 on_close=self._on_close,
#                 on_error=self._on_error,
#                 on_message=self._on_message
#             )
#             self.fyers_ws.connect()
        
#         except KeyboardInterrupt:
#             print("\n\n" + "="*60)
#             print("Shutting down gracefully...")
#             self.ema_calculator.save_emas()
#             print("\n👋 Goodbye!")
        
#         except Exception as e:
#             print(f"\n✗ An unexpected error occurred: {e}")


# def main():
#     """Main entry point."""
#     # Configuration
#     CLIENT_ID = os.getenv("CLIENT_ID")
#     SECRET_KEY = os.getenv("SECRET_KEY")
#     REDIRECT_URI = "https://www.google.com"
#     USERNAME = os.getenv("USERNAME")
#     PIN = os.getenv("PIN")
#     TOTP_KEY = os.getenv("TOTP_KEY")
    
#     SYMBOLS_CONFIG = {
#         "NSE:RELIANCE-EQ": {
#             "name": "RELIANCE",
#             "EMA_PERIOD": 45,
#             "CANDLE_INTERVAL": 5,
#             "TOUCH_THRESHOLD": 0.035,
#             "MANUAL_CURRENT_EMA": 1377.54,
#             "EMA_CALCULATED_UNTIL": "09:35",
#         },
#         "NSE:NIFTY50-INDEX": {
#             "name": "NIFTY 50",
#             "EMA_PERIOD": 45,
#             "CANDLE_INTERVAL": 5,
#             "TOUCH_THRESHOLD": 0.035,
#             "MANUAL_CURRENT_EMA": 25166.57,
#             "EMA_CALCULATED_UNTIL": "09:35",
#         }
#     }
    
#     EMAIL_CONFIG = {
#         "sender_email": "pavansaireddy30@gmail.com",
#         "sender_password": "ollm utld cwxo dqtu",
#         "recipient_email": "pavansaireddy30@gmail.com"
#     }
    
#     # Get access token
#     authenticator = FyersAuthenticator(
#         client_id=CLIENT_ID,
#         secret_key=SECRET_KEY,
#         redirect_uri=REDIRECT_URI,
#         username=USERNAME,
#         pin=PIN,
#         totp_key=TOTP_KEY
#     )
    
#     access_token, error = authenticator.get_access_token()
    
#     if not access_token:
#         print(f"\n❌ Failed to get access token: {error}")
#         return
    
#     # Start monitoring
#     monitor = EMAMonitor(
#         access_token=access_token,
#         client_id=CLIENT_ID,
#         symbols_config=SYMBOLS_CONFIG,
#         email_config=EMAIL_CONFIG
#     )
    
#     monitor.start()


# if __name__ == "__main__":
#     main()





from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws
import pyotp
import requests
import json
import hashlib
import smtplib
import os
import pytz
import time
import schedule
from urllib.parse import urlparse, parse_qs
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, time as dt_time, timedelta
import dotenv

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
        print("=" * 70)
        print("FYERS API - AUTOMATED LOGIN WITH TOTP")
        print("=" * 70)
        print(f"\n📌 Configuration:")
        print(f"   Client ID: {self.client_id}")
        print(f"   Username: {self.username}")
        
        # Step 1: Send login OTP
        print("\n[1/6] Sending login OTP...")
        status, request_key = self._send_login_otp()
        if status == self.ERROR:
            return None, f"Error sending OTP: {request_key}"
        print("✓ OTP sent successfully")
        
        # Step 2: Generate TOTP
        print("\n[2/6] Generating TOTP...")
        status, totp = self._generate_totp()
        if status == self.ERROR:
            return None, f"Error generating TOTP: {totp}"
        print(f"✓ TOTP generated: {totp}")
        
        # Step 3: Verify TOTP
        print("\n[3/6] Verifying TOTP...")
        status, request_key = self._verify_totp(request_key, totp)
        if status == self.ERROR:
            return None, f"Error verifying TOTP: {request_key}"
        print("✓ TOTP verified successfully")
        
        # Step 4: Verify PIN
        print("\n[4/6] Verifying PIN...")
        status, temp_access_token = self._verify_pin(request_key)
        if status == self.ERROR:
            return None, f"Error verifying PIN: {temp_access_token}"
        print("✓ PIN verified successfully")
        
        # Step 5: Get auth code
        print("\n[5/6] Getting auth code...")
        status, auth_code = self._get_auth_code(temp_access_token)
        if status == self.ERROR:
            return None, f"Error getting auth code: {auth_code}"
        print(f"✓ Auth code obtained")
        
        # Step 6: Validate auth code
        print("\n[6/6] Validating auth code...")
        status, access_token = self._validate_auth_code(auth_code)
        if status == self.ERROR:
            return None, f"Error validating auth code: {access_token}"
        
        print("\n✅ Access token generated successfully!")
        return access_token, None


class EmailNotifier:
    """Handles email notifications for EMA alerts."""
    
    def __init__(self, sender_email, sender_password, recipient_email):
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.recipient_email = recipient_email
        self.last_alert_times = {}
        self.alert_cooldown = 300  # 5 minutes
    
    def send_alert(self, symbol, symbol_name, ltp, ema_value, ema_period, timestamp):
        """Send email alert when LTP touches EMA."""
        if symbol in self.last_alert_times:
            elapsed = (datetime.now() - self.last_alert_times[symbol]).total_seconds()
            if elapsed < self.alert_cooldown:
                print(f"⏳ [{symbol_name}] Alert cooldown active. Next alert in {self.alert_cooldown - elapsed:.0f} seconds")
                return False
        
        try:
            msg = MIMEMultipart()
            msg['From'] = self.sender_email
            msg['To'] = self.recipient_email
            msg['Subject'] = f"🔔 {symbol_name} Alert: LTP Touched {ema_period} EMA"
            
            diff_amount = ltp - ema_value
            diff_percent = (diff_amount / ema_value) * 100
            
            body = f"""
            <html><body>
                <h2 style="color: #764ba2;">🔔 {symbol_name} EMA Alert</h2>
                <p><strong>Time:</strong> {timestamp}</p>
                <p><strong>Symbol:</strong> {symbol}</p>
                <hr>
                <p><strong>Last Traded Price:</strong> ₹{ltp:.2f}</p>
                <p><strong>{ema_period} EMA (5-min):</strong> ₹{ema_value:.2f}</p>
                <p><strong>Difference:</strong> ₹{abs(diff_amount):.2f} ({abs(diff_percent):.2f}%)</p>
                <hr>
                <p>✓ LTP has touched the {ema_period} EMA threshold</p>
            </body></html>
            """
            msg.attach(MIMEText(body, 'html'))
            
            print(f"📧 [{symbol_name}] Sending email alert...")
            with smtplib.SMTP('smtp.gmail.com', 587) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.send_message(msg)
            
            print(f"✓ [{symbol_name}] Email alert sent successfully")
            self.last_alert_times[symbol] = datetime.now()
            return True
            
        except Exception as e:
            print(f"✗ [{symbol_name}] Error sending email: {e}")
            return False


class EMACalculator:
    """Handles EMA calculation and persistence."""
    
    def __init__(self, symbols_config, data_file="ema_data.json"):
        self.symbols_config = symbols_config
        self.data_file = data_file
        self.current_emas = {}
        self.ema_multipliers = {}
        self.end_of_day_saved = False
        
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
                
                print("\n[INFO] Loading saved EMAs from previous session...")
                for symbol in self.symbols_config:
                    if symbol in saved_data:
                        self.current_emas[symbol] = saved_data[symbol]['ema']
                        print(f"   ✓ {self.symbols_config[symbol]['name']}: ₹{self.current_emas[symbol]:.2f}")
                        print(f"      Last saved: {saved_data[symbol].get('timestamp', 'N/A')}")
                    else:
                        self.current_emas[symbol] = self.symbols_config[symbol]['MANUAL_CURRENT_EMA']
                        print(f"   ! {self.symbols_config[symbol]['name']} using manual EMA: ₹{self.current_emas[symbol]:.2f}")
            except json.JSONDecodeError:
                print(f"\n[WARNING] Could not read {self.data_file}. Using manual values.")
                self._use_manual_emas()
        else:
            print(f"\n[INFO] {self.data_file} not found. Using manual EMA values for first run.")
            self._use_manual_emas()
    
    def _use_manual_emas(self):
        """Use manual EMA values from config."""
        for symbol in self.symbols_config:
            self.current_emas[symbol] = self.symbols_config[symbol]['MANUAL_CURRENT_EMA']
    
    def save_emas(self, force=False):
        """Save current EMAs to file with timestamp."""
        if self.end_of_day_saved and not force:
            return
        
        print(f"\n\n{'='*60}")
        print(f"[SAVE] Saving EMAs to {self.data_file}...")
        
        emas_to_save = {}
        timestamp = datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')
        
        for symbol, ema in self.current_emas.items():
            if ema is not None:
                emas_to_save[symbol] = {
                    'ema': ema,
                    'timestamp': timestamp
                }
                print(f"   - {self.symbols_config[symbol]['name']}: ₹{ema:.2f} at {timestamp}")
        
        with open(self.data_file, 'w') as f:
            json.dump(emas_to_save, f, indent=4)
        
        print(f"✓ EMAs saved successfully.")
        print(f"{'='*60}\n")
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
                print(f"\n{'='*60}")
                print(f"[{config['name']}] FIRST COMPLETE CANDLE DETECTED")
                print(f"   Previous EMA valid until: {config['EMA_CALCULATED_UNTIL']}")
                print(f"   Starting EMA updates from: {bucket_time.strftime('%H:%M')}")
                print(f"{'='*60}\n")
                self.skip_first_incomplete[symbol] = False
                self.first_complete_candle_times[symbol] = bucket_time
                return False
        except Exception as e:
            print(f"\n⚠ Error parsing EMA_CALCULATED_UNTIL for {config['name']}: {e}")
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
        
        # If market is currently open, return 0
        if self.is_market_open():
            return 0
        
        # Find the next market open time
        days_ahead = 0
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
    
    def __init__(self, access_token, client_id, symbols_config, email_config):
        self.access_token = access_token
        self.client_id = client_id
        self.symbols_config = symbols_config
        self.timezone = pytz.timezone('Asia/Kolkata')
        
        # Initialize components
        self.email_notifier = EmailNotifier(**email_config)
        self.ema_calculator = EMACalculator(symbols_config)
        self.candle_manager = CandleManager(symbols_config, self.timezone)
        self.market_scheduler = MarketScheduler(self.timezone)
        
        # Live data tracking
        self.live_symbol_data = {}
        
        # WebSocket
        self.fyers_ws = None
        self.is_websocket_active = False
        self.should_stop = False
    
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
            
            if symbol not in self.symbols_config:
                return
            
            # Check if market is still open
            if not self.market_scheduler.is_market_open():
                print(f"\n⚠ Market closed. Stopping WebSocket...")
                self.stop_websocket()
                return
            
            timestamp = datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S")
            
            # Update candle and check for completion
            completed_candle = self.candle_manager.update_candle(symbol, ltp)
            
            if completed_candle:
                config = self.symbols_config[symbol]
                close_price = completed_candle['close']
                previous_ema, new_ema = self.ema_calculator.update_ema(symbol, close_price)
                
                print(f"\n\n{'='*60}")
                print(f"[{config['name']}] 5-MIN CANDLE COMPLETED")
                print(f"   Time: {completed_candle['bucket_time'].strftime('%Y-%m-%d %H:%M')}")
                print(f"   Close: ₹{close_price:.2f}")
                print(f"   Previous EMA: ₹{previous_ema:.2f} | New EMA: ₹{new_ema:.2f}")
                print(f"{'='*60}\n")
            
            # Update live data
            ema_value = self.ema_calculator.get_ema(symbol)
            if ema_value is not None:
                self.live_symbol_data[symbol] = {'ltp': ltp, 'ema': ema_value}
                
                # Build status line for all symbols
                status_line = f"[{timestamp}] "
                for s, config in self.symbols_config.items():
                    data = self.live_symbol_data.get(s)
                    if data:
                        status_line += f"| {config['name']} LTP: ₹{data['ltp']:.2f} (EMA: ₹{data['ema']:.2f}) "
                    else:
                        status_line += f"| {config['name']} LTP: Waiting... "
                
                print(status_line, end='\r')
                
                # Check for EMA touch
                if self.check_ema_touch(symbol, ltp, ema_value):
                    config = self.symbols_config[symbol]
                    print(f"\n\n{'='*60}")
                    print(f"🔔 ALERT: [{config['name']}] LTP (₹{ltp:.2f}) TOUCHED EMA (₹{ema_value:.2f})")
                    print(f"{'='*60}\n")
                    self.email_notifier.send_alert(
                        symbol, config['name'], ltp, ema_value,
                        config['EMA_PERIOD'], timestamp
                    )
            
            # Check for market close time
            now = datetime.now(self.timezone)
            market_close = self.market_scheduler.get_market_close_time_today()
            
            if now >= market_close and not self.ema_calculator.end_of_day_saved:
                print(f"\n⏰ Market closed at {market_close.strftime('%H:%M')}. Saving EMAs...")
                self.ema_calculator.save_emas()
                self.stop_websocket()
        
        except Exception as e:
            print(f"\n✗ Error in message handler: {e}")
    
    def _on_error(self, message):
        print(f"\n✗ WebSocket Error: {message}")
    
    def _on_close(self, message):
        print(f"\n⚠ WebSocket Connection Closed: {message}")
        self.is_websocket_active = False
    
    def _on_open(self):
        """Subscribe to symbols when WebSocket opens."""
        print("\n" + "="*60)
        print("✓ WebSocket Connected Successfully")
        print("="*60)
        
        symbols_to_subscribe = list(self.symbols_config.keys())
        print(f"📡 Subscribing to: {', '.join(symbols_to_subscribe)}")
        
        for symbol in symbols_to_subscribe:
            config = self.symbols_config[symbol]
            print(f"\n--- {config['name']} ({symbol}) ---")
            print(f"   📈 EMA Period: {config['EMA_PERIOD']}")
            print(f"   🎯 Touch Threshold: ±{config['TOUCH_THRESHOLD']}%")
            print(f"   ⏰ Starting EMA: ₹{self.ema_calculator.get_ema(symbol):.2f}")
        
        print("="*60)
        print("\nLive monitoring started...\n")
        
        self.fyers_ws.subscribe(symbols=symbols_to_subscribe, data_type="SymbolUpdate")
        self.is_websocket_active = True
        self.fyers_ws.keep_running()
    
    def start_websocket(self):
        """Start the WebSocket connection."""
        if self.is_websocket_active:
            print("⚠ WebSocket is already active")
            return
        
        try:
            print("\n🔧 Initializing WebSocket connection...")
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
            print(f"\n✗ Error starting WebSocket: {e}")
            self.is_websocket_active = False
    
    def stop_websocket(self):
        """Stop the WebSocket connection."""
        if self.fyers_ws and self.is_websocket_active:
            try:
                print("\n🛑 Stopping WebSocket connection...")
                self.fyers_ws.close()
                self.is_websocket_active = False
                print("✓ WebSocket stopped successfully")
            except Exception as e:
                print(f"✗ Error stopping WebSocket: {e}")
    
    def run_daily_cycle(self):
        """Run one complete daily cycle of monitoring."""
        # Check if today is a trading day
        if self.market_scheduler.is_market_holiday():
            now = datetime.now(self.timezone)
            print(f"\n📅 {now.strftime('%A, %B %d, %Y')}")
            print("🏖️ Market is closed today (Weekend/Holiday)")
            print("⏰ Waiting for next trading day...\n")
            return
        
        # Wait until market opens
        if not self.market_scheduler.is_market_open():
            seconds_until_open = self.market_scheduler.seconds_until_market_open()
            hours = int(seconds_until_open // 3600)
            minutes = int((seconds_until_open % 3600) // 60)
            
            print(f"\n⏰ Market is not open yet.")
            print(f"   Market opens at 09:15 AM IST")
            print(f"   Time until market open: {hours}h {minutes}m")
            print("   Waiting...\n")
            
            # Sleep until 5 minutes before market opens
            sleep_time = max(0, seconds_until_open - 300)  # Wake up 5 min early
            if sleep_time > 0:
                time.sleep(sleep_time)
            
            # Final wait and check
            while not self.market_scheduler.is_market_open():
                time.sleep(10)  # Check every 10 seconds
        
        # Reset for new trading day
        print("\n" + "="*60)
        print(f"🌅 NEW TRADING DAY: {datetime.now(self.timezone).strftime('%A, %B %d, %Y')}")
        print("="*60)
        
        self.ema_calculator.reset_end_of_day_flag()
        self.candle_manager.reset_for_new_day()
        
        # Start monitoring
        print("\n🚀 Starting market monitoring...")
        self.start_websocket()
        
        # Keep running until market closes or error
        while self.market_scheduler.is_market_open() and not self.should_stop:
            time.sleep(30)  # Check every 30 seconds
            
            # Additional check to stop at market close
            now = datetime.now(self.timezone)
            market_close = self.market_scheduler.get_market_close_time_today()
            
            if now >= market_close:
                print(f"\n🌙 Market closed at {market_close.strftime('%H:%M')}.")
                if not self.ema_calculator.end_of_day_saved:
                    self.ema_calculator.save_emas()
                self.stop_websocket()
                break
        
        # Ensure WebSocket is stopped
        if self.is_websocket_active:
            self.stop_websocket()
        
        print("\n✓ Daily cycle completed.")
    
    def run(self):
        """Main run loop - runs 24/7 and handles daily cycles."""
        print("\n" + "="*70)
        print("Multi-Symbol EMA Monitor - 24/7 Service")
        print("="*70)
        print(f"🕐 Service started at: {datetime.now(self.timezone).strftime('%Y-%m-%d %H:%M:%S IST')}")
        print(f"📊 Monitoring symbols: {', '.join([c['name'] for c in self.symbols_config.values()])}")
        print(f"⏰ Market hours: 09:15 AM - 03:30 PM IST")
        print(f"📅 Market closed on: Weekends & Holidays")
        print("="*70 + "\n")
        
        try:
            while not self.should_stop:
                self.run_daily_cycle()
                
                # After daily cycle, wait until next trading day
                if not self.should_stop:
                    seconds_until_next = self.market_scheduler.seconds_until_market_open()
                    hours = int(seconds_until_next // 3600)
                    minutes = int((seconds_until_next % 3600) // 60)
                    
                    print(f"\n💤 Sleeping until next trading session...")
                    print(f"   Next market open in: {hours}h {minutes}m\n")
                    
                    # Sleep in chunks to allow graceful shutdown
                    sleep_chunks = int(seconds_until_next / 60)  # 1-minute chunks
                    for _ in range(sleep_chunks):
                        if self.should_stop:
                            break
                        time.sleep(60)
        
        except KeyboardInterrupt:
            print("\n\n" + "="*60)
            print("⚠ Received shutdown signal...")
            self.shutdown()
        
        except Exception as e:
            print(f"\n✗ Unexpected error in main loop: {e}")
            self.shutdown()
    
    def shutdown(self):
        """Gracefully shutdown the monitor."""
        print("🛑 Shutting down gracefully...")
        self.should_stop = True
        
        # Stop WebSocket if active
        if self.is_websocket_active:
            self.stop_websocket()
        
        # Save EMAs if market was open
        if self.market_scheduler.is_market_open() or not self.ema_calculator.end_of_day_saved:
            print("💾 Saving current EMAs before shutdown...")
            self.ema_calculator.save_emas(force=True)
        
        print("\n👋 Goodbye! Service stopped.\n")


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
            "MANUAL_CURRENT_EMA": float(os.getenv("MANUAL_CURRENT_EMA_Re")),  # Set this to your starting EMA
            "EMA_CALCULATED_UNTIL": os.getenv("EMA_CALCULATED_UNTIL"),
        },
        "NSE:NIFTY50-INDEX": {
            "name": "NIFTY 50",
            "EMA_PERIOD": 45,
            "CANDLE_INTERVAL": 5,
            "TOUCH_THRESHOLD": 0.035,
            "MANUAL_CURRENT_EMA": float(os.getenv("MANUAL_CURRENT_EMA_N50")),  # Set this to your starting EMA
            "EMA_CALCULATED_UNTIL": os.getenv("EMA_CALCULATED_UNTIL"),
        }
    }
    
    # Email configuration
    EMAIL_CONFIG = {
        "sender_email": os.getenv("SENDER_EMAIL", "pavansaireddy30@gmail.com"),
        "sender_password": os.getenv("SENDER_PASSWORD", "ollm utld cwxo dqtu"),
        "recipient_email": os.getenv("RECIPIENT_EMAIL", "pavansaireddy30@gmail.com")
    }
    
    # Validate configuration
    if not all([CLIENT_ID, SECRET_KEY, USERNAME, PIN, TOTP_KEY]):
        print("\n❌ Error: Missing required environment variables!")
        print("Please set: CLIENT_ID, SECRET_KEY, USERNAME, PIN, TOTP_KEY")
        return
    
    print("\n" + "="*70)
    print("INITIALIZING EMA MONITOR SERVICE")
    print("="*70)
    
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
            print(f"\n❌ Failed to get access token: {error}")
            return
    
    except Exception as e:
        print(f"\n❌ Authentication error: {e}")
        return
    
    # Create and start monitor
    try:
        monitor = EMAMonitor(
            access_token=access_token,
            client_id=CLIENT_ID,
            symbols_config=SYMBOLS_CONFIG,
            email_config=EMAIL_CONFIG
        )
        
        # Run the 24/7 monitoring service
        monitor.run()
    
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

