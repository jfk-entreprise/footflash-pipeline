#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 4 / Script "BUTS" (Coupe du Monde 2026)
========================================================

Pipeline AutoGoal / FootFlash.   Chainage : Bloc1 -> Bloc2 -> Bloc3 -> [Bloc4].

Role :
    1. Lire chaque analyse produite par le Bloc 3 (01_Donnees/matchs/*_moments.json).
    2. Generer un script de voix off centre sur les BUTS (cible 60-90 s) :
         [HOOK]   accroche choc (0-3 s)
         [CORPS]  1 beat par but : minute, buteur (passeur), contexte du score, emotion
         [OUTRO]  call-to-action
    3. Ecrire 03_Scripts/buts/AAAA-MM-JJ_eq1-eq2_script-buts.txt
       (texte pret pour le Bloc 5 ; les lignes '#' = metadonnees ignorees par la voix off).

Format respectant 00_Context/style.md : ton passionne, phrases courtes, accessible.
Contraintes : STDLIB UNIQUEMENT. Anti-doublon : etat partage .scripted.json.
Journalisation : [Bloc4].
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Chemins
# --------------------------------------------------------------------------- #
SCRIPT_DIR = Path(__file__).resolve().parent          # .../03_Scripts/buts
SCRIPTS_DIR = SCRIPT_DIR.parent                       # .../03_Scripts
PROJECT_ROOT = SCRIPTS_DIR.parent                     # racine du repo


def _find_dir(root: Path, keyword: str) -> Path:
    for child in root.iterdir():
        if child.is_dir() and keyword.lower() in child.name.lower():
            return child
    raise FileNotFoundError(f"Dossier contenant '{keyword}' introuvable sous {root}")


DONNEES_DIR = _find_dir(PROJECT_ROOT, "01_Donnees")
MATCHS_DIR = _find_dir(DONNEES_DIR, "matchs")
ARCHIVES_DIR = _find_dir(DONNEES_DIR, "archives")
BUTS_DIR = SCRIPT_DIR                                  # sortie : 03_Scripts/buts/

LOG_FILE = ARCHIVES_DIR / "log.md"
STATE_FILE = MATCHS_DIR / ".scripted.json"

MOTS_PAR_SECONDE = 2.5                                 # debit voix off FR (estimation)


# --------------------------------------------------------------------------- #
# Journalisation [Bloc4]
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- `{ts}` — [Bloc4] {message}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Journal de suivi — FootFlash\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())


# --------------------------------------------------------------------------- #
# Etat partage .scripted.json  -> {fixture_id: {"buts": bool, "analyse": bool}}
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def mark_state(fixture_id, champ: str) -> None:
    """Met a jour le flag sans ecraser l'autre script (merge)."""
    state = load_state()
    key = str(fixture_id)
    entry = state.get(key, {})
    entry[champ] = True
    state[key] = entry
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Helpers redaction
# --------------------------------------------------------------------------- #
def _duree_estimee(texte_parle: str) -> int:
    mots = len(texte_parle.split())
    return round(mots / MOTS_PAR_SECONDE)


def _contexte_match(m: dict) -> str:
    """Tag de contexte pour choisir le hook : comeback / late / blowout / draw / win."""
    score = m["match"]["score_final"]
    gh, ga = score["home"], score["away"]
    buts = [x for x in m["moments"] if x["type"] == "but"]

    if gh == ga:
        return "nul_blanc" if gh == 0 else "nul_spectacle"

    vainqueur_cote = "home" if gh > ga else "away"
    # Le vainqueur a-t-il ete mene a un moment ? (comeback)
    mene = False
    for b in sorted(buts, key=lambda x: x["minute"]):
        sa = b["score_apres"]
        if (vainqueur_cote == "home" and sa["home"] < sa["away"]) or \
           (vainqueur_cote == "away" and sa["away"] < sa["home"]):
            mene = True
    dernier_but = max((b["minute"] for b in buts), default=0)

    if abs(gh - ga) >= 3:
        return "demonstration"
    if mene and dernier_but >= 75:
        return "comeback"
    if dernier_but >= 80:
        return "money_time"
    return "victoire"


def _hook(m: dict) -> str:
    ctx = _contexte_match(m)
    home = m["match"]["domicile"]
    away = m["match"]["exterieur"]
    gh = m["match"]["score_final"]["home"]
    ga = m["match"]["score_final"]["away"]
    vainqueur = m["match"]["vainqueur"]
    perdant = away if vainqueur == home else home

    banque = {
        "nul_blanc": f"0-0… et pourtant ce {home}-{away} avait tout d'un thriller.",
        "nul_spectacle": f"{gh} buts partout : impossible de les départager, et tu vas comprendre pourquoi !",
        "demonstration": f"{vainqueur} a infligé une véritable leçon : {gh}-{ga}, sans pitié.",
        "comeback": f"{vainqueur} était au bord du gouffre… et a tout renversé. Accroche-toi !",
        "money_time": f"Tout s'est joué dans les dernières minutes : voici comment {vainqueur} a fait craquer {perdant}.",
        "victoire": f"{vainqueur} s'impose face à {perdant} : retour sur tous les buts.",
    }
    return banque.get(ctx, banque["victoire"])


def _beat_but(b: dict, home: str, away: str) -> str:
    """Une phrase pour un but : minute, buteur (passeur), contexte score, emotion."""
    minute = b["minute"]
    joueur = b.get("joueur") or "Un joueur"
    passeur = b.get("passeur")
    detail = b.get("detail", "")
    label = b.get("label", "")
    sa = b["score_apres"]
    score_txt = f"{sa['home']}-{sa['away']}"

    # Mention passeur / type de but
    suffixe = ""
    if detail == "Penalty":
        suffixe = " sur penalty"
    elif detail == "Own Goal":
        suffixe = " contre son camp"
    elif passeur:
        suffixe = f", servi par {passeur},"

    # Contexte + emotion selon le label de l'analyse
    if "Égalisation" in label:
        contexte = f"remet les pendules à l'heure ({score_txt}) et relance totalement la rencontre"
    elif "But décisif" in label or "But tardif" in label:
        contexte = f"plante le but qui fait basculer le match ({score_txt}) — quelle delivrance"
    elif "But qui donne l'avantage" in label:
        contexte = f"fait passer son équipe devant ({score_txt}) au meilleur des moments"
    elif "But précoce" in label:
        contexte = f"lance les hostilités d'entrée ({score_txt}) et assomme le match"
    else:
        contexte = f"alourdit l'addition ({score_txt})"

    return f"{minute}' — {joueur}{suffixe} {contexte}."


def _outro(m: dict) -> str:
    return ("Quel but t'a fait bondir de ton canapé ? Dis-le en commentaire, "
            "et abonne-toi pour ne rien rater de la Coupe du Monde 2026 !")


# --------------------------------------------------------------------------- #
# Construction du script
# --------------------------------------------------------------------------- #
def construire_script(m: dict, source_name: str) -> str:
    home = m["match"]["domicile"]
    away = m["match"]["exterieur"]
    buts = sorted((x for x in m["moments"] if x["type"] == "but"),
                  key=lambda x: x["minute"])

    hook = _hook(m)
    corps = [_beat_but(b, home, away) for b in buts]
    if not corps:
        corps = ["Aucun but dans cette rencontre, mais l'intensité, elle, était bien là."]
    outro = _outro(m)

    parle = "\n".join([hook, *corps, outro])
    duree = _duree_estimee(parle)
    score = m["match"]["score_final"]

    lignes = [
        f"# Script BUTS — {home} {score['home']}–{score['away']} {away} — {m['match']['date']}",
        f"# Durée estimée : ~{duree} s (cible 60-90 s)",
        f"# Source : {source_name}",
        "# (Bloc 5 : ignorer les lignes commençant par #)",
        "",
        "[HOOK]",
        hook,
        "",
        "[CORPS]",
        *corps,
        "",
        "[OUTRO]",
        outro,
        "",
    ]
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# Push (ignore sous GitHub Actions)
# --------------------------------------------------------------------------- #
def git_push_changes(nb: int) -> None:
    if nb <= 0 or os.environ.get("GITHUB_ACTIONS") == "true":
        if nb > 0:
            log("ℹ️ GitHub Actions detecte — push gere par le workflow (auto-push ignore).")
        return
    if not (PROJECT_ROOT / ".git").exists():
        return

    def _git(*a):
        r = subprocess.run(["git", "-C", str(PROJECT_ROOT), *a],
                           capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    try:
        _git("add", "-A")
        if _git("diff", "--cached", "--quiet")[0] == 0:
            return
        _git("commit", "-m", f"Bloc4: {nb} script(s) buts")
        code, out = _git("push")
        log("⬆️ Push reussi (scripts buts)." if code == 0 else f"⚠️ Echec git push : {out[:200]}")
    except Exception as exc:
        log(f"⚠️ Erreur git push : {exc}")


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    state = load_state()
    fichiers = sorted(MATCHS_DIR.glob("*_moments.json"))
    if not fichiers:
        log("ℹ️ Aucun _moments.json — rien a scripter (buts).")
        return 0

    nouveaux = 0
    for path in fichiers:
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"❌ _moments.json illisible {path.name} : {exc}")
            continue

        fid = m.get("fixture_id")
        if state.get(str(fid), {}).get("buts"):
            continue

        base = path.name.replace("_moments.json", "")
        out_path = BUTS_DIR / f"{base}_script-buts.txt"
        try:
            out_path.write_text(construire_script(m, path.name), encoding="utf-8")
        except Exception as exc:
            log(f"❌ Echec generation script buts {path.name} : {exc}")
            continue

        mark_state(fid, "buts")
        nb_buts = sum(1 for x in m["moments"] if x["type"] == "but")
        log(f"🎙️ Script buts : {out_path.name} ({nb_buts} but(s)).")
        nouveaux += 1

    if nouveaux == 0:
        log("ℹ️ Aucun nouveau script buts a generer.")
    else:
        log(f"✅ Termine : {nouveaux} script(s) buts genere(s).")
        git_push_changes(nouveaux)
    return 0


if __name__ == "__main__":
    sys.exit(main())
