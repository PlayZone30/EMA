#!/usr/bin/env python3
"""
Test script for WhatsApp messaging via Twilio
"""

import os
from dotenv import load_dotenv
from twilio.rest import Client as TwilioClient

# Load environment variables
load_dotenv()

def test_whatsapp():
    # Get credentials from .env
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_WHATSAPP_FROM")
    to_number = os.getenv("WHATSAPP_TO")
    
    print("=" * 50)
    print("WhatsApp Test - Twilio")
    print("=" * 50)
    print(f"Account SID: {account_sid[:10]}..." if account_sid else "Account SID: NOT SET")
    print(f"Auth Token: {'*' * 10}..." if auth_token else "Auth Token: NOT SET")
    print(f"From: {from_number}")
    print(f"To: {to_number}")
    print("=" * 50)
    
    # Validate credentials
    if not all([account_sid, auth_token, from_number, to_number]):
        print("❌ Error: Missing credentials in .env file")
        return False
    
    try:
        # Create Twilio client
        client = TwilioClient(account_sid, auth_token)
        
        # Send test message
        test_message = """🧪 *EMA Monitor Test Message*

This is a test message from your EMA Trading Monitor.

If you received this, WhatsApp notifications are working correctly! ✅

_Sent via Twilio WhatsApp API_"""
        
        print("\n📤 Sending test message...")
        
        message = client.messages.create(
            body=test_message,
            from_=from_number,
            to=to_number
        )
        
        print(f"\n✅ Message sent successfully!")
        print(f"   Message SID: {message.sid}")
        print(f"   Status: {message.status}")
        print(f"\n📱 Check your WhatsApp for the test message!")
        return True
        
    except Exception as e:
        print(f"\n❌ Error sending message: {e}")
        print("\n💡 Troubleshooting tips:")
        print("   1. Make sure you've joined the Twilio Sandbox")
        print("      (Send 'join <keyword>' to the Twilio WhatsApp number)")
        print("   2. Verify your Account SID and Auth Token are correct")
        print("   3. Check that phone numbers include country code")
        return False

if __name__ == "__main__":
    test_whatsapp()
