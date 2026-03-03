#!/usr/bin/env python3
"""
Test script for Gmail email notifications
"""

import os
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Load environment variables
load_dotenv()

def test_gmail():
    # Get credentials from .env
    gmail_user = os.getenv("GMAIL_USER")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD")
    to_email = os.getenv("ALERT_EMAIL")
    
    print("=" * 50)
    print("Gmail SMTP Test")
    print("=" * 50)
    print(f"Gmail User: {gmail_user}")
    print(f"App Password: {'*' * 10}..." if gmail_app_password else "App Password: NOT SET")
    print(f"To Email: {to_email}")
    print("=" * 50)
    
    # Validate credentials
    if not all([gmail_user, gmail_app_password, to_email]):
        print("❌ Error: Missing credentials in .env file")
        print("\n💡 Required variables:")
        print("   GMAIL_USER=your_email@gmail.com")
        print("   GMAIL_APP_PASSWORD=your_16_char_app_password")
        print("   ALERT_EMAIL=recipient@email.com")
        return False
    
    try:
        # Create test message
        msg = MIMEMultipart('alternative')
        msg['From'] = gmail_user
        msg['To'] = to_email
        msg['Subject'] = "🧪 EMA Monitor Test - Gmail SMTP"
        
        # HTML body
        html_body = """
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #2c3e50;">🧪 Test Message</h2>
            <hr style="border: 1px solid #3498db;">
            
            <p>This is a test message from your <strong>EMA Trading Monitor</strong>.</p>
            
            <div style="background-color: #d5f4e6; padding: 15px; border-left: 4px solid #27ae60; margin: 20px 0;">
                <p style="margin: 0;">✅ <strong>If you received this, Gmail notifications are working correctly!</strong></p>
            </div>
            
            <table style="width: 100%; margin-top: 20px;">
                <tr>
                    <td style="padding: 10px; background-color: #ecf0f1;"><strong>Test Time:</strong></td>
                    <td style="padding: 10px;">""" + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """</td>
                </tr>
                <tr>
                    <td style="padding: 10px; background-color: #ecf0f1;"><strong>SMTP Server:</strong></td>
                    <td style="padding: 10px;">smtp.gmail.com:587</td>
                </tr>
                <tr>
                    <td style="padding: 10px; background-color: #ecf0f1;"><strong>From:</strong></td>
                    <td style="padding: 10px;">""" + gmail_user + """</td>
                </tr>
            </table>
            
            <p style="color: #7f8c8d; font-size: 12px; margin-top: 30px;">
                <em>Sent via Gmail SMTP API</em>
            </p>
        </body>
        </html>
        """
        
        html_part = MIMEText(html_body, 'html')
        msg.attach(html_part)
        
        print("\n📤 Connecting to Gmail SMTP server...")
        
        # Connect to Gmail SMTP
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        
        print("🔐 Authenticating...")
        server.login(gmail_user, gmail_app_password)
        
        print("📧 Sending test email...")
        server.send_message(msg)
        server.quit()
        
        print(f"\n✅ Email sent successfully!")
        print(f"\n📬 Check your inbox at: {to_email}")
        print("\n💡 If you don't see it, check your spam folder.")
        return True
        
    except smtplib.SMTPAuthenticationError:
        print("\n❌ Authentication failed!")
        print("\n💡 Troubleshooting steps:")
        print("   1. Enable 2-Step Verification in your Google Account")
        print("   2. Generate an App Password:")
        print("      - Go to: https://myaccount.google.com/apppasswords")
        print("      - Select 'Mail' and your device")
        print("      - Copy the 16-character password")
        print("   3. Use the App Password (not your regular Gmail password)")
        return False
        
    except Exception as e:
        print(f"\n❌ Error sending email: {e}")
        print("\n💡 Troubleshooting tips:")
        print("   1. Check your internet connection")
        print("   2. Verify GMAIL_USER is correct")
        print("   3. Ensure GMAIL_APP_PASSWORD is the 16-char app password")
        print("   4. Check if Gmail SMTP is accessible from your network")
        return False

if __name__ == "__main__":
    test_gmail()
