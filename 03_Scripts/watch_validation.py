#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Bloc 7 (2/2) : Watcher de validation (Coupe du Monde 2026)
======================================================================

Pipeline AutoGoal / FootFlash.   Workflow cron (toutes les 5 min).

Role :
    1. Lire les reponses Telegram via getUpdates (offset persistant dans
       05_Montage/validation_state.json, fichier SUIVI par git).
    2. Pour chaque match en attente ("pending") :
         - OUI / bouton "✅ Publier"  -> status "validated" + DECLENCHE le Bloc 8
           (gh workflow run bloc8-publication.yml -f base=<base>) ;
         - NON / bouton "❌ Rejeter"  -> status "rejected" (arret propre, aucune
           publication).
    3. Accuser reception (answerCallbackQuery) et mettre a jour le message.

Decision : un callback porte la base exacte ; une reponse texte OUI/NON
s'applique au match "pending" le plus recent.

Securite : ne traite que les messages provenant de TELEGRAM_CHAT_ID.
Journalisation : [Bloc7].   Mode test : DRY_RUN=1.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib import parse, request

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
MONTAGE_DIR = _find_dir(PROJECT_ROOT, "05_Montage")

LOG_FILE = ARCHIVES_DIR / "log.md"
STATE_FILE = MONTAGE_DIR / "validation_state.json"

DRY_RUN = os.environ.get("DRY_RUN") == "1"
REQUEST_TIMEOUT = 30

OUI = {"oui", "yes", "ok", "publier", "👍", "✅"}
NON = {"non", "no", "rejeter", "stop", "👎", "❌"}
BLOC8_WORKFLOW = "bloc8-publication.yml"


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
# Telegram
# --------------------------------------------------------------------------- #
def tg_call(method: str, payload: dict) -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if DRY_RUN:
        print(f"[DRY_RUN] Telegram.{method} <- {json.dumps(payload, ensure_ascii=False)}")
        return {"ok": True, "result": []}
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method="POST",
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_updates(offset: int) -> list:
    res = tg_call("getUpdates", {
        "offset": offset + 1,
        "timeout": 0,
        "allowed_updates": ["message", "callback_query"],
    })
    return res.get("result", []) if res.get("ok") else []


# --------------------------------------------------------------------------- #
# Declenchement du Bloc 8 (tolerant si le workflow n'existe pas encore)
# --------------------------------------------------------------------------- #
def declencher_bloc8(base: str) -> None:
    if DRY_RUN:
        print(f"[DRY_RUN] gh workflow run {BLOC8_WORKFLOW} -f base={base}")
        return
    branche = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        res = subprocess.run(
            ["gh", "workflow", "run", BLOC8_WORKFLOW, "--ref", branche, "-f", f"base={base}"],
            capture_output=True, text=True,
        )
        if res.returncode == 0:
            log(f"🚀 Bloc 8 déclenché pour {base}.")
        else:
            log(f"⚠️ Bloc 8 non déclenché ({base}) — {res.stderr.strip()[:160]} "
                f"(workflow {BLOC8_WORKFLOW} pas encore créé ?).")
    except FileNotFoundError:
        log(f"⚠️ 'gh' indisponible — Bloc 8 non déclenché pour {base}.")


# --------------------------------------------------------------------------- #
# Actions de validation
# --------------------------------------------------------------------------- #
def valider(base: str, state: dict) -> None:
    entry = state["matchs"].get(base)
    if not entry or entry.get("status") != "pending":
        return
    entry["status"] = "validated"
    entry["decide_le"] = datetime.now().isoformat()
    log(f"✅ Validé par l'utilisateur : {entry.get('titre', base)} — publication autorisée.")
    declencher_bloc8(base)


def rejeter(base: str, state: dict) -> None:
    entry = state["matchs"].get(base)
    if not entry or entry.get("status") != "pending":
        return
    entry["status"] = "rejected"
    entry["decide_le"] = datetime.now().isoformat()
    log(f"❌ Rejeté par l'utilisateur : {entry.get('titre', base)} — arrêt propre, aucune publication.")


def _base_pending_recente(state: dict) -> str | None:
    pend = [(b, e) for b, e in state["matchs"].items() if e.get("status") == "pending"]
    if not pend:
        return None
    pend.sort(key=lambda x: x[1].get("notifie_le", ""), reverse=True)
    return pend[0][0]


# --------------------------------------------------------------------------- #
# Point d'entree
# --------------------------------------------------------------------------- #
def main() -> int:
    chat_id = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
    state = load_state()
    offset = int(state.get("telegram_offset", 0))

    try:
        updates = get_updates(offset)
    except Exception as exc:
        log(f"⚠️ Echec getUpdates : {exc}")
        return 0

    if not updates:
        return 0

    max_update_id = offset
    decisions = 0

    for upd in updates:
        max_update_id = max(max_update_id, upd.get("update_id", offset))

        # 1) Bouton inline (porte la base exacte)
        cq = upd.get("callback_query")
        if cq:
            de = str((cq.get("from") or {}).get("id", ""))
            data = cq.get("data", "")
            if chat_id and de != chat_id:
                continue
            if ":" in data:
                action, base = data.split(":", 1)
                if action == "publish":
                    valider(base, state); decisions += 1
                elif action == "reject":
                    rejeter(base, state); decisions += 1
                if not DRY_RUN:
                    try:
                        tg_call("answerCallbackQuery", {
                            "callback_query_id": cq.get("id"),
                            "text": "Pris en compte ✅" if action == "publish" else "Rejeté ❌",
                        })
                    except Exception:
                        pass
            continue

        # 2) Reponse texte OUI / NON (s'applique au pending le plus recent)
        msg = upd.get("message") or {}
        de = str((msg.get("from") or {}).get("id", ""))
        if chat_id and de != chat_id:
            continue
        texte = (msg.get("text") or "").strip().lower()
        if texte in OUI or texte in NON:
            base = _base_pending_recente(state)
            if base is None:
                log("ℹ️ Réponse reçue mais aucun match en attente de validation.")
            elif texte in OUI:
                valider(base, state); decisions += 1
            else:
                rejeter(base, state); decisions += 1

    state["telegram_offset"] = max_update_id
    save_state(state)
    if decisions:
        log(f"✅ Watcher : {decisions} décision(s) traitée(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
