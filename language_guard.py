import json
import subprocess
import tempfile
import os
import logging
from pathlib import Path
from config_manager import get_config

# Load configuration
config = get_config()

# -------- Settings (können aus config.json überschrieben werden) ----------
DESIRED_LANG_TAGS = set(map(str.lower, config.get('language.prefer', ["de", "deu", "ger"])))
WHISPER_ACCEPT_639_1 = set(map(str.lower, config.get('language.whisper_accept_639_1', ['de'])))
SAMPLE_SECONDS = config.get('language.sample_seconds', 45)
REMUX_TO_DE_IF_PRESENT = config.get('language.remux_to_de_if_present', True)
VERIFY_WITH_WHISPER = config.get('language.verify_with_whisper', True)
REQUIRE_DUB = config.get('language.require_dub', False)  # wenn True: Subs reichen NICHT

LANG_ISO_EQUIV = {
    "de": {"de", "deu", "ger"},
    "en": {"en", "eng"},
    "ja": {"ja", "jpn"},
}

LANGUAGE_FALLBACK_PRIORITY = [str(lang).lower() for lang in config.get('language.fallback_priority', ['de', 'en', 'ja'])]
# Language Priority - loaded from config with safe defaults
DEFAULT_LANG_PRIORITY = [
    ("de", None),     # Deutsch
    ("en", "de"),     # Englisch mit German Dub (falls du das so modellierst)
    ("en", None),     # Englisch
    ("ja", "de"),     # Japanisch mit German Dub
    ("ja", "en"),     # Japanisch mit English Dub
    ("ja", None),     # Japanisch (Original)
]

def _get_lang_priority() -> list[tuple[str | None, str | None]]:
    """
    Holt die Sprach-Priorität robust:
    - Wenn config ein Objekt mit get_language_priority() ist -> verwende das.
    - Sonst als Dict: 'language.priority' oder Default.
    """
    # Objekt-Methode?
    try:
        if hasattr(config, "get_language_priority"):
            prio = config.get_language_priority()
            if prio:
                return prio
    except Exception:
        pass

    # Dict-Config (mit Punkt-Keys)
    try:
        prio = config.get("language.priority")
        if prio:
            return prio
    except Exception:
        pass

    return DEFAULT_LANG_PRIORITY

# ------------------------ Helpers ----------------------------------------
def _run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

