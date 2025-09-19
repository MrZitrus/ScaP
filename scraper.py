import os
import subprocess
import logging
import time
import threading
import queue
import re
import json
import requests
import base64
import random
import concurrent.futures
import shutil
from urllib.parse import urlparse, urljoin
from datetime import datetime
from pathlib import Path
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any, Set
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled
from scrapers.voe_fallback import VoeFallbackDownloader
from database import get_media_db, MediaDatabase

def _resolve_ff_binary(name: str) -> str | None:
    # 1) env override
    env = os.environ.get(f"{name.upper()}_PATH")
    if env and Path(env).exists():
        return env

    # 2) PATH
    which = shutil.which(name)
    if which:
        return which

    # 3) typische Windows-Installationspfade
    candidates = [
        fr"C:\ffmpeg\bin\{name}.exe",
        fr"C:\ffmpeg\{name}.exe",
        fr"C:\Program Files\ffmpeg\bin\{name}.exe",
        fr"C:\Program Files (x86)\ffmpeg\bin\{name}.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None

def _assert_ffmpeg():
    """Prüft, ob FFmpeg und FFprobe verfügbar sind (robuste Windows-Version)."""
    ffmpeg = _resolve_ff_binary("ffmpeg")
    ffprobe = _resolve_ff_binary("ffprobe")

    print(f"[DEBUG] Gefunden: ffmpeg={ffmpeg}, ffprobe={ffprobe}")
    print(f"[DEBUG] PATH-Auszug: {os.environ.get('PATH','')[:200]}...")

    if not ffmpeg:
        raise RuntimeError("ffmpeg nicht gefunden. Bitte C:\\ffmpeg\\bin in PATH eintragen oder FFMPEG_PATH setzen.")
    if not ffprobe:
        raise RuntimeError("ffprobe nicht gefunden. Bitte C:\\ffmpeg\\bin in PATH eintragen oder FFPROBE_PATH setzen.")

    # Test ob die Binaries funktionieren
    for binary in (ffmpeg, ffprobe):
        try:
            subprocess.run([binary, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        except Exception as e:
            raise RuntimeError(f"{binary} gefunden aber nicht ausführbar: {e}")

    # Setze Umgebungsvariablen für spätere Verwendung
    os.environ["FFMPEG_PATH"] = ffmpeg
    os.environ["FFPROBE_PATH"] = ffprobe

class DownloadStatus:
    '''Thread-safe state tracker for long running downloads.'''

    def __init__(self):
        self.is_downloading = False
        self.current_title = ""
        self.current_episode_title = ""
        self.progress = 0.0
        self.total_episodes = 0
        self.current_episode = 0
        self.status_message = ""
        self.state = "idle"
        self.bytes_downloaded = 0
        self.bytes_total = 0
        self.speed = 0.0
        self.eta = None
        self.job_id = None
        self.cancel_requested = False
        self.pause_requested = False
        self._lock = threading.Lock()
        self._listener = None
        self._pause_event = threading.Event()
        self._pause_event.set()

    def set_listener(self, listener):
        '''Register a callback that receives status dictionaries.'''
        with self._lock:
            self._listener = listener

    def set_job(self, job_id):
        with self._lock:
            self.job_id = job_id
            self._notify()

    def update(self, *, title="", progress=None, current_episode=None, total_episodes=None,
               status_message="", bytes_downloaded=None, bytes_total=None, speed=None,
               eta=None, state=None, episode_title=""):
        with self._lock:
            if title:
                self.current_title = title
            if episode_title:
                self.current_episode_title = episode_title
            if progress is not None:
                try:
                    self.progress = float(progress)
                except (TypeError, ValueError):
                    logging.debug("Invalid progress value: %s", progress)
            if current_episode is not None:
                self.current_episode = current_episode
            if total_episodes is not None:
                self.total_episodes = total_episodes
            if status_message:
                self.status_message = status_message
            if bytes_downloaded is not None:
                self.bytes_downloaded = max(0, int(bytes_downloaded))
            if bytes_total is not None:
                self.bytes_total = max(0, int(bytes_total))
            if speed is not None:
                self.speed = float(speed) if speed else 0.0
            if eta is not None:
                self.eta = int(eta) if eta else None
            if state:
                self.state = state
        self._notify()

    def start_download(self):
        with self._lock:
            self.is_downloading = True
            self.cancel_requested = False
            self.pause_requested = False
            self.progress = 0.0
            self.current_episode = 0
            self.bytes_downloaded = 0
            self.bytes_total = 0
            self.speed = 0.0
            self.eta = None
            self.state = "running"
            self._pause_event.set()
        self._notify()

    def finish_download(self, state=None, status_message=None):
        with self._lock:
            self.is_downloading = False
            if status_message:
                self.status_message = status_message
            if state:
                self.state = state
            elif self.cancel_requested:
                self.state = "canceled"
            elif self.state not in ("failed", "canceled"):
                self.state = "completed"
            if self.state == "completed":
                self.progress = 100.0
            self.cancel_requested = False
            self.pause_requested = False
            self._pause_event.set()
        self._notify()

    def request_cancel(self):
        with self._lock:
            if self.is_downloading and not self.cancel_requested:
                self.cancel_requested = True
                self.state = "canceling"
                self._pause_event.set()
                self._notify()
                return True
        return False

    def request_pause(self):
        with self._lock:
            if self.is_downloading and not self.pause_requested:
                self.pause_requested = True
                self.state = "paused"
                self._pause_event.clear()
                self._notify()
                return True
        return False

    def resume(self):
        with self._lock:
            if self.pause_requested:
                self.pause_requested = False
                self.state = "running" if self.is_downloading else "idle"
                self._pause_event.set()
                self._notify()
                return True
        return False

    def wait_if_paused(self):
        while True:
            self._pause_event.wait()
            with self._lock:
                if not self.pause_requested:
                    return
            time.sleep(0.2)

    def is_cancel_requested(self):
        with self._lock:
            return self.cancel_requested

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'is_downloading': self.is_downloading,
                'current_title': self.current_title,
                'current_episode_title': self.current_episode_title,
                'progress': self.progress,
                'current_episode': self.current_episode,
                'total_episodes': self.total_episodes,
                'status_message': self.status_message,
                'cancel_requested': self.cancel_requested,
                'pause_requested': self.pause_requested,
                'state': self.state,
                'bytes_downloaded': self.bytes_downloaded,
                'bytes_total': self.bytes_total,
                'speed': self.speed,
                'eta': self.eta,
                'job_id': self.job_id
            }

    def _notify(self):
        listener = None
        with self._lock:
            listener = self._listener
            snapshot = self.get_status()
        if listener:
            try:
                listener(snapshot)
            except Exception:
                logging.exception("Download status listener raised an exception")



class RealDebrid:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.real-debrid.com/rest/1.0"
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        })
        self.is_premium = self.check_premium()
        if self.is_premium:
            logging.info("Real-Debrid Premium Account aktiv")
        else:
            logging.warning("Real-Debrid Account ist kein Premium-Account")

    def check_premium(self) -> bool:
        try:
            response = self.session.get(f"{self.base_url}/user")
            response.raise_for_status()
            data = response.json()
            return bool(data.get("type") == "premium")
        except Exception as exc:
            logging.error(f"Real-Debrid Benutzerinfo konnte nicht geladen werden: {exc}")
            return False

    def unrestrict_link(self, url: str) -> Optional[str]:
        try:
            payload = {"link": url}
            response = self.session.post(f"{self.base_url}/unrestrict/link", data=payload)
            if response.status_code == 429:
                logging.warning("Real-Debrid Rate-Limit erreicht")
                return None
            response.raise_for_status()
            data = response.json()
            direct_link = data.get("download")
            if not direct_link:
                logging.warning("Real-Debrid konnte keinen Direktlink erzeugen")
                return None
            return direct_link
        except Exception as exc:
            logging.error(f"Real-Debrid Fehler: {exc}")
            return None

@dataclass
class DownloadTask:
    url: str
    output_path: str
    title: str
    episode_num: int = 0
    season_num: int = 0
    series_title: str = ""
    episode_url: str = ""
    language_tag: str = ""
    job_id: Optional[int] = None
    last_result: Optional[Dict[str, Any]] = None
    last_error: Optional[str] = None
    order_index: int = 0
    total_count: int = 0

    def __str__(self):
        return f"DownloadTask(title={self.title}, episode_num={self.episode_num})"

