#!/usr/bin/env python3
"""
Comprehensive unit tests for the new language guard functionality.
Tests the EpisodeVariant model and language selection logic.
"""

import sys
import os
import pytest
from unittest.mock import patch, MagicMock

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import EpisodeVariant
from language_guard import (
    tag_variant,
    normalize_variants,
    pick_best,
    sort_by_preference,
    pick_best_with_quality,
    guess_audio_and_dub,
    _match_any
)

class TestEpisodeVariant:
    """Test the EpisodeVariant data model."""

    def test_episode_variant_creation(self):
        """Test creating an EpisodeVariant instance."""
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

        assert variant.url == "https://example.com/stream"
        assert variant.source == "test_source"
        assert variant.season == 1
        assert variant.episode == 1
        assert variant.title == "Test Episode"
        assert variant.quality == "1080p"
        assert variant.audio_lang == "de"
        assert variant.dub_lang is None
        assert variant.subs == ["en"]
        assert variant.extra == {"label": "Test Stream"}

    def test_episode_variant_defaults(self):
        """Test EpisodeVariant with default values."""
        variant = EpisodeVariant(
            url="https://example.com/stream",
            source="test_source"
        )

        assert variant.url == "https://example.com/stream"
        assert variant.source == "test_source"
        assert variant.season is None
        assert variant.episode is None
        assert variant.title is None
        assert variant.quality is None
        assert variant.audio_lang is None
        assert variant.dub_lang is None
        assert variant.subs == []
        assert variant.extra == {}

class TestLanguageGuardHelpers:
    """Test helper functions for language detection."""

    def test_match_any_positive(self):
        """Test _match_any with matching patterns."""
        assert _match_any("German Dub 1080p", [r"german", r"dub"]) == True
        assert _match_any("English Sub", [r"english", r"sub"]) == True
        assert _match_any("Deutsch German", [r"deutsch", r"german"]) == True

    def test_match_any_negative(self):
        """Test _match_any with non-matching patterns."""
        assert _match_any("French Movie", [r"german", r"english"]) == False
        assert _match_any("Spanish Audio", [r"deutsch", r"french"]) == False

    def test_guess_audio_and_dub(self):
        """Test guessing audio and dub languages from labels."""
        # Test German dub detection
        audio, dub = guess_audio_and_dub("German Dub 1080p")
        assert dub == "de"
        assert audio is None  # Audio language not explicitly stated

        # Test English audio detection
        audio, dub = guess_audio_and_dub("English Original")
        assert audio == "en"
        assert dub is None

        # Test Japanese with German dub
        audio, dub = guess_audio_and_dub("Japanese German Dub")
        assert audio == "ja"
        assert dub == "de"

        # Test no match
        audio, dub = guess_audio_and_dub("Unknown Language")
        assert audio is None
        assert dub is None

class TestVariantTagging:
    """Test the tag_variant function."""

    def test_tag_variant_with_german_dub(self):
        """Test tagging a variant with German dub."""
        variant = EpisodeVariant(
            url="https://example.com/stream",
            source="test",
            extra={"label": "German Dub HD"}
        )

        tagged = tag_variant(variant)

        assert tagged.audio_lang is None  # Not explicitly stated
        assert tagged.dub_lang == "de"

    def test_tag_variant_with_english_audio(self):
        """Test tagging a variant with English audio."""
        variant = EpisodeVariant(
            url="https://example.com/stream",
            source="test",
            extra={"label": "English Original"}
        )

        tagged = tag_variant(variant)

        assert tagged.audio_lang == "en"
        assert tagged.dub_lang is None

    def test_tag_variant_with_japanese_german_dub(self):
        """Test tagging a variant with Japanese audio and German dub."""
        variant = EpisodeVariant(
            url="https://example.com/stream",
            source="test",
            extra={"label": "Japanese German Dub"}
        )

        tagged = tag_variant(variant)

        assert tagged.audio_lang == "ja"
        assert tagged.dub_lang == "de"

    def test_tag_variant_preserves_existing_values(self):
        """Test that existing language values are preserved."""
        variant = EpisodeVariant(
            url="https://example.com/stream",
            source="test",
            audio_lang="en",
            dub_lang="de",
            extra={"label": "English German Dub"}
        )

        tagged = tag_variant(variant)

        # Should preserve existing values
        assert tagged.audio_lang == "en"
        assert tagged.dub_lang == "de"

class TestVariantNormalization:
    """Test the normalize_variants function."""

    def test_normalize_variants_processes_all(self):
        """Test that normalize_variants processes all variants."""
        variants = [
            EpisodeVariant(url="https://example.com/1", source="test", extra={"label": "German Dub"}),
            EpisodeVariant(url="https://example.com/2", source="test", extra={"label": "English Original"}),
            EpisodeVariant(url="https://example.com/3", source="test", extra={"label": "Japanese German Dub"})
        ]

        normalized = normalize_variants(variants)

        assert len(normalized) == 3
        assert normalized[0].dub_lang == "de"
        assert normalized[1].audio_lang == "en"
        assert normalized[2].audio_lang == "ja"
        assert normalized[2].dub_lang == "de"

