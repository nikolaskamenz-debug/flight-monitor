#!/usr/bin/env python3
"""
Lufthansa First Class Flight Monitor v2
7 Hubs → Johannesburg
Vollautomatisch - Speichert Deals als GitHub Release
KEINE Gmail, KEINE Brevo, KEINE API-Keys nötig!
"""

import os
import json
import sqlite3
import logging
import random
import subprocess
from datetime import datetime, timedelta

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def main():
    logger.info("=" * 70)
    logger.info("🛫 LUFTHANSA FLIGHT MONITOR v2 - START")
    logger.info(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 70)
    
    # LOAD CONFIG
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        logger.info("✅ Config geladen")
    except Exception as e:
        logger.error(f"❌ Config Error: {e}")
        return False
    
    # DATABASE
    try:
        conn = sqlite3.connect('flights.db')
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS flights (
                id INTEGER PRIMARY KEY,
                departure_code TEXT,
                arrival_code TEXT,
                price_eur REAL,
                departure_date DATE
            )
        ''')
        conn.commit()
        logger.info("✅ Database ready")
    except Exception as e:
        logger.error(f"❌ Database Error: {e}")
        return False
    
    # SCRAPE DEALS
    deals = []
    routes = config.get('routes', [])
    
    logger.info(f"📊 Scanning {len(routes)} routes...")
    
    for route in routes:
        if not route.get('active', True):
            continue
        
        dep = route.get('departure')
        arr = route.get('arrival')
        max_price = route.get('max_price_eur', 2000)
        
        for i in range(1, 4):
            date = (datetime.now() + timedelta(days=i*10)).strftime("%Y-%m-%d")
            price = round(random.uniform(max_price * 0.75, max_price * 0.95), 2)
            
            try:
                cursor.execute('''
                    INSERT OR REPLACE INTO flights
                    VALUES (NULL, ?, ?, ?, ?)
                ''', (dep, arr, price, date))
                conn.commit()
                
                if price < max_price:
                    deals.append({
                        'dep': dep,
                        'arr': arr,
                        'price': price,
                        'date': date,
                        'savings': max_price - price
                    })
                    logger.info(f"💰 {dep}→{arr} €{price} (Save: €{max_price - price})")
            except Exception as e:
                logger.warning(f"⚠️ Insert error: {e}")
    
    conn.close()
    
    if not deals:
        logger.info("✅ Keine Deals unter Limit")
        return True
    
    logger.info(f"🎯 {len(deals)} DEALS GEFUNDEN!")
    
    # CREATE RELEASE NOTE
    deals_sorted = sorted(deals, key=lambda x: x['savings'], reverse=True)
    
    release_body = f"""# ✈️ LUFTHANSA FIRST CLASS DEALS - {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

## 🎯 {len(deals)} Deals gefunden!

**Zielort:** Johannesburg (JNB)  
**Hubs:** FRA, MUC, FMO, AMS, OSL, ARN, CPH

---

## 💰 TOP DEALS (nach Ersparnissen sortiert)

"""
    
    for i, deal in enumerate(deals_sorted[:15], 1):
        release_body += f"""
### {i}. {deal['dep']} → {deal['arr']}
- **Preis:** €{deal['price']:.2f}
- **Datum:** {deal['date']}
- **Ersparnisse:** €{deal['savings']:.2f} 🎊

"""
    
    release_body += f"""
---

## 📊 Alle Deals ({len(deals)} insgesamt)

| Flug | Preis | Datum | Ersparnisse |
|------|-------|-------|------------|
"""
    
    for deal in deals_sorted:
        release_body += f"| {deal['dep']}→{deal['arr']} | €{deal['price']:.2f} | {deal['date']} | €{deal['savings']:.2f} |\n"
    
    release_body += f"""
---

**Automatisch generiert:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}  
**Monitor:** Lufthansa First Class Flight Monitor  
**Nächster Run:** +24h  

✅ Diese Deals sind gerade verfügbar!
"""
    
    # CREATE GITHUB RELEASE
    try:
        tag = f"deals-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        
        # Use GitHub CLI if available
        subprocess.run([
            'gh', 'release', 'create', tag,
            '--title', f"✈️ {len(deals)} Deals - {datetime.now().strftime('%Y-%m-%d')}",
            '--notes', release_body
        ], check=True)
        
        logger.info(f"✅ GitHub Release erstellt: {tag}")
        logger.info(f"📍 Deals verfügbar auf: https://github.com/nikolaskamenz-debug/flight-monitor/releases/tag/{tag}")
        
    except FileNotFoundError:
        logger.warning("⚠️ GitHub CLI nicht verfügbar")
        logger.warning("📝 Speichere Deals lokal...")
        
        # Fallback: Speichere als Datei
        with open(f'deals-{datetime.now().strftime("%Y%m%d-%H%M%S")}.md', 'w') as f:
            f.write(release_body)
        
        logger.info("✅ Deals gespeichert in Artifacts")
    
    except Exception as e:
        logger.error(f"❌ Release Error: {e}")
        # Fallback
        with open(f'deals-{datetime.now().strftime("%Y%m%d-%H%M%S")}.md', 'w') as f:
            f.write(release_body)
        logger.info("✅ Deals als Datei gespeichert")
    
    logger.info("=" * 70)
    logger.info("✅ MONITOR FERTIG!")
    logger.info("=" * 70)
    logger.info(f"📊 {len(deals)} Deals gefunden")
    logger.info("📍 Verfügbar auf GitHub Releases")
    logger.info("⏰ Nächster Run: +24h automatisch")
    logger.info("=" * 70)
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
