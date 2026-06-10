#!/usr/bin/env python3
"""
Fly Me To The Roon — drag-and-drop music metadata editor for Roon.

Drop files/folders here → review & edit tags → Process All → files land in ~/PARA/5. ROON

Requirements (install once):
    pip install mutagen pillow requests tkinterdnd2
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import threading
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── stdlib tkinter ──────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ── third-party ─────────────────────────────────────────────────────────────
# Try to locate system tkdnd (installed via `brew install tkdnd`) so that
# tkinterdnd2 uses it instead of its bundled (possibly incompatible) binary.
import subprocess as _sp

# Locate tkdnd library: check local build first, then Homebrew.
def _find_tkdnd_lib() -> str:
    """Return path to a directory containing tkdnd, or empty string."""
    import sys as _sys
    # 1. PyInstaller frozen bundle — tkdnd_lib is bundled next to app code
    if getattr(_sys, "frozen", False):
        meipass = Path(getattr(_sys, "_MEIPASS", ""))
        local = meipass / "tkdnd_lib" / "lib"
        if local.is_dir():
            return str(local)
    # 2. Locally built by build_tkdnd.sh (development)
    here = Path(__file__).parent
    local = here / "tkdnd_lib" / "lib"
    if local.is_dir():
        return str(local)
    # 3. Homebrew formula (removed from Homebrew, but maybe user has a tap)
    try:
        prefix = _sp.check_output(
            ["brew", "--prefix", "tkdnd"], stderr=_sp.DEVNULL
        ).decode().strip()
        if prefix:
            return f"{prefix}/lib"
    except Exception:
        pass
    return ""

_tkdnd_lib = _find_tkdnd_lib()
if _tkdnd_lib and not os.environ.get("TKDND_LIBRARY"):
    os.environ["TKDND_LIBRARY"] = _tkdnd_lib

# ── version ─────────────────────────────────────────────────────────────────
def _load_version() -> str:
    """Read VERSION file. Works in dev and inside a PyInstaller bundle."""
    import sys as _sys
    candidates = []
    if getattr(_sys, "frozen", False):
        meipass = Path(getattr(_sys, "_MEIPASS", ""))
        if meipass:
            candidates.append(meipass / "VERSION")
    candidates.append(Path(__file__).parent / "VERSION")
    for p in candidates:
        try:
            if p.is_file():
                v = p.read_text().strip()
                if v:
                    return v
        except Exception:
            pass
    return "dev"

def _load_changelog(max_chars: int = 2000) -> str:
    """Read CHANGELOG.md (truncated) for the About dialog."""
    import sys as _sys
    candidates = []
    if getattr(_sys, "frozen", False):
        meipass = Path(getattr(_sys, "_MEIPASS", ""))
        if meipass:
            candidates.append(meipass / "CHANGELOG.md")
    candidates.append(Path(__file__).parent / "CHANGELOG.md")
    for p in candidates:
        try:
            if p.is_file():
                txt = p.read_text()
                if len(txt) > max_chars:
                    txt = txt[:max_chars].rstrip() + "\n…"
                return txt
        except Exception:
            pass
    return ""

VERSION = _load_version()

# ── update checker (GitHub Releases) ────────────────────────────────────────
UPDATE_REPO = "sh1nd0/roontaggr"
UPDATE_RELEASES_API = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"

def _parse_version(s: str) -> tuple:
    """'1.3.0' -> (1, 3, 0). Non-numeric parts become 0."""
    out = []
    for part in re.findall(r"\d+", s or ""):
        try:
            out.append(int(part))
        except Exception:
            out.append(0)
    return tuple(out) or (0,)

def _fetch_latest_release() -> tuple:
    """Fetch latest release metadata from GitHub.

    Returns:
        ("ok", data)          — release found
        ("no_releases", {})   — repo exists but no releases yet (HTTP 404)
        ("error", {"msg":...})— network/other failure
    """
    import sys
    import urllib.error
    try:
        req = urllib.request.Request(
            UPDATE_RELEASES_API,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": f"FlyMeToTheRoon/{VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            return "ok", json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"[FlyMeToTheRoon] update check HTTP {e.code}: {e.reason}",
              file=sys.stderr, flush=True)
        if e.code == 404:
            return "no_releases", {}
        return "error", {"msg": f"GitHub returned HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        print(f"[FlyMeToTheRoon] update check network error: {e.reason}",
              file=sys.stderr, flush=True)
        return "error", {"msg": f"Network unreachable: {e.reason}"}
    except Exception as e:
        print(f"[FlyMeToTheRoon] update check failed: {e}",
              file=sys.stderr, flush=True)
        return "error", {"msg": str(e)}

# When running as a bundled .app, redirect stdout/stderr to a log file
# so errors are visible via Console.app instead of disappearing silently.
import sys as _sys
if getattr(_sys, "frozen", False):
    _log_path = Path.home() / "Library" / "Logs" / "Fly Me To The Roon.log"
    try:
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        _log_fh = open(_log_path, "a", buffering=1)
        _sys.stdout = _log_fh
        _sys.stderr = _log_fh
    except Exception:
        pass

# Force Python's HTTPS layer to use certifi's CA bundle. PyInstaller builds
# can't find the system keychain on macOS, which breaks urllib with
# "CERTIFICATE_VERIFY_FAILED". Override default context so GitHub / iTunes /
# CAA / Deezer metadata calls all succeed.
try:
    import ssl as _ssl
    import certifi as _certifi
    _ssl._create_default_https_context = lambda: _ssl.create_default_context(
        cafile=_certifi.where()
    )
except Exception as _e:
    print(f"[FlyMeToTheRoon] SSL/certifi setup skipped: {_e}", file=_sys.stderr, flush=True)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

try:
    from mutagen.id3 import (
        ID3, ID3NoHeaderError, APIC, TALB, TDRC, TIT2, TPE1, TPE2, TRCK, USLT,
    )
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC, Picture
    from mutagen.aiff import AIFF
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.wave import WAVE
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── config ───────────────────────────────────────────────────────────────────
_CONFIG_FILE = Path.home() / ".roontaggr.json"

def _load_config() -> dict:
    try:
        import json
        return json.loads(_CONFIG_FILE.read_text())
    except Exception:
        return {}

def _save_config(cfg: dict) -> None:
    import json
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

def _get_roon_dir() -> Path:
    if "ROONTAGGR_DEST" in os.environ:
        return Path(os.environ["ROONTAGGR_DEST"])
    cfg = _load_config()
    if "roon_dir" in cfg:
        return Path(cfg["roon_dir"])
    return Path.home() / "PARA" / "5. ROON"

ROON_DIR = _get_roon_dir()
MUSIC_EXTS = {".mp3", ".flac", ".aif", ".aiff", ".wav", ".m4a"}

# ── palette + typography (Roon-inspired light theme) ────────────────────────
BG       = "#FAF8F4"   # warm paper-white window background
BG2      = "#FFFFFF"   # cards, toolbar, surfaces
BG3      = "#F1EEE7"   # input fields, queue, table rows
BG_HOVER = "#E9E5DC"   # hover state
ACCENT   = "#6D5BE3"   # Roon-style violet
ACCENT_HI = "#7E6DEC"
ACCENT_LO = "#5747C8"
FG       = "#1A1A1F"   # primary text
FG_DIM   = "#6B665E"   # warm secondary/label text
FG_MUTED = "#9A958C"   # tertiary / caption
SEL      = "#E8E1FF"   # lavender selected row
ERR      = "#C0392B"
OK       = "#2BA84A"
BORDER   = "#E6E1D6"   # warm subtle divider

# Typography. Display = serif headline (Roon-style). Body = clean sans.
# Iowan Old Style + SF Pro ship on every modern Mac.
FONT_DISPLAY = "Iowan Old Style"
FONT_BODY    = "SF Pro Text"
FONT_MONO    = "SF Mono"

# ═══════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Track:
    path: Path
    title: str = ""
    artist: str = ""        # track-level artist (for compilations)
    track_num: int = 0
    duration: float = 0.0

@dataclass
class Album:
    tracks: list[Track] = field(default_factory=list)
    artist: str = ""
    album: str = ""
    year: str = ""
    artwork_bytes: Optional[bytes] = None
    artwork_mime: str = "image/jpeg"
    tracklist: str = ""     # for DJ mixes
    is_dj_mix: bool = False
    is_live: bool = False
    is_compilation: bool = False
    is_single: bool = False
    status: str = "pending"   # pending / fetching / ready / done / error
    status_msg: str = ""

    @property
    def display_name(self) -> str:
        a = self.artist or "Unknown Artist"
        if self.is_single:
            t = self.tracks[0].title if self.tracks else "Single"
            return f"{a} — {t} [Single]"
        b = self.album or "Unknown Album"
        return f"{a} — {b}"

    @property
    def folder_name(self) -> str:
        a = _sanitize(self.artist or "Unknown Artist")
        if self.is_single:
            return f"{a} - Singles"
        b = _sanitize(self.album or "Unknown Album")
        return f"{a} - {b}"

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _sanitize(s: str) -> str:
    """Remove characters illegal in macOS filenames."""
    return re.sub(r'[/:*?"<>|\\]', "-", s).strip()

def _detect_container(path: Path) -> str:
    """Sniff the actual container format from file magic bytes.

    Returns one of: 'mp3', 'flac', 'aiff', 'mp4', 'wav', 'webm', 'ogg',
    'unknown'. Independent of the file's extension — this is what the
    *content* actually is.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(32)
    except OSError:
        return "unknown"

    # ID3v2-tagged file: skip past the tag to find the real signature.
    if head[:3] == b"ID3" and len(head) >= 10:
        try:
            tag_size = ((head[6] & 0x7F) << 21) | \
                       ((head[7] & 0x7F) << 14) | \
                       ((head[8] & 0x7F) << 7)  | \
                        (head[9] & 0x7F)
            with open(path, "rb") as f:
                f.seek(10 + tag_size)
                probe = f.read(8)
            if len(probe) >= 2 and probe[0] == 0xFF and (probe[1] & 0xE0) == 0xE0:
                return "mp3"
            if probe[:4] == b"\x1A\x45\xDF\xA3":
                return "webm"
            if probe[:4] == b"OggS":
                return "ogg"
            if probe[:4] == b"fLaC":
                return "flac"
            if probe[:4] == b"FORM":
                return "aiff"
            if len(probe) >= 8 and probe[4:8] == b"ftyp":
                return "mp4"
            # ID3 tag exists but we can't recognize what's after it.
            return "unknown"
        except Exception:
            pass

    # Non-ID3 file signatures
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"\x1A\x45\xDF\xA3":
        return "webm"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "mp4"
    return "unknown"


