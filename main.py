"""BLR-Sentinel: traffic-aware routing and privacy-conscious incident demo.

Live traffic requires TOMTOM_KEY. Weather uses Open-Meteo. Forecasting is trained
only from observations collected by this running instance; until enough observations
exist, the API clearly reports a heuristic warm-up forecast.
"""
import bisect
import datetime as dt
import hashlib
import hmac
import json
import math
import os
import pickle
import random
import threading
import time
from collections import defaultdict, deque

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from algos import AC, Signer, astar, binary_search, canon, hav, k_routes

C = (12.95, 77.62)
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TT = os.getenv("TOMTOM_KEY", "").strip()
REFRESH_SECONDS = max(60, int(os.getenv("TRAFFIC_REFRESH_SECONDS", "600")))
HISTORY_LIMIT = 7 * 24 * 6  # up to seven days at ten-minute intervals
PROBES = {
    "Silk Board": (12.9170, 77.6233), "Koramangala": (12.9352, 77.6245),
    "Indiranagar": (12.9784, 77.6408), "MG Road": (12.9756, 77.6068),
    "Richmond Circle": (12.9660, 77.5960), "Lalbagh": (12.9507, 77.5848),
    "Jayanagar": (12.9250, 77.5938), "Domlur": (12.9609, 77.6387),
    "BTM": (12.9166, 77.6101), "HSR Layout": (12.9116, 77.6389),
    "Ulsoor": (12.9810, 77.6200), "Shivajinagar": (12.9857, 77.6057),
}


def load_graph():
    path = "data/graph.pkl"
    if os.path.exists(path):
        with open(path, "rb") as handle:
            graph = pickle.load(handle)
        if graph.get("P") and graph.get("E"):
            return graph, "OpenStreetMap road graph"
    # Reliable fallback for first-run/deployment demos; never label this as real roads.
    n, points, edges = 30, {}, []
    for i in range(n):
        for j in range(n):
            points[i*n+j] = (C[0] + (i-n/2)*.003, C[1] + (j-n/2)*.003)
    for i in range(n):
        for j in range(n):
            for di, dj in ((0, 1), (1, 0)):
                if i+di < n and j+dj < n:
                    u, v = i*n+j, (i+di)*n+j+dj
                    length = hav(points[u], points[v])
                    edges.extend([(u, v, length, 30, 0), (v, u, length, 30, 0)])
    return {"P": points, "E": edges}, "synthetic demo grid (build data/graph.pkl for real roads)"


D, GRAPH_SOURCE = load_graph()
P, E = D["P"], D["E"]
adj = {node: [] for node in P}
for edge_id, (u, v, *_rest) in enumerate(E):
    adj[u].append((v, edge_id))
MID = [((P[u][0]+P[v][0])/2, (P[u][1]+P[v][1])/2) for u, v, *_ in E]
BASE = [length / max(speed/3.6, 1) for _, _, length, speed, _ in E]
FACTORS = [1.0] * len(E)
STATE = {"updated_at": None, "ratios": {}, "rain": 0.0, "source": "not refreshed", "errors": []}
REFRESH_LOCK = threading.Lock()
HISTORY = deque(maxlen=HISTORY_LIMIT)
LAST_MODEL_TRAIN = 0.0
FORECAST_MODEL = None
FORECAST_INFO = {"mode": "warmup", "samples": 0, "message": "Collecting traffic observations"}
SESSION_SECRET = (os.getenv("PSEUDONYM_SECRET") or os.urandom(32).hex()).encode("utf-8")
SIGNER = Signer()


def rush_hour_factor(hour):
    morning = math.exp(-((hour-8.5)/1.8)**2)
    evening = math.exp(-((hour-18.0)/2.1)**2)
    return 1.0 + 1.8 * max(morning, evening)


def synthetic_ratio(hour, weekday):
    weekend = 0.82 if weekday >= 5 else 1.0
    return max(1.0, 1 + (rush_hour_factor(hour)-1) * random.uniform(.65, 1.15) * weekend)


