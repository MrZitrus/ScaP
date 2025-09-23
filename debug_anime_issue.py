#!/usr/bin/env python3
"""
Debug-Script für Anime-Scraping-Probleme
Verwendung: python debug_anime_issue.py
"""

import requests
import sys
import logging
from bs4 import BeautifulSoup

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def test_direct_website_access():
    """Testet direkten Zugriff auf aniworld.to"""
    
    print("🌐 Teste direkten Website-Zugriff...")
    print("=" * 50)
    
    url = "https://aniworld.to/animes"
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'de,en-US;q=0.7,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        }
        
        print(f"📡 Lade: {url}")
        response = requests.get(url, headers=headers, timeout=10)
        
        print(f"Status: {response.status_code}")
        print(f"Content-Length: {len(response.text)}")
        print(f"Content-Type: {response.headers.get('content-type', 'unknown')}")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Suche nach verschiedenen Link-Typen
            selectors = [
                ('Anime-Stream-Links', 'a[href^="/anime/stream/"]'),
                ('Alle Anime-Links', 'a[href*="/anime/"]'),
                ('Serie-Links', 'a[href*="/serie/"]'),
                ('Alle Links', 'a[href]')
            ]
            
            for name, selector in selectors:
                links = soup.select(selector)
                print(f"{name}: {len(links)} gefunden")
                
                if links and len(links) <= 5:
                    for i, link in enumerate(links[:3]):
                        title = link.text.strip()[:50]
                        href = link.get('href', '')[:50]
                        print(f"  {i+1}. '{title}' -> '{href}'")
            
            # Suche nach typischen Anime-Inhalten
            anime_indicators = [
                'anime', 'manga', 'episode', 'staffel', 'stream'
            ]
            
            text_lower = response.text.lower()
            found_indicators = [word for word in anime_indicators if word in text_lower]
            print(f"Anime-Indikatoren gefunden: {found_indicators}")
            
            return True
        else:
            print(f"❌ HTTP-Fehler: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Verbindungsfehler: {e}")
        return False

def test_scraper_method():
    """Testet die Scraper-Methode direkt"""
    
    print("\n🔧 Teste Scraper-Methode...")
    print("=" * 50)
    
    try:
        from scraper import StreamScraper
        
        scraper = StreamScraper()
        print("✅ Scraper initialisiert")
        
        print("🎌 Rufe get_anime_list() auf...")
        anime_list = scraper.get_anime_list()
        
        print(f"Ergebnis: {len(anime_list)} Animes")
        
        if anime_list:
            print("\n📋 Erste 3 Animes:")
            for i, anime in enumerate(anime_list[:3]):
                print(f"  {i+1}. {anime.get('title', 'Kein Titel')}")
                print(f"     URL: {anime.get('url', 'Keine URL')}")
                print(f"     Typ: {anime.get('type', 'Kein Typ')}")
        
        return len(anime_list) > 0
        
    except Exception as e:
        print(f"❌ Scraper-Fehler: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_database_content():
    """Testet den Datenbankinhalt"""
    
    print("\n💾 Teste Datenbank-Inhalt...")
    print("=" * 50)
    
    try:
        from database import get_media_db
        from config_manager import get_config
        
        config = get_config()
        db_path = config.get('download.db_path', 'media.db')
        
        media_db = get_media_db(db_path)
        all_media = media_db.get_all_media()
        
        print(f"Gesamt in DB: {len(all_media)} Medien")
        
        anime_count = sum(1 for m in all_media if m.get('type') == 'anime')
        series_count = sum(1 for m in all_media if m.get('type') == 'series')
        
        print(f"Animes in DB: {anime_count}")
        print(f"Serien in DB: {series_count}")
        
        if anime_count > 0:
            print("\n📋 Erste 3 Animes aus DB:")
            anime_media = [m for m in all_media if m.get('type') == 'anime']
            for i, anime in enumerate(anime_media[:3]):
                print(f"  {i+1}. {anime.get('title', 'Kein Titel')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Datenbank-Fehler: {e}")
        return False

def main():
    print("🐛 Debug: Anime-Scraping-Problem")
    print("=" * 60)
    
    website_ok = test_direct_website_access()
    scraper_ok = test_scraper_method()
    db_ok = test_database_content()
    
    print("\n" + "=" * 60)
    print("📊 Diagnose-Zusammenfassung:")
    print(f"Website-Zugriff: {'✅ OK' if website_ok else '❌ PROBLEM'}")
    print(f"Scraper-Methode: {'✅ OK' if scraper_ok else '❌ PROBLEM'}")
    print(f"Datenbank: {'✅ OK' if db_ok else '❌ PROBLEM'}")
    
    if not website_ok:
        print("\n🚨 Website-Problem erkannt!")
        print("- aniworld.to ist nicht erreichbar oder blockiert")
        print("- Firewall/Proxy-Probleme möglich")
        print("- Website-Struktur könnte sich geändert haben")
    
    if not scraper_ok:
        print("\n🚨 Scraper-Problem erkannt!")
        print("- Parsing-Logik funktioniert nicht")
        print("- Selektoren müssen angepasst werden")
        print("- Siehe Debug-Ausgaben oben")
    
    if not db_ok:
        print("\n🚨 Datenbank-Problem erkannt!")
        print("- Datenbank-Zugriff fehlgeschlagen")
        print("- Möglicherweise Berechtigungsproblem")
    
    print(f"\n💡 Nächste Schritte:")
    if not scraper_ok:
        print("1. Führe aus: python test_anime_scraping.py")
        print("2. Prüfe die Debug-Ausgaben im Log")
        print("3. Teste mit: python test_api_scraping.py")
    
    sys.exit(0 if (website_ok and scraper_ok) else 1)

if __name__ == "__main__":
    main()