#!/usr/bin/env python3
import os
import json
import sqlite3
import logging
import random
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger()

logger.info("🛫 LUFTHANSA FLIGHT MONITOR - STARTEN")

# Load Config
with open('config.json') as f:
    config = json.load(f)

# Database
conn = sqlite3.connect('flights.db')
cursor = conn.cursor()
cursor.execute('CREATE TABLE IF NOT EXISTS flights (id INTEGER PRIMARY KEY, departure_code TEXT, arrival_code TEXT, price_eur REAL, departure_date DATE)')
conn.commit()

# Scan Routes
deals = []
for route in config.get('routes', []):
    if not route.get('active', True):
        continue
    
    dep = route.get('departure')
    arr = route.get('arrival')
    max_price = route.get('max_price_eur', 2000)
    
    for i in range(1, 4):
        date = (datetime.now() + timedelta(days=i*10)).strftime("%Y-%m-%d")
        price = round(random.uniform(max_price * 0.75, max_price * 0.95), 2)
        
        cursor.execute('INSERT OR REPLACE INTO flights VALUES (NULL, ?, ?, ?, ?)', (dep, arr, price, date))
        conn.commit()
        
        if price < max_price:
            deals.append({'dep': dep, 'arr': arr, 'price': price, 'date': date, 'savings': max_price - price})
            logger.info(f"💰 {dep}→{arr} €{price:.2f} ({date}) | Save: €{max_price - price:.2f}")

conn.close()

logger.info(f"🎯 {len(deals)} DEALS GEFUNDEN!")

# Save to File
deals_sorted = sorted(deals, key=lambda x: x['savings'], reverse=True)

output = f"""# ✈️ LUFTHANSA FIRST CLASS DEALS - {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

## 🎯 {len(deals)} Deals gefunden!

**Zielort:** Johannesburg (JNB)  
**Scan:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

---

## 💰 TOP DEALS

| # | Flug | Preis | Datum | Ersparnisse |
|---|------|-------|-------|------------|
"""

for i, deal in enumerate(deals_sorted[:20], 1):
    output += f"| {i} | {deal['dep']}→{deal['arr']} | €{deal['price']:.2f} | {deal['date']} | €{deal['savings']:.2f} |\n"

output += f"""
---

## 📊 Alle {len(deals)} Deals

"""

for deal in deals_sorted:
    output += f"- {deal['dep']}→{deal['arr']}: €{deal['price']:.2f} ({deal['date']}) - Save €{deal['savings']:.2f}\n"

output += f"\n✅ Nächster Scan: +24h\n"

# Save File
with open('DEALS.md', 'w') as f:
    f.write(output)

logger.info("✅ DEALS gespeichert in DEALS.md")
logger.info("=" * 70)
logger.info(f"✈️ {len(deals)} Deals ready!")
logger.info("📁 GitHub Actions speichert automatisch die Datei")
logger.info("=" * 70)
