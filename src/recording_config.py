from __future__ import annotations

from dataclasses import dataclass


_FORMAT_ALIASES = {
    "FLV": "FLV",
    "MKV": "MKV",
    "TS": "TS",
    "MP4": "MP4",
    "MP3": "MP3",
    "MP3音频": "MP3",
    "M4A": "M4A",
    "M4A音频": "M4A",
}


def normalize_audio_bitrate(value, default: int = 96) -> str:
    try:
        bitrate = int(str(value).lower().removesuffix("k").strip())
    except (TypeError, ValueError):
        bitrate = default
    if not 32 <= bitrate <= 320:
        bitrate = default
    return f"{bitrate}k"


@dataclass(frozen=True)
class RecordFormat:
    name: str
    extension: str
    container: str
    audio_only: bool = False

    @classmethod
    def parse(cls, value: str, default: str = "TS") -> "RecordFormat":
        normalized = _FORMAT_ALIASES.get((value or "").strip().upper())
        if normalized is None:
            normalized = _FORMAT_ALIASES.get((default or "").strip().upper(), "TS")

        formats = {
            "FLV": cls("FLV", "flv", "flv"),
            "MKV": cls("MKV", "mkv", "matroska"),
            "TS": cls("TS", "ts", "mpegts"),
            "MP4": cls("MP4", "mp4", "mp4"),
            "MP3": cls("MP3", "mp3", "mp3", audio_only=True),
            "M4A": cls("M4A", "m4a", "mp4", audio_only=True),
        }
        return formats[normalized]

    @classmethod
    def normalize_optional(cls, value: str) -> str:
        return _FORMAT_ALIASES.get((value or "").strip().upper(), "")


@dataclass(frozen=True)
class RecordingConfig:
    quality: str
    url: str
    anchor_name: str
    requested_format: str
    source_line: str

    @property
    def fingerprint(self) -> tuple[str, str, str, str]:
        return self.url, self.quality, self.anchor_name, self.requested_format

    @classmethod
    def from_tuple(cls, value: tuple) -> "RecordingConfig":
        quality, url, anchor_name = value[:3]
        requested_format = value[3] if len(value) > 3 else ""
        source_line = value[4] if len(value) > 4 else url
        return cls(quality, url, anchor_name, requested_format, source_line)
