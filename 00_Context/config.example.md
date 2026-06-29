# Configuration — Clés API (EXEMPLE)

> ⚠️ Ce fichier est un MODÈLE suivi par git. Il ne contient AUCUNE vraie valeur.
> En production, les secrets vivent **uniquement** dans les GitHub Secrets.
> Pour un run LOCAL, copie ce fichier en `config.md` (gitignoré) et remplis les valeurs.

## Football-Data.org (Bloc 1 — détection des matchs)
FOOTBALL_DATA_API_KEY=<ta_cle_football_data_org>
BASE_URL=<https://api.football-data.org/v4>

## Telegram
TELEGRAM_BOT_TOKEN=<token_du_bot_telegram>
TELEGRAM_CHAT_ID=<id_du_chat_telegram>

## YouTube
YOUTUBE_API_KEY=<cle_api_youtube_lecture_seule>
# Upload (OAuth) — voir 03_Scripts/obtenir_tokens.py
YOUTUBE_CLIENT_ID=<client_id_oauth_youtube>
YOUTUBE_CLIENT_SECRET=<client_secret_oauth_youtube>
YOUTUBE_REFRESH_TOKEN=<refresh_token_youtube>

## TikTok
TIKTOK_SANDBOX_CLIENT_KEY=<client_key_tiktok>
TIKTOK_SANDBOX_CLIENT_SECRET=<client_secret_tiktok>
TIKTOK_REFRESH_TOKEN=<refresh_token_tiktok>