def _verify_extension(path: Path) -> tuple:
    """Check that the file extension matches the sniffed container.

    Returns (ok, expected_group, detected). `expected_group` is the family
    the extension implies (e.g. .aif and .aiff both map to 'aiff'). If the
    real format is 'unknown' we don't flag it — we trust the extension to
    avoid false positives.
    """
    ext_group = {
        "mp3": "mp3",
        "flac": "flac",
        "aif": "aiff", "aiff": "aiff",
        "wav": "wav",
        "m4a": "mp4",
    }.get(path.suffix.lower().lstrip("."), "unknown")

    detected = _detect_container(path)
    if detected == "unknown":
        return True, ext_group, detected
    return ext_group == detected, ext_group, detected


def _music_files(paths: list[Path]) -> list[Path]:
    out = []
    for p in paths:
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.suffix.lower() in MUSIC_EXTS:
                    out.append(f)
        elif p.suffix.lower() in MUSIC_EXTS:
            out.append(p)
    return out

def _http_get(url: str, timeout: int = 8) -> Optional[bytes]:
    try:
        if HAS_REQUESTS:
            r = _requests.get(url, timeout=timeout,
                              headers={"User-Agent": "FlyMeToTheRoon/1.0"})
            return r.content if r.ok else None
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "FlyMeToTheRoon/1.0"}),
            timeout=timeout
        ) as resp:
            return resp.read()
    except Exception:
        return None

def _parse_filename_hints(p: Path) -> tuple[str, str, str]:
    """Return (artist, title, year) guessed from filename."""
    stem = p.stem
    # YouTube cover pattern: "Artist cover Original Song for Show"
    m = re.match(r'^(.+?)\s+cover\s+(.+?)\s+for\s+', stem, re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip(), ""
    # "Artist - Title" pattern
    if " - " in stem:
        parts = stem.split(" - ", 1)
        return parts[0].strip(), parts[1].strip(), ""
    # Bare title
    return "", stem.strip(), ""

# ═══════════════════════════════════════════════════════════════════════════
# Tag reading
# ═══════════════════════════════════════════════════════════════════════════

def read_tags(path: Path) -> Track:
    t = Track(path=path)
    ext = path.suffix.lower()

    if not HAS_MUTAGEN:
        a, title, _ = _parse_filename_hints(path)
        t.artist = a; t.title = title
        return t

    try:
        if ext == ".mp3":
            audio = MP3(str(path))
            t.duration = audio.info.length
            try:
                tags = ID3(str(path))
            except ID3NoHeaderError:
                tags = {}
            t.title  = (tags["TIT2"].text[0] if "TIT2" in tags else "").strip()
            t.artist = (tags["TPE1"].text[0] if "TPE1" in tags else "").strip()
            trck = (tags["TRCK"].text[0] if "TRCK" in tags else "0").split("/")[0]
            t.track_num = int(trck) if trck.isdigit() else 0

        elif ext == ".flac":
            audio = FLAC(str(path))
            t.duration = audio.info.length
            t.title    = (audio.get("title")  or [""])[0].strip()
            t.artist   = (audio.get("artist") or [""])[0].strip()
            raw_trk = (audio.get("tracknumber") or ["0"])[0].split("/")[0]
            t.track_num = int(raw_trk) if raw_trk.isdigit() else 0

        elif ext in (".aif", ".aiff"):
            audio = AIFF(str(path))
            t.duration = audio.info.length
            try:
                tags = audio.tags or {}
            except Exception:
                tags = {}
            t.title  = (tags["TIT2"].text[0] if "TIT2" in tags else "").strip()
            t.artist = (tags["TPE1"].text[0] if "TPE1" in tags else "").strip()
            trck = (tags["TRCK"].text[0] if "TRCK" in tags else "0").split("/")[0]
            t.track_num = int(trck) if trck.isdigit() else 0

        elif ext in (".wav",):
            audio = WAVE(str(path))
            t.duration = audio.info.length
            try:
                tags = audio.tags or {}
                t.title  = (tags["TIT2"].text[0] if "TIT2" in tags else "").strip()
                t.artist = (tags["TPE1"].text[0] if "TPE1" in tags else "").strip()
                trck = (tags["TRCK"].text[0] if "TRCK" in tags else "0").split("/")[0]
                t.track_num = int(trck) if trck.isdigit() else 0
            except Exception:
                pass

        elif ext == ".m4a":
            audio = MP4(str(path))
            t.duration = audio.info.length
            t.title  = (audio.tags.get("©nam") or [""])[0].strip()
            t.artist = (audio.tags.get("©ART") or [""])[0].strip()
            trkn = audio.tags.get("trkn")
            if trkn:
                t.track_num = trkn[0][0]

    except Exception as _e:
        import sys
        print(f"[FlyMeToTheRoon] read_tags error for {path}: {_e}", file=sys.stderr, flush=True)

    if not t.title or not t.artist:
        fa, ft, _ = _parse_filename_hints(path)
        if not t.artist: t.artist = fa
        if not t.title:  t.title  = ft

    return t

def read_album_tags(paths: list[Path]) -> Album:
    """Build an Album by reading tags from all files and picking consensus values."""
    tracks = sorted([read_tags(p) for p in paths], key=lambda t: (t.track_num, t.path.name))
    alb = Album(tracks=tracks)

    artists = [t.artist for t in tracks if t.artist]
    alb.artist = artists[0] if artists else ""

    # Try album tag from first file
    first = paths[0]
    ext   = first.suffix.lower()
    try:
        if ext == ".mp3":
            tags = ID3(str(first))
            alb.album = (tags["TALB"].text[0] if "TALB" in tags else "").strip()
            # TDRC = ID3v2.4 year; TYER = ID3v2.3 year (written by some tools / mutagen v2_version=3)
            _yr = tags.get("TDRC") or tags.get("TYER")
            alb.year  = str(_yr.text[0] if _yr else "").strip()[:4]
            # Grab existing artwork
            for tag in tags.values():
                if isinstance(tag, APIC):
                    alb.artwork_bytes = tag.data
                    alb.artwork_mime  = tag.mime
                    break
        elif ext == ".flac":
            audio = FLAC(str(first))
            alb.album = (audio.get("album") or [""])[0].strip()
            alb.year  = (audio.get("date")  or [""])[0].strip()
            if audio.pictures:
                alb.artwork_bytes = audio.pictures[0].data
                alb.artwork_mime  = audio.pictures[0].mime
        elif ext in (".aif", ".aiff"):
            audio = AIFF(str(first))
            tags  = audio.tags or {}
            alb.album = (tags["TALB"].text[0] if "TALB" in tags else "").strip()
            alb.year  = str(tags["TDRC"].text[0] if "TDRC" in tags else "").strip()[:4]
        elif ext == ".m4a":
            audio = MP4(str(first))
            alb.album = (audio.tags.get("©alb") or [""])[0].strip()
            alb.year  = (audio.tags.get("©day") or [""])[0].strip()[:4]
            covr = audio.tags.get("covr")
            if covr:
                alb.artwork_bytes = bytes(covr[0])
        elif ext in (".wav",):
            audio = WAVE(str(first))
            tags  = audio.tags or {}
            alb.album = (tags["TALB"].text[0] if "TALB" in tags else "").strip()
            _yr = tags.get("TDRC") or tags.get("TYER")
            alb.year  = str(_yr.text[0] if _yr else "").strip()[:4]
            for _tag in (list(tags.values()) if tags else []):
                if isinstance(_tag, APIC):
                    alb.artwork_bytes = _tag.data
                    alb.artwork_mime  = _tag.mime
                    break
    except Exception as _e:
        import sys
        print(f"[FlyMeToTheRoon] read_album_tags error for {first}: {_e}", file=sys.stderr, flush=True)

    # Fall back to image files in the same folder if no embedded artwork
    if not alb.artwork_bytes:
        _img_names = [
            "cover", "folder", "artwork", "front", "album",
            "AlbumArt", "AlbumArtSmall", "Folder",
        ]
        _img_exts = [".jpg", ".jpeg", ".png"]
        _folder = first.parent
        # Check common names first, then any image file in the folder
        _candidates = [
            _folder / (n + e)
            for n in _img_names for e in _img_exts
        ]
        _candidates += sorted(_folder.glob("*.jpg")) + sorted(_folder.glob("*.jpeg")) + sorted(_folder.glob("*.png"))
        for _img_path in _candidates:
            if _img_path.exists():
                try:
                    alb.artwork_bytes = _img_path.read_bytes()
                    alb.artwork_mime  = "image/png" if _img_path.suffix.lower() == ".png" else "image/jpeg"
                    break
                except Exception:
                    pass

    # Infer from folder name if still missing
    if not alb.album:
        folder = first.parent.name
        if " - " in folder:
            parts = folder.split(" - ", 1)
            if not alb.artist: alb.artist = parts[0].strip()
            alb.album = parts[1].strip()
        else:
            alb.album = folder

    # Year from filenames if still missing
    if not alb.year:
        for p in paths:
            m = re.search(r'\b(19|20)\d{2}\b', p.stem)
            if m:
                alb.year = m.group(0)
                break

    import sys
    _aw = len(alb.artwork_bytes) if alb.artwork_bytes else 0
    _t0 = alb.tracks[0].title if alb.tracks else ""
    print(
        f"[FlyMeToTheRoon] loaded: artist={alb.artist!r} album={alb.album!r} "
        f"year={alb.year!r} title0={_t0!r} artwork={_aw}b",
        file=sys.stderr, flush=True
    )
    return alb

# ═══════════════════════════════════════════════════════════════════════════
# Metadata / artwork lookup
# ═══════════════════════════════════════════════════════════════════════════

def _itunes_search(artist: str, album: str) -> Optional[dict]:
    q = urllib.parse.quote(f"{artist} {album}")
    url = f"https://itunes.apple.com/search?term={q}&entity=album&limit=5"
    data = _http_get(url)
    if not data:
        return None
    try:
        j = json.loads(data)
        for res in j.get("results", []):
            if res.get("collectionType") == "Album":
                return res
        return j["results"][0] if j.get("results") else None
    except Exception:
        return None

def _itunes_artwork(url: str) -> Optional[bytes]:
    # Replace 100x100 thumb with 600x600
    url = re.sub(r'\d+x\d+bb', '600x600bb', url)
    return _http_get(url)

def _musicbrainz_search(artist: str, album: str) -> Optional[dict]:
    q = urllib.parse.quote(f'artist:"{artist}" AND release:"{album}"')
    url = f"https://musicbrainz.org/ws/2/release/?query={q}&limit=1&fmt=json"
    data = _http_get(url, timeout=10)
    if not data:
        return None
    try:
        j = json.loads(data)
        releases = j.get("releases", [])
        return releases[0] if releases else None
    except Exception:
        return None

def _caa_artwork(mbid: str) -> Optional[bytes]:
    url = f"https://coverartarchive.org/release/{mbid}/front-500"
    return _http_get(url, timeout=10)

def _deezer_artwork(artist: str, album: str) -> Optional[bytes]:
    q = urllib.parse.quote(f"{artist} {album}")
    url = f"https://api.deezer.com/search/album?q={q}&limit=1"
    data = _http_get(url)
    if not data:
        return None
    try:
        j = json.loads(data)
        items = j.get("data", [])
        if items:
            cover = items[0].get("cover_xl") or items[0].get("cover_big")
            if cover:
                return _http_get(cover)
    except Exception:
        pass
    return None

def fetch_metadata(alb: Album) -> None:
    """Fill in missing artist/album/year/artwork on alb (in-place). Blocking."""
    artist = alb.artist
    album  = alb.album

    if not artist or not album:
        return

    # iTunes first
    result = _itunes_search(artist, album)
    if result:
        if not alb.year:
            raw = result.get("releaseDate", "")[:4]
            if raw.isdigit():
                alb.year = raw
        if not alb.artwork_bytes:
            art_url = result.get("artworkUrl100", "")
            if art_url:
                alb.artwork_bytes = _itunes_artwork(art_url)

    # MusicBrainz + CAA fallback for artwork
    if not alb.artwork_bytes:
        mb = _musicbrainz_search(artist, album)
        if mb:
            mbid = mb.get("id")
            if mbid:
                alb.artwork_bytes = _caa_artwork(mbid)
            if not alb.year and not alb.year:
                date = mb.get("date", "")[:4]
                if date.isdigit():
                    alb.year = date

    # Deezer last resort for artwork
    if not alb.artwork_bytes:
        alb.artwork_bytes = _deezer_artwork(artist, album)

# ═══════════════════════════════════════════════════════════════════════════
# Tag writing + file moving
# ═══════════════════════════════════════════════════════════════════════════

def _tag_filename(track: Track, alb: Album) -> str:
    """Return clean filename for a track, without extension."""
    artist = _sanitize(alb.artist or "Unknown Artist")
    album  = _sanitize(alb.album  or "Unknown Album")
    title  = _sanitize(track.title or track.path.stem)

    if alb.is_single or len(alb.tracks) == 1:
        return f"{artist} - {title}"
    if track.track_num:
        return f"{artist} - {album} - {track.track_num:02d} - {title}"
    return f"{artist} - {album} - {title}"

def _write_tags_only(alb: Album) -> None:
    """Write metadata into each track's file in place. No rename, no move.

    This is what the Save Changes button uses. It calls the same per-format
    writers as write_tags_and_move but stops after the tag write so files
    stay at their current paths.
    """
    import sys
    for track in alb.tracks:
        path = track.path
        ext = path.suffix.lower()
        print(
            f"[FlyMeToTheRoon] save_tags: title={track.title!r} src={path}",
            file=sys.stderr, flush=True,
        )
        try:
            if ext == ".mp3":
                _write_mp3(track, alb)
            elif ext == ".flac":
                _write_flac(track, alb)
            elif ext in (".aif", ".aiff"):
                _write_aiff(track, alb)
            elif ext == ".m4a":
                _write_m4a(track, alb)
            # WAV — mutagen WAV tag support is patchy; skip
        except Exception as e:
            raise RuntimeError(f"Tag write failed for {path.name}: {e}") from e


def write_tags_and_move(alb: Album, move: bool = True) -> None:
    """Write ID3/Vorbis/MP4 tags, rename, and optionally move files to ROON_DIR."""
    import sys
    if move:
        dest_dir = ROON_DIR / alb.folder_name
        dest_dir.mkdir(parents=True, exist_ok=True)

    for track in alb.tracks:
        path = track.path
        ext  = path.suffix.lower()
        print(f"[FlyMeToTheRoon] write_and_move: title={track.title!r} src={path}", file=sys.stderr, flush=True)

        try:
            if ext == ".mp3":
                _write_mp3(track, alb)
            elif ext == ".flac":
                _write_flac(track, alb)
            elif ext in (".aif", ".aiff"):
                _write_aiff(track, alb)
            elif ext == ".m4a":
                _write_m4a(track, alb)
            # WAV — skip tags, just rename/move
        except Exception as e:
            raise RuntimeError(f"Tag write failed for {path.name}: {e}") from e

        new_name = _tag_filename(track, alb) + path.suffix
        dest = (ROON_DIR / alb.folder_name / new_name) if move else (path.parent / new_name)
        # If source == dest the file is already in the right place — just keep it.
        try:
            if path.resolve() == dest.resolve():
                track.path = dest
                continue
        except Exception:
            pass
        # Avoid clobbering a *different* existing file
        if dest.exists():
            base, suf = dest.stem, dest.suffix
            dest = dest.parent / f"{base}_dup{suf}"
        print(f"[FlyMeToTheRoon] {'moving' if move else 'renaming'}: {path.name!r} -> {dest.name!r}", file=sys.stderr, flush=True)
        shutil.move(str(path), str(dest))
        track.path = dest
        print(f"[FlyMeToTheRoon] done: {dest}", file=sys.stderr, flush=True)

def _write_mp3(track: Track, alb: Album) -> None:
    path = track.path
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall("TPE2")
    tags["TPE1"] = TPE1(encoding=3, text=alb.artist)
    tags["TALB"] = TALB(encoding=3, text=alb.album)
    tags["TIT2"] = TIT2(encoding=3, text=track.title)
    if alb.year:
        tags["TDRC"] = TDRC(encoding=3, text=alb.year)
    if track.track_num:
        tags["TRCK"] = TRCK(encoding=3, text=str(track.track_num))
    # Embed the tracklist into USLT whenever the user provided one — works for
    # DJ mixes, live albums, and single-file compilations alike.
    if alb.tracklist:
        tags.delall("USLT")
        tags.add(USLT(encoding=3, lang="eng", desc="", text=alb.tracklist))
    if alb.artwork_bytes:
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=alb.artwork_mime,
                      type=3, desc="Cover", data=alb.artwork_bytes))
    tags.save(str(path), v2_version=3)

