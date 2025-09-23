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
from typing import List, Tuple, Optional, Dict, Any
from yt_dlp import YoutubeDL
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
    def __init__(self):
        """Initialisiere den Download-Status mit Thread-sicherem Lock"""
        self.is_downloading = False
        self.current_title = ""
        self.progress = 0
        self.total_episodes = 0
        self.current_episode = 0
        self.status_message = ""
        self._lock = threading.Lock()  # Thread-sicherer Lock für Statusaktualisierungen
        self.cancel_requested = False  # Flag für Abbruch-Anforderung

    def update(self, title="", progress=None, current_episode=None, total_episodes=None, status_message=""):
        """Aktualisiere den Status thread-sicher"""
        with self._lock:  # Verwende den Lock, um Race Conditions zu vermeiden
            if title:
                self.current_title = title
            if progress is not None:
                self.progress = progress
            if current_episode is not None:
                self.current_episode = current_episode
            if total_episodes is not None:
                self.total_episodes = total_episodes
            if status_message:
                self.status_message = status_message

    def get_status(self) -> Dict[str, Any]:
        """Hole den aktuellen Status thread-sicher"""
        with self._lock:  # Verwende den Lock beim Zugriff auf den Status
            return {
                'is_downloading': self.is_downloading,
                'current_title': self.current_title,
                'progress': self.progress,
                'current_episode': self.current_episode,
                'total_episodes': self.total_episodes,
                'status_message': self.status_message,
                'cancel_requested': self.cancel_requested
            }

    def start_download(self):
        """Markiere den Download als gestartet"""
        with self._lock:
            self.is_downloading = True
            self.progress = 0
            self.current_episode = 0
            self.status_message = "Download gestartet"

    def finish_download(self):
        """Markiere den Download als beendet"""
        with self._lock:
            self.is_downloading = False
            self.progress = 100
            self.cancel_requested = False
            self.status_message = "Download abgeschlossen"

    def request_cancel(self):
        """Fordert den Abbruch des Downloads an"""
        with self._lock:
            if self.is_downloading:
                self.cancel_requested = True
                self.status_message = "Abbruch angefordert..."
                return True
            return False

    def is_cancel_requested(self):
        """Prüft ob ein Abbruch angefordert wurde"""
        with self._lock:
            return self.cancel_requested

