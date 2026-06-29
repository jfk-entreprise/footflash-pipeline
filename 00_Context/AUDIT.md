# FootFlash — Rapport d'audit de production

> Audit QA complet avant premier lancement. Sévérités : 🔴 critique · 🟠 majeur · 🟡 mineur · 🔵 suggestion.
> Statut des correctifs en fin de document.

## 🔴 CRITIQUE

### C1 — Fenêtre de détection ratait les matchs du soir (hôtes Amérique du Nord)
- **Fichier** : `bloc1-detection.yml` (cron) + `surveillance_matchs.py` (`main`).
- **Problème** : cron 13–23h UTC + interrogation de la seule date UTC du jour. Les matchs nord-américains du soir finissent entre 01h et 07h UTC ; un match démarré à 23h UTC J n'était jamais re-interrogé (J+1 interroge une autre date).
- **Impact** : les affiches les plus virales détectées avec ~11h de retard, voire jamais.
- **Correctif** : cron `*/20 * * * *` (24h/24) ; interrogation **hier + aujourd'hui** UTC avec déduplication par `fixture_id` ; `.processed.json` conservé.

### C2 — Hypothèses API-Football non vérifiées
- **Fichier** : `surveillance_matchs.py` (`LEAGUE_ID=1`, `SEASON=2026`).
- **Problème** : si l'ID ligue/saison est faux ou non couvert par le plan, `fixtures` reste vide en permanence, sans alerte.
- **Impact** : pipeline « vert » mais stérile.
- **Correctif** : mode `--verify` (self-test `next=10` + notif Telegram) ; alerte santé `.health.json` après 3 jours consécutifs sans match en plein tournoi ; input `verify` dans le workflow.

## 🟠 MAJEUR

### M1 — Échec d'upload mis en cache → vidéo jamais republiée
- **Fichier** : `publier.py` (`publier_match`).
- **Correctif** : on ne met en cache que les **succès** (`.get("id")`) ; YouTube et TikTok retentés indépendamment.

### M2 — Coût GitHub Actions du watcher
- **Fichier** : `bloc7-watch-validation.yml`.
- **Correctif** : cron borné `*/5 12-23 * * *` + `timeout-minutes: 4`. **Recommandé** : passer le repo en **public** (minutes illimitées) pour rétablir un watcher 24/7.

### M3 — Secrets réels en clair dans `config.md`
- **Correctif** : `config.example.md` (modèle suivi par git) créé ; `config.md` vidé en placeholders (reste gitignoré). **Action manuelle** : régénérer les clés exposées (API-Football, YouTube, token Telegram) côté fournisseurs.

### M4 — Voix française non garantie
- **Fichiers** : `bloc5-voix.yml`, `generer_voix.py`.
- **Correctif** : `misaki[fr]` installé (requirements) ; alerte Telegram si bascule en voix anglaise.

### M5 — Versions non épinglées
- **Correctif** : `requirements.txt` + `requirements-bloc{2,5,8}.txt` (versions fixes ; `yt-dlp` en plancher, à garder à jour). Workflows B2/B5/B8 branchés dessus.

## 🟡 MINEUR

- **m1** — `publier.py` : champ `mode: simulation|reel` ; en simulation, vidéos **non archivées**.
- **m2** — `watch_validation.py` : réponse OUI/NON ignorée si ≥2 matchs en attente → message « utilise les boutons ».
- **m3** — `analyse_moments.py` : alias xG (`expected_goals`, `Expected Goals`, …).
- **m4** — Musique `.mp3` commitée hors LFS. **Action manuelle** (machine avec git-lfs) :
  `git lfs migrate import --include="*.mp3,*.mp4" --everything` avant le push.
- **m5** — Échappement HTML des champs dynamiques (`publier.py`, `notifier_validation.py`).
- **m6** — `bloc2` déclenché sur `**/*matchs*/*_data.json` (n'inclut plus `_moments.json`).
- **m7** — Sous-titres : `SUB_FONTSIZE` réduit + rétrécissement auto des mots longs.

## 🔵 SUGGESTIONS

- **s1** — Cache pip / modèle Kokoro (`actions/cache`).
- **s2** — Retry ×3 sur les uploads (`publier.py`).
- **s3** — Validation du format `base` (`^\d{4}-\d{2}-\d{2}_[a-z0-9-]+$`).
- **s4** — Heartbeat quotidien Telegram (« pipeline vivant »).
- **s5** — Tests unitaires (`tests/`) : SEO, extraction sous-titres, PKCE, alias xG.
- **s6** — Watcher : `timeout-minutes: 4` < intervalle cron (anti-chevauchement).

## Score de maturité : 66/100 (avant correctifs)

## ⚠️ Incident environnement (session de correction)
Le dossier est monté dans le sandbox via une couche réseau qui a présenté une **instabilité de lecture sévère** : lectures tronquées en milieu de fichier et **index git corrompu**, provoquant des commits à blobs corrompus / fichiers manquants. Les **fichiers source sur disque sont intègres** (écritures fiables, vérifiées via l'outil de lecture applicatif). **Conséquence** : les commits doivent être refaits depuis une machine native (git Windows lit le disque réel, sans cette couche). Voir « Actions manuelles ».

## Statut des correctifs
- 🔴 C1, C2 : appliqués sur disque (workflow bloc1 + surveillance_matchs.py).
- 🟠 M1–M5 : appliqués sur disque.
- 🟡 m1–m3, m5–m7 : appliqués sur disque ; m4 = action manuelle.
- 🔵 : s6 (timeout watcher) appliqué. s1–s5 **documentés ici comme recommandations** (non appliqués : l'instabilité du montage rendait tout nouvel écrit risqué pour des items « bonus »).
