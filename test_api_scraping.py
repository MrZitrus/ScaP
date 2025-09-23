#!/usr/bin/env python3
"""
Test-Script für API-Scraping
Verwendung: python test_api_scraping.py
"""

import requests
import json
import sys

def test_api_scraping():
    """Testet die API-Scraping-Endpunkte."""
    
    print("🔍 Teste API-Scraping...")
    print("=" * 50)
    
    base_url = "http://127.0.0.1:5000"
    
    # Teste Anime-Scraping
    print("🎌 Teste Anime-Scraping via API...")
    try:
        response = requests.post(f"{base_url}/api/scrape/list", 
                               json={"type": "anime"}, 
                               timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Anime-API erfolgreich: {data.get('count', 0)} Animes gefunden")
        else:
            print(f"❌ Anime-API Fehler: {response.status_code}")
            print(f"Response: {response.text}")
            
    except requests.exceptions.ConnectionError:
        print("❌ Verbindung fehlgeschlagen - ist der Server gestartet?")
        print("Starte zuerst: python app.py")
        return False
    except Exception as e:
        print(f"❌ Anime-API Fehler: {e}")
    
    # Teste Serien-Scraping
    print("\n📺 Teste Serien-Scraping via API...")
    try:
        response = requests.post(f"{base_url}/api/scrape/list", 
                               json={"type": "series"}, 
                               timeout=30)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Serien-API erfolgreich: {data.get('count', 0)} Serien gefunden")
        else:
            print(f"❌ Serien-API Fehler: {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ Serien-API Fehler: {e}")
    
    # Teste Suche
    print("\n🔍 Teste Suche...")
    try:
        response = requests.get(f"{base_url}/search?q=naruto&type=anime")
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Suche erfolgreich: {len(data)} Ergebnisse für 'naruto'")
            if data:
                print(f"Erstes Ergebnis: {data[0].get('title', 'Unbekannt')}")
        else:
            print(f"❌ Suche Fehler: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Suche Fehler: {e}")
    
    return True

def main():
    print("🧪 Stream Scraper - API Test")
    print("=" * 40)
    
    success = test_api_scraping()
    
    if not success:
        print("\n💡 Troubleshooting:")
        print("1. Starte den Server: python app.py")
        print("2. Warte bis 'Running on http://127.0.0.1:5000' erscheint")
        print("3. Führe diesen Test erneut aus")
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()