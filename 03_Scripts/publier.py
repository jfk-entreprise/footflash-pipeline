#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 8 : Publication TikTok + YouTube Shorts (Coupe du Monde 2026)
==============================================================================

Pipeline AutoGoal / FootFlash.   Declenche par le watcher du Bloc 7 (validation).

Role : pour un match VALIDE (validation_state.json -> status "validated"),
publier les 2 videos finales sur TikTok ET YouTube Shorts, puis :
    - generer un SEO automatique (titre, description, hashtags) par variante ;
    - notifier Telegram (publie / liens) ;
    - passer le match a status "published" (avec IDs/URLs par plateforme) ;
    - deplacer les 2 MP4 vers 06_Archives/publie/.

CONFIDENTIALITE : uploads en prive/unlisted par defaut (sandbox / app non auditee).

MODE :
    - Reel des que les refresh tokens OAuth sont presents (variables d'env).
    - Sinon (ou DRY_RUN=1) : repli SIMULATION — toute la chaine s'execute
      (SEO, notif, etat, archivage) sauf l'appel d'upload, logge [SIMULATION].

Dependance : requests (installee par le workflow).   Journalisation : [Bloc8].
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:                                   # absent en local : mode simulation force
    requests = None

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
ARCH_PUBLIE_DIR = _find_dir(_find_dir(PROJECT_ROOT, "06_Archives"), "publie")

LOG_FILE = ARCHIVES_DIR / "log.md"
STATE_FILE = MONTAGE_DIR / "validation_state.json"

# --------------------------------------------------------------------------- #
# Parametres
# --------------------------------------------------------------------------- #
YT_PRIVACY = os.environ.get("PUBLISH_PRIVACY_YT", "unlisted")     # non listé par defaut
TIKTOK_PRIVACY = os.environ.get("PUBLISH_PRIVACY_TIKTOK", "SELF_ONLY")  # prive par defaut
YT_CATEGORY_SPORT = "17"
FORCE_DRY = os.environ.get("DRY_RUN") == "1"
HTTP_TIMEOUT = 120

VARIANTES = ["buts", "analyse"]


# --------------------------------------------------------------------------- #
# Journalisation [Bloc8]
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- `{ts}` — [Bloc8] {message}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Journal de suivi — FootFlash\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())


# --------------------------------------------------------------------------- #
# Etat
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
# Telegram (non bloquant)
# --------------------------------------------------------------------------- #
def telegram(text: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id or requests is None or FORCE_DRY:
        print(f"[notif] {text}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        log(f"⚠️ Notification Telegram echouee : {exc}")


# --------------------------------------------------------------------------- #
# SEO automatique
# --------------------------------------------------------------------------- #
def _slug_hashtag(nom: str) -> str:
    norm = unicodedata.normalize("NFKD", nom)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    return "#" + re.sub(r"[^0-9A-Za-z]", "", norm)


def _equipes(titre: str, base: str) -> tuple[str, str]:
    m = re.match(r"\s*(.+?)\s+\d+\s*[–-]\s*\d+\s+(.+?)\s*$", titre)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    try:
        eq = base.split("_", 1)[1].split("-")
        return eq[0].title(), eq[-1].title()
    except Exception:
        return "Equipe 1", "Equipe 2"


def _scenario(base: str) -> str:
    f = MATCHS_DIR / f"{base}_moments.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("scenario", "")
        except Exception:
            pass
    return ""


def build_seo(variante: str, titre: str, base: str) -> dict:
    home, away = _equipes(titre, base)
    scenario = _scenario(base)
    base_tags = ["#CDM2026", "#CoupeDuMonde2026", "#football", "#foot", "#shorts",
                 _slug_hashtag(home), _slug_hashtag(away)]
    if variante == "buts":
        titre_video = f"{titre} : TOUS LES BUTS ⚽ | Coupe du Monde 2026 #shorts"
        tags = base_tags + ["#buts", "#highlights", "#goals"]
        accroche = "Tous les buts du match, commentés."
    else:
        titre_video = f"{titre} : L'ANALYSE TACTIQUE 🎯 | CDM 2026 #shorts"
        tags = base_tags + ["#analyse", "#tactique", "#decryptage"]
        accroche = "Le décryptage tactique du match."

    titre_video = titre_video[:100]                   # limite YouTube
    description = (
        f"{accroche}\n"
        + (f"{scenario}\n" if scenario else "")
        + "\nClips officiels FIFA — analyse éditoriale FootFlash.\n\n"
        + " ".join(tags)
    )
    # liste de tags YouTube (sans le '#')
    yt_tags = [t.lstrip("#") for t in tags]
    caption = f"{titre_video}\n\n" + " ".join(tags)   # caption TikTok (<=2200)
    return {"title": titre_video, "description": description,
            "tags": yt_tags, "caption": caption[:2200]}


# --------------------------------------------------------------------------- #
# Disponibilite des plateformes (tokens OAuth)
# --------------------------------------------------------------------------- #
def youtube_ready() -> bool:
    return bool(requests) and all(os.environ.get(k) for k in
                                  ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET",
                                   "YOUTUBE_REFRESH_TOKEN")) and not FORCE_DRY


def tiktok_ready() -> bool:
    key = os.environ.get("TIKTOK_SANDBOX_CLIENT_KEY") or os.environ.get("TIKTOK_CLIENT_KEY")
    secret = os.environ.get("TIKTOK_SANDBOX_CLIENT_SECRET") or os.environ.get("TIKTOK_CLIENT_SECRET")
    return bool(requests) and bool(key and secret and os.environ.get("TIKTOK_REFRESH_TOKEN")) and not FORCE_DRY


# --------------------------------------------------------------------------- #
# Upload YouTube (resumable)
# --------------------------------------------------------------------------- #
def _youtube_token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()["access_token"]


def upload_youtube(video: Path, seo: dict) -> dict:
    token = _youtube_token()
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={"Authorization": f"Bearer {token}",
                 "X-Upload-Content-Type": "video/mp4",
                 "Content-Type": "application/json; charset=UTF-8"},
        json={"snippet": {"title": seo["title"], "description": seo["description"],
                          "tags": seo["tags"], "categoryId": YT_CATEGORY_SPORT},
              "status": {"privacyStatus": YT_PRIVACY, "selfDeclaredMadeForKids": False}},
        timeout=HTTP_TIMEOUT,
    )
    init.raise_for_status()
    upload_url = init.headers["Location"]
    put = requests.put(upload_url, headers={"Content-Type": "video/mp4"},
                       data=video.read_bytes(), timeout=HTTP_TIMEOUT * 5)
    put.raise_for_status()
    vid = put.json()["id"]
    return {"id": vid, "url": f"https://youtu.be/{vid}", "privacy": YT_PRIVACY}


