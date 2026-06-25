# Intérieur vs Extérieur — Netatmo

Savoir **quand ouvrir** (il fait plus frais dehors) et **quand refermer** (ça repasse au-dessus),
en comparant tes capteurs Netatmo intérieurs aux **stations Netatmo publiques autour de chez toi**,
avec **filtrage des valeurs aberrantes** (ex. capteur exposé au plein soleil). Notifications
déclenchées **uniquement en cas de forte chaleur**.

---

## Deux modes d'installation

| | **Mode A — Cloud** (recommandé) | **Mode B — Local** (Mac) |
|---|---|---|
| Où ça tourne | Serveurs GitHub (GitHub Actions) | Ton Mac |
| Mac allumé ? | **Non** | Oui (pour la planification) |
| Notifications | iPhone (app **ntfy**) | **macOS** |
| Dashboard | En ligne (**GitHub Pages**) | `dashboard.html` local |
| Coût | Gratuit | Gratuit |
| Mise en place | ~15 min, voir **`DEPLOIEMENT-GITHUB-ACTIONS.md`** | quelques min, voir §6 |

Les deux modes utilisent le même moteur (`netatmo_compare.py`) et les mêmes identifiants Netatmo
(§3) et réglages (§4). Choisis l'un **ou** l'autre.

---

## 1. Contenu

| Fichier | Rôle |
|---|---|
| `netatmo_compare.py` | Le moteur : auth, données, filtrage, dashboard, alerte. **Aucune dépendance** (Python 3 standard). |
| `netatmo_config.example.json` | Modèle de configuration locale → à renommer `netatmo_config.json` (mode B). |
| `github-workflow-netatmo.yml` | Workflow GitHub Actions → à placer dans `.github/workflows/netatmo.yml` (mode A). |
| `DEPLOIEMENT-GITHUB-ACTIONS.md` | Guide pas-à-pas du mode A (cloud + iPhone + Pages). |
| `com.netatmo.compare.plist` | Planificateur macOS (launchd) pour le mode B. |
| `.gitignore` | Empêche de committer secrets / état (mode A). |
| `dashboard.html`, `netatmo_history.csv` | Générés à l'exécution. |

---

## 2. Identifiants API Netatmo (commun aux deux modes)

Il te faut `client_id`, `client_secret` et un **refresh_token** avec les scopes
**`read_station`** (capteurs météo) **et `read_thermostat`** (vannes + thermostat) :

1. Va sur **https://dev.netatmo.com** → connecte-toi → ton app.
2. Section **« Token generator »** : coche **`read_station`** ET **`read_thermostat`**, clique **Generate Token**.
3. Copie le **Refresh Token** (le script génère les access tokens tout seul ensuite).

> Le script fait **tourner** le refresh_token automatiquement (Netatmo le renouvelle à chaque
> appel) et persiste le nouveau. Tu n'as rien à refaire ensuite.

---

## 3. Réglages (commun aux deux modes)

Mêmes paramètres dans les deux modes — seul l'**endroit** où on les met change :
- **Mode A** : Variables de dépôt GitHub (`vars.*`) — voir `DEPLOIEMENT-GITHUB-ACTIONS.md`.
- **Mode B** : fichier `netatmo_config.json`.

| Clé (config) / Variable (GitHub) | Défaut | Effet |
|---|---|---|
| `indoor_rooms` / `NETATMO_INDOOR_ROOMS` | `[]` (toutes) | **Zones à suivre** : noms exacts des modules, séparés par des virgules. |
| `radius_km` / `NETATMO_RADIUS_KM` | 2.0 | Rayon de recherche des stations publiques. |
| `heat_outdoor_max_c` / `NETATMO_HEAT_MAX_C` | 28.0 | **Mode forte chaleur** : notifs actives seulement si l'extérieur a atteint ce seuil récemment. |
| `notify_min_indoor_c` / `NETATMO_NOTIFY_MIN_INDOOR_C` | 25.0 | « OUVRIR » notifié seulement si la pièce dépasse cette température. |
| `margin_open_c` / `NETATMO_MARGIN_OPEN_C` | 1.0 | « OUVRIR » si dehors est au moins X°C plus frais. |
| `margin_close_c` / `NETATMO_MARGIN_CLOSE_C` | 0.0 | « FERMER » quand dehors repasse à ≤ X°C sous l'intérieur. |
| `dashboard_url` / `DASHBOARD_URL` | *(vide)* | URL ouverte au tap sur la notif iPhone (page Pages). |
| `mad_k`, `min_band_c`, `heat_lookback_hours` | 3.0 / 1.5 / 24 | Réglages fins du filtrage et de la fenêtre forte chaleur. |

