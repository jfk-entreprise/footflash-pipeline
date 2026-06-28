#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 3 : Analyse des moments cles d'un match (Coupe du Monde 2026)
==============================================================================

Pipeline AutoGoal / FootFlash.   Chainage : Bloc1 -> Bloc2 -> [Bloc3].

Role :
    1. Lire chaque JSON brut produit par le Bloc 1 (01_Donnees/matchs/*_data.json).
    2. N'analyser QUE les matchs dont le clip officiel FIFA est deja telecharge
       (02_Clips/bruts/..._brut.mp4) -> on veut les clips ET les donnees.
    3. Extraire les moments cles : buts, penalties, cartons rouges, tournants.
    4. Ponderer l'importance de chaque moment (selection des temps forts par
       le Bloc 4 : 3 points max, format court).
    5. Synthetiser stats cles, scenario du match et axes d'analyse tactique.
    6. Ecrire AAAA-MM-JJ_equipe1-equipe2_moments.json dans 01_Donnees/matchs/.

Sortie consommee par les 2 scripts du Bloc 4 :
    - script "buts"   -> liste `moments` filtree sur type == "but", triee minute.
    - script "analyse"-> `scenario`, `stats_cles`, `axes_analyse`.

Contraintes : STDLIB UNIQUEMENT (zero pip install), comme Bloc 1 et Bloc 2.
Anti-doublon : fichier d'etat .analyzed.json (un match analyse une seule fois).
Securite     : aucun secret en dur ; Telegram lu depuis l'environnement (option).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import parse, request

# --------------------------------------------------------------------------- #
# Chemins du projet (resolus relativement a l'emplacement du script)
# --------------------------------------------------------------------------- #
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent


def _find_dir(root: Path, keyword: str) -> Path:
    """Retrouve un sous-dossier par mot-cle (gere les prefixes type emoji)."""
    for child in root.iterdir():
        if child.is_dir() and keyword.lower() in child.name.lower():
            return child
    raise FileNotFoundError(f"Dossier contenant '{keyword}' introuvable sous {root}")


DONNEES_DIR = _find_dir(PROJECT_ROOT, "01_Donnees")
MATCHS_DIR = _find_dir(DONNEES_DIR, "matchs")
ARCHIVES_DIR = _find_dir(DONNEES_DIR, "archives")
CLIPS_DIR = _find_dir(PROJECT_ROOT, "02_Clips")
BRUTS_DIR = _find_dir(CLIPS_DIR, "bruts")

ANALYZED_FILE = MATCHS_DIR / ".analyzed.json"
LOG_FILE = ARCHIVES_DIR / "log.md"

REQUEST_TIMEOUT = 30


# --------------------------------------------------------------------------- #
# Journalisation  ([Bloc3], coherent avec Bloc1/Bloc2)
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- `{ts}` — [Bloc3] {message}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Journal de suivi — FootFlash\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())


# --------------------------------------------------------------------------- #
# Telegram (optionnel, non bloquant — notifie que l'analyse est prete)
# --------------------------------------------------------------------------- #
def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
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
            return bool(json.loads(resp.read().decode("utf-8")).get("ok"))
    except Exception as exc:  # notification non bloquante
        log(f"⚠️ Echec notification Telegram : {exc}")
        return False


# --------------------------------------------------------------------------- #
# Etat (anti-doublon)
# --------------------------------------------------------------------------- #
def load_analyzed() -> set[int]:
    if ANALYZED_FILE.exists():
        try:
            return set(json.loads(ANALYZED_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_analyzed(ids: set[int]) -> None:
    ANALYZED_FILE.write_text(json.dumps(sorted(ids)), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Helpers d'extraction
# --------------------------------------------------------------------------- #
def _minute(ev_time: dict) -> int:
    """Minute reelle = elapsed + extra (temps additionnel)."""
    elapsed = ev_time.get("elapsed") or 0
    extra = ev_time.get("extra") or 0
    return int(elapsed) + int(extra)


def _stat_map(statistics: list, team_id: int) -> dict:
    """Renvoie {type_normalise: valeur} pour une equipe donnee."""
    for block in statistics:
        if (block.get("team") or {}).get("id") == team_id:
            out = {}
            for item in block.get("statistics", []):
                out[(item.get("type") or "").strip()] = item.get("value")
            return out
    return {}


def _to_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().rstrip("%")
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Coeur : analyse d'un match
# --------------------------------------------------------------------------- #
def analyser_match(record: dict, source_name: str, clip_rel: str) -> dict:
    teams = record.get("equipes", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    home_id, away_id = home.get("id"), away.get("id")
    home_name, away_name = home.get("name", "?"), away.get("name", "?")

    goals = record.get("score", {}).get("buts", {}) or {}
    gh = goals.get("home") or 0
    ga = goals.get("away") or 0

    fixture = record.get("match", {})
    league = record.get("competition", {})
    date_iso = (fixture.get("date") or "")[:10]
    venue = (fixture.get("venue") or {}).get("name")

    # --- Parcours chronologique des evenements -> moments + score courant --- #
    events = sorted(
        record.get("events", []),
        key=lambda e: (_minute(e.get("time", {})), e.get("time", {}).get("elapsed") or 0),
    )
    score = {"home": 0, "away": 0}
    moments: list[dict] = []

    for ev in events:
        etype = (ev.get("type") or "").strip()
        detail = (ev.get("detail") or "").strip()
        team_id = (ev.get("team") or {}).get("id")
        team_name = (ev.get("team") or {}).get("name", "?")
        player = (ev.get("player") or {}).get("name")
        assist = (ev.get("assist") or {}).get("name")
        minute = _minute(ev.get("time", {}))
        cote = "home" if team_id == home_id else "away"

        if etype == "Goal" and detail != "Missed Penalty":
            # Un csc compte pour l'equipe adverse.
            if detail == "Own Goal":
                cote = "away" if team_id == home_id else "home"
            score[cote] += 1
            label, importance = _classer_but(
                cote, minute, dict(score), detail, gh, ga
            )
            moments.append({
                "minute": minute,
                "type": "but",
                "equipe": home_name if cote == "home" else away_name,
                "joueur": player,
                "passeur": assist,
                "detail": detail,                       # Normal Goal / Penalty / Own Goal
                "score_apres": {"home": score["home"], "away": score["away"]},
                "label": label,
                "importance": importance,
            })

        elif etype == "Goal" and detail == "Missed Penalty":
            moments.append({
                "minute": minute, "type": "penalty_manque",
                "equipe": team_name, "joueur": player, "passeur": None,
                "detail": detail,
                "score_apres": {"home": score["home"], "away": score["away"]},
                "label": "Penalty manqué", "importance": 60,
            })

        elif etype == "Card" and detail == "Red Card":
            moments.append({
                "minute": minute, "type": "carton_rouge",
                "equipe": team_name, "joueur": player, "passeur": None,
                "detail": detail,
                "score_apres": {"home": score["home"], "away": score["away"]},
                "label": "Carton rouge", "importance": 70,
            })

    moments.sort(key=lambda m: (-m["importance"], m["minute"]))

    # --- Stats cles --- #
    statistics = record.get("statistics", [])
    sh, sa = _stat_map(statistics, home_id), _stat_map(statistics, away_id)

    def _paire(key, conv):
        return {"home": conv(sh.get(key)), "away": conv(sa.get(key))}

    stats_cles = {
        "possession": _paire("Ball Possession", _to_int),
        "tirs_total": _paire("Total Shots", _to_int),
        "tirs_cadres": _paire("Shots on Goal", _to_int),
        "corners": _paire("Corner Kicks", _to_int),
        "xg": _paire("expected_goals", _to_float),
    }

    vainqueur = home_name if gh > ga else away_name if ga > gh else None
    scenario = _scenario(home_name, away_name, gh, ga, moments)
    axes = _axes_analyse(home_name, away_name, gh, ga, stats_cles, moments)

    return {
        "source": source_name,
        "clip": clip_rel,
        "fixture_id": record.get("fixture_id"),
        "genere_le": datetime.now(timezone.utc).isoformat(),
        "match": {
            "date": date_iso,
            "competition": league.get("name"),
            "tour": league.get("round"),
            "stade": venue,
            "domicile": home_name,
            "exterieur": away_name,
            "score_final": {"home": gh, "away": ga},
            "vainqueur": vainqueur,
        },
        "scenario": scenario,
        "moments": moments,
        "stats_cles": stats_cles,
        "axes_analyse": axes,
    }


def _classer_but(cote, minute, score, detail, gh_final, ga_final) -> tuple[str, int]:
    """Label + score d'importance (0-100) d'un but selon le contexte."""
    importance = 50
    labels = []

    diff = score["home"] - score["away"]
    autre = "away" if cote == "home" else "home"

    if score["home"] == score["away"]:
        labels.append("Égalisation")
        importance += 25
    elif (cote == "home" and diff == 1) or (cote == "away" and diff == -1):
        # ce but fait passer l'equipe devant
        labels.append("But qui donne l'avantage")
        importance += 20

    if detail == "Penalty":
        labels.append("Penalty")
        importance += 5
    if detail == "Own Goal":
        labels.append("CSC")
        importance += 10

    if minute <= 5:
        labels.append("But précoce")
        importance += 10
    elif minute >= 85:
        labels.append("But tardif")
        importance += 15

    # But qui scelle le resultat final (dernier ecart decisif)
    if (gh_final > ga_final and score["home"] - score["away"] == gh_final - ga_final
            and cote == "home") or \
       (ga_final > gh_final and score["away"] - score["home"] == ga_final - gh_final
            and cote == "away"):
        labels.append("But décisif")
        importance += 15

    importance = max(0, min(100, importance))
    return (" · ".join(labels) if labels else "But"), importance


def _scenario(home, away, gh, ga, moments) -> str:
    """Phrase de scenario du match (1 ligne, pour le hook / contexte)."""
    but_minutes = [m["minute"] for m in moments if m["type"] == "but"]
    dernier_but = max(but_minutes) if but_minutes else None
    rouges = any(m["type"] == "carton_rouge" for m in moments)

    if gh == ga:
        if gh == 0:
            base = f"Match fermé et sans but entre {home} et {away}."
        else:
            base = f"Partage des points spectaculaire : {home} {gh}–{ga} {away}."
    else:
        vainqueur = home if gh > ga else away
        perdant = away if gh > ga else home
        marge = abs(gh - ga)
        if marge >= 3:
            base = f"Démonstration de {vainqueur}, qui domine {perdant} {gh}–{ga}."
        elif dernier_but is not None and dernier_but >= 80:
            base = f"{vainqueur} arrache la victoire dans le money-time face à {perdant}."
        else:
            base = f"{vainqueur} s'impose {gh}–{ga} face à {perdant}."

    if rouges:
        base += " Un carton rouge a fait basculer la rencontre."
    return base


def _axes_analyse(home, away, gh, ga, stats, moments) -> list[str]:
    """Quelques angles tactiques derives des stats (matiere pour le script analyse)."""
    axes: list[str] = []

    pos = stats["possession"]
    if pos["home"] is not None and pos["away"] is not None:
        dom = home if pos["home"] >= pos["away"] else away
        axes.append(f"Maîtrise du ballon : {dom} ({max(pos['home'], pos['away'])}%).")

    tc = stats["tirs_cadres"]
    tt = stats["tirs_total"]
    if tc["home"] is not None and tc["away"] is not None:
        axes.append(
            f"Efficacité offensive : {home} {tc['home']}/{tt.get('home','?')} tirs cadrés, "
            f"{away} {tc['away']}/{tt.get('away','?')}."
        )

    xg = stats["xg"]
    if xg["home"] is not None and xg["away"] is not None:
        # ecart entre buts reels et xG = sur/sous-performance
        sur_h = gh - xg["home"]
        sur_a = ga - xg["away"]
        if abs(sur_h) >= 1 or abs(sur_a) >= 1:
            cible = home if abs(sur_h) >= abs(sur_a) else away
            axes.append(f"Réalisme devant le but : {cible} a sur/sous-performé son xG.")

    rouges = [m for m in moments if m["type"] == "carton_rouge"]
    if rouges:
        r = rouges[0]
        axes.append(f"Tournant : carton rouge ({r['equipe']}, {r['minute']}').")

    if not axes:
        axes.append("Données statistiques limitées : analyse centrée sur les buts.")
    return axes


# --------------------------------------------------------------------------- #
# Localisation du clip associe a un JSON de match
# --------------------------------------------------------------------------- #
def clip_pour(data_path: Path) -> Path | None:
    """Clip brut attendu : meme prefixe que le JSON, suffixe _brut.mp4."""
    base = data_path.name.replace("_data.json", "")
    cible = BRUTS_DIR / f"{base}_brut.mp4"
    return cible if cible.exists() else None


# --------------------------------------------------------------------------- #
# Push automatique (ignore sous GitHub Actions : le workflow s'en charge)
# --------------------------------------------------------------------------- #
def git_push_changes(nb: int) -> None:
    if nb <= 0:
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
        _git("commit", "-m", f"Bloc3: {nb} analyse(s) de moments cles")
        code, out = _git("push")
        log("⬆️ Push reussi (analyses)." if code == 0 else f"⚠️ Echec git push : {out[:200]}")
    except Exception as exc:
        log(f"⚠️ Erreur git push : {exc}")


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    analyzed = load_analyzed()
    data_files = sorted(MATCHS_DIR.glob("*_data.json"))
    if not data_files:
        log("ℹ️ Aucun JSON de match a analyser.")
        return 0

    nouveaux = 0
    for data_path in data_files:
        try:
            record = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"❌ JSON illisible {data_path.name} : {exc}")
            continue

        fid = record.get("fixture_id")
        if fid in analyzed:
            continue

        clip = clip_pour(data_path)
        if clip is None:
            # Chainage Bloc2 -> Bloc3 : on attend le clip avant d'analyser.
            log(f"⏳ Clip absent pour {data_path.name} — analyse reportee.")
            continue

        clip_rel = str(clip.relative_to(PROJECT_ROOT)).replace("\\", "/")
        try:
            analyse = analyser_match(record, data_path.name, clip_rel)
        except Exception as exc:
            log(f"❌ Echec analyse {data_path.name} : {exc}")
            continue

        out_path = MATCHS_DIR / data_path.name.replace("_data.json", "_moments.json")
        out_path.write_text(json.dumps(analyse, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        m = analyse["match"]
        nb_buts = sum(1 for x in analyse["moments"] if x["type"] == "but")
        log(f"🧠 Moments cles : {out_path.name} "
            f"({m['domicile']} {m['score_final']['home']}–{m['score_final']['away']} "
            f"{m['exterieur']}, {nb_buts} but(s), {len(analyse['moments'])} moment(s)).")

        send_telegram(
            "🧠 <b>Analyse prête — CdM 2026</b>\n"
            f"{m['domicile']} <b>{m['score_final']['home']}–{m['score_final']['away']}</b> "
            f"{m['exterieur']}\n"
            f"{analyse['scenario']}\n"
            f"📁 <code>{out_path.name}</code>"
        )

        analyzed.add(fid)
        save_analyzed(analyzed)
        nouveaux += 1

    if nouveaux == 0:
        log("ℹ️ Aucun nouveau match a analyser (clips manquants ou deja faits).")
    else:
        log(f"✅ Termine : {nouveaux} analyse(s) generee(s).")
        git_push_changes(nouveaux)
    return 0


if __name__ == "__main__":
    sys.exit(main())
