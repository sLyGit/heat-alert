# Déploiement gratuit sur GitHub Actions + notifications iPhone (ntfy)

Objectif : faire tourner le script **sans laisser ton Mac allumé**. GitHub exécute le script
toutes les 30 min sur ses serveurs (gratuit), et les alertes arrivent sur ton iPhone via
l'app **ntfy**. Tes identifiants restent dans les « Secrets » du dépôt (jamais dans le code).

Temps de mise en place : ~15 min, sans rien installer sur ton ordinateur.

---

## Vue d'ensemble

```
GitHub Actions (cron 30 min)  →  netatmo_compare.py  →  API Netatmo
                                          │
                                          └──> ntfy.sh ──> app ntfy sur iPhone (push)
```

- L'**historique** et le **refresh_token** (qui « tourne ») sont conservés entre deux
  exécutions grâce au **cache** GitHub. Aucun secret n'est écrit dans le dépôt.

---

## Étape 1 — App ntfy sur iPhone

1. Installe **ntfy** depuis l'App Store (gratuit, open-source).
2. Invente un **nom de sujet (topic) long et secret**, par ex. `netatmo-sly-9f3k7q2x`.
   ⚠️ ntfy.sh est public : quiconque connaît le topic peut lire/écrire. Garde-le secret.
3. Dans l'app ntfy : **+** → « Subscribe to topic » → saisis exactement ce nom.

Garde ce topic de côté, il ira dans les secrets GitHub.

---

## Étape 2 — Token Netatmo avec les bons scopes

Sur **https://dev.netatmo.com** → ton app → « Token generator » : coche
**`read_station`** ET **`read_thermostat`**, génère, et copie le **Refresh Token**.
(Voir README pour le détail.)

---

## Étape 3 — Créer le dépôt GitHub

1. Crée un compte sur github.com (gratuit) si besoin.
2. Crée un dépôt (ex. `netatmo-alerte`). Tu peux le créer privé pour configurer tranquillement,
   puis le passer **public** à l'étape 7 (obligatoire pour Pages en plan gratuit). Les secrets
   restent protégés dans tous les cas.
3. Ajoute ces fichiers à la racine du dépôt :
   - `netatmo_compare.py`
   - `.gitignore`
   - `.github/workflows/netatmo.yml`  ← c'est le fichier `github-workflow-netatmo.yml` fourni,
     **renommé** `netatmo.yml` et placé dans le dossier `.github/workflows/`.

> Tu peux tout faire depuis l'interface web GitHub (bouton « Add file » → « Create new file »),
> pas besoin de Git en ligne de commande. Pour créer le dossier, tape
> `.github/workflows/netatmo.yml` comme nom de fichier : GitHub crée les dossiers tout seul.

---

## Étape 4 — Ajouter les secrets

Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**.
Crée ces 4 secrets :

| Nom du secret | Valeur |
|---|---|
| `NETATMO_CLIENT_ID` | ton client id |
| `NETATMO_CLIENT_SECRET` | ton client secret |
| `NETATMO_REFRESH_TOKEN` | le refresh token généré à l'étape 2 |
| `NTFY_TOPIC` | ton topic ntfy secret (étape 1) |

---

## Étape 5 — Régler tes paramètres (Variables de dépôt)

Les réglages sont **externalisés** : ils ne sont pas écrits en clair dans le workflow, mais gérés
depuis l'interface GitHub. Va dans **Settings → Secrets and variables → Actions → onglet
« Variables » → New repository variable**, et crée celles qui t'intéressent :

| Variable | Défaut | Rôle |
|---|---|---|
| `NETATMO_INDOOR_ROOMS` | *(vide = toutes les zones)* | Noms exacts des zones à suivre, séparés par des virgules. Ex : `RDC / entrée,Souplex / salon` |
| `NETATMO_RADIUS_KM` | `2.0` | Rayon de recherche des stations publiques |
| `NETATMO_HEAT_MAX_C` | `28.0` | Seuil « forte chaleur » (max extérieur récent) qui active les notifs |
| `NETATMO_NOTIFY_MIN_INDOOR_C` | `25.0` | Alerte OUVRIR seulement si la pièce dépasse cette température |
| `NETATMO_MARGIN_OPEN_C` | `1.0` | Marge pour basculer « OUVRIR » |
| `NETATMO_MARGIN_CLOSE_C` | `0.0` | Marge pour basculer « FERMER » |
| `DASHBOARD_URL` | *(vide)* | URL de ta page Pages : la notif devient cliquable et l'ouvre. Ex : `https://ton-pseudo.github.io/netatmo-alerte/` |

