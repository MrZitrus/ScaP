#!/usr/bin/env python3
"""
Test-Script für Language Guard Funktionalität
Verwendung:
  python test_language_guard.py <video_file_path>  # Test video file processing
  python test_language_guard.py --test-new         # Test new variant functionality
  python test_language_guard.py --test-all         # Test both
"""

import sys
import os
from pathlib import Path

def test_video_language_guard(video_path: str):
    """Testet die Language Guard Funktionalität mit einer Video-Datei."""

    if not os.path.exists(video_path):
        print(f"❌ Datei nicht gefunden: {video_path}")
        return False

    try:
        from language_guard import verify_language

        print(f"🔍 Prüfe Datei: {video_path}")
        print("=" * 50)

        # Test mit verschiedenen Konfigurationen
        print("🔧 Test 1: Standard-Konfiguration")
        ok, detail, fixed_path = verify_language(video_path)

        print(f"Ergebnis: {'✅ AKZEPTIERT' if ok else '❌ ABGELEHNT'}")
        print(f"Details: {detail}")
        if fixed_path:
            print(f"Remuxte Datei: {fixed_path}")

        print("\n🔧 Test 2: Nur Untertitel erlaubt")
        ok2, detail2, fixed_path2 = verify_language(
            video_path,
            require_dub=False,
            sample_seconds=30
        )

        print(f"Ergebnis: {'✅ AKZEPTIERT' if ok2 else '❌ ABGELEHNT'}")
        print(f"Details: {detail2}")
        if fixed_path2:
            print(f"Remuxte Datei: {fixed_path2}")

        return ok or ok2

    except ImportError as e:
        print(f"❌ Language Guard nicht verfügbar: {e}")
        print("Installiere Dependencies: pip install faster-whisper openai-whisper")
        return False
    except Exception as e:
        print(f"❌ Fehler bei der Prüfung: {e}")
        return False

def test_new_variant_functionality():
    """Testet die neue EpisodeVariant-Funktionalität."""
    print("🆕 Teste neue EpisodeVariant-Funktionalität")
    print("=" * 50)

    try:
        from models import EpisodeVariant
        from language_guard import (
            tag_variant,
            normalize_variants,
            pick_best,
            sort_by_preference,
            pick_best_with_quality,
            guess_audio_and_dub
        )

        # Test 1: EpisodeVariant Creation
        print("🔧 Test 1: EpisodeVariant Erstellung")
        variant = EpisodeVariant(
            url="https://example.com/stream",
            source="test_source",
            season=1,
            episode=1,
            title="Test Episode",
            quality="1080p",
            audio_lang="de",
            dub_lang=None,
            subs=["en"],
            extra={"label": "Test Stream"}
        )
        print(f"✅ EpisodeVariant erstellt: {variant.title}")

        # Test 2: Language Detection
        print("\n🔧 Test 2: Spracherkennung")
        test_labels = [
            ("German Dub 1080p", "de", None),
            ("English Original", "en", None),
            ("Japanese German Dub", "ja", "de"),
            ("French Movie", None, None)
        ]

        for label, expected_audio, expected_dub in test_labels:
            audio, dub = guess_audio_and_dub(label)
            status = "✅" if audio == expected_audio and dub == expected_dub else "❌"
            print(f"{status} '{label}' -> Audio: {audio}, Dub: {dub}")

        # Test 3: Variant Tagging
        print("\n🔧 Test 3: Variant Tagging")
        test_variant = EpisodeVariant(
            url="https://example.com/stream",
            source="test",
            extra={"label": "German Dub HD"}
        )
        tagged = tag_variant(test_variant)
        print(f"✅ Tagged variant: Audio={tagged.audio_lang}, Dub={tagged.dub_lang}")

        # Test 4: Variant Selection
        print("\n🔧 Test 4: Variant Selection")
        variants = [
            EpisodeVariant(url="https://example.com/de", source="test", audio_lang="de", quality="720p"),
            EpisodeVariant(url="https://example.com/en", source="test", audio_lang="en", quality="1080p"),
            EpisodeVariant(url="https://example.com/ja", source="test", audio_lang="ja", quality="1080p")
        ]

        best = pick_best(variants)
        if best:
            print(f"✅ Beste Variante: {best.audio_lang} ({best.url})")

        # Test 5: Quality Selection
        print("\n🔧 Test 5: Quality Selection")
        quality_variants = [
            EpisodeVariant(url="https://example.com/de-720p", source="test", audio_lang="de", quality="720p"),
            EpisodeVariant(url="https://example.com/de-1080p", source="test", audio_lang="de", quality="1080p"),
            EpisodeVariant(url="https://example.com/de-4k", source="test", audio_lang="de", quality="4K")
        ]

        best_quality = pick_best_with_quality(quality_variants)
        if best_quality:
            print(f"✅ Beste Qualität: {best_quality.quality} ({best_quality.url})")

        # Test 6: Sorting
        print("\n🔧 Test 6: Variant Sorting")
        sorted_variants = sort_by_preference(variants)
        print(f"✅ Sortiert: {[f'{v.audio_lang}' for v in sorted_variants]}")

        return True

    except ImportError as e:
        print(f"❌ Neue Language Guard Funktionalität nicht verfügbar: {e}")
        return False
    except Exception as e:
        print(f"❌ Fehler beim Testen der neuen Funktionalität: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        print("Verwendung:")
        print("  python test_language_guard.py <video_file_path>  # Test video file processing")
        print("  python test_language_guard.py --test-new         # Test new variant functionality")
        print("  python test_language_guard.py --test-all         # Test both")
        sys.exit(1)

    arg = sys.argv[1]

    if arg == "--test-new":
        # Test only new functionality
        success = test_new_variant_functionality()
        sys.exit(0 if success else 1)

    elif arg == "--test-all":
        # Test both old and new functionality
        success1 = test_new_variant_functionality()
        print("\n" + "="*50)
        if len(sys.argv) > 2:
            success2 = test_video_language_guard(sys.argv[2])
        else:
            print("⚠️  Für Video-Tests bitte Dateipfad angeben")
            success2 = True
        sys.exit(0 if (success1 and success2) else 1)

    else:
        # Test video file processing
        video_path = arg
        success = test_video_language_guard(video_path)
        sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()