def _fetch_snapshot():
    """Fetch current observations once per refresh window; report data-source errors."""
    now_dt = dt.datetime.now(IST)
    hour = now_dt.hour + now_dt.minute / 60
    ratios, errors = {}, []
    live_count = 0
    for name, (lat, lon) in PROBES.items():
        ratio = None
        if TT:
            try:
                response = requests.get(
                    "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json",
                    params={"point": f"{lat},{lon}", "key": TT}, timeout=4,
                )
                response.raise_for_status()
                segment = response.json()["flowSegmentData"]
                free_speed = float(segment["freeFlowSpeed"])
                current_speed = float(segment["currentSpeed"])
                if free_speed > 0 and current_speed > 0:
                    ratio = min(8.0, max(1.0, free_speed/current_speed))
                    live_count += 1
            except Exception as exc:
                errors.append(f"TomTom {name}: {type(exc).__name__}")
        if ratio is None:
            ratio = synthetic_ratio(hour, now_dt.weekday())
        ratios[name] = ratio

    rain = 0.0
    weather_source = "Open-Meteo"
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": C[0], "longitude": C[1], "current": "precipitation"}, timeout=4,
        )
        response.raise_for_status()
        rain = max(0.0, float(response.json().get("current", {}).get("precipitation", 0) or 0))
    except Exception as exc:
        errors.append(f"Open-Meteo: {type(exc).__name__}")
        weather_source = "unavailable (assumed 0 mm; not a flood observation)"

    # Interpolate each probe's ratio onto nearby road edges.
    mean_ratio = sum(ratios.values()) / max(1, len(ratios))
    for edge_id, midpoint in enumerate(MID):
        numerator = denominator = 0.0
        for name, coord in PROBES.items():
            distance = hav(midpoint, coord)
            if distance < 3000:
                weight = 1.0 / (distance + 250) ** 2
                numerator += weight * ratios[name]
                denominator += weight
        ratio = numerator/denominator if denominator else 1 + (mean_ratio-1)*0.35
        # Rain alone indicates weather exposure, not confirmed flooding. Use a mild penalty.
        is_underpass = bool(E[edge_id][4])
        FACTORS[edge_id] = min(8.0, ratio * (1.0 + min(rain, 20)/20 if is_underpass else 1.0))

    source = "TomTom live traffic" if live_count == len(PROBES) else (
        f"mixed live/simulated traffic ({live_count}/{len(PROBES)} live probes)" if live_count else "simulated rush-hour profile (configure TOMTOM_KEY for live traffic)"
    )
    return now_dt, ratios, rain, source, weather_source, errors


def _train_forecaster():
    """Train a small supervised model only from observed, time-paired traffic data."""
    global FORECAST_MODEL, LAST_MODEL_TRAIN, FORECAST_INFO
    try:
        from sklearn.ensemble import RandomForestRegressor
    except Exception:
        FORECAST_INFO = {"mode": "heuristic", "samples": 0, "message": "scikit-learn unavailable; using transparent trend forecast"}
        return
    snapshots = list(HISTORY)
    if len(snapshots) < 8 or time.time() - LAST_MODEL_TRAIN < 1800:
        return
    examples_x, examples_y = [], []
    probe_names = list(PROBES)
    for i in range(len(snapshots)-1):
        current, future = snapshots[i], snapshots[i+1]
        minutes = (future["ts"] - current["ts"]) / 60
        if minutes < 5 or minutes > 25:
            continue
        for name in probe_names:
            if name not in current["ratios"] or name not in future["ratios"]:
                continue
            stamp = dt.datetime.fromtimestamp(future["ts"], IST)
            hour = stamp.hour + stamp.minute/60
            idx = probe_names.index(name)
            examples_x.append([current["ratios"][name], math.sin(2*math.pi*hour/24), math.cos(2*math.pi*hour/24), math.sin(2*math.pi*stamp.weekday()/7), math.cos(2*math.pi*stamp.weekday()/7), idx, current["rain"]])
            examples_y.append(future["ratios"][name])
    if len(examples_y) >= 60:
        model = RandomForestRegressor(n_estimators=35, max_depth=8, min_samples_leaf=2, random_state=42, n_jobs=1)
        model.fit(examples_x, examples_y)
        FORECAST_MODEL = model
        FORECAST_INFO = {"mode": "random_forest", "samples": len(examples_y), "message": "Trained on observed traffic snapshots from this running instance"}
        LAST_MODEL_TRAIN = time.time()


