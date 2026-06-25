# Intérieur vs Extérieur — Netatmo

Savoir **quand ouvrir** (il fait plus frais dehors) et **quand refermer** (ça repasse au-dessus),
en comparant tes capteurs Netatmo intérieurs aux **stations Netatmo publiques autour de chez toi**,
avec **filtrage des valeurs aberrantes** (ex. capteur exposé au plein soleil).

> ⚠️ Pourquoi ça tourne sur ta machine et pas dans le cloud : l'environnement d'exécution
> de l'assistant est derrière un proxy qui **bloque `api.netatmo.com`**. Ton Mac, lui, y accède
> sans problème — et tes identifiants restent chez toi.

---

## 1. Contenu

| Fichier | Rôle |
|---|---|
| `netatmo_compare.py` | Le moteur : auth, récupération données, filtrage, dashboard, alerte. **Aucune dépendance** (Python 3 standard). |
| `netatmo_config.example.json` | Modèle de configuration → à renommer `netatmo_config.json`. |
| `com.netatmo.compare.plist` | Planificateur macOS (launchd) pour l'alerte automatique toutes les 30 min. |
| `dashboard.html` | Généré à chaque exécution (tableau de bord visuel + courbe). |
| `netatmo_history.csv` | Historique des relevés (généré). |

---

## 2. Récupérer tes identifiants API (une seule fois)

Tu as dit avoir déjà `client_id` / `client_secret`. Il te faut en plus un **refresh_token**
avec les scopes **`read_station`** (capteurs météo) **et `read_thermostat`** (vannes + thermostat) :

1. Va sur **https://dev.netatmo.com** → connecte-toi → ton app.
2. Section **« Token generator »** : coche **`read_station`** ET **`read_thermostat`**, clique **Generate Token**.
3. Copie l'**Access Token** ET le **Refresh Token**.
   - Seul le **refresh_token** est nécessaire ici (le script génère les access tokens tout seul ensuite).

> Le script fait **tourner** le refresh_token automatiquement (Netatmo le renouvelle à chaque appel)
> et le réécrit dans `netatmo_config.json`. Tu n'as donc rien à refaire ensuite.

---

## 3. Configuration

```bash
cd <dossier où tu as mis les fichiers>
cp netatmo_config.example.json netatmo_config.json
```

Édite `netatmo_config.json` :

```json
{
  "client_id": "...",
  "client_secret": "...",
  "refresh_token": "..."
}
```

Options utiles (facultatives) :

| Clé | Défaut | Effet |
|---|---|---|
| `radius_km` | 2.0 | Rayon de recherche des stations publiques. ↑ si peu de stations près de chez toi. |
| `mad_k` | 3.0 | Sévérité du filtre anti-aberrant (↓ = plus strict). |
| `min_band_c` | 1.5 | Bande minimale (°C) autour de la médiane, même si tout le monde est d'accord. |
| `margin_open_c` | 1.0 | « OUVRIR » seulement si dehors est au moins X°C plus frais. |
| `margin_close_c` | 0.0 | « REFERMER » quand dehors repasse à ≤ X°C sous l'intérieur. |
| `notify_min_indoor_c` | 25.0 | Dans un épisode de chaleur, on ne notifie « OUVRIR » que si la pièce dépasse cette température. |
| `heat_outdoor_max_c` | 28.0 | **Mode forte chaleur** : les notifications ne s'activent QUE si l'extérieur a atteint ce seuil récemment. |
| `heat_lookback_hours` | 24 | Fenêtre (heures) sur laquelle on regarde le max extérieur pour décider du mode forte chaleur. |
| `indoor_rooms` | `[]` | **Zones à suivre** (RDC, souplex…) : liste des **noms exacts** de tes modules Netatmo. `[]` = toutes. |

> **Trouver les noms de tes zones :** `python3 netatmo_compare.py --list-rooms`
> affiche tous les modules intérieurs détectés (nom + température + **source**). Copie les noms voulus
> dans `indoor_rooms`, ex. `["RDC / entrée", "Souplex / salon"]`. Chaque zone a son propre
> verdict OUVRIR/FERMER et sa courbe dans le dashboard.

**Sources de température intérieure** (combinables) :

| Source | Activée par | API | Scope |
|---|---|---|---|
| Capteurs météo (NAMain / module intérieur) | `use_weather` | Weather | `read_station` |
| Vannes (NRV) + thermostat (NATherm1) | `use_thermostats` | Energy | `read_thermostat` |

