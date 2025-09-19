from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from datetime import datetime
import os
import json
import uuid
import sys
import subprocess
from collections import deque
from scraper import StreamScraper
from download_manager import DownloadManager
import logging
import threading
from config_manager import get_config
from database import get_media_db
from gemini_client import GeminiClient

# Get configuration
config = get_config()

# Konfiguriere Logging
logging_level = getattr(logging, config.get('logging.level', 'DEBUG'))
logging.basicConfig(
    level=logging_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///streams.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'streamscraper-secret-key'
db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Models
class Series(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    url = db.Column(db.String(500), unique=True, nullable=False)
    type = db.Column(db.String(50))  # 'anime' oder 'series'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    episodes = db.relationship('Episode', backref='series', lazy=True)

class Episode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    series_id = db.Column(db.Integer, db.ForeignKey('series.id'), nullable=False)
    season = db.Column(db.Integer)
    episode = db.Column(db.Integer)
    title = db.Column(db.String(200))
    status = db.Column(db.String(50))  # 'pending', 'downloading', 'completed', 'failed'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)



class DownloadJob(db.Model):
    __tablename__ = 'download_jobs'

    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    url = db.Column(db.String(500), nullable=False)
    title = db.Column(db.String(255))
    series_title = db.Column(db.String(255))
    status = db.Column(db.String(20), default='queued')
    job_type = db.Column(db.String(20), default='series')
    progress = db.Column(db.Float, default=0.0)
    bytes_downloaded = db.Column(db.BigInteger, default=0)
    bytes_total = db.Column(db.BigInteger, default=0)
    speed = db.Column(db.Float, default=0.0)
    eta = db.Column(db.Integer)
    current_episode_title = db.Column(db.String(255))
    language_tag = db.Column(db.String(32))
    result_path = db.Column(db.String(500))
    error_message = db.Column(db.Text)
    queue_position = db.Column(db.Integer, default=0)
    options = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)


class DownloadResult(db.Model):
    __tablename__ = 'download_results'

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey('download_jobs.id'), nullable=False)
    season_num = db.Column(db.Integer)
    episode_num = db.Column(db.Integer)
    title = db.Column(db.String(255))
    file_path = db.Column(db.String(500))
    file_size = db.Column(db.BigInteger)
    language_tag = db.Column(db.String(32))
    success = db.Column(db.Boolean, default=True)
    skipped = db.Column(db.Boolean, default=False)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    job = db.relationship('DownloadJob', backref=db.backref('results', lazy=True, cascade='all, delete-orphan'))
# Erstelle die Datenbank
with app.app_context():
    db.create_all()

# Initialisiere die Media-Datenbank
media_db = get_media_db(config.get('download.db_path', 'media.db'))

# Initialisiere den Scraper
scraper = StreamScraper(
    download_dir=config.get('download.directory'),
    max_parallel_downloads=config.get('download.max_parallel_downloads'),
    max_parallel_extractions=config.get('download.max_parallel_extractions'),
    socketio=socketio
)

download_manager = DownloadManager(app, scraper, socketio, db, DownloadJob, DownloadResult, logger)

# Initialisiere den Gemini Client, wenn aktiviert
gemini_enabled = config.get('gemini.enabled', False)
gemini_api_key = config.get('gemini.api_key', '')
gemini_model = config.get('gemini.model', 'gemini-1.5-pro-latest')
gemini_client = None

if gemini_enabled and gemini_api_key:
    gemini_client = GeminiClient(api_key=gemini_api_key, model=gemini_model)
    logger.info(f"Gemini API Client initialisiert mit Modell: {gemini_model}")
else:
    logger.info("Gemini API Client nicht aktiviert")

# Scanne das Download-Verzeichnis beim Start, wenn in der Konfiguration aktiviert
if config.get('download.scan_on_startup', False):
    download_dir = config.get('download.directory', 'downloads')
    logger.info(f"Scanne Download-Verzeichnis: {download_dir}")

    # Starte den Scan in einem separaten Thread, um den Serverstart nicht zu blockieren
    def scan_directory():
        try:
            media_count, season_count, episode_count = media_db.scan_directory(download_dir)
            logger.info(f"Scan abgeschlossen: {media_count} Serien/Animes, {season_count} Staffeln, {episode_count} Episoden gefunden")
        except Exception as e:
            logger.error(f"Fehler beim Scannen des Verzeichnisses: {str(e)}")

    scan_thread = threading.Thread(target=scan_directory)
    scan_thread.daemon = True
    scan_thread.start()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/gemini')
