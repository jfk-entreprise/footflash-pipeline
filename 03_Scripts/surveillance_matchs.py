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

Aucune cle n'est codee en dur : secrets lus depuis l'environnement (GitHub
Secrets) en priorite, repli sur 00_Context/config.md en local.
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
HEALTH_FILE = MATCHS_DIR / ".health.json"   # suivi des jours sans match (tracke par git)
LOG_FILE = ARCHIVES_DIR / "log.md"

# --------------------------------------------------------------------------- #
# Parametres CdM 2026 (API-Football : World Cup = league 1)
# --------------------------------------------------------------------------- #
LEAGUE_ID = 1
SEASON = 2026
TOURNOI_START = "2026-06-11"                # fenetre officielle CdM 2026
TOURNOI_END = "2026-07-19"
JOURS_VIDES_ALERTE = 3                      # alerte si N jours consecutifs sans match
FINISHED_STATUSES = {"FT", "AET", "PEN"}   # match termine (temps reglementaire / prolong. / t.a.b.)
REQUEST_TIMEOUT = 30                       # secondes
MAX_RETRIES = 3
RETRY_BACKOFF = 5                          # secondes (x tentative)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def _parse_config_md(path: Path) -> dict:
    """Lit les paires CLE=VALEUR depuis config.md (ignore titres et commentaires)."""
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
    """Construit la config en priorisant l'environnement (GitHub Secrets), repli config.md.

    - En CI (GitHub Actions) : les secrets viennent de l'environnement, config.md
      est absent/gitignore.
    - En local : repli sur 00_Context/config.md.
    Le secret GitHub `API_FOOTBALL_KEY` est mappe sur la cle interne `API_KEY`
    (config.md utilise historiquement `API_KEY`).
    """
    cfg = _parse_config_md(path)        # base locale (peut etre vide en CI)

    # L'environnement a la priorite sur config.md.
    env_map = {
        "API_KEY": ("API_FOOTBALL_KEY", "API_KEY"),   # secret CI, puis nom historique
        "BASE_URL": ("BASE_URL",),
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

    def fixtures_upcoming(self, n: int = 10) -> list[dict]:
        """Les n prochains matchs de la ligue/saison (sert au self-test de couverture)."""
        data = self._get("fixtures", {"league": LEAGUE_ID, "season": SEASON, "next": n})
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
    # Sous GitHub Actions : c'est le workflow qui commit/push ET declenche le Bloc 2
    # (un push fait par GITHUB_TOKEN ne declenche pas d'autre workflow). On n'auto-pousse pas.
    if os.environ.get("GITHUB_ACTIONS") == "true":
        log("ℹ️ GitHub Actions detecte — push gere par le workflow (auto-push ignore).")
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
# Sante du pipeline (C2) : alerte si aucun match detecte pendant N jours
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


def maj_sante(jour: str, nb_fixtures: int, cfg: dict) -> None:
    """Suit les jours du tournoi sans aucun match programme et alerte au seuil."""
    if not (TOURNOI_START <= jour <= TOURNOI_END):
        return                                          # hors tournoi : on ne compte pas
    h = load_health()
    vides = [d for d in h.get("jours_vides", []) if d != jour]
    if nb_fixtures == 0:
        vides.append(jour)
        vides = sorted(set(vides))[-JOURS_VIDES_ALERTE:]
        if len(vides) >= JOURS_VIDES_ALERTE and not h.get("alerte_envoyee"):
            send_telegram(
                cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", ""),
                "🚑 <b>FootFlash — anomalie détection</b>\n"
                f"Aucun match CdM 2026 détecté depuis {JOURS_VIDES_ALERTE} jours "
                f"({', '.join(vides)}).\n"
                "Vérifier la couverture API-Football (league=1 / season=2026) et le plan.",
            )
            log(f"🚑 Alerte santé envoyée : {JOURS_VIDES_ALERTE} jours sans match.")
            h["alerte_envoyee"] = True
    else:
        vides = []
        h["alerte_envoyee"] = False
    h["jours_vides"] = vides
    save_health(h)


# --------------------------------------------------------------------------- #
# Self-test de couverture API (C2) :  python surveillance_matchs.py --verify
# --------------------------------------------------------------------------- #
def verifier_couverture(api: "ApiFootball", cfg: dict) -> int:
    log("🩺 Self-test couverture API-Football (league=1, season=2026, next=10)…")
    try:
        prochains = api.fixtures_upcoming(10)
    except RuntimeError as exc:
        log(f"❌ Self-test KO : {exc}")
        send_telegram(cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", ""),
                      f"❌ <b>FootFlash — self-test API KO</b>\n{exc}")
        return 1
    n = len(prochains)
    apercu = ", ".join(
        f"{f['teams']['home']['name']}-{f['teams']['away']['name']} ({f['fixture']['date'][:10]})"
        for f in prochains[:3]
    )
    log(f"🩺 {n} match(s) à venir renvoyé(s) par l'API. {apercu}")
    send_telegram(
        cfg.get("TELEGRAM_BOT_TOKEN", ""), cfg.get("TELEGRAM_CHAT_ID", ""),
        f"🩺 <b>FootFlash — self-test API</b>\n{n} match(s) à venir. {apercu or '—'}\n"
        + ("✅ Couverture OK." if n > 0 else "⚠️ 0 match : vérifier league/season/plan."),
    )
    return 0 if n > 0 else 2


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Bloc 1 — détection des matchs CdM 2026.")
    ap.add_argument("--verify", action="store_true",
                    help="Self-test de couverture API (next=10) puis sortie.")
    args = ap.parse_args()

    cfg = load_config(CONFIG_FILE)
    api_key = cfg.get("API_KEY")
    base_url = cfg.get("BASE_URL", "https://v3.football.api-sports.io")
    if not api_key:
        log("❌ API_KEY absente (env GitHub Secrets ou config.md) — arret.")
        return 1

    api = ApiFootball(api_key, base_url)
    MATCHS_DIR.mkdir(parents=True, exist_ok=True)

    if args.verify:
        return verifier_couverture(api, cfg)

    # C1 : on interroge HIER + AUJOURD'HUI (UTC) pour capter les matchs nocturnes
    # nord-americains terminés après 23h UTC de leur jour de coup d'envoi.
    now = datetime.now(timezone.utc)
    jours = [
        (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        now.strftime("%Y-%m-%d"),
    ]
    log(f"🔎 Surveillance des matchs CdM 2026 — jours UTC {jours}")

    fixtures_par_id: dict[int, dict] = {}
    for jour in jours:
        try:
            for f in api.fixtures_of_day(jour):
                fixtures_par_id[f["fixture"]["id"]] = f      # dedup par fixture_id
        except RuntimeError as exc:
            log(f"❌ {exc}")
            return 1
    fixtures = list(fixtures_par_id.values())

    # Suivi de santé sur le jour courant (alerte si 0 match en plein tournoi)
    maj_sante(now.strftime("%Y-%m-%d"), len(fixtures), cfg)

    if not fixtures:
        log("ℹ️ Aucun match CdM 2026 programmé (hier/aujourd'hui).")
        return 0

    processed = load_processed()
    finished = [
        f for f in fixtures
        if f["fixture"]["status"]["short"] in FINISHED_STATUSES
        and f["fixture"]["id"] not in processed
    ]

    if not finished:
        log(f"ℹ️ {len(fixtures)} match(s) sur la fenêtre, aucun nouveau terminé.")
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
