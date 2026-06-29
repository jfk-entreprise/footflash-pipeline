#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 2 : Telechargement des clips officiels FIFA
============================================================

Pipeline AutoGoal / FootFlash.

Role :
    1. Recevoir la liste des nouveaux JSON de match (ajoutes dans 01_Donnees/matchs/).
    2. Pour chaque match, DECOUVRIR le highlight officiel FIFA via une CASCADE :
         (a) URL explicite dans le JSON  -> champ "url_clip_fifa"
         (b) YouTube Data API            -> recherche restreinte a la chaine @FIFA
         (c) yt-dlp                      -> recherche + filtre chaine officielle FIFA
       On s'arrete a la PREMIERE source dont l'origine officielle est CERTIFIEE.
    3. VERIFIER strictement que la video provient de la chaine officielle FIFA
       (channel_id == UCpcTrCXblq78GZrTUTLWeBw). Conforme a regles-fifa.md.
    4. Telecharger via yt-dlp dans 02_Clips/bruts/ avec la nomenclature FootFlash.
    5. Notifier Telegram (succes / echec / doute).
    6. Journaliser chaque action dans 01_Donnees/archives/log.md  (prefixe [Bloc2]).

Regle de securite : AUCUNE cle n'est lue depuis config.md.
    Tous les secrets proviennent des variables d'environnement (GitHub Secrets) :
        YOUTUBE_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.

En cas de doute (aucune source officielle certifiee) : ON NE TELECHARGE RIEN,
    on stoppe le traitement de ce match et on notifie pour validation manuelle.

Usage :
    python telecharger_clips.py [fichier1.json fichier2.json ...]
    (sans argument : traite tous les *.json de 01_Donnees/matchs/ non encore telecharges)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, parse, request

# --------------------------------------------------------------------------- #
# Chaine officielle FIFA (ancre de verification) — regles-fifa.md
# --------------------------------------------------------------------------- #
OFFICIAL_FIFA_CHANNEL_ID = "UCpcTrCXblq78GZrTUTLWeBw"   # youtube.com/@FIFA
OFFICIAL_FIFA_HANDLE = "FIFA"

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


DONNEES_DIR = _find_dir(PROJECT_ROOT, "01_Donnees")
MATCHS_DIR = _find_dir(DONNEES_DIR, "matchs")
ARCHIVES_DIR = _find_dir(DONNEES_DIR, "archives")
CLIPS_DIR = _find_dir(PROJECT_ROOT, "02_Clips")
BRUTS_DIR = _find_dir(CLIPS_DIR, "bruts")

LOG_FILE = ARCHIVES_DIR / "log.md"
DOWNLOADED_FILE = BRUTS_DIR / ".downloaded.json"      # anti-doublon (matchs deja telecharges)

REQUEST_TIMEOUT = 30


def _cookie_args() -> list[str]:
    """Args yt-dlp pour les cookies YouTube, si fournis (contourne le bot-check CI).

    Le workflow decode le secret YOUTUBE_COOKIES (base64) vers $RUNNER_TEMP/cookies.txt
    et exporte YOUTUBE_COOKIES_FILE. Sans cookies -> [] (comportement inchange).
    """
    path = os.environ.get("YOUTUBE_COOKIES_FILE", "")
    if path and Path(path).is_file():
        return ["--cookies", path]
    return []


# --------------------------------------------------------------------------- #
# Journalisation
# --------------------------------------------------------------------------- #
def log(message: str) -> None:
    """Ajoute une ligne horodatee au journal de suivi (et a la console)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"- `{ts}` — [Bloc2] {message}\n"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_FILE.exists():
        LOG_FILE.write_text("# Journal de suivi — FootFlash\n\n", encoding="utf-8")
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(line)
    print(line.rstrip())


# --------------------------------------------------------------------------- #
# Telegram (non bloquant)
# --------------------------------------------------------------------------- #
def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log("⚠️ TELEGRAM_BOT_TOKEN/CHAT_ID absents de l'environnement — notification ignoree.")
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
    except Exception as exc:
        log(f"⚠️ Echec notification Telegram : {exc}")
        return False


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #
def slugify(name: str) -> str:
    norm = unicodedata.normalize("NFKD", name)
    norm = "".join(c for c in norm if not unicodedata.combining(c))
    slug = "".join(c.lower() if c.isalnum() else "-" for c in norm)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "equipe"


def parse_match(json_path: Path) -> dict:
    """Extrait les infos utiles d'un JSON de match (format Bloc 1)."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    teams = data.get("equipes", {})
    home = teams.get("home", {}).get("name", "")
    away = teams.get("away", {}).get("name", "")
    # date : champ match.date (ISO) sinon prefixe du nom de fichier
    date_iso = (data.get("match", {}).get("date") or "")[:10]
    if not date_iso:
        date_iso = json_path.name[:10]
    return {
        "home": home,
        "away": away,
        "date": date_iso,
        "base": f"{date_iso}_{slugify(home)}-{slugify(away)}",
        "url_clip_fifa": data.get("url_clip_fifa"),   # (a) eventuel champ explicite
        "raw": data,
    }


