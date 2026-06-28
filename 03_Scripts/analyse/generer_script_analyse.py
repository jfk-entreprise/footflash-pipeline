#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 4 / Script "ANALYSE TACTIQUE" (Coupe du Monde 2026)
====================================================================

Pipeline AutoGoal / FootFlash.   Chainage : Bloc1 -> Bloc2 -> Bloc3 -> [Bloc4].

Role :
    1. Lire chaque analyse du Bloc 3 (01_Donnees/matchs/*_moments.json).
    2. Generer un script de voix off d'ANALYSE TACTIQUE (cible 90-120 s) :
         [HOOK]   question tactique choc (0-3 s)
         [CORPS]  3 points forts : (1) systeme de jeu / domination,
                  (2) turning point, (3) 3e angle (realisme / efficacite).
         [OUTRO]  call-to-action
    3. Ecrire 03_Scripts/analyse/AAAA-MM-JJ_eq1-eq2_script-analyse.txt.

Format respectant 00_Context/style.md (3 points, ton accessible, sans jargon).
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
SCRIPT_DIR = Path(__file__).resolve().parent          # .../03_Scripts/analyse
SCRIPTS_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SCRIPTS_DIR.parent


def _find_dir(root: Path, keyword: str) -> Path:
    for child in root.iterdir():
        if child.is_dir() and keyword.lower() in child.name.lower():
            return child
    raise FileNotFoundError(f"Dossier contenant '{keyword}' introuvable sous {root}")


DONNEES_DIR = _find_dir(PROJECT_ROOT, "01_Donnees")
MATCHS_DIR = _find_dir(DONNEES_DIR, "matchs")
ARCHIVES_DIR = _find_dir(DONNEES_DIR, "archives")
ANALYSE_DIR = SCRIPT_DIR                               # sortie : 03_Scripts/analyse/

LOG_FILE = ARCHIVES_DIR / "log.md"
STATE_FILE = MATCHS_DIR / ".scripted.json"

MOTS_PAR_SECONDE = 2.5


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
# Etat partage .scripted.json
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def mark_state(fixture_id, champ: str) -> None:
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
    return round(len(texte_parle.split()) / MOTS_PAR_SECONDE)


def _hook(m: dict) -> str:
    home, away = m["match"]["domicile"], m["match"]["exterieur"]
    gh, ga = m["match"]["score_final"]["home"], m["match"]["score_final"]["away"]
    vainqueur = m["match"]["vainqueur"]
    perdant = away if vainqueur == home else home
    if gh == ga:
        return f"Pourquoi {home} et {away} n'ont-ils pas réussi à se départager ? Décryptage tactique."
    return f"Comment {vainqueur} a-t-il fait tomber {perdant} ? La vraie réponse est tactique."


def _point_systeme(m: dict) -> str:
    """Point 1 — systeme de jeu / domination, derive des stats."""
    home, away = m["match"]["domicile"], m["match"]["exterieur"]
    s = m["stats_cles"]
    pos, tc, tt = s["possession"], s["tirs_cadres"], s["tirs_total"]

    phrases = []
    if pos["home"] is not None and pos["away"] is not None:
        if pos["home"] == pos["away"]:
            phrases.append("Possession partagée : deux équipes qui ont voulu jouer.")
        else:
            dom = home if pos["home"] > pos["away"] else away
            val = max(pos["home"], pos["away"])
            phrases.append(f"{dom} a imposé son tempo en gardant le ballon ({val}%).")
    if tc["home"] is not None and tc["away"] is not None:
        tueur = home if tc["home"] >= tc["away"] else away
        phrases.append(
            f"Mais c'est {tueur} qui a été le plus tranchant : "
            f"{home} {tc['home']}/{tt.get('home','?')} tirs cadrés contre "
            f"{away} {tc['away']}/{tt.get('away','?')}."
        )
    if not phrases:
        phrases.append("Un match équilibré où le système de jeu a fait la différence dans les détails.")
    return " ".join(phrases)


def _point_turning(m: dict) -> str:
    """Point 2 — turning point : rouge prioritaire, sinon but decisif/le plus important."""
    moments = m["moments"]
    rouges = [x for x in moments if x["type"] == "carton_rouge"]
    if rouges:
        r = rouges[0]
        return (f"Le tournant : le carton rouge de {r.get('joueur') or 'un joueur'} "
                f"à la {r['minute']}' a brisé l'équilibre et tout fait basculer.")

    buts = [x for x in moments if x["type"] == "but"]
    decisif = next((b for b in buts if "décisif" in b.get("label", "")
                    or "tardif" in b.get("label", "")), None)
    if decisif is None and buts:
        decisif = max(buts, key=lambda b: b["importance"])
    if decisif:
        sa = decisif["score_apres"]
        return (f"Le tournant : le but de {decisif.get('joueur') or 'un joueur'} "
                f"à la {decisif['minute']}' ({sa['home']}-{sa['away']}) a fait définitivement "
                f"pencher la balance.")
    return "Le tournant s'est joué dans la bataille du milieu, sans fait de jeu marquant."


def _point_realisme(m: dict) -> str:
    """Point 3 — realisme devant le but (xG) ou angle restant de l'analyse Bloc 3."""
    home, away = m["match"]["domicile"], m["match"]["exterieur"]
    gh, ga = m["match"]["score_final"]["home"], m["match"]["score_final"]["away"]
    xg = m["stats_cles"]["xg"]

    if xg["home"] is not None and xg["away"] is not None:
        sur_h, sur_a = gh - xg["home"], ga - xg["away"]
        cible, ecart = (home, sur_h) if abs(sur_h) >= abs(sur_a) else (away, sur_a)
        if ecart >= 0.8:
            return (f"Troisième clé : un réalisme glacial. {cible} a marqué plus que ne "
                    f"le promettait son xG — une efficacité qui a fait la différence.")
        if ecart <= -0.8:
            return (f"Troisième clé : un cruel manque de réalisme. {cible} a largement "
                    f"sous-performé son xG et l'a payé cash.")
        return (f"Troisième clé : une efficacité conforme aux occasions créées "
                f"(xG {home} {xg['home']} – {xg['away']} {away}), pas de hold-up.")

    # Repli : reutilise un axe d'analyse non couvert par les deux premiers points.
    for axe in m.get("axes_analyse", []):
        if "rouge" not in axe.lower() and "Maîtrise" not in axe:
            return f"Troisième clé : {axe[0].lower() + axe[1:]}"
    return "Troisième clé : la gestion des temps faibles aura pesé sur le résultat final."


def _outro() -> str:
    return ("Tu valides cette lecture du match ? Donne ta version en commentaire "
            "et abonne-toi pour les prochaines analyses de la Coupe du Monde 2026 !")


# --------------------------------------------------------------------------- #
# Construction du script
# --------------------------------------------------------------------------- #
def construire_script(m: dict, source_name: str) -> str:
    home, away = m["match"]["domicile"], m["match"]["exterieur"]
    score = m["match"]["score_final"]

    hook = _hook(m)
    p1, p2, p3 = _point_systeme(m), _point_turning(m), _point_realisme(m)
    outro = _outro()

    parle = "\n".join([hook, p1, p2, p3, outro])
    duree = _duree_estimee(parle)

    lignes = [
        f"# Script ANALYSE — {home} {score['home']}–{score['away']} {away} — {m['match']['date']}",
        f"# Durée estimée : ~{duree} s (cible 90-120 s)",
        f"# Scénario : {m.get('scenario', '')}",
        f"# Source : {source_name}",
        "# (Bloc 5 : ignorer les lignes commençant par #)",
        "",
        "[HOOK]",
        hook,
        "",
        "[CORPS]",
        f"1. {p1}",
        f"2. {p2}",
        f"3. {p3}",
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
        _git("commit", "-m", f"Bloc4: {nb} script(s) analyse")
        code, out = _git("push")
        log("⬆️ Push reussi (scripts analyse)." if code == 0 else f"⚠️ Echec git push : {out[:200]}")
    except Exception as exc:
        log(f"⚠️ Erreur git push : {exc}")


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    state = load_state()
    fichiers = sorted(MATCHS_DIR.glob("*_moments.json"))
    if not fichiers:
        log("ℹ️ Aucun _moments.json — rien a scripter (analyse).")
        return 0

    nouveaux = 0
    for path in fichiers:
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"❌ _moments.json illisible {path.name} : {exc}")
            continue

        fid = m.get("fixture_id")
        if state.get(str(fid), {}).get("analyse"):
            continue

        base = path.name.replace("_moments.json", "")
        out_path = ANALYSE_DIR / f"{base}_script-analyse.txt"
        try:
            out_path.write_text(construire_script(m, path.name), encoding="utf-8")
        except Exception as exc:
            log(f"❌ Echec generation script analyse {path.name} : {exc}")
            continue

        mark_state(fid, "analyse")
        log(f"🎙️ Script analyse : {out_path.name}.")
        nouveaux += 1

    if nouveaux == 0:
        log("ℹ️ Aucun nouveau script analyse a generer.")
    else:
        log(f"✅ Termine : {nouveaux} script(s) analyse genere(s).")
        git_push_changes(nouveaux)
    return 0


if __name__ == "__main__":
    sys.exit(main())
