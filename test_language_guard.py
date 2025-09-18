#!/usr/bin/env python3
"""
Test-Script für Language Guard Funktionalität
Verwendung: python test_language_guard.py <video_file_path>
"""

import sys
import os
from pathlib import Path

def test_language_guard(video_path: str):
    """Testet die Language Guard Funktionalität mit einer Video-Datei."""
    
    if not os.path.exists(video_path):
        print(f"❌ Datei nicht gefunden: {video_path}")
        return False
    
    try:
        from language_guard import verify_language
        
        print(f"🔍 Prüfe Datei: {video_path}")
        print("=" * 50)
        
        # Test mit verschiedenen Konfigurationen
        print("🔧 Test 1: Standard-Konfiguration")
        ok, detail, fixed_path = verify_language(video_path)
        
        print(f"Ergebnis: {'✅ AKZEPTIERT' if ok else '❌ ABGELEHNT'}")
        print(f"Details: {detail}")
        if fixed_path:
            print(f"Remuxte Datei: {fixed_path}")
        
        print("\n🔧 Test 2: Nur Untertitel erlaubt")
        ok2, detail2, fixed_path2 = verify_language(
            video_path, 
            require_dub=False,
            sample_seconds=30
        )
        
        print(f"Ergebnis: {'✅ AKZEPTIERT' if ok2 else '❌ ABGELEHNT'}")
        print(f"Details: {detail2}")
        if fixed_path2:
            print(f"Remuxte Datei: {fixed_path2}")
        
        return ok or ok2
        
    except ImportError as e:
        print(f"❌ Language Guard nicht verfügbar: {e}")
        print("Installiere Dependencies: pip install faster-whisper openai-whisper")
        return False
    except Exception as e:
        print(f"❌ Fehler bei der Prüfung: {e}")
        return False

def main():
    if len(sys.argv) != 2:
        print("Verwendung: python test_language_guard.py <video_file_path>")
        print("Beispiel: python test_language_guard.py downloads/Serie/S01E01.mkv")
        sys.exit(1)
    
    video_path = sys.argv[1]
    success = test_language_guard(video_path)
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()