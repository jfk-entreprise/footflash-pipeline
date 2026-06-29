#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 1 : Surveillance des matchs (Coupe du Monde 2026)
==================================================================

Source de donnees : Football-Data.org (API v4, plan gratuit).
    - Base URL : https://api.football-data.org/v4
    - Auth     : header X-Auth-Token (pas de query param)
    - Competition : WC (Coupe du Monde)

Role :
    1. Interroger Football-Data pour les matchs WC de la fenetre hier->aujourd'hui (UTC).
    2. Detecter les matchs termines (status == "FINISHED").
    3. Enrichir chaque match via GET /v4/matches/{id} (buts/buteurs/minutes + cartons
       rouges) quand le plan les expose ; repli automatique sur le seul score sinon.
    4. Sauvegarder un JSON par match dans 01_Donnees/matchs/ (schema inchange : compat Bloc 2/3).
    5. Notification Telegram.  6. Journalisation [Bloc1].

Secrets : env FOOTBALL_DATA_API_KEY en priorite, repli 00_Context/config.md
          (cle FOOTBALL_DATA_API_KEY). Aucune cle en dur.
Anti-doublon : .processed.json (par match id).   Sante : .health.json.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib import error, parse, request

# --------------------------------------------------------------------------- #
# Chemins
# --------------------------------------------------------------------------- #
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def _find_dir(root: Path, keyword: str) -> Path:
    for child in root.iterdir():
        if child.is_dir() and keyword.lower() in child.name.lower():
            return child
    raise FileNotFoundError(f"Dossier contenant '{keyword}' introuvable sous {root}")


CONTEXT_DIR = _find_dir(PROJECT_ROOT, "00_Context")
DONNEES_DIR = _find_dir(PROJECT_ROOT, "01_Donnees")
MATCHS_DIR = _find_dir(DONNEES_DIR, "matchs")
ARCHIVES_DIR = _find_dir(DONNEES_DIR, "archives")

CONFIG_FILE = CONTEXT_DIR / "config.md"
PROCESSED_FILE = MATCHS_DIR / ".processed.json"
HEALTH_FILE = MATCHS_DIR / ".health.json"
LOG_FILE = ARCHIVES_DIR / "log.md"

# --------------------------------------------------------------------------- #
# Parametres CdM 2026 (Football-Data : competition = WC)
# --------------------------------------------------------------------------- #
COMPETITION = "WC"
DEFAULT_BASE_URL = "https://api.football-data.org/v4"
TOURNOI_START = "2026-06-11"
TOURNOI_END = "2026-07-19"
JOURS_VIDES_ALERTE = 3
FINISHED_STATUSES = {"FINISHED", "AWARDED"}   # match termine cote Football-Data
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF = 5


# --------------------------------------------------------------------------- #
# Configuration (env prioritaire, repli config.md)
# --------------------------------------------------------------------------- #
def _parse_config_md(path: Path) -> dict:
    cfg: dict[str, str] = {}
    if not path.exists():
        return cfg
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("<>").strip()
        if key and value:
            cfg[key] = value
    return cfg


def load_config(path: Path) -> dict:
    """Env (GitHub Secrets) prioritaire, repli config.md. Cle : FOOTBALL_DATA_API_KEY."""
    cfg = _parse_config_md(path)
    env_map = {
        "FOOTBALL_DATA_API_KEY": ("FOOTBALL_DATA_API_KEY",),
        "BASE_URL": ("BASE_URL", "FOOTBALL_DATA_BASE_URL"),
        "TELEGRAM_BOT_TOKEN": ("TELEGRAM_BOT_TOKEN",),
        "TELEGRAM_CHAT_ID": ("TELEGRAM_CHAT_ID",),
        "YOUTUBE_API_KEY": ("YOUTUBE_API_KEY",),
    }
    for internal_key, env_names in env_map.items():
        for env_name in env_names:
            val = os.environ.get(env_name)
            if val:
                cfg[internal_key] = val.strip()
                break
    return cfg


# --------------------------------------------------------------------------- #
# Journalisation
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- `{ts}` — [Bloc1] {message}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Journal de suivi — FootFlash\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())


