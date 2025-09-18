"""
Gemini API Client für die Verbesserung von Metadaten und Inhaltsanalyse.
"""

import os
import logging
import requests
import json
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

class GeminiClient:
    """Client für die Interaktion mit der Google Gemini API."""

    def __init__(self, api_key: str, model: str = "gemini-1.5-pro-latest"):
        """
        Initialisiere den Gemini API Client.

        Args:
            api_key (str): Der API-Schlüssel für die Gemini API
            model (str): Das zu verwendende Modell (Standard: gemini-1.5-pro-latest)
        """
        self.api_key = api_key
        self.model = model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.enabled = bool(api_key)

        if self.enabled:
            logger.info(f"Gemini API Client initialisiert mit Modell: {model}")
        else:
            logger.warning("Gemini API Client deaktiviert (kein API-Schlüssel)")

    def generate_content(self, prompt: str, max_tokens: int = 1024) -> Optional[str]:
        """
        Generiere Inhalte mit der Gemini API.

        Args:
            prompt (str): Der Prompt für die Generierung
            max_tokens (int): Maximale Anzahl der Tokens in der Antwort

        Returns:
            Optional[str]: Die generierte Antwort oder None bei Fehler
        """
        if not self.enabled:
            logger.warning("Gemini API ist nicht aktiviert")
            return None

        # Liste der zu versuchenden Modelle, beginnend mit dem konfigurierten Modell
        models_to_try = [self.model]

        # Füge weitere Modelle hinzu, wenn sie nicht bereits in der Liste sind
        fallback_models = ["gemini-2.5-pro-exp-03-25", "gemini-1.5-pro-latest", "gemini-1.5-flash-latest"]
        for model_name in fallback_models:
            if model_name != self.model and model_name not in models_to_try:
                models_to_try.append(model_name)

        for model in models_to_try:
            try:
                url = f"{self.base_url}/models/{model}:generateContent?key={self.api_key}"

                logger.info(f"Versuche Anfrage mit Modell: {model}")

                payload = {
                    "contents": [
                        {
                            "parts": [
                                {
                                    "text": prompt
                                }
                            ]
                        }
                    ],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "temperature": 0.4,
                        "topP": 0.95,
                        "topK": 40
                    }
                }

                response = requests.post(url, json=payload)
                response.raise_for_status()

                data = response.json()

                # Extrahiere den generierten Text
                if "candidates" in data and len(data["candidates"]) > 0:
                    candidate = data["candidates"][0]
                    if "content" in candidate and "parts" in candidate["content"]:
                        parts = candidate["content"]["parts"]
                        if parts and "text" in parts[0]:
                            logger.info(f"Erfolgreich Antwort von Modell {model} erhalten")
                            return parts[0]["text"]

                logger.warning(f"Unerwartetes Antwortformat von Modell {model}: {data}")

            except Exception as e:
                logger.error(f"Fehler bei der Gemini API-Anfrage mit Modell {model}: {str(e)}")
                # Wenn es ein 400 oder 429 Fehler ist, versuche das nächste Modell
                if isinstance(e, requests.exceptions.HTTPError) and (e.response.status_code == 400 or e.response.status_code == 429):
                    logger.info(f"Versuche nächstes Modell nach Fehler mit {model}")
                    continue

        logger.error("Alle Modelle fehlgeschlagen")
        return None

    def enhance_series_metadata(self, title: str, existing_info: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Verbessere die Metadaten einer Serie mit Hilfe der Gemini API.

        Args:
            title (str): Der Titel der Serie
            existing_info (Dict[str, Any]): Vorhandene Informationen zur Serie

        Returns:
            Dict[str, Any]: Verbesserte Metadaten
        """
        if not self.enabled:
            return existing_info or {}

        try:
            # Erstelle einen Prompt für die Metadaten-Verbesserung
            prompt = f"""
            Ich benötige detaillierte Informationen zur Serie/Anime "{title}".
            Bitte gib mir folgende Informationen im JSON-Format zurück:

            - Originaltitel
            - Alternativtitel (falls vorhanden)
            - Kurzbeschreibung (max. 200 Zeichen)
            - Ausführliche Beschreibung
            - Genre (Liste)
            - Erscheinungsjahr
            - Produktionsland
            - Sprachen
            - Bewertung (auf einer Skala von 1-10)
            - Anzahl der Staffeln (falls bekannt)
            - Anzahl der Episoden (falls bekannt)
            - Ist es ein Anime? (true/false)
            - Altersfreigabe

            Gib die Antwort nur als valides JSON-Objekt zurück, ohne zusätzlichen Text.
            """

            response = self.generate_content(prompt)

            if not response:
                return existing_info or {}

            # Versuche, die Antwort als JSON zu parsen
            try:
                # Extrahiere nur den JSON-Teil aus der Antwort
                json_start = response.find('{')
                json_end = response.rfind('}') + 1

                if json_start >= 0 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    metadata = json.loads(json_str)

                    # Füge vorhandene Informationen hinzu, wenn sie nicht in der Antwort enthalten sind
                    if existing_info:
                        for key, value in existing_info.items():
                            if key not in metadata:
                                metadata[key] = value

                    return metadata
                else:
                    logger.warning(f"Konnte kein JSON in der Antwort finden: {response}")
                    return existing_info or {}

            except json.JSONDecodeError as e:
                logger.error(f"Fehler beim Parsen der JSON-Antwort: {str(e)}")
                logger.debug(f"Antwort: {response}")
                return existing_info or {}

        except Exception as e:
            logger.error(f"Fehler bei der Metadaten-Verbesserung: {str(e)}")
            return existing_info or {}

    def analyze_episode_content(self, series_title: str, episode_title: str, season_num: int, episode_num: int) -> Dict[str, Any]:
        """
        Analysiere den Inhalt einer Episode und generiere eine Zusammenfassung.

        Args:
            series_title (str): Der Titel der Serie
            episode_title (str): Der Titel der Episode
            season_num (int): Die Staffelnummer
            episode_num (int): Die Episodennummer

        Returns:
            Dict[str, Any]: Analyseergebnisse mit Zusammenfassung
        """
        if not self.enabled:
            return {"summary": ""}

        try:
            # Erstelle einen Prompt für die Episodenanalyse
            prompt = f"""
            Bitte erstelle eine kurze Zusammenfassung für die Episode "{episode_title}" (S{season_num:02d}E{episode_num:02d})
            der Serie/des Animes "{series_title}".

            Die Zusammenfassung sollte folgendes enthalten:
            1. Eine kurze Inhaltsangabe (max. 3 Sätze)
            2. Wichtige Handlungspunkte
            3. Relevante Charakterentwicklung

            Gib die Antwort als JSON-Objekt mit den Feldern "summary", "plot_points" (Array) und "character_development" (Array) zurück.
            """

            response = self.generate_content(prompt)

            if not response:
                return {"summary": ""}

            # Versuche, die Antwort als JSON zu parsen
            try:
                # Extrahiere nur den JSON-Teil aus der Antwort
                json_start = response.find('{')
                json_end = response.rfind('}') + 1

                if json_start >= 0 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    analysis = json.loads(json_str)
                    return analysis
                else:
                    # Fallback: Verwende die gesamte Antwort als Zusammenfassung
                    return {"summary": response.strip()}

            except json.JSONDecodeError:
                # Fallback: Verwende die gesamte Antwort als Zusammenfassung
                return {"summary": response.strip()}

        except Exception as e:
            logger.error(f"Fehler bei der Episodenanalyse: {str(e)}")
            return {"summary": ""}

    def suggest_similar_content(self, title: str, genres: List[str] = None) -> List[Dict[str, Any]]:
        """
        Schlage ähnliche Inhalte basierend auf dem Titel und den Genres vor.

        Args:
            title (str): Der Titel der Serie/des Animes
            genres (List[str]): Die Genres der Serie/des Animes

        Returns:
            List[Dict[str, Any]]: Liste ähnlicher Inhalte
        """
        if not self.enabled:
            return []

        try:
            # Erstelle einen Prompt für Vorschläge ähnlicher Inhalte
            genres_str = ", ".join(genres) if genres else "unbekannt"
            prompt = f"""
            Basierend auf der Serie/dem Anime "{title}" mit den Genres [{genres_str}],
            schlage bitte 5 ähnliche Serien oder Animes vor.

            Gib die Antwort als JSON-Array zurück, wobei jedes Element folgende Felder hat:
            - title: Der Titel der Serie/des Animes
            - type: "anime" oder "series"
            - genres: Ein Array von Genres
            - year: Das Erscheinungsjahr
            - similarity_reason: Ein kurzer Grund, warum diese Serie ähnlich ist

            Gib nur das JSON-Array zurück, ohne zusätzlichen Text.
            """

            response = self.generate_content(prompt)

            if not response:
                return []

            # Versuche, die Antwort als JSON zu parsen
            try:
                # Extrahiere nur den JSON-Teil aus der Antwort
                json_start = response.find('[')
                json_end = response.rfind(']') + 1

                if json_start >= 0 and json_end > json_start:
                    json_str = response[json_start:json_end]
                    suggestions = json.loads(json_str)
                    return suggestions
                else:
                    logger.warning(f"Konnte kein JSON-Array in der Antwort finden: {response}")
                    return []

            except json.JSONDecodeError as e:
                logger.error(f"Fehler beim Parsen der JSON-Antwort: {str(e)}")
                logger.debug(f"Antwort: {response}")
                return []

        except Exception as e:
            logger.error(f"Fehler bei der Generierung von Vorschlägen: {str(e)}")
            return []