# --------------------------------------------------------------------------- #
# Verification stricte : la video appartient-elle a la chaine officielle FIFA ?
# --------------------------------------------------------------------------- #
def _video_id(url_or_id: str) -> str | None:
    """Extrait l'identifiant video depuis une URL watch?v= / youtu.be, sinon renvoie tel quel."""
    if not url_or_id:
        return None
    if "watch?v=" in url_or_id:
        return parse.parse_qs(parse.urlparse(url_or_id).query).get("v", [None])[0]
    if "youtu.be/" in url_or_id:
        return url_or_id.rsplit("/", 1)[-1].split("?")[0]
    return url_or_id


def verify_via_youtube_api(video_url: str, api_key: str) -> dict | None:
    """
    Garde-fou central (regles-fifa.md) via l'API YouTube Data (autoritative et
    fiable sur les runners CI). Accepte UNIQUEMENT si la video appartient a la
    chaine OFFICIELLE FIFA : snippet.channelId == OFFICIAL_FIFA_CHANNEL_ID.
    """
    vid = _video_id(video_url)
    if not vid or not api_key:
        return None
    url = "https://www.googleapis.com/youtube/v3/videos?" + parse.urlencode(
        {"key": api_key, "part": "snippet", "id": vid})
    try:
        with request.urlopen(request.Request(url), timeout=REQUEST_TIMEOUT) as resp:
            items = json.loads(resp.read().decode("utf-8")).get("items", [])
    except Exception as exc:
        log(f"ℹ️ Verif API YouTube echouee : {exc}")
        return None
    if not items:
        return None
    channel_id = items[0].get("snippet", {}).get("channelId", "")
    if channel_id == OFFICIAL_FIFA_CHANNEL_ID:
        return {"channel_id": channel_id, "title": items[0]["snippet"].get("title", "")}
    return None


