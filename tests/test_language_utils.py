from language_utils import (
    build_language_tagged_name,
    get_language_dub_tag,
    language_codes_from_filename,
    strip_language_tag_suffix,
    subtitle_codes_from_filename,
)

def test_strip_language_tag_suffix_removes_known_tags() -> None:
    assert strip_language_tag_suffix("Episode [GerDub]") == "Episode"
    assert strip_language_tag_suffix("Episode [Dub:EN]") == "Episode"
    assert strip_language_tag_suffix("Episode [GerDub] [Extra]") == "Episode [Extra]"

def test_get_language_dub_tag_returns_canonical() -> None:
    assert get_language_dub_tag("en") == "[EngDub]"
    assert get_language_dub_tag("fr") == "[FrDub]"
    assert get_language_dub_tag("xx") == "[Dub:XX]"

def test_build_language_tagged_name_normalizes_existing_tag() -> None:
    assert build_language_tagged_name("Episode [GerDub]", "en") == "Episode [EngDub]"
    assert build_language_tagged_name("Episode [GerSub]", "ja") == "Episode [JapDub]"
    assert build_language_tagged_name("Episode", None) == "Episode"

def test_language_codes_from_filename_detects_multiple_formats() -> None:
    assert language_codes_from_filename("S01E01 - Test [EngDub].mp4") == {"en"}
    assert language_codes_from_filename("S01E01 - Test [Dub:JA].mp4") == {"ja"}
    assert language_codes_from_filename("S01E01 - Test [GerDub][Dub:EN].mp4") == {"de", "en"}

def test_subtitle_codes_from_filename_detects_german() -> None:
    assert subtitle_codes_from_filename("S01E01 - Test [GerSub].mp4") == {"de"}
    assert subtitle_codes_from_filename("S01E01 - Test [Sub:EN].mp4") == {"en"}
