#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
netatmo_compare.py
==================
Compare la température de plusieurs ZONES INTÉRIEURES (tes capteurs Netatmo)
avec la température EXTÉRIEURE relevée par les stations Netatmo publiques
AUTOUR de chez toi, en écartant les valeurs aberrantes (ex. capteur au soleil).

Chaque zone (ex. "RDC / entrée" et "Souplex / salon") a son propre verdict
OUVRIR / GARDER FERMÉ avec hystérésis pour éviter le yo-yo.

- Zéro dépendance : uniquement la bibliothèque standard Python 3 (urllib).
- Tourne sur TA machine (le service cloud n'a pas accès à api.netatmo.com).
- Écrit un dashboard HTML, un historique CSV, et peut notifier (macOS).

Usage :
    python3 netatmo_compare.py                 # un relevé + maj dashboard
    python3 netatmo_compare.py --notify        # + notification macOS si un état change
    python3 netatmo_compare.py --open-dashboard# + ouvre le dashboard dans le navigateur
    python3 netatmo_compare.py --list-rooms    # affiche les pièces détectées et quitte
    python3 netatmo_compare.py --quiet         # sans sortie console (pour cron/launchd)

Config : voir netatmo_config.json (créé à partir de netatmo_config.example.json).
"""

import argparse
import csv
import json
import math
import os
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Chemins
# --------------------------------------------------------------------------- #
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# STATE_DIR : où sont écrits l'historique, l'état et le dashboard.
# Sur GitHub Actions on le pointe vers un dossier mis en cache (NETATMO_STATE_DIR).
STATE_DIR = os.environ.get("NETATMO_STATE_DIR") or SCRIPT_DIR
CONFIG_PATH = os.path.join(SCRIPT_DIR, "netatmo_config.json")
STATE_PATH = os.path.join(STATE_DIR, "netatmo_state.json")
HISTORY_PATH = os.path.join(STATE_DIR, "netatmo_history.csv")
DASHBOARD_PATH = os.path.join(STATE_DIR, "dashboard.html")
TOKEN_FILE = os.path.join(STATE_DIR, "netatmo_refresh_token.txt")  # token tournant persisté

API_BASE = "https://api.netatmo.com"
TOKEN_URL = API_BASE + "/oauth2/token"
STATIONS_URL = API_BASE + "/api/getstationsdata"
PUBLIC_URL = API_BASE + "/api/getpublicdata"
HOMESDATA_URL = API_BASE + "/api/homesdata"
HOMESTATUS_URL = API_BASE + "/api/homestatus"

# Valeurs par défaut (surchargées par le fichier de config)
DEFAULTS = {
    "radius_km": 2.0,            # rayon de recherche des stations publiques
    "max_age_minutes": 60,       # on ignore les relevés plus vieux que ça
    "mad_k": 3.0,                # seuil de rejet : k * 1.4826 * MAD autour de la médiane
    "min_band_c": 1.5,           # bande mini (°C) même si la dispersion est faible
    "min_stations": 3,           # nb mini de stations valides pour faire confiance
    "margin_open_c": 1.0,        # OUVRIR si ext <= zone - margin_open
    "margin_close_c": 0.0,       # REFERMER si ext >= zone - margin_close
    "notify_min_indoor_c": 25.0, # alerte OUVRIR seulement si la pièce dépasse ce seuil
    "heat_outdoor_max_c": 28.0,  # MODE FORTE CHALEUR : notifs actives seulement si l'extérieur
    "heat_lookback_hours": 24,   #   a atteint heat_outdoor_max_c sur les dernières N heures
    "ntfy_topic": None,          # push iPhone via ntfy.sh (None = notification macOS locale)
    "ntfy_server": "https://ntfy.sh",
    "dashboard_url": None,       # URL ouverte au tap sur la notif (ex. page GitHub Pages)
    "indoor_rooms": [],          # [] = toutes les zones détectées ; sinon liste de noms
    "use_weather": True,         # zones depuis les capteurs météo (getstationsdata)
    "use_thermostats": True,     # zones depuis vannes NRV + thermostat NATherm1 (API Energy)
    "home_id": None,             # API Energy : id du logement (None = le 1er)
    "home_lat": None,            # forcer la position si pas de station météo
    "home_lon": None,
    "history_max_rows": 5000,
}

# Couleurs pour les zones dans le dashboard
ZONE_COLORS = ["#e3342f", "#f6993f", "#9561e2", "#38c172", "#6574cd", "#f66d9b"]


# --------------------------------------------------------------------------- #
# Utilitaires
# --------------------------------------------------------------------------- #
def log(msg, quiet=False):
    if not quiet:
        print(msg)


def load_config():
    """
    Sources de configuration, par priorité croissante :
      1. valeurs par défaut
      2. fichier netatmo_config.json (s'il existe)
      3. variables d'environnement (secrets GitHub Actions, etc.)
      4. fichier de refresh_token persisté (le plus récent, car il « tourne »)
    """
    cfg = dict(DEFAULTS)

    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})

    # Surcharges par variables d'environnement
    env = os.environ
    env_map = {
        "NETATMO_CLIENT_ID": "client_id",
        "NETATMO_CLIENT_SECRET": "client_secret",
        "NETATMO_REFRESH_TOKEN": "refresh_token",
        "NTFY_TOPIC": "ntfy_topic",
        "NTFY_SERVER": "ntfy_server",
        "DASHBOARD_URL": "dashboard_url",
    }
    for ek, ck in env_map.items():
        if env.get(ek):
            cfg[ck] = env[ek]
    if env.get("NETATMO_INDOOR_ROOMS"):
        cfg["indoor_rooms"] = [s.strip() for s in env["NETATMO_INDOOR_ROOMS"].split(",") if s.strip()]
    # Réglages numériques optionnels par variable d'environnement (pratique en CI sans fichier)
    num_map = {
        "NETATMO_RADIUS_KM": "radius_km",
        "NETATMO_HEAT_MAX_C": "heat_outdoor_max_c",
        "NETATMO_NOTIFY_MIN_INDOOR_C": "notify_min_indoor_c",
        "NETATMO_MARGIN_OPEN_C": "margin_open_c",
        "NETATMO_MARGIN_CLOSE_C": "margin_close_c",
    }
    for ek, ck in num_map.items():
        if env.get(ek):
            try:
                cfg[ck] = float(env[ek])
            except ValueError:
                pass

    # Rétro-compat : ancienne clé "indoor_room" (singulier)
    if cfg.get("indoor_room") and not cfg.get("indoor_rooms"):
        cfg["indoor_rooms"] = [cfg["indoor_room"]]

    # Le token persisté (qui a « tourné ») prime sur la graine (secret/config)
    if os.path.exists(TOKEN_FILE):
        try:
            saved = open(TOKEN_FILE, encoding="utf-8").read().strip()
            if saved:
                cfg["refresh_token"] = saved
        except Exception:
            pass

    for required in ("client_id", "client_secret", "refresh_token"):
        if not cfg.get(required):
            sys.exit(
                f"ERREUR : '{required}' manquant.\n"
                "  - En local : renseigne-le dans netatmo_config.json.\n"
                "  - Sur GitHub Actions : ajoute le secret correspondant "
                "(NETATMO_CLIENT_ID / NETATMO_CLIENT_SECRET / NETATMO_REFRESH_TOKEN)."
            )
    return cfg


def save_config(cfg):
    """Réécrit la config (on persiste le refresh_token qui tourne à chaque appel)."""
    out = {k: v for k, v in cfg.items() if not k.startswith("_")}
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG_PATH)


TRANSIENT_CODES = {500, 502, 503, 504}


def _urlopen_retry(req, attempts=4):
    """Ouvre la requête avec retries sur erreurs transitoires (5xx, réseau)."""
    ctx = ssl.create_default_context()
    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code in TRANSIENT_CODES and i < attempts - 1:
                time.sleep(2 * (i + 1))  # 2s, 4s, 6s...
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if i < attempts - 1:
                time.sleep(2 * (i + 1))
                continue
            raise
    raise last


def http_post_form(url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8")
    return _urlopen_retry(req)


def http_get(url, params, token):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + qs, method="GET")
    req.add_header("Authorization", "Bearer " + token)
    return _urlopen_retry(req)


def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def bbox_from_center(lat, lon, radius_km):
    dlat = radius_km / 111.0
    dlon = radius_km / (111.0 * max(0.1, math.cos(math.radians(lat))))
    return {
        "lat_ne": lat + dlat, "lon_ne": lon + dlon,
        "lat_sw": lat - dlat, "lon_sw": lon - dlon,
    }


# --------------------------------------------------------------------------- #
# Statistiques robustes
# --------------------------------------------------------------------------- #
def median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return None
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def robust_filter(stations, mad_k, min_band_c):
    """Écarte les valeurs aberrantes via médiane + MAD. Retourne (inliers, outliers, estimate)."""
    temps = [s["temp"] for s in stations]
    med = median(temps)
    if med is None:
        return [], [], None
    mad = median([abs(t - med) for t in temps]) or 0.0
    band = max(mad_k * 1.4826 * mad, min_band_c)
    inliers, outliers = [], []
    for s in stations:
        if abs(s["temp"] - med) <= band:
            inliers.append(s)
        else:
            s["delta_to_median"] = round(s["temp"] - med, 2)
            outliers.append(s)
    estimate = median([s["temp"] for s in inliers]) if inliers else med
    return inliers, outliers, estimate


# --------------------------------------------------------------------------- #
# Netatmo API
# --------------------------------------------------------------------------- #
def get_access_token(cfg):
    data = {
        "grant_type": "refresh_token",
        "refresh_token": cfg["refresh_token"],
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
    }
    try:
        res = http_post_form(TOKEN_URL, data)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        sys.exit(
            f"ERREUR d'authentification Netatmo (HTTP {e.code}).\n  {detail}\n"
            "  -> Vérifie client_id / client_secret / refresh_token, et le scope 'read_station'.\n"
            "  -> Si ça persiste, régénère un token sur https://dev.netatmo.com (token generator)."
        )
    if "access_token" not in res:
        sys.exit(f"ERREUR : réponse token inattendue : {res}")
    if res.get("refresh_token"):
        cfg["refresh_token"] = res["refresh_token"]
        # Persiste le token tournant : fichier (pour le cache CI) + config locale si présente
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(res["refresh_token"])
        except Exception:
            pass
        if os.path.exists(CONFIG_PATH):
            save_config(cfg)
    return res["access_token"]


def get_weather_rooms(token):
    """
    Zones depuis les capteurs météo (getstationsdata).
    Retourne (rooms, home_lat, home_lon). rooms : [{name, temp, type, source}].
    """
    rooms, home_lat, home_lon = [], None, None
    try:
        res = http_get(STATIONS_URL, {}, token)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        log(f"  [!] Capteurs météo indisponibles (HTTP {e.code} sur getstationsdata).\n"
            f"      {detail}\n"
            "      -> Souvent transitoire (réessaie), ou pas de station météo sur ce compte.",
            False)
        return rooms, home_lat, home_lon
    except urllib.error.URLError as e:
        log(f"  [!] Capteurs météo injoignables ({e.reason}).", False)
        return rooms, home_lat, home_lon
    devices = res.get("body", {}).get("devices", [])
    for dev in devices:
        loc = dev.get("place", {}).get("location", [None, None])  # [lon, lat]
        if home_lat is None and loc[1] is not None:
            home_lon, home_lat = loc[0], loc[1]
        main_temp = dev.get("dashboard_data", {}).get("Temperature")
        if main_temp is not None:
            rooms.append({"name": dev.get("module_name") or dev.get("station_name") or "Module principal",
                          "temp": main_temp, "type": "NAMain", "source": "Météo"})
        for mod in dev.get("modules", []):
            if mod.get("type") == "NAModule4":
                t = mod.get("dashboard_data", {}).get("Temperature")
                if t is not None:
                    rooms.append({"name": mod.get("module_name", "Module"),
                                  "temp": t, "type": "NAModule4", "source": "Météo"})
    return rooms, home_lat, home_lon


def get_energy_rooms(cfg, token):
    """
    Zones depuis l'API Energy : température mesurée par les vannes (NRV)
    et le thermostat (NATherm1), par pièce.
    Retourne (rooms, home_lat, home_lon). En cas d'erreur (scope manquant), ([], None, None).
    """
    try:
        hd = http_get(HOMESDATA_URL, {}, token)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        log(f"  [!] API Energy indisponible (HTTP {e.code}). Scope 'read_thermostat' manquant ?\n"
            f"      {detail}", cfg.get("_quiet"))
        return [], None, None

    homes = hd.get("body", {}).get("homes", [])
    if not homes:
        return [], None, None

    # Choix du logement
    home = None
    if cfg.get("home_id"):
        home = next((h for h in homes if str(h.get("id")) == str(cfg["home_id"])), None)
    if home is None:
        home = homes[0]

    # Localisation éventuelle fournie par l'API Energy : coordinates = [lon, lat]
    home_lat = home_lon = None
    coords = home.get("coordinates")
    if isinstance(coords, list) and len(coords) == 2:
        home_lon, home_lat = coords[0], coords[1]

    # Quelles pièces ont un module mesurant la température (NRV ou NATherm1) ?
    room_name = {r.get("id"): r.get("name", "Pièce") for r in home.get("rooms", [])}
    heated_room_ids = set()
    for m in home.get("modules", []):
        if m.get("type") in ("NRV", "NATherm1") and m.get("room_id") is not None:
            heated_room_ids.add(m["room_id"])

    # Températures temps réel
    try:
        hs = http_get(HOMESTATUS_URL, {"home_id": home.get("id")}, token)
    except urllib.error.HTTPError as e:
        log(f"  [!] homestatus indisponible (HTTP {e.code}).", cfg.get("_quiet"))
        return [], home_lat, home_lon

    rooms = []
    for r in hs.get("body", {}).get("home", {}).get("rooms", []):
        rid = r.get("id")
        temp = r.get("therm_measured_temperature")
        if temp is None or rid not in heated_room_ids:
            continue
        rooms.append({"name": room_name.get(rid, "Pièce"), "temp": float(temp),
                      "type": "Energy", "source": "Vanne/Thermostat"})
    return rooms, home_lat, home_lon


def get_indoor_rooms(cfg, token):
    """
    Fusionne les zones météo et Energy (vannes/thermostat).
    Retourne (rooms_all, home_lat, home_lon).
    """
    rooms_all, home_lat, home_lon = [], None, None

    if cfg.get("use_weather", True):
        wr, wlat, wlon = get_weather_rooms(token)
        rooms_all.extend(wr)
        if wlat is not None:
            home_lat, home_lon = wlat, wlon

    if cfg.get("use_thermostats", True):
        er, elat, elon = get_energy_rooms(cfg, token)
        # Désambiguïse les noms identiques entre les deux sources
        existing = {r["name"].strip().lower() for r in rooms_all}
        for r in er:
            if r["name"].strip().lower() in existing:
                r["name"] = f"{r['name']} (vanne)"
            rooms_all.append(r)
        if home_lat is None and elat is not None:
            home_lat, home_lon = elat, elon

    # Position forcée par la config si besoin
    if cfg.get("home_lat") is not None and cfg.get("home_lon") is not None:
        home_lat, home_lon = cfg["home_lat"], cfg["home_lon"]

    if not rooms_all:
        sys.exit("ERREUR : aucune température intérieure exploitable.\n"
                 "  - Capteurs météo hors-ligne ? Vannes/thermostat absents ?\n"
                 "  - Pour les vannes/thermostat, le token doit avoir le scope 'read_thermostat'.")
    if home_lat is None:
        sys.exit("ERREUR : localisation introuvable. Renseigne 'home_lat' et 'home_lon' "
                 "dans netatmo_config.json (l'API Energy ne fournit pas toujours les coordonnées).")
    return rooms_all, home_lat, home_lon


def select_rooms(cfg, rooms_all):
    """Filtre les zones à suivre selon cfg['indoor_rooms']. Vide -> toutes."""
    wanted = cfg.get("indoor_rooms") or []
    if not wanted:
        return list(rooms_all)
    by_name = {r["name"].strip().lower(): r for r in rooms_all}
    chosen, missing = [], []
    for w in wanted:
        r = by_name.get(str(w).strip().lower())
        if r:
            chosen.append(r)
        else:
            missing.append(w)
    if missing:
        dispo = ", ".join(f"'{r['name']}'" for r in rooms_all)
        log(f"  [!] Zones introuvables : {missing}. Disponibles : {dispo}", cfg.get("_quiet"))
    if not chosen:
        log("  [!] Aucune zone demandée trouvée -> on prend toutes les zones.", cfg.get("_quiet"))
        return list(rooms_all)
    return chosen


def get_public_outdoor(cfg, token, home_lat, home_lon):
    box = bbox_from_center(home_lat, home_lon, cfg["radius_km"])
    params = {
        "lat_ne": box["lat_ne"], "lon_ne": box["lon_ne"],
        "lat_sw": box["lat_sw"], "lon_sw": box["lon_sw"],
        "required_data": "temperature", "filter": "true",
    }
    res = http_get(PUBLIC_URL, params, token)
    body = res.get("body", [])
    now = time.time()
    max_age = cfg["max_age_minutes"] * 60

    stations = []
    for item in body:
        loc = item.get("place", {}).get("location", [None, None])  # [lon, lat]
        slon, slat = loc[0], loc[1]
        if slat is None or slon is None:
            continue
        dist = haversine_km(home_lat, home_lon, slat, slon)
        if dist > cfg["radius_km"]:
            continue
        temp, ts = None, None
        for mac, meas in item.get("measures", {}).items():
            types = meas.get("type")
            res_map = meas.get("res")
            if not types or not res_map or "temperature" not in types:
                continue
            ti = types.index("temperature")
            last_ts = max(res_map.keys(), key=lambda k: int(k))
            vals = res_map[last_ts]
            if ti < len(vals):
                temp = vals[ti]
                ts = int(last_ts)
            break
        if temp is None or ts is None or now - ts > max_age:
            continue
        stations.append({
            "temp": round(float(temp), 2), "dist_km": round(dist, 3),
            "age_min": round((now - ts) / 60.0, 1), "lat": slat, "lon": slon,
        })
    return stations


# --------------------------------------------------------------------------- #
# Décision ouvrir / fermer (avec hystérésis), par zone
# --------------------------------------------------------------------------- #
def decide(indoor, outdoor, cfg, prev_state):
    delta = outdoor - indoor  # négatif = plus frais dehors
    open_threshold = -cfg["margin_open_c"]
    close_threshold = -cfg["margin_close_c"]

    if delta <= open_threshold:
        state = "open"
    elif delta >= close_threshold:
        state = "closed"
    else:
        state = prev_state if prev_state in ("open", "closed") else "neutral"

    changed = (state != prev_state)
    if state == "open":
        label, advice = "OUVRIR", f"{abs(delta):.1f}°C plus frais dehors → ouvre pour rafraîchir."
    elif state == "closed":
        if delta >= 0:
            label, advice = "GARDER FERMÉ", f"{delta:.1f}°C plus chaud dehors → garde fermé."
        else:
            label, advice = "FERMER", "Plus assez frais dehors → referme pour garder le frais."
    else:
        label, advice = "NEUTRE", "Écart faible, pas d'action nette."
    return {"state": state, "label": label, "advice": advice,
            "changed": changed, "delta": round(delta, 2)}


# --------------------------------------------------------------------------- #
# Historique (format large : 1 colonne par zone)
# --------------------------------------------------------------------------- #
def append_history(ts, outdoor, zones, max_rows):
    fields = ["timestamp", "outdoor"] + [z["name"] for z in zones]
    row = {"timestamp": ts, "outdoor": round(outdoor, 2)}
    for z in zones:
        row[z["name"]] = round(z["temp"], 2)

    # Si l'en-tête existant ne correspond plus (zones changées), on archive l'ancien.
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            existing_header = f.readline().strip()
        if existing_header and existing_header != ",".join(fields):
            os.replace(HISTORY_PATH, HISTORY_PATH.replace(".csv", f"_{int(time.time())}.csv"))

    new_file = not os.path.exists(HISTORY_PATH)
    with open(HISTORY_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            w.writeheader()
        w.writerow(row)

    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) - 1 > max_rows:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                f.write(lines[0])
                f.writelines(lines[-max_rows:])
    except Exception:
        pass


def read_history():
    if not os.path.exists(HISTORY_PATH):
        return [], []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = reader.fieldnames or []
    return rows, fields


def recent_outdoor_max(lookback_hours):
    """Max de la température extérieure sur les N dernières heures (depuis l'historique)."""
    rows, _ = read_history()
    if not rows:
        return None
    cutoff = datetime.now() - __import__("datetime").timedelta(hours=lookback_hours)
    vals = []
    for r in rows:
        try:
            t = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
            if t >= cutoff:
                vals.append(float(r["outdoor"]))
        except Exception:
            continue
    return max(vals) if vals else None


def notify_macos(title, message):
    try:
        msg = message.replace('"', "'")
        ttl = title.replace('"', "'")
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{msg}" with title "{ttl}" sound name "Glass"'],
            check=False, capture_output=True,
        )
    except Exception:
        pass


def _ascii(s):
    """Replie les accents en ASCII (les en-têtes HTTP ntfy n'aiment pas l'UTF-8)."""
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii") or "Netatmo"


def notify_ntfy(cfg, title, message, tag="house"):
    """Envoie une notification push via ntfy.sh (corps en UTF-8, titre en ASCII)."""
    topic = cfg.get("ntfy_topic")
    if not topic:
        return False
    server = (cfg.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    url = f"{server}/{topic}"
    req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
    req.add_header("Title", _ascii(title))
    req.add_header("Tags", tag)
    req.add_header("Priority", "default")
    click = cfg.get("dashboard_url")
    if click:
        req.add_header("Click", click)  # ouvre le dashboard au tap sur la notif
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=15, context=ctx):
            return True
    except Exception as e:
        log(f"  [!] Échec envoi ntfy : {e}", cfg.get("_quiet"))
        return False


def notify(cfg, title, message, tag="house"):
    """Dispatch : ntfy si configuré (iPhone), sinon notification macOS locale."""
    if cfg.get("ntfy_topic"):
        return notify_ntfy(cfg, title, message, tag)
    notify_macos(title, message)
    return True


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
def render_dashboard(ctx):
    rows, fields = read_history()
    rows = rows[-300:]
    labels = [r["timestamp"][11:16] for r in rows]
    outdoor_series = [r.get("outdoor", "") for r in rows]

    zones = ctx["zones"]  # liste de dicts {name, temp, delta, state, label, advice}
    # Séries d'historique par zone (si la colonne existe)
    zone_series = {}
    for z in zones:
        if z["name"] in fields:
            zone_series[z["name"]] = [r.get(z["name"], "") for r in rows]

    state_color = {"open": "#1f9d55", "closed": "#e3342f", "neutral": "#8795a1"}

    # Cartes par zone
    zone_cards = ""
    for i, z in enumerate(zones):
        col = state_color.get(z["state"], "#8795a1")
        dcol = "#1f9d55" if z["delta"] < 0 else "#e3342f"
        zone_cards += f"""
      <div class="card zone">
        <div class="zone-head">
          <span class="dot" style="background:{ZONE_COLORS[i % len(ZONE_COLORS)]}"></span>
          <b>{z['name']}</b>
        </div>
        <div class="row">
          <div><div class="k">Intérieur</div><div class="v">{z['temp']:.1f}°C</div></div>
          <div><div class="k">Écart / ext.</div><div class="v" style="color:{dcol}">{z['delta']:+.1f}°C</div></div>
        </div>
        <span class="badge" style="background:{col}">{z['label']}</span>
        <div class="advice">{z['advice']}</div>
      </div>"""

    outliers_rows = "".join(
        f"<tr><td>{o['temp']:.1f}°C</td><td>{o['dist_km']:.2f} km</td>"
        f"<td>{o.get('delta_to_median', 0):+.1f}°C</td></tr>"
        for o in ctx["outliers"]
    ) or "<tr><td colspan='3' class='muted'>Aucune valeur écartée</td></tr>"

    # Datasets chart
    datasets = [f"""{{ label:'Extérieur', data: {json.dumps(outdoor_series)}.map(Number),
        borderColor:'#3490dc', borderWidth:3, tension:.3, pointRadius:0 }}"""]
    for i, z in enumerate(zones):
        if z["name"] in zone_series:
            datasets.append(f"""{{ label:{json.dumps(z['name'])},
              data: {json.dumps(zone_series[z['name']])}.map(Number),
              borderColor:'{ZONE_COLORS[i % len(ZONE_COLORS)]}', borderWidth:2, tension:.3, pointRadius:0 }}""")
    datasets_js = ",\n    ".join(datasets)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zones vs Extérieur — Netatmo</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
         margin:0; padding:24px; background:#f5f6fa; color:#1a1a1a; }}
  @media (prefers-color-scheme: dark) {{ body{{background:#16181d;color:#e6e6e6;}}
         .card{{background:#1f232b !important;}} .muted{{color:#8a93a2;}} }}
  .wrap {{ max-width:960px; margin:0 auto; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:#8795a1; font-size:13px; margin-bottom:20px; }}
  .card {{ background:#fff; border-radius:14px; padding:18px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .ext {{ display:flex; align-items:center; gap:14px; margin-bottom:18px; }}
  .ext .v {{ font-size:34px; font-weight:700; }}
  .zones {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; margin-bottom:20px; }}
  .zone-head {{ display:flex; align-items:center; gap:8px; font-size:15px; margin-bottom:12px; }}
  .dot {{ width:11px; height:11px; border-radius:50%; display:inline-block; }}
  .zone .row {{ display:flex; gap:24px; margin-bottom:12px; }}
  .k {{ font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:#8795a1; }}
  .v {{ font-size:26px; font-weight:700; margin-top:2px; }}
  .badge {{ display:inline-block; padding:7px 14px; border-radius:999px; color:#fff; font-weight:700; font-size:14px; }}
  .advice {{ margin-top:9px; font-size:13px; color:#5a6573; }}
  @media (prefers-color-scheme: dark){{ .advice{{color:#9aa4b2;}} }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid rgba(128,128,128,.18); }}
  .muted {{ color:#8795a1; }}
  canvas {{ max-height:320px; }}
  .foot {{ margin-top:18px; font-size:12px; color:#8795a1; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Zones intérieures vs Extérieur — Netatmo</h1>
  <div class="sub">{ctx['n_inliers']} stations publiques retenues dans un rayon de
     {ctx['radius_km']:.1f} km · maj {ctx['timestamp']}</div>

  <div class="card" style="margin-bottom:18px; border-left:5px solid {'#e3342f' if ctx['heat_mode'] else '#8795a1'};">
    <b>Mode forte chaleur : {'ACTIF — notifications activées' if ctx['heat_mode'] else 'inactif — notifications en veille'}</b>
    <div class="muted" style="font-size:13px; margin-top:4px;">
      Max extérieur {ctx['recent_max']:.1f}°C sur {ctx['heat_lookback_hours']}h ·
      seuil de déclenchement {ctx['heat_threshold']:.0f}°C
    </div>
  </div>

  <div class="card ext">
    <div>
      <div class="k">Extérieur (médiane filtrée)</div>
      <div class="v">{ctx['outdoor']:.1f}°C</div>
    </div>
    <div class="muted" style="font-size:13px">sur {ctx['n_inliers']} stations · {ctx['n_outliers']} écartée(s)</div>
  </div>

  <div class="zones">{zone_cards}
  </div>

  <div class="card" style="margin-bottom:20px;">
    <div class="k" style="margin-bottom:10px;">Historique</div>
    <canvas id="chart"></canvas>
  </div>

  <div class="card">
    <div class="k" style="margin-bottom:10px;">Valeurs extérieures écartées ({ctx['n_outliers']})
       <span class="muted">— ex. capteurs au soleil</span></div>
    <table>
      <tr><th>Température</th><th>Distance</th><th>Écart à la médiane</th></tr>
      {outliers_rows}
    </table>
  </div>

  <div class="foot">Filtrage : médiane ± max({ctx['mad_k']}×MAD, {ctx['min_band_c']}°C).
     Stations &gt; {ctx['max_age_minutes']} min ou hors rayon ignorées.
     Page générée par netatmo_compare.py — relance le script pour rafraîchir.</div>
</div>

<script>
new Chart(document.getElementById('chart'), {{
  type:'line',
  data:{{ labels:{json.dumps(labels)}, datasets:[
    {datasets_js}
  ]}},
  options:{{ responsive:true, interaction:{{mode:'index',intersect:false}},
    scales:{{ y:{{ title:{{display:true,text:'°C'}} }} }} }}
}});
</script>
</body>
</html>
"""
    with open(DASHBOARD_PATH, "w", encoding="utf-8") as f:
        f.write(html)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Comparaison zones intérieures / extérieur Netatmo.")
    ap.add_argument("--notify", action="store_true", help="Notification macOS si un état change.")
    ap.add_argument("--open-dashboard", action="store_true", help="Ouvre le dashboard.")
    ap.add_argument("--list-rooms", action="store_true", help="Liste les zones détectées et quitte.")
    ap.add_argument("--test-notify", action="store_true",
                    help="Envoie une notification macOS de test et quitte (vérifie que les alertes marchent).")
    ap.add_argument("--quiet", action="store_true", help="Pas de sortie console.")
    args = ap.parse_args()

    if args.test_notify:
        # Config minimale pour la notif (sans exiger les identifiants Netatmo)
        tcfg = {"ntfy_topic": None, "ntfy_server": "https://ntfy.sh",
                "dashboard_url": None, "_quiet": False}
        if os.path.exists(CONFIG_PATH):
            try:
                fc = json.load(open(CONFIG_PATH, encoding="utf-8"))
                tcfg["ntfy_topic"] = fc.get("ntfy_topic")
                tcfg["ntfy_server"] = fc.get("ntfy_server") or tcfg["ntfy_server"]
                tcfg["dashboard_url"] = fc.get("dashboard_url")
            except Exception:
                pass
        if os.environ.get("NTFY_TOPIC"):
            tcfg["ntfy_topic"] = os.environ["NTFY_TOPIC"]
        if os.environ.get("NTFY_SERVER"):
            tcfg["ntfy_server"] = os.environ["NTFY_SERVER"]
        if os.environ.get("DASHBOARD_URL"):
            tcfg["dashboard_url"] = os.environ["DASHBOARD_URL"]
        ok = notify(tcfg, "Netatmo — test",
                    "Souplex 26.8°C · il fait plus frais dehors → OUVRIR. (test)")
        canal = f"ntfy ({tcfg['ntfy_topic']})" if tcfg["ntfy_topic"] else "macOS"
        print(f"Notification de test envoyée via {canal}." + ("" if ok else " (échec, voir ci-dessus)"))
        if not tcfg["ntfy_topic"]:
            print("  Si tu ne la vois pas : Réglages Système > Notifications > Terminal > Autoriser.")
        else:
            print("  Vérifie que l'app ntfy sur iPhone est abonnée à ce topic.")
        return

    cfg = load_config()
    cfg["_quiet"] = args.quiet

    token = get_access_token(cfg)
    rooms_all, home_lat, home_lon = get_indoor_rooms(cfg, token)

    if args.list_rooms:
        print("Zones intérieures détectées :")
        for r in rooms_all:
            print(f"  - {r['name']:<24} {r['temp']:.1f}°C   [{r.get('source','?')}]")
        print("\nMets les noms voulus dans 'indoor_rooms' de netatmo_config.json.")
        return

    zones = select_rooms(cfg, rooms_all)
    stations = get_public_outdoor(cfg, token, home_lat, home_lon)

    if len(stations) < cfg["min_stations"]:
        log(f"  [!] Seulement {len(stations)} station(s) publique(s) valide(s) "
            f"(min {cfg['min_stations']}). Augmente radius_km.", args.quiet)

    inliers, outliers, outdoor = robust_filter(stations, cfg["mad_k"], cfg["min_band_c"])
    if outdoor is None:
        sys.exit("ERREUR : aucune température extérieure publique exploitable. Augmente radius_km.")

    # États précédents par zone
    prev_states = {}
    if os.path.exists(STATE_PATH):
        try:
            prev_states = json.load(open(STATE_PATH)).get("zones", {})
        except Exception:
            pass

    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    # Décision par zone
    changes = []
    for z in zones:
        prev = prev_states.get(z["name"], "neutral")
        d = decide(z["temp"], outdoor, cfg, prev)
        z.update(d)
        if d["changed"] and d["state"] in ("open", "closed"):
            changes.append(z)

    # Persiste les états
    json.dump({"timestamp": ts, "zones": {z["name"]: z["state"] for z in zones}},
              open(STATE_PATH, "w"))

    # Historique (avant le calcul du max récent, pour inclure le relevé courant)
    append_history(ts, outdoor, zones, cfg["history_max_rows"])

    # MODE FORTE CHALEUR : l'extérieur a-t-il atteint le seuil récemment ?
    rmax = recent_outdoor_max(cfg["heat_lookback_hours"])
    if rmax is None:
        rmax = outdoor
    heat_mode = rmax >= cfg["heat_outdoor_max_c"]

    render_dashboard({
        "timestamp": ts, "outdoor": outdoor, "zones": zones,
        "n_inliers": len(inliers), "n_outliers": len(outliers), "outliers": outliers,
        "radius_km": cfg["radius_km"], "mad_k": cfg["mad_k"],
        "min_band_c": cfg["min_band_c"], "max_age_minutes": cfg["max_age_minutes"],
        "heat_mode": heat_mode, "recent_max": rmax,
        "heat_threshold": cfg["heat_outdoor_max_c"], "heat_lookback_hours": cfg["heat_lookback_hours"],
    })

    if not args.quiet:
        print(f"\n  {ts}")
        print(f"  Extérieur (méd.) : {outdoor:.1f}°C   (sur {len(inliers)} stations, "
              f"{len(outliers)} écartée(s))")
        for z in zones:
            print(f"  • {z['name']:<22} {z['temp']:.1f}°C  écart {z['delta']:+.1f}°C  "
                  f">>> {z['label']} — {z['advice']}")
        if changes:
            print("  (changement(s) d'état : " + ", ".join(z["name"] for z in changes) + ")")
        hm = "ACTIF" if heat_mode else "inactif"
        print(f"  Mode forte chaleur : {hm}  (max ext. {rmax:.1f}°C sur {cfg['heat_lookback_hours']}h, "
              f"seuil {cfg['heat_outdoor_max_c']:.0f}°C)")
        print(f"\n  Dashboard : {DASHBOARD_PATH}\n")

    # Notifications uniquement en MODE FORTE CHALEUR.
    # Dans ce mode : "OUVRIR" si la pièce dépasse notify_min_indoor_c ; "FERMER" toujours.
    seuil = cfg["notify_min_indoor_c"]
    notif_zones = []
    if heat_mode:
        notif_zones = [z for z in changes
                       if z["state"] == "closed" or z["temp"] >= seuil]
    if args.notify and notif_zones:
        if len(notif_zones) == 1:
            z = notif_zones[0]
            extra = f" ({z['temp']:.1f}°C)" if z["state"] == "open" else ""
            notify(cfg, f"{z['name']} — {z['label']}{extra}", z["advice"])
        else:
            body = " · ".join(f"{z['name']} : {z['label']}" for z in notif_zones)
            notify(cfg, "Maison — changements", body)

    if args.open_dashboard:
        try:
            subprocess.run(["open", DASHBOARD_PATH], check=False)
        except Exception:
            pass


if __name__ == "__main__":
    main()
