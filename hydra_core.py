"""Core config, profile engine, logging, and shared helpers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "hydra_mods_config.json"
PROFILES_ROOT = APP_DIR / "profiles"
ICON_CACHE = APP_DIR / ".icon_cache"
LOG_PATH = APP_DIR / "logs" / "hydra_companion.log"

PROFILES_ROOT.mkdir(exist_ok=True)
ICON_CACHE.mkdir(exist_ok=True)
LOG_PATH.parent.mkdir(exist_ok=True)

DEFAULT_CONFIG = {
    "curseforge_api_key": "",
    "nexus_api_key": "",
    "hydra_launcher_path": "",
    "use_symlinks": False,
    "library_card_size": "Normal",
    "catalogue_source": "All sources",
    "official_sources_only": True,
    "enabled_sources": {
        "Thunderstore": True,
        "Modrinth": True,
        "CurseForge": True,
        "Nexus Mods": True,
        "SpaceDock": True,
        "GitHub": True,
        "Manual / Forum": True,
    },
    "first_run_complete": False,
    "active_game": None,
    "active_profile": None,
    "games": {},
}

# Per-game-type defaults (KSP uses GameData + SpaceDock, etc.)
GAME_PRESETS = {
    "kerbal space program": {
        "mod_subfolder": "GameData",
        "game_id": "",
        "nexus_domain": "kerbalspaceprogram",
        "sources": ["SpaceDock", "Nexus Mods", "GitHub", "Manual / Forum"],
    },
    "ksp": {
        "mod_subfolder": "GameData",
        "nexus_domain": "kerbalspaceprogram",
        "sources": ["SpaceDock", "Nexus Mods", "GitHub", "Manual / Forum"],
    },
    "minecraft": {
        "mod_subfolder": "mods",
        "game_id": "minecraft",
        "nexus_domain": "",
        "sources": ["Modrinth", "CurseForge"],
    },
    "rounds": {
        "mod_subfolder": "mods",
        "game_id": "",
        "nexus_domain": "",
        "sources": ["Thunderstore", "GitHub", "Manual / Forum"],
    },
}

EXTRA_FOLDER_TEMPLATES = {
    "Generic mods": [],
    "KSP (GameData)": [{"name": "GameData", "profile_subdir": "GameData"}],
    "Minecraft (+ config)": [
        {"name": "config", "profile_subdir": "config"},
    ],
}

KSP_COMMON_DEPS = {
    "kopernicus": ["Module Manager", "Harmony"],
    "b9": ["Module Manager"],
    "spacedocks": ["Module Manager"],
}

LIB_CARD_SIZES = {
    "Compact": (120, 155, 6),
    "Normal": (160, 210, 5),
    "Comfortable": (200, 260, 4),
    "Large": (240, 310, 3),
}

logger = logging.getLogger("hydra_companion")


def setup_logging():
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    for k, v in DEFAULT_CONFIG.items():
        if k == "enabled_sources" and isinstance(v, dict):
            data.setdefault(k, {})
            for sk, sv in v.items():
                data[k].setdefault(sk, sv)
        else:
            data.setdefault(k, v if not isinstance(v, dict) else {})
    for name, g in data.get("games", {}).items():
        if "mod_path" in g and "game_folder" not in g:
            mp = Path(g.pop("mod_path"))
            g["game_folder"] = str(mp.parent) if mp.parent != mp else str(mp)
            g["mod_subfolder"] = mp.name or "mods"
        g.setdefault("game_folder", "")
        g.setdefault("mod_subfolder", "mods")
        g.setdefault("game_id", "")
        g.setdefault("nexus_domain", "")
        g.setdefault("icon_url", "")
        g.setdefault("cover_path", "")
        g.setdefault("banner_path", "")
        g.setdefault("extra_folders", [])
        g.setdefault("profiles", ["Vanilla"])
        g.setdefault("notes", {})
        g.setdefault("tags", [])
        g.setdefault("manual_mods", [])
        g.setdefault("applied_profile", None)
        g.setdefault("applied_hash", "")
        g.setdefault("last_backup_profile", None)
        g.setdefault("favorite", False)
        g.setdefault("game_version_filter", "")
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(data := cfg, indent=2), encoding="utf-8")
    logger.info("Config saved (%d games)", len(data.get("games", {})))


def safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in name).strip() or "Unnamed"


def profile_folder(game: str, profile: str) -> Path:
    p = PROFILES_ROOT / safe_name(game) / safe_name(profile)
    p.mkdir(parents=True, exist_ok=True)
    return p


def real_mod_path(g: dict) -> Path:
    return Path(g["game_folder"]) / g["mod_subfolder"]


def human_size(n: float) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def folder_hash(folder: Path) -> str:
    if not folder.exists():
        return ""
    h = hashlib.sha1()
    items = []
    for p in folder.rglob("*"):
        if p.is_file():
            items.append((str(p.relative_to(folder)).replace("\\", "/"),
                          p.stat().st_size))
    for rel, sz in sorted(items):
        h.update(f"{rel}|{sz}\n".encode())
    return h.hexdigest()


def profile_stats(folder: Path) -> dict:
    stats = {"count": 0, "bytes": 0, "ext": {}}
    if not folder.exists():
        return stats
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        stats["count"] += 1
        stats["bytes"] += p.stat().st_size
        ext = p.suffix.lower() or "(none)"
        stats["ext"][ext] = stats["ext"].get(ext, 0) + 1
    return stats


ART_PRIORITY = (
    "library_header", "library_hero", "header", "hero", "banner",
    "cover", "capsule", "logo", "icon", "portrait", "thumbnail",
)
ART_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
ART_SKIP_DIRS = {
    "mods", "mod", "plugins", "cache", "temp", "logs", "saves",
    "profiles", "backup", "node_modules",
}


def _art_score(path: Path) -> int:
    name = path.stem.lower()
    score = 0
    for i, key in enumerate(ART_PRIORITY):
        if key in name:
            score += (len(ART_PRIORITY) - i) * 100
    score += min(path.stat().st_size // 1024, 500)
    if "banner" in name or "header" in name or "hero" in name:
        score += 40
    if path.parent.name.lower() in ("graphics", "images", "art", "media"):
        score += 25
    if any(x in name for x in ("saveicon", "save_icon", "favicon", "cursor")):
        score -= 200
    return score


def _collect_images(folder: Path, max_depth: int = 6) -> list[Path]:
    found: list[Path] = []
    if not folder.is_dir():
        return found
    root_depth = len(folder.parts)
    try:
        for p in folder.rglob("*"):
            if not p.is_file():
                continue
            if len(p.parts) - root_depth > max_depth:
                continue
            if p.suffix.lower() not in ART_EXTENSIONS:
                continue
            if p.stat().st_size < 2048:
                continue
            rel_parts = p.relative_to(folder).parts[:-1]
            if {x.lower() for x in rel_parts} & ART_SKIP_DIRS:
                continue
            found.append(p)
    except OSError:
        pass
    return found


def discover_game_art(game_folder: str | Path, game_name: str = "") -> dict:
    """Find cover + banner: profile art folder, game dir, then parent library folder."""
    root = Path(game_folder)
    found: list[Path] = []

    if game_name:
        prof_art = PROFILES_ROOT / safe_name(game_name)
        for fixed in ("cover.png", "cover.jpg", "banner.jpg", "banner.png",
                      "icon.png", "header.jpg"):
            p = prof_art / fixed
            if p.is_file():
                found.append(p)

    if root.is_dir():
        found.extend(_collect_images(root))

    parent = root.parent if root.is_dir() else None
    if parent and parent.is_dir() and game_name:
        gk = game_name.lower().replace(" ", "")
        for p in _collect_images(parent, max_depth=1):
            stem = p.stem.lower().replace(" ", "")
            if gk in stem or stem in gk:
                found.append(p)

    hydra_cache = Path.home() / "AppData" / "Roaming" / "hydralauncher" / "Cache"
    if game_name and hydra_cache.is_dir():
        gk = game_name.lower()
        try:
            for p in hydra_cache.rglob("*"):
                if not p.is_file() or p.suffix.lower() not in ART_EXTENSIONS:
                    continue
                if p.stat().st_size < 8000:
                    continue
                if gk in p.name.lower() or gk in str(p.parent).lower():
                    found.append(p)
        except OSError:
            pass

    if not found:
        return {"cover_path": "", "banner_path": ""}
    if not found:
        return {"cover_path": "", "banner_path": ""}
    found.sort(key=_art_score, reverse=True)
    cover = found[0]
    banner = found[0]
    for p in found:
        n = p.stem.lower()
        if any(k in n for k in ("banner", "header", "hero", "library")):
            banner = p
            break
    for p in found:
        n = p.stem.lower()
        if any(k in n for k in ("cover", "icon", "logo", "capsule", "portrait")):
            cover = p
            break
    return {
        "cover_path": str(cover.resolve()),
        "banner_path": str(banner.resolve()),
    }


def apply_discovered_art(game: dict, game_name: str = "") -> dict:
    """Merge auto-detected art paths into a game record."""
    folder = game.get("game_folder", "")
    if not folder:
        return game
    art = discover_game_art(folder, game_name)
    if art["cover_path"]:
        game["cover_path"] = art["cover_path"]
    if art["banner_path"]:
        game["banner_path"] = art["banner_path"]
    return game


def guess_preset(game_name: str) -> dict | None:
    key = game_name.strip().lower()
    if key in GAME_PRESETS:
        return GAME_PRESETS[key]
    for k, v in GAME_PRESETS.items():
        if k in key or key in k:
            return v
    return None


def apply_load_order_prefix(folder: Path, ordered_names: list[str]):
    """Rename profile files with 01_, 02_ load-order prefixes."""
    mapping = {}
    for p in folder.iterdir():
        if p.is_file():
            base = re.sub(r"^\d{2,3}_", "", p.name)
            mapping[base] = p
    for i, name in enumerate(ordered_names, start=1):
        src = mapping.get(name)
        if not src or not src.exists():
            continue
        dest = folder / f"{i:02d}_{name}"
        if src != dest:
            if dest.exists():
                dest.unlink()
            src.rename(dest)


def profile_diff(live: Path, profile: Path) -> dict:
    live_files = {}
    prof_files = {}
    if live.exists():
        for p in live.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(live)).replace("\\", "/")
                live_files[rel] = p.stat().st_size
    if profile.exists():
        for p in profile.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(profile)).replace("\\", "/")
                prof_files[rel] = p.stat().st_size
    only_live = sorted(set(live_files) - set(prof_files))
    only_prof = sorted(set(prof_files) - set(live_files))
    changed = sorted(
        k for k in set(live_files) & set(prof_files)
        if live_files[k] != prof_files[k])
    return {"only_live": only_live, "only_profile": only_prof, "changed": changed}


def dry_run_apply(src: Path, dst: Path) -> dict:
    src_names = {p.name for p in src.iterdir()} if src.exists() else set()
    dst_names = {p.name for p in dst.iterdir()} if dst.exists() else set()
    return {
        "copy_count": len(src_names),
        "remove_count": len(dst_names),
        "to_copy": sorted(src_names),
        "to_remove": sorted(dst_names),
    }


def export_mod_list(game: str, profile: str, folder: Path) -> str:
    lines = [f"# {game} — profile: {profile}",
             f"# Exported {datetime.now().isoformat(timespec='seconds')}", ""]
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            rel = p.relative_to(folder)
            lines.append(f"- {rel} ({human_size(p.stat().st_size)})")
    return "\n".join(lines) + "\n"


def detect_conflicts_v2(folder: Path) -> list[str]:
    msgs = []
    groups = {}
    dlls = {}
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        base = re.sub(r"^\d{2,3}_", "", p.name)
        base = re.sub(r"\.disabled$", "", base, flags=re.I)
        norm = re.sub(r"[-_]\d+(\.\d+)+.*", "", base).lower()
        groups.setdefault(norm, []).append(p.name)
        if p.suffix.lower() in (".dll", ".so"):
            dlls.setdefault(p.name.lower(), []).append(str(p.relative_to(folder)))
    for k, v in groups.items():
        if len(v) > 1:
            msgs.append(f"Duplicate mod name group '{k}': " + ", ".join(v))
    for name, paths in dlls.items():
        if len(paths) > 1:
            msgs.append(f"Same DLL in multiple paths: {name} -> {paths}")
    return msgs


def find_hydra_exe() -> str:
    candidates = [
        Path.home() / "AppData/Local/Hydra/hydra-launcher.exe",
        Path.home() / "AppData/Local/Programs/Hydra/hydra-launcher.exe",
        Path("C:/Program Files/Hydra/hydra-launcher.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return ""


def backup_config_zip(out_path: Path) -> Path:
    import zipfile
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        if CONFIG_PATH.exists():
            z.write(CONFIG_PATH, CONFIG_PATH.name)
        if PROFILES_ROOT.exists():
            for p in PROFILES_ROOT.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(APP_DIR))
    return out_path


def ksp_dependency_hint(mod_name: str) -> list[str]:
    key = mod_name.lower()
    for k, deps in KSP_COMMON_DEPS.items():
        if k in key:
            return deps
    return []
