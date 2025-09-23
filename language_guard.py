import json
import subprocess
import tempfile
import os
from pathlib import Path
from config_manager import get_config

# Load configuration
config = get_config()

# -------- Settings (können aus config.json überschrieben werden) ----------
DESIRED_LANG_TAGS = set(config.get('language.prefer', ["de", "deu", "ger"]))
WHISPER_ACCEPT_639_1 = {"de"}                # Whisper liefert 'de' (639-1)
SAMPLE_SECONDS = config.get('language.sample_seconds', 45)
REMUX_TO_DE_IF_PRESENT = config.get('language.remux_to_de_if_present', True)
VERIFY_WITH_WHISPER = config.get('language.verify_with_whisper', True)
REQUIRE_DUB = config.get('language.require_dub', True)  # wenn True: Subs reichen NICHT

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

def remux_to_de(video_in: str, meta=None, desired=DESIRED_LANG_TAGS) -> str | None:
    if meta is None:
        meta = ffprobe_streams(video_in)
    idxs = audio_lang_indices(meta, desired)
    if not idxs:
        return None

    out_path = Path(video_in).with_suffix('.de.mkv')
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
def verify_language(video_path: str, prefer_tags=None, require_dub=None, sample_seconds=None, remux=None) -> tuple[bool, str, str | None]:
    """
    Prüft Datei. Rückgabe: (ok, detail, fixed_path_or_none)
    ok=True  -> akzeptiert
    ok=False -> ablehnen
    fixed_path_or_none -> Pfad zur remuxten Datei, falls angefallen
    """
    # Use provided parameters or fall back to defaults
    desired_tags = prefer_tags if prefer_tags is not None else DESIRED_LANG_TAGS
    require_dub_setting = require_dub if require_dub is not None else REQUIRE_DUB
    sample_sec = sample_seconds if sample_seconds is not None else SAMPLE_SECONDS
    remux_setting = remux if remux is not None else REMUX_TO_DE_IF_PRESENT
    
    try:
        meta = ffprobe_streams(video_path)
    except Exception as e:
        return False, f"ffprobe-error: {e}", None

    # 1) Tag-basierter Schnellcheck (Dub vs. nur Subs)
    de_audio_idxs = audio_lang_indices(meta, desired_tags)
    if de_audio_idxs:
        if remux_setting:
            out = remux_to_de(video_path, meta, desired_tags)
            if out:
                return True, "tag-match-remuxed", out
        return True, "tag-match", None

    # wenn Dub Pflicht und nur Subs vorhanden → ablehnen
    if require_dub_setting and has_subtitles_in_lang(meta, desired_tags):
        return False, "subs-only-de", None

    # 2) Inhaltscheck (erste Audiospur)
    lang = ""
    mismatch_detail = 'unknown'
    if VERIFY_WITH_WHISPER:
        lang = content_language_guess(video_path, meta, sample_sec)
        if lang in WHISPER_ACCEPT_639_1:
            return True, f"content-match:{lang}", None
        mismatch_detail = lang or 'unknown'
    else:
        mismatch_detail = 'whisper-disabled'

    # 3) Falls irgendwo DE-Audiospur, aber nicht aktiv -> remux versuchen
    if VERIFY_WITH_WHISPER and remux_setting:
        out = remux_to_de(video_path, meta, desired_tags)
        if out:
            # kurze Gegenprobe
            lang2 = content_language_guess(out, sample_seconds=sample_sec)
            if lang2 in WHISPER_ACCEPT_639_1:
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