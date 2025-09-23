import os
import re
from typing import Callable, Iterable, Set

LANGUAGE_FILENAME_TAGS = {
    "de": "[GerDub]",
    "en": "[EngDub]",
    "ja": "[JapDub]",
    "fr": "[FrDub]",
    "es": "[EsDub]",
    "it": "[ItDub]",
    "pt": "[PtDub]",
    "ru": "[RuDub]",
}

SUBTITLE_FILENAME_TAGS = {
    "de": "[GerSub]",
    "en": "[EngSub]",
}

_TAG_PATTERN = re.compile(r"\[([^\]]+)\]")
_SUFFIX_PATTERN = re.compile(r"\s*\[([^\]]+)\]\s*$")

_BASE_TOKEN_TO_LANG = {
    "ger": "de",
    "de": "de",
    "deu": "de",
    "german": "de",
    "eng": "en",
    "en": "en",
    "englis": "en",
    "english": "en",
    "ja": "ja",
    "jp": "ja",
    "jpn": "ja",
    "jap": "ja",
    "japanese": "ja",
    "fr": "fr",
    "fra": "fr",
    "fre": "fr",
    "french": "fr",
    "es": "es",
    "spa": "es",
    "span": "es",
    "spanish": "es",
    "it": "it",
    "ita": "it",
    "ital": "it",
    "italian": "it",
    "pt": "pt",
    "por": "pt",
    "port": "pt",
    "portuguese": "pt",
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
}

_LANGUAGE_CANONICAL_TOKENS = {
    lang: LANGUAGE_FILENAME_TAGS[lang].strip("[]").lower()
    for lang in LANGUAGE_FILENAME_TAGS
}
_SUBTITLE_CANONICAL_TOKENS = {
    lang: SUBTITLE_FILENAME_TAGS[lang].strip("[]").lower()
    for lang in SUBTITLE_FILENAME_TAGS
}


def _normalize_token(token: str) -> str:
    return token.strip().lower()


def _extract_language_code_from_token(token: str, suffix: str, canonical_map: dict) -> str | None:
    token = _normalize_token(token)
    if token.startswith(f"{suffix}:"):
        value = token.split(":", 1)[1]
        return value or None
    if token.endswith(suffix):
        base = token[:-len(suffix)]
        base = base.rstrip(":")
        if base in _BASE_TOKEN_TO_LANG:
            return _BASE_TOKEN_TO_LANG[base]
        if base in canonical_map:
            return canonical_map[base]
    if token in canonical_map:
        return canonical_map[token]
    if token in _BASE_TOKEN_TO_LANG and suffix == "dub":
        return _BASE_TOKEN_TO_LANG[token]
    return None


def _iter_tag_tokens(text: str) -> Iterable[str]:
    for match in _TAG_PATTERN.finditer(text):
        yield _normalize_token(match.group(1))


def language_codes_from_filename(filename: str) -> Set[str]:
    tokens = list(_iter_tag_tokens(filename))
    codes = {
        code for token in tokens
        if (code := _extract_language_code_from_token(token, "dub", _LANGUAGE_CANONICAL_TOKENS))
    }
    return codes


def subtitle_codes_from_filename(filename: str) -> Set[str]:
    tokens = list(_iter_tag_tokens(filename))
    codes = {
        code for token in tokens
        if (code := _extract_language_code_from_token(token, "sub", _SUBTITLE_CANONICAL_TOKENS))
    }
    return codes


def strip_language_tag_suffix(name: str) -> str:
    result = name
    while True:
        match = _SUFFIX_PATTERN.search(result)
        if not match:
            break
        token = _normalize_token(match.group(1))
        if _extract_language_code_from_token(token, "dub", _LANGUAGE_CANONICAL_TOKENS) or _extract_language_code_from_token(token, "sub", _SUBTITLE_CANONICAL_TOKENS):
            result = result[:match.start()].rstrip()
            continue
        break
    return result.rstrip()


def get_language_dub_tag(lang_code: str) -> str:
    canonical = LANGUAGE_FILENAME_TAGS.get(lang_code.lower())
    if canonical:
        return canonical
    return f"[Dub:{lang_code.upper()}]"


def build_language_tagged_name(base_name: str, lang_code: str | None) -> str:
    base = strip_language_tag_suffix(base_name)
    if not lang_code:
        return base
    tag = get_language_dub_tag(lang_code)
    return f"{base} {tag}".strip()


def rename_file_with_language_tag(file_path: str, lang_code: str | None, sanitize: Callable[[str], str]) -> str:
    if not file_path or not os.path.exists(file_path):
        return file_path
    if not lang_code:
        return file_path

    directory, filename = os.path.split(file_path)
    name, ext = os.path.splitext(filename)
    new_name = build_language_tagged_name(name, lang_code)
    sanitized = sanitize(f"{new_name}{ext}")
    new_path = os.path.join(directory, sanitized)
    if new_path == file_path:
        return file_path

    os.replace(file_path, new_path)
    return new_path


def filename_has_language(filename: str, lang_code: str) -> bool:
    return lang_code.lower() in language_codes_from_filename(filename)


def filename_has_subtitle(filename: str, lang_code: str) -> bool:
    return lang_code.lower() in subtitle_codes_from_filename(filename)