Seule `NETATMO_INDOOR_ROOMS` mérite vraiment d'être renseignée (sinon **toutes** les zones sont
suivies). Les autres ont une valeur par défaut raisonnable — ne crée la variable que si tu veux
changer la valeur. Pour connaître les noms exacts de tes zones :
`python3 netatmo_compare.py --list-rooms` (en local).

> **Variables ≠ Secrets.** Les Variables servent aux réglages non sensibles (et ne sont pas
> exposées dans les fichiers du dépôt, même public). Les identifiants, eux, restent dans l'onglet
> **Secrets**. Tu modifies une valeur quand tu veux dans l'UI, sans toucher au code ni redéployer.

---

## Étape 6 — Lancer et vérifier

1. Onglet **Actions** du dépôt → workflow « Netatmo alerte chaleur » → **Run workflow**
   (lancement manuel pour tester tout de suite).
2. Ouvre l'exécution : si tout est vert, c'est bon. En cas d'erreur, le log indique quoi corriger
   (souvent un secret manquant ou un scope `read_thermostat` oublié).
3. Ensuite, ça tourne **automatiquement toutes les 30 min**.

> Pour tester une vraie notification immédiatement, tu peux aussi, en local :
> `NTFY_TOPIC=ton-topic python3 netatmo_compare.py --test-notify`

---

## Étape 7 — Dashboard en ligne (GitHub Pages)

Le workflow publie automatiquement le `dashboard.html` sur une page web mise à jour à chaque
exécution.

> ⚠️ **Important (plan gratuit)** : GitHub Pages n'est disponible **que pour un dépôt PUBLIC**.
> Pour garder le dépôt privé avec Pages, il faut GitHub Pro (payant). Ici on choisit donc de
> rendre le dépôt **public** — c'est sans danger : les secrets (`NETATMO_*`, `NTFY_TOPIC`) sont
> stockés comme **GitHub Secrets** et restent invisibles même dans un dépôt public, et le
> `.gitignore` garde la config / le token / l'historique hors du dépôt.

Activation (une seule fois) :

1. **Rendre le dépôt public** : Settings → General → tout en bas, **Danger Zone** →
   « Change repository visibility » → **Make public**.
2. **Activer Pages** : Settings → **Pages** → **Build and deployment → Source = « GitHub Actions »**.
3. Lance le workflow (Actions → Run workflow). L'URL apparaît dans **Settings → Pages**
   (ou sur la page de l'exécution, encart **« github-pages »**) :
   `https://<ton-pseudo>.github.io/<nom-du-depot>/`.

> **Check-list avant de passer public** : vérifie qu'aucun fichier `netatmo_config.json`,
> `netatmo_refresh_token.txt` ou `netatmo_history.csv` n'a été committé (le `.gitignore` les
> exclut). Les réglages (dont les noms de tes pièces) sont désormais dans les **Variables de
> dépôt** (étape 5), donc absents des fichiers publics. La page publiée n'affiche que des
> températures, jamais ton adresse ni tes identifiants.

> Note : les actions Pages (`configure-pages`, `upload-pages-artifact`, `deploy-pages`) peuvent
> encore afficher un petit avertissement « Node 20 » — c'est inoffensif, GitHub ne les a pas
> encore basculées sur Node 24.

---

## Notes

- **Horaire / fuseau** : le cron GitHub est en UTC. `*/30 4-21 * * *` ≈ 06h–00h heure de Paris
  l'été. Ajuste les heures dans le `cron` si besoin.
- **Coût** : gratuit. Un dépôt privé dispose d'un quota mensuel de minutes Actions largement
  suffisant pour un job de quelques secondes toutes les 30 min.
- **Pas de spam** : hors forte chaleur, le script tourne mais reste silencieux (voir README,
  « Mode forte chaleur »).
- **Dashboard** : publié sur **GitHub Pages** (étape 7), mis à jour à chaque exécution. Le
  workflow n'utilise plus `upload-artifact` (qui saturait le quota de stockage).
- **« Artifact storage quota has been hit »** : si tu as encore l'erreur d'avant, supprime les
  anciens artefacts (**Actions** → une exécution → section « Artifacts » → corbeille) ou attends
  6–12 h que le quota se recalcule. La version actuelle du workflow ne crée plus ce type d'artefact.
- **Quand le token est régénéré** : si un jour tu régénères le token sur dev.netatmo.com, mets à
  jour le secret `NETATMO_REFRESH_TOKEN` (le cache prendra le relais ensuite).