def verify_official_fifa(video_url: str) -> dict | None:
    """
    REPLI hors-CI (si pas de cle API) : verifie via yt-dlp que la video provient
    de la chaine OFFICIELLE FIFA. Standard identique (channelId == FIFA), mais
    fragile sur les runners (YouTube bloque les IP datacenter) — d'ou la priorite
    donnee a verify_via_youtube_api().
    """
    try:
        out = subprocess.run(
            ["yt-dlp", *_cookie_args(), "-J", "--no-warnings", "--no-playlist", "--skip-download", video_url],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            log("ℹ️ Verif yt-dlp indisponible (extraction echouee) — non concluant.")
            return None
        meta = json.loads(out.stdout)
    except Exception:
        return None
    channel_id = meta.get("channel_id") or ""
    handle = (meta.get("uploader_id") or "").lstrip("@").lower()
    uploader_url = (meta.get("channel_url") or meta.get("uploader_url") or "").lower()
    is_official = (
        channel_id == OFFICIAL_FIFA_CHANNEL_ID
        or OFFICIAL_FIFA_CHANNEL_ID.lower() in uploader_url
        or handle == OFFICIAL_FIFA_HANDLE.lower()
        or uploader_url.rstrip("/").endswith(f"@{OFFICIAL_FIFA_HANDLE}".lower())
    )
    return meta if is_official else None


def _looks_like_match(title: str, home: str, away: str) -> bool:
    """Correspondance souple : le titre evoque-t-il bien ce match ?"""
    t = slugify(title)
    return slugify(home) in t and slugify(away) in t


# --------------------------------------------------------------------------- #
# Cascade de decouverte du clip officiel FIFA
# --------------------------------------------------------------------------- #
def discover_via_json(match: dict) -> str | None:
    """(a) URL explicite fournie dans le JSON de match."""
    url = match.get("url_clip_fifa")
    if url:
        log(f"🔗 (a) URL explicite trouvee dans le JSON : {url}")
    return url or None


def discover_via_youtube_api(match: dict) -> str | None:
    """(b) YouTube Data API : recherche restreinte a la chaine @FIFA."""
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        log("ℹ️ (b) YOUTUBE_API_KEY absente — methode API ignoree.")
        return None
    query = f"{match['home']} {match['away']} highlights"
    params = {
        "key": api_key,
        "part": "snippet",
        "channelId": OFFICIAL_FIFA_CHANNEL_ID,   # restriction stricte a la chaine officielle
        "q": query,
        "type": "video",
        "maxResults": 5,
        "order": "relevance",
    }
    url = "https://www.googleapis.com/youtube/v3/search?" + parse.urlencode(params)
    try:
        with request.urlopen(request.Request(url), timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        log(f"ℹ️ (b) Echec recherche YouTube API : {exc}")
        return None
    for item in data.get("items", []):
        vid = item.get("id", {}).get("videoId")
        title = item.get("snippet", {}).get("title", "")
        if vid and _looks_like_match(title, match["home"], match["away"]):
            log(f"🎯 (b) Candidat API @FIFA : « {title} »")
            return f"https://www.youtube.com/watch?v={vid}"
    # repli : meilleur resultat de la chaine officielle meme si titre moins precis
    for item in data.get("items", []):
        vid = item.get("id", {}).get("videoId")
        if vid:
            log(f"🎯 (b) Candidat API @FIFA (repli) : « {item.get('snippet', {}).get('title', '')} »")
            return f"https://www.youtube.com/watch?v={vid}"
    return None


def discover_via_ytdlp_search(match: dict) -> str | None:
    """(c) yt-dlp : recherche large puis filtre sur la chaine officielle FIFA."""
    query = f"ytsearch15:{match['home']} {match['away']} FIFA World Cup 2026 highlights"
    try:
        out = subprocess.run(
            ["yt-dlp", *_cookie_args(), "-J", "--no-warnings", "--flat-playlist", "--skip-download", query],
            capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0:
            log("ℹ️ (c) Recherche yt-dlp sans resultat exploitable.")
            return None
        data = json.loads(out.stdout)
    except Exception as exc:
        log(f"ℹ️ (c) Echec recherche yt-dlp : {exc}")
        return None
    candidates = []
    for entry in data.get("entries", []):
        ch = entry.get("channel_id") or ""
        if ch == OFFICIAL_FIFA_CHANNEL_ID:
            vid = entry.get("id")
            title = entry.get("title", "")
            url = entry.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
            if not url:
                continue
            # priorite aux titres qui collent au match
            score = 0 if _looks_like_match(title, match["home"], match["away"]) else 1
            candidates.append((score, url, title))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    log(f"🎯 (c) Candidat yt-dlp @FIFA : « {candidates[0][2]} »")
    return candidates[0][1]


def find_official_clip(match: dict) -> str | None:
    """
    CASCADE : tente chaque methode dans l'ordre ; la premiere URL dont l'origine
    officielle FIFA est CERTIFIEE est retenue. Sinon None (aucune source sure).
    """
    for label, finder in (
        ("a/JSON", discover_via_json),
        ("b/YouTube API", discover_via_youtube_api),
        ("c/yt-dlp", discover_via_ytdlp_search),
    ):
        try:
            candidate = finder(match)
        except Exception as exc:
            log(f"ℹ️ Methode {label} en erreur : {exc}")
            candidate = None
        if not candidate:
            continue
        # Certification PRIORITAIRE via l'API YouTube (fiable en CI) ; repli yt-dlp
        # UNIQUEMENT si aucune cle API n'est disponible (usage local).
        api_key = os.environ.get("YOUTUBE_API_KEY", "")
        meta = verify_via_youtube_api(candidate, api_key)
        if meta is None and not api_key:
            meta = verify_official_fifa(candidate)
        if meta:
            log(f"✅ Source CERTIFIEE officielle FIFA (channelId) via methode {label}.")
            return candidate
        log(f"⛔ Candidat {label} REJETE : channelId ≠ FIFA ou non vérifiable.")
    return None


# --------------------------------------------------------------------------- #
# Telechargement
# --------------------------------------------------------------------------- #
def download_clip(video_url: str, base_name: str) -> Path | None:
    """Telecharge la video en .mp4 dans 02_Clips/bruts/ selon la nomenclature FootFlash."""
    BRUTS_DIR.mkdir(parents=True, exist_ok=True)
    out_template = str(BRUTS_DIR / f"{base_name}_brut.%(ext)s")
    cmd = [
        "yt-dlp",
        *_cookie_args(),
        "--extractor-args", "youtube:player_client=default,android",
        "-f", "bv*+ba/b",                 # meilleur video+audio, sinon meilleur fichier unique
        "-S", "ext:mp4:m4a,res,br",        # PREFERE mp4/m4a (sans l'exiger), puis resolution/bitrate
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--no-warnings",
        "-o", out_template,
        video_url,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as exc:
        log(f"❌ Exception yt-dlp pendant le telechargement : {exc}")
        return None
    if res.returncode != 0:
        log(f"❌ yt-dlp a echoue (code {res.returncode}) : {res.stderr.strip()[:300]}")
        return None
    matches = sorted(BRUTS_DIR.glob(f"{base_name}_brut.*"))
    return matches[0] if matches else None


# --------------------------------------------------------------------------- #
# Anti-doublon
# --------------------------------------------------------------------------- #
def load_downloaded() -> set[str]:
    if DOWNLOADED_FILE.exists():
        try:
            return set(json.loads(DOWNLOADED_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def save_downloaded(names: set[str]) -> None:
    DOWNLOADED_FILE.write_text(json.dumps(sorted(names)), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Traitement d'un match
# --------------------------------------------------------------------------- #
def process_json(json_path: Path, done: set[str]) -> None:
    match = parse_match(json_path)
    base = match["base"]
    label = f"{match['home']} – {match['away']}"

    if base in done:
        log(f"⏭️ Clip deja telecharge pour {base} — ignore.")
        return

    log(f"🔎 Recherche du highlight officiel FIFA : {label} ({base})")
    clip_url = find_official_clip(match)

    if not clip_url:
        # Regle absolue regles-fifa.md : en cas de doute, NE RIEN telecharger + alerter.
        log(f"🛑 Aucun clip OFFICIEL FIFA certifie pour {label} — arret + validation manuelle requise.")
        send_telegram(
            "🛑 <b>FootFlash — clip FIFA introuvable</b>\n"
            f"Match : <b>{label}</b>\n"
            f"Aucune source officielle FIFA certifiee (cascade a/b/c epuisee).\n"
            "Aucun telechargement effectue — validation manuelle requise."
        )
        return

    clip_path = download_clip(clip_url, base)
    if not clip_path:
        log(f"❌ Echec telechargement pour {label}.")
        send_telegram(
            "⚠️ <b>FootFlash — echec telechargement</b>\n"
            f"Match : <b>{label}</b>\nSource : {clip_url}"
        )
        return

    done.add(base)
    save_downloaded(done)
    size_mb = clip_path.stat().st_size / (1024 * 1024)
    log(f"💾 Clip telecharge : {clip_path.name} ({size_mb:.1f} Mo) — source officielle FIFA.")
    send_telegram(
        "🎬 <b>FootFlash — clip FIFA telecharge</b>\n"
        f"Match : <b>{label}</b>\n"
        f"📁 <code>{clip_path.name}</code> ({size_mb:.1f} Mo)\n"
        f"Source officielle FIFA verifiee ✔️\nPret pour l'etape d'analyse/montage."
    )


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main(argv: list[str]) -> int:
    # Liste des JSON a traiter : args explicites, sinon tout le dossier matchs.
    if argv:
        targets = [Path(a) for a in argv if a.lower().endswith(".json")]
        targets = [p if p.is_absolute() else (MATCHS_DIR / p.name) for p in targets]
    else:
        targets = sorted(MATCHS_DIR.glob("*.json"))

    targets = [p for p in targets if p.exists() and not p.name.startswith(".")]
    if not targets:
        log("ℹ️ Aucun nouveau JSON de match a traiter.")
        return 0

    log(f"▶️ Bloc 2 demarre — {len(targets)} match(s) a traiter.")
    done = load_downloaded()
    for json_path in targets:
        try:
            process_json(json_path, done)
        except Exception as exc:
            log(f"❌ Erreur inattendue sur {json_path.name} : {exc}")
    log("✅ Bloc 2 termine.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