def gemini_settings():
    return render_template('gemini_settings.html',
                           gemini_enabled=config.get('gemini.enabled', False),
                           gemini_api_key=config.get('gemini.api_key', ''),
                           gemini_model=config.get('gemini.model', 'gemini-1.5-pro-latest'),
                           auto_enhance_metadata=config.get('gemini.auto_enhance_metadata', False))

@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    series_type = request.args.get('type', 'all')

    if not query:
        return jsonify([])

    # Suche in der Datenbank
    query = f"%{query}%"
    if series_type == 'all':
        results = Series.query.filter(Series.title.ilike(query)).all()
    else:
        results = Series.query.filter(Series.title.ilike(query), Series.type == series_type).all()

    return jsonify([{
        'id': s.id,
        'title': s.title,
        'url': s.url,
        'type': s.type
    } for s in results])


@app.route('/api/anime/details', methods=['POST'])
def get_anime_details():
    data = request.get_json(force=True) or {}
    url = data.get('url')
    if not url:
        return jsonify({'error': 'URL ist erforderlich'}), 400
    try:
        details = scraper.get_series_details(url)
        return jsonify({'status': 'success', 'data': details})
    except Exception as exc:
        logger.error(f"Fehler beim Laden der Serien-Details: {exc}", exc_info=True)
        return jsonify({'error': str(exc)}), 500