# --------------------------------------------------------------------------- #
# Upload TikTok (Content Posting API - FILE_UPLOAD)
# --------------------------------------------------------------------------- #
def _tiktok_token() -> str:
    key = os.environ.get("TIKTOK_SANDBOX_CLIENT_KEY") or os.environ["TIKTOK_CLIENT_KEY"]
    secret = os.environ.get("TIKTOK_SANDBOX_CLIENT_SECRET") or os.environ["TIKTOK_CLIENT_SECRET"]
    r = requests.post("https://open.tiktokapis.com/v2/oauth/token/", data={
        "client_key": key, "client_secret": secret,
        "grant_type": "refresh_token",
        "refresh_token": os.environ["TIKTOK_REFRESH_TOKEN"],
    }, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()["access_token"]


def upload_tiktok(video: Path, seo: dict) -> dict:
    token = _tiktok_token()
    size = video.stat().st_size
    init = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=UTF-8"},
        json={"post_info": {"title": seo["caption"], "privacy_level": TIKTOK_PRIVACY,
                            "disable_comment": False, "disable_duet": False},
              "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                              "chunk_size": size, "total_chunk_count": 1}},
        timeout=HTTP_TIMEOUT,
    )
    init.raise_for_status()
    data = init.json()["data"]
    publish_id, upload_url = data["publish_id"], data["upload_url"]
    put = requests.put(upload_url,
                       headers={"Content-Range": f"bytes 0-{size - 1}/{size}",
                                "Content-Type": "video/mp4"},
                       data=video.read_bytes(), timeout=HTTP_TIMEOUT * 5)
    put.raise_for_status()
    return {"id": publish_id, "url": f"tiktok://publish/{publish_id}",
            "privacy": TIKTOK_PRIVACY}


# --------------------------------------------------------------------------- #
# Publication d'une variante sur une plateforme (avec repli simulation)
# --------------------------------------------------------------------------- #
def publier_plateforme(plateforme: str, ready: bool, fn, video: Path, seo: dict) -> dict:
    if not ready:
        log(f"🧪 [SIMULATION] {plateforme} ← {video.name} "
            f"(titre: « {seo['title']} ») — pas de token OAuth, upload simulé.")
        return {"id": f"SIMULATED-{plateforme}", "url": "(simulation)",
                "simule": True, "privacy": YT_PRIVACY if plateforme == "youtube" else TIKTOK_PRIVACY}
    try:
        res = fn(video, seo)
        res["simule"] = False
        log(f"📤 {plateforme} OK ← {video.name} → {res['url']} ({res['privacy']}).")
        return res
    except Exception as exc:
        log(f"❌ {plateforme} a echoue ← {video.name} : {exc}")
        return {"erreur": str(exc)[:200]}


# --------------------------------------------------------------------------- #
# Traitement d'un match valide
# --------------------------------------------------------------------------- #
def publier_match(base: str, entry: dict) -> bool:
    titre = entry.get("titre", base)
    yt_ok, tt_ok = youtube_ready(), tiktok_ready()
    publication = entry.get("publication", {})
    tout_ok = True

    for variante in VARIANTES:
        video = PROJECT_ROOT / entry.get("videos", {}).get(
            variante, f"05_Montage/rendus/{base}_video-{variante}.mp4")
        deja = publication.get(variante, {})
        if deja.get("youtube", {}).get("id") and deja.get("tiktok", {}).get("id"):
            continue                                  # variante deja publiee
        if not video.exists():
            log(f"❌ Vidéo introuvable : {video.name} — publication reportée.")
            tout_ok = False
            continue

        seo = build_seo(variante, titre, base)
        res_yt = deja.get("youtube") or publier_plateforme("youtube", yt_ok, upload_youtube, video, seo)
        res_tt = deja.get("tiktok") or publier_plateforme("tiktok", tt_ok, upload_tiktok, video, seo)
        publication[variante] = {"youtube": res_yt, "tiktok": res_tt, "seo_titre": seo["title"]}

        if "erreur" in res_yt or "erreur" in res_tt:
            tout_ok = False

    entry["publication"] = publication
    return tout_ok


def archiver(base: str, entry: dict) -> None:
    for variante in VARIANTES:
        src = PROJECT_ROOT / entry.get("videos", {}).get(
            variante, f"05_Montage/rendus/{base}_video-{variante}.mp4")
        if src.exists():
            dest = ARCH_PUBLIE_DIR / src.name
            shutil.move(str(src), str(dest))
            log(f"📦 Archivé : {src.name} → 06_Archives/publie/")


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    if requests is None:
        log("ℹ️ Module 'requests' absent — exécution en mode SIMULATION intégral.")

    state = load_state()
    cible = os.environ.get("BASE", "").strip()        # input du workflow (-f base=...)

    candidats = []
    if cible:
        if cible in state["matchs"]:
            candidats = [cible]
        else:
            log(f"⚠️ base '{cible}' absente de validation_state.json — rien à publier.")
            return 0
    else:
        candidats = [b for b, e in state["matchs"].items() if e.get("status") == "validated"]

    if not candidats:
        log("ℹ️ Aucun match validé en attente de publication.")
        return 0

    publies = 0
    for base in candidats:
        entry = state["matchs"][base]
        if entry.get("status") == "published":
            log(f"ℹ️ {base} déjà publié — ignoré.")
            continue
        if entry.get("status") != "validated":
            log(f"⏭️ {base} status='{entry.get('status')}' (≠ validated) — ignoré.")
            continue

        log(f"🚀 Publication de « {entry.get('titre', base)} » ({base})…")
        ok = publier_match(base, entry)
        save_state(state)

        if not ok:
            log(f"⚠️ Publication partielle pour {base} — sera retentée au prochain run.")
            telegram(f"⚠️ <b>FootFlash</b> — publication partielle de {entry.get('titre', base)} "
                     f"(voir log). Nouvelle tentative au prochain passage.")
            continue

        # Succes (reel ou simule) : archivage + etat + notif
        archiver(base, entry)
        entry["status"] = "published"
        entry["publie_le"] = datetime.now().isoformat()
        save_state(state)

        pub = entry["publication"]
        simule = any(v[p].get("simule") for v in pub.values() for p in ("youtube", "tiktok"))
        tag = " [SIMULATION]" if simule else ""
        liens = []
        for v in VARIANTES:
            yt = pub[v]["youtube"].get("url", "?")
            liens.append(f"{v.capitalize()} — YT: {yt} · TikTok: {pub[v]['tiktok'].get('id','?')}")
        telegram(
            f"✅ <b>FootFlash — Publié{tag}</b>\n"
            f"📺 {entry.get('titre', base)}\n"
            + "\n".join(liens)
            + f"\n🔒 Confidentialité : YouTube {YT_PRIVACY} / TikTok {TIKTOK_PRIVACY}."
        )
        log(f"✅ Publié{tag} : {entry.get('titre', base)} ({base}).")
        publies += 1

    log(f"✅ Terminé : {publies} match(s) publié(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
