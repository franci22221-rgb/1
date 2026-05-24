"""Trusted mod source search and download resolution."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import requests

from hydra_core import logger

MODRINTH_API = "https://api.modrinth.com/v2"
CURSEFORGE_API = "https://api.curseforge.com/v1"
NEXUS_API = "https://api.nexusmods.com/v1"
SPACEDOCK_API = "https://spacedock.info/api"

TRUSTED_SOURCES = (
    "Modrinth",
    "CurseForge",
    "Nexus Mods",
    "SpaceDock",
    "GitHub",
    "Manual / Forum",
)


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def dedupe_results(results: list[dict]) -> list[dict]:
    seen = {}
    out = []
    for r in results:
        key = _norm_name(r.get("name", ""))
        if not key:
            out.append(r)
            continue
        if key not in seen:
            seen[key] = r
            out.append(r)
        else:
            prev = seen[key]
            prev_sources = prev.get("alt_sources", [prev.get("source", "")])
            if r.get("source") and r["source"] not in prev_sources:
                prev_sources.append(r["source"])
            prev["alt_sources"] = prev_sources
    return out


def search_modrinth(query: str, game_id: str = "") -> list[dict]:
  url = f"{MODRINTH_API}/search?query={quote(query)}&limit=30"
  if game_id:
      url += f"&facets={quote('[\"project_type:mod\"]')}"
  r = requests.get(url, timeout=20)
  r.raise_for_status()
  out = []
  for h in r.json().get("hits", []):
      slug = h.get("slug", "")
      out.append({
          "name": h.get("title", "?"),
          "author": h.get("author", "?"),
          "downloads": h.get("downloads", 0),
          "source": "Modrinth",
          "version": h.get("version", ""),
          "project_id": h.get("project_id") or slug,
          "slug": slug,
          "web_url": f"https://modrinth.com/mod/{slug}",
          "manual_only": False,
      })
  return out


def search_curseforge(query: str, game_id: str, api_key: str) -> list[dict]:
  if not api_key:
      raise RuntimeError("Add CurseForge API key in Settings.")
  try:
      gid_int = int(game_id)
  except ValueError:
      raise RuntimeError("CurseForge needs a numeric Game ID in the sidebar.")
  r = requests.get(
      f"{CURSEFORGE_API}/mods/search?gameId={gid_int}"
      f"&searchFilter={quote(query)}&pageSize=30",
      headers={"x-api-key": api_key, "Accept": "application/json"},
      timeout=20)
  r.raise_for_status()
  out = []
  for h in r.json().get("data", []):
      slug = h.get("slug", "")
      out.append({
          "name": h.get("name", "?"),
          "author": ", ".join(a.get("name", "") for a in h.get("authors", [])) or "?",
          "downloads": int(h.get("downloadCount", 0)),
          "source": "CurseForge",
          "cf_mod_id": h.get("id"),
          "web_url": f"https://www.curseforge.com/projects/{slug}" if slug else "",
          "manual_only": False,
      })
  return out


def search_nexus(query: str, domain: str, api_key: str) -> list[dict]:
  if not api_key:
      raise RuntimeError("Add Nexus API key in Settings.")
  domain = (domain or "").strip().lower()
  if not domain:
      raise RuntimeError("Set Nexus domain (e.g. kerbalspaceprogram).")
  r = requests.get(
      f"{NEXUS_API}/games/{quote(domain)}/mods.json"
      f"?include_adult=false&terms={quote(query)}",
      headers={"apikey": api_key, "accept": "application/json"},
      timeout=20)
  r.raise_for_status()
  out = []
  for h in r.json().get("mods", []):
      mod_id = h.get("mod_id")
      out.append({
          "name": h.get("name", "?"),
          "author": h.get("author", "?"),
          "downloads": int(h.get("downloads", 0) or 0),
          "source": "Nexus Mods",
          "nexus_mod_id": mod_id,
          "nexus_domain": domain,
          "web_url": f"https://www.nexusmods.com/{domain}/mods/{mod_id}" if mod_id else "",
          "manual_only": False,
      })
  return out


def search_spacedock(query: str) -> list[dict]:
  mods = []
  for url in (
      f"{SPACEDOCK_API}/mod/search/{quote(query)}",
      f"{SPACEDOCK_API}/mod/filterName/{quote(query)}",
  ):
      try:
          r = requests.get(url, timeout=20)
          if r.status_code == 404:
              continue
          r.raise_for_status()
          data = r.json()
          mods = data if isinstance(data, list) else data.get("mods", data.get("results", []))
          if mods:
              break
      except Exception:
          continue
  out = []
  for h in mods[:30]:
      mid = h.get("id") or h.get("mod_id")
      name = h.get("name", "?")
      out.append({
          "name": name,
          "author": h.get("author", h.get("owner", "?")),
          "downloads": int(h.get("downloads", 0) or 0),
          "source": "SpaceDock",
          "spacedock_id": mid,
          "web_url": f"https://spacedock.info/mod/{mid}" if mid else "",
          "manual_only": False,
      })
  if not out:
      out.append({
          "name": f"SpaceDock search: {query}",
          "author": "SpaceDock",
          "downloads": 0,
          "source": "SpaceDock",
          "web_url": f"https://spacedock.info/search/{quote(query)}",
          "manual_only": True,
      })
  return out


def search_github(query: str, game_name: str = "") -> list[dict]:
  q = f"{query} mod"
  if game_name:
      q = f"{query} {game_name} mod"
  r = requests.get(
      "https://api.github.com/search/repositories",
      params={"q": q, "sort": "stars", "per_page": 20},
      headers={"Accept": "application/vnd.github+json"},
      timeout=20)
  if r.status_code == 403:
      return []
  r.raise_for_status()
  out = []
  for h in r.json().get("items", []):
      out.append({
          "name": h.get("full_name", h.get("name", "?")),
          "author": h.get("owner", {}).get("login", "?"),
          "downloads": int(h.get("stargazers_count", 0)),
          "source": "GitHub",
          "github_repo": h.get("full_name", ""),
          "web_url": h.get("html_url", ""),
          "manual_only": True,
      })
  return out


def search_manual_links(query: str, game_name: str) -> list[dict]:
  """Curated manual/forum style entries — opens browser; user installs by hand."""
  q = quote(f"{game_name} {query} mod")
  links = [
      ("Nexus Mods search", f"https://www.nexusmods.com/games/search/?keyword={q}"),
      ("KSP Forum search", f"https://forum.kerbalspaceprogram.com/search/?q={q}"),
      ("Google (manual verify)", f"https://www.google.com/search?q={q}"),
  ]
  out = []
  for title, url in links:
      out.append({
          "name": f"{title}: {query}",
          "author": "Manual install",
          "downloads": 0,
          "source": "Manual / Forum",
          "web_url": url,
          "manual_only": True,
      })
  return out


def search_all(cfg: dict, query: str, game_id: str, nexus_domain: str,
               game_name: str = "", source_filter: str = "All sources") -> tuple[list[dict], list[str]]:
  enabled = cfg.get("enabled_sources", {})
  official_only = cfg.get("official_sources_only", True)
  notes = []
  tasks = []

  def want(src: str) -> bool:
      if source_filter not in ("All sources", src):
          return False
      if not enabled.get(src, True):
          return False
      if official_only and src == "Manual / Forum":
          return True  # still show but manual_only
      return True

  cf_key = cfg.get("curseforge_api_key", "").strip()
  nx_key = cfg.get("nexus_api_key", "").strip()

  if want("Modrinth"):
      tasks.append(("Modrinth", lambda: search_modrinth(query, game_id)))
  if want("CurseForge"):
      if cf_key:
          tasks.append(("CurseForge", lambda: search_curseforge(query, game_id, cf_key)))
      else:
          notes.append("CurseForge skipped (no API key)")
  if want("Nexus Mods"):
      if nx_key and nexus_domain:
          tasks.append(("Nexus Mods", lambda: search_nexus(query, nexus_domain, nx_key)))
      else:
          notes.append("Nexus skipped (key or domain missing)")
  if want("SpaceDock"):
      tasks.append(("SpaceDock", lambda: search_spacedock(query)))
  if want("GitHub"):
      tasks.append(("GitHub", lambda: search_github(query, game_name)))
  if want("Manual / Forum"):
      tasks.append(("Manual / Forum", lambda: search_manual_links(query, game_name or "game")))

  results = []
  with ThreadPoolExecutor(max_workers=5) as ex:
      futs = {ex.submit(fn): name for name, fn in tasks}
      for fut in as_completed(futs):
          src = futs[fut]
          try:
              results.extend(fut.result())
          except Exception as exc:
              notes.append(f"{src}: {exc}")
              logger.warning("Search %s failed: %s", src, exc)

  return dedupe_results(results), notes


def resolve_download(mod: dict, cfg: dict) -> tuple[str, str]:
  src = mod.get("source", "")
  if mod.get("manual_only"):
      raise RuntimeError("Manual / forum mod — use Open in browser and copy into profile folder.")
  if src == "Modrinth":
      return modrinth_file(mod)
  if src == "CurseForge":
      return curseforge_file(mod, cfg.get("curseforge_api_key", "").strip())
  if src == "Nexus Mods":
      return nexus_file(mod, cfg.get("nexus_api_key", "").strip())
  if src == "SpaceDock":
      return spacedock_file(mod)
  raise RuntimeError(f"Download not supported for {src}. Use Open.")


def modrinth_file(mod: dict) -> tuple[str, str]:
  pid = mod.get("project_id") or mod.get("slug")
  r = requests.get(f"{MODRINTH_API}/project/{pid}/version", timeout=20)
  r.raise_for_status()
  versions = r.json()
  if not versions:
      raise RuntimeError("No files on Modrinth.")
  v = versions[0]
  primary = next((f for f in v["files"] if f.get("primary")), v["files"][0])
  return primary["url"], primary["filename"]


def curseforge_file(mod: dict, api_key: str) -> tuple[str, str]:
  mod_id = mod["cf_mod_id"]
  r = requests.get(f"{CURSEFORGE_API}/mods/{mod_id}/files",
                     headers={"x-api-key": api_key, "Accept": "application/json"},
                     timeout=20)
  r.raise_for_status()
  files = r.json().get("data", [])
  if not files:
      raise RuntimeError("No files on CurseForge.")
  files.sort(key=lambda f: f.get("fileDate", ""), reverse=True)
  chosen = files[0]
  dl = chosen.get("downloadUrl")
  if not dl:
      fid = str(chosen["id"])
      dl = f"https://edge.forgecdn.net/files/{fid[:4]}/{fid[4:]}/{chosen['fileName']}"
  return dl, chosen["fileName"]


def nexus_file(mod: dict, api_key: str) -> tuple[str, str]:
  domain = mod.get("nexus_domain", "").strip().lower()
  mod_id = mod.get("nexus_mod_id")
  r = requests.get(
      f"{NEXUS_API}/games/{quote(domain)}/mods/{mod_id}/files.json",
      headers={"apikey": api_key, "accept": "application/json"},
      timeout=20)
  r.raise_for_status()
  files = [f for f in r.json().get("files", []) if f.get("file_id")]
  if not files:
      raise RuntimeError("No Nexus files.")
  files.sort(key=lambda f: f.get("uploaded_timestamp", 0), reverse=True)
  chosen = files[0]
  dl = requests.get(
      f"{NEXUS_API}/games/{quote(domain)}/mods/{mod_id}/files/{chosen['file_id']}/download_link.json",
      headers={"apikey": api_key, "accept": "application/json"},
      timeout=20)
  dl.raise_for_status()
  links = dl.json()
  if not links:
      raise RuntimeError("Nexus download link unavailable.")
  return links[0]["URI"], chosen["name"]


def spacedock_file(mod: dict) -> tuple[str, str]:
  mid = mod.get("spacedock_id")
  r = requests.get(f"{SPACEDOCK_API}/mod/{mid}/latest", timeout=20)
  if r.status_code == 404:
      r = requests.get(f"{SPACEDOCK_API}/mod/{mid}", timeout=20)
  r.raise_for_status()
  data = r.json()
  versions = data.get("versions", [])
  if versions:
      v = versions[0]
      return v.get("download_link") or v.get("downloadLink"), v.get("name", f"mod_{mid}.zip")
  link = data.get("download_link") or data.get("downloadLink")
  if link:
      return link, data.get("name", f"spacedock_{mid}.zip")
  raise RuntimeError("SpaceDock has no direct download link — use Open.")
