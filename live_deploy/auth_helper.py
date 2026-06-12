"""
Fyers API Authentication Helper
================================
Extracted from main.py for standalone deployment.
Handles automated login with TOTP for Fyers API v3.
"""

import json
import hashlib
import logging
from urllib.parse import urlparse, parse_qs

import pyotp
import requests

logger = logging.getLogger(__name__)


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
