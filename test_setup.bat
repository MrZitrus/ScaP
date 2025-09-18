@echo off
echo ========================================
echo Stream Scraper Setup Test
echo ========================================
echo.

echo 1. Teste Python...
python --version
if %errorlevel% neq 0 (
    echo FEHLER: Python nicht gefunden!
    pause
    exit /b 1
)
echo ✓ Python OK
echo.

echo 2. Teste FFmpeg-Erkennung...
python test_ffmpeg.py
if %errorlevel% neq 0 (
    echo FEHLER: FFmpeg-Setup fehlgeschlagen!
    echo.
    echo Lösungsvorschläge:
    echo - FFmpeg von https://ffmpeg.org/download.html herunterladen
    echo - Nach C:\ffmpeg\ entpacken
    echo - C:\ffmpeg\bin zum PATH hinzufügen
    echo - Neue CMD öffnen und nochmal testen
    pause
    exit /b 1
)
echo ✓ FFmpeg OK
echo.

echo 3. Teste Dependencies...
python -c "import flask, requests, bs4, yt_dlp; print('✓ Core Dependencies OK')"
if %errorlevel% neq 0 (
    echo FEHLER: Dependencies fehlen!
    echo Führe aus: pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo ========================================
echo ✅ Setup komplett! Du kannst starten:
echo    python app.py
echo ========================================
pause