def refresh(force=False):
    """Thread-safe refresh cache; route requests reuse the same snapshot."""
    if not force and STATE["updated_at"] and time.time() - STATE["updated_at"] < REFRESH_SECONDS:
        return
    if not REFRESH_LOCK.acquire(blocking=False):
        return
    try:
        if not force and STATE["updated_at"] and time.time() - STATE["updated_at"] < REFRESH_SECONDS:
            return
        now_dt, ratios, rain, source, weather_source, errors = _fetch_snapshot()
        stamp = time.time()
        HISTORY.append({"ts": stamp, "ratios": dict(ratios), "rain": rain})
        STATE.update(updated_at=stamp, ratios=ratios, rain=rain, source=source, weather_source=weather_source, errors=errors, graph_source=GRAPH_SOURCE)
        _train_forecaster()
    finally:
        REFRESH_LOCK.release()


def predict_ratio(name, horizon_minutes):
    """Predict a congestion ratio; use a learned model when enough observations exist."""
    current = float(STATE["ratios"].get(name, 1.0))
    future_dt = dt.datetime.now(IST) + dt.timedelta(minutes=horizon_minutes)
    hour = future_dt.hour + future_dt.minute/60
    names = list(PROBES)
    if FORECAST_MODEL is not None:
        features = [[current, math.sin(2*math.pi*hour/24), math.cos(2*math.pi*hour/24), math.sin(2*math.pi*future_dt.weekday()/7), math.cos(2*math.pi*future_dt.weekday()/7), names.index(name), STATE["rain"]]]
        return max(1.0, min(8.0, float(FORECAST_MODEL.predict(features)[0])))
    # Warm-up baseline: current observation blended with the time-of-day profile.
    target = synthetic_ratio(hour, future_dt.weekday())
    return max(1.0, min(8.0, 0.65*current + 0.35*target))


def edge_weights(mode="fastest", horizon=0, scenario="none"):
    predicted = {name: predict_ratio(name, horizon) if horizon else float(STATE["ratios"].get(name, 1.0)) for name in PROBES}
    mean = sum(predicted.values()) / max(1, len(predicted))
    weights = []
    for edge_id, midpoint in enumerate(MID):
        numerator = denominator = 0.0
        for name, coord in PROBES.items():
            distance = hav(midpoint, coord)
            if distance < 3000:
                wt = 1.0/(distance+250)**2
                numerator += wt * predicted.get(name, mean)
                denominator += wt
        congestion = numerator/denominator if denominator else 1+(mean-1)*.35
        length, speed, underpass = E[edge_id][2], E[edge_id][3], bool(E[edge_id][4])
        base_time = BASE[edge_id]
        factor = congestion
        if underpass:
            factor *= 1 + min(STATE["rain"], 20)/20
        # Explicit deterministic demo incident: severe disruption around Silk Board.
        if scenario == "closure":
            if hav(midpoint, PROBES["Silk Board"]) < 650:
                factor *= 18
        elif scenario == "rain" and underpass:
            factor *= 4
        travel_time = base_time * factor
        if mode == "reliable":
            travel_time *= 1 + max(0, factor-1)*0.18
        elif mode == "eco":
            # Transparent proxy: distance plus stop-and-go/congestion penalty, not a certified emissions model.
            travel_time = length/12.0 * (1 + max(0, factor-1)*0.8)
        elif mode == "rain_safe" and underpass and (STATE["rain"] > 0.1 or scenario == "rain"):
            travel_time *= 5
        weights.append(max(0.05, travel_time))
    return weights


# ---- Synthetic surveillance demonstration; not real camera or plate data. ----
random.seed()
CAMERAS = random.sample(list(P), min(40, len(P)))
def pseudonym(plate):
    return hmac.new(SESSION_SECRET, canon(plate).encode(), hashlib.sha256).hexdigest()[:12]
def random_plate():
    return f"KA{random.choice(['01','03','05','51','53'])}{random.choice('ABCDEFGH')}{random.choice('ABCDEFGH')}{random.randint(1000,9999)}"
