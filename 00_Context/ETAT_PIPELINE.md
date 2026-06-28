# FootFlash — État du pipeline (document de reprise)

> **À lire en premier par toute nouvelle session Cowork.**
> Pipeline d'analyse football CdM 2026 — clips officiels FIFA uniquement.
> Le dépôt vit dans `C:\dev\footflash-pipeline` (repo GitHub `jfk-entreprise/footflash-pipeline`).

## 🟢 Statut global : **COMPLET** — les 8 blocs sont implémentés et testés.

Reste avant la mise en production réelle : configurer **4 secrets OAuth** (§ Tokens) ;
sans eux, le Bloc 8 tourne en **mode SIMULATION** (toute la chaîne s'exécute sauf l'upload).

---

## 1. Objet

Création de contenu football : analyses/commentaires de la **Coupe du Monde 2026**, publiés en
TikTok & YouTube Shorts. Le pipeline va de la donnée brute du match jusqu'à la vidéo publiée.
**Jonas ne fait que la validation finale (OUI/NON par Telegram).**

**Règle absolue** (voir `00_Context/regles-fifa.md`) : **UNIQUEMENT** les clips officiels FIFA
(YouTube `@FIFA` = `UCpcTrCXblq78GZrTUTLWeBw`). Jamais de diffuseurs TV ni de comptes tiers.
Valeur éditoriale obligatoire (voix off / analyse). En cas de doute : on s'arrête et on notifie.

## 2. Architecture (tout en GitHub Actions, runners éphémères)

```
B1 (cron */30 13-23h) détecte matchs terminés (API-Football) → *_data.json
   → gh workflow run → B2
B2 (push JSON)        télécharge le highlight officiel FIFA (yt-dlp) → *_brut.mp4
B3 (after B2)         moments clés → *_moments.json
B4 (after B3)         scripts voix off → *_script-{buts,analyse}.txt
B5 (after B4)         voix off Kokoro TTS → *_voix-{buts,analyse}.mp3
B6 (after B5)         montage FFmpeg 9:16 → *_video-{buts,analyse}.mp4
B7 (after B6)         notif Telegram + validation ; watcher cron */5 lit OUI/NON
   → gh workflow run → B8 (si OUI)
B8 (dispatch)         publie TikTok + YouTube (privé), archive, status=published
```
Chaînage : `workflow_run` (B3→B6, B8 via watcher), `gh workflow run` (B1→B2, B7→B8).
Un push fait par `GITHUB_TOKEN` ne déclenche pas d'autre workflow → déclenchements explicites.

## 3. Les 8 blocs

| Bloc | Script(s) | Workflow | Sortie | Statut |
|------|-----------|----------|--------|:------:|
| 1 — Détection | `surveillance_matchs.py` | `bloc1-detection.yml` (cron + dispatch) | `01_Donnees/matchs/*_data.json` | ✅ |
| 2 — Clips FIFA | `telecharger_clips.py` | `bloc2-telechargement-clips.yml` (push JSON) | `02_Clips/bruts/*_brut.mp4` | ✅ |
| 3 — Moments clés | `analyse_moments.py` | `bloc3-analyse-moments.yml` (after B2) | `01_Donnees/matchs/*_moments.json` | ✅ |
| 4 — Scripts | `buts/generer_script_buts.py`, `analyse/generer_script_analyse.py` | `bloc4-scripts.yml` (after B3) | `03_Scripts/{buts,analyse}/*_script-*.txt` | ✅ |
| 5 — Voix off | `generer_voix.py` (Kokoro `ff_siwis`, repli EN) | `bloc5-voix.yml` (after B4) | `04_Audio/voixoff/*_voix-*.mp3` | ✅ |
| 6 — Montage | `generer_montage.py` (FFmpeg, 9:16 fond flou, sous-titres `.ass` bas-centre) | `bloc6-montage.yml` (after B5) | `05_Montage/rendus/*_video-*.mp4` | ✅ |
| 7 — Validation | `notifier_validation.py` + `watch_validation.py` | `bloc7-notification.yml` (after B6) + `bloc7-watch-validation.yml` (cron */5) | `05_Montage/validation_state.json` | ✅ |
| 8 — Publication | `publier.py` (TikTok + YouTube, SEO auto, privé) | `bloc8-publication.yml` (dispatch `base`) | `06_Archives/publie/*_video-*.mp4` | ✅ |

Helper hors-pipeline : `03_Scripts/obtenir_tokens.py` (récupération des refresh tokens OAuth).

## 4. Secrets GitHub

**Présents / requis dès maintenant :** `API_FOOTBALL_KEY`, `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`, `YOUTUBE_API_KEY` (lecture FIFA), `TIKTOK_SANDBOX_CLIENT_KEY`,
`TIKTOK_SANDBOX_CLIENT_SECRET`.