class JellyfinAPI:
    def __init__(self, base_url: str, api_key: str, user_id: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.user_id = user_id
        self.session = requests.Session()
        self.session.headers.update({
            'X-MediaBrowser-Token': api_key,
            'Content-Type': 'application/json'
        })

    def refresh_libraries(self):
        """Startet einen Bibliotheksscan in Jellyfin."""
        try:
            # Starte einen vollständigen Bibliotheksscan
            scan_url = f"{self.base_url}/Library/Refresh"
            response = self.session.post(scan_url)
            response.raise_for_status()

            logging.info("Jellyfin Bibliotheksscan erfolgreich gestartet")
            return True

        except Exception as e:
            logging.error(f"Fehler beim Jellyfin-Bibliotheksscan: {str(e)}")
            return False

class StreamScraper:
    def __init__(self, download_dir: str = "downloads", max_parallel_downloads: int = 5, max_parallel_extractions: int = 8, socketio = None):
        """Initialize the scraper."""
        # Prüfe FFmpeg-Verfügbarkeit früh
        _assert_ffmpeg()
        
        self.download_dir = download_dir
        self.max_parallel_downloads = max_parallel_downloads
        self.max_parallel_extractions = max_parallel_extractions
        self.socketio = socketio

        # Erstelle logs Verzeichnis falls es nicht existiert
        self.logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
        os.makedirs(self.logs_dir, exist_ok=True)

        # Pfad zur unsupported_urls.txt
        self.unsupported_urls_file = os.path.join(self.logs_dir, 'unsupported_urls.txt')

        # Set für bereits geloggte URLs
        self._logged_urls = set()
        # Lade bereits existierende URLs
        if os.path.exists(self.unsupported_urls_file):
            with open(self.unsupported_urls_file, 'r', encoding='utf-8') as f:
                self._logged_urls = set(line.strip() for line in f if line.strip())

        # Lade Konfiguration
        self.config = self._load_config()

        # Initialisiere die Liste der Domains für die Sprachprüfung
        self.language_check_domains = self.config.get("scraper", {}).get(
            "language_check_domains",
            ["maxfinishseveral.com", "kristiesoundsimply.com"]
        )
        logging.info(f"Verwende folgende Domains für Sprachprüfung: {self.language_check_domains}")

        # Initialisiere Real-Debrid wenn aktiviert
        self.real_debrid = None
        self.use_real_debrid_priority = False  # Flag für Real-Debrid Priorisierung

        if self.config.get("real_debrid", {}).get("enabled"):
            api_key = self.config["real_debrid"]["api_key"]
            self.real_debrid = RealDebrid(api_key)

            # Wenn Premium-Account vorhanden ist, aktiviere die Priorisierung
            if self.real_debrid.is_premium:
                self.use_real_debrid_priority = True
                logging.info("Real-Debrid Premium Account aktiv - Priorisierung aktiviert")
            else:
                logging.warning("Real-Debrid Account hat kein Premium - Priorisierung deaktiviert")
                # Behalte den Real-Debrid-Client, aber ohne Priorisierung

        # Initialisiere Jellyfin API wenn Umgebungsvariablen gesetzt sind
        jellyfin_url = os.getenv('JELLYFIN_URL')
        jellyfin_api_key = os.getenv('JELLYFIN_API_KEY')
        jellyfin_user_id = os.getenv('JELLYFIN_USER_ID')

        if all([jellyfin_url, jellyfin_api_key, jellyfin_user_id]):
            self.jellyfin = JellyfinAPI(jellyfin_url, jellyfin_api_key, jellyfin_user_id)
        else:
            self.jellyfin = None
            logging.warning("Jellyfin-Integration deaktiviert (fehlende Umgebungsvariablen)")

        # Initialisiere Session mit Browser-ähnlichen Headers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'DNT': '1'
        })

        self.voe_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://voe.sx/",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://voe.sx"
        }

        # Create download directory
        os.makedirs(self.download_dir, exist_ok=True)
        logging.info(f"Using download directory: {self.download_dir}")

        self.download_status = DownloadStatus()
        self.download_status.set_listener(self._on_status_update)
        self._status_listener = None
        self._event_listener = None
        self._results_lock = threading.Lock()
        self._current_results = []
        self.current_job_id = None
        self.current_job_meta = {}

    def set_status_listener(self, listener):
        """Register external listener for status updates."""
        self._status_listener = listener

    def set_event_listener(self, listener):
        """Register external listener for job events."""
        self._event_listener = listener

    def begin_job(self, job_id, meta=None):
        """Initialize bookkeeping for a new download job."""
        self.current_job_id = job_id
        self.current_job_meta = meta or {}
        self.download_status.set_job(job_id)
        with self._results_lock:
            self._current_results = []

    def finalize_job(self):
        """Reset job state and return collected results."""
        with self._results_lock:
            results = list(self._current_results)
        self.current_job_id = None
        self.current_job_meta = {}
        self.download_status.set_job(None)
        return results

    def _on_status_update(self, status):
        """Internal bridge for DownloadStatus notifications."""
        data = dict(status)
        if self.current_job_id is not None:
            data['job_id'] = self.current_job_id
        if self._status_listener:
            try:
                self._status_listener(data)
            except Exception:
                logging.exception("Status listener raised an exception")
        elif self.socketio:
            self.socketio.emit('status_update', data)

    def _emit_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None):
        """Notify listeners about job level events."""
        data = dict(payload or {})
        if self.current_job_id is not None:
            data['job_id'] = self.current_job_id
        data['event'] = event_type
        if self._event_listener:
            try:
                self._event_listener(event_type, data)
            except Exception:
                logging.exception("Event listener raised an exception")
        elif self.socketio:
            self.socketio.emit(event_type, data)

    def _record_result(self, task: DownloadTask, success: bool, extra: Optional[Dict[str, Any]] = None):
        """Store episode level result for later history reporting."""
        result = {
            'title': task.title,
            'episode_num': task.episode_num,
            'season_num': task.season_num,
            'output_path': task.output_path,
            'language_tag': task.language_tag,
            'url': task.episode_url or task.url,
            'success': success
        }
        if extra:
            result.update(extra)
        with self._results_lock:
            self._current_results.append(result)
        return result

    def _load_config(self) -> dict:
        """Lädt die Konfigurationsdatei"""
        config_path = os.path.join(os.path.dirname(__file__), "config.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Fehler beim Laden der Konfiguration: {str(e)}")
        return {}

    def log_unsupported_url(self, url: str, error_message: str):
        """Loggt nicht unterstützte URLs in eine Datei, ohne Duplikate."""
        if url not in self._logged_urls:
            self._logged_urls.add(url)
            with open(self.unsupported_urls_file, 'a', encoding='utf-8') as f:
                f.write(f"{url}\n")

    def _find_potential_stream_links(self, episode_url: str, base_url: str) -> List[str]:
        """Findet alle potenziellen Stream-Links auf der Episode-Seite"""
        try:
            response = self.session.get(episode_url, headers=self.session.headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')

            links = set()

            # Suche nach dem deutschen Stream-Container
            language_boxes = soup.find_all('div', class_='changeLanguageBox')
            for box in language_boxes:
                # Finde den ausgewählten deutschen Stream
                german_img = box.find('img', {'data-lang-key': '1', 'class': 'selectedLanguage'})
                if german_img:
                    # Suche den zugehörigen Stream-Container
                    stream_container = box.find_parent('div', class_='hosterSiteVideo')
                    if stream_container:
                        # Extrahiere die <li>-Elemente mit data-lang-key="1"
                        for li in stream_container.find_all('li', {'data-lang-key': '1'}):
                            link = li.find('a', href=True)
                            if link and 'href' in link.attrs:
                                redirect_url = link['href']
                                if redirect_url.startswith('/redirect/'):
                                    full_url = urljoin(base_url, redirect_url)
                                    links.add(full_url)

            # Fallback: Suche nach allen Streams
            if not links:
                for link in soup.find_all('a', href=re.compile(r'/redirect/\d+')):
                    redirect_url = link['href']
                    if redirect_url.startswith('/redirect/'):
                        full_url = urljoin(base_url, redirect_url)
                        links.add(full_url)

            return list(links)

        except Exception as e:
            logging.error(f"Fehler beim Suchen der Stream-Links: {str(e)}")
            return []

    def extract_stream_urls(self, episode_url: str, base_url: str) -> List[str]:
        """Extrahiert die Stream-URLs von der Seite."""
        try:
            logging.info(f"\nExtrahiere Stream-URLs von: {episode_url}")

            # Hole den Seiteninhalt
            response = self.make_request(episode_url)
            if not response:
                logging.error("Fehler beim Laden der Seite")
                return []

            # Parse den HTML-Inhalt
            soup = BeautifulSoup(response.text, 'html.parser')

            # Finde alle Redirect-Links
            redirects = []
            for a in soup.find_all('a', href=True):
                href = a['href']
                if '/redirect/' in href:
                    redirects.append(href)

            logging.info(f"Gefundene Redirect-Links: {len(redirects)}")

            # Verarbeite jeden Redirect und sammle die VOE-URLs
            stream_urls = []

            # Verarbeite Redirects parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_parallel_extractions) as executor:
                future_to_redirect = {
                    executor.submit(self._follow_redirect, redirect, base_url): redirect
                    for redirect in redirects
                }

                for future in concurrent.futures.as_completed(future_to_redirect):
                    final_url = future.result()
                    if final_url:
                        stream_urls.append(final_url)

            logging.info(f"Gefunden: {len(stream_urls)} verfügbare Streams")
            return stream_urls

        except Exception as e:
            logging.error(f"Fehler beim Extrahieren der Stream-URLs: {str(e)}")
            return []

    def _follow_redirect(self, redirect_url: str, base_url: str) -> Optional[str]:
        """Folgt einem Redirect-Link und gibt die finale URL zurück."""
        try:
            if redirect_url.startswith('/'):
                redirect_url = urljoin(base_url, redirect_url)

            logging.info(f"\nFolge Redirect: {redirect_url}")

            # Erster Redirect (von aniworld.to zu voe.sx)
            response = self.session.get(redirect_url, allow_redirects=True, timeout=10)
            voe_url = response.url

            # Wenn es kein VOE.sx Link ist, überspringen
            if 'voe.sx' not in voe_url:
                logging.info(f"Kein VOE.sx Link: {voe_url}")
                return None

            # Extrahiere die ID aus dem VOE.sx Link
            # https://voe.sx/e/sgoohgni1jb4 -> sgoohgni1jb4
            match = re.search(r'voe\.sx/e/([a-zA-Z0-9]+)', voe_url)
            if not match:
                logging.warning(f"Konnte keine VOE.sx ID finden in: {voe_url}")
                return None

            # Gib den VOE.sx Link direkt zurück, da wir die Sprachprüfung nicht mehr brauchen
            return voe_url

        except Exception as e:
            logging.error(f"Fehler beim Folgen des Redirects: {str(e)}")
            return None

    def _try_voe_fallback(self, url, output_path, title):
        """
        Try to download a VOE.sx video using the fallback downloader.

        Args:
            url (str): The VOE.sx URL
            output_path (str): Path to save the video
            title (str): Title of the video

        Returns:
            bool: True if successful, False otherwise
        """
        logging.info(f"Trying VOE.sx fallback downloader for: {url}")
        try:
            fallback = VoeFallbackDownloader()
            filename = f"{title}.mp4"
            full_path = os.path.join(os.path.dirname(output_path), filename)

            # Use the fallback downloader directly
            success = fallback.download_video(url, full_path)

            if success:
                logging.info(f"VOE fallback: Download successful! File saved to: {full_path}")
                return True
            else:
                logging.error("VOE fallback: Download failed")
                return False
        except Exception as e:
            logging.error(f"VOE fallback: Error - {str(e)}")
            return False

    def _verify_german_audio(self, video_path: str, title: str) -> bool:
        """
        Prüft, ob die heruntergeladene Datei deutsche Audiospur hat.
        
        Args:
            video_path (str): Pfad zur Video-Datei
            title (str): Titel für Logging
            
        Returns:
            bool: True wenn deutsche Audiospur vorhanden, False sonst
        """
        try:
            from language_guard import verify_language
            
            # Hole Language Guard Konfiguration
            lang_cfg = self.config.get('language', {})
            prefer = set(map(str.lower, lang_cfg.get('prefer', ['de','deu','ger'])))
            require_dub = lang_cfg.get('require_dub', True)
            sample_seconds = lang_cfg.get('sample_seconds', 45)
            remux = lang_cfg.get('remux_to_de_if_present', True)
            
            logging.info(f"Prüfe deutsche Audiospur für: {title}")
            ok, detail, fixed_path = verify_language(
                video_path,
                prefer_tags=prefer,
                require_dub=require_dub,
                sample_seconds=sample_seconds,
                remux=remux
            )
            
            if ok:
                logging.info(f"✓ Deutsche Audiospur bestätigt für {title}: {detail}")
                
                # Falls eine remuxte Datei erstellt wurde, ersetze die ursprüngliche
                if fixed_path and fixed_path != video_path:
                    try:
                        target_path = Path(video_path)
                        replacement_path = Path(fixed_path)
                        if not replacement_path.exists():
                            logging.error(f"Remux-Ergebnis fehlt: {fixed_path}")
                        else:
                            replacement_path.replace(target_path)
                            logging.info(f"Datei erfolgreich remuxed: {video_path}")
                    except Exception as e:
                        logging.error(f"Fehler beim Ersetzen der remuxten Datei: {e}")
                
                return True
            else:
                logging.warning(f"✗ Keine deutsche Audiospur gefunden für {title}: {detail}")
                
                # Unbrauchbare Datei über temporären Pfad löschen
                try:
                    target_path = Path(video_path)
                    if target_path.exists():
                        reject_path = target_path.with_suffix(target_path.suffix + '.reject')
                        if reject_path.exists():
                            reject_path.unlink(missing_ok=True)
                        target_path.replace(reject_path)
                        try:
                            reject_path.unlink(missing_ok=True)
                        except Exception as cleanup_err:
                            logging.warning(f"Temporäre Reject-Datei konnte nicht entfernt werden: {cleanup_err}")
                        logging.info(f"Datei ohne deutsche Audiospur gelöscht: {video_path}")
                except Exception as e:
                    logging.error(f"Fehler beim Löschen der Datei: {e}")


                return False
                
        except ImportError:
            logging.warning("Language Guard nicht verfügbar - überspringe Sprachprüfung")
            # Konfigurierbar: bei fehlender Language Guard akzeptieren oder ablehnen
            accept_on_error = self.config.get('language.accept_on_error', False)
            return accept_on_error
        except Exception as e:
            logging.error(f"Fehler bei der Sprachprüfung für {title}: {e}")
            # Konfigurierbar: bei Fehlern akzeptieren oder ablehnen
            accept_on_error = self.config.get('language.accept_on_error', False)
            return accept_on_error

    def _download_video(self, task: DownloadTask, max_retries: int = 3) -> bool:
        """Video von VOE.sx oder maxfinishseveral.com herunterladen"""
        retries = 0
        rd_failed = False  # Real-Debrid Fehlschlag
        original_url = None  # Store the original URL before Real-Debrid
        total_tasks = task.total_count or self.current_job_meta.get('total_episodes', 1) or 1

        # Sicherstellen, dass task.url ein String ist
        if isinstance(task.url, list):
            if task.url:
                task.url = task.url[0]  # Verwende die erste URL aus der Liste
                logging.debug(f"Verwende erste URL aus Liste: {task.url}")
            else:
                logging.error(f"Keine gültige URL gefunden für {task.title}")
                return False
        elif not isinstance(task.url, str):
            logging.error(f"Ungültiger URL-Typ für {task.title}: {type(task.url)}")
            return False

        # Check if this is a VOE.sx URL
        parsed_url = urlparse(task.url)
        is_voe = parsed_url.netloc.endswith('voe.sx')

        # Save the original URL for potential fallback
        if is_voe:
            original_url = task.url
            logging.debug(f"Saved original VOE.sx URL for potential fallback: {original_url}")

        while retries < max_retries:
            self.download_status.wait_if_paused()
            task.last_error = None
            # Check if cancel was requested
            if self.download_status.is_cancel_requested():
                logging.info(f"Download abgebrochen für: {task.title}")
                task.last_error = 'Abgebrochen'
                return False

            try:
                if retries > 0:
                    # Exponentielles Backoff: 5s, 10s, 20s...
                    wait_time = 5 * (2 ** (retries - 1))
                    logging.debug(f"Warte {wait_time} Sekunden vor Wiederholungsversuch {retries + 1} von {max_retries}")
                    time.sleep(wait_time)

                logging.info(f"Starte Download: {task.title}")
                os.makedirs(os.path.dirname(task.output_path), exist_ok=True)

                # Wenn Real-Debrid verfügbar ist und noch nicht fehlgeschlagen ist
                # Priorisiere Real-Debrid, wenn Premium-Account vorhanden ist oder es ein VOE.sx Link ist
                if self.real_debrid and not rd_failed and (task.url.startswith(('https://voe.sx/', 'https://maxfinishseveral.com/')) or self.use_real_debrid_priority):
                    logging.debug("Nutze Real-Debrid für Download...")
                    direct_url = self.real_debrid.unrestrict_link(task.url)
                    if direct_url:
                        task.url = direct_url
                        logging.debug("Real-Debrid Link erfolgreich erstellt")
                    else:
                        logging.warning("Real-Debrid fehlgeschlagen nach mehreren Versuchen")
                        logging.debug("Wechsle zu normalem Download...")
                        rd_failed = True
                        retries = 0  # Reset retries für normale Downloads

                        # If this is a VOE.sx URL and we have the original URL, try fallback right away
                        if is_voe and original_url:
                            logging.info("Real-Debrid fehlgeschlagen für VOE.sx Link, versuche direkte Fallback-Methode...")
                            if self._try_voe_fallback(original_url, task.output_path, task.title):
                                logging.debug("VOE.sx Fallback erfolgreich, prüfe deutsche Audiospur...")
                                if self._verify_german_audio(task.output_path, task.title):
                                    return True
                                else:
                                    logging.warning(f"VOE Fallback Datei {task.title} entspricht nicht den deutschen Sprachanforderungen")
                                    task.last_error = 'Sprachprüfung fehlgeschlagen'
                                    return False

                        continue

                # Download mit yt-dlp
                def progress_hook(progress):
                    self.download_status.wait_if_paused()
                    if self.download_status.is_cancel_requested():
                        raise DownloadCancelled('Benutzerabbruch')
                    status = progress.get('status')
                    bytes_downloaded = progress.get('downloaded_bytes', 0)
                    total_bytes = progress.get('total_bytes') or progress.get('total_bytes_estimate') or 0
                    speed = progress.get('speed') or 0.0
                    eta = progress.get('eta')
                    if total_bytes and total_bytes > 0:
                        episode_fraction = bytes_downloaded / total_bytes
                    else:
                        episode_fraction = 0.0
                    overall_progress = ((task.order_index - 1) + episode_fraction) / (total_tasks or 1) * 100
                    status_message = f"Lade {task.title}" if status == 'downloading' else f"Fertig: {task.title}"
                    self.download_status.update(episode_title=task.title, current_episode=task.order_index, total_episodes=total_tasks, progress=overall_progress, bytes_downloaded=bytes_downloaded, bytes_total=total_bytes, speed=speed, eta=eta, status_message=status_message, state='running')

                ydl_opts = {
                    'format': 'best',
                    'outtmpl': task.output_path,
                    'quiet': True,
                    'no_warnings': True,
                    'progress_hooks': [progress_hook],
                    'noprogress': True,
                    'extractor_args': {'youtube': {'player_skip': ['js', 'configs', 'webpage']}},
                }

                try:
                    with YoutubeDL(ydl_opts) as ydl:
                        ydl.download([task.url])
                    logging.info(f"Download erfolgreich: {task.title}")

                    # Language Guard: Prüfe deutsche Audiospur
                    if self._verify_german_audio(task.output_path, task.title):
                        file_size = os.path.getsize(task.output_path) if os.path.exists(task.output_path) else 0
                        task.last_result = {'file_size': file_size}
                        return True
                    else:
                        # Datei entspricht nicht den Sprachanforderungen
                        logging.warning(f"Datei {task.title} entspricht nicht den deutschen Sprachanforderungen")
                        task.last_error = 'Sprachprüfung fehlgeschlagen'
                        return False

                except DownloadCancelled:
                    logging.info(f"Download durch Benutzer gestoppt: {task.title}")
                    task.last_error = 'Abgebrochen'
                    return False
                except Exception as e:
                    error_msg = str(e)
                    task.last_error = error_msg
                    if "Unsupported URL" in error_msg:
                        self.log_unsupported_url(task.url, error_msg)
                    logging.error(f"yt-dlp Fehler: {error_msg}")
                    if "Video unavailable" in error_msg:
                        logging.warning("Video nicht mehr verfügbar")
                        task.last_error = 'Video nicht mehr verfügbar'
                        return False
                    raise  # Re-raise für andere Fehler

            except Exception as e:
                task.last_error = str(e)
                logging.error(f"Download-Fehler: {str(e)}")
                retries += 1
                if retries >= max_retries:
                    task.last_error = 'Maximale Anzahl von Versuchen erreicht'
                    logging.error(f"Maximale Anzahl von Versuchen erreicht für {task.title}")

                    # Try VOE fallback if this is a VOE.sx URL
                    if is_voe:
                        # Use the original URL if we have it
                        url_to_try = original_url if original_url else task.url
                        logging.debug(f"Versuche VOE Fallback mit ursprünglicher URL: {url_to_try}")
                        if self._try_voe_fallback(url_to_try, task.output_path, task.title):
                            logging.debug("VOE.sx Fallback erfolgreich, prüfe deutsche Audiospur...")
                            if self._verify_german_audio(task.output_path, task.title):
                                logging.debug("Download als erfolgreich markiert.")
                                file_size = os.path.getsize(task.output_path) if os.path.exists(task.output_path) else 0
                                task.last_result = {'file_size': file_size, 'fallback': 'voe'}
                                return True
                            else:
                                logging.warning(f"VOE Fallback Datei {task.title} entspricht nicht den deutschen Sprachanforderungen")
                                return False

                    return False

        return False

    def make_request(self, url: str, retries: int = 3) -> Optional[requests.Response]:
        """Make an HTTP request with retries and error handling (thread-safe)."""
        for attempt in range(retries):
            try:
                # Thread-safe: verwende eigene Headers statt geteilte Session
                response = requests.get(url, headers=self.session.headers, timeout=10)
                response.raise_for_status()
                return response
            except requests.RequestException as e:
                logging.error(f"Request failed (attempt {attempt + 1}/{retries}): {str(e)}")
                if attempt < retries - 1:
                    time.sleep(1)  # Wait before retrying
                continue
        return None

    def _extract_seasons(self, soup: BeautifulSoup, base_url: str, current_url: str) -> List[Dict]:
        """Extrahiert alle verfügbaren Staffeln"""
        seasons = []
        seen_seasons = set()

        # Finde alle Staffel-Links
        season_links = soup.find_all('a', href=re.compile(r'/staffel-\d+'))

        for link in season_links:
            season_url = link.get('href', '')
            if not season_url.startswith('http'):
                season_url = urljoin(base_url, season_url)

            # Extrahiere Staffelnummer
            season_match = re.search(r'/staffel-(\d+)', season_url)
            if not season_match:
                continue

            season_num = int(season_match.group(1))

            # Überspringe Duplikate
            if season_num in seen_seasons:
                continue
            seen_seasons.add(season_num)

            seasons.append({
                'number': season_num,
                'url': season_url
            })

        # Wenn keine Staffeln gefunden wurden, füge aktuelle URL als Staffel 1 hinzu
        if not seasons:
            seasons.append({
                'number': 1,
                'url': current_url
            })

        # Sortiere nach Staffelnummer
        seasons.sort(key=lambda x: x['number'])

        # Zeige gefundene Staffeln
        if len(seasons) > 0:
            min_season = min(s['number'] for s in seasons)
            max_season = max(s['number'] for s in seasons)
            logging.info(f"\nGefunden: {len(seasons)} Staffeln (Staffel {min_season} bis {max_season})")

        return seasons

    def _extract_episode_title(self, episode_elem) -> str:
        """Extract episode title from the episode element."""
        title_cell = episode_elem.find('td', class_='seasonEpisodeTitle')
        if not title_cell:
            return f"Episode {episode_elem.get('data-episode-season-id', '')}"

        # Try to get the German title from <strong> first
        strong_title = title_cell.find('strong')
        if strong_title:
            return strong_title.text.strip()

        # Fall back to English title in <span> if no German title exists
        span_title = title_cell.find('span')
        if span_title:
            return span_title.text.strip()

        # Last resort: get any text content
        return title_cell.text.strip()

    def _extract_episodes(self, season_url: str, base_url: str) -> List[Dict]:
        """Extract all episodes from a season page."""
        response = self.make_request(season_url)
        if not response:
            return []

        soup = BeautifulSoup(response.text, 'html.parser')
        episodes = []

        # Look for episode elements in the table
        episode_rows = soup.find_all('tr', attrs={'data-episode-id': True})

        for row in episode_rows:
            # Get episode number from meta tag
            ep_num_meta = row.find('meta', attrs={'itemprop': 'episodeNumber'})
            number = int(ep_num_meta['content']) if ep_num_meta else len(episodes) + 1

            # Get episode URL
            ep_link = row.find('a', attrs={'itemprop': 'url'})
            if not ep_link:
                continue

            episode_url = ep_link.get('href', '')
            if not episode_url.startswith('http'):
                episode_url = urljoin(base_url, episode_url)

            # Extract title using the new method
            title = self._extract_episode_title(row)

            # Check for language flags - look for German dub and German sub
            has_german_dub = False
            has_german_sub = False
            edit_functions_cell = row.find('td', class_='editFunctions')

            if edit_functions_cell:
                # Check for German dub (german.svg)
                german_flag = edit_functions_cell.find('img', src=lambda s: s and 'german.svg' in s)
                if german_flag:
                    has_german_dub = True
                    logging.info(f"Found German dub for episode {number}: {title}")

                # Check for German sub (japanese-german.svg)
                german_sub_flag = edit_functions_cell.find('img', src=lambda s: s and 'japanese-german.svg' in s)
                if german_sub_flag:
                    has_german_sub = True
                    logging.info(f"Found German subtitles for episode {number}: {title}")

            episodes.append({
                "title": title,
                "url": episode_url,
                "number": number,
                "has_german_dub": has_german_dub,  # Add flag indicating if German dub is available
                "has_german_sub": has_german_sub   # Add flag indicating if German sub is available
            })

        return sorted(episodes, key=lambda x: x["number"])

    def scrape_series(self, url: str, retry_failed: bool = True, auto_next_season: bool = True):
        """Scrape eine komplette Serie mit Unterstützung für Wiederholungsversuche und automatische nächste Staffel"""
        logging.info(f"\nStarte Serien-Scraping von: {url}")

        try:
            # Hole die Seite einmal am Anfang
            response = self.session.get(url)
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extrahiere den Seriennamen
            series_name = self._extract_series_name(url)
            logging.info(f"\nSerie: {series_name}")

            # Finde alle Staffel-Links
            season_links = set()  # Verwende ein Set für eindeutige Staffeln
            for link in soup.find_all('a', href=re.compile(r'/staffel-\d+')):
                href = link.get('href', '')
                season_match = re.search(r'/staffel-(\d+)', href)
                if season_match:
                    season_num = int(season_match.group(1))
                    season_url = urljoin(self.get_base_url(url), href)
                    season_links.add((season_num, season_url))

            # Konvertiere zu Liste und sortiere nach Staffelnummer
            season_links = sorted(list(season_links))

            if not season_links:
                # Wenn keine Staffeln gefunden wurden, behandle als Staffel 1
                season_links = [(1, url)]

            # Zeige gefundene Staffeln
            min_season = season_links[0][0]
            max_season = season_links[-1][0]
            logging.info(f"\nGefunden: {len(season_links)} Staffeln (Staffel {min_season} bis {max_season})")

            while url:
                try:
                    # Extrahiere Staffelnummer aus URL
                    season_match = re.search(r'/staffel-(\d+)', url)
                    current_season = int(season_match.group(1)) if season_match else 1
                    logging.info(f"\nVerarbeite Staffel {current_season}")

                    # Verarbeite aktuelle Staffel
                    failed_episodes = self.process_series(url)

                    # Versuche fehlgeschlagene Episoden erneut
                    if failed_episodes and retry_failed:
                        logging.info(f"\nStarte Wiederholungsversuch für {len(failed_episodes)} fehlgeschlagene Episoden...")
                        retry_failed_episodes = []
                        for episode in failed_episodes:
                            logging.info(f"\nWiederhole Download für: {episode.title}")
                            if not self._download_video(episode, max_retries=3):
                                retry_failed_episodes.append(episode)

                        if retry_failed_episodes:
                            logging.warning(f"\nEndgültig fehlgeschlagene Episoden in Staffel {current_season}:")
                            for episode in retry_failed_episodes:
                                logging.warning(f"- {episode.title}")

                    # Wenn auto_next_season aktiv ist, suche nach der nächsten Staffel
                    if auto_next_season:
                        next_season = current_season + 1
                        next_url = re.sub(r'/staffel-\d+', f'/staffel-{next_season}', url)

                        # Prüfe ob die nächste Staffel existiert
                        try:
                            response = self.session.get(next_url, headers=self.session.headers)
                            if response.status_code == 200 and 'Keine Streams verfügbar' not in response.text:
                                logging.info(f"\nGefunden: Staffel {next_season}")
                                url = next_url
                                continue
                            else:
                                logging.info(f"\nKeine weitere Staffel gefunden. Beende Scraping.")
                                break
                        except Exception as e:
                            logging.error(f"\nFehler beim Prüfen der nächsten Staffel: {str(e)}")
                            break
                    else:
                        break

                except Exception as e:
                    logging.error(f"\nFehler beim Verarbeiten der Staffel: {str(e)}")
                    break

        except Exception as e:
            logging.error(f"\nFehler beim Scrapen der Serie: {str(e)}")

        logging.info("\nSerien-Scraping abgeschlossen.")

    def process_series(self, url: str, job_options: Optional[Dict[str, Any]] = None) -> bool:
        job_options = job_options or {}
        try:
            logging.info(f"Starte Verarbeitung von {url}")
            response = self.make_request(url)
            if not response:
                logging.error("Konnte Serienseite nicht abrufen")
                self._emit_event('job_error', {'error': 'Serienseite konnte nicht geladen werden', 'url': url})
                return False
            soup = BeautifulSoup(response.text, 'html.parser')
            base_url = self.get_base_url(url)
            series_name = job_options.get('title') or self._extract_series_name(url)
            series_path = self._get_series_path(series_name, url)
            os.makedirs(series_path, exist_ok=True)
            seasons = self._extract_seasons(soup, base_url, url)
            if not seasons:
                message = f"Keine Staffeln für {series_name} gefunden"
                logging.warning(message)
                self._emit_event('job_error', {'error': message, 'series': series_name, 'url': url})
                return False
            seasons.sort(key=lambda s: s.get('number', 0))
            season_episodes: Dict[int, List[Dict[str, Any]]] = {}
            max_workers = min(len(seasons), max(1, self.max_parallel_extractions))
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {executor.submit(self._extract_episodes, season['url'], base_url): season for season in seasons}
                for future in concurrent.futures.as_completed(future_map):
                    season = future_map[future]
                    season_num = season['number']
                    try:
                        episodes = future.result()
                    except Exception as exc:
                        logging.error(f"Fehler beim Laden der Episodenliste für Staffel {season_num}: {exc}")
                        episodes = []
                    season_episodes[season_num] = episodes
            selection_map = self._build_selection_map(job_options)
            total_tasks = 0
            for season in seasons:
                season_num = season['number']
                episodes = season_episodes.get(season_num, [])
                total_tasks += self._count_eligible_episodes(episodes, selection_map.get(season_num))
            self.current_job_meta['total_episodes'] = total_tasks
            self.current_job_meta['processed'] = 0
            self.current_job_meta['completed'] = 0
            if total_tasks == 0:
                message = "Keine passenden Episoden gefunden"
                logging.info(message)
                self.download_status.update(progress=0.0, total_episodes=0, status_message=message, state='idle')
                self._emit_event('job_noop', {'message': message, 'series': series_name})
                return False
            self.download_status.update(total_episodes=total_tasks, status_message=f"{total_tasks} Episoden geplant", state='running')
            self._emit_event('job_plan_ready', {'total_episodes': total_tasks, 'series': series_name, 'season_count': len(seasons)})
            overall_success = True
            for season in seasons:
                season_num = season['number']
                episodes = season_episodes.get(season_num, [])
                part_success = self._process_season(
                    season_url=season['url'],
                    series_name=series_name,
                    season_num=season_num,
                    series_path=series_path,
                    job_options=job_options,
                    episodes=episodes,
                    selected_episode_numbers=selection_map.get(season_num),
                    total_tasks=total_tasks
                )
                overall_success = overall_success and part_success
                if self.download_status.is_cancel_requested():
                    logging.info("Download wurde abgebrochen - restliche Staffeln werden übersprungen")
                    break
            return overall_success and not self.download_status.is_cancel_requested()
        except Exception as e:
            logging.error(f"Fehler beim Verarbeiten der Serie: {str(e)}", exc_info=True)
            self._emit_event('job_error', {'error': str(e), 'url': url})
            return False
    def _build_selection_map(self, job_options: Dict[str, Any]) -> Dict[int, Optional[Set[int]]]:
        """Normalize episode selection payload into a season->episodes mapping."""
        selections = job_options.get('selected_episodes')
        if not selections:
            return {}
        normalized: Dict[int, Optional[Set[int]]] = {}
        if isinstance(selections, list):
            for entry in selections:
                if not isinstance(entry, dict):
                    continue
                season = entry.get('season')
                episodes = entry.get('episodes')
                try:
                    season_num = int(season)
                except (TypeError, ValueError):
                    continue
                if episodes in (None, 'all', 'ALL', '*'):
                    normalized[season_num] = None
                elif isinstance(episodes, list):
                    try:
                        normalized[season_num] = {int(e) for e in episodes}
                    except ValueError:
                        normalized[season_num] = None
                else:
                    try:
                        normalized[season_num] = {int(e) for e in str(episodes).split(',') if e}
                    except ValueError:
                        normalized[season_num] = None
        elif isinstance(selections, dict):
            for key, value in selections.items():
                try:
                    season_num = int(key)
                except (TypeError, ValueError):
                    continue
                if value in (None, 'all', 'ALL', '*'):
                    normalized[season_num] = None
                elif isinstance(value, list):
                    try:
                        normalized[season_num] = {int(e) for e in value}
                    except ValueError:
                        normalized[season_num] = None
                else:
                    try:
                        normalized[season_num] = {int(e) for e in str(value).split(',') if e}
                    except ValueError:
                        normalized[season_num] = None
        return normalized

    def _count_eligible_episodes(self, episodes: List[Dict[str, Any]], selected_numbers: Optional[Set[int]]) -> int:
        if not episodes:
            return 0
        lang_config = self.config.get("scraper", {}).get("language_preference", {})
        allow_german_sub = lang_config.get('allow_german_sub', True)
        count = 0
        for episode in episodes:
            number = episode.get('number')
            if number is None:
                continue
            try:
                number = int(number)
            except (TypeError, ValueError):
                continue
            if selected_numbers is not None and number not in selected_numbers:
                continue
            has_dub = episode.get('has_german_dub', False)
            has_sub = episode.get('has_german_sub', False)
            if has_dub or (allow_german_sub and has_sub):
                count += 1
        return count

    def get_series_details(self, url: str) -> Dict[str, Any]:
        """Return seasons and episodes for a series details view."""
        base_url = self.get_base_url(url)
        response = self.make_request(url)
        if not response:
            return {"title": "Unbekannt", "seasons": []}

        series_title = self._extract_series_name(url)
        soup = BeautifulSoup(response.text, "html.parser")
        seasons = self._extract_seasons(soup, base_url, url)

        result: Dict[str, Any] = {"title": series_title, "seasons": []}
        for season in seasons:
            episodes = self._extract_episodes(season["url"], base_url)
            result["seasons"].append({
                "number": season["number"],
                "url": season["url"],
                "episodes": episodes,
            })
        return result


    def start_download(self, url: str, job_options: Optional[Dict[str, Any]] = None):
        """Haupteinstiegspunkt für den Download mit optionalen Job-Metadaten."""
        if self.download_status.is_downloading:
            raise Exception("Es läuft bereits ein Download!")

        job_options = job_options or {}
        job_id = job_options.get('job_id')
        self.begin_job(job_id, job_options)
        display_title = job_options.get('title') or job_options.get('series_name') or url
        state = 'failed'
        message = ''
        success = False
        results: List[Dict[str, Any]] = []

        try:
            self.download_status.start_download()
            self.download_status.update(title=display_title, status_message="Starte Download...", state='running')
            self._emit_event('job_started', {'title': display_title, 'url': url, 'job_id': job_id})
            success = self.process_series(url, job_options=job_options)
            if self.download_status.cancel_requested:
                state = 'canceled'
                message = 'Download abgebrochen'
            elif success:
                state = 'completed'
                message = 'Download abgeschlossen'
            else:
                state = 'failed'
                message = 'Download teilweise fehlgeschlagen'
            self.download_status.finish_download(state=state, status_message=message)
        except Exception as e:
            message = str(e)
            state = 'failed'
            logging.error(f"Fehler beim Download: {message}", exc_info=True)
            self.download_status.finish_download(state=state, status_message=message)
            self._emit_event('job_error', {'error': message, 'url': url, 'job_id': job_id})
            raise
        finally:
            results = self.finalize_job()
            self._emit_event('job_finished', {'state': state, 'success': state == 'completed', 'message': message, 'results': results, 'job_id': job_id})

        return {'success': state == 'completed', 'state': state, 'message': message, 'results': results}
    def reset_session(self):
        """Reset die Session für neue Downloads, ohne andere Funktionen zu beeinflussen."""
        try:
            # Speichere wichtige Header
            important_headers = {
                'User-Agent': self.session.headers.get('User-Agent'),
                'Accept-Language': self.session.headers.get('Accept-Language')
            }

            # Erstelle neue Session
            self.session = requests.Session()

            # Stelle wichtige Header wieder her
            self.session.headers.update(important_headers)

            # Setze Download-Status zurück
            self.download_status = DownloadStatus()
            self.download_status.set_listener(self._on_status_update)

            logging.info("Session erfolgreich zurückgesetzt")
            return True
        except Exception as e:
            logging.error(f"Fehler beim Zurücksetzen der Session: {str(e)}")
            return False

    def _extract_series_name(self, url: str) -> str:
        """Extrahiert und bereinigt den Seriennamen"""
        try:
            response = self.session.get(url)
            soup = BeautifulSoup(response.text, 'html.parser')

            # Versuche zuerst den h1 Tag mit itemprop="name" zu finden
            title_elem = soup.find('h1', {'itemprop': 'name'})
            if not title_elem:
                # Fallback auf normalen h1 Tag
                title_elem = soup.find('h1')

            if title_elem:
                series_name = title_elem.get_text().strip()

                # Entferne Website-Suffixe
                series_name = re.sub(r'\s*[❤♥]\s*S\.to.*$', '', series_name)
                series_name = re.sub(r'\s*\|\s*S\.to.*$', '', series_name)
                series_name = re.sub(r'\s*\|\s*AniWorld\.to.*$', '', series_name)
                series_name = re.sub(r'\s+Stream$', '', series_name)

                # Entferne zusätzliche Whitespaces
                series_name = ' '.join(series_name.split())

                return series_name

            return "Unknown Series"

        except Exception as e:
            logging.error(f"Fehler beim Extrahieren des Seriennamens: {str(e)}")
            return "Unknown Series"

    def _sanitize_filename(self, filename: str) -> str:
        """Remove invalid characters from filename."""
        # Remove or replace common website suffixes
        filename = re.sub(r'\s*\|\s*AniWorld\.to.*$', '', filename)
        filename = re.sub(r'\s*\|\s*S\.to.*$', '', filename)

        # Remove invalid characters
        invalid_chars = r'<>:"/\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '')

        # Remove or replace other problematic characters
        filename = filename.replace('\n', ' ').replace('\r', ' ')
        filename = re.sub(r'\s+', ' ', filename)  # Replace multiple spaces with single space
        filename = filename.strip()

        # Ensure filename is not too long (Windows has a 255 char limit)
        if len(filename) > 240:  # Leave some room for extension
            filename = filename[:240]

        return filename

    def _sanitize_directory_name(self, directory_name: str) -> str:
        """Sanitize directory name by replacing invalid characters with hyphens."""
        # Remove or replace common website suffixes
        directory_name = re.sub(r'\s*\|\s*AniWorld\.to.*$', '', directory_name)
        directory_name = re.sub(r'\s*\|\s*S\.to.*$', '', directory_name)

        # Replace invalid characters with hyphens
        invalid_chars = r'<>:"/\|?*'
        for char in invalid_chars:
            directory_name = directory_name.replace(char, '-')

        # Remove or replace other problematic characters
        directory_name = directory_name.replace('\n', ' ').replace('\r', ' ')
        directory_name = re.sub(r'\s+', ' ', directory_name)  # Replace multiple spaces with single space
        directory_name = directory_name.strip()

        # Ensure directory name is not too long (Windows has a 255 char limit for full path)
        if len(directory_name) > 240:  # Leave some room for path
            directory_name = directory_name[:240]

        return directory_name

    def __del__(self):
        """Clean up resources."""
        pass

    def get_anime_list(self) -> List[Dict[str, str]]:
        """Scrape die Liste aller Animes von aniworld.to"""
        url = "https://aniworld.to/animes"

        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()  # Wirft Fehler bei HTTP-Statuscode >= 400

            soup = BeautifulSoup(response.text, 'html.parser')
            anime_list = []

            # Finde alle Anime-Links in allen Genre-Kategorien
            for link in soup.select('ul li a[href^="/anime/stream/"]'):
                title = link.text.strip()
                if 'Stream anschauen' in title:
                    title = title.replace(' Stream anschauen', '')
                url = 'https://aniworld.to' + link.get('href', '')

                # Extrahiere alternative Titel falls vorhanden
                alt_titles = []
                if link.has_attr('data-alternative-title'):
                    alt_titles = link.get('data-alternative-title', '').split(', ')

                if title and url:  # Nur hinzufügen wenn Titel und URL vorhanden
                    # Prüfe ob der Anime bereits in der Liste ist (Duplikate vermeiden)
                    if not any(anime['url'] == url for anime in anime_list):
                        anime_list.append({
                            'title': title,
                            'url': url,
                            'alternative_titles': alt_titles,
                            'type': 'anime'
                        })

            logging.info(f"Gefunden: {len(anime_list)} Animes")
            return anime_list
        except Exception as e:
            logging.error(f"Fehler beim Scrapen der Anime-Liste: {str(e)}")
            return []

    def get_series_list(self) -> List[Dict[str, str]]:
        """Scrape die Liste aller Serien von s.to"""
        url = "http://186.2.175.5/serien"

        try:
            response = self.session.get(url, timeout=10)
            response.raise_for_status()  # Wirft Fehler bei HTTP-Statuscode >= 400

            soup = BeautifulSoup(response.text, 'html.parser')
            series_list = []

            # Finde alle Serien-Links innerhalb von li-Elementen
            for link in soup.select('.seriesList li a'):
                title = link.text.strip()
                url = 'http://186.2.175.5' + link.get('href', '')

                if title and url:  # Nur hinzufügen wenn Titel und URL vorhanden
                    series_list.append({
                        'title': title,
                        'url': url,
                        'type': 'series'
                    })

            return series_list
        except Exception as e:
            logging.error(f"Fehler beim Scrapen der Serien-Liste: {str(e)}")
            return []

    def _get_content_type(self, url: str) -> str:
        """Ermittelt den Content-Typ (Anime/Serie) basierend auf der URL"""
        if "aniworld.to" in url:
            return "Animes"
        return "Serien"

    def _get_series_path(self, series_name: str, url: str) -> str:
        """Erstellt den Pfad für die Serie basierend auf dem Content-Typ"""
        content_type = self._get_content_type(url)
        # Sanitize the series name to avoid invalid directory names
        sanitized_series_name = self._sanitize_directory_name(series_name)
        series_path = os.path.join(self.download_dir, content_type, sanitized_series_name)
        os.makedirs(series_path, exist_ok=True)
        return series_path

    def _process_season(
        self,
        season_url: str,
        series_name: str,
        season_num: int,
        series_path: str,
        job_options: Dict[str, Any],
        episodes: List[Dict[str, Any]],
        selected_episode_numbers: Optional[Set[int]],
        total_tasks: int
    ) -> bool:
        try:
            if not episodes:
                logging.warning(f"Keine Episoden in Staffel {season_num} gefunden")
                return False

            season_dir = os.path.join(series_path, f"Staffel {season_num}")
            os.makedirs(season_dir, exist_ok=True)
            base_url = self.get_base_url(season_url)
            lang_config = self.config.get('scraper', {}).get('language_preference', {})
            allow_german_sub = lang_config.get('allow_german_sub', True)
            season_success = True

            for episode in episodes:
                self.download_status.wait_if_paused()
                if self.download_status.is_cancel_requested():
                    break

                episode_num = episode.get('number')
                if episode_num is None:
                    continue
                try:
                    episode_num = int(episode_num)
                except (TypeError, ValueError):
                    continue

                if selected_episode_numbers is not None and episode_num not in selected_episode_numbers:
                    continue

                episode_title = episode.get('title') or f"Episode {episode_num}"
                episode_url = episode.get('url')
                has_dub = episode.get('has_german_dub', False)
                has_sub = episode.get('has_german_sub', False)

                should_download = False
                language_tag = ''
                if has_dub:
                    should_download = True
                    language_tag = '[GerDub]'
                elif allow_german_sub and has_sub:
                    should_download = True
                    language_tag = '[GerSub]'

                if not should_download:
                    self._emit_event('episode_skipped_language', {
                        'season_num': season_num,
                        'episode_num': episode_num,
                        'title': episode_title,
                        'has_german_dub': has_dub,
                        'has_german_sub': has_sub
                    })
                    continue

                filename = f"S{season_num:02d}E{episode_num:02d} - {episode_title} {language_tag}.mp4"
                filename = self._sanitize_filename(filename)
                output_path = os.path.join(season_dir, filename)
                filename_no_tag = f"S{season_num:02d}E{episode_num:02d} - {episode_title}.mp4"
                filename_no_tag = self._sanitize_filename(filename_no_tag)
                output_path_no_tag = os.path.join(season_dir, filename_no_tag)

                if os.path.exists(output_path_no_tag) and not os.path.exists(output_path):
                    try:
                        os.rename(output_path_no_tag, output_path)
                        logging.info(f"Bestehende Datei angepasst: {os.path.basename(output_path)}")
                    except Exception as rename_error:
                        logging.error(f"Fehler beim Umbenennen bestehender Datei: {rename_error}")

                order_index = self.current_job_meta.get('processed', 0) + 1
                task = DownloadTask(
                    title=episode_title,
                    url='',
                    output_path=output_path,
                    episode_num=episode_num,
                    season_num=season_num,
                    series_title=series_name,
                    episode_url=episode_url,
                    language_tag=language_tag,
                    job_id=self.current_job_id,
                    order_index=order_index,
                    total_count=total_tasks
                )

                if os.path.exists(output_path):
                    file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                    result = self._record_result(task, True, {'skipped': True, 'file_size': file_size})
                    self._emit_event('episode_skipped_existing', result)
                    self.current_job_meta['completed'] = self.current_job_meta.get('completed', 0) + 1
                    processed = self.current_job_meta.get('processed', 0) + 1
                    self.current_job_meta['processed'] = processed
                    total = self.current_job_meta.get('total_episodes', total_tasks)
                    progress = (processed / total) * 100 if total else 0.0
                    status_message = f"{processed}/{total} Episoden verarbeitet" if total else 'Episode übersprungen'
                    self.download_status.update(progress=progress, current_episode=processed, total_episodes=total, status_message=status_message)
                    continue

                if not episode_url:
                    task.last_error = 'Episode URL fehlt'
                    self._record_result(task, False, {'error': task.last_error})
                    self._emit_event('episode_failed', {'season_num': season_num, 'episode_num': episode_num, 'title': episode_title, 'error': task.last_error})
                    season_success = False
                    processed = self.current_job_meta.get('processed', 0) + 1
                    self.current_job_meta['processed'] = processed
                    total = self.current_job_meta.get('total_episodes', total_tasks)
                    progress = (processed / total) * 100 if total else 0.0
                    self.download_status.update(progress=progress, current_episode=processed, total_episodes=total, status_message='Episode übersprungen')
                    continue

                stream_urls = self.extract_stream_urls(episode_url, base_url)
                if not stream_urls:
                    task.last_error = 'Keine Video-URLs gefunden'
                    self._record_result(task, False, {'error': task.last_error})
                    self._emit_event('episode_failed', {'season_num': season_num, 'episode_num': episode_num, 'title': episode_title, 'error': task.last_error})
                    season_success = False
                    processed = self.current_job_meta.get('processed', 0) + 1
                    self.current_job_meta['processed'] = processed
                    total = self.current_job_meta.get('total_episodes', total_tasks)
                    progress = (processed / total) * 100 if total else 0.0
                    self.download_status.update(progress=progress, current_episode=processed, total_episodes=total, status_message='Keine Streams gefunden')
                    continue

                mirror_success = False
                for mirror_index, stream_url in enumerate(stream_urls, start=1):
                    self.download_status.wait_if_paused()
                    if self.download_status.is_cancel_requested():
                        break

                    task.url = stream_url
                    self.download_status.update(
                        episode_title=episode_title,
                        current_episode=order_index,
                        total_episodes=total_tasks,
                        status_message=f"Lade {episode_title} (Mirror {mirror_index}/{len(stream_urls)})",
                        state='running'
                    )
                    mirror_success = self._download_video(task, max_retries=3)
                    if mirror_success:
                        file_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                        extra_data = dict(task.last_result or {})
                        extra_data.setdefault('file_size', file_size)
                        extra_data['mirror'] = mirror_index
                        result = self._record_result(task, True, extra_data)
                        self._emit_event('episode_downloaded', result)
                        task.last_result = None
                        self.current_job_meta['completed'] = self.current_job_meta.get('completed', 0) + 1
                        break
                    else:
                        error_message = task.last_error or 'Unbekannter Fehler'
                        self._emit_event('mirror_failed', {'season_num': season_num, 'episode_num': episode_num, 'title': episode_title, 'mirror_index': mirror_index, 'error': error_message})

                if not mirror_success:
                    if self.download_status.is_cancel_requested():
                        season_success = False
                    else:
                        season_success = False
                        error_message = task.last_error or 'Alle Mirrors fehlgeschlagen'
                        self._record_result(task, False, {'error': error_message})
                        self._emit_event('episode_failed', {'season_num': season_num, 'episode_num': episode_num, 'title': episode_title, 'error': error_message})

                processed = self.current_job_meta.get('processed', 0) + 1
                self.current_job_meta['processed'] = processed
                total = self.current_job_meta.get('total_episodes', total_tasks)
                progress = (processed / total) * 100 if total else 0.0
                status_message = f"{processed}/{total} Episoden verarbeitet" if total else 'Fortschritt aktualisiert'
                self.download_status.update(progress=progress, current_episode=processed, total_episodes=total, status_message=status_message)

                if self.download_status.is_cancel_requested():
                    break

            return season_success
        except Exception as e:
            logging.error(f"Fehler beim Verarbeiten von Staffel {season_num}: {str(e)}", exc_info=True)
            self._emit_event('season_error', {'season': season_num, 'series': series_name, 'error': str(e)})
            return False
    def get_base_url(self, url: str) -> str:
        """Extrahiert die Basis-URL aus der gegebenen URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _rotate_user_agent(self):
        """Rotate user agent to avoid detection."""
        self.session.headers["User-Agent"] = random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36'
        ])
        self.session.headers.update(self.session.headers)

    def _process_download_tasks(self, tasks):
        """Verarbeitet eine Liste von Download-Tasks parallel"""
        if not tasks:
            return

        logging.info(f"\nStarte {len(tasks)} Downloads...")

        # Optimierte Konfiguration für parallele Downloads
        SAFE_DOWNLOAD_THRESHOLD = 50  # Erhöhter Schwellenwert
        SAFETY_DELAY = 10  # Reduzierte Wartezeit
        GROUP_SIZE = 15  # Größere Gruppen

        if len(tasks) > SAFE_DOWNLOAD_THRESHOLD:
            logging.info(f"\nSicherheitsmodus aktiviert: {len(tasks)} Episoden werden in Gruppen heruntergeladen")
            logging.info(f"Gruppengröße: {GROUP_SIZE} Episoden, Wartezeit zwischen Gruppen: {SAFETY_DELAY} Sekunden")

            # Teile Tasks in Gruppen auf
            task_groups = [tasks[i:i + GROUP_SIZE] for i in range(0, len(tasks), GROUP_SIZE)]
            total_groups = len(task_groups)
            total_tasks = len(tasks)
            completed_tasks = 0

            for group_index, task_group in enumerate(task_groups, 1):
                logging.info(f"\nStarte Gruppe {group_index}/{total_groups} ({len(task_group)} Episoden)")

                # Aktualisiere Status
                self.download_status.update(
                    status_message=f"Gruppe {group_index}/{total_groups} - {completed_tasks}/{total_tasks} Episoden"
                )

                # Prüfe ob Abbruch angefordert wurde
                if self.download_status.is_cancel_requested():
                    logging.info("Download abgebrochen durch Benutzer")
                    self.download_status.update(status_message="Download abgebrochen")
                    self.download_status.finish_download()
                    return

                # Verarbeite aktuelle Gruppe
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_parallel_downloads) as executor:
                    future_to_task = {
                        executor.submit(self._download_video, task): task
                        for task in task_group
                    }

                    # Verarbeite die Ergebnisse der Gruppe
                    for future in concurrent.futures.as_completed(future_to_task):
                        task = future_to_task[future]
                        completed_tasks += 1
                        try:
                            success = future.result()
                            status = "Erfolg" if success else "Fehlgeschlagen"
                            logging.info(f"[{completed_tasks}/{total_tasks}] {task.title}: {status}")
                        except Exception as e:
                            logging.error(f"[{completed_tasks}/{total_tasks}] {task.title}: Fehler - {str(e)}")

                # Reduzierte Wartezeit zwischen Gruppen
                if group_index < total_groups:
                    logging.info(f"\nKurze Pause von {SAFETY_DELAY} Sekunden vor der nächsten Gruppe...")
                    self.download_status.update(
                        status_message=f"Kurze Pause zwischen Gruppen ({SAFETY_DELAY}s)..."
                    )
                    time.sleep(SAFETY_DELAY)
        else:
            # Normaler Download für weniger als SAFE_DOWNLOAD_THRESHOLD Episoden
            total_tasks = len(tasks)
            completed_tasks = 0

            # Prüfe ob Abbruch angefordert wurde
            if self.download_status.is_cancel_requested():
                logging.info("Download abgebrochen durch Benutzer")
                self.download_status.update(status_message="Download abgebrochen")
                self.download_status.finish_download()
                return

            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_parallel_downloads) as executor:
                future_to_task = {
                    executor.submit(self._download_video, task): task
                    for task in tasks
                }

                # Verarbeite die Ergebnisse
                for future in concurrent.futures.as_completed(future_to_task):
                    task = future_to_task[future]
                    completed_tasks += 1
                    try:
                        success = future.result()
                        status = "Erfolg" if success else "Fehlgeschlagen"
                        logging.info(f"[{completed_tasks}/{total_tasks}] {task.title}: {status}")
                    except Exception as e:
                        logging.error(f"[{completed_tasks}/{total_tasks}] {task.title}: Fehler - {str(e)}")

    def download_direct_voe(self, voe_url: str, output_filename: str = None):
        """
        Downloads a video directly from a VOE.sx link using Real-Debrid.

        Args:
            voe_url: Direct link to the VOE.sx video
            output_filename: Optional custom filename for the downloaded video
        """
        if not self.real_debrid:
            raise ValueError("Real-Debrid is not configured. Please add your API key to the config file.")

        if not output_filename:
            # Generate a timestamp-based filename if none provided
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            output_filename = f"voe_download_{timestamp}.mp4"

        # Ensure the filename has .mp4 extension
        if not output_filename.lower().endswith('.mp4'):
            output_filename += '.mp4'

        output_path = os.path.join(self.download_dir, output_filename)

        # Update download status
        self.download_status.start_download()
        self.download_status.update(
            title=output_filename,
            progress=0,
            current_episode=1,
            total_episodes=1,
            status_message="Starting download from VOE.sx"
        )

        try:
            # Get unrestricted link from Real-Debrid
            unrestricted_link = self.real_debrid.unrestrict_link(voe_url)

            # Create and process download task
            task = DownloadTask(
                url=unrestricted_link,
                output_path=output_path,
                title=output_filename
            )

            self._download_video(task)

            self.download_status.update(
                progress=100,
                status_message="Download completed successfully"
            )
            logging.info(f"Successfully downloaded video to: {output_path}")

        except Exception as e:
            error_msg = str(e)
            if "Unsupported URL" in error_msg:
                self.log_unsupported_url(voe_url, error_msg)
            self.download_status.update(status_message=f"Error during download: {str(e)}")
            logging.error(f"Error downloading VOE.sx video: {str(e)}")
            raise

        finally:
            self.download_status.finish_download()

if __name__ == "__main__":
    # Get download directory from user
    default_dir = "D:/Serien"
    download_dir = input(f"Enter download directory (default: {default_dir}): ").strip()
    if not download_dir:
        download_dir = default_dir

    # Get thread counts from user
    try:
        download_threads = int(input("Enter number of parallel downloads (default: 5): ").strip() or "5")
        extraction_threads = int(input("Enter number of parallel URL extractions (default: 8): ").strip() or "8")
    except ValueError:
        logging.warning("Invalid input, using defaults")
        download_threads = 5
        extraction_threads = 8

    # Create scraper with custom settings
    scraper = StreamScraper(
        download_dir=download_dir,
        max_parallel_downloads=download_threads,
        max_parallel_extractions=extraction_threads
    )

    # Ask user for download type
    print("\nSelect download type:")
    print("1. Download series from s.to or aniworld.to")
    print("2. Download direct VOE.sx link")
    choice = input("Enter your choice (1 or 2): ").strip()

    if choice == "1":
        # Get series URL from user
        url = input("Enter series URL (from s.to or aniworld.to): ").strip()
        scraper.start_download(url)
    elif choice == "2":
        # Get VOE.sx link and optional filename
        voe_url = input("Enter VOE.sx link: ").strip()
        filename = input("Enter output filename (optional, press Enter for automatic name): ").strip()
        scraper.download_direct_voe(voe_url, filename if filename else None)
    else:
        print("Invalid choice. Please select 1 or 2.")