class RealDebrid:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.real-debrid.com/rest/1.0"
        self.headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        self.is_premium = self.check_premium()
        if self.is_premium:
            logging.info("Real-Debrid Premium Account aktiv")
        else:
            logging.warning("Real-Debrid Account ist kein Premium-Account")

    def check_premium(self) -> bool:
        """Überprüft ob der Account Premium hat"""
        try:
            response = requests.get(
                f"{self.base_url}/user",
                headers=self.headers
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("premium", 0) > 0
            return False
        except Exception as e:
            logging.error(f"Fehler beim Prüfen des Premium-Status: {str(e)}")
            return False

    def unrestrict_link(self, link: str, max_retries: int = 3) -> Optional[str]:
        """Konvertiert einen Hoster-Link in einen direkten Download-Link"""
        retries = 0
        while retries < max_retries:
            try:
                if retries > 0:
                    # Exponentielles Backoff: 10s, 20s, 40s...
                    wait_time = 10 * (2 ** (retries - 1))
                    logging.info(f"Warte {wait_time}s vor Real-Debrid Versuch {retries + 1}/{max_retries}")
                    time.sleep(wait_time)

                response = requests.post(
                    f"{self.base_url}/unrestrict/link",
                    headers=self.headers,
                    data={"link": link}
                )

                if response.status_code == 503:
                    logging.warning("Real-Debrid Server überlastet (503)")
                    retries += 1
                    continue

                if response.status_code == 200:
                    data = response.json()
                    download_url = data.get("download")
                    if download_url:
                        # Prüfe ob die Download-URL erreichbar ist
                        head_response = requests.head(download_url, timeout=10)
                        if head_response.status_code == 200:
                            return download_url
                        logging.warning(f"Download-URL nicht erreichbar (Status: {head_response.status_code})")
                    else:
                        logging.warning("Keine Download-URL in Real-Debrid Antwort")
                else:
                    error_data = None
                    try:
                        error_data = response.json()
                    except:
                        logging.error(f"Real-Debrid API Fehler: {response.status_code}")
                        logging.error(f"API Antwort: {response.content}")

                    if error_data and error_data.get("error") == "unavailable_file":
                        logging.warning("Diese Datei ist bei Real-Debrid nicht verfügbar")
                        logging.warning("Wechsle zu normalem Download...")
                        return None
                    elif error_data:
                        logging.error(f"Real-Debrid API Fehler: {error_data}")

                retries += 1

            except requests.exceptions.RequestException as e:
                logging.error(f"Verbindungsfehler zu Real-Debrid: {str(e)}")
                retries += 1
                continue
            except Exception as e:
                logging.error(f"Unerwarteter Fehler bei Real-Debrid: {str(e)}")
                retries += 1
                continue

        return None

@dataclass
class DownloadTask:
    url: str
    output_path: str
    title: str
    episode_num: int = 0

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
            # Check if cancel was requested
            if self.download_status.is_cancel_requested():
                logging.info(f"Download abgebrochen für: {task.title}")
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
                                    return False

                        continue

                # Download mit yt-dlp
                ydl_opts = {
                    'format': 'best',
                    'outtmpl': task.output_path,
                    'quiet': True,
                    'no_warnings': True,
                    'extractor_args': {'youtube': {'player_skip': ['js', 'configs', 'webpage']}},
                }

                try:
                    with YoutubeDL(ydl_opts) as ydl:
                        ydl.download([task.url])
                    logging.info(f"Download erfolgreich: {task.title}")
                    
                    # Language Guard: Prüfe deutsche Audiospur
                    if self._verify_german_audio(task.output_path, task.title):
                        return True
                    else:
                        # Datei entspricht nicht den Sprachanforderungen
                        logging.warning(f"Datei {task.title} entspricht nicht den deutschen Sprachanforderungen")
                        return False

                except Exception as e:
                    error_msg = str(e)
                    if "Unsupported URL" in error_msg:
                        self.log_unsupported_url(task.url, error_msg)
                    logging.error(f"yt-dlp Fehler: {error_msg}")
                    if "Video unavailable" in error_msg:
                        logging.warning("Video nicht mehr verfügbar")
                        return False
                    raise  # Re-raise für andere Fehler

            except Exception as e:
                logging.error(f"Download-Fehler: {str(e)}")
                retries += 1
                if retries >= max_retries:
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

    def process_series(self, url: str):
        """Verarbeitet eine Serie mit paralleler Staffel-Verarbeitung"""
        try:
            logging.info(f"Starte Verarbeitung von {url}")
            # Rufe die Startseite der Serie ab
            response = self.make_request(url)
            if not response:
                return False

            soup = BeautifulSoup(response.text, 'html.parser')
            base_url = self.get_base_url(url)

            # Extrahiere Seriennamen
            series_name = self._extract_series_name(url)
            series_path = self._get_series_path(series_name, url)

            # Erstelle Serienverzeichnis
            os.makedirs(series_path, exist_ok=True)

            # Hole alle Staffeln
            seasons = self._extract_seasons(soup, base_url, url)
            if not seasons:
                logging.warning(f"Keine Staffeln für {series_name} gefunden")
                return False

            # Sortiere Staffeln nach Nummer
            seasons.sort(key=lambda s: s.get('number', 0))
            logging.info(f"\nGefunden: {len(seasons)} Staffeln für {series_name}")

            # Verarbeite Staffeln parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_parallel_extractions) as executor:
                future_to_season = {
                    executor.submit(
                        self._process_season,
                        season['url'],
                        series_name,
                        season['number'],
                        self._get_series_path(series_name, url)
                    ): season['number']
                    for season in seasons
                }

                # Verarbeite die Ergebnisse
                completed_seasons = 0
                new_episodes_found = False
                for future in concurrent.futures.as_completed(future_to_season):
                    season_num = future_to_season[future]
                    completed_seasons += 1
                    try:
                        success = future.result()
                        if success:
                            new_episodes_found = True
                        status = "Erfolg" if success else "Fehlgeschlagen oder keine neuen Episoden"
                        logging.info(f"[{completed_seasons}/{len(seasons)}] Staffel {season_num}: {status}")
                    except Exception as e:
                        logging.error(f"[{completed_seasons}/{len(seasons)}] Staffel {season_num}: Fehler - {str(e)}")

            logging.info(f"\nAlle Staffeln von {series_name} wurden verarbeitet")

            # Aktualisiere Jellyfin wenn aktiviert
            if self.jellyfin:
                logging.info("Starte Jellyfin Bibliotheks-Scan...")
                self.jellyfin.refresh_libraries()
                logging.info("Jellyfin Bibliotheks-Scan wurde gestartet")

            return True

        except Exception as e:
            logging.error(f"Fehler beim Verarbeiten der Serie: {str(e)}")
            return False

    def start_download(self, url: str):
        """Haupteinstiegspunkt für den Download."""
        if self.download_status.is_downloading:
            raise Exception("Es läuft bereits ein Download!")

        try:
            self.download_status.start_download()
            self.download_status.update(status_message="Starte Download...")
            self.process_series(url)
        except Exception as e:
            self.download_status.update(status_message=f"Fehler: {str(e)}")
            # Versuche Session zurückzusetzen bei Fehlern
            self.reset_session()
            raise
        finally:
            self.download_status.finish_download()

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

    def _process_season(self, url: str, series_name: str, season_num: int, series_path: str) -> bool:
        """Verarbeitet eine einzelne Staffel. Gibt True zurück wenn neue Episoden gefunden wurden."""
        try:
            # Verwende die erweiterte Episode-Extraktions-Methode
            episodes = self._extract_episodes(url, self.get_base_url(url))

            if not episodes:
                logging.warning(f"Keine Episoden in Staffel {season_num} gefunden")
                return False

            # Erstelle Staffel-Verzeichnis
            season_dir = os.path.join(series_path, f"Staffel {season_num}")
            os.makedirs(season_dir, exist_ok=True)

            # Hole Spracheinstellungen aus der Konfiguration
            lang_config = self.config.get("scraper", {}).get("language_preference", {})
            prefer_german_dub = lang_config.get("prefer_german_dub", True)  # Bevorzuge deutschen Ton
            allow_german_sub = lang_config.get("allow_german_sub", True)    # Erlaube deutschen Untertitel als Fallback

            # Prüfe welche Episoden neu sind
            new_episodes = []
            skipped_count = 0
            skipped_no_german_count = 0

            for episode in episodes:
                episode_num = episode["number"]
                episode_url = episode["url"]
                episode_title = episode["title"]
                has_german_dub = episode.get("has_german_dub", False)
                has_german_sub = episode.get("has_german_sub", False)

                # Entscheide basierend auf Spracheinstellungen
                should_download = False
                lang_tag = ""

                if has_german_dub:
                    # Deutschen Ton immer herunterladen wenn verfügbar
                    should_download = True
                    lang_tag = "[GerDub]"
                elif has_german_sub and allow_german_sub:
                    # Deutschen Untertitel nur herunterladen, wenn erlaubt und kein deutscher Ton verfügbar
                    should_download = True
                    lang_tag = "[GerSub]"

                if not should_download:
                    skipped_no_german_count += 1
                    logging.info(f"Überspringe Episode ohne deutsche Tonspur/Untertitel: S{season_num:02d}E{episode_num:02d} - {episode_title}")
                    continue

                # Erstelle Dateinamen mit Sprach-Tag
                filename = f"S{season_num:02d}E{episode_num:02d} - {episode_title} {lang_tag}.mp4"
                filename = self._sanitize_filename(filename)
                output_path = os.path.join(season_dir, filename)

                # Überspringe bereits heruntergeladene Episoden
                if os.path.exists(output_path):
                    skipped_count += 1
                    continue

                # Prüfe auch, ob eine Version ohne Tag existiert
                filename_no_tag = f"S{season_num:02d}E{episode_num:02d} - {episode_title}.mp4"
                filename_no_tag = self._sanitize_filename(filename_no_tag)
                output_path_no_tag = os.path.join(season_dir, filename_no_tag)

                if os.path.exists(output_path_no_tag):
                    # Wenn eine Version ohne Tag existiert, umbenennen statt neu herunterladen
                    logging.info(f"Datei ohne Sprach-Tag gefunden, benenne um: {filename_no_tag} -> {filename}")
                    try:
                        os.rename(output_path_no_tag, output_path)
                        skipped_count += 1
                        continue
                    except Exception as e:
                        logging.error(f"Fehler beim Umbenennen: {str(e)}")

                new_episodes.append((episode_num, episode_url, episode_title, output_path))

            total_episodes = len(episodes)
            if not new_episodes:
                german_dub_count = sum(1 for ep in episodes if ep.get("has_german_dub", False))
                german_sub_count = sum(1 for ep in episodes if ep.get("has_german_sub", False))

                if german_dub_count == 0 and (not allow_german_sub or german_sub_count == 0):
                    logging.info(f"Keine Episoden mit deutscher Tonspur/Untertitel in Staffel {season_num} gefunden")
                else:
                    logging.info(f"Alle verfügbaren Episoden mit deutscher Tonspur/Untertitel bereits heruntergeladen")

                return False

            logging.info(f"Gefunden: {total_episodes} Episoden in Staffel {season_num}")
            logging.info(f"Davon mit deutschem Ton: {sum(1 for ep in episodes if ep.get('has_german_dub', False))}")
            logging.info(f"Davon mit deutschem Untertitel: {sum(1 for ep in episodes if ep.get('has_german_sub', False))}")
            logging.info(f"Überspringe {skipped_count} existierende Episoden")
            logging.info(f"Überspringe {skipped_no_german_count} Episoden ohne deutsche Tonspur/Untertitel")
            logging.info(f"Lade {len(new_episodes)} neue Episoden herunter")

            # Erstelle Download-Tasks für neue Episoden
            download_tasks = []
            failed_downloads = []

            for episode_num, episode_url, episode_title, output_path in new_episodes:
                logging.info(f"Bereite vor: {os.path.basename(output_path)}")

                # Hole Video-URLs
                stream_urls = self.extract_stream_urls(episode_url, self.get_base_url(url))
                if not stream_urls:
                    logging.warning(f"Keine Video-URLs gefunden für Episode {episode_num}")
                    failed_downloads.append(f"S{season_num:02d}E{episode_num:02d} - {episode_title}")
                    continue

                # Versuche alle Mirrors nacheinander bis Language Guard OK sagt
                success = False
                for mirror_idx, stream_url in enumerate(stream_urls):
                    logging.debug(f"Versuche Mirror {mirror_idx + 1}/{len(stream_urls)} für {episode_title}")
                    task = DownloadTask(
                        title=episode_title,
                        url=stream_url,
                        output_path=output_path,
                        episode_num=episode_num
                    )
                    if self._download_video(task, max_retries=3):
                        success = True
                        logging.debug(f"✓ Erfolgreicher Download mit Mirror {mirror_idx + 1}: {episode_title}")
                        break
                    else:
                        logging.warning(f"✗ Mirror {mirror_idx + 1} fehlgeschlagen für {episode_title}")
                
                if not success:
                    failed_downloads.append(f"S{season_num:02d}E{episode_num:02d} - {episode_title}")
                    logging.error(f"Alle Mirrors fehlgeschlagen für {episode_title}")

            # Zeige fehlgeschlagene Downloads
            if failed_downloads:
                logging.warning("\nFehlgeschlagene Downloads:")
                for failed in failed_downloads:
                    logging.warning(f"- {failed}")

            return True

        except Exception as e:
            logging.error(f"Fehler beim Verarbeiten von Staffel {season_num}: {str(e)}")
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
