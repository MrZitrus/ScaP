#!/usr/bin/env python3
"""
Script um die Anime-Datenbank zu aktualisieren
"""

import requests
import json

def update_anime_database():
    """Ruft den Scrape-Endpunkt auf, um Animes in die DB zu laden"""
    
    url = "http://localhost:5000/api/scrape/list"
    
    payload = {
        "type": "anime"
    }
    
    print("🎌 Starte Anime-Scraping und DB-Update...")
    
    try:
        response = requests.post(url, json=payload, timeout=60)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Erfolgreich! {data.get('count', 0)} Animes in DB gespeichert")
            print(f"Status: {data.get('status')}")
            
            # Zeige erste paar Animes
            items = data.get('items', [])
            if items:
                print("\n📋 Erste 5 Animes:")
                for i, anime in enumerate(items[:5]):
                    print(f"  {i+1}. {anime.get('title')}")
        else:
            print(f"❌ Fehler: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            
    except Exception as e:
        print(f"❌ Verbindungsfehler: {e}")
        print("Stelle sicher, dass der Server läuft: python app.py")

if __name__ == "__main__":
    update_anime_database()