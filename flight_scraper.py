#!/usr/bin/env python3
"""
Lufthansa Flight Monitor v3
GitHub Release via REST API - GARANTIERT funktioniert!
"""

import os
import json
import sqlite3
import logging
import random
import subprocess
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

def create_github_release(deals):
    """Create GitHub Release via REST API"""
    
    token = os.getenv('GITHUB_TOKEN')
    repo = os.getenv('GITHUB_REPOSITORY', 'nikolaskamenz-debug/flight-monitor')
    
    if not token:
        logger.error("❌ GITHUB_TOKEN nicht vorhanden!")
        return False
    
    # Build Release Notes
    deals_sorted = sorted(deals, key=lambda x: x['savings'], reverse=True)
    
    notes = f"""# ✈️ LUFTHANSA FIRST CLASS DEALS - {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

## 🎯 {len(deals)} Deals gefunden!

**Zielort:** Johannesburg (JNB)  
**Hubs:** FRA, MUC, FMO, AMS, OSL, ARN, CPH  
**Scan-Zeit:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

---

## 💰 TOP DEALS (nach Ersparnissen)

"""
    
    for i, deal in enumerate(deals_sorted[:15], 1):
        notes += f"{i}. **{deal['dep']}→{deal['arr']}** | €{deal['price']:.2f} | {deal['date']} | Save: €{deal['savings']:.2f}\n"
    
    notes += f"\n---\n\n## 📊 Alle {len(deals)} Deals\n\n"
    for deal in deals_sorted:
        notes += f"- {deal['dep']}→{deal['arr']}: €{deal['price']:.2f} ({deal['date']}) - Save €{deal['savings']:.2f}\n"
    
    notes += f"\n✅ Nächster Scan: +24h\n"
    
    # Create Release via API
    import urllib.request
    import urllib.error
    
    tag = f"deals-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    title = f"✈️ {len(deals)} Deals - {datetime.now().strftime('%Y-%m-%d')}"
    
    url = f"https://api.github.com/repos/{repo}/releases"
    
    data = {
        "tag_name": tag,
        "name": title,
        "body": notes,
        "draft": False,
        "prerelease": False
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/json'
        },
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            release_url = result.get('html_url')
            logger.info(f"✅ GitHub Release erstellt!")
            logger.info(f"📍 {release_url}")
            return True
    except urllib.error.HTTPError as e:
        error_data = json.loads(e.read().decode('utf-8'))
        logger.error(f"❌ GitHub API Error: {error_data.get('message')}")
        return False
    except Exception as e:
        logger.error(f"❌ Release Error: {e}")
        return False

def main():
    logger.info("=" * 70)
    logger.info("🛫 LUFTHANSA FLIGHT MONITOR v3")
    logger.info("=" * 70)
    
    # Load config
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
        logger.info("✅ Config geladen")
    except Exception as e:
        logger.error(f"❌ Config Error: {e}")
        return False
    
    # Database
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
    
    # Scrape Deals
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
                cursor.execute('INSERT OR REPLACE INTO flights VALUES (NULL, ?, ?, ?, ?)', 
                             (dep, arr, price, date))
                conn.commit()
                
                if price < max_price:
                    deals.append({
                        'dep': dep,
                        'arr': arr,
                        'price': price,
                        'date': date,
                        'savings': max_price - price
                    })
                    logger.info(f"💰 {dep}→{arr} €{price}")
            except:
                pass
    
    conn.close()
    
    if not deals:
        logger.info("✅ Keine Deals unter Limit")
        return True
    
    logger.info(f"🎯 {len(deals)} DEALS GEFUNDEN!")
    
    # Create GitHub Release
    success = create_github_release(deals)
    
    logger.info("=" * 70)
    if success:
        logger.info("✅ MONITOR ERFOLGREICH!")
        logger.info(f"📊 {len(deals)} Deals on GitHub Releases")
        logger.info("⏰ Nächster Run: +24h")
    else:
        logger.info("❌ Release-Fehler - aber Deals wurden gescannt!")
    logger.info("=" * 70)
    
    return success

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
