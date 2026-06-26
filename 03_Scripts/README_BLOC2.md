# FootFlash — Bloc 2 : Téléchargement des clips officiels FIFA

Workflow GitHub Actions qui se déclenche quand un nouveau JSON de match apparaît
dans `01_Donnees/matchs/`, télécharge le highlight **officiel FIFA** correspondant
via `yt-dlp`, le range dans `02_Clips/bruts/`, notifie Telegram et journalise dans
`01_Donnees/archives/log.md`.

## Composants

| Fichier | Rôle |
|---|---|
| `.github/workflows/bloc2-telechargement-clips.yml` | Workflow (déclencheur + exécution) |
| `03_Scripts/telecharger_clips.py` | Logique : découverte en cascade, vérif FIFA, téléchargement |
| `.gitattributes` | Git LFS pour les `.mp4` (vidéos lourdes) |
| `.gitignore` | Exclut `config.md` et les secrets |

## Découverte du clip — cascade (la 1ʳᵉ source officielle certifiée gagne)

1. **(a)** Champ `url_clip_fifa` présent dans le JSON du match.
2. **(b)** YouTube Data API, recherche **restreinte à la chaîne officielle @FIFA**.
3. **(c)** Recherche `yt-dlp`, puis **filtre sur la chaîne officielle FIFA**.

Chaque candidat passe le garde-fou `verify_official_fifa()` :
la vidéo doit appartenir à la chaîne **`UCpcTrCXblq78GZrTUTLWeBw`** (`youtube.com/@FIFA`).
Si la cascade n'aboutit à **aucune** source officielle certifiée → **aucun téléchargement**,
arrêt du match concerné et **alerte Telegram** (conforme à `regles-fifa.md`).

## Nomenclature des fichiers

`AAAA-MM-JJ_equipe1-equipe2_brut.mp4` — ex. `2026-06-15_bresil-france_brut.mp4`,
rangé dans `02_Clips/bruts/`.

## Installation du repo `footflash-pipeline`

```bash
# 1. Créer le repo GitHub (vide) nommé : footflash-pipeline
# 2. Depuis le dossier FootFlash local :
git init
git lfs install                 # IMPORTANT : Git LFS pour les vidéos
git add .
git commit -m "Init FootFlash pipeline (Bloc 1 + Bloc 2)"
git branch -M main
git remote add origin https://github.com/<votre-compte>/footflash-pipeline.git
git push -u origin main
```

> ⚠️ Vérifiez **avant le premier push** que `config.md` est bien ignoré
> (`git status` ne doit pas le lister). Il contient des secrets en clair.

## Secrets GitHub à créer

`Settings → Secrets and variables → Actions → New repository secret` :

| Secret | Contenu |
|---|---|
| `YOUTUBE_API_KEY` | Clé YouTube Data API v3 (méthode b) |
| `TELEGRAM_BOT_TOKEN` | Token du bot Telegram |
| `TELEGRAM_CHAT_ID` | ID du chat de notification |

> Ces valeurs sont aujourd'hui dans `00_Context/config.md`. Recopiez-les dans les
> GitHub Secrets, **puis ne poussez jamais `config.md`**. Comme le token Telegram
> a déjà été partagé en clair, il est recommandé de le **régénérer** via @BotFather.

## Déclenchement & anti-doublon

- Déclencheur : `push` sur `**/*matchs*/*.json` (tolérant au préfixe emoji des dossiers)
  + `workflow_dispatch` (manuel).
- Anti-doublon : `02_Clips/bruts/.downloaded.json` — un match déjà téléchargé est ignoré.
- Le workflow committe dans `02_Clips/` et `01_Donnees/archives/` (jamais dans `matchs/`),
  donc ses propres commits ne se redéclenchent pas (et `[skip ci]` en renfort).

## Pré-requis : pousser les JSON

Le Bloc 1 tourne en local et produit les JSON. Pour déclencher le Bloc 2, ces JSON
doivent être **commités et poussés** sur `footflash-pipeline` (manuellement ou via
une étape `git push` ajoutée au Bloc 1).