class TestVariantSelection:
    """Test variant selection functions."""

    def test_pick_best_german_priority(self):
        """Test that German variants are picked first."""
        variants = [
            EpisodeVariant(url="https://example.com/en", source="test", audio_lang="en"),
            EpisodeVariant(url="https://example.com/de", source="test", audio_lang="de"),
            EpisodeVariant(url="https://example.com/ja", source="test", audio_lang="ja")
        ]

        best = pick_best(variants)

        assert best is not None
        assert best.audio_lang == "de"
        assert best.url == "https://example.com/de"

    def test_pick_best_german_dub_priority(self):
        """Test that German dub variants are picked before regular German."""
        variants = [
            EpisodeVariant(url="https://example.com/de", source="test", audio_lang="de"),
            EpisodeVariant(url="https://example.com/en-de", source="test", audio_lang="en", dub_lang="de"),
            EpisodeVariant(url="https://example.com/ja", source="test", audio_lang="ja")
        ]

        best = pick_best(variants)

        assert best is not None
        assert best.audio_lang == "en"
        assert best.dub_lang == "de"
        assert best.url == "https://example.com/en-de"

    def test_pick_best_no_match_returns_none(self):
        """Test that pick_best returns None when no variants match priorities."""
        variants = [
            EpisodeVariant(url="https://example.com/fr", source="test", audio_lang="fr"),
            EpisodeVariant(url="https://example.com/es", source="test", audio_lang="es")
        ]

        best = pick_best(variants)

        assert best is None

    def test_sort_by_preference(self):
        """Test sorting variants by preference."""
        variants = [
            EpisodeVariant(url="https://example.com/ja", source="test", audio_lang="ja"),
            EpisodeVariant(url="https://example.com/de", source="test", audio_lang="de"),
            EpisodeVariant(url="https://example.com/en-de", source="test", audio_lang="en", dub_lang="de"),
            EpisodeVariant(url="https://example.com/en", source="test", audio_lang="en")
        ]

        sorted_variants = sort_by_preference(variants)

        # German should be first
        assert sorted_variants[0].audio_lang == "de"

        # German dub should be second
        assert sorted_variants[1].audio_lang == "en"
        assert sorted_variants[1].dub_lang == "de"

        # English should be third
        assert sorted_variants[2].audio_lang == "en"

        # Japanese should be last
        assert sorted_variants[3].audio_lang == "ja"

class TestQualitySelection:
    """Test quality-based selection."""

    def test_pick_best_with_quality_prefers_higher_quality(self):
        """Test that higher quality variants are preferred within the same language."""
        variants = [
            EpisodeVariant(url="https://example.com/de-720p", source="test", audio_lang="de", quality="720p"),
            EpisodeVariant(url="https://example.com/de-1080p", source="test", audio_lang="de", quality="1080p"),
            EpisodeVariant(url="https://example.com/de-480p", source="test", audio_lang="de", quality="480p")
        ]

        best = pick_best_with_quality(variants)

        assert best is not None
        assert best.quality == "1080p"
        assert best.url == "https://example.com/de-1080p"

    def test_pick_best_with_quality_considers_language_first(self):
        """Test that language preference takes precedence over quality."""
        variants = [
            EpisodeVariant(url="https://example.com/en-1080p", source="test", audio_lang="en", quality="1080p"),
            EpisodeVariant(url="https://example.com/de-720p", source="test", audio_lang="de", quality="720p")
        ]

        best = pick_best_with_quality(variants)

        assert best is not None
        assert best.audio_lang == "de"  # German preferred over English
        assert best.quality == "720p"

class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_variants_list(self):
        """Test behavior with empty variant lists."""
        assert pick_best([]) is None
        assert sort_by_preference([]) == []
        assert pick_best_with_quality([]) is None

    def test_mixed_quality_values(self):
        """Test handling of mixed quality values."""
        variants = [
            EpisodeVariant(url="https://example.com/de-hd", source="test", audio_lang="de", quality="HD"),
            EpisodeVariant(url="https://example.com/de-720p", source="test", audio_lang="de", quality="720p"),
            EpisodeVariant(url="https://example.com/de-4k", source="test", audio_lang="de", quality="4K")
        ]

        best = pick_best_with_quality(variants)

        assert best is not None
        assert best.quality == "4K"  # 4K should be highest quality

    def test_unknown_quality_values(self):
        """Test handling of unknown quality values."""
        variants = [
            EpisodeVariant(url="https://example.com/de-unknown", source="test", audio_lang="de", quality="unknown"),
            EpisodeVariant(url="https://example.com/de-720p", source="test", audio_lang="de", quality="720p")
        ]

        best = pick_best_with_quality(variants)

        assert best is not None
        assert best.quality == "720p"  # Known quality preferred over unknown

if __name__ == "__main__":
    # Run the tests
    pytest.main([__file__, "-v"])