#!/usr/bin/env python3
"""
Test-Script für Anime-Scraping
Verwendung: python test_anime_scraping.py
"""

import sys
import logging

# Setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def test_anime_scraping():
    """Testet das Anime-Scraping."""
    
    print("🔍 Teste Anime-Scraping...")
    print("=" * 50)
    
    try:
        from scraper import StreamScraper
        
        print("📡 Initialisiere Scraper...")
        scraper = StreamScraper()
        
        print("🎌 Lade Anime-Liste...")
        anime_list = scraper.get_anime_list()
        
        print(f"✅ Gefunden: {len(anime_list)} Animes")
        
        if anime_list:
            print("\n📋 Erste 5 Animes:")
            for i, anime in enumerate(anime_list[:5]):
                print(f"  {i+1}. {anime['title']}")
                print(f"     URL: {anime['url']}")
                if anime.get('alternative_titles'):
                    print(f"     Alt: {', '.join(anime['alternative_titles'])}")
                print()
        else:
            print("❌ Keine Animes gefunden!")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Fehler beim Anime-Scraping: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_series_scraping():
    """Testet das Serien-Scraping zum Vergleich."""
    
    print("\n🔍 Teste Serien-Scraping zum Vergleich...")
    print("=" * 50)
    
    try:
        from scraper import StreamScraper
        
        scraper = StreamScraper()
        
        print("📺 Lade Serien-Liste...")
        series_list = scraper.get_series_list()
        
        print(f"✅ Gefunden: {len(series_list)} Serien")
        
        if series_list:
            print("\n📋 Erste 3 Serien:")
            for i, series in enumerate(series_list[:3]):
                print(f"  {i+1}. {series['title']}")
                print(f"     URL: {series['url']}")
                print()
        
        return True
        
    except Exception as e:
        print(f"❌ Fehler beim Serien-Scraping: {e}")
        return False

def main():
    print("🧪 Stream Scraper - Anime/Serien Test")
    print("=" * 60)
    
    anime_success = test_anime_scraping()
    series_success = test_series_scraping()
    
    print("\n" + "=" * 60)
    print("📊 Zusammenfassung:")
    print(f"Anime-Scraping: {'✅ OK' if anime_success else '❌ FEHLER'}")
    print(f"Serien-Scraping: {'✅ OK' if series_success else '❌ FEHLER'}")
    
    if not anime_success:
        print("\n💡 Mögliche Ursachen für Anime-Probleme:")
        print("1. aniworld.to ist nicht erreichbar")
        print("2. Website-Struktur hat sich geändert")
        print("3. Netzwerk-/Firewall-Probleme")
        print("4. Rate-Limiting von der Website")
    
    sys.exit(0 if (anime_success and series_success) else 1)

if __name__ == "__main__":
    main()