"""
Datenbank-Modul für die Verwaltung von vorhandenen Serien und Animes.
"""

import os
import logging
import sqlite3
import json
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

class MediaDatabase:
    """Datenbank für die Verwaltung von vorhandenen Serien und Animes."""

    def __init__(self, db_path: str = "media.db"):
        """
        Initialisiere die Datenbank.

        Args:
            db_path (str): Pfad zur Datenbank-Datei
        """
        self.db_path = db_path
        self._create_tables()

    def _get_connection(self) -> sqlite3.Connection:
        """
        Erstelle eine robuste Datenbankverbindung mit WAL-Modus und Timeouts.

        Returns:
            sqlite3.Connection: Datenbankverbindung
        """
        conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        cursor = conn.cursor()

        # Aktiviere WAL-Modus für bessere Robustheit
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")

        return conn

    def _create_tables(self) -> None:
        """Erstelle die benötigten Tabellen, falls sie nicht existieren."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Tabelle für Serien/Animes
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            type TEXT NOT NULL,
            url TEXT UNIQUE NOT NULL,
            directory TEXT NOT NULL,
            description TEXT,
            genres TEXT,
            year INTEGER,
            rating REAL,
            poster_url TEXT,
            metadata_json TEXT,
            ai_enhanced BOOLEAN DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Tabelle für Staffeln
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER NOT NULL,
            season_number INTEGER NOT NULL,
            directory TEXT NOT NULL,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (media_id) REFERENCES media (id) ON DELETE CASCADE,
            UNIQUE (media_id, season_number)
        )
        ''')

        # Tabelle für Episoden
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season_id INTEGER NOT NULL,
            episode_number INTEGER NOT NULL,
            title TEXT,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER,
            has_german_dub BOOLEAN DEFAULT 0,
            has_german_sub BOOLEAN DEFAULT 0,
            summary TEXT,
            plot_points TEXT,
            ai_enhanced BOOLEAN DEFAULT 0,
            download_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (season_id) REFERENCES seasons (id) ON DELETE CASCADE,
            UNIQUE (season_id, episode_number)
        )
        ''')

        conn.commit()
        conn.close()

    def add_media(self, title: str, media_type: str, url: str, directory: str) -> int:
        """
        Füge eine neue Serie oder Anime zur Datenbank hinzu.

        Args:
            title (str): Titel der Serie/des Animes
            media_type (str): Typ ('series' oder 'anime')
            url (str): URL der Serie/des Animes
            directory (str): Verzeichnis, in dem die Serie/der Anime gespeichert ist

        Returns:
            int: ID des hinzugefügten Eintrags
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT OR REPLACE INTO media (title, type, url, directory, last_updated) VALUES (?, ?, ?, ?, ?)",
                (title, media_type, url, directory, datetime.now())
            )
            media_id = cursor.lastrowid
            conn.commit()
            return media_id
        except Exception as e:
            logger.error(f"Fehler beim Hinzufügen von {title}: {str(e)}")
            conn.rollback()
            return -1
        finally:
            conn.close()

    def add_season(self, media_id: int, season_number: int, directory: str) -> int:
        """
        Füge eine neue Staffel zur Datenbank hinzu.

        Args:
            media_id (int): ID der Serie/des Animes
            season_number (int): Staffelnummer
            directory (str): Verzeichnis, in dem die Staffel gespeichert ist

        Returns:
            int: ID des hinzugefügten Eintrags
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT OR REPLACE INTO seasons (media_id, season_number, directory, last_updated) VALUES (?, ?, ?, ?)",
                (media_id, season_number, directory, datetime.now())
            )
            season_id = cursor.lastrowid
            conn.commit()
            return season_id
        except Exception as e:
            logger.error(f"Fehler beim Hinzufügen von Staffel {season_number}: {str(e)}")
            conn.rollback()
            return -1
        finally:
            conn.close()

    def add_episode(self, season_id: int, episode_number: int, title: str, filename: str,
                   file_path: str, file_size: int = 0, has_german_dub: bool = False,
                   has_german_sub: bool = False) -> int:
        """
        Füge eine neue Episode zur Datenbank hinzu.

        Args:
            season_id (int): ID der Staffel
            episode_number (int): Episodennummer
            title (str): Titel der Episode
            filename (str): Dateiname
            file_path (str): Vollständiger Dateipfad
            file_size (int): Dateigröße in Bytes
            has_german_dub (bool): Hat deutsche Synchronisation
            has_german_sub (bool): Hat deutsche Untertitel

        Returns:
            int: ID des hinzugefügten Eintrags
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """INSERT OR REPLACE INTO episodes
                   (season_id, episode_number, title, filename, file_path, file_size,
                    has_german_dub, has_german_sub, download_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (season_id, episode_number, title, filename, file_path, file_size,
                 has_german_dub, has_german_sub, datetime.now())
            )
            episode_id = cursor.lastrowid
            conn.commit()
            return episode_id
        except Exception as e:
            logger.error(f"Fehler beim Hinzufügen von Episode {episode_number}: {str(e)}")
            conn.rollback()
            return -1
        finally:
            conn.close()

    def get_media_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        """
        Hole Informationen zu einer Serie/einem Anime anhand der URL.

        Args:
            url (str): URL der Serie/des Animes

        Returns:
            Optional[Dict[str, Any]]: Informationen zur Serie/zum Anime oder None
        """
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM media WHERE url = ?", (url,))
        result = cursor.fetchone()

        conn.close()

        if result:
            return dict(result)
        return None

    def get_media_by_id(self, media_id: int) -> Optional[Dict[str, Any]]:
        """
        Hole Informationen zu einer Serie/einem Anime anhand der ID.

        Args:
            media_id (int): ID der Serie/des Animes

        Returns:
            Optional[Dict[str, Any]]: Informationen zur Serie/zum Anime oder None
        """
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM media WHERE id = ?", (media_id,))
        result = cursor.fetchone()

        conn.close()

        if result:
            return dict(result)
        return None

    def get_season_by_id(self, season_id: int) -> Optional[Dict[str, Any]]:
        """
        Hole Informationen zu einer Staffel anhand der ID.

        Args:
            season_id (int): ID der Staffel

        Returns:
            Optional[Dict[str, Any]]: Informationen zur Staffel oder None
        """
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM seasons WHERE id = ?", (season_id,))
        result = cursor.fetchone()

        conn.close()

        if result:
            return dict(result)
        return None

    def get_episode_by_id(self, episode_id: int) -> Optional[Dict[str, Any]]:
        """
        Hole Informationen zu einer Episode anhand der ID.

        Args:
            episode_id (int): ID der Episode

        Returns:
            Optional[Dict[str, Any]]: Informationen zur Episode oder None
        """
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        result = cursor.fetchone()

        conn.close()

        if result:
            return dict(result)
        return None

    def get_seasons_by_media_id(self, media_id: int) -> List[Dict[str, Any]]:
        """
        Hole alle Staffeln einer Serie/eines Animes.

        Args:
            media_id (int): ID der Serie/des Animes

        Returns:
            List[Dict[str, Any]]: Liste der Staffeln
        """
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM seasons WHERE media_id = ? ORDER BY season_number", (media_id,))
        results = cursor.fetchall()

        conn.close()

        return [dict(row) for row in results]

    def get_episodes_by_season_id(self, season_id: int) -> List[Dict[str, Any]]:
        """
        Hole alle Episoden einer Staffel.

        Args:
            season_id (int): ID der Staffel

        Returns:
            List[Dict[str, Any]]: Liste der Episoden
        """
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM episodes WHERE season_id = ? ORDER BY episode_number", (season_id,))
        results = cursor.fetchall()

        conn.close()

        return [dict(row) for row in results]

    def get_episode_by_season_and_number(self, season_id: int, episode_number: int) -> Optional[Dict[str, Any]]:
        """
        Hole Informationen zu einer Episode anhand der Staffel-ID und Episodennummer.

        Args:
            season_id (int): ID der Staffel
            episode_number (int): Episodennummer

        Returns:
            Optional[Dict[str, Any]]: Informationen zur Episode oder None
        """
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM episodes WHERE season_id = ? AND episode_number = ?",
                       (season_id, episode_number))
        result = cursor.fetchone()

        conn.close()

        if result:
            return dict(result)
        return None

    def _sanitize_directory_name(self, directory_name: str) -> str:
        """Sanitize directory name by replacing invalid characters with hyphens."""
        # Remove or replace common website suffixes
        directory_name = directory_name.replace('\n', ' ').replace('\r', ' ')
        directory_name = ' '.join(directory_name.split())  # Replace multiple spaces with single space

        # Replace invalid characters with hyphens
        invalid_chars = r'<>:"/\\|?*'
        for char in invalid_chars:
            directory_name = directory_name.replace(char, '-')

        directory_name = directory_name.strip()

        # Ensure directory name is not too long (Windows has a 255 char limit for full path)
        if len(directory_name) > 240:  # Leave some room for path
            directory_name = directory_name[:240]

        return directory_name

    def scan_directory(self, base_dir: str) -> Tuple[int, int, int]:
        """
        Scanne ein Verzeichnis nach vorhandenen Serien/Animes und füge sie zur Datenbank hinzu.

        Args:
            base_dir (str): Basisverzeichnis, in dem nach Serien/Animes gesucht werden soll

        Returns:
            Tuple[int, int, int]: Anzahl der gefundenen Serien/Animes, Staffeln und Episoden
        """
        if not os.path.exists(base_dir):
            logger.error(f"Verzeichnis {base_dir} existiert nicht")
            return 0, 0, 0

        # Erstelle eine Datenbankverbindung
        conn = self._get_connection()
        cursor = conn.cursor()

        media_count = 0
        season_count = 0
        episode_count = 0

        # Durchsuche das Basisverzeichnis nach Serien und Animes
        for media_type_dir in ['Serien', 'Animes']:
            media_type_path = os.path.join(base_dir, media_type_dir)
            if not os.path.exists(media_type_path):
                continue

            media_type = 'series' if media_type_dir == 'Serien' else 'anime'

            # Durchsuche das Verzeichnis nach Serien/Animes
            for media_name in os.listdir(media_type_path):
                media_dir = os.path.join(media_type_path, media_name)
                if not os.path.isdir(media_dir):
                    continue

                # Erstelle einen Eintrag für die Serie/den Anime
                # URL ist unbekannt, daher verwenden wir einen Platzhalter
                # Sanitize the media name to ensure it's valid for database and future directory creation
                sanitized_media_name = self._sanitize_directory_name(media_name)
                url_placeholder = f"local://{media_type}/{sanitized_media_name}"
                media_id = self.add_media(sanitized_media_name, media_type, url_placeholder, media_dir)

                if media_id > 0:
                    media_count += 1

                    # Durchsuche das Verzeichnis nach Staffeln
                    for season_name in os.listdir(media_dir):
                        if not season_name.lower().startswith('staffel'):
                            continue

                        season_dir = os.path.join(media_dir, season_name)
                        if not os.path.isdir(season_dir):
                            continue

                        # Extrahiere die Staffelnummer
                        try:
                            season_number = int(season_name.lower().replace('staffel', '').strip())
                        except ValueError:
                            season_number = 0

                        # Erstelle einen Eintrag für die Staffel
                        season_id = self.add_season(media_id, season_number, season_dir)

                        if season_id > 0:
                            season_count += 1

                            # Durchsuche das Verzeichnis nach Episoden
                            for filename in os.listdir(season_dir):
                                if not filename.lower().endswith(('.mp4', '.mkv', '.avi')):
                                    continue

                                file_path = os.path.join(season_dir, filename)
                                if not os.path.isfile(file_path):
                                    continue

                                # Extrahiere die Episodennummer und den Titel
                                episode_number = 0
                                episode_title = ""

                                # Versuche, die Episodennummer aus dem Dateinamen zu extrahieren
                                # Format: S01E01 - Titel [GerDub].mp4
                                if 'E' in filename:
                                    try:
                                        episode_part = filename.split('E')[1].split(' ')[0]
                                        episode_number = int(episode_part)
                                    except (IndexError, ValueError):
                                        pass

                                # Versuche, den Titel aus dem Dateinamen zu extrahieren
                                if ' - ' in filename:
                                    try:
                                        # Extrahiere den Teil zwischen ' - ' und ' [' oder Ende des Dateinamens
                                        title_part = filename.split(' - ')[1]
                                        if ' [' in title_part:
                                            episode_title = title_part.split(' [')[0]
                                        else:
                                            # Entferne die Dateiendung
                                            episode_title = title_part.rsplit('.', 1)[0]
                                    except IndexError:
                                        pass

                                # Prüfe, ob die Datei deutsche Synchronisation oder Untertitel hat
                                has_german_dub = '[GerDub]' in filename
                                has_german_sub = '[GerSub]' in filename

                                # Prüfe, ob bereits ein Eintrag für diese Episode existiert
                                # (um doppelte Einträge zu vermeiden)
                                cursor.execute(
                                    "SELECT id FROM episodes WHERE season_id = ? AND episode_number = ?",
                                    (season_id, episode_number)
                                )
                                existing_episode = cursor.fetchone()

                                if existing_episode:
                                    # Aktualisiere den bestehenden Eintrag mit dem neuesten Dateinamen
                                    cursor.execute(
                                        "UPDATE episodes SET filename = ?, file_path = ?, file_size = ?, "
                                        "has_german_dub = ?, has_german_sub = ? WHERE id = ?",
                                        (filename, file_path, file_size, has_german_dub, has_german_sub, existing_episode[0])
                                    )
                                    conn.commit()
                                    logger.info(f"Episodeneintrag aktualisiert: S{season_number:02d}E{episode_number:02d} - {episode_title}")
                                    episode_count += 1
                                    continue

                                # Dateigröße ermitteln
                                file_size = os.path.getsize(file_path)

                                # Erstelle einen Eintrag für die Episode
                                episode_id = self.add_episode(
                                    season_id, episode_number, episode_title, filename, file_path,
                                    file_size, has_german_dub, has_german_sub
                                )

                                if episode_id > 0:
                                    episode_count += 1

        # Schließe die Datenbankverbindung
        conn.close()

        logger.info(f"Scan abgeschlossen: {media_count} Serien/Animes, {season_count} Staffeln, {episode_count} Episoden gefunden")
        return media_count, season_count, episode_count

    def update_media_url(self, media_id: int, url: str) -> bool:
        """
        Aktualisiere die URL einer Serie/eines Animes.

        Args:
            media_id (int): ID der Serie/des Animes
            url (str): Neue URL

        Returns:
            bool: True bei Erfolg, False bei Fehler
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "UPDATE media SET url = ?, last_updated = ? WHERE id = ?",
                (url, datetime.now(), media_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren der URL für Media ID {media_id}: {str(e)}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def update_media_metadata(self, media_id: int, metadata: Dict[str, Any]) -> bool:
        """
        Aktualisiere die Metadaten einer Serie/eines Animes.

        Args:
            media_id (int): ID der Serie/des Animes
            metadata (Dict[str, Any]): Metadaten als Dictionary

        Returns:
            bool: True bei Erfolg, False bei Fehler
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # Extrahiere die wichtigsten Felder aus den Metadaten
            description = metadata.get('ausführliche_beschreibung') or metadata.get('kurzbeschreibung') or ''

            # Konvertiere Genres-Liste in einen String
            genres = metadata.get('genre', [])
            if isinstance(genres, list):
                genres_str = ', '.join(genres)
            else:
                genres_str = str(genres)

            year = metadata.get('erscheinungsjahr', None)
            if year and isinstance(year, str):
                try:
                    year = int(year)
                except ValueError:
                    year = None

            rating = metadata.get('bewertung', None)
            if rating:
                try:
                    rating = float(rating)
                except (ValueError, TypeError):
                    rating = None

            poster_url = metadata.get('poster_url', '')

            # Speichere die vollständigen Metadaten als JSON
            metadata_json = json.dumps(metadata, ensure_ascii=False)

            cursor.execute(
                """UPDATE media SET
                   description = ?,
                   genres = ?,
                   year = ?,
                   rating = ?,
                   poster_url = ?,
                   metadata_json = ?,
                   ai_enhanced = 1,
                   last_updated = ?
                   WHERE id = ?""",
                (description, genres_str, year, rating, poster_url, metadata_json, datetime.now(), media_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren der Metadaten für Media ID {media_id}: {str(e)}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def update_episode_metadata(self, episode_id: int, metadata: Dict[str, Any]) -> bool:
        """
        Aktualisiere die Metadaten einer Episode.

        Args:
            episode_id (int): ID der Episode
            metadata (Dict[str, Any]): Metadaten als Dictionary

        Returns:
            bool: True bei Erfolg, False bei Fehler
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            summary = metadata.get('summary', '')

            # Konvertiere plot_points in einen String, falls es eine Liste ist
            plot_points = metadata.get('plot_points', [])
            if isinstance(plot_points, list):
                plot_points_str = json.dumps(plot_points, ensure_ascii=False)
            else:
                plot_points_str = str(plot_points)

            cursor.execute(
                """UPDATE episodes SET
                   summary = ?,
                   plot_points = ?,
                   ai_enhanced = 1
                   WHERE id = ?""",
                (summary, plot_points_str, episode_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren der Metadaten für Episode ID {episode_id}: {str(e)}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def get_all_media(self) -> List[Dict[str, Any]]:
        """
        Hole alle Serien und Animes aus der Datenbank.

        Returns:
            List[Dict[str, Any]]: Liste aller Serien und Animes
        """
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM media ORDER BY title")
        results = cursor.fetchall()

        conn.close()

        return [dict(row) for row in results]

    def get_media_with_episodes(self) -> List[Dict[str, Any]]:
        """
        Hole alle Serien und Animes mit ihren Staffeln und Episoden.

        Returns:
            List[Dict[str, Any]]: Liste aller Serien und Animes mit Staffeln und Episoden
        """
        media_list = self.get_all_media()

        for media in media_list:
            seasons = self.get_seasons_by_media_id(media['id'])
            media['seasons'] = []

            for season in seasons:
                episodes = self.get_episodes_by_season_id(season['id'])
                season['episodes'] = episodes
                media['seasons'].append(season)

        return media_list

    def get_episode_count(self) -> int:
        """
        Ermittle die Gesamtzahl der Episoden in der Datenbank.

        Returns:
            int: Anzahl der Episoden
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM episodes")
        count = cursor.fetchone()[0]

        conn.close()

        return count

    def get_total_size(self) -> int:
        """
        Ermittle die Gesamtgröße aller Episoden in der Datenbank.

        Returns:
            int: Gesamtgröße in Bytes
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT SUM(file_size) FROM episodes")
        total_size = cursor.fetchone()[0] or 0

        conn.close()

        return total_size

# Singleton-Instanz
_media_db = None

def get_media_db(db_path: str = "media.db") -> MediaDatabase:
    """
    Hole die Singleton-Instanz der MediaDatabase.

    Args:
        db_path (str): Pfad zur Datenbank-Datei

    Returns:
        MediaDatabase: Die Datenbankinstanz
    """
    global _media_db
    if _media_db is None:
        _media_db = MediaDatabase(db_path)
    return _media_db
