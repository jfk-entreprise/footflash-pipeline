#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 6 : Montage video final (FFmpeg) (Coupe du Monde 2026)
=======================================================================

Pipeline AutoGoal / FootFlash.   Chainage : Bloc1 -> ... -> Bloc5 -> [Bloc6].

Role : assembler, pour chaque match et chaque variante (buts / analyse), une
video verticale 9:16 prete pour TikTok / YouTube Shorts :

    - clip FIFA en fond (cadrage 9:16 par FOND FLOU, action centree) ;
    - voix off par-dessus (audio du clip a 20 %, musique de fond a 20 %) ;
    - sous-titres MOT-A-MOT facon TikTok (blanc, contour noir epais, grande taille)
      generes en .ass dans 05_Montage/timelines/ (timing estime au prorata) ;
    - intro 2 s : titre du match en texte anime (fondu) ;
    - encodage H.264 optimise mobile.

Entrees (appariees par base = AAAA-MM-JJ_eq1-eq2) :
    02_Clips/bruts/{base}_brut.mp4
    04_Audio/voixoff/{base}_voix-{buts|analyse}.mp3
    03_Scripts/{buts|analyse}/{base}_script-{buts|analyse}.txt   (sous-titres)
    04_Audio/musique/*.mp3   (1er fichier, optionnel)

Sorties :
    05_Montage/rendus/{base}_video-{buts|analyse}.mp4

Outil : FFmpeg uniquement (Python ne fait qu'orchestrer).
Anti-doublon : etat .rendered.json.   Journalisation : [Bloc6].
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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
CLIPS_DIR = _find_dir(PROJECT_ROOT, "02_Clips")
BRUTS_DIR = _find_dir(CLIPS_DIR, "bruts")
AUDIO_DIR = _find_dir(PROJECT_ROOT, "04_Audio")
VOIXOFF_DIR = _find_dir(AUDIO_DIR, "voixoff")
MUSIQUE_DIR = _find_dir(AUDIO_DIR, "musique")
MONTAGE_DIR = _find_dir(PROJECT_ROOT, "05_Montage")
RENDUS_DIR = _find_dir(MONTAGE_DIR, "rendus")
TIMELINES_DIR = _find_dir(MONTAGE_DIR, "timelines")
BUTS_DIR = _find_dir(SCRIPT_DIR, "buts")
ANALYSE_DIR = _find_dir(SCRIPT_DIR, "analyse")

LOG_FILE = ARCHIVES_DIR / "log.md"
STATE_FILE = RENDUS_DIR / ".rendered.json"

# --------------------------------------------------------------------------- #
# Parametres video / TikTok
# --------------------------------------------------------------------------- #
W, H = 1080, 1920                                     # 9:16
FPS = 30
INTRO = 2.0                                           # secondes d'intro
SUB_FONTSIZE = 90
TITLE_FONTSIZE = 78

# Polices DejaVu (installees via apt dans le workflow)
FONTFILE = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONTSDIR = "/usr/share/fonts/truetype/dejavu"

# Variantes a produire : (dossier scripts, motif, champ etat, suffixes)
JOBS = [
    (lambda: BUTS_DIR, "*_script-buts.txt", "buts", "_voix-buts.mp3", "_video-buts.mp4"),
    (lambda: ANALYSE_DIR, "*_script-analyse.txt", "analyse", "_voix-analyse.mp3", "_video-analyse.mp4"),
]


# --------------------------------------------------------------------------- #
# Journalisation [Bloc6]
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- `{ts}` — [Bloc6] {message}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Journal de suivi — FootFlash\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())


# --------------------------------------------------------------------------- #
# Etat .rendered.json -> {base: {"buts": bool, "analyse": bool}}
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
# Texte parle (identique au Bloc 5 -> sous-titres = mots prononces)
# --------------------------------------------------------------------------- #
SECTIONS = {"[HOOK]", "[CORPS]", "[OUTRO]"}
RE_PUCE = re.compile(r"^\s*\d+\.\s+")
RE_MINUTE = re.compile(r"(\d{1,3})'\s*—\s*")


def _normaliser_ligne(ligne: str) -> str:
    ligne = RE_PUCE.sub("", ligne)
    ligne = RE_MINUTE.sub(lambda m: f"{m.group(1)}e minute, ", ligne)
    return ligne.strip()


def extraire_texte(contenu: str) -> str:
    morceaux, dans = [], False
    for raw in contenu.splitlines():
        l = raw.strip()
        if not l or l.startswith("#"):
            continue
        if l in SECTIONS:
            dans = True
            continue
        if l.startswith("[") and l.endswith("]"):
            dans = False
            continue
        if dans:
            n = _normaliser_ligne(l)
            if n:
                morceaux.append(n)
    return " ".join(morceaux)


def parse_titre(contenu: str, base: str) -> str:
    """Titre d'intro : extrait de l'en-tete '# Script ... — <TITRE> — <date>'."""
    for raw in contenu.splitlines():
        if raw.startswith("#") and " — " in raw:
            parts = [p.strip() for p in raw.lstrip("# ").split(" — ")]
            if len(parts) >= 2 and parts[1]:
                return parts[1]               # ex. "France 3–2 Espagne"
    # Repli : derive du nom de fichier "2026-07-14_france-espagne"
    try:
        equipes = base.split("_", 1)[1].replace("-", " ").upper()
        return equipes
    except Exception:
        return base


# --------------------------------------------------------------------------- #
# FFprobe
# --------------------------------------------------------------------------- #
def ffprobe_duree(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def a_piste_audio(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(out.stdout.strip())


# --------------------------------------------------------------------------- #
# Sous-titres ASS (mot-a-mot, timing estime au prorata de la longueur des mots)
# --------------------------------------------------------------------------- #
def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs = 0
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_echappe(mot: str) -> str:
    return mot.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def construire_ass(texte: str, voix_dur: float, ass_path: Path,
                   offset: float = INTRO) -> int:
    """Ecrit un .ass mot-a-mot. Retourne le nombre de mots."""
    mots = texte.split()
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: TikTok,DejaVu Sans,{SUB_FONTSIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,6,2,2,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lignes = [header]
    if mots and voix_dur > 0:
        poids = [len(m) + 1 for m in mots]            # +1 = espace, evite poids nul
        total = sum(poids)
        curseur = offset
        for mot, p in zip(mots, poids):
            part = voix_dur * (p / total)
            debut, fin = curseur, curseur + part
            curseur = fin
            lignes.append(
                f"Dialogue: 0,{_ass_time(debut)},{_ass_time(fin)},TikTok,,0,0,0,,"
                f"{_ass_echappe(mot)}"
            )
    ass_path.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return len(mots)


# --------------------------------------------------------------------------- #
# Construction et execution de la commande FFmpeg
# --------------------------------------------------------------------------- #
def _esc_filter_path(p: str) -> str:
    """Echappe un chemin pour usage dans un filtre (subtitles=...)."""
    return p.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def rendre(base: str, clip: Path, voix: Path, titre: str,
           ass_path: Path, musique: Path | None, out_path: Path) -> None:
    voix_dur = ffprobe_duree(voix)
    if voix_dur <= 0:
        raise RuntimeError("duree de la voix off nulle")
    total = INTRO + voix_dur
    clip_audio = a_piste_audio(clip)

    # --- Entrees (clip et musique boucles pour couvrir toute la duree) --- #
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-stream_loop", "-1", "-i", str(clip),
           "-i", str(voix)]
    if musique is not None:
        cmd += ["-stream_loop", "-1", "-i", str(musique)]

    # --- Filtre video : fond flou 9:16 + clip centre + titre + sous-titres --- #
    title_txt = titre.replace("\\", "").replace(":", r"\:").replace("'", r"’")
    alpha = ("if(lt(t,0.4),t/0.4,if(lt(t,1.6),1,if(lt(t,2),(2-t)/0.4,0)))")
    fontfile = FONTFILE if Path(FONTFILE).exists() else None
    draw = (
        "drawtext=" + (f"fontfile='{fontfile}':" if fontfile else "")
        + f"text='{title_txt}':fontcolor=white:fontsize={TITLE_FONTSIZE}"
        + ":borderw=8:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2"
        + f":enable='lt(t,{INTRO})':alpha='{alpha}'"
    )
    vfilters = [
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur=20:2,setsar=1[bg]",
        f"[0:v]scale={W}:-2:force_original_aspect_ratio=decrease[fg]",
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[bgfg]",
        f"[bgfg]{draw}[titled]",
        f"[titled]subtitles='{_esc_filter_path(str(ass_path))}'"
        + (f":fontsdir='{_esc_filter_path(FONTSDIR)}'" if Path(FONTSDIR).exists() else "")
        + "[vout]",
    ]

    # --- Filtre audio : voix (delai intro) + clip 20% + musique 20% --- #
    afilters = ["[1:a]adelay={d}|{d},volume=1.0[voix]".format(d=int(INTRO * 1000))]
    mix_inputs = ["[voix]"]
    if clip_audio:
        afilters.append("[0:a]volume=0.2[ca]")
        mix_inputs.append("[ca]")
    if musique is not None:
        afilters.append("[2:a]volume=0.2[mus]")
        mix_inputs.append("[mus]")
    if len(mix_inputs) == 1:
        afilters.append("[voix]anull[aout]")
    else:
        afilters.append(
            "".join(mix_inputs)
            + f"amix=inputs={len(mix_inputs)}:duration=first:normalize=0[aout]"
        )

    filter_complex = ";".join(vfilters + afilters)
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-t", f"{total:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23",
        "-preset", "veryfast", "-profile:v", "high", "-level", "4.0",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(out_path),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg a echoue : {res.stderr.strip()[-500:]}")


# --------------------------------------------------------------------------- #
# Musique de fond (1er .mp3, optionnel)
# --------------------------------------------------------------------------- #
def trouver_musique() -> Path | None:
    fichiers = sorted(MUSIQUE_DIR.glob("*.mp3"))
    return fichiers[0] if fichiers else None


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
        _git("commit", "-m", f"Bloc6: {nb} video(s) montee(s)")
        code, out = _git("push")
        log("⬆️ Push reussi (videos)." if code == 0 else f"⚠️ Echec git push : {out[:200]}")
    except Exception as exc:
        log(f"⚠️ Erreur git push : {exc}")


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    RENDUS_DIR.mkdir(parents=True, exist_ok=True)
    TIMELINES_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    musique = trouver_musique()
    if musique is None:
        log("ℹ️ Aucune musique dans 04_Audio/musique/ — montage sans musique de fond.")
    nouveaux = 0

    for get_dir, motif, champ, voix_suf, video_suf in JOBS:
        for txt_path in sorted(get_dir().glob(motif)):
            base = txt_path.name.split("_script-")[0]
            if state.get(base, {}).get(champ):
                continue

            clip = BRUTS_DIR / f"{base}_brut.mp4"
            voix = VOIXOFF_DIR / f"{base}{voix_suf}"
            manquants = [p.name for p in (clip, voix) if not p.exists()]
            if manquants:
                log(f"⏳ {base} ({champ}) — pieces manquantes {manquants}, montage reporte.")
                continue

            contenu = txt_path.read_text(encoding="utf-8")
            texte = extraire_texte(contenu)
            titre = parse_titre(contenu, base)
            voix_dur = ffprobe_duree(voix)

            ass_path = TIMELINES_DIR / f"{base}_{champ}.ass"
            nb_mots = construire_ass(texte, voix_dur, ass_path)

            out_path = RENDUS_DIR / f"{base}{video_suf}"
            try:
                rendre(base, clip, voix, titre, ass_path, musique, out_path)
            except Exception as exc:
                log(f"❌ Echec montage {base} ({champ}) : {exc}")
                continue

            mark_state(base, champ)
            log(f"🎬 Video montee ({champ}) : {out_path.name} "
                f"[{nb_mots} mots, ~{INTRO + voix_dur:.0f}s, 9:16].")
            nouveaux += 1

    if nouveaux == 0:
        log("ℹ️ Aucune nouvelle video a monter.")
    else:
        log(f"✅ Termine : {nouveaux} video(s) montee(s).")
        git_push_changes(nouveaux)
    return 0


if __name__ == "__main__":
    sys.exit(main())
