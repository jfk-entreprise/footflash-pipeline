#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 1 : Surveillance des matchs (Coupe du Monde 2026)
==================================================================

Pipeline AutoGoal / FootFlash.

Role :
    1. Interroger API-Football pour les matchs CdM 2026 du jour.
    2. Detecter les matchs termines (statut FT / AET / PEN).
    3. Recuperer toutes les donnees (infos, buts/events, stats, compos).
    4. Sauvegarder un JSON par match dans 01_Donnees/matchs/.
    5. Envoyer une notification Telegram.
    6. Journaliser chaque action dans 01_Donnees/archives/log.md.

Mode d'execution : one-shot (a lancer via une tache planifiee/cron).
Anti-doublon     : fichier d'etat .processed.json (un match notifie une seule fois).

Aucune cle n'est codee en dur : tout est lu depuis 00_Context/config.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

# --------------------------------------------------------------------------- #
# Chemins du projet (resolus relativement a l'emplacement du script)
# --------------------------------------------------------------------------- #
SCRIPT_DIR = Path(__file__).resolve().parent          # .../FootFlash/<03_Scripts>
PROJECT_ROOT = SCRIPT_DIR.parent                      # .../FootFlash

def _find_dir(root: Path, keyword: str) -> Path:
    """Retrouve un sous-dossier par mot-cle (gere les prefixes type emoji)."""
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
LOG_FILE = ARCHIVES_DIR / "log.md"

# --------------------------------------------------------------------------- #
# Parametres CdM 2026 (API-Football : World Cup = league 1)
# --------------------------------------------------------------------------- #
LEAGUE_ID = 1
SEASON = 2026
FINISHED_STATUSES = {"FT", "AET", "PEN"}   # match termine (temps reglementaire / prolong. / t.a.b.)
REQUEST_TIMEOUT = 30                       # secondes
MAX_RETRIES = 3
RETRY_BACKOFF = 5                          # secondes (x tentative)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def load_config(path: Path) -> dict:
    """Lit les paires CLE=VALEUR depuis config.md (ignore titres et commentaires)."""
    cfg: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"config.md introuvable : {path}")
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