> Si une même pièce existe dans les deux sources, la version « vanne » est suffixée `(vanne)`
> pour les distinguer. `home_id` cible un logement précis (sinon le 1er). Si tu n'as **pas**
> de station météo, l'API Energy ne fournit pas toujours les coordonnées : renseigne alors
> `home_lat` / `home_lon` dans la config pour la recherche des stations extérieures.

> `margin_open_c` et `margin_close_c` créent une **hystérésis** : pas de yo-yo ouvrir/fermer
> quand les températures se croisent.

---

## 4. Utilisation manuelle

```bash
python3 netatmo_compare.py --list-rooms    # 1ère fois : voir les noms de tes zones
python3 netatmo_compare.py --test-notify   # vérifier que les notifications macOS marchent
python3 netatmo_compare.py                 # relevé + maj dashboard
python3 netatmo_compare.py --open-dashboard# + ouvre dashboard.html
python3 netatmo_compare.py --notify        # + notif macOS (uniquement en mode forte chaleur)
```

### Mode forte chaleur

Les notifications ne se déclenchent **que pendant un épisode de chaleur** : il faut que la
température **extérieure** ait atteint `heat_outdoor_max_c` (28°C par défaut) sur les
`heat_lookback_hours` dernières heures (24h). Hors canicule, le script continue de tourner
(dashboard + historique) mais **reste silencieux**. Le dashboard indique en haut si le mode
est ACTIF ou en veille. Baisse `heat_outdoor_max_c` si tu veux être alerté plus tôt.

Exemple de sortie (deux zones) :

```
  Extérieur (méd.) : 21.8°C   (sur 7 stations, 2 écartée(s))
  • RDC / entrée        24.0°C  écart -2.2°C  >>> OUVRIR — 2.2°C plus frais dehors → ouvre.
  • Souplex / salon     26.5°C  écart -4.7°C  >>> OUVRIR — 4.7°C plus frais dehors → ouvre.
```

Ouvre **`dashboard.html`** dans ton navigateur pour la vue graphique
(intérieur vs extérieur, écart, verdict, courbe, et la liste des capteurs écartés).

---

## 5. Alerte automatique (macOS, toutes les 30 min)

1. Trouve ton python : `which python3`
2. Édite `com.netatmo.compare.plist` → remplace les **deux chemins** (python3 et le script).
3. Installe :

```bash
cp com.netatmo.compare.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.netatmo.compare.plist
```

Tu recevras une **notification macOS** uniquement **au changement d'état** :
« OUVRIR » quand il devient plus frais dehors, « GARDER FERMÉ » quand ça repasse au-dessus.

Pour arrêter : `launchctl unload ~/Library/LaunchAgents/com.netatmo.compare.plist`
Logs : `/tmp/netatmo_compare.log` et `/tmp/netatmo_compare.err`.

> Alternative cron (Linux/Mac) : `*/30 * * * * /usr/bin/python3 /chemin/netatmo_compare.py --notify --quiet`

### Sans laisser le Mac allumé : GitHub Actions + iPhone (ntfy)

Pour faire tourner le tout **gratuitement dans le cloud** et recevoir les alertes **sur iPhone**
(app ntfy), sans aucun ordinateur allumé : voir **`DEPLOIEMENT-GITHUB-ACTIONS.md`**.
Le script lit alors ses identifiants depuis des variables d'environnement / secrets, et notifie
via ntfy au lieu de macOS (`NTFY_TOPIC`). Test rapide d'une notif iPhone :
`NTFY_TOPIC=ton-topic python3 netatmo_compare.py --test-notify`.

---

## 6. Comment marche le filtrage anti-aberrant

1. On récupère toutes les stations publiques dans `radius_km` autour de chez toi
   (`filter:true` côté Netatmo écarte déjà certaines stations douteuses).
2. On ignore les relevés trop **anciens** (`max_age_minutes`).
3. On calcule la **médiane** et le **MAD** (median absolute deviation), robustes aux extrêmes.
4. On **écarte** toute station hors de `médiane ± max(mad_k×1.4826×MAD, min_band_c)`.
   → typiquement les capteurs **au soleil** (trop chauds) ou défaillants.
5. La température extérieure retenue = **médiane des stations restantes**.

Les valeurs écartées sont listées dans le dashboard (transparence totale).
```
