# ScaP Roadmap

ScaP wird vorerst ausschließlich lokal betrieben. Deshalb liegt der Fokus
zunächst auf zuverlässiger Funktion, stabilen Downloads und einer guten
Bedienung. Sicherheits-Hardening wird eingeplant, bevor ScaP für Zugriffe aus
dem Netzwerk geöffnet wird.

## Aktueller Update-Plan

- **Erledigt:** Design-Grundlage sowie zentrale Download-, Konfigurations- und
  Spracherkennungsfehler
- **PR #6:** Bibliotheks-Synchronisation reparieren und ungenutzte Pakete
  `lxml` sowie `google-generativeai` entfernen
- **Dieser PR:** vollständige persistente Serien-Queue mit Auswahl,
  Episodenfortschritt, Pause/Fortsetzen, Verlauf, Abbrechen und Wiederholen
- **Als Nächstes:** verfügbare Qualität, Audio und Untertitel vor dem Start
  anzeigen sowie vorhandene Episoden und Speicherbedarf vorab prüfen

## Priorität 1: Bugs und Stabilität

- reproduzierbare Start-, Datenbank- und Bibliotheksfehler beheben
- Downloadabbrüche und fehlgeschlagene Quellen verständlich anzeigen
- Wiederholungsversuche und Fehlerzustände zuverlässig behandeln
- für jeden behobenen Kernfehler einen Regressionstest ergänzen
- Abhängigkeiten klein, nachvollziehbar und reproduzierbar halten

## Priorität 2: Persistente Download-Queue

- [x] mehrere Serien vormerken
- [x] Zustände `pending`, `downloading`, `paused`, `completed`, `failed` und `cancelled`
- [x] Abbrechen und Wiederholen
- [x] Queue und Verlauf nach einem Neustart wiederherstellen
- [x] Pause und Fortsetzen
- [x] Fortschritt pro Episode zusätzlich zum gesamten Auftrag
- [x] Staffel- und Episodenauswahl vor dem Download

## Priorität 3: Download-Workflow

- Staffel und Episoden vor dem Start auswählen
- verfügbare Qualität, Audio und Untertitel anzeigen
- Zielbibliothek festlegen
- bereits vorhandene Episoden erkennen und überspringen
- benötigten Speicherplatz vor dem Start prüfen

## Priorität 4: Mediathek

- Cover, Beschreibungen und weitere Metadaten übersichtlich darstellen
- nach Typ, Sprache, Status und Bibliothek filtern
- fehlende Episoden erkennen
- Jellyfin nach erfolgreichen Downloads aktualisieren

## Priorität 5: Architektur und Tests

- `app.py` in Routen und Services aufteilen
- doppelte Download-Endpunkte zusammenführen
- Scraper als austauschbare Quellen-Adapter organisieren
- API-, Datenbank- und Queue-Tests ausbauen
- automatische Prüfungen in GitHub Actions ausführen

## Später: Sicherheit vor Netzwerkbetrieb

- Secrets und lokale Konfiguration vom Repository trennen
- Authentifizierung und CSRF-Schutz ergänzen
- erlaubte Origins und Netzwerkzugriff einschränken
- Eingaben und Dateipfade stärker validieren
