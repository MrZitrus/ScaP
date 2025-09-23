from dataclasses import dataclass, field
from typing import Optional, List, Dict

@dataclass
class EpisodeVariant:
    url: str
    source: str                    # z.B. "aniworld", "bs"
    season: Optional[int] = None
    episode: Optional[int] = None
    title: Optional[str] = None
    quality: Optional[str] = None  # "1080p", "720p", ...
    audio_lang: Optional[str] = None  # ISO-639-1: "de", "en", "ja"
    dub_lang: Optional[str] = None    # Falls erkennbar, z.B. "de", "en"; sonst None
    subs: List[str] = field(default_factory=list)  # ["de","en"] etc.
    extra: Dict = field(default_factory=dict)      # beliebige Zusatzinfos