#!/usr/bin/env python3
"""
Test-Script für FFmpeg-Erkennung
Verwendung: python test_ffmpeg.py
"""

import os
import sys
from pathlib import Path

def test_ffmpeg_detection():
    """Testet die FFmpeg-Erkennung."""
    
    print("🔍 Teste FFmpeg-Erkennung...")
    print("=" * 50)
    
    try:
        # Importiere die FFmpeg-Check-Funktion
        from scraper import _assert_ffmpeg, _resolve_ff_binary
        
        print("📍 Suche FFmpeg-Binaries...")
        ffmpeg_path = _resolve_ff_binary("ffmpeg")
        ffprobe_path = _resolve_ff_binary("ffprobe")
        
        print(f"FFmpeg gefunden: {ffmpeg_path}")
        print(f"FFprobe gefunden: {ffprobe_path}")
        
        print("\n🧪 Teste FFmpeg-Funktionalität...")
        _assert_ffmpeg()
        
        print("✅ FFmpeg-Check erfolgreich!")
        print(f"FFMPEG_PATH: {os.environ.get('FFMPEG_PATH')}")
        print(f"FFPROBE_PATH: {os.environ.get('FFPROBE_PATH')}")
        
        return True
        
    except Exception as e:
        print(f"❌ FFmpeg-Check fehlgeschlagen: {e}")
        
        print("\n🔧 Debugging-Informationen:")
        print(f"Python-Pfad: {sys.executable}")
        print(f"Arbeitsverzeichnis: {os.getcwd()}")
        print(f"PATH (erste 500 Zeichen): {os.environ.get('PATH', '')[:500]}...")
        
        # Teste manuelle Pfade
        print("\n🔍 Teste bekannte Windows-Pfade:")
        candidates = [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        ]
        
        for candidate in candidates:
            exists = Path(candidate).exists()
            print(f"  {candidate}: {'✅' if exists else '❌'}")
        
        return False

def main():
    success = test_ffmpeg_detection()
    
    if success:
        print("\n🎉 FFmpeg ist bereit für Language Guard!")
    else:
        print("\n💡 Lösungsvorschläge:")
        print("1. FFmpeg von https://ffmpeg.org/download.html herunterladen")
        print("2. Nach C:\\ffmpeg\\ entpacken")
        print("3. C:\\ffmpeg\\bin zum PATH hinzufügen")
        print("4. Neue CMD/PowerShell öffnen und nochmal testen")
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()