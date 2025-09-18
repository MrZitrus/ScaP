# Stream Scraper

Eine Flask-basierte Webanwendung zum Scrapen und Herunterladen von Serien und Animes.

## Kernkomponenten

- **app.py** - Hauptanwendung (Flask-Server mit WebSocket-Support)
- **scraper.py** - Scraping-Logik für verschiedene Streaming-Seiten
- **database.py** - Datenbankmanagement für Medien-Metadaten
- **config_manager.py** - Konfigurationsverwaltung
- **gemini_client.py** - KI-Integration für Metadaten-Verbesserung
- **websocket_server.py** - WebSocket-Server für Echtzeit-Updates

## Ordnerstruktur

- **scrapers/** - Spezielle Scraper-Module
- **static/** - CSS/JS-Dateien für das Frontend
- **templates/** - HTML-Templates
- **downloads/** - Download-Verzeichnis (wird automatisch erstellt)

## Installation & Start

### Voraussetzungen
- Python 3.8+
- FFmpeg (systemweit installiert, im PATH verfügbar)

### Installation
1. Abhängigkeiten installieren: `pip install -r requirements.txt`
2. FFmpeg installieren (falls nicht vorhanden)
3. FFmpeg-Erkennung testen: `python test_ffmpeg.py`
4. Konfiguration anpassen: `config.json`
5. Anwendung starten: `python app.py`

### FFmpeg Installation
- **Windows**: Download von https://ffmpeg.org/download.html, nach `C:\ffmpeg\` entpacken
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt install ffmpeg` (Ubuntu/Debian)

### Troubleshooting
- **FFmpeg nicht gefunden**: `python test_ffmpeg.py` ausführen für Diagnose
- **Language Guard Test**: `python test_language_guard.py pfad/zur/datei.mkv`

## Konfiguration

Die Anwendung wird über `config.json` konfiguriert. Wichtige Einstellungen:
- **Download-Verzeichnis**: Wo Dateien gespeichert werden
- **Server-Port und Host**: Webserver-Konfiguration
- **Language Guard**: Deutsche Audiospur-Erkennung
- **Gemini API**: KI-Metadaten-Verbesserung
- **Logging-Level**: Debug-Informationen

### Language Guard
Automatische Erkennung und Filterung deutscher Audiospuren:
- `require_dub: true` - Nur deutsche Synchronisation (Untertitel reichen nicht)
- `remux_to_de_if_present: true` - Automatisches Umschalten auf deutsche Audiospur
- `sample_seconds: 45` - Länge der Audio-Proben für Whisper-Erkennung