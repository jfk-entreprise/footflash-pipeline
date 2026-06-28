#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 7 (1/2) : Notification de validation (Coupe du Monde 2026)
===========================================================================

Pipeline AutoGoal / FootFlash.   Chainage : Bloc1 -> ... -> Bloc6 -> [Bloc7].

Role :
    1. Reperer les matchs dont les 2 videos finales sont pretes
       (05_Montage/rendus/{base}_video-buts.mp4 ET _video-analyse.mp4).
    2. Envoyer une notification Telegram de validation : nom du match + score,
       liens GitHub (page blob) vers les 2 MP4, et un clavier inline
       "✅ Publier" / "❌ Rejeter" (en plus de la reponse texte OUI / NON).
    3. Enregistrer l'attente de validation dans 05_Montage/validation_state.json
       (fichier SUIVI par git : il doit persister entre les runs du watcher).

La reponse (OUI/NON ou bouton) est traitee par l'autre moitie du Bloc 7 :
    03_Scripts/watch_validation.py (workflow cron toutes les 5 min).

Anti-doublon : un match deja notifie n'est pas renotifie.
Journalisation : [Bloc7].   Mode test : DRY_RUN=1 (n'appelle pas l'API).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
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


DONNEES_DIR = _find_dir(PROJECT_ROOT, "01_Donnees")
ARCHIVES_DIR = _find_dir(DONNEES_DIR, "archives")
MATCHS_DIR = _find_dir(DONNEES_DIR, "matchs")
MONTAGE_DIR = _find_dir(PROJECT_ROOT, "05_Montage")
RENDUS_DIR = _find_dir(MONTAGE_DIR, "rendus")
BUTS_DIR = _find_dir(SCRIPT_DIR, "buts")

LOG_FILE = ARCHIVES_DIR / "log.md"
# IMPORTANT : fichier SUIVI par git (persiste entre les runs cron du watcher).
STATE_FILE = MONTAGE_DIR / "validation_state.json"

DRY_RUN = os.environ.get("DRY_RUN") == "1"
REQUEST_TIMEOUT = 30


# --------------------------------------------------------------------------- #
# Journalisation [Bloc7]
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- `{ts}` — [Bloc7] {message}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Journal de suivi — FootFlash\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())


# --------------------------------------------------------------------------- #
# Etat de validation (fichier suivi par git)
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            data.setdefault("telegram_offset", 0)
            data.setdefault("matchs", {})
            return data
        except Exception:
            pass
    return {"telegram_offset": 0, "matchs": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def tg_call(method: str, payload: dict) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if DRY_RUN:
        print(f"[DRY_RUN] Telegram.{method} <- {json.dumps(payload, ensure_ascii=False)}")
        return {"ok": True, "result": {"message_id": 1}}
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method="POST",
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# Titre / score du match (en-tete du script Bloc 4, repli _moments.json)
# --------------------------------------------------------------------------- #
def titre_match(base: str) -> str:
    txt = BUTS_DIR / f"{base}_script-buts.txt"
    if txt.exists():
        for raw in txt.read_text(encoding="utf-8").splitlines():
            if raw.startswith("#") and " — " in raw:
                parts = [p.strip() for p in raw.lstrip("# ").split(" — ")]
                if len(parts) >= 2 and parts[1]:
                    return parts[1]                       # ex. "France 3–2 Espagne"
    moments = MATCHS_DIR / f"{base}_moments.json"
    if moments.exists():
        try:
            m = json.loads(moments.read_text(encoding="utf-8"))["match"]
            sc = m["score_final"]
            return f"{m['domicile']} {sc['home']}-{sc['away']} {m['exterieur']}"
        except Exception:
            pass
    return base.split("_", 1)[-1].replace("-", " ").title()


# --------------------------------------------------------------------------- #
# Liens GitHub (page blob)
# --------------------------------------------------------------------------- #
def lien_blob(rel_path: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "jfk-entreprise/footflash-pipeline")
    branche = os.environ.get("GITHUB_REF_NAME", "main")
    chemin = parse.quote(rel_path)
    return f"https://github.com/{repo}/blob/{branche}/{chemin}"


# --------------------------------------------------------------------------- #
# Construction + envoi de la notification
# --------------------------------------------------------------------------- #
def notifier(base: str, state: dict) -> bool:
    titre = titre_match(base)
    url_buts = lien_blob(f"05_Montage/rendus/{base}_video-buts.mp4")
    url_analyse = lien_blob(f"05_Montage/rendus/{base}_video-analyse.mp4")

    texte = (
        "⚽ <b>FootFlash — Match terminé</b>\n"
        f"📺 {titre}\n"
        "🎬 2 vidéos prêtes pour validation :\n"
        f"→ Vidéo Buts : <a href=\"{url_buts}\">ouvrir</a>\n"
        f"→ Vidéo Analyse : <a href=\"{url_analyse}\">ouvrir</a>\n"
        "✅ Pour publier : répondez OUI\n"
        "❌ Pour rejeter : répondez NON"
    )
    clavier = {"inline_keyboard": [[
        {"text": "✅ Publier", "callback_data": f"publish:{base}"},
        {"text": "❌ Rejeter", "callback_data": f"reject:{base}"},
    ]]}
    payload = {
        "chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "text": texte,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": clavier,
    }

    try:
        res = tg_call("sendMessage", payload)
    except (error.URLError, error.HTTPError, Exception) as exc:
        log(f"❌ Echec notification Telegram pour {base} : {exc}")
        return False
    if not res.get("ok"):
        log(f"❌ Telegram a refuse la notification pour {base} : {res}")
        return False

    message_id = (res.get("result") or {}).get("message_id")
    state["matchs"][base] = {
        "status": "pending",
        "message_id": message_id,
        "titre": titre,
        "notifie_le": datetime.now(timezone.utc).isoformat(),
        "videos": {
            "buts": f"05_Montage/rendus/{base}_video-buts.mp4",
            "analyse": f"05_Montage/rendus/{base}_video-analyse.mp4",
        },
    }
    log(f"📨 Notification de validation envoyée : {titre} ({base}).")
    return True


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    state = load_state()
    nouveaux = 0

    for buts_mp4 in sorted(RENDUS_DIR.glob("*_video-buts.mp4")):
        base = buts_mp4.name.replace("_video-buts.mp4", "")
        analyse_mp4 = RENDUS_DIR / f"{base}_video-analyse.mp4"
        if not analyse_mp4.exists():
            log(f"⏳ {base} — vidéo analyse manquante, notification reportée.")
            continue
        if base in state["matchs"]:                      # deja notifie (anti-doublon)
            continue
        if notifier(base, state):
            save_state(state)
            nouveaux += 1

    if nouveaux == 0:
        log("ℹ️ Aucune nouvelle paire de vidéos à soumettre à validation.")
    else:
        log(f"✅ Terminé : {nouveaux} match(s) en attente de validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
