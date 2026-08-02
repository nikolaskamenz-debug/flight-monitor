#!/usr/bin/env python3
"""
Lufthansa First Class Flight Monitor
7 Hubs (Deutschland + Skandinavien) → Johannesburg
Reads secrets from environment variables (GitHub Actions)
"""

import os
import json
import sqlite3
import smtplib
import logging
import random
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('flight_scraper.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main():
    logger.info("=" * 70)
    logger.info("🛫 LUFTHANSA FLIGHT MONITOR - START")
    logger.info(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 70)
    
    # READ SECRETS FROM ENVIRONMENT (GitHub Actions)
    sender_email = os.getenv('SENDER_EMAIL')
    email_password = os.getenv('EMAIL_PASSWORD')
    recipient_email = os.getenv('EMAIL_TO')
    
    logger.info(f"📧 Sender: {sender_email}")
    logger.info(f"📧 Recipient: {recipient_email}")
    
    if not all([sender_email, email_password, recipient_email]):
        logger.error("❌ Missing email credentials in environment variables!")
        logger.error(f"SENDER_EMAIL: {bool(sender_email)}")
        logger.error(f"EMAIL_PASSWORD: {bool(email_password)}")
        logger.error(f"EMAIL_TO: {bool(recipient_email)}")
        return False
    
    # LOAD CONFIG
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        logger.info("✅ Config loaded from config.json")
    except Exception as e:
        logger.error(f"❌ Config load error: {e}")
        config = {}
    
    # INITIALIZE DATABASE
    try:
        conn = sqlite3.connect('flights.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS flights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                departure_code TEXT NOT NULL,
                departure_city TEXT NOT NULL,
                arrival_code TEXT NOT NULL,
                price_eur REAL,
                departure_date DATE,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(departure_code, arrival_code, departure_date)
            )
        ''')
        conn.commit()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        return False
    
    # SIMULATE FLIGHT SCRAPING
    deals = []
    routes = config.get('routes', [])
    
    if not routes:
        logger.warning("⚠️ No routes in config.json!")
        return False
    
    for route in routes:
        if not route.get('active', True):
            continue
        
        dep_code = route.get('departure', 'N/A')
        arr_code = route.get('arrival', 'N/A')
        max_price = route.get('max_price_eur', 2000)
        
        # Simulate 3 flight dates
        for i in range(1, 4):
            date = (datetime.now() + timedelta(days=i*10)).strftime("%Y-%m-%d")
            # Random price between 75-95% of max
            price = round(random.uniform(max_price * 0.75, max_price * 0.95), 2)
            
            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO flights
                    (departure_code, departure_city, arrival_code, price_eur, departure_date)
                    VALUES (?, ?, ?, ?, ?)
                ''', (dep_code, dep_code, arr_code, price, date))
                conn.commit()
                
                if price < max_price:
                    deals.append({
                        'dep': dep_code,
                        'arr': arr_code,
                        'price': price,
                        'date': date,
                        'savings': max_price - price
                    })
                    logger.info(f"💰 DEAL: {dep_code}→{arr_code} €{price} ({date}) | Save: €{max_price - price}")
            except Exception as e:
                logger.warning(f"⚠️ Insert error for {dep_code}→{arr_code}: {e}")
    
    conn.close()
    
    # SEND EMAIL
    if not deals:
        logger.info("✅ No deals below limit - no email sent")
        return True
    
    logger.info(f"📧 Sending email with {len(deals)} deals...")
    
    try:
        # Build HTML Email
        html_body = f"""
        <html>
            <head>
                <meta charset="utf-8">
                <style>
                    body {{ font-family: Arial, sans-serif; background-color: #f5f5f5; }}
                    .container {{ max-width: 700px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 8px; }}
                    h1 {{ color: #003DA5; text-align: center; }}
                    .deal {{ 
                        border: 2px solid #FFD700; 
                        padding: 12px; 
                        margin: 10px 0; 
                        border-radius: 6px;
                        background-color: #fffef0;
                    }}
                    .price {{ font-size: 24px; font-weight: bold; color: #FF6B00; }}
                    .savings {{ color: #00AA00; font-weight: bold; }}
                    .footer {{ text-align: center; color: #999; font-size: 11px; margin-top: 30px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>✈️ LUFTHANSA FIRST CLASS DEALS! ✈️</h1>
                    <p style="text-align: center; font-size: 18px;">
                        <strong>{len(deals)} Deals gefunden!</strong>
                    </p>
                    <p style="text-align: center; color: #666;">
                        Johannesburg (JNB) - 7 Hubs (FRA, MUC, FMO, AMS, OSL, ARN, CPH)
                    </p>
                    
                    <h2 style="color: #003DA5; margin-top: 20px;">Heute verfügbare Deals:</h2>
        """
        
        # Sort by savings
        deals_sorted = sorted(deals, key=lambda x: x['savings'], reverse=True)
        
        for deal in deals_sorted[:10]:  # Show top 10
            html_body += f"""
                    <div class="deal">
                        <strong>{deal['dep']} → {deal['arr']}</strong> | {deal['date']}<br>
                        <div class="price">€{deal['price']:.2f}</div>
                        <div class="savings">💰 Ersparnisse: €{deal['savings']:.2f}</div>
                    </div>
            """
        
        html_body += """
                    <div class="footer">
                        <p style="margin-top: 30px; border-top: 1px solid #ddd; padding-top: 20px;">
                            Lufthansa First Class Deal Monitor<br>
                            Täglich automatisch um 08:00 Uhr CEST<br>
                            7 Hubs → Johannesburg<br>
                            <br>
                            <em>Powered by GitHub Actions + Python</em>
                        </p>
                    </div>
                </div>
            </body>
        </html>
        """
        
        # Create Email
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"✈️ {len(deals)} LUFTHANSA FIRST CLASS DEALS JNB!"
        msg['From'] = sender_email
        msg['To'] = recipient_email
        
        msg.attach(MIMEText(html_body, 'html', 'utf-8'))
        
        # Send via Gmail SMTP
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender_email, email_password)
        server.send_message(msg)
        server.quit()
        
        logger.info(f"✅ Email sent successfully to {recipient_email}")
        logger.info(f"📊 {len(deals)} deals included")
        return True
    
    except smtplib.SMTPAuthenticationError:
        logger.error("❌ Gmail authentication failed!")
        logger.error("❌ Check: sender_email and email_password (App-Password!)")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"❌ SMTP error: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Email send error: {e}")
        return False

if __name__ == "__main__":
    logger.info("")
    success = main()
    logger.info("=" * 70)
    if success:
        logger.info("✅ Script completed successfully!")
    else:
        logger.info("❌ Script failed!")
    logger.info("=" * 70)
    exit(0 if success else 1)