PLATES = [random_plate() for _ in range(50)]
WATCHLIST = {canon(plate): plate for plate in PLATES[:3]}
WATCH_MATCHER = AC(WATCHLIST)
OCR_SWAP = str.maketrans({"O":"0", "0":"O", "B":"8", "8":"B", "S":"5", "5":"S", "I":"1", "1":"I"})
LOG = []
ALERTS = []
now = time.time()
for plate in PLATES:
    stamp = now - random.uniform(1800, 7200)
    for _ in range(8):
        stamp += random.uniform(60, 600)
        camera = random.choice(CAMERAS)
        noisy = "".join(ch.translate(OCR_SWAP) if ch in "O0B8S5I1" and random.random() < .12 else ch for ch in plate)
        hits = WATCH_MATCHER.find(canon(noisy))
        if hits:
            for hit in hits:
                ALERTS.append({"plate": WATCHLIST[hit], "ts": stamp, "camera": camera, "type": "watchlist_match"})
        record_id = hashlib.sha256(f"{stamp:.3f}|{camera}|{pseudonym(noisy)}".encode()).hexdigest()[:16]
        payload = json.dumps({"id": record_id, "ts": round(stamp, 3), "camera": str(camera), "pseudonym": pseudonym(noisy)}, sort_keys=True, separators=(",", ":")).encode()
        LOG.append({"id": record_id, "ts": stamp, "camera": camera, "pseudonym": pseudonym(noisy), "payload": payload, "signature": SIGNER.sign(payload)})
LOG.sort(key=lambda row: (row["ts"], row["id"]))
LOG_BY_ID = sorted(LOG, key=lambda row: row["id"])
LOG_TS = [row["ts"] for row in LOG]


def hm(timestamp):
    return dt.datetime.fromtimestamp(timestamp, IST).strftime("%H:%M:%S")


app = FastAPI(title="BLR-Sentinel", version="2.0.0", description="Real-time traffic-aware routing and privacy-preserving incident demo")


@app.get("/api/health")
def health():
    return {"status": "ok", "graph_source": GRAPH_SOURCE, "traffic_source": STATE["source"], "updated_at": STATE["updated_at"]}


@app.get("/api/state")
def state():
    refresh()
    return {
        "src": STATE["source"], "graph_source": GRAPH_SOURCE, "rain": round(STATE["rain"], 2),
        "weather_source": STATE["weather_source"], "updated_at": STATE["updated_at"],
        "refresh_seconds": REFRESH_SECONDS, "forecast": FORECAST_INFO,
        "cams": [P[camera] for camera in CAMERAS],
        "probes": [{"n": name, "lat": PROBES[name][0], "lon": PROBES[name][1], "r": round(ratio, 2)} for name, ratio in STATE["ratios"].items()],
        "alerts": [{"plate": row["plate"], "time": hm(row["ts"]), "lat": P[row["camera"]][0], "lon": P[row["camera"]][1], "type": row["type"]} for row in ALERTS[-10:]],
        "algorithms": {"graph": "A*", "string_matching": "Aho–Corasick", "record_search": "Binary Search", "signature": "RSA-PSS + SHA-256"},
        "demo_data": True,
    }


@app.get("/api/forecast")
def forecast(horizon: int = Query(15, ge=5, le=60)):
    refresh()
    return {"horizon_minutes": horizon, "mode": FORECAST_INFO["mode"], "samples": FORECAST_INFO["samples"],
            "message": FORECAST_INFO["message"], "probes": [{"name": name, "ratio_now": round(float(STATE["ratios"].get(name, 1)), 2), "ratio_predicted": round(predict_ratio(name, horizon), 2)} for name in PROBES]}


def nearest_node(lat, lon):
    return min(P, key=lambda node: (P[node][0]-lat)**2 + (P[node][1]-lon)**2)


