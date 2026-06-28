#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 5 : Generation de la voix off (Coupe du Monde 2026)
====================================================================

Pipeline AutoGoal / FootFlash.   Chainage : Bloc1 -> ... -> Bloc4 -> [Bloc5].

Role :
    1. Lire les scripts texte du Bloc 4 :
         03_Scripts/buts/*_script-buts.txt
         03_Scripts/analyse/*_script-analyse.txt
    2. Extraire le texte parle (lignes sous [HOOK]/[CORPS]/[OUTRO], les lignes
       commencant par '#' et les balises elles-memes sont ignorees).
    3. Normaliser pour une lecture naturelle (puces 1./2./3. retirees,
       "88' —" -> "88e minute,").
    4. Synthetiser la voix avec Kokoro TTS (voix francaise ff_siwis, repli anglais).
    5. Ecrire 04_Audio/voixoff/AAAA-MM-JJ_eq1-eq2_voix-buts.mp3 (resp. _voix-analyse.mp3).

Dependances (installees par le workflow, PAS stdlib) :
    pip : kokoro, soundfile        |  systeme : espeak-ng, ffmpeg
Anti-doublon : etat .voiced.json.   Journalisation : [Bloc5].
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Chemins
# --------------------------------------------------------------------------- #
SCRIPT_DIR = Path(__file__).resolve().parent          # .../03_Scripts
PROJECT_ROOT = SCRIPT_DIR.parent


def _find_dir(root: Path, keyword: str) -> Path:
    for child in root.iterdir():
        if child.is_dir() and keyword.lower() in child.name.lower():
            return child
    raise FileNotFoundError(f"Dossier contenant '{keyword}' introuvable sous {root}")


DONNEES_DIR = _find_dir(PROJECT_ROOT, "01_Donnees")
ARCHIVES_DIR = _find_dir(DONNEES_DIR, "archives")
AUDIO_DIR = _find_dir(PROJECT_ROOT, "04_Audio")
VOIXOFF_DIR = _find_dir(AUDIO_DIR, "voixoff")
BUTS_DIR = _find_dir(SCRIPT_DIR, "buts")
ANALYSE_DIR = _find_dir(SCRIPT_DIR, "analyse")

LOG_FILE = ARCHIVES_DIR / "log.md"
STATE_FILE = VOIXOFF_DIR / ".voiced.json"

SAMPLE_RATE = 24000                                    # Kokoro = 24 kHz

# Jeux de scripts a traiter : (dossier source, motif, suffixe sortie, champ etat)
JOBS = [
    (lambda: BUTS_DIR, "*_script-buts.txt", "_voix-buts.mp3", "buts"),
    (lambda: ANALYSE_DIR, "*_script-analyse.txt", "_voix-analyse.mp3", "analyse"),
]


# --------------------------------------------------------------------------- #
# Journalisation [Bloc5]
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- `{ts}` — [Bloc5] {message}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Journal de suivi — FootFlash\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())


# --------------------------------------------------------------------------- #
# Etat .voiced.json  -> {base_match: {"buts": bool, "analyse": bool}}
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def mark_state(base: str, champ: str) -> None:
    state = load_state()
    entry = state.get(base, {})
    entry[champ] = True
    state[base] = entry
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Parsing + normalisation du texte parle
# --------------------------------------------------------------------------- #
SECTIONS = {"[HOOK]", "[CORPS]", "[OUTRO]"}
RE_PUCE = re.compile(r"^\s*\d+\.\s+")                  # "1. ", "2. " en debut de ligne
RE_MINUTE = re.compile(r"(\d{1,3})'\s*—\s*")          # "88' — " -> "88e minute, "


def normaliser_ligne(ligne: str) -> str:
    ligne = RE_PUCE.sub("", ligne)                    # retire la puce 1./2./3.
    ligne = RE_MINUTE.sub(lambda m: f"{m.group(1)}e minute, ", ligne)
    return ligne.strip()


def extraire_texte(contenu: str) -> str:
    """Renvoie le texte parle : lignes sous les balises, sans '#' ni balises."""
    morceaux: list[str] = []
    dans_section = False
    for raw in contenu.splitlines():
        ligne = raw.strip()
        if not ligne or ligne.startswith("#"):
            continue
        if ligne in SECTIONS:                         # balise -> on entre en lecture
            dans_section = True
            continue
        if ligne.startswith("[") and ligne.endswith("]"):
            dans_section = False                      # balise inconnue -> on coupe
            continue
        if dans_section:
            norm = normaliser_ligne(ligne)
            if norm:
                morceaux.append(norm)
    return "\n".join(morceaux)


# --------------------------------------------------------------------------- #
# Synthese Kokoro (chargee paresseusement : import lourd)
# --------------------------------------------------------------------------- #
_PIPELINES: dict[str, object] = {}


def _get_pipeline(lang_code: str):
    """Instancie (une fois) un KPipeline pour la langue demandee."""
    if lang_code not in _PIPELINES:
        from kokoro import KPipeline  # import paresseux (torch lourd)
        _PIPELINES[lang_code] = KPipeline(lang_code=lang_code)
    return _PIPELINES[lang_code]


def synthetiser(texte: str):
    """Renvoie (audio_numpy, voix_utilisee). Tente le francais, repli anglais."""
    import numpy as np

    essais = [("f", "ff_siwis"), ("a", "af_heart")]   # FR d'abord, sinon EN
    derniere_err: Exception | None = None
    for lang_code, voix in essais:
        try:
            pipeline = _get_pipeline(lang_code)
            segments = [audio for _, _, audio in pipeline(texte, voice=voix)]
            if not segments:
                raise RuntimeError("aucun segment audio produit")
            audio = np.concatenate([np.asarray(s, dtype="float32") for s in segments])
            return audio, voix
        except Exception as exc:                      # G2P manquant, voix absente...
            derniere_err = exc
            log(f"⚠️ Synthese {lang_code}/{voix} echouee ({exc}) — essai suivant.")
    raise RuntimeError(f"Synthese impossible (FR puis EN) : {derniere_err}")


def ecrire_mp3(audio, out_path: Path) -> None:
    """WAV temporaire (soundfile) -> MP3 (ffmpeg)."""
    import soundfile as sf

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name
    try:
        sf.write(wav_path, audio, SAMPLE_RATE)
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-i", wav_path, "-codec:a", "libmp3lame", "-q:a", "2",
               str(out_path)]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg a echoue : {res.stderr.strip()[:200]}")
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


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
        _git("commit", "-m", f"Bloc5: {nb} voix off generee(s)")
        code, out = _git("push")
        log("⬆️ Push reussi (voix off)." if code == 0 else f"⚠️ Echec git push : {out[:200]}")
    except Exception as exc:
        log(f"⚠️ Erreur git push : {exc}")


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    VOIXOFF_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    nouveaux = 0

    for get_dir, motif, suffixe, champ in JOBS:
        for txt_path in sorted(get_dir().glob(motif)):
            # base = nom du match sans le suffixe "_script-buts.txt" / "_script-analyse.txt"
            base = txt_path.name.split("_script-")[0]
            if state.get(base, {}).get(champ):
                continue

            texte = extraire_texte(txt_path.read_text(encoding="utf-8"))
            if not texte:
                log(f"⚠️ Aucun texte exploitable dans {txt_path.name} — ignore.")
                continue

            out_path = VOIXOFF_DIR / f"{base}{suffixe}"
            try:
                audio, voix = synthetiser(texte)
                ecrire_mp3(audio, out_path)
            except Exception as exc:
                log(f"❌ Echec voix off {txt_path.name} : {exc}")
                continue

            mark_state(base, champ)
            log(f"🔊 Voix off ({champ}) : {out_path.name} [voix {voix}].")
            nouveaux += 1

    if nouveaux == 0:
        log("ℹ️ Aucune nouvelle voix off a generer.")
    else:
        log(f"✅ Termine : {nouveaux} voix off generee(s).")
        git_push_changes(nouveaux)
    return 0


if __name__ == "__main__":
    sys.exit(main())