# --------------------------------------------------------------------------- #
# Client Football-Data.org (v4)
# --------------------------------------------------------------------------- #
class FootballData:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        if params:
            url += "?" + parse.urlencode(params)
        headers = {"X-Auth-Token": self.api_key, "Accept": "application/json"}
        last_err: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                with request.urlopen(request.Request(url, headers=headers),
                                     timeout=REQUEST_TIMEOUT) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except error.HTTPError as exc:
                # 429 = quota (10 req/min en gratuit) -> backoff ; sinon message API
                detail = ""
                try:
                    detail = json.loads(exc.read().decode("utf-8")).get("message", "")
                except Exception:
                    pass
                last_err = RuntimeError(f"HTTP {exc.code} ({endpoint}) {detail}".strip())
                if exc.code == 429 and attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF * attempt * 2)
                    continue
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF * attempt)
            except (error.URLError, TimeoutError, ValueError) as exc:
                last_err = exc
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF * attempt)
        raise RuntimeError(f"Echec appel '{endpoint}' apres {MAX_RETRIES} tentatives : {last_err}")

    def matches_window(self, date_from: str, date_to: str) -> list[dict]:
        """Tous les matchs WC de la fenetre (tous statuts) -> sante + detection."""
        data = self._get(f"competitions/{COMPETITION}/matches",
                         {"dateFrom": date_from, "dateTo": date_to})
        return data.get("matches", [])

    def matches_scheduled(self) -> list[dict]:
        """Matchs WC a venir (self-test de couverture)."""
        data = self._get(f"competitions/{COMPETITION}/matches", {"status": "SCHEDULED"})
        return data.get("matches", [])

    def match_detail(self, match_id: int) -> dict:
        """Detail d'un match (peut exposer goals/bookings selon le plan)."""
        return self._get(f"matches/{match_id}")


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = parse.urlencode({
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        with request.urlopen(request.Request(url, data=body, method="POST"),
                             timeout=REQUEST_TIMEOUT) as resp:
            return bool(json.loads(resp.read().decode("utf-8")).get("ok"))
    except Exception as exc:
        log(f"⚠️ Echec notification Telegram : {exc}")
        return False


# --------------------------------------------------------------------------- #
# Etat anti-doublon
# --------------------------------------------------------------------------- #
def load_processed() -> set[int]:
    if PROCESSED_FILE.exists():
        try:
            return set(json.loads(PROCESSED_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_processed(ids: set[int]) -> None:
    PROCESSED_FILE.write_text(json.dumps(sorted(ids)), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Sante (alerte si aucun match dans la fenetre pendant N jours — tous statuts)
# --------------------------------------------------------------------------- #
def load_health() -> dict:
    if HEALTH_FILE.exists():
        try:
            return json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"jours_vides": [], "alerte_envoyee": False}


def save_health(h: dict) -> None:
    HEALTH_FILE.write_text(json.dumps(h, indent=2), encoding="utf-8")


def maj_sante(jour: str, nb_matchs_fenetre: int, cfg: dict) -> None:
    if not (TOURNOI_START <= jour <= TOURNOI_END):
        return
    h = load_health()
    vides = [d for d in h.get("jours_vides", []) if d != jour]
    if nb_matchs_fenetre == 0:
        vides.append(jour)
        vides = sorted(set(vides))[-JOURS_VIDES_ALERTE:]
        if len(vides) >= JOURS_VIDES_ALERTE and not h.get("alerte_envoyee"):
            send_telegram(
                cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", ""),
                "🚑 <b>FootFlash — anomalie détection</b>\n"
                f"Aucun match WC détecté depuis {JOURS_VIDES_ALERTE} jours ({', '.join(vides)}).\n"
                "Vérifier la couverture Football-Data (competition=WC) et la clé API.",
            )
            log(f"🚑 Alerte santé envoyée : {JOURS_VIDES_ALERTE} jours sans match.")
            h["alerte_envoyee"] = True
    else:
        vides = []
        h["alerte_envoyee"] = False
    h["jours_vides"] = vides
    save_health(h)


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #
def slugify(name: str) -> str:
    import unicodedata
    norm = unicodedata.normalize("NFKD", name or "")
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    slug = "".join(c.lower() if c.isalnum() else "-" for c in norm)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "equipe"


def build_filename(home: str, away: str, date_iso: str) -> str:
    return f"{date_iso}_{slugify(home)}-{slugify(away)}_data.json"


def _events_from_detail(detail: dict) -> list[dict]:
    """Convertit goals/bookings (si presents) au format 'events' attendu par le Bloc 3.

    NB plan gratuit : 'goals'/'bookings' peuvent etre absents -> liste vide (repli A).
    Les buts contre-son-camp sont credites a l'equipe fournie par l'API (score correct),
    sans flip, donc etiquetes 'Normal Goal' (le score final reste coherent).
    """
    events: list[dict] = []
    for g in detail.get("goals") or []:
        team = g.get("team") or {}
        scorer = g.get("scorer") or {}
        assist = g.get("assist") or {}
        gtype = (g.get("type") or "").upper()
        label = {"PENALTY": "Penalty"}.get(gtype, "Normal Goal")
        events.append({
            "time": {"elapsed": g.get("minute"), "extra": g.get("injuryTime")},
            "team": {"id": team.get("id"), "name": team.get("name")},
            "player": {"name": scorer.get("name")},
            "assist": {"name": assist.get("name")},
            "type": "Goal",
            "detail": label,
        })
    for b in detail.get("bookings") or []:
        if (b.get("card") or "").upper().startswith("RED"):
            team = b.get("team") or {}
            player = b.get("player") or {}
            events.append({
                "time": {"elapsed": b.get("minute"), "extra": None},
                "team": {"id": team.get("id"), "name": team.get("name")},
                "player": {"name": player.get("name")},
                "type": "Card",
                "detail": "Red Card",
            })
    return events


# --------------------------------------------------------------------------- #
# Traitement d'un match termine
# --------------------------------------------------------------------------- #
def process_fixture(api: "FootballData", match: dict, cfg: dict) -> Path:
    mid = match["id"]
    home = (match.get("homeTeam") or {}).get("name", "?")
    away = (match.get("awayTeam") or {}).get("name", "?")
    home_id = (match.get("homeTeam") or {}).get("id")
    away_id = (match.get("awayTeam") or {}).get("id")
    full = (match.get("score") or {}).get("fullTime") or {}
    gh, ga = full.get("home"), full.get("away")
    date_iso = (match.get("utcDate") or "")[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Option B : enrichissement via le detail du match (goals/bookings si dispo).
    events: list[dict] = []
    try:
        detail = api.match_detail(mid)
        events = _events_from_detail(detail)
        if not events:
            log(f"ℹ️ Pas d'events détaillés pour {mid} (plan gratuit) — repli score seul.")
    except Exception as exc:
        log(f"ℹ️ Détail match {mid} indisponible ({exc}) — repli score seul.")

    record = {
        "fixture_id": mid,
        "source_api": "football-data.org/v4",
        "recupere_le": datetime.now(timezone.utc).isoformat(),
        "competition": match.get("competition", {}),
        "match": {
            "id": mid,
            "date": match.get("utcDate"),
            "status": match.get("status"),
            "stage": match.get("stage"),
            "group": match.get("group"),
            "venue": {"name": (match.get("venue") if isinstance(match.get("venue"), str) else None)},
        },
        "equipes": {
            "home": {"id": home_id, "name": home},
            "away": {"id": away_id, "name": away},
        },
        "score": {
            "buts": {"home": gh, "away": ga},
            "detail": match.get("score", {}),
        },
        "events": events,
        "statistics": [],   # non fourni par le plan gratuit
        "lineups": [],      # non fourni par le plan gratuit
    }

    out_path = MATCHS_DIR / build_filename(home, away, date_iso)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    nb_buts = sum(1 for e in events if e.get("type") == "Goal")
    log(f"💾 Donnees sauvegardees : {out_path.name} ({home} {gh}–{ga} {away}, {nb_buts} but(s) détaillé(s))")

    msg = (
        "⚽ <b>Match terminé — CdM 2026</b>\n"
        f"{home} <b>{gh}–{ga}</b> {away}\n"
        f"📁 <code>{out_path.name}</code>\n"
        + (f"Buts détaillés : {nb_buts}." if events else "Score seul (events non fournis par le plan).")
    )
    if send_telegram(cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", ""), msg):
        log(f"📨 Notification Telegram envoyée pour {home}-{away}")
    return out_path


# --------------------------------------------------------------------------- #
# Self-test de couverture (--verify)
# --------------------------------------------------------------------------- #
def verifier_couverture(api: "FootballData", cfg: dict) -> int:
    log("🩺 Self-test couverture Football-Data (competition=WC, status=SCHEDULED)…")
    try:
        prochains = api.matches_scheduled()
    except RuntimeError as exc:
        log(f"❌ Self-test KO : {exc}")
        send_telegram(cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", ""),
                      f"❌ <b>FootFlash — self-test API KO</b>\n{exc}")
        return 1
    n = len(prochains)
    apercu = ", ".join(
        f"{(m.get('homeTeam') or {}).get('name','?')}-{(m.get('awayTeam') or {}).get('name','?')}"
        f" ({(m.get('utcDate') or '')[:10]})"
        for m in prochains[:3]
    )
    log(f"🩺 {n} match(s) à venir. {apercu}")
    send_telegram(
        cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", ""),
        f"🩺 <b>FootFlash — self-test API</b>\n{n} match(s) à venir. {apercu or '—'}\n"
        + ("✅ Couverture OK." if n > 0 else "⚠️ 0 match : vérifier competition/clé/plan."),
    )
    return 0 if n > 0 else 2


# --------------------------------------------------------------------------- #
# Push automatique (ignore sous GitHub Actions : le workflow s'en charge)
# --------------------------------------------------------------------------- #
def git_push_changes(nb_matchs: int) -> None:
    if nb_matchs <= 0:
        return
    if os.environ.get("GITHUB_ACTIONS") == "true":
        log("ℹ️ GitHub Actions detecte — push gere par le workflow (auto-push ignore).")
        return
    if not (PROJECT_ROOT / ".git").exists():
        log("ℹ️ Pas de depot git — push ignore.")
        return

    def _git(*args: str) -> tuple[int, str]:
        res = subprocess.run(["git", "-C", str(PROJECT_ROOT), *args],
                             capture_output=True, text=True)
        return res.returncode, (res.stdout + res.stderr).strip()

    try:
        _git("add", "-A")
        if _git("diff", "--cached", "--quiet")[0] == 0:
            log("ℹ️ Rien de nouveau a pousser.")
            return
        _git("commit", "-m", f"Bloc1: {nb_matchs} nouveau(x) match(s) — donnees JSON")
        code, out = _git("push")
        log(f"⬆️ Push reussi — Bloc 2 declenche ({nb_matchs} match(s))." if code == 0
            else f"⚠️ Echec git push : {out[:200]}")
    except FileNotFoundError:
        log("⚠️ git introuvable — push ignore.")
    except Exception as exc:
        log(f"⚠️ Erreur git push : {exc}")


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Bloc 1 — détection des matchs CdM 2026 (Football-Data).")
    ap.add_argument("--verify", action="store_true",
                    help="Self-test de couverture API (matchs à venir) puis sortie.")
    args = ap.parse_args()

    cfg = load_config(CONFIG_FILE)
    api_key = cfg.get("FOOTBALL_DATA_API_KEY")
    base_url = cfg.get("BASE_URL", DEFAULT_BASE_URL)
    if not api_key:
        log("❌ FOOTBALL_DATA_API_KEY absente (env GitHub Secrets ou config.md) — arret.")
        return 1

    api = FootballData(api_key, base_url)
    MATCHS_DIR.mkdir(parents=True, exist_ok=True)

    if args.verify:
        return verifier_couverture(api, cfg)

    # C1 : fenetre HIER -> AUJOURD'HUI (UTC) en un seul appel range.
    now = datetime.now(timezone.utc)
    date_from = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    date_to = now.strftime("%Y-%m-%d")
    log(f"🔎 Surveillance WC — fenetre UTC {date_from} -> {date_to}")

    try:
        matches = api.matches_window(date_from, date_to)
    except RuntimeError as exc:
        log(f"❌ {exc}")
        return 1

    # Sante : tous statuts dans la fenetre (evite les fausses alertes en jours de repos).
    maj_sante(now.strftime("%Y-%m-%d"), len(matches), cfg)

    processed = load_processed()
    finished = [
        m for m in matches
        if m.get("status") in FINISHED_STATUSES and m.get("id") not in processed
    ]

    if not finished:
        log(f"ℹ️ {len(matches)} match(s) dans la fenetre, aucun nouveau terminé.")
        return 0

    for match in finished:
        try:
            process_fixture(api, match, cfg)
            processed.add(match["id"])
            save_processed(processed)
        except Exception as exc:
            log(f"❌ Echec traitement match {match.get('id')} : {exc}")

    log(f"✅ Terminé : {len(finished)} match(s) traité(s).")
    git_push_changes(len(finished))
    return 0


if __name__ == "__main__":
    sys.exit(main())