# --------------------------------------------------------------------------- #
# Journalisation
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    """Ajoute une ligne horodatee au journal de suivi (et a la console)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- `{ts}` — [Bloc1] {message}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Journal de suivi — FootFlash\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())


# --------------------------------------------------------------------------- #
# Client API-Football
# --------------------------------------------------------------------------- #
class ApiFootball:
    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _get(self, endpoint: str, params: dict) -> dict:
        url = f"{self.base_url}/{endpoint}?{parse.urlencode(params)}"
        headers = {
            "x-apisports-key": self.api_key,
            "Accept": "application/json",
        }
        last_err: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                req = request.Request(url, headers=headers)
                with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                errs = payload.get("errors")
                if errs:
                    # API renvoie un dict/list d'erreurs (quota, parametres, etc.)
                    raise RuntimeError(f"Erreur API ({endpoint}) : {errs}")
                return payload
            except (error.URLError, error.HTTPError, RuntimeError, TimeoutError) as exc:
                last_err = exc
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF * attempt)
        raise RuntimeError(f"Echec appel '{endpoint}' apres {MAX_RETRIES} tentatives : {last_err}")

    def fixtures_of_day(self, day: str) -> list[dict]:
        data = self._get("fixtures", {"league": LEAGUE_ID, "season": SEASON, "date": day})
        return data.get("response", [])

    def events(self, fixture_id: int) -> list[dict]:
        return self._get("fixtures/events", {"fixture": fixture_id}).get("response", [])

    def statistics(self, fixture_id: int) -> list[dict]:
        return self._get("fixtures/statistics", {"fixture": fixture_id}).get("response", [])

    def lineups(self, fixture_id: int) -> list[dict]:
        return self._get("fixtures/lineups", {"fixture": fixture_id}).get("response", [])


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def send_telegram(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    try:
        req = request.Request(url, data=body, method="POST")
        with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return bool(result.get("ok"))
    except Exception as exc:  # notification non bloquante
        log(f"⚠️ Echec notification Telegram : {exc}")
        return False


# --------------------------------------------------------------------------- #
# Etat (anti-doublon)
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
# Utilitaires
# --------------------------------------------------------------------------- #
def slugify(name: str) -> str:
    import unicodedata
    norm = unicodedata.normalize("NFKD", name)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    keep = [c.lower() if c.isalnum() else "-" for c in norm]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "equipe"


def build_filename(fixture: dict) -> str:
    teams = fixture["teams"]
    home = slugify(teams["home"]["name"])
    away = slugify(teams["away"]["name"])
    date_iso = fixture["fixture"]["date"][:10]  # YYYY-MM-DD
    return f"{date_iso}_{home}-{away}_data.json"


# --------------------------------------------------------------------------- #
# Traitement d'un match termine
# --------------------------------------------------------------------------- #
def process_fixture(api: ApiFootball, fixture: dict, cfg: dict) -> Path:
    fid = fixture["fixture"]["id"]
    teams = fixture["teams"]
    goals = fixture["goals"]
    home, away = teams["home"]["name"], teams["away"]["name"]

    record = {
        "fixture_id": fid,
        "recupere_le": datetime.now(timezone.utc).isoformat(),
        "competition": fixture.get("league", {}),
        "match": fixture.get("fixture", {}),
        "equipes": teams,
        "score": {
            "buts": goals,
            "detail": fixture.get("score", {}),
        },
        "events": api.events(fid),       # buts, passeurs, cartons, remplacements
        "statistics": api.statistics(fid),
        "lineups": api.lineups(fid),
    }

    out_path = MATCHS_DIR / build_filename(fixture)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"💾 Donnees sauvegardees : {out_path.name} ({home} {goals['home']}–{goals['away']} {away})")

    msg = (
        "⚽ <b>Match terminé — CdM 2026</b>\n"
        f"{home} <b>{goals['home']}–{goals['away']}</b> {away}\n"
        f"📁 <code>{out_path.name}</code>\n"
        f"Données prêtes : buts, stats, compositions."
    )
    if send_telegram(cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", ""), msg):
        log(f"📨 Notification Telegram envoyée pour {home}-{away}")
    return out_path


# --------------------------------------------------------------------------- #
# Push automatique -> declenche le Bloc 2 (GitHub Actions)
# --------------------------------------------------------------------------- #
def git_push_changes(nb_matchs: int) -> None:
    """Commit + push des nouveaux JSON (et du log) vers footflash-pipeline.

    Le push sur 01_Donnees/matchs/*.json declenche le workflow Bloc 2.
    Non bloquant : un echec git ne doit jamais interrompre le pipeline.
    NB : config.md et .processed.json sont exclus par .gitignore (pas de fuite).
    """
    if nb_matchs <= 0:
        return
    if not (PROJECT_ROOT / ".git").exists():
        log("ℹ️ Pas de depot git (.git absent) — push ignore (Bloc 2 non declenche).")
        return

    def _git(*args: str) -> tuple[int, str]:
        res = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), *args],
            capture_output=True, text=True,
        )
        return res.returncode, (res.stdout + res.stderr).strip()

    try:
        _git("add", "-A")
        code, _ = _git("diff", "--cached", "--quiet")
        if code == 0:                      # 0 = rien de stage
            log("ℹ️ Rien de nouveau a pousser.")
            return
        _git("commit", "-m", f"Bloc1: {nb_matchs} nouveau(x) match(s) — donnees JSON")
        code, out = _git("push")
        if code == 0:
            log(f"⬆️ Push reussi vers footflash-pipeline — Bloc 2 declenche ({nb_matchs} match(s)).")
        else:
            log(f"⚠️ Echec git push : {out[:200]}")
    except FileNotFoundError:
        log("⚠️ git introuvable sur la machine — push ignore.")
    except Exception as exc:
        log(f"⚠️ Erreur git push : {exc}")


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    cfg = load_config(CONFIG_FILE)
    api_key = cfg.get("API_KEY")
    base_url = cfg.get("BASE_URL", "https://v3.football.api-sports.io")
    if not api_key:
        log("❌ API_KEY absente de config.md — arret.")
        return 1

    api = ApiFootball(api_key, base_url)
    MATCHS_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log(f"🔎 Surveillance des matchs CdM 2026 du {today}")

    try:
        fixtures = api.fixtures_of_day(today)
    except RuntimeError as exc:
        log(f"❌ {exc}")
        return 1

    if not fixtures:
        log("ℹ️ Aucun match CdM 2026 programmé aujourd'hui.")
        return 0

    processed = load_processed()
    finished = [
        f for f in fixtures
        if f["fixture"]["status"]["short"] in FINISHED_STATUSES
        and f["fixture"]["id"] not in processed
    ]

    if not finished:
        log(f"ℹ️ {len(fixtures)} match(s) aujourd'hui, aucun nouveau match terminé.")
        return 0

    for fixture in finished:
        try:
            process_fixture(api, fixture, cfg)
            processed.add(fixture["fixture"]["id"])
            save_processed(processed)
        except Exception as exc:
            log(f"❌ Echec traitement fixture {fixture['fixture']['id']} : {exc}")

    log(f"✅ Terminé : {len(finished)} match(s) traité(s).")
    git_push_changes(len(finished))
    return 0


if __name__ == "__main__":
    sys.exit(main())