def _write_flac(track: Track, alb: Album) -> None:
    audio = FLAC(str(track.path))
    audio.pop("albumartist", None)
    audio["artist"]      = alb.artist
    audio["album"]       = alb.album
    audio["title"]       = track.title
    if alb.year:
        audio["date"]    = alb.year
    if track.track_num:
        audio["tracknumber"] = str(track.track_num)
    if alb.tracklist:
        audio["lyrics"]  = alb.tracklist
    if alb.artwork_bytes:
        pic = Picture()
        pic.type = 3
        pic.mime = alb.artwork_mime
        pic.data = alb.artwork_bytes
        audio.clear_pictures()
        audio.add_picture(pic)
    audio.save(str(track.path))

def _write_aiff(track: Track, alb: Album) -> None:
    audio = AIFF(str(track.path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    for key in list(tags.keys()):
        if key.startswith("TPE2"):
            del tags[key]
    tags["TPE1"] = TPE1(encoding=3, text=alb.artist)
    tags["TALB"] = TALB(encoding=3, text=alb.album)
    tags["TIT2"] = TIT2(encoding=3, text=track.title)
    if alb.year:
        tags["TDRC"] = TDRC(encoding=3, text=alb.year)
    if alb.tracklist:
        for key in list(tags.keys()):
            if key.startswith("USLT"):
                del tags[key]
        tags.add(USLT(encoding=3, lang="eng", desc="", text=alb.tracklist))
    if alb.artwork_bytes:
        for key in list(tags.keys()):
            if key.startswith("APIC"):
                del tags[key]
        tags["APIC:Cover"] = APIC(encoding=3, mime=alb.artwork_mime,
                                  type=3, desc="Cover", data=alb.artwork_bytes)
    audio.save()

def _write_m4a(track: Track, alb: Album) -> None:
    audio = MP4(str(track.path))
    audio.tags.pop("aART", None)
    audio.tags["©ART"] = [alb.artist]
    audio.tags["©alb"] = [alb.album]
    audio.tags["©nam"] = [track.title]
    if alb.year:
        audio.tags["©day"] = [alb.year]
    if track.track_num:
        audio.tags["trkn"] = [(track.track_num, len(alb.tracks))]
    if alb.tracklist:
        audio.tags["©lyr"] = [alb.tracklist]
    if alb.artwork_bytes:
        fmt = MP4Cover.FORMAT_JPEG
        if alb.artwork_mime == "image/png":
            fmt = MP4Cover.FORMAT_PNG
        audio.tags["covr"] = [MP4Cover(alb.artwork_bytes, imageformat=fmt)]
    audio.save()

# ═══════════════════════════════════════════════════════════════════════════
# UI helpers
# ═══════════════════════════════════════════════════════════════════════════

def _tk_image(data: bytes, size: int = 120) -> Optional["ImageTk.PhotoImage"]:
    if not HAS_PIL or not data:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.thumbnail((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None

# ═══════════════════════════════════════════════════════════════════════════
# Main application
# ═══════════════════════════════════════════════════════════════════════════

class RoonTag:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"Fly Me To The Roon {VERSION}")
        self.root.configure(bg=BG)
        self.root.minsize(960, 640)
        self.root.geometry("1160x820")

        self.albums: list[Album] = []
        self.current_idx: Optional[int] = None
        self._artwork_cache: dict[int, "ImageTk.PhotoImage"] = {}
        self._loading_album: bool = False   # suppresses trace during _show_album

        self._setup_styles()
        self._build_ui()
        self._setup_field_traces()   # live-sync UI fields → album model
        self._setup_mac_open()
        self._try_setup_dnd()
        # Check for updates in the background (only when running the bundled app)
        if getattr(_sys, "frozen", False):
            self.root.after(1500, lambda: self._check_for_updates(silent=True))
        # Cmd+V pastes clipboard artwork — but only when focus is NOT on a text entry
        def _maybe_paste_artwork(e):
            w = self.root.focus_get()
            if isinstance(w, (tk.Entry, tk.Text)):
                return   # let the widget handle its own paste
            self._paste_artwork()
        self.root.bind("<Command-v>", _maybe_paste_artwork)

    # ── live field sync ──────────────────────────────────────────────────

    def _setup_field_traces(self):
        """Keep the Album model in sync with UI fields on every keystroke."""
        def _sync(*_):
            if self.current_idx is None or self._loading_album:
                return
            alb = self.albums[self.current_idx]
            alb.artist = self._artist_var.get().strip()
            alb.album  = self._album_var.get().strip()
            alb.year   = self._year_var.get().strip()
            new_title  = self._title_var.get().strip()
            if alb.tracks:
                alb.tracks[0].title = new_title
            alb.is_single = not bool(alb.album)
            self._update_preview(alb)
            # Update only the current queue entry label (cheaper than full rebuild)
            self._refresh_queue_item(self.current_idx)
        for var in (self._title_var, self._artist_var,
                    self._album_var, self._year_var):
            var.trace_add("write", _sync)

    def _update_preview(self, alb: "Album" = None):
        """Show the filename that will be written, live."""
        if alb is None:
            if self.current_idx is None or not self.albums:
                self._preview_var.set("")
                return
            alb = self.albums[self.current_idx]
        if not alb.tracks:
            self._preview_var.set("")
            return
        fname = _tag_filename(alb.tracks[0], alb) + alb.tracks[0].path.suffix
        self._preview_var.set(f"→  {fname}")

    # ── styles ──────────────────────────────────────────────────────────

    def _setup_menubar(self):
        """Build the native macOS menu bar. Lives at the top of the screen."""
        menubar = tk.Menu(self.root)

        # Apple/App menu (name='apple' is treated specially on macOS)
        app_menu = tk.Menu(menubar, name="apple", tearoff=False)
        menubar.add_cascade(menu=app_menu)
        app_menu.add_command(label="About Fly Me To The Roon",
                             command=self._show_about)
        app_menu.add_separator()
        app_menu.add_command(label="Check for Updates…",
                             command=lambda: self._check_for_updates(silent=False))

        # File menu
        file_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Add Files…",
                              accelerator="Cmd+O",
                              command=self._add_files)
        file_menu.add_command(label="Add Folder…",
                              accelerator="Cmd+Shift+O",
                              command=self._add_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Clear Completed",
                              command=self._clear_done)

        # Tags menu — actions that act on the current selection
        tags_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Tags", menu=tags_menu)
        tags_menu.add_command(label="Fetch Metadata + Artwork",
                              accelerator="Cmd+F",
                              command=self._fetch_current_metadata)
        tags_menu.add_command(label="Fetch All",
                              command=self._fetch_all_metadata)
        tags_menu.add_separator()
        tags_menu.add_command(label="Save Changes",
                              accelerator="Cmd+S",
                              command=self._apply_fields)
        tags_menu.add_command(label="Process All",
                              accelerator="Cmd+Return",
                              command=self._process_all)

        # Settings
        settings_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(label="Roon Folder…",
                                  command=self._set_roon_dir)

        self.root.configure(menu=menubar)

        # Keyboard shortcuts (work whether or not the menu consumes them)
        self.root.bind_all("<Command-o>",       lambda e: self._add_files())
        self.root.bind_all("<Command-O>",       lambda e: self._add_folder())
        self.root.bind_all("<Command-f>",       lambda e: self._fetch_current_metadata())
        self.root.bind_all("<Command-s>",       lambda e: self._apply_fields())
        self.root.bind_all("<Command-Return>",  lambda e: self._process_all())

    def _setup_styles(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG,
                    fieldbackground=BG2, borderwidth=0, relief="flat")
        s.configure("TFrame", background=BG)
        s.configure("Card.TFrame", background=BG2)
        s.configure("TLabel", background=BG, foreground=FG,
                    font=(FONT_BODY, 12))
        s.configure("Card.TLabel", background=BG2, foreground=FG,
                    font=(FONT_BODY, 12))
        s.configure("Dim.TLabel", background=BG, foreground=FG_DIM,
                    font=(FONT_BODY, 10, "bold"))

        # Secondary button — pill-shaped, hairline border
        s.configure("TButton", background=BG2, foreground=FG,
                    padding=(14, 7), relief="flat",
                    borderwidth=1, bordercolor=BORDER,
                    focusthickness=0, focuscolor=BG2,
                    font=(FONT_BODY, 12))
        s.map("TButton",
              background=[("active", BG_HOVER), ("pressed", BG3)],
              bordercolor=[("active", BORDER), ("pressed", BORDER)],
              foreground=[("active", FG), ("pressed", FG)])

        # Primary button — filled violet, no border
        s.configure("Accent.TButton", background=ACCENT, foreground="#FFFFFF",
                    padding=(18, 8), relief="flat", borderwidth=0,
                    focusthickness=0, focuscolor=ACCENT,
                    font=(FONT_BODY, 12, "bold"))
        s.map("Accent.TButton",
              background=[("active", ACCENT_HI), ("pressed", ACCENT_LO)],
              foreground=[("active", "#FFFFFF"), ("pressed", "#FFFFFF")])

        # Ghost button — text-only, for lightweight actions
        s.configure("Ghost.TButton", background=BG, foreground=ACCENT,
                    padding=(8, 5), relief="flat", borderwidth=0,
                    focusthickness=0, focuscolor=BG,
                    font=(FONT_BODY, 12))
        s.map("Ghost.TButton",
              background=[("active", BG_HOVER), ("pressed", BG3)],
              foreground=[("active", ACCENT_HI), ("pressed", ACCENT_LO)])

        # Treeview
        s.configure("Treeview", background=BG2, foreground=FG,
                    fieldbackground=BG2, rowheight=30, borderwidth=0,
                    font=(FONT_BODY, 12))
        s.configure("Treeview.Heading", background=BG2, foreground=FG_DIM,
                    relief="flat", padding=(10, 6),
                    font=(FONT_BODY, 10, "bold"))
        s.map("Treeview",
              background=[("selected", SEL)],
              foreground=[("selected", FG)])
        s.map("Treeview.Heading",
              background=[("active", BG_HOVER)])

        # Entries
        s.configure("TEntry", fieldbackground=BG2, foreground=FG,
                    insertcolor=FG, relief="flat", padding=(10, 7),
                    borderwidth=1, bordercolor=BORDER,
                    font=(FONT_BODY, 12))
        s.map("TEntry", bordercolor=[("focus", ACCENT)])

        # Check / Scroll
        s.configure("TCheckbutton", background=BG, foreground=FG,
                    font=(FONT_BODY, 12))
        s.map("TCheckbutton", background=[("active", BG)])
        s.configure("Card.TCheckbutton", background=BG2, foreground=FG,
                    font=(FONT_BODY, 12))
        s.map("Card.TCheckbutton", background=[("active", BG2)])
        s.configure("TScrollbar", background=BG3, troughcolor=BG,
                    arrowcolor=FG_DIM, relief="flat", borderwidth=0)

    # ── layout ──────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── native menu bar (macOS picks this up as top-of-screen) ──────
        self._setup_menubar()

        # ── toolbar ─────────────────────────────────────────────────────
        bar = tk.Frame(self.root, bg=BG2)
        bar.pack(side="top", fill="x")
        tk.Frame(bar, bg=BG2, height=16).pack(side="top", fill="x")  # top breathing

        bar_inner = tk.Frame(bar, bg=BG2)
        bar_inner.pack(side="top", fill="x", padx=22, pady=(0, 16))

        # Left cluster — brand + bring work in
        left_cluster = tk.Frame(bar_inner, bg=BG2)
        left_cluster.pack(side="left")
        tk.Label(left_cluster, text="Fly Me To The Roon", bg=BG2, fg=FG,
                 font=(FONT_DISPLAY, 22, "bold")).pack(side="left", padx=(0, 22))
        ttk.Button(left_cluster, text="＋  Add Files",
                   command=self._add_files).pack(side="left", padx=(0, 6))
        ttk.Button(left_cluster, text="＋  Add Folder",
                   command=self._add_folder).pack(side="left", padx=(0, 6))

        # Right cluster — do the work
        right_cluster = tk.Frame(bar_inner, bg=BG2)
        right_cluster.pack(side="right")

        self._move_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(right_cluster, text="Move to Roon",
                        variable=self._move_var,
                        style="Card.TCheckbutton").pack(side="left", padx=(0, 14))
        ttk.Button(right_cluster, text="Fetch All",
                   command=self._fetch_all_metadata).pack(side="left", padx=(0, 6))
        ttk.Button(right_cluster, text="Process All",
                   command=self._process_all,
                   style="Accent.TButton").pack(side="left")

        # Toolbar bottom border
        tk.Frame(self.root, bg=BORDER, height=1).pack(side="top", fill="x")

        # ── status bar ──────────────────────────────────────────────────
        tk.Frame(self.root, bg=BORDER, height=1).pack(side="bottom", fill="x")
        sbar = tk.Frame(self.root, bg=BG2, pady=5)
        sbar.pack(side="bottom", fill="x")
        self._status_var = tk.StringVar(value="Add files to begin.")
        tk.Label(sbar, textvariable=self._status_var,
                 bg=BG2, fg=FG_DIM, font=(FONT_BODY, 11)).pack(side="left", padx=12)

        # ── main paned area ──────────────────────────────────────────────
        paned = tk.PanedWindow(self.root, orient="horizontal",
                               bg=BORDER, sashwidth=1, sashrelief="flat",
                               handlesize=0)
        paned.pack(fill="both", expand=True)

        # ── left: queue ──────────────────────────────────────────────────
        left = tk.Frame(paned, bg=BG3, width=280)
        paned.add(left, minsize=200)

        q_hdr = tk.Frame(left, bg=BG3)
        q_hdr.pack(fill="x")
        tk.Label(q_hdr, text="QUEUE", bg=BG3, fg=FG_DIM,
                 font=(FONT_BODY, 10, "bold")).pack(side="left", padx=12, pady=(11, 4))
        tk.Frame(left, bg=BORDER, height=1).pack(fill="x")

        # Empty-state help label
        self._empty_lbl = tk.Label(
            left,
            text="Click Add Files… or Add Folder…\nto load music into the queue.\n\n"
                 "You can also drag files onto the\nFly Me To The Roon icon in your Dock.",
            bg=BG3, fg=FG_DIM, font=(FONT_BODY, 12), justify="center",
        )
        self._empty_lbl.pack(pady=30, padx=10)

        lf = tk.Frame(left, bg=BG3)
        lf.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(lf, orient="vertical")
        self._queue = tk.Listbox(
            lf,
            bg=BG3, fg=FG, selectbackground=SEL, selectforeground=FG,
            activestyle="none", highlightthickness=0,
            relief="flat", borderwidth=0,
            font=(FONT_BODY, 13),
            yscrollcommand=sb.set,
        )
        sb.configure(command=self._queue.yview)
        sb.pack(side="right", fill="y")
        self._queue.pack(fill="both", expand=True)
        self._queue.bind("<<ListboxSelect>>", self._on_queue_select)

        # ── right: detail ────────────────────────────────────────────────
        right = tk.Frame(paned, bg=BG)
        paned.add(right, minsize=560)

        # Scrollable detail area
        canvas = tk.Canvas(right, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self._detail_canvas = canvas

        self._detail = tk.Frame(canvas, bg=BG)
        self._detail_window = canvas.create_window((0, 0), window=self._detail, anchor="nw")

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        def _on_canvas_configure(e):
            canvas.itemconfig(self._detail_window, width=e.width)

        self._detail.bind("<Configure>", _on_frame_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        # Two-finger trackpad / mousewheel scroll. Bind globally only while
        # the cursor is over the detail panel so the queue listbox keeps its
        # own scroll behavior.
        def _on_wheel(e):
            # macOS Tk delivers small deltas (±1 per wheel notch / per
            # trackpad event); scroll one unit per delta.
            canvas.yview_scroll(int(-e.delta), "units")
        def _bind_wheel(_e):
            self.root.bind_all("<MouseWheel>", _on_wheel)
        def _unbind_wheel(_e):
            self.root.unbind_all("<MouseWheel>")
        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)
        self._detail.bind("<Enter>", _bind_wheel)
        self._detail.bind("<Leave>", _unbind_wheel)

        self._build_detail_panel()

    def _make_card(self, parent, title: str, pady_top: int = 0):
        """Build a white rounded-feeling card with a section header label.

        Returns the card body frame (child widgets should be packed with their
        own padding). The card packs itself into `parent` so the caller can
        chain cards top-to-bottom simply by calling this in order.
        """
        wrap_pack = dict(fill="x", padx=22, pady=(pady_top, 16))
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(**wrap_pack)

        card = tk.Frame(wrap, bg=BG2, highlightbackground=BORDER,
                        highlightthickness=1)
        card.pack(fill="x")

        hdr = tk.Frame(card, bg=BG2)
        hdr.pack(fill="x", padx=22, pady=(14, 6))
        tk.Label(hdr, text=title.upper(), bg=BG2, fg=FG_DIM,
                 font=(FONT_BODY, 10, "bold")).pack(anchor="w")

        # Attach the wrap (and its pack args) so callers can show/hide the
        # whole card — surrounding padding included — without leaving a gap.
        card._wrap = wrap
        card._wrap_pack = wrap_pack
        return card

    def _build_detail_panel(self):
        d = self._detail

        # ── section: Metadata card (artwork + fields) ─────────────────────
        meta_card = self._make_card(d, "Metadata", pady_top=18)
        top = tk.Frame(meta_card, bg=BG2)
        top.pack(fill="x", padx=18, pady=(4, 16))

        # Artwork
        art_col = tk.Frame(top, bg=BG2)
        art_col.pack(side="left", padx=(0, 22))

        art_outer = tk.Frame(art_col, bg=BORDER, width=160, height=160)
        art_outer.pack()
        art_outer.pack_propagate(False)
        self._art_label = tk.Label(art_outer, bg=BG3, fg=FG_DIM,
                                   text="No Artwork", font=(FONT_BODY, 11),
                                   justify="center", cursor="hand2")
        self._art_label.pack(expand=True, fill="both", padx=1, pady=1)
        self._art_label.bind("<Button-1>", lambda e: self._choose_artwork())

        art_btns = tk.Frame(art_col, bg=BG2)
        art_btns.pack(fill="x", pady=(6, 0))
        ttk.Button(art_btns, text="Paste",
                   command=self._paste_artwork).pack(side="left", padx=(0, 4))
        ttk.Button(art_btns, text="Choose…",
                   command=self._choose_artwork).pack(side="left")

        # Fields
        ff = tk.Frame(top, bg=BG2)
        ff.pack(side="left", fill="x", expand=True)

        def lbl(text):
            tk.Label(ff, text=text, bg=BG2, fg=FG_DIM,
                     font=(FONT_BODY, 10, "bold")).pack(anchor="w", pady=(6, 2))

        self._title_var  = tk.StringVar()
        self._artist_var = tk.StringVar()
        self._album_var  = tk.StringVar()
        self._year_var   = tk.StringVar()

        lbl("TITLE")
        # Use tk.Entry (not ttk) so font + textvariable both work reliably
        self._title_entry = tk.Entry(
            ff, textvariable=self._title_var, width=42,
            bg=BG3, fg=FG, insertbackground=FG, relief="flat",
            font=(FONT_BODY, 13),
            highlightbackground=BORDER, highlightthickness=1,
        )
        self._title_entry.pack(fill="x", ipady=3)

        def _force_title_save(event=None):
            import sys
            if self.current_idx is None or not self.albums:
                return
            alb = self.albums[self.current_idx]
            # Read the raw entry (don't strip yet — stripping + writing back to
            # the StringVar on every keystroke was eating spaces the user typed).
            raw = self._title_entry.get()
            # Store the raw value on the track model; _tag_filename / write_and_move
            # will sanitize at save time.
            if alb.tracks:
                if raw != alb.tracks[0].title:
                    print(f"[FlyMeToTheRoon] title key event: {alb.tracks[0].title!r} → {raw!r}", file=sys.stderr, flush=True)
                alb.tracks[0].title = raw
                # Do NOT call self._title_var.set(raw) — the Entry's textvariable
                # is already _title_var, so the var is up-to-date. Re-setting it
                # here forces a redraw that collapses trailing spaces mid-typing.
            self._update_preview(alb)

        self._title_entry.bind("<KeyRelease>", _force_title_save)
        self._title_entry.bind("<FocusOut>", _force_title_save)

        lbl("ARTIST")
        ttk.Entry(ff, textvariable=self._artist_var, width=42).pack(fill="x", ipady=3)

        lbl("ALBUM    —  leave blank for a single")
        ttk.Entry(ff, textvariable=self._album_var, width=42).pack(fill="x", ipady=3)

        yr_row = tk.Frame(ff, bg=BG2)
        yr_row.pack(fill="x", pady=(0, 2))
        yl = tk.Frame(yr_row, bg=BG2)
        yl.pack(side="left")
        tk.Label(yl, text="YEAR", bg=BG2, fg=FG_DIM,
                 font=(FONT_BODY, 10, "bold")).pack(anchor="w", pady=(6, 2))
        ttk.Entry(yl, textvariable=self._year_var, width=8).pack(anchor="w", ipady=3)

        # Live filename preview
        self._preview_var = tk.StringVar(value="")
        tk.Label(ff, textvariable=self._preview_var,
                 bg=BG2, fg=ACCENT,
                 font=(FONT_BODY, 11), anchor="w",
                 wraplength=420).pack(fill="x", pady=(10, 0))

        # Fetch + Save
        btn_row = tk.Frame(ff, bg=BG2)
        btn_row.pack(anchor="w", pady=(14, 0))
        ttk.Button(btn_row, text="Fetch Metadata + Artwork",
                   command=self._fetch_current_metadata).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Save Changes",
                   command=self._apply_fields).pack(side="left")

        # ── section: Album Type ──────────────────────────────────────────
        type_card = self._make_card(d, "Album Type")
        tf = tk.Frame(type_card, bg=BG2)
        tf.pack(fill="x", padx=18, pady=(2, 16))

        self._is_dj_var   = tk.BooleanVar()
        self._is_live_var = tk.BooleanVar()
        self._is_comp_var = tk.BooleanVar()

        flags_row = tk.Frame(tf, bg=BG2)
        flags_row.pack(anchor="w")
        ttk.Checkbutton(flags_row, text="DJ Mix / Radio Set",
                        variable=self._is_dj_var,
                        command=self._on_type_change,
                        style="Card.TCheckbutton").pack(side="left", padx=(0, 18))
        ttk.Checkbutton(flags_row, text="Live Album",
                        variable=self._is_live_var,
                        command=self._on_type_change,
                        style="Card.TCheckbutton").pack(side="left", padx=(0, 18))
        ttk.Checkbutton(flags_row, text="Compilation",
                        variable=self._is_comp_var,
                        command=self._on_type_change,
                        style="Card.TCheckbutton").pack(side="left")

        tk.Label(tf, bg=BG2, fg=FG_MUTED,
                 text=("Flagging any of these reveals the Tracklist field "
                       "below — useful for single-file mixes, live sets, or "
                       "compilations where you want a full tracklist in the "
                       "Lyrics tag so Roon can read it."),
                 font=(FONT_BODY, 11), wraplength=680,
                 justify="left").pack(anchor="w", pady=(10, 0))

        # ── section: Tracklist (conditional) ─────────────────────────────
        self._tl_card = self._make_card(d, "Tracklist  —  embedded as lyrics")
        tl_body = tk.Frame(self._tl_card, bg=BG2)
        tl_body.pack(fill="both", expand=True, padx=18, pady=(2, 16))
        self._tl_text = tk.Text(
            tl_body,
            bg=BG3, fg=FG, insertbackground=FG,
            relief="flat", borderwidth=0,
            highlightbackground=BORDER, highlightthickness=1,
            font=(FONT_MONO, 11), height=8, wrap="word",
            padx=10, pady=8,
        )
        self._tl_text.pack(fill="both", expand=True)
        tk.Label(tl_body, bg=BG2, fg=FG_MUTED,
                 text="One track per line. Example: 00:00 · Artist – Title",
                 font=(FONT_BODY, 10),
                 justify="left").pack(anchor="w", pady=(6, 0))

        # ── section: Tracks (multi-track albums) ─────────────────────────
        self._tracks_card = self._make_card(d, "Tracks")
        tracks_body = tk.Frame(self._tracks_card, bg=BG2)
        tracks_body.pack(fill="both", expand=True, padx=18, pady=(2, 16))

        hint_row = tk.Frame(tracks_body, bg=BG2)
        hint_row.pack(fill="x", pady=(0, 8))
        tk.Label(hint_row, bg=BG2, fg=FG_MUTED,
                 text="Double-click a cell to edit. ⌘-click or Shift-click "
                      "to multi-select.",
                 font=(FONT_BODY, 10),
                 justify="left").pack(side="left")
        ttk.Button(hint_row, text="Auto-number",
                   command=self._autonumber_tracks,
                   style="Ghost.TButton").pack(side="right")

        tf2 = tk.Frame(tracks_body, bg=BG2)
        tf2.pack(fill="both", expand=True)
        sb2 = ttk.Scrollbar(tf2, orient="vertical")
        self._track_tree = ttk.Treeview(
            tf2,
            columns=("#", "title", "artist", "dur"),
            show="headings",
            yscrollcommand=sb2.set,
            selectmode="extended",
            height=7,
        )
        sb2.configure(command=self._track_tree.yview)
        sb2.pack(side="right", fill="y")
        self._track_tree.pack(fill="both", expand=True)
        self._track_tree.heading("#",      text="#")
        self._track_tree.heading("title",  text="Title")
        self._track_tree.heading("artist", text="Artist")
        self._track_tree.heading("dur",    text="Dur")
        self._track_tree.column("#",      width=36, stretch=False, anchor="center")
        self._track_tree.column("title",  width=300)
        self._track_tree.column("artist", width=200)
        self._track_tree.column("dur",    width=60, anchor="center")
        self._tree_entry = None
        self._track_tree.bind("<Double-1>", self._on_tree_dbl)

        self._set_detail_enabled(False)

    # ── DnD ─────────────────────────────────────────────────────────────

    def _try_setup_dnd(self):
        """Set up drag-and-drop. Tries tkinterdnd2 widget API first, then raw Tcl."""
        import sys

        # ── Approach 1: tkinterdnd2 widget API (requires TkinterDnD.Tk root) ──
        if HAS_DND:
            try:
                for widget in [self.root, self._queue, self._detail]:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop)
                self._status_var.set("Ready — drag files onto this window to add them.")
                print("[DnD] tkinterdnd2 registered ok", file=sys.stderr, flush=True)
                return
            except Exception as e:
                print(f"[DnD] tkinterdnd2 registration failed: {e}", file=sys.stderr, flush=True)

        # ── Approach 2: raw Tcl tkdnd ─────────────────────────────────────────
        try:
            lib = _tkdnd_lib
            if not lib:
                print("[DnD] no tkdnd library found — run build_tkdnd.sh", file=sys.stderr, flush=True)
                return
            self.root.tk.eval(f'lappend auto_path {{{lib}}}')
            self.root.tk.call("package", "require", "tkdnd")
            self.root.tk.call("tkdnd::drop_target", "register", ".", "DND_Files")
            self.root.tk.eval('bind . <<DropEnter>> { return copy }')
            self.root.tk.eval('bind . <<DropPosition>> { return copy }')
            self.root.tk.eval(
                'bind . <<Drop:DND_Files>> { set ::roontaggr_dropped %D ; event generate . <<RT_FilesDropped>> ; return copy }'
            )
            self.root.bind("<<RT_FilesDropped>>", self._on_tcl_drop)
            self._status_var.set("Ready — drag files onto this window to add them.")
            print("[DnD] raw Tcl tkdnd registered ok", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[DnD] setup failed: {e}", file=sys.stderr, flush=True)

    def _on_tcl_drop(self, event):
        try:
            raw = self.root.tk.globalgetvar("roontaggr_dropped")
            print(f"[DnD] raw drop data: {raw!r}", file=sys.stderr, flush=True)
            # Tcl returns a tuple/list of paths
            if isinstance(raw, (list, tuple)):
                paths = [Path(p) for p in raw if p]
            else:
                paths = [Path(p) for p in self.root.tk.splitlist(str(raw)) if p]
            print(f"[DnD] parsed paths: {paths}", file=sys.stderr, flush=True)
            self._ingest(paths)
        except Exception as _e:
            import traceback as _tb
            print(f"[DnD] _on_tcl_drop error: {_e}\n{_tb.format_exc()}", file=sys.stderr, flush=True)
            self._status(f"Drop error: {_e}")

    def _setup_mac_open(self):
        try:
            self.root.createcommand("::tk::mac::OpenDocument", self._mac_open_files)
        except Exception:
            pass

    def _mac_open_files(self, *args):
        paths = [Path(p) for p in args if p]
        if paths:
            self._ingest(paths)

    # ── artwork helpers ──────────────────────────────────────────────────

    def _paste_artwork(self):
        """Paste image from macOS clipboard into the current album's artwork."""
        if self.current_idx is None:
            self._status("Select an item in the queue first, then paste artwork.")
            return

        data: Optional[bytes] = None

        # ── Try PIL ImageGrab first ──────────────────────────────────────
        if HAS_PIL:
            try:
                from PIL import ImageGrab, Image as _Img
                clip = ImageGrab.grabclipboard()
                if isinstance(clip, list):
                    # macOS sometimes gives a list of file paths
                    for p in clip:
                        if Path(str(p)).suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'):
                            raw = Path(str(p)).read_bytes()
                            buf = io.BytesIO()
                            _Img.open(io.BytesIO(raw)).convert("RGB").save(buf, format="JPEG", quality=92)
                            data = buf.getvalue()
                            break
                elif isinstance(clip, _Img.Image):
                    buf = io.BytesIO()
                    clip.convert("RGB").save(buf, format="JPEG", quality=92)
                    data = buf.getvalue()
                # If clip is a string (text) or anything else, skip silently
            except Exception as e:
                import sys; print(f"[FlyMeToTheRoon] PIL clipboard failed: {e}", file=sys.stderr, flush=True)

        # ── macOS fallback: read PNG from clipboard via osascript ────────
        if data is None:
            try:
                script = (
                    'set tmpFile to (POSIX path of (path to temporary items)) & "roontaggr_clip.png"\n'
                    'set imgData to (the clipboard as «class PNGf»)\n'
                    'set fRef to open for access POSIX file tmpFile with write permission\n'
                    'set eof of fRef to 0\n'
                    'write imgData to fRef\n'
                    'close access fRef\n'
                    'return tmpFile'
                )
                r = _sp.run(["osascript", "-e", script],
                            capture_output=True, text=True, timeout=6)
                if r.returncode == 0:
                    tmp = Path(r.stdout.strip())
                    if tmp.exists() and tmp.stat().st_size > 0:
                        data = tmp.read_bytes()
                        tmp.unlink(missing_ok=True)
            except Exception as e:
                import sys; print(f"[FlyMeToTheRoon] osascript clipboard failed: {e}", file=sys.stderr, flush=True)

        if data is None:
            self._status("No image on clipboard — right-click artwork in a browser and choose Copy Image, then paste here.")
            return

        alb = self.albums[self.current_idx]
        alb.artwork_bytes = data
        alb.artwork_mime  = "image/jpeg"
        self._artwork_cache.pop(id(alb), None)
        self._refresh_artwork(alb)
        self._status("Artwork pasted from clipboard.")

    def _choose_artwork(self):
        """Open a file picker to choose an image file as artwork."""
        if self.current_idx is None:
            self._status("Select an item in the queue first, then choose artwork.")
            return
        path = filedialog.askopenfilename(
            title="Choose artwork image",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.webp *.bmp"),
                       ("All files", "*.*")],
        )
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            mime = "image/jpeg" if path.lower().endswith((".jpg", ".jpeg")) else "image/png"
            if HAS_PIL:
                img = Image.open(io.BytesIO(data))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=92)
                data = buf.getvalue()
                mime = "image/jpeg"
            alb = self.albums[self.current_idx]
            alb.artwork_bytes = data
            alb.artwork_mime  = mime
            self._artwork_cache.pop(id(alb), None)
            self._refresh_artwork(alb)
            self._status("Artwork loaded from file.")
        except Exception as e:
            self._status(f"Could not load artwork: {e}")

    def _on_drop(self, event):
        raw = event.data
        paths, i = [], 0
        while i < len(raw):
            if raw[i] == "{":
                end = raw.index("}", i)
                paths.append(raw[i+1:end])
                i = end + 2
            else:
                j = raw.find(" ", i)
                if j == -1:
                    paths.append(raw[i:]); break
                paths.append(raw[i:j]); i = j + 1
        self._ingest([Path(p) for p in paths if p])

    # ── ingesting ───────────────────────────────────────────────────────

    def _add_files(self):
        paths = filedialog.askopenfilenames(
            title="Select music files",
            filetypes=[("Audio files", "*.mp3 *.flac *.aif *.aiff *.wav *.m4a"),
                       ("All files", "*.*")],
        )
        if paths:
            self._ingest([Path(p) for p in paths])

    def _add_folder(self):
        folder = filedialog.askdirectory(title="Select folder")
        if folder:
            self._ingest([Path(folder)])

    def _ingest(self, paths: list[Path]):
        files = _music_files(paths)
        if not files:
            self._status("No music files found.")
            return

        # Sniff container format — catch files with the wrong extension
        # (e.g. WebM/Opus renamed as .mp3 from a YouTube download). Roon
        # silently rejects these, so we'd rather warn the user now.
        fakes = []
        for p in files:
            ok, expected, detected = _verify_extension(p)
            if not ok:
                fakes.append((p, expected, detected))

        if fakes:
            diag_lines = []
            cmd_lines = []
            for p, expected, detected in fakes:
                diag_lines.append(
                    f"  • {p.name}\n"
                    f"       extension: .{expected}    actual content: {detected}"
                )
                # Build a copy-paste-ready ffmpeg command using the file's real
                # absolute path and a "-FIXED" sibling as output. Shell-quote
                # via Python's shlex so paths with spaces/special chars work.
                import shlex
                src = str(p)
                dst = str(p.with_name(p.stem + "-FIXED" + p.suffix))
                cmd_lines.append(
                    f"ffmpeg -i {shlex.quote(src)} -map_metadata 0 "
                    f"-c:a libmp3lame -b:a 320k {shlex.quote(dst)}"
                )

            msg = (
                "These file(s) have the wrong extension for their "
                "actual content:\n\n"
                + "\n\n".join(diag_lines)
                + "\n\nRoon silently rejects files like these. To fix, "
                "paste this into Terminal (one command per bad file):\n\n"
                + "\n\n".join(cmd_lines)
                + "\n\nAfter ffmpeg finishes, each file will sit next to the "
                "original with \"-FIXED\" in the name. Re-add the -FIXED "
                "version to Fly Me To The Roon (and delete the original once you've "
                "confirmed it plays).\n\n"
                "Add the original (broken) file(s) to the queue anyway?"
            )
            if not messagebox.askyesno("Misnamed audio file(s) detected", msg):
                bad_paths = {p for p, _, _ in fakes}
                files = [p for p in files if p not in bad_paths]
                if not files:
                    self._status("All dropped files had wrong extensions — nothing added.")
                    return

        # Grouping rule (matches the user's mental model of "one folder = one
        # album"):
        #   - Each dropped folder becomes one album, recursing into ALL its
        #     subfolders. So a release split across CD1/, CD2/, CD3/ stays one
        #     album with all its tracks.
        #   - Dropping multiple folders gives you one album per folder.
        #   - Loose files dropped together (no folder) become one album.
        kept = set(files)
        groups: list[list[Path]] = []
        loose: list[Path] = []
        for p in paths:
            if p.is_dir():
                sub = sorted(
                    f for f in p.rglob("*")
                    if f.is_file() and f.suffix.lower() in MUSIC_EXTS and f in kept
                )
                if sub:
                    groups.append(sub)
            elif p.suffix.lower() in MUSIC_EXTS and p in kept:
                loose.append(p)
        if loose:
            groups.append(loose)
        # Safety net: if grouping somehow yielded nothing (e.g. all files were
        # filtered by the fakes prompt and `paths` doesn't intersect `files`),
        # fall back to one big group so we never silently drop input.
        if not groups and files:
            groups = [files]

        added = 0
        for group in groups:
            alb = read_album_tags(group)
            self.albums.append(alb)
            added += 1

        self._refresh_queue()
        self._status(f"Added {len(files)} file(s) in {added} group(s).")

        # Auto-select the newly added item
        idx = len(self.albums) - 1
        self._queue.selection_clear(0, "end")
        self._queue.selection_set(idx)
        self._queue.see(idx)
        self._show_album(idx)

    # ── queue list ──────────────────────────────────────────────────────

    def _refresh_queue(self):
        self._queue.delete(0, "end")
        icons = {"pending": "○", "fetching": "↻", "ready": "●",
                 "done": "✓", "error": "✗"}
        for alb in self.albums:
            icon = icons.get(alb.status, "○")
            self._queue.insert("end", f"  {icon}  {alb.display_name}")
        if self.albums:
            self._empty_lbl.pack_forget()
        else:
            self._empty_lbl.pack(pady=30, padx=10)

    def _refresh_queue_item(self, idx: int):
        """Update a single queue entry without rebuilding the whole list."""
        if idx is None or idx < 0 or idx >= len(self.albums):
            return
        icons = {"pending": "○", "fetching": "↻", "ready": "●",
                 "done": "✓", "error": "✗"}
        alb = self.albums[idx]
        icon = icons.get(alb.status, "○")
        self._queue.delete(idx)
        self._queue.insert(idx, f"  {icon}  {alb.display_name}")
        self._queue.selection_set(idx)

    def _on_queue_select(self, event=None):
        sel = self._queue.curselection()
        if sel:
            self._show_album(sel[0])

    def _show_album(self, idx: int):
        if idx < 0 or idx >= len(self.albums):
            return
        self._save_current_fields()
        self.current_idx = idx
        alb = self.albums[idx]
        self._set_detail_enabled(True)

        # Core fields — always populated from tags
        # Guard prevents the _sync trace from cross-contaminating fields
        # while we're setting them one-by-one from the new album's data.
        self._loading_album = True
        try:
            first_title = alb.tracks[0].title if alb.tracks else ""
            self._title_var.set(first_title)
            self._artist_var.set(alb.artist)
            self._album_var.set(alb.album)
            self._year_var.set(alb.year)
            self._is_dj_var.set(alb.is_dj_mix)
            self._is_live_var.set(alb.is_live)
            self._is_comp_var.set(alb.is_compilation)
        finally:
            self._loading_album = False
        self._update_preview(alb)

        # Artwork
        self._artwork_cache.pop(id(alb), None)
        self._refresh_artwork(alb)

        # Track table
        for item in self._track_tree.get_children():
            self._track_tree.delete(item)
        for t in alb.tracks:
            dur = (f"{int(t.duration//60)}:{int(t.duration%60):02d}"
                   if t.duration else "")
            self._track_tree.insert("", "end",
                                    values=(t.track_num or "", t.title, t.artist, dur))

        # Show tracks card only for multi-track items
        if len(alb.tracks) > 1:
            self._tracks_card._wrap.pack(**self._tracks_card._wrap_pack)
        else:
            self._tracks_card._wrap.pack_forget()

        # Tracklist
        self._on_type_change()
        self._tl_text.delete("1.0", "end")
        if alb.tracklist:
            self._tl_text.insert("1.0", alb.tracklist)

        self._update_preview(alb)

    def _refresh_artwork(self, alb: Album):
        cache_key = id(alb)
        if alb.artwork_bytes and HAS_PIL:
            if cache_key not in self._artwork_cache:
                ph = _tk_image(alb.artwork_bytes, size=150)
                if ph:
                    self._artwork_cache[cache_key] = ph
            ph = self._artwork_cache.get(cache_key)
            if ph:
                self._art_label.configure(image=ph, text="")
                self._art_label.image = ph
                return
        self._art_label.configure(image="", text="No Artwork")

    def _on_type_change(self):
        """Show the Tracklist section whenever the user flags this album as a
        DJ mix, live album, or compilation. Those are the cases where a single
        file (or grouping) contains multiple pieces of music, and embedding a
        tracklist into Lyrics is what lets Roon identify the pieces."""
        show_tl = (self._is_dj_var.get()
                   or self._is_live_var.get()
                   or self._is_comp_var.get())
        if show_tl:
            self._tl_card._wrap.pack(**self._tl_card._wrap_pack)
        else:
            self._tl_card._wrap.pack_forget()

    def _set_detail_enabled(self, on: bool):
        state = "normal" if on else "disabled"
        for w in self._walk(self._detail):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass

    def _walk(self, w):
        yield w
        for c in w.winfo_children():
            yield from self._walk(c)

    # ── saving fields ────────────────────────────────────────────────────

    def _apply_fields(self):
        """Save Changes — write the current metadata to the underlying audio
        files in place (no rename, no move). Process All still does the full
        write + rename + move-to-Roon-folder flow.
        """
        if self.current_idx is None:
            return
        self._save_current_fields()
        alb = self.albums[self.current_idx]
        try:
            _write_tags_only(alb)
        except Exception as e:
            import sys, traceback
            traceback.print_exc(file=sys.stderr)
            messagebox.showerror(
                "Save failed",
                f"Couldn't write tags to one of the files:\n\n{e}"
            )
            self._status("Save failed.")
            return
        alb.status = "ready"
        self._refresh_queue()
        n = len(alb.tracks)
        self._status(
            f"Saved tags to {n} file{'s' if n != 1 else ''} — "
            "ready to Process All."
        )

    def _save_current_fields(self):
        if self.current_idx is None:
            return
        alb = self.albums[self.current_idx]
        alb.artist         = self._artist_var.get().strip()
        alb.album          = self._album_var.get().strip()
        alb.year           = self._year_var.get().strip()
        alb.is_dj_mix      = self._is_dj_var.get()
        alb.is_live        = self._is_live_var.get()
        alb.is_compilation = self._is_comp_var.get()
        alb.tracklist      = self._tl_text.get("1.0", "end").strip()
        # Sync the track table first (captures any in-progress inline edits)
        self._sync_track_table(alb)
        # For single-track albums, the Title Entry is the canonical title source.
        # For multi-track albums the track table wins, so don't overwrite it.
        if len(alb.tracks) <= 1:
            title_val = self._title_entry.get().strip() if hasattr(self, "_title_entry") else self._title_var.get().strip()
            if alb.tracks and title_val:
                alb.tracks[0].title = title_val
        # Single if Album left blank
        alb.is_single = not bool(alb.album)

    def _sync_track_table(self, alb: Album):
        rows = self._track_tree.get_children()
        for i, (track, row) in enumerate(zip(alb.tracks, rows)):
            vals = self._track_tree.item(row, "values")
            if len(vals) >= 3:
                try:
                    track.track_num = int(vals[0]) if vals[0] else None
                except ValueError:
                    pass
                track.title  = vals[1]
                track.artist = vals[2]

    def _autonumber_tracks(self):
        """Set each track's number to its position in the displayed list.

        - Nothing (or one row) selected: renumber every track 1..N.
        - Multiple rows selected: only renumber those, using their position
          in the full list — so selecting rows 5–10 yields 5, 6, …, 10.
        """
        if self.current_idx is None or not self.albums:
            return
        alb = self.albums[self.current_idx]
        if not alb.tracks:
            return
        rows = list(self._track_tree.get_children())
        selected = set(self._track_tree.selection())
        if len(selected) <= 1:
            target_idxs = list(range(len(rows)))
        else:
            target_idxs = [i for i, r in enumerate(rows) if r in selected]
        if not target_idxs:
            return
        for i in target_idxs:
            row = rows[i]
            new_num = i + 1
            vals = list(self._track_tree.item(row, "values"))
            vals[0] = new_num
            self._track_tree.item(row, values=vals)
            if i < len(alb.tracks):
                alb.tracks[i].track_num = new_num
        self._update_preview(alb)
        n = len(target_idxs)
        self._status(
            f"Auto-numbered {n} track{'s' if n != 1 else ''} "
            f"({target_idxs[0] + 1}–{target_idxs[-1] + 1})."
        )

    # ── inline track editing ─────────────────────────────────────────────

    def _on_tree_dbl(self, event):
        region = self._track_tree.identify_region(event.x, event.y)
        if region != "cell":
            return
        iid = self._track_tree.identify_row(event.y)
        col = self._track_tree.identify_column(event.x)
        col_idx = {"#1": 0, "#2": 1, "#3": 2}.get(col)
        if col_idx is None:
            return
        bbox = self._track_tree.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        vals = self._track_tree.item(iid, "values")
        cur = vals[col_idx] if col_idx < len(vals) else ""
        var = tk.StringVar(value=cur)
        if self._tree_entry:
            self._tree_entry.destroy()
        ent = tk.Entry(self._track_tree, textvariable=var,
                       bg=BG2, fg=FG, insertbackground=FG,
                       relief="flat", highlightbackground=ACCENT, highlightthickness=1)
        ent.place(x=x, y=y, width=w, height=h)
        ent.select_range(0, "end")
        ent.focus_set()
        self._tree_entry = ent

        _done = [False]

        def commit(e=None):
            if _done[0]:
                return
            _done[0] = True
            v = list(self._track_tree.item(iid, "values"))
            v[col_idx] = var.get().strip()
            self._track_tree.item(iid, values=v)
            if ent.winfo_exists():
                ent.destroy()
            self._tree_entry = None
            # Immediately write to the track model (don't rely on _save_current_fields timing)
            if self.current_idx is not None and self.albums:
                alb_cur = self.albums[self.current_idx]
                rows = list(self._track_tree.get_children())
                row_idx = rows.index(iid) if iid in rows else -1
                if 0 <= row_idx < len(alb_cur.tracks):
                    t = alb_cur.tracks[row_idx]
                    if col_idx == 0:
                        try:
                            t.track_num = int(v[0]) if v[0] else None
                        except ValueError:
                            pass
                    elif col_idx == 1:
                        t.title = v[1]
                        # Keep the Title entry in sync if this is the first track
                        if row_idx == 0:
                            self._loading_album = True
                            try:
                                self._title_var.set(t.title)
                            finally:
                                self._loading_album = False
                    elif col_idx == 2:
                        t.artist = v[2]

        def cancel(e=None):
            _done[0] = True
            if ent.winfo_exists():
                ent.destroy()
            self._tree_entry = None

        ent.bind("<Return>",   commit)
        ent.bind("<FocusOut>", commit)
        ent.bind("<Escape>",   cancel)

    # ── metadata fetch ───────────────────────────────────────────────────

    def _fetch_current_metadata(self):
        if self.current_idx is None:
            return
        self._save_current_fields()
        alb = self.albums[self.current_idx]
        if not alb.artist:
            self._status("Enter an artist name first, then click Fetch.")
            return
        alb.status = "fetching"
        self._refresh_queue()
        self._status(f"Fetching metadata for {alb.artist} — {alb.album or alb.tracks[0].title if alb.tracks else '?'}...")

        def _run():
            try:
                fetch_metadata(alb)
                alb.status = "ready"
            except Exception as e:
                alb.status = "error"
                alb.status_msg = str(e)
            self.root.after(0, lambda: self._on_fetch_done(self.current_idx))

        threading.Thread(target=_run, daemon=True).start()

    def _fetch_all_metadata(self):
        self._save_current_fields()
        for i, alb in enumerate(self.albums):
            if alb.status != "done":
                alb.status = "fetching"
                self._refresh_queue()
                def _run(a=alb, idx=i):
                    try:
                        fetch_metadata(a)
                        a.status = "ready"
                    except Exception as e:
                        a.status = "error"; a.status_msg = str(e)
                    self.root.after(0, lambda: self._on_fetch_done(idx))
                threading.Thread(target=_run, daemon=True).start()

    def _on_fetch_done(self, idx: int):
        self._refresh_queue()
        if idx == self.current_idx and idx is not None and idx < len(self.albums):
            alb = self.albums[idx]
            self._artist_var.set(alb.artist)
            self._album_var.set(alb.album)
            self._year_var.set(alb.year)
            if alb.tracks:
                self._title_var.set(alb.tracks[0].title)
            self._artwork_cache.pop(id(alb), None)
            self._refresh_artwork(alb)
        self._status("Metadata fetch complete.")

    # ── processing ───────────────────────────────────────────────────────

    def _set_roon_dir(self):
        global ROON_DIR
        folder = filedialog.askdirectory(
            title="Select Roon destination folder",
            initialdir=str(ROON_DIR) if ROON_DIR.exists() else str(Path.home()),
        )
        if not folder:
            return
        ROON_DIR = Path(folder)
        cfg = _load_config()
        cfg["roon_dir"] = str(ROON_DIR)
        _save_config(cfg)
        self._status(f"Roon folder set to: {ROON_DIR}")

    def _clear_done(self):
        self.albums = [a for a in self.albums if a.status != "done"]
        self.current_idx = None
        self._refresh_queue()
        self._set_detail_enabled(False)

    def _process_all(self):
        self._save_current_fields()
        pending = [a for a in self.albums if a.status != "done"]
        if not pending:
            messagebox.showinfo("Fly Me To The Roon", "Nothing left to process.")
            return
        move = self._move_var.get()
        if move:
            confirm_msg = f"Write tags and move {len(pending)} item(s) to:\n{ROON_DIR}"
        else:
            confirm_msg = f"Write tags in place for {len(pending)} item(s) (files will be renamed but not moved)."
        if not messagebox.askyesno("Process All", confirm_msg):
            return
        self._save_current_fields()   # capture any edits made while dialog was open
        self._status("Processing…")
        errors = []

        def _run():
            for alb in pending:
                if not alb.artist:
                    errors.append(f"Skipped — no artist: {alb.display_name}")
                    alb.status = "error"; continue
                if not alb.artwork_bytes:
                    errors.append(f"Skipped — no artwork: {alb.display_name}")
                    alb.status = "error"; continue
                # If no album, treat as single (goes in Artist - Singles/)
                if not alb.album:
                    alb.is_single = True
                try:
                    write_tags_and_move(alb, move=move)
                    alb.status = "done"
                except Exception as e:
                    alb.status = "error"
                    errors.append(f"{alb.display_name}: {e}")
                self.root.after(0, self._refresh_queue)
            self.root.after(0, lambda: self._on_process_done(errors))

        threading.Thread(target=_run, daemon=True).start()

    def _on_process_done(self, errors):
        self._refresh_queue()
        if errors:
            messagebox.showerror("Errors", "\n\n".join(errors))
            self._status(f"Done with {len(errors)} error(s).")
        else:
            self._status("All done! Files moved to Roon folder.")
            messagebox.showinfo("Fly Me To The Roon", "Done! Files are in your Roon folder.")

    # ── helpers ──────────────────────────────────────────────────────────

    def _status(self, msg: str):
        self._status_var.set(msg)
        self.root.update_idletasks()

    # ── about ────────────────────────────────────────────────────────────

    def _show_about(self):
        """Show a scrollable About dialog with version + changelog."""
        win = tk.Toplevel(self.root)
        win.title("About Fly Me To The Roon")
        win.configure(bg=BG)
        win.geometry("560x520")
        win.transient(self.root)

        tk.Label(win, text="Fly Me To The Roon",
                 bg=BG, fg=FG,
                 font=(FONT_DISPLAY, 26, "bold")).pack(pady=(22, 0))
        tk.Label(win, text=f"version {VERSION}",
                 bg=BG, fg=ACCENT,
                 font=(FONT_BODY, 11, "bold")).pack(pady=(2, 4))
        tk.Label(win, text="Drag-and-drop music metadata editor for Roon.",
                 bg=BG, fg=FG_DIM,
                 font=(FONT_BODY, 11)).pack(pady=(0, 14))

        # Changelog pane
        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        sb = ttk.Scrollbar(frame, orient="vertical")
        txt = tk.Text(frame, wrap="word", bg=BG2, fg=FG,
                      relief="flat", padx=12, pady=10,
                      font=(FONT_MONO, 11),
                      yscrollcommand=sb.set, highlightthickness=0)
        sb.configure(command=txt.yview)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", _load_changelog() or "No changelog available.")
        txt.configure(state="disabled")

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 14))
        win.bind("<Escape>", lambda e: win.destroy())

    # ── updater ──────────────────────────────────────────────────────────

    def _check_for_updates(self, silent: bool = True):
        """Fetch latest release metadata from GitHub, prompt user if newer.

        silent=True: called on startup. No 'up-to-date' popup, no error popup.
        silent=False: called from the menu item. Always reports the result.
        """
        if not silent:
            self._status("Checking for updates…")
        def worker():
            status, data = _fetch_latest_release()
            self.root.after(0, lambda: self._handle_update_info(status, data, silent))
        threading.Thread(target=worker, daemon=True).start()

    def _handle_update_info(self, status: str, data: dict, silent: bool):
        local_ver = VERSION

        if status == "no_releases":
            self._status(f"Up to date (v{local_ver}) — no releases published yet.")
            if not silent:
                messagebox.showinfo(
                    "Fly Me To The Roon",
                    f"No releases have been published to GitHub yet.\n\n"
                    f"Running version: {local_ver}\n\n"
                    f"Run `bash publish.sh` on the dev Mac to cut the first release."
                )
            return

        if status == "error":
            self._status("Update check failed.")
            if not silent:
                messagebox.showwarning(
                    "Couldn't check for updates",
                    f"{data.get('msg', 'Unknown error')}\n\n"
                    f"Running version: {local_ver}"
                )
            return

        if not data or "tag_name" not in data:
            self._status("Update check returned no data.")
            if not silent:
                messagebox.showwarning(
                    "Fly Me To The Roon",
                    "GitHub returned an unexpected response."
                )
            return

        remote_tag = data.get("tag_name", "")
        remote_ver = remote_tag.lstrip("v")

        local_t = _parse_version(local_ver)
        remote_t = _parse_version(remote_ver)

        if local_t == remote_t:
            self._status(f"Up to date (v{local_ver}).")
            if not silent:
                messagebox.showinfo(
                    "You're up to date",
                    f"Running version: {local_ver}\n"
                    f"Latest release:  {remote_ver}"
                )
            return

        if local_t > remote_t:
            # Dev Mac running an unpublished build.
            self._status(f"Version {local_ver} hasn't been published yet.")
            if not silent:
                messagebox.showinfo(
                    "Unpublished version",
                    f"This Mac is already on the newest version "
                    f"({local_ver}).\n\n"
                    f"Your other Macs can only see version {remote_ver} "
                    f"right now. To share {local_ver} with them, run "
                    f"this in Terminal on this Mac:\n\n"
                    f"    bash publish.sh"
                )
            return

        # Find the .zip asset
        zip_url = None
        for asset in data.get("assets", []) or []:
            name = (asset.get("name") or "").lower()
            if name.endswith(".zip"):
                zip_url = asset.get("browser_download_url")
                break
        if not zip_url:
            if not silent:
                messagebox.showwarning(
                    "Fly Me To The Roon",
                    f"Version {remote_ver} is available on GitHub, but the "
                    "release doesn't have a .zip asset attached. Install it "
                    "manually from the Releases page."
                )
            return

        notes = (data.get("body") or "").strip()
        if len(notes) > 1200:
            notes = notes[:1200].rstrip() + "\n…"

        prompt = (
            f"A new version of Fly Me To The Roon is available.\n\n"
            f"Current: {local_ver}\n"
            f"Latest:  {remote_ver}\n\n"
            f"What's new:\n{notes or '(no release notes)'}\n\n"
            f"Install now? Fly Me To The Roon will relaunch."
        )
        if messagebox.askyesno("Update available", prompt):
            self._install_update(remote_ver, zip_url)

    def _install_update(self, new_ver: str, zip_url: str):
        """Download the release zip and swap it into /Applications."""
        import sys as _sys
        self._status(f"Downloading Fly Me To The Roon {new_ver}…")
        self.root.update_idletasks()

        tmp_dir = Path("/tmp") / f"flymetotheroon-update-{os.getpid()}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        zip_path = tmp_dir / "FlyMeToTheRoon.app.zip"

        try:
            req = urllib.request.Request(
                zip_url, headers={"User-Agent": f"FlyMeToTheRoon/{VERSION}"}
            )
            with urllib.request.urlopen(req, timeout=60) as r, open(zip_path, "wb") as f:
                shutil.copyfileobj(r, f)
        except Exception as e:
            messagebox.showerror("Update failed",
                                 f"Couldn't download the new version:\n\n{e}")
            self._status("Update failed.")
            return

        # Detach a shell script that waits for us to quit, swaps /Applications, and relaunches.
        install_script = tmp_dir / "install.sh"
        install_script.write_text(f"""#!/bin/bash
set -e
sleep 1
cd "{tmp_dir}"
rm -rf extracted
mkdir extracted
ditto -x -k "{zip_path}" extracted
NEW_APP="$(find extracted -maxdepth 2 -name 'Fly Me To The Roon.app' -print -quit)"
if [ -z "$NEW_APP" ]; then
    osascript -e 'display dialog "Fly Me To The Roon update: extracted zip contained no Fly Me To The Roon.app." buttons {{"OK"}} default button 1'
    exit 1
fi
rm -rf "/Applications/Fly Me To The Roon.app"
cp -R "$NEW_APP" "/Applications/Fly Me To The Roon.app"
# Ad-hoc re-sign (harmless if already signed)
codesign --force --deep --sign - "/Applications/Fly Me To The Roon.app" 2>/dev/null || true
open "/Applications/Fly Me To The Roon.app"
""")
        install_script.chmod(0o755)

        try:
            import subprocess
            subprocess.Popen(
                ["/bin/bash", str(install_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            messagebox.showerror("Update failed",
                                 f"Couldn't launch installer:\n\n{e}")
            return

        self._status(f"Installing Fly Me To The Roon {new_ver}… relaunching.")
        self.root.update_idletasks()
        # Give the launcher a moment to start, then quit so it can replace us.
        self.root.after(400, lambda: (self.root.destroy(), _sys.exit(0)))


# ═══════════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════════

def main():
    if not HAS_MUTAGEN:
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("Missing dependency",
                             "mutagen not installed.\n\nRun setup.sh first.")
        return

    if HAS_DND:
        try:
            root = TkinterDnD.Tk()
        except Exception as _e:
            import sys
            print(f"[DnD] TkinterDnD.Tk() failed: {_e}", file=sys.stderr, flush=True)
            root = tk.Tk()
    else:
        root = tk.Tk()

    RoonTag(root)
    root.mainloop()


if __name__ == "__main__":
    main()
