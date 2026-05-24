# Hydra Companion — Mod Profile Switcher

Desktop mod-profile switcher for **Hydra Launcher**. Pick a profile, apply it to your game's mod folder, then launch Hydra.

## Run

```bash
pip install -r requirements.txt
python hydra_companion.py
```

Or double-click `run.bat` (Windows).

## Trusted mod sources (Catalogue)

| Source | API key? |
|--------|----------|
| Thunderstore | No (ROUNDS, Lethal Company, Valheim, Risk of Rain 2, etc.) |
| Modrinth | No |
| CurseForge | Yes — [console.curseforge.com](https://console.curseforge.com/) |
| Nexus Mods | Yes — [nexusmods.com account](https://www.nexusmods.com/users/myaccount?tab=api) |
| SpaceDock (KSP) | No (search + open/download) |
| GitHub | No (repo links; manual download) |
| Manual / Forum | Browser links (KSP forum, Nexus search, etc.) |

**All sources** runs them in parallel and merges results (deduped by name).

## Quick workflow

1. **Library** → `+ Add Game` → set folder, game ID, Nexus domain if needed. Use **Apply game preset** for KSP/Minecraft/ROUNDS templates.
2. **Catalogue** → type 2+ characters (e.g. `rounds`) → pick target game/profile in the right sidebar → **Download** or **Open**.
3. **Game detail** → manage profiles, load order, dry-run, diff, **APPLY PROFILE FOR HYDRA**.
4. Open Hydra and play.

## New features

- **Thunderstore** integration — search and download mods for ROUNDS, Lethal Company, Valheim, R2, and 30+ communities
- **Game-specific search** — mod searches are automatically scoped to the selected game
- **Mouse back/forward** buttons (Button 3/4) and **Alt+Left/Right** for page navigation
- **Card size** dropdown in Library (Compact → Large)
- **Dry-run**, **Diff**, **Undo last apply**, **Load order**, **Export mod list**
- **Profile stats** (file count / size)
- **First-run wizard**, Hydra exe auto-detect
- **Backup** config + profiles zip in Settings
- Logs: `logs/hydra_companion.log`

## Files

| File | Role |
|------|------|
| `hydra_companion.py` | UI (tkinter) |
| `hydra_core.py` | Config, profiles, presets |
| `hydra_sources.py` | Mod search & download APIs |

## KSP note

Use preset **KSP** or set `Nexus domain` to `kerbalspaceprogram` and mod folder to `GameData`. SpaceDock + Manual/Forum links are included for forum-only mods.