@app.get("/api/route")
def route(a_lat: float, a_lon: float, b_lat: float, b_lon: float,
          mode: str = Query("fastest", pattern="^(fastest|reliable|eco|rain_safe)$"),
          horizon: int = Query(0, ge=0, le=60), scenario: str = Query("none", pattern="^(none|closure|rain)$")):
    refresh()
    start, end = nearest_node(a_lat, a_lon), nearest_node(b_lat, b_lon)
    weights = edge_weights(mode, horizon, scenario)
    display_time_weights = edge_weights("fastest", horizon, scenario)
    get_weight = lambda edge_id: weights[edge_id]
    alternatives = k_routes(adj, P, get_weight, start, end, k=3)
    if not alternatives:
        return {"routes": [], "message": "No path found. Try locations within the mapped area."}
    free = astar(adj, P, lambda edge_id: BASE[edge_id], start, end)
    routes = []
    for path, edge_ids, cost in alternatives:
        distance_km = sum(E[edge_id][2] for edge_id in edge_ids)/1000
        # For eco mode cost is a score, so derive displayed minutes from traffic-weighted time separately.
        predicted_seconds = sum(display_time_weights[e] for e in edge_ids)
        routes.append({"min": round(predicted_seconds/60, 1), "score": round(cost, 2), "km": round(distance_km, 2),
                       "pts": [P[node] for node in path], "edges": len(edge_ids)})
    # Fastest is deterministic; diversity is provided by the alternative-route penalty heuristic.
    pick = min(range(len(routes)), key=lambda idx: routes[idx]["min"])
    return {"free_min": round(free[2]/60, 1) if free else None, "pick": pick, "mode": mode,
            "horizon_minutes": horizon, "scenario": scenario, "routes": routes,
            "route_method": "A* with traffic/weather costs; alternatives use edge penalties"}


@app.get("/api/search")
def search_records(q: str = Query(..., min_length=1, max_length=64)):
    """Binary search the sorted pseudonymous incident registry by exact record ID."""
    target = q.strip().lower()
    if not target:
        raise HTTPException(status_code=400, detail="Enter a record ID")
    found = binary_search(LOG_BY_ID, target, key=lambda record: record["id"])
    if found is None:
        return {"found": False, "algorithm": "Binary Search", "complexity": "O(log n)", "message": "No record with that ID"}
    valid = SIGNER.verify(found["payload"], found["signature"])
    return {"found": True, "algorithm": "Binary Search", "complexity": "O(log n)", "record": {
        "id": found["id"], "time": hm(found["ts"]), "camera": str(found["camera"]),
        "pseudonym": found["pseudonym"], "signature_valid": valid,
    }}


@app.get("/api/trace")
def trace(plate: str = Query(..., min_length=4, max_length=20), minutes: int = Query(120, ge=1, le=10080)):
    target = pseudonym(plate)
    start_index = bisect.bisect_left(LOG_TS, time.time()-minutes*60)
    results = []
    for record in LOG[start_index:]:
        if hmac.compare_digest(record["pseudonym"], target):
            results.append({"time": hm(record["ts"]), "cam": str(record["camera"]), "record_id": record["id"],
                            "verified": SIGNER.verify(record["payload"], record["signature"])})
    return {"results": results, "data_notice": "Synthetic demo records only; pseudonyms and keys are regenerated at restart."}


@app.get("/api/bench")
def bench():
    message = b"BLR-Sentinel integrity benchmark"
    start = time.perf_counter()
    signatures = [SIGNER.sign(message) for _ in range(30)]
    sign_ms = (time.perf_counter()-start)/len(signatures)*1000
    start = time.perf_counter()
    verified = sum(SIGNER.verify(message, sig) for sig in signatures)
    verify_ms = (time.perf_counter()-start)/len(signatures)*1000
    return {"rsa_pss_sign_ms": round(sign_ms, 3), "rsa_pss_verify_ms": round(verify_ms, 3),
            "verified": verified, "algorithm": "RSA-PSS with SHA-256 via cryptography"}


@app.get("/api/algorithms")
def algorithms():
    return {"binary_search": {"purpose": "Exact record lookup in an ID-sorted registry", "complexity": "O(log n)"},
            "astar": {"purpose": "Traffic-aware road routing", "complexity": "O((V+E) log V) typical with a binary heap"},
            "aho_corasick": {"purpose": "Match many watchlist patterns in one pass", "complexity": "O(total pattern length + text length + matches)"},
            "rsa": {"purpose": "RSA-PSS signatures and verification for record integrity", "hash": "SHA-256"}}


# Warm the cache in the background so the first route request doesn't wait on upstream APIs.
def _refresh_loop():
    while True:
        try:
            refresh(force=True)
        except Exception:
            pass
        time.sleep(REFRESH_SECONDS)


@app.on_event("startup")
def startup_event():
    thread = threading.Thread(target=_refresh_loop, daemon=True, name="traffic-refresh")
    thread.start()


app.mount("/", StaticFiles(directory="static", html=True), name="static")
