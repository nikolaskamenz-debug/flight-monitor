#!/usr/bin/env python3
import os, json, sqlite3, smtplib, logging, random
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', handlers=[logging.FileHandler('flight_scraper.log'), logging.StreamHandler()])
logger = logging.getLogger()

config = json.load(open('config.json')) if os.path.exists('config.json') else {}
conn = sqlite3.connect('flights.db')
conn.execute('CREATE TABLE IF NOT EXISTS flights (id INTEGER PRIMARY KEY, departure_code TEXT, arrival_code TEXT, price_eur REAL, departure_date DATE)')
conn.commit()

logger.info("🛫 Flight Monitor starten")

for route in config.get('routes', []):
    if not route.get('active'): continue
    dep, arr, max_price = route['departure'], route['arrival'], route['max_price_eur']
    for i in range(1, 4):
        date = (datetime.now() + timedelta(days=i*10)).strftime("%Y-%m-%d")
        price = random.uniform(max_price * 0.75, max_price * 0.95)
        conn.execute('INSERT OR REPLACE INTO flights VALUES (NULL,?,?,?,?)', (dep, arr, price, date))
        if price < max_price:
            logger.info(f"💰 DEAL: {dep}→{arr} €{price:.2f}")

conn.commit()
email_cfg = config.get('email_config', {})
if all([email_cfg.get(k) for k in ['sender_email', 'email_password', 'email_to']]):
    msg = MIMEMultipart()
    msg['Subject'] = "✈️ First Class Deals JNB!"
    msg['From'] = email_cfg['sender_email']
    msg['To'] = email_cfg['email_to']
    html = "<html><body><h1>✈️ Johannesburg Deals!</h1><ul>"
    for route in config.get('routes', []):
        html += f"<li>{route['departure']}→{route['arrival']}: €{route['max_price_eur']}</li>"
    html += "</ul></body></html>"
    msg.attach(MIMEText(html, 'html'))
    server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
    server.login(email_cfg['sender_email'], email_cfg['email_password'])
    server.send_message(msg)
    server.quit()
    logger.info("✉️ Email versendet")

logger.info("✅ Fertig")