def ffprobe_streams(video_path: str):
    # Nutze absoluten Pfad falls verfügbar
    ffprobe_bin = os.environ.get("FFPROBE_PATH", "ffprobe")
    cmd = [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", video_path]
    p = _run(cmd)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {p.stderr}")
    return json.loads(p.stdout)

def get_duration(meta) -> float:
    return float(meta["format"].get("duration") or 0.0)

def list_streams(meta, kind):
    return [s for s in meta.get("streams", []) if s.get("codec_type") == kind]

def audio_lang_indices(meta, desired=DESIRED_LANG_TAGS):
    hits = []
    for s in list_streams(meta, "audio"):
        tags = s.get("tags", {}) or {}
        lang = (tags.get("language") or s.get("TAG:language") or "").lower()
        if lang in desired:
            hits.append(s["index"])
    return hits

def has_subtitles_in_lang(meta, desired=DESIRED_LANG_TAGS):
    for s in list_streams(meta, "subtitle"):
        tags = s.get("tags", {}) or {}
        lang = (tags.get("language") or s.get("TAG:language") or "").lower()
        if lang in desired:
            return True
    return False

def extract_wav_segment(video_path: str, out_wav: str, start: float, duration: int = SAMPLE_SECONDS, audio_map="a:0"):
    # Nutze absoluten Pfad falls verfügbar
    ffmpeg_bin = os.environ.get("FFMPEG_PATH", "ffmpeg")
    cmd = [
        ffmpeg_bin, "-v", "error",
        "-ss", f"{start:.2f}", "-t", str(duration),
        "-i", video_path, "-map", audio_map,
        "-ac", "1", "-ar", "16000", "-f", "wav", out_wav, "-y"
    ]
    p = _run(cmd)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg extraction failed: {p.stderr}")

def detect_lang_whisper(audio_wav_path: str):
    # Erst schnell: faster-whisper
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("tiny", compute_type="auto")
        segments, info = model.transcribe(audio_wav_path, task="transcribe", vad_filter=True)
        return (info.language or "").lower()
    except Exception:
        # Fallback: openai-whisper
        try:
            import whisper
            m = whisper.load_model("tiny")
            res = m.transcribe(audio_wav_path, task="transcribe", temperature=0.0, no_speech_threshold=0.7)
            return (res.get("language") or "").lower()
        except Exception as e:
            print(f"Whisper detection failed: {e}")
            return ""

def content_language_guess(video_path: str, meta=None, sample_seconds=None) -> str:
    """Nimmt 1–3 Proben (50 %, ggf. 30 % & 70 %) von a:0 und erkennt Sprache."""
    if not VERIFY_WITH_WHISPER:
        return ""
    if meta is None: 
        meta = ffprobe_streams(video_path)
    
    sample_sec = sample_seconds if sample_seconds is not None else SAMPLE_SECONDS
    dur = max(1.0, get_duration(meta))
    positions = [0.50]
    if dur >= 180:  # lange Folgen: mehr Proben
        positions = [0.30, 0.50, 0.70]
    
    with tempfile.TemporaryDirectory() as td:
        for pos in positions:
            start = max(0.0, dur * pos - sample_sec / 2)
            wav = os.path.join(td, f"sample_{int(pos*100)}.wav")
            try:
                extract_wav_segment(video_path, wav, start=start, duration=sample_sec, audio_map="a:0")
                lang = detect_lang_whisper(wav)
                if lang:
                    return lang.lower()
            except Exception as e:
                print(f"Sample extraction failed at {pos}: {e}")
                continue
    return ""

def remux_to_de(video_in: str, meta=None, desired=DESIRED_LANG_TAGS, preferred_suffix: str | None = None) -> str | None:
    if meta is None:
        meta = ffprobe_streams(video_in)
    idxs = audio_lang_indices(meta, desired)
    if not idxs:
        return None

    suffix_token = None
    if preferred_suffix:
        suffix_token = preferred_suffix.lower()
    else:
        if desired:
            ordered = list(desired)
            suffix_token = next((tag for tag in ordered if len(tag) == 2 and tag.isalpha()), None)
            if not suffix_token:
                suffix_token = ordered[0]
    if not suffix_token:
        suffix_token = 'lang'

    out_path = Path(video_in).with_suffix(f'.{suffix_token}.mkv')
    temp_out = out_path.with_suffix(out_path.suffix + '.tmp')

    # Nutze absoluten Pfad falls verfügbar
    ffmpeg_bin = os.environ.get('FFMPEG_PATH', 'ffmpeg')
    if temp_out.exists():
        temp_out.unlink(missing_ok=True)
    cmd = [ffmpeg_bin, '-v', 'error', '-i', video_in, '-map', '0:v:0', '-map', f"0:{idxs[0]}", '-c', 'copy', str(temp_out), '-y']
    p = _run(cmd)
    if p.returncode != 0:
        temp_out.unlink(missing_ok=True)
        return None

    try:
        temp_out.replace(out_path)
    except Exception as exc:
        print(f'Failed to finalize remux: {exc}')
        temp_out.unlink(missing_ok=True)
        return None

    return str(out_path)


# -------------------- Public API -----------------------------------------


def verify_language(
    video_path: str,
    prefer_tags=None,
    require_dub=None,
    sample_seconds=None,
    remux=None,
    accept_langs_639_1: set[str] | None = None,
    reject_subs_only: bool = True
) -> tuple[bool, str, str | None]:
    """Check file language. Returns (ok, detail, fixed_path_or_none)."""
    if prefer_tags is None:
        prefer_sequence = list(DESIRED_LANG_TAGS)
    elif isinstance(prefer_tags, (list, tuple)):
        prefer_sequence = [str(tag).lower() for tag in prefer_tags]
    else:
        prefer_sequence = [str(tag).lower() for tag in prefer_tags]

    desired_tags = set(prefer_sequence) if prefer_sequence else DESIRED_LANG_TAGS
    require_dub_setting = require_dub if require_dub is not None else REQUIRE_DUB
    sample_sec = sample_seconds if sample_seconds is not None else SAMPLE_SECONDS
    remux_setting = remux if remux is not None else REMUX_TO_DE_IF_PRESENT
    if accept_langs_639_1 is None:
        whisper_sequence = list(WHISPER_ACCEPT_639_1)
    else:
        whisper_sequence = [str(tag).lower() for tag in accept_langs_639_1]
    allowed_whisper = set(whisper_sequence)
    preferred_suffix = next(iter(whisper_sequence), None)

    try:
        meta = ffprobe_streams(video_path)
    except Exception as e:
        return False, f"ffprobe-error: {e}", None

    audio_idxs = audio_lang_indices(meta, desired_tags)
    if audio_idxs:
        if remux_setting:
            out = remux_to_de(video_path, meta, desired_tags, preferred_suffix)
            if out:
                return True, "tag-match-remuxed", out
        return True, "tag-match", None

    if reject_subs_only and has_subtitles_in_lang(meta, desired_tags):
        return False, "subs-only", None

    if require_dub_setting:
        return False, "dub-required", None

    mismatch_detail = "unknown"
    if VERIFY_WITH_WHISPER:
        lang = content_language_guess(video_path, meta, sample_sec)
        lang_lower = lang.lower() if lang else ""
        if lang_lower and lang_lower in allowed_whisper:
            return True, f"content-match:{lang_lower}", None
        mismatch_detail = lang_lower or "unknown"
    else:
        mismatch_detail = "whisper-disabled"

    if VERIFY_WITH_WHISPER and remux_setting:
        out = remux_to_de(video_path, meta, desired_tags, preferred_suffix)
        if out:
            lang2 = content_language_guess(out, sample_seconds=sample_sec)
            lang2_lower = lang2.lower() if lang2 else ""
            if lang2_lower and lang2_lower in allowed_whisper:
                return True, "accepted-after-remux", out

    return False, f"mismatch:{mismatch_detail}", None

def audit_and_retry(download_func, candidate_urls: list[str]) -> tuple[str | None, str]:
    """
    Lädt nacheinander URLs, prüft jede Datei, akzeptiert die erste korrekte.
    download_func(url) -> gespeicherter Dateipfad
    Rückgabe: (final_path or None, detail)
    """
    for url in candidate_urls:
        try:
            path = download_func(url)
            if not path or not os.path.exists(path):
                continue
                
            ok, detail, fixed = verify_language(path)
            final_path = fixed or path
            if ok:
                return final_path, detail
            
            # unbrauchbare Datei aufräumen
            try:
                Path(path).unlink(missing_ok=True)
                if fixed and fixed != path:
                    Path(fixed).unlink(missing_ok=True)
            except Exception:
                pass
        except Exception as e:
            print(f"Download failed for {url}: {e}")
            continue
    
    return None, "no-valid-de-source"




def audit_and_retry_with_priority(
    download_func,
    candidate_urls: list[str],
    audio_priority: list[str] = ["de", "en", "ja"]
) -> tuple[str | None, str]:
    """
    Testet URLs gegen eine Sprach-Priorität. Für jede Zielsprache:
    - prüft ffprobe-Tags (de/en/ja Aliase)
    - lehnt 'nur Subs' ab
    - nutzt Whisper zur Verifikation
    - remuxt ggf. auf gewünschte Spur
    """
    priority = list(audio_priority) if audio_priority else list(LANGUAGE_FALLBACK_PRIORITY)

    for target in priority:
        tagset = {tag.lower() for tag in LANG_ISO_EQUIV.get(target, {target})}
        whisper_accept = {target.lower()}

        for url in candidate_urls:
            try:
                path = download_func(url)
                if not path or not os.path.exists(path):
                    continue

                ok, detail, fixed = verify_language(
                    path,
                    prefer_tags=tagset,
                    require_dub=False,
                    accept_langs_639_1=whisper_accept,
                    reject_subs_only=True
                )
                final_path = fixed or path
                if ok:
                    logging.info("✓ [%s] akzeptiert: %s (file=%s)", target, detail, final_path)
                    return final_path, f"{target}:{detail}"

                logging.warning("✗ [%s] abgelehnt: %s (url=%s, file=%s)", target, detail, url, path)
                try:
                    Path(path).unlink(missing_ok=True)
                    if fixed and fixed != path:
                        Path(fixed).unlink(missing_ok=True)
                except Exception:
                    pass

            except Exception as exc:
                logging.warning("[%s] Download failed for %s: %s", target, url, exc)
                continue

    return None, "no-valid-source-any-lang"

# ==================== NEW: Episode Variant Language Guard ====================

import re
import requests
from typing import Iterable, List, Optional, Tuple, Dict, Any
from models import EpisodeVariant

# 1) Language Priority (Audio, Dub) - loaded from config
# Note: Using the robust _get_lang_priority() function defined above

# 2) Enhanced patterns for language detection from titles/labels/tracks
LANG_MAP = {
    "de": [
        r"\bde\b", r"\bger\b", r"german", r"deutsch", r"dub(?:bed)?\s*de", r"ger(?:man)?\s*dub",
        r"deutsch(?:e|er)?\s*dub", r"ger\s*dub", r"de\s*dub", r"deu\b", r"deutsch",
        r"🇩🇪", r"flag.*de", r"deutschland", r"gerdub", r"ger-dub", r"de-dub",
        r"\bomu\b.*de", r"om[uü]\s*de", r"original.*untertiteln", r"omut", r"omu"
    ],
    "en": [
        r"\ben\b", r"engl(?:ish|isch)", r"eng", r"en\s*dub", r"english\s*dub",
        r"eng\s*dub", r"english", r"englisch", r"🇺🇸", r"flag.*en", r"usa",
        r"english.*dub", r"eng.*dub", r"subbed.*en", r"sub.*en"
    ],
    "ja": [
        r"\bja\b", r"jap(?:anese|anisch)", r"jp", r"jpn", r"japanese", r"japanisch",
        r"🇯🇵", r"flag.*jp", r"japan", r"jap", r"nihongo", r"jpn\b", r"japanese.*dub",
        r"jap.*dub", r"subbed.*ja", r"sub.*ja", r"omu.*ja", r"om[uü]\s*ja"
    ],
    "fr": [r"\bfr\b", r"french", r"französisch", r"fra\b", r"français", r"🇫🇷", r"flag.*fr"],
    "es": [r"\bes\b", r"spanish", r"spanisch", r"spa\b", r"español", r"🇪🇸", r"flag.*es"],
    "it": [r"\bit\b", r"italian", r"italienisch", r"ita\b", r"italiano", r"🇮🇹", r"flag.*it"],
    "pt": [r"\bpt\b", r"portuguese", r"portugiesisch", r"por\b", r"português", r"🇵🇹", r"flag.*pt"],
    "ru": [r"\bru\b", r"russian", r"russisch", r"rus\b", r"русский", r"🇷🇺", r"flag.*ru"],
    "ko": [r"\bko\b", r"korean", r"koreanisch", r"kor\b", r"한국어", r"🇰🇷", r"flag.*ko"],
    "zh": [r"\bzh\b", r"chinese", r"chinesisch", r"chi\b", r"中文", r"🇨🇳", r"flag.*zh"],
}

DUB_PATTERNS = {
    "de": [
        r"german\s*dub", r"ger\s*dub", r"de\s*dub", r"deutsch(?:e|er)?\s*dub",
        r"gerdub", r"ger-dub", r"de-dub", r"deutsch.*dub", r"german.*dub",
        r"dubbed.*german", r"dubbed.*deutsch", r"dub.*ger", r"dub.*de",
        r"🇩🇪.*dub", r"flag.*de.*dub"
    ],
    "en": [
        r"english\s*dub", r"eng\s*dub", r"en\s*dub", r"english.*dub", r"eng.*dub",
        r"dubbed.*english", r"dubbed.*eng", r"dub.*en", r"dub.*english",
        r"🇺🇸.*dub", r"flag.*en.*dub"
    ],
    "ja": [
        r"japanese\s*dub", r"jap\s*dub", r"ja\s*dub", r"japanese.*dub", r"jap.*dub",
        r"dubbed.*japanese", r"dubbed.*jap", r"dub.*ja", r"dub.*japanese",
        r"🇯🇵.*dub", r"flag.*jp.*dub"
    ],
    "fr": [r"french\s*dub", r"fra\s*dub", r"fr\s*dub", r"french.*dub", r"dub.*french"],
    "es": [r"spanish\s*dub", r"spa\s*dub", r"es\s*dub", r"spanish.*dub", r"dub.*spanish"],
    "it": [r"italian\s*dub", r"ita\s*dub", r"it\s*dub", r"italian.*dub", r"dub.*italian"],
    "pt": [r"portuguese\s*dub", r"por\s*dub", r"pt\s*dub", r"portuguese.*dub", r"dub.*portuguese"],
    "ru": [r"russian\s*dub", r"rus\s*dub", r"ru\s*dub", r"russian.*dub", r"dub.*russian"],
    "ko": [r"korean\s*dub", r"kor\s*dub", r"ko\s*dub", r"korean.*dub", r"dub.*korean"],
    "zh": [r"chinese\s*dub", r"chi\s*dub", r"zh\s*dub", r"chinese.*dub", r"dub.*chinese"],
}

# Additional patterns for special cases
SPECIAL_PATTERNS = {
    "omu": [r"omu", r"om[uü]", r"original.*untertiteln", r"original.*subtitles"],
    "dubbed": [r"dubbed", r"dub", r"vertont", r"synchronisiert"],
    "subbed": [r"subbed", r"sub", r"untertitelt", r"mit.*untertiteln"],
    "original": [r"original", r"orig", r"omu", r"om[uü]", r"ohne.*dub"],
}

def _match_any(text: str, patterns: List[str]) -> bool:
    """Check if any pattern matches the text."""
    t = text.lower()
    return any(re.search(p, t, flags=re.IGNORECASE) for p in patterns)

def guess_audio_and_dub(label: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Try to guess audio_lang & dub_lang from a free-form label/title/track name.
    Enhanced version with better pattern matching and edge case handling.
    """
    if not label or not isinstance(label, str):
        return None, None

    label_l = label.lower().strip()

    # Clean up common formatting issues
    label_l = re.sub(r'[\[\]{}()\'"]', ' ', label_l)  # Remove brackets and quotes
    label_l = re.sub(r'\s+', ' ', label_l)  # Normalize whitespace

    # Detect special cases first
    special_indicators = {}

    for special_type, patterns in SPECIAL_PATTERNS.items():
        if _match_any(label_l, patterns):
            special_indicators[special_type] = True

    # Detect dub language (target language dub)
    dub_lang: Optional[str] = None
    for lang, pats in DUB_PATTERNS.items():
        if _match_any(label_l, pats):
            dub_lang = lang
            break

    # Detect audio language
    audio_lang: Optional[str] = None
    for lang, pats in LANG_MAP.items():
        if _match_any(label_l, pats):
            audio_lang = lang
            break

    # Enhanced logic for edge cases

    # Case 1: "OmU" (Original mit Untertiteln) - audio is original, subtitles in target language
    if special_indicators.get("omu"):
        if dub_lang == "de":
            # German subtitles, audio is likely Japanese for anime
            audio_lang = "ja"
        elif not audio_lang:
            # If no specific audio language detected, assume it's the original
            # For OmU, we typically don't set audio_lang as it's the original language
            pass

    # Case 2: Multiple language mentions
    # If we have both audio and dub detected, validate consistency
    if audio_lang and dub_lang:
        # For anime: Japanese audio with German dub is common
        if audio_lang == "ja" and dub_lang == "de":
            pass  # This is a valid combination
        # For western content: English audio with German dub
        elif audio_lang == "en" and dub_lang == "de":
            pass  # This is also valid
        else:
            # If inconsistent, prefer the more specific detection
            # Keep both but log the ambiguity
            pass

    # Case 3: Only dub detected, infer audio language
    if dub_lang and not audio_lang:
        # Common patterns for anime
        if "anime" in label_l or "japan" in label_l or "manga" in label_l:
            audio_lang = "ja"  # Assume Japanese audio for anime
        elif "cartoon" in label_l or "animation" in label_l:
            audio_lang = "en"  # Assume English audio for western animation

    # Case 4: Handle quality indicators that might be confused with languages
    quality_indicators = ["1080p", "720p", "480p", "4k", "hd", "sd", "bluray", "webrip"]
    for quality in quality_indicators:
        if quality in label_l and audio_lang:
            # Don't let quality indicators override language detection
            pass

    # Case 5: Handle regional variants
    if audio_lang == "de":
        # Check for specific German variants
        if _match_any(label_l, [r"österreich", r"austrian", r"at\b"]):
            audio_lang = "de-at"  # Austrian German
        elif _match_any(label_l, [r"schweiz", r"swiss", r"ch\b"]):
            audio_lang = "de-ch"  # Swiss German

    return audio_lang, dub_lang

def guess_subtitles(label: str) -> List[str]:
    """
    Try to guess subtitle languages from a label/title/track name.
    """
    if not label or not isinstance(label, str):
        return []

    label_l = label.lower().strip()
    label_l = re.sub(r'[\[\]{}()\'"]', ' ', label_l)
    label_l = re.sub(r'\s+', ' ', label_l)

    subtitles = []

    # Check for OmU (Original mit Untertiteln) patterns
    if _match_any(label_l, SPECIAL_PATTERNS["omu"]):
        # OmU typically means subtitles in the viewer's language
        # Check for specific subtitle language indicators
        for lang, patterns in LANG_MAP.items():
            if _match_any(label_l, patterns):
                subtitles.append(lang)

    # Check for explicit subtitle mentions
    if _match_any(label_l, SPECIAL_PATTERNS["subbed"]):
        for lang, patterns in LANG_MAP.items():
            if _match_any(label_l, patterns):
                subtitles.append(lang)

    # Check for multiple subtitle indicators
    subtitle_patterns = [
        r"subs?\s*[:\-]?\s*([a-z]{2})",
        r"untertiteln?\s*[:\-]?\s*([a-z]{2})",
        r"subtitle[s]?\s*[:\-]?\s*([a-z]{2})",
    ]

    for pattern in subtitle_patterns:
        matches = re.findall(pattern, label_l)
        for match in matches:
            if len(match) == 2:  # ISO language code
                subtitles.append(match)

    return list(set(subtitles))  # Remove duplicates

def tag_variant(variant: EpisodeVariant) -> EpisodeVariant:
    """
    Try to normalize language information from title/extra fields.
    Enhanced version with subtitle detection and better edge case handling.
    """
    candidates = [
        variant.title or "",
        variant.extra.get("label", ""),
        variant.extra.get("audio_label", ""),
        variant.extra.get("track_name", ""),
    ]
    audio, dub = variant.audio_lang, variant.dub_lang
    subs = list(variant.subs) if variant.subs else []

    for c in candidates:
        if not c:
            continue
        a, d = guess_audio_and_dub(c)
        s = guess_subtitles(c)

        if a and not audio:
            audio = a
        if d and not dub:
            dub = d
        if s and not subs:
            subs.extend(s)

    # Enhanced correction logic

    # Case 1: OmU detection - if we detected OmU, ensure subtitles are set
    candidates_text = " ".join(candidates)
    if _match_any(candidates_text.lower(), SPECIAL_PATTERNS["omu"]):
        if not subs and dub:  # If we have dub language, subtitles are likely in that language
            subs.append(dub)
        elif not subs:  # Fallback: assume German subtitles for OmU
            subs.append("de")

    # Case 2: If "german dub" in label but audio_lang==None:
    # For western shows, audio is often directly "de" (finished dub).
    # For anime, audio is often "ja" + dub "de".
    # We leave this open as scraper may provide real track info.

    # Case 3: Handle inconsistent language combinations
    if audio and dub:
        # If audio is Japanese and dub is German, this is likely anime
        if audio == "ja" and dub == "de":
            pass  # Valid combination
        # If audio is English and dub is German, this is likely western content
        elif audio == "en" and dub == "de":
            pass  # Valid combination
        else:
            # For other combinations, we might want to be more careful
            # but for now, we trust the detection
            pass

    variant.audio_lang = audio
    variant.dub_lang = dub
    variant.subs = subs
    return variant

def normalize_variants(variants: Iterable[EpisodeVariant]) -> List[EpisodeVariant]:
    """Normalize a list of episode variants."""
    return [tag_variant(v) for v in variants]

def pick_best(variants: Iterable[EpisodeVariant]) -> Optional[EpisodeVariant]:
    """
    Select the best variant according to configured language priority.
    """
    vs = list(variants)
    prio = _get_lang_priority()
    if not prio or not vs:
        return vs[0] if vs else None

    # Exact matches of priorities
    for (a_pref, d_pref) in prio:
        for v in vs:
            if v.audio_lang == a_pref and v.dub_lang == d_pref:
                return v

    # Tolerance: If dub_lang unknown (None) but label suggests dub (edge cases),
    # try fallback matches on audio_lang only:
    for (a_pref, d_pref) in prio:
        if d_pref is None:
            for v in vs:
                if v.audio_lang == a_pref:
                    return v
    return vs[0] if vs else None

def sort_by_preference(variants: Iterable[EpisodeVariant]) -> List[EpisodeVariant]:
    """Sort variants by language preference."""
    lang_priority = _get_lang_priority()

    def rank(v: EpisodeVariant) -> int:
        for idx, (a_pref, d_pref) in enumerate(lang_priority):
            if v.audio_lang == a_pref and v.dub_lang == d_pref:
                return idx
        # Second-best heuristic (audio_lang only)
        for idx, (a_pref, d_pref) in enumerate(lang_priority):
            if d_pref is None and v.audio_lang == a_pref:
                return idx + 100
        return 999
    return sorted(variants, key=rank)

def pick_best_with_quality(variants: Iterable[EpisodeVariant]) -> Optional[EpisodeVariant]:
    """
    Pick best variant with quality consideration.
    First by language preference, then by quality within same language group.
    """
    ordered = sort_by_preference(variants)
    by_pref = {}
    for v in ordered:
        key = (v.audio_lang, v.dub_lang)
        by_pref.setdefault(key, []).append(v)

    # Quality order preference
    quality_order = ["2160p", "1440p", "1080p", "720p", "480p", "360p"]
    def qrank(q):
        q = (q or "").lower()
        try:
            return quality_order.index(q)
        except ValueError:
            return 999

    for _, lst in by_pref.items():
        return sorted(lst, key=lambda v: qrank(v.quality))[0]
    return ordered[0] if ordered else None


# ==================== M3U8/HLS Track Parsing Support ====================

def parse_m3u8_playlist(playlist_url: str, headers: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """
    Parse an M3U8 playlist and extract audio track information.

    Args:
        playlist_url: URL to the M3U8 playlist
        headers: Optional headers for the request

    Returns:
        Dict containing playlist info and audio tracks, or None if parsing fails
    """
    try:
        base_headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        if headers:
            base_headers.update(headers)
        response = requests.get(playlist_url, headers=base_headers, timeout=10)
        response.raise_for_status()

        playlist_content = response.text
        lines = playlist_content.split('\n')

        playlist_info = {
            'master_playlist': False,
            'audio_tracks': [],
            'subtitles': []
        }

        current_track = None
        track_type = None

        for line in lines:
            line = line.strip()
            if not line or line.startswith('#EXTM3U'):
                if '#EXTM3U' in line:
                    playlist_info['master_playlist'] = True
                continue

            # Parse EXT-X-MEDIA tags (audio/subtitle tracks)
            if line.startswith('#EXT-X-MEDIA:'):
                track_info = _parse_ext_x_media(line)
                if track_info:
                    track_type = track_info.get('TYPE')
                    if track_type == 'AUDIO':
                        playlist_info['audio_tracks'].append(track_info)
                    elif track_type == 'SUBTITLES':
                        playlist_info['subtitles'].append(track_info)

            # Parse EXT-X-STREAM-INF tags (video streams)
            elif line.startswith('#EXT-X-STREAM-INF:'):
                current_track = _parse_ext_x_stream_inf(line)

        return playlist_info

    except Exception as e:
        print(f"Error parsing M3U8 playlist: {e}")
        return None

def _parse_ext_x_media(line: str) -> Optional[Dict[str, Any]]:
    """Parse an EXT-X-MEDIA tag."""
    track_info = {}

    # Split by comma and parse key=value pairs
    parts = line.replace('#EXT-X-MEDIA:', '').split(',')

    for part in parts:
        if '=' in part:
            key, value = part.split('=', 1)
            key = key.strip()
            value = value.strip('"\'')
            track_info[key] = value

    return track_info if track_info else None

def _parse_ext_x_stream_inf(line: str) -> Optional[Dict[str, Any]]:
    """Parse an EXT-X-STREAM-INF tag."""
    stream_info = {}

    # Split by comma and parse key=value pairs
    parts = line.replace('#EXT-X-STREAM-INF:', '').split(',')

    for part in parts:
        if '=' in part:
            key, value = part.split('=', 1)
            key = key.strip()
            value = value.strip('"\'')
            stream_info[key] = value

    return stream_info if stream_info else None

def extract_audio_info_from_m3u8(playlist_url: str, headers: Optional[dict] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Gibt (audio_lang, dub_lang) zurück. In HLS beschreibt LANGUAGE die Sprache der
    Audiospur selbst. Ein 'German Dub' ist daher LANGUAGE='de' -> audio_lang='de', dub_lang=None.
    """
    info = parse_m3u8_playlist(playlist_url, headers)
    if not info:
        return None, None

    audio_lang = None
    dub_lang = None  # In HLS nicht verlässlich ableitbar

    # Nimm bevorzugt eine DE-Spur, sonst EN, sonst JA (oder erste verfügbare)
    preferred = ["de", "en", "ja"]

    tracks = info.get("audio_tracks", [])
    # Normalisiere Sprache
    def norm(lang: str | None) -> str | None:
        if not lang:
            return None
        l = lang.lower()
        aliases = {"deu":"de", "ger":"de", "eng":"en", "jpn":"ja"}
        return aliases.get(l, l)

    # 1) Versuche gezielt preferred
    for pref in preferred:
        for t in tracks:
            L = norm(t.get("LANGUAGE"))
            if L == pref:
                return L, None

    # 2) Fallback erste Spur mit Sprache
    for t in tracks:
        L = norm(t.get("LANGUAGE"))
        if L:
            return L, None

    return None, None

def enhance_variant_with_m3u8_info(variant: EpisodeVariant, headers: Optional[dict] = None) -> EpisodeVariant:
    """
    Enhance an EpisodeVariant with information from M3U8 playlist if URL is M3U8.

    Args:
        variant: The EpisodeVariant to enhance
        headers: Optional headers for the request

    Returns:
        Enhanced EpisodeVariant
    """
    if not (variant.url.endswith('.m3u8') or '/hls/' in variant.url):
        return variant

    try:
        # Use the corrected extract_audio_info_from_m3u8 function
        a, d = extract_audio_info_from_m3u8(variant.url)
        if a and not variant.audio_lang:
            variant.audio_lang = a
        if d and not variant.dub_lang:
            variant.dub_lang = d
        variant.extra['m3u8_parsed'] = True
    except Exception as e:
        variant.extra['m3u8_parse_error'] = str(e)
    return variant

def normalize_variants_with_m3u8(variants: Iterable[EpisodeVariant], headers: Optional[dict] = None) -> List[EpisodeVariant]:
    """
    Normalize variants and enhance M3U8 variants with playlist information.

    Args:
        variants: List of EpisodeVariant objects
        headers: Optional headers for M3U8 requests

    Returns:
        List of normalized and enhanced EpisodeVariant objects
    """
    normalized = normalize_variants(variants)

    # Enhance M3U8 variants with playlist information
    enhanced = []
    for variant in normalized:
        if variant.url.endswith('.m3u8') or '/hls/' in variant.url:
            enhanced_variant = enhance_variant_with_m3u8_info(variant, headers)
            enhanced.append(enhanced_variant)
        else:
            enhanced.append(variant)

    return enhanced