@app.route('/api/scrape/list', methods=['POST'])
def scrape_list():
    """Scrapt die Liste aller verfügbaren Serien/Animes"""
    series_type = request.json.get('type')
    logger.info(f"🔍 Starte Scraping für Typ: {series_type}")

    try:
        if series_type == 'anime':
            logger.info("🎌 Hole Anime-Liste von aniworld.to...")
            items = scraper.get_anime_list()
        else:
            logger.info("📺 Hole Serien-Liste von s.to...")
            items = scraper.get_series_list()

        logger.info(f"✅ Scraping abgeschlossen: {len(items)} {series_type}s gefunden")

        # Speichere in Datenbank
        for item in items:
            existing = Series.query.filter_by(url=item['url']).first()
            if not existing:
                series = Series(
                    title=item['title'],
                    url=item['url'],
                    type=item['type']
                )
                db.session.add(series)

        db.session.commit()
        logger.debug("Datenbank erfolgreich aktualisiert")
        
        # 👈 WICHTIG: Items zurückgeben!
        return jsonify({'status': 'success', 'count': len(items), 'items': items})

    except Exception as e:
        logger.error(f"Fehler beim Scraping: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/download', methods=['POST'])
def start_download():
    """Fügt einen neuen Download-Auftrag zur Warteschlange hinzu."""
    data = request.get_json(force=True)
    url = (data or {}).get('url')
    if not url:
        return jsonify({'error': 'URL ist erforderlich'}), 400
    series_title = (data or {}).get('title') or url
    options = (data or {}).get('options') or {}
    if 'selectedEpisodes' in data:
        options['selected_episodes'] = data.get('selectedEpisodes')
    if 'seriesTitle' in data:
        options['series_title'] = data.get('seriesTitle')
    job = download_manager.enqueue_job(url, series_title, options=options)
    return jsonify({'status': 'queued', 'job': download_manager.serialize_job(job)})

@app.route('/download', methods=['POST'])
def download():
    """Legacy Endpoint: enqueued download job based on URL payload."""
    try:
        data = request.get_json(force=True)
        url = (data or {}).get('url')
        logger.debug(f"Download-Anfrage erhalten für URL: {url}")
        if not url:
            logger.error('Keine URL in der Anfrage gefunden')
            return jsonify({'error': 'URL fehlt'}), 400
        options = (data or {}).get('options') or {}
        if data and 'selectedEpisodes' in data:
            options['selected_episodes'] = data.get('selectedEpisodes')
        if data and 'seriesTitle' in data:
            options['series_title'] = data.get('seriesTitle')
        job = download_manager.enqueue_job(url, data.get('title') or url, options=options)
        return jsonify({'status': 'queued', 'job': download_manager.serialize_job(job)})
    except Exception as e:
        logger.error(f"Kritischer Fehler beim Download: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/download/status')
def download_status():
    """Hole den aktuellen Download-Status inklusive Warteschlange und Historie."""
    snapshot = download_manager.get_status_snapshot()
    return jsonify(snapshot)

@app.route('/api/media/list')
def get_media_list():
    """Hole die Liste aller Serien und Animes aus der Datenbank."""
    try:
        media_list = media_db.get_all_media()
        return jsonify({
            'status': 'success',
            'count': len(media_list),
            'media': media_list
        })
    except Exception as e:
        logger.error(f"Fehler beim Abrufen der Medienliste: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/media/details/<int:media_id>')
def get_media_details(media_id):
    """Hole detaillierte Informationen zu einer Serie/einem Anime."""
    try:
        # Hole die Serie/den Anime
        media = None
        for m in media_db.get_all_media():
            if m['id'] == media_id:
                media = m
                break

        if not media:
            return jsonify({
                'status': 'error',
                'error': 'Media not found'
            }), 404

        # Hole die Staffeln
        seasons = media_db.get_seasons_by_media_id(media_id)

        # Hole die Episoden für jede Staffel
        for season in seasons:
            season['episodes'] = media_db.get_episodes_by_season_id(season['id'])

        return jsonify({
            'status': 'success',
            'media': media,
            'seasons': seasons
        })
    except Exception as e:
        logger.error(f"Fehler beim Abrufen der Mediendetails: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/media/stats')
def get_media_stats():
    """Hole Statistiken über die Mediendatenbank."""
    try:
        media_list = media_db.get_all_media()
        episode_count = media_db.get_episode_count()
        total_size = media_db.get_total_size()

        # Berechne die Anzahl der Serien und Animes
        series_count = sum(1 for media in media_list if media['type'] == 'series')
        anime_count = sum(1 for media in media_list if media['type'] == 'anime')

        return jsonify({
            'status': 'success',
            'stats': {
                'total_media': len(media_list),
                'series_count': series_count,
                'anime_count': anime_count,
                'episode_count': episode_count,
                'total_size_bytes': total_size,
                'total_size_mb': round(total_size / (1024 * 1024), 2),
                'total_size_gb': round(total_size / (1024 * 1024 * 1024), 2)
            }
        })
    except Exception as e:
        logger.error(f"Fehler beim Abrufen der Medienstatistiken: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/reset', methods=['POST'])
def reset_session():
    """Reset die Session für neue Downloads."""
    try:
        if scraper.reset_session():
            return jsonify({'message': 'Session zurückgesetzt'}), 200
        else:
            return jsonify({'error': 'Fehler beim Zurücksetzen der Session'}), 500
    except Exception as e:
        logger.error(f"Fehler beim Session-Reset: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/cancel', methods=['POST'])
def cancel_download():
    """Bricht einen Download-Auftrag ab."""
    payload = request.get_json(silent=True) or {}
    job_id = payload.get('job_id')
    if not job_id:
        job_id = download_manager.current_job_id
        if not job_id:
            return jsonify({'error': 'Kein aktiver Download'}), 400
    success, message = download_manager.cancel_job(job_id)
    status_code = 200 if success else 400
    return jsonify({'success': success, 'message': message}), status_code



@app.route('/api/download/queue/pause', methods=['POST'])
def pause_queue():
    payload = request.get_json(silent=True) or {}
    paused = payload.get('paused', True)
    state = download_manager.toggle_queue_pause(bool(paused))
    return jsonify({'paused': state})

@app.route('/api/download/jobs', methods=['GET'])
def list_download_jobs():
    filter_param = request.args.get('filter', 'all')
    limit = int(request.args.get('limit', 20))
    snapshot = download_manager.get_status_snapshot()
    response = {
        'active': snapshot.get('active'),
        'queue': snapshot.get('queue'),
        'history': snapshot.get('history'),
    }
    if filter_param == 'queue':
        response.pop('history', None)
    elif filter_param == 'history':
        response.pop('queue', None)
    if limit and response.get('history'):
        response['history'] = response['history'][:limit]
    return jsonify(response)


@app.route('/api/download/jobs/<int:job_id>/pause', methods=['POST'])
def pause_job(job_id):
    success, message = download_manager.pause_job(job_id)
    return jsonify({'success': success, 'message': message}), 200 if success else 400


@app.route('/api/download/jobs/<int:job_id>/resume', methods=['POST'])
def resume_job(job_id):
    success, message = download_manager.resume_job(job_id)
    return jsonify({'success': success, 'message': message}), 200 if success else 400


@app.route('/api/download/jobs/<int:job_id>/cancel', methods=['POST'])
def cancel_job(job_id):
    success, message = download_manager.cancel_job(job_id)
    return jsonify({'success': success, 'message': message}), 200 if success else 400


@app.route('/api/download/results/<int:result_id>/open', methods=['POST'])
def open_downloaded_file(result_id):
    result = DownloadResult.query.get(result_id)
    if not result or not result.file_path:
        return jsonify({'error': 'Download nicht gefunden'}), 404
    file_path = result.file_path
    if not os.path.exists(file_path):
        return jsonify({'error': 'Datei nicht vorhanden'}), 404
    try:
        if os.name == 'nt':
            os.startfile(file_path)  # type: ignore[attr-defined]
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', file_path])
        else:
            subprocess.Popen(['xdg-open', file_path])
        return jsonify({'success': True})
    except Exception as exc:
        logger.error(f"Fehler beim Öffnen der Datei: {exc}", exc_info=True)
        return jsonify({'error': str(exc)}), 500


@app.route('/api/download/results/<int:result_id>', methods=['DELETE'])
def delete_downloaded_file(result_id):
    result = DownloadResult.query.get(result_id)
    if not result:
        return jsonify({'error': 'Download nicht gefunden'}), 404
    file_path = result.file_path
    removed = False
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
            removed = True
        except Exception as exc:
            logger.error(f"Fehler beim Löschen der Datei: {exc}", exc_info=True)
            return jsonify({'error': str(exc)}), 500
    with app.app_context():
        DownloadResult.query.filter_by(id=result_id).delete()
        db.session.commit()
    return jsonify({'success': True, 'removed_file': removed})

@app.route('/api/settings/download-dir', methods=['GET', 'POST'])
def manage_download_dir():
    """Verwaltet das Download-Verzeichnis."""
    if request.method == 'GET':
        # Gib das aktuelle Download-Verzeichnis zurück
        return jsonify({
            'status': 'success',
            'download_dir': config.get('download.directory', 'downloads')
        })
    elif request.method == 'POST':
        try:
            data = request.json

            # Wenn nur ein Scan angefordert wird
            if data and data.get('scan_only', False):
                download_dir = config.get('download.directory', 'downloads')

                def scan_directory_thread():
                    try:
                        media_count, season_count, episode_count = media_db.scan_directory(download_dir)
                        logger.info(f"Scan abgeschlossen: {media_count} Serien/Animes, {season_count} Staffeln, {episode_count} Episoden gefunden")
                    except Exception as e:
                        logger.error(f"Fehler beim Scannen des Verzeichnisses: {str(e)}")

                scan_thread = threading.Thread(target=scan_directory_thread)
                scan_thread.daemon = True
                scan_thread.start()

                return jsonify({
                    'status': 'success',
                    'message': f"Scan des Verzeichnisses {download_dir} gestartet"
                })

            # Wenn ein neues Verzeichnis angegeben wird
            if not data or 'download_dir' not in data:
                return jsonify({
                    'status': 'error',
                    'error': 'Download-Verzeichnis nicht angegeben'
                }), 400

            new_dir = data['download_dir']

            # Prüfe, ob das Verzeichnis existiert
            if not os.path.exists(new_dir):
                try:
                    # Versuche, das Verzeichnis zu erstellen
                    os.makedirs(new_dir, exist_ok=True)
                    logger.info(f"Verzeichnis erstellt: {new_dir}")
                except Exception as e:
                    return jsonify({
                        'status': 'error',
                        'error': f"Fehler beim Erstellen des Verzeichnisses: {str(e)}"
                    }), 500

            # Aktualisiere die Konfiguration
            config.config['download']['directory'] = new_dir

            # Speichere die Konfiguration
            with open('config.json', 'w') as f:
                json.dump(config.config, f, indent=4)

            # Aktualisiere den Scraper
            scraper.download_dir = new_dir

            # Scanne das neue Verzeichnis
            def scan_new_directory():
                try:
                    media_count, season_count, episode_count = media_db.scan_directory(new_dir)
                    logger.info(f"Scan abgeschlossen: {media_count} Serien/Animes, {season_count} Staffeln, {episode_count} Episoden gefunden")
                except Exception as e:
                    logger.error(f"Fehler beim Scannen des Verzeichnisses: {str(e)}")

            scan_thread = threading.Thread(target=scan_new_directory)
            scan_thread.daemon = True
            scan_thread.start()

            return jsonify({
                'status': 'success',
                'message': f"Download-Verzeichnis auf {new_dir} geändert",
                'download_dir': new_dir
            })
        except Exception as e:
            logger.error(f"Fehler beim Ändern des Download-Verzeichnisses: {str(e)}")
            return jsonify({
                'status': 'error',
                'error': str(e)
            }), 500

@app.route('/api/settings/gemini', methods=['GET', 'POST'])
def manage_gemini_settings():
    """Verwaltet die Gemini API-Einstellungen."""
    if request.method == 'GET':
        # Gib die aktuellen Gemini-Einstellungen zurück
        return jsonify({
            'status': 'success',
            'enabled': config.get('gemini.enabled', False),
            'api_key': config.get('gemini.api_key', ''),
            'model': config.get('gemini.model', 'gemini-1.5-pro-latest'),
            'auto_enhance_metadata': config.get('gemini.auto_enhance_metadata', False)
        })
    elif request.method == 'POST':
        try:
            data = request.json

            if not data:
                return jsonify({
                    'status': 'error',
                    'error': 'Keine Daten angegeben'
                }), 400

            # Aktualisiere die Konfiguration
            if 'gemini' not in config.config:
                config.config['gemini'] = {}

            config.config['gemini']['enabled'] = data.get('enabled', False)
            config.config['gemini']['api_key'] = data.get('api_key', '')
            config.config['gemini']['model'] = data.get('model', 'gemini-1.5-pro-latest')
            config.config['gemini']['auto_enhance_metadata'] = data.get('auto_enhance_metadata', False)

            # Speichere die Konfiguration
            with open('config.json', 'w') as f:
                json.dump(config.config, f, indent=4)

            # Aktualisiere den Gemini-Client
            global gemini_client
            if config.config['gemini']['enabled'] and config.config['gemini']['api_key']:
                gemini_client = GeminiClient(
                    api_key=config.config['gemini']['api_key'],
                    model=config.config['gemini']['model']
                )
                logger.info(f"Gemini API Client aktualisiert mit Modell: {config.config['gemini']['model']}")
            else:
                gemini_client = None
                logger.info("Gemini API Client deaktiviert")

            return jsonify({
                'status': 'success',
                'message': 'Gemini-Einstellungen erfolgreich aktualisiert'
            })
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren der Gemini-Einstellungen: {str(e)}")
            return jsonify({
                'status': 'error',
                'error': str(e)
            }), 500

@app.route('/api/media/clear', methods=['POST'])
def clear_media_database():
    """Löscht alle Einträge aus der Mediendatenbank."""
    try:
        # Lösche die Datenbank-Datei und erstelle eine neue
        db_path = config.get('download.db_path', 'media.db')
        if os.path.exists(db_path):
            os.remove(db_path)

        # Initialisiere die Datenbank neu
        global media_db
        media_db = get_media_db(db_path)

        return jsonify({
            'status': 'success',
            'message': 'Datenbank erfolgreich zurückgesetzt'
        })
    except Exception as e:
        logger.error(f"Fehler beim Zurücksetzen der Datenbank: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

# Cleanup duplicates functionality removed - was a one-time helper script

@app.route('/api/media/enhance/<int:media_id>', methods=['POST'])
def enhance_media_metadata(media_id):
    """Verbessert die Metadaten einer Serie/eines Animes mit Hilfe der Gemini API."""
    if not gemini_client:
        return jsonify({
            'status': 'error',
            'error': 'Gemini API ist nicht aktiviert'
        }), 400

    try:
        # Hole die Medieninformationen aus der Datenbank
        media = media_db.get_media_by_id(media_id)
        if not media:
            return jsonify({
                'status': 'error',
                'error': f'Keine Medien mit ID {media_id} gefunden'
            }), 404

        # Starte die Metadaten-Verbesserung in einem separaten Thread
        def enhance_metadata_thread():
            try:
                # Verbessere die Metadaten mit Gemini
                enhanced_metadata = gemini_client.enhance_series_metadata(media['title'])

                # Aktualisiere die Datenbank
                if enhanced_metadata:
                    success = media_db.update_media_metadata(media_id, enhanced_metadata)
                    if success:
                        logger.info(f"Metadaten für '{media['title']}' erfolgreich verbessert")
                    else:
                        logger.error(f"Fehler beim Aktualisieren der Metadaten für '{media['title']}'")
            except Exception as e:
                logger.error(f"Fehler bei der Metadaten-Verbesserung: {str(e)}")

        # Starte den Thread
        thread = threading.Thread(target=enhance_metadata_thread)
        thread.daemon = True
        thread.start()

        return jsonify({
            'status': 'success',
            'message': f"Metadaten-Verbesserung für '{media['title']}' gestartet"
        })
    except Exception as e:
        logger.error(f"Fehler beim Starten der Metadaten-Verbesserung: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/episode/enhance/<int:episode_id>', methods=['POST'])
def enhance_episode_metadata(episode_id):
    """Verbessert die Metadaten einer Episode mit Hilfe der Gemini API."""
    if not gemini_client:
        return jsonify({
            'status': 'error',
            'error': 'Gemini API ist nicht aktiviert'
        }), 400

    try:
        # Hole die Episodeninformationen aus der Datenbank
        episode = media_db.get_episode_by_id(episode_id)
        if not episode:
            return jsonify({
                'status': 'error',
                'error': f'Keine Episode mit ID {episode_id} gefunden'
            }), 404

        # Hole die Staffelinformationen
        season = media_db.get_season_by_id(episode['season_id'])
        if not season:
            return jsonify({
                'status': 'error',
                'error': f'Keine Staffel mit ID {episode["season_id"]} gefunden'
            }), 404

        # Hole die Medieninformationen
        media = media_db.get_media_by_id(season['media_id'])
        if not media:
            return jsonify({
                'status': 'error',
                'error': f'Keine Medien mit ID {season["media_id"]} gefunden'
            }), 404

        # Starte die Metadaten-Verbesserung in einem separaten Thread
        def enhance_episode_thread():
            try:
                # Verbessere die Metadaten mit Gemini
                enhanced_metadata = gemini_client.analyze_episode_content(
                    series_title=media['title'],
                    episode_title=episode['title'] or f"Episode {episode['episode_number']}",
                    season_num=season['season_number'],
                    episode_num=episode['episode_number']
                )

                # Aktualisiere die Datenbank
                if enhanced_metadata:
                    success = media_db.update_episode_metadata(episode_id, enhanced_metadata)
                    if success:
                        logger.info(f"Metadaten für Episode {season['season_number']}x{episode['episode_number']} erfolgreich verbessert")
                    else:
                        logger.error(f"Fehler beim Aktualisieren der Metadaten für Episode {season['season_number']}x{episode['episode_number']}")
            except Exception as e:
                logger.error(f"Fehler bei der Episoden-Metadaten-Verbesserung: {str(e)}")

        # Starte den Thread
        thread = threading.Thread(target=enhance_episode_thread)
        thread.daemon = True
        thread.start()

        return jsonify({
            'status': 'success',
            'message': f"Metadaten-Verbesserung für Episode {season['season_number']}x{episode['episode_number']} gestartet"
        })
    except Exception as e:
        logger.error(f"Fehler beim Starten der Episoden-Metadaten-Verbesserung: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/media/enhance/all', methods=['POST'])
def enhance_all_media_metadata():
    """Verbessert die Metadaten aller Serien/Animes mit Hilfe der Gemini API."""
    if not gemini_client:
        return jsonify({
            'status': 'error',
            'error': 'Gemini API ist nicht aktiviert'
        }), 400

    try:
        # Hole alle Medien aus der Datenbank
        all_media = media_db.get_all_media()

        # Filtere Medien, die noch nicht verbessert wurden
        media_to_enhance = [m for m in all_media if not m.get('ai_enhanced')]

        if not media_to_enhance:
            return jsonify({
                'status': 'success',
                'message': 'Alle Medien wurden bereits verbessert'
            })

        # Starte die Metadaten-Verbesserung in einem separaten Thread
        def enhance_all_thread():
            try:
                for media in media_to_enhance:
                    try:
                        # Verbessere die Metadaten mit Gemini
                        enhanced_metadata = gemini_client.enhance_series_metadata(media['title'])

                        # Aktualisiere die Datenbank
                        if enhanced_metadata:
                            success = media_db.update_media_metadata(media['id'], enhanced_metadata)
                            if success:
                                logger.info(f"Metadaten für '{media['title']}' erfolgreich verbessert")
                            else:
                                logger.error(f"Fehler beim Aktualisieren der Metadaten für '{media['title']}'")

                        # Kurze Pause, um die API nicht zu überlasten
                        import time
                        time.sleep(1)
                    except Exception as e:
                        logger.error(f"Fehler bei der Metadaten-Verbesserung für '{media['title']}': {str(e)}")
                        continue

                logger.info(f"Metadaten-Verbesserung für {len(media_to_enhance)} Medien abgeschlossen")
            except Exception as e:
                logger.error(f"Fehler bei der Metadaten-Verbesserung: {str(e)}")

        # Starte den Thread
        thread = threading.Thread(target=enhance_all_thread)
        thread.daemon = True
        thread.start()

        return jsonify({
            'status': 'success',
            'message': f"Metadaten-Verbesserung für {len(media_to_enhance)} Medien gestartet"
        })
    except Exception as e:
        logger.error(f"Fehler beim Starten der Metadaten-Verbesserung: {str(e)}")
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/settings/language', methods=['GET', 'POST'])
def manage_language_settings():
    """Verwaltet die Language Guard Einstellungen."""
    if request.method == 'GET':
        # Gib die aktuellen Language Guard Einstellungen zurück
        return jsonify({
            'status': 'success',
            'prefer': config.get('language.prefer', ['de', 'deu', 'ger']),
            'require_dub': config.get('language.require_dub', True),
            'sample_seconds': config.get('language.sample_seconds', 45),
            'remux_to_de_if_present': config.get('language.remux_to_de_if_present', True),
            'accept_on_error': config.get('language.accept_on_error', False),
            'verify_with_whisper': config.get('language.verify_with_whisper', True)
        })
    elif request.method == 'POST':
        try:
            data = request.json

            if not data:
                return jsonify({
                    'status': 'error',
                    'error': 'Keine Daten angegeben'
                }), 400

            # Aktualisiere die Konfiguration
            if 'language' not in config.config:
                config.config['language'] = {}

            config.config['language']['prefer'] = data.get('prefer', ['de', 'deu', 'ger'])
            config.config['language']['require_dub'] = data.get('require_dub', True)
            config.config['language']['sample_seconds'] = data.get('sample_seconds', 45)
            config.config['language']['remux_to_de_if_present'] = data.get('remux_to_de_if_present', True)
            config.config['language']['accept_on_error'] = data.get('accept_on_error', False)
            config.config['language']['verify_with_whisper'] = data.get('verify_with_whisper', True)

            # Speichere die Konfiguration
            with open('config.json', 'w') as f:
                json.dump(config.config, f, indent=4)

            return jsonify({
                'status': 'success',
                'message': 'Language Guard Einstellungen erfolgreich aktualisiert'
            })
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren der Language Guard Einstellungen: {str(e)}")
            return jsonify({
                'status': 'error',
                'error': str(e)
            }), 500

@app.route('/download_voe', methods=['POST'])
def download_voe():
    """Endpoint für direkten VOE.sx Download"""
    try:
        data = request.get_json()
        voe_url = data.get('url')
        filename = data.get('filename')

        if not voe_url:
            return jsonify({'error': 'VOE.sx URL ist erforderlich'}), 400

        # Starte den Download in einem separaten Thread
        def download_thread():
            try:
                scraper.download_direct_voe(voe_url, filename)
            except Exception as e:
                logger.error(f"Fehler beim VOE.sx Download: {str(e)}")

        thread = threading.Thread(target=download_thread)
        thread.start()

        return jsonify({'message': 'Download gestartet'})

    except Exception as e:
        logger.error(f"Fehler beim Verarbeiten der VOE.sx Download-Anfrage: {str(e)}")
        return jsonify({'error': str(e)}), 500

# WebSocket Routes
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info(f"Client connected: {request.sid}")
    # Send current status on connect
    status = scraper.download_status.get_status()
    socketio.emit('status_update', status, room=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info(f"Client disconnected: {request.sid}")

if __name__ == '__main__':
    port = config.get('server.port')
    debug = config.get('server.debug')
    host = config.get('server.host')

    logger.info(f"Starting server on {host}:{port} (debug={debug})")
    socketio.run(app, debug=debug, port=port, host=host)
