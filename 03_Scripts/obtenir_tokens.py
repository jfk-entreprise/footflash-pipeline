#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FootFlash - Helper : obtention des refresh tokens OAuth (YouTube + TikTok)
==========================================================================

A LANCER EN LOCAL (sur ta machine), une seule fois par plateforme, pour
recuperer les `refresh_token` a deposer dans les GitHub Secrets du depot :
    YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN
    TIKTOK_*_CLIENT_KEY / _SECRET (deja en secrets) + TIKTOK_REFRESH_TOKEN

STDLIB UNIQUEMENT (aucun pip). Le script :
  1. affiche l'URL de consentement a ouvrir dans ton navigateur ;
  2. te demande de coller le `code` recupere dans l'URL de redirection ;
  3. echange ce code et affiche le `refresh_token`.

Exemples :
  python obtenir_tokens.py youtube --client-id XXX --client-secret YYY
  python obtenir_tokens.py tiktok  --client-key XXX --client-secret YYY \\
                                   --redirect-uri https://localhost/callback
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import sys
from urllib import parse, request, error

YT_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
YT_TOKEN = "https://oauth2.googleapis.com/token"
YT_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

TT_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TT_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
TT_SCOPE = "video.publish"


def _post_form(url: str, data: dict) -> dict:
    body = parse.urlencode(data).encode("utf-8")
    req = request.Request(url, data=body, method="POST",
                          headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"❌ Echec HTTP {exc.code} : {detail}")


def _pkce_pair() -> tuple[str, str]:
    """Génère (code_verifier, code_challenge) PKCE — méthode S256.

    - verifier : base64url sans padding, 43-128 caractères (ici 86).
    - challenge : base64url(SHA256(verifier)) sans padding.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _demander_code(url_consentement: str) -> str:
    print("\n1) Ouvre cette URL dans ton navigateur (connecté au bon compte) :\n")
    print("   " + url_consentement + "\n")
    print("2) Autorise l'accès. Tu seras redirigé vers ton redirect_uri avec\n"
          "   un paramètre `code=...` dans l'URL (la page peut afficher une erreur,\n"
          "   c'est normal : seul le `code` compte).\n")
    code = input("3) Colle ici la valeur du `code` : ").strip()
    if not code:
        raise SystemExit("Aucun code fourni — abandon.")
    return code


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #
def youtube(args) -> None:
    params = {
        "client_id": args.client_id,
        "redirect_uri": args.redirect_uri,
        "response_type": "code",
        "scope": YT_SCOPE,
        "access_type": "offline",      # indispensable pour obtenir un refresh_token
        "prompt": "consent",           # force la délivrance d'un nouveau refresh_token
    }
    code = _demander_code(f"{YT_AUTH}?{parse.urlencode(params)}")
    res = _post_form(YT_TOKEN, {
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": args.redirect_uri,
    })
    rt = res.get("refresh_token")
    if not rt:
        raise SystemExit(f"❌ Pas de refresh_token dans la réponse : {res}")
    print("\n✅ YOUTUBE_REFRESH_TOKEN =\n" + rt)
    print("\n→ À déposer dans GitHub Secrets (avec YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET).")


# --------------------------------------------------------------------------- #
# TikTok
# --------------------------------------------------------------------------- #
def tiktok(args) -> None:
    verifier, challenge = _pkce_pair()                # PKCE (S256)
    params = {
        "client_key": args.client_key,
        "scope": TT_SCOPE,
        "response_type": "code",
        "redirect_uri": args.redirect_uri,
        "state": "footflash",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    code = _demander_code(f"{TT_AUTH}?{parse.urlencode(params)}")
    res = _post_form(TT_TOKEN, {
        "client_key": args.client_key,
        "client_secret": args.client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": args.redirect_uri,
        "code_verifier": verifier,                    # PKCE : preuve liée au challenge
    })
    # TikTok renvoie soit a plat, soit sous une clé "data" selon les versions.
    rt = res.get("refresh_token") or (res.get("data") or {}).get("refresh_token")
    if not rt:
        raise SystemExit(f"❌ Pas de refresh_token dans la réponse : {res}")
    print("\n✅ TIKTOK_REFRESH_TOKEN =\n" + rt)
    print("\n→ À déposer dans GitHub Secrets (CLIENT_KEY / _SECRET déjà présents).")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> int:
    ap = argparse.ArgumentParser(description="Récupère les refresh tokens OAuth FootFlash.")
    sub = ap.add_subparsers(dest="plateforme", required=True)

    y = sub.add_parser("youtube", help="Refresh token YouTube Data API v3 (scope youtube.upload).")
    y.add_argument("--client-id", required=True)
    y.add_argument("--client-secret", required=True)
    y.add_argument("--redirect-uri", default="http://localhost",
                   help="Doit être enregistrée sur le client OAuth (défaut: http://localhost).")
    y.set_defaults(func=youtube)

    t = sub.add_parser("tiktok", help="Refresh token TikTok Content Posting API (scope video.publish).")
    t.add_argument("--client-key", required=True)
    t.add_argument("--client-secret", required=True)
    t.add_argument("--redirect-uri", required=True,
                   help="Doit correspondre EXACTEMENT à l'URI enregistrée sur l'app TikTok.")
    t.set_defaults(func=tiktok)

    args = ap.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