**À CONFIGURER pour publier réellement (sinon Bloc 8 = SIMULATION) :**

| Secret | Obtention |
|--------|-----------|
| `YOUTUBE_CLIENT_ID` | Google Cloud Console → OAuth client (Desktop) |
| `YOUTUBE_CLIENT_SECRET` | idem |
| `YOUTUBE_REFRESH_TOKEN` | `python 03_Scripts/obtenir_tokens.py youtube --client-id … --client-secret …` |
| `TIKTOK_REFRESH_TOKEN` | `python 03_Scripts/obtenir_tokens.py tiktok --client-key … --client-secret … --redirect-uri …` |

⚠️ YouTube : tant que l'écran de consentement est en « Testing », le refresh token (scope
`youtube.upload`, sensible) **expire après 7 jours** → passer l'app « In production ».
⚠️ TikTok sandbox/non audité : posts forcés **privés (SELF_ONLY)** et limités au compte de test
jusqu'à l'audit. `config.md` reste **gitignoré** (jamais commité).

## 5. Conventions & robustesse

- Nomenclature : `AAAA-MM-JJ_equipe1-equipe2_[type]`. `_find_dir()` tolère un préfixe emoji « 📁 ».
- Timelines `.ass` → `05_Montage/timelines/` · Rendus MP4 → `05_Montage/rendus/` · Publié → `06_Archives/publie/` (**ne jamais modifier**).
- **Journaliser chaque action** dans `01_Donnees/archives/log.md` (préfixes `[Bloc1]`…`[Bloc8]`).
- **Persistance des états (CRITIQUE)** : `.processed/.downloaded/.analyzed/.scripted/.voiced/.rendered.json`
  et `validation_state.json` sont **suivis par git et commités** (runners éphémères). **Ne jamais
  les re-ignorer.** Seuls `config.md`, `*.env`, `__pycache__`, `*.pyc` restent gitignorés.
- **Push résilient** : chaque workflow pousse via `git pull --rebase --autostash` + `git push`
  en boucle (retry ×3) → absorbe les pushs concurrents (chaînage + watcher cron */5).
- LFS : `*.mp4` et `*.mp3` (`.gitattributes`).
- Sécurité secrets : lus depuis l'environnement (GitHub Secrets), repli `config.md` en local.
  Les scripts ne s'auto-poussent pas sous `GITHUB_ACTIONS` (c'est le workflow qui commit/push).
- Stdlib only pour B1-B4 ; B5 (kokoro/torch/soundfile), B6 (ffmpeg) et B8 (requests) ont des deps.

## 6. Procédure de premier lancement

1. **Secrets** : ajouter les secrets du §4 (au minimum ceux « présents » ; ajouter les 4 OAuth pour publier en vrai).
2. **Actions** : activer les workflows + permission « Read and write » (Settings → Actions → General).
3. **Musique** (optionnel) : déposer un `.mp3` libre de droits dans `04_Audio/musique/` (sinon montage sans musique).
4. **Test manuel de bout en bout**, dans la fenêtre tournoi (11/06 → 19/07 2026) :
   - `bloc1-detection.yml` → *Run workflow* (ou attendre le cron) ; vérifier un `*_data.json` + notif Telegram.
   - La chaîne s'enchaîne seule jusqu'au Bloc 6 (clip → moments → scripts → voix → vidéo).
   - Bloc 7 envoie la notif de validation ; **répondre `OUI`** (ou bouton ✅) → le watcher (cron */5) déclenche le Bloc 8.
   - Sans tokens OAuth : Bloc 8 en **SIMULATION** (état `published`, MP4 archivés, notif `[SIMULATION]`).
     Avec tokens : upload réel **privé/unlisted** → vérifier puis passer en public manuellement.
5. **Garde-fous** : B1 et le watcher s'arrêtent proprement hors fenêtre tournoi.

## 7. Leçons / pièges

- **Pas de dépôt dans OneDrive** (déshydratation des fichiers `.git` → casse). D'où `C:\dev\footflash-pipeline`.
- **Écriture de gros fichiers** : toujours vérifier après coup (`py_compile`, `tail`) — de gros
  remplacements peuvent être tronqués en fin de fichier.
- `validation_state.json` doit rester **tracké** (sinon le watcher perd l'offset Telegram et l'état).

## 8. Profil utilisateur & ton attendu

Jonas — profil multidisciplinaire (géomatique/SIG, terrain, data analyst, créateur de contenu).
**Réponses concises et directes**, en français, structurées (tableaux/listes/code). Pas de
réexplication des bases. Esprit critique bienvenu (proposer des alternatives meilleures).