> **Trouver les noms de tes zones :** `python3 netatmo_compare.py --list-rooms` affiche tous les
> modules intérieurs (nom + température + **source**). Chaque zone a son propre verdict
> OUVRIR/FERMER et sa courbe.

**Sources de température intérieure** (combinables) :

| Source | Activée par | API | Scope |
|---|---|---|---|
| Capteurs météo (NAMain / module intérieur) | `use_weather` | Weather | `read_station` |
| Vannes (NRV) + thermostat (NATherm1) | `use_thermostats` | Energy | `read_thermostat` |

> Si une même pièce existe dans les deux sources, la version « vanne » est suffixée `(vanne)`.
> `margin_open_c` / `margin_close_c` créent une **hystérésis** (pas de yo-yo ouvrir/fermer).

### Mode forte chaleur

Les notifications ne se déclenchent **que pendant un épisode de chaleur** : il faut que la
température **extérieure** ait atteint `heat_outdoor_max_c` (28°C) sur les dernières 24h. Hors
canicule, le script tourne (dashboard + historique) mais **reste silencieux**. Le dashboard
indique en haut si le mode est ACTIF ou en veille.

---

## 4. Mode A — Cloud (GitHub Actions + iPhone + Pages)

Le tout tourne gratuitement sur les serveurs GitHub, sans Mac allumé. Les alertes arrivent sur
ton iPhone via l'app **ntfy**, et le dashboard est publié en ligne sur **GitHub Pages**.

➡️ **Guide complet pas-à-pas : `DEPLOIEMENT-GITHUB-ACTIONS.md`**

En résumé : créer un dépôt GitHub, y mettre `netatmo_compare.py` + `.gitignore` +
`.github/workflows/netatmo.yml`, ajouter les **secrets** (`NETATMO_CLIENT_ID/SECRET/REFRESH_TOKEN`,
`NTFY_TOPIC`), régler les **Variables** (§3), activer Pages, et c'est parti toutes les 30 min.

Test rapide d'une notif iPhone :
```bash
NTFY_TOPIC=ton-topic DASHBOARD_URL=https://ton-pseudo.github.io/ton-depot/ \
  python3 netatmo_compare.py --test-notify
```

---

## 5. Mode B — Local (Mac)

### 5.1 Configuration

```bash
cd <dossier des fichiers>
cp netatmo_config.example.json netatmo_config.json
```

Édite `netatmo_config.json` (au minimum les 3 identifiants, voir §2/§3) :

```json
{ "client_id": "...", "client_secret": "...", "refresh_token": "...",
  "indoor_rooms": ["RDC / entrée", "Souplex / salon"] }
```

### 5.2 Utilisation manuelle

```bash
python3 netatmo_compare.py --list-rooms     # 1ère fois : noms de tes zones
python3 netatmo_compare.py --test-notify    # vérifier les notifications macOS
python3 netatmo_compare.py --open-dashboard # relevé + ouvre dashboard.html
python3 netatmo_compare.py --notify         # relevé + notif macOS (si forte chaleur)
```

Exemple de sortie (deux zones) :
```
  Extérieur (méd.) : 21.8°C   (sur 7 stations, 2 écartée(s))
  • RDC / entrée        24.0°C  écart -2.2°C  >>> OUVRIR — 2.2°C plus frais dehors → ouvre.
  • Souplex / salon     26.5°C  écart -4.7°C  >>> OUVRIR — 4.7°C plus frais dehors → ouvre.
```

### 5.3 Planification automatique (launchd, toutes les 30 min)

1. `which python3` pour trouver ton interpréteur.
2. Édite `com.netatmo.compare.plist` → remplace les **deux chemins** (python3 et le script).
3. Installe :
```bash
cp com.netatmo.compare.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.netatmo.compare.plist
```

Arrêter : `launchctl unload ~/Library/LaunchAgents/com.netatmo.compare.plist`
Logs : `/tmp/netatmo_compare.log` et `/tmp/netatmo_compare.err`.

> Alternative cron : `*/30 * * * * /usr/bin/python3 /chemin/netatmo_compare.py --notify --quiet`

---

## 6. Comment marche le filtrage anti-aberrant

1. On récupère les stations publiques dans `radius_km` (`filter:true` côté Netatmo écarte déjà
   certaines stations douteuses).
2. On ignore les relevés trop **anciens** (`max_age_minutes`).
3. On calcule la **médiane** et le **MAD** (median absolute deviation), robustes aux extrêmes.
4. On **écarte** toute station hors de `médiane ± max(mad_k×1.4826×MAD, min_band_c)`
   → typiquement les capteurs **au soleil** (trop chauds) ou défaillants.
5. La température extérieure retenue = **médiane des stations restantes**.

Les valeurs écartées sont listées dans le dashboard (transparence totale).
