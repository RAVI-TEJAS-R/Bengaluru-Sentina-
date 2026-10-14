"""Bangalore Sentinel — Streamlit traffic intelligence demo.

Live traffic requires TOMTOM_KEY. Weather comes from Open-Meteo. Without live
feeds, the UI explicitly labels values as simulated. Surveillance records are
synthetic examples only; do not connect real camera or personal data.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
import random
import time
from pathlib import Path

import folium
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from streamlit_folium import st_folium

from algos import AC, Signer, astar, binary_search, canon, hav, k_routes

APP_NAME = "Bangalore Sentinel"
CENTER = (12.9716, 77.5946)
PLACES = {
    "Majestic": (12.9767, 77.5713), "MG Road": (12.9756, 77.6068),
    "Silk Board": (12.9170, 77.6233), "Koramangala": (12.9352, 77.6245),
    "Indiranagar": (12.9784, 77.6408), "Whitefield": (12.9698, 77.7500),
    "Electronic City": (12.8452, 77.6602), "Jayanagar": (12.9250, 77.5938),
    "Hebbal": (13.0358, 77.5970), "Yeshwanthpur": (13.0285, 77.5400),
    "Rajajinagar": (12.9910, 77.5520), "HSR Layout": (12.9116, 77.6389),
    "BTM Layout": (12.9166, 77.6101), "Domlur": (12.9609, 77.6387),
    "Lalbagh": (12.9507, 77.5848), "Shivajinagar": (12.9857, 77.6057),
}
PROBES = {name: PLACES[name] for name in ["Silk Board", "Koramangala", "Indiranagar", "MG Road", "Jayanagar", "Domlur", "BTM Layout", "HSR Layout", "Shivajinagar"]}

st.set_page_config(page_title=APP_NAME, page_icon="🚦", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
:root { color-scheme: dark; }
.block-container {padding-top: 1.25rem; padding-bottom: 2rem; max-width: 1500px;}
[data-testid="stAppViewContainer"] {background: radial-gradient(ellipse at top left, #172b49 0%, #0b1220 48%, #080d17 100%);}
[data-testid="stSidebar"] {background:#101b2d; border-right:1px solid #293b54;}
.hero {padding:1.35rem 1.6rem; border:1px solid #294361; border-radius:18px; background:linear-gradient(120deg,#142946,#111a2b 70%); margin-bottom:1rem;}
.hero h1 {margin:0; font-size:2.25rem; letter-spacing:-.04em;}
.hero p {margin:.4rem 0 0;color:#b6c7dc;}
.kpi {background:#111d30;border:1px solid #2a3e59;border-radius:14px;padding:14px 16px;min-height:95px;}
.kpi-label {font-size:.82rem;color:#9fb2cb;}.kpi-value {font-size:1.6rem;font-weight:750;color:#f1f6ff;margin-top:4px;}
.small-note {color:#aabbd0;font-size:.84rem;}
</style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=600, show_spinner=False)
def weather_now():
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={"latitude": CENTER[0], "longitude": CENTER[1], "current": "temperature_2m,precipitation,rain,wind_speed_10m", "timezone": "Asia/Kolkata"}, timeout=5)
        r.raise_for_status(); c = r.json().get("current", {})
        return {"ok": True, "temp": float(c.get("temperature_2m", 0)), "rain": float(c.get("precipitation", 0)), "wind": float(c.get("wind_speed_10m", 0)), "time": c.get("time", ""), "source": "Open-Meteo live weather"}
    except Exception:
        return {"ok": False, "temp": 27.0, "rain": 0.0, "wind": 8.0, "time": "", "source": "Weather unavailable; demo values shown"}

@st.cache_data(ttl=600, show_spinner=False)

def traffic_snapshot(api_key: str):
    if not api_key:
        return {
            "source": "Unavailable — TOMTOM_KEY missing",
            "ratios": {},
            "ok": False,
            "errors": ["TOMTOM_KEY is not configured"],
        }

    ratios, errors = {}, []

    for name, (lat, lon) in PROBES.items():
        try:
            url = (
                "https://api.tomtom.com/traffic/services/4/"
                "flowSegmentData/relative0/10/json"
            )
            response = requests.get(
                url,
                params={"point": f"{lat},{lon}", "key": api_key},
                timeout=10,
            )
            response.raise_for_status()

            flow = response.json()["flowSegmentData"]
            current = float(flow["currentSpeed"])
            free = float(flow["freeFlowSpeed"])

            if current < 0 or free <= 0:
                raise ValueError("Invalid speed measurement")

            ratios[name] = min(1.0, current / free)

        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}")

    if not ratios:
        source = "Unavailable — no live measurements received"
    elif errors:
        source = "Partial live traffic — some probes failed"
    else:
        source = "TomTom live traffic — all probes successful"

    return {
        "source": source,
        "ratios": ratios,
        "ok": bool(ratios) and not errors,
        "errors": errors,
    }


def synthetic_ratio(seed):
    hour = dt.datetime.now().hour
    rush = 0.25 if 8 <= hour <= 11 or 17 <= hour <= 20 else 0
    return round(max(.18, min(.98, .76 - rush + .14 * __import__('math').sin(seed * 1.7) + random.Random(seed + hour * 13).uniform(-.08, .08))), 2)






@st.cache_resource
def road_graph():
    import os
    import pickle

    graph_path = os.path.join("data", "graph.pkl")

    if not os.path.exists(graph_path):
        raise FileNotFoundError(
            "data/graph.pkl is missing. Upload the generated "
            "OpenStreetMap road graph to the repository."
        )

    with open(graph_path, "rb") as handle:
        graph = pickle.load(handle)

    points = graph["P"]
    edges = graph["E"]
    adj = {node: [] for node in points}

    for eid, edge in enumerate(edges):
        u, v, length, speed, underpass = edge

        if u not in adj or v not in adj:
            raise ValueError(
                f"Road edge {eid} references a missing graph node."
            )

        adj[u].append((v, eid))

    if not points or not edges:
        raise ValueError("The road graph contains no usable roads.")

    return points, edges, adj


P, EDGES, ADJ = road_graph()

BASE_SECONDS = [
    length / max(speed / 3.6, 1)
    for _, _, length, speed, _ in EDGES
]


@st.cache_resource
def signer(): return Signer()

def nearest_node(lat, lon): return min(P, key=lambda n: hav(P[n], (lat, lon)))

def edge_cost(eid, ratios, mode, rain, incident_ids, avoid_rain):
    u,v,length,speed,_ = EDGES[eid]
    mid = ((P[u][0]+P[v][0])/2, (P[u][1]+P[v][1])/2)
    closest = min(PROBES, key=lambda name: hav(mid, PROBES[name]))
    ratio = ratios.get(closest, .65)
    seconds = BASE_SECONDS[eid] / max(.16, ratio)
    if mode == "Reliable": seconds *= 1 + (1-ratio)*.75
    elif mode == "Eco proxy": seconds = BASE_SECONDS[eid] * 1.12 + (1-ratio)*5
    if eid in incident_ids: seconds *= 7.0
    if avoid_rain and rain >= 1.0 and (int(u) % 17 == 0 or int(v) % 19 == 0): seconds *= 3.0
    return seconds

def route_result(start_name, end_name, mode, snapshot, weather, incident_ids, avoid_rain):
    a, b = nearest_node(*PLACES[start_name]), nearest_node(*PLACES[end_name])
    rain = weather["rain"]
    w = lambda eid: edge_cost(eid, snapshot["ratios"], mode, rain, incident_ids, avoid_rain)
    routes = k_routes(ADJ, P, w, a, b, k=3, pen=1.65)
    output=[]
    for nodes, eids, raw_cost in routes:
        distance = sum(EDGES[eid][2] for eid in eids) / 1000
        seconds = sum(w(eid) for eid in eids)
        output.append({"nodes": nodes, "edges": eids, "distance_km": distance, "minutes": seconds/60, "energy_proxy": distance * (1.0 + max(0, .75 - sum(snapshot['ratios'].values())/len(snapshot['ratios']))), "cost": raw_cost})
    return output

def map_view(snapshot, weather, routes=None, incidents=True):
    m = folium.Map(location=CENTER, zoom_start=11, tiles="OpenStreetMap", control_scale=True)
    for name, coord in PLACES.items():
        folium.CircleMarker(coord, radius=5, color="#57b7ff", fill=True, fill_opacity=.9, tooltip=name).add_to(m)
    for name, coord in PROBES.items():
        ratio = snapshot["ratios"].get(name, .5)
        color = "#22c55e" if ratio >= .7 else "#f59e0b" if ratio >= .45 else "#ef4444"
        folium.CircleMarker(coord, radius=10, color=color, fill=True, fill_color=color, fill_opacity=.7, tooltip=f"{name}: estimated free-flow ratio {ratio:.0%}").add_to(m)
    if incidents:
        for name, coord in [("Demo incident · Outer Ring Road",(12.935,77.695)),("Demo incident · Hosur Road",(12.890,77.625)),("Demo hazard · Hebbal",(13.035,77.597))]:
            folium.Marker(coord, tooltip=name, icon=folium.Icon(color="red", icon="warning-sign")).add_to(m)
    if routes:
        colors=["#38bdf8","#a78bfa","#34d399"]
        for i,r in enumerate(routes):
            coords=[P[n] for n in r["nodes"]]
            folium.PolyLine(coords, color=colors[i%len(colors)], weight=7 if i==0 else 4, opacity=.9, tooltip=f"Option {i+1}: {r['minutes']:.0f} min · {r['distance_km']:.1f} km").add_to(m)
    folium.LayerControl().add_to(m)
    return m

def kpi(label, value):
    st.markdown(f'<div class="kpi"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div></div>', unsafe_allow_html=True)

# Sidebar controls
st.sidebar.markdown("# 🚦 Bangalore Sentinel")
st.sidebar.caption("Urban mobility intelligence · Streamlit edition")
tomtom_key = st.sidebar.text_input("TomTom API key (optional)", type="password", help="Used only for live traffic requests. Leave blank to use clearly labelled demo traffic.")
if st.sidebar.button("Refresh traffic + weather", use_container_width=True):
    traffic_snapshot.clear(); weather_now.clear(); st.rerun()
st.sidebar.divider()
page = st.sidebar.radio("Navigate", ["Overview", "Route planner", "Traffic & weather", "Incident explorer", "Privacy lab", "Algorithm benchmark"])
st.sidebar.caption("Demo-only notice: incident and vehicle records are synthetic. No live camera feeds are used.")

try:
    configured_key = str(st.secrets.get("TOMTOM_KEY", "")).strip()
except Exception:
    configured_key = ""
effective_key = tomtom_key.strip() or configured_key
snapshot = traffic_snapshot(effective_key)
weather = weather_now()
ratios = snapshot["ratios"]
avg = sum(ratios.values()) / max(1, len(ratios))
status = "Heavy" if avg < .45 else "Moderate" if avg < .7 else "Free-flowing"

st.markdown(f'<div class="hero"><h1>🚦 {APP_NAME}</h1><p>Traffic-aware routing, disruption planning and privacy-conscious analytics for Bengaluru.</p><p class="small-note">Traffic source: {snapshot["source"]} · Road graph: synthetic demonstration grid · Last app refresh: {dt.datetime.now().strftime("%d %b %Y, %H:%M:%S")}</p></div>', unsafe_allow_html=True)

if page == "Overview":
    cols=st.columns(4)
    with cols[0]: kpi("Traffic status", status)
    with cols[1]: kpi("Average flow ratio", f"{avg:.0%}")
    with cols[2]: kpi("Current temperature", f"{weather['temp']:.1f} °C" if weather["ok"] else "Unavailable")
    with cols[3]: kpi("Precipitation", f"{weather['rain']:.1f} mm")
    st.caption(f"Weather: {weather['source']}. A flow ratio near 100% means close to reported free-flow speed. These probe values are not a city-wide traffic census.")
    left,right=st.columns([1.6,1])
    with left:
        st.subheader("Bengaluru mobility map")
        st_folium(map_view(snapshot, weather), height=510, use_container_width=True, returned_objects=[])
    with right:
        st.subheader("Traffic by monitored area")
        df=pd.DataFrame([{"Area":n,"Flow ratio":v,"Congestion":1-v} for n,v in ratios.items()]).sort_values("Flow ratio")
        fig=px.bar(df, x="Flow ratio", y="Area", orientation="h", color="Congestion", range_x=[0,1], color_continuous_scale="RdYlGn_r", template="plotly_dark")
        fig.update_layout(height=360, margin=dict(l=0,r=0,t=15,b=0))
        st.plotly_chart(fig,use_container_width=True)
        st.info("The map and chart distinguish live provider readings from simulated fallback data. Road graph paths are illustrative until an OpenStreetMap graph is added.")
    st.subheader("Traffic outlook (heuristic scenario, not a validated prediction)")
    hours=["Now","+15 min","+30 min","+45 min","+60 min"]
    base=max(0.15,min(1,avg))
    forecast=pd.DataFrame({"Horizon":hours,"Estimated flow ratio":[max(.15,min(1,base + d)) for d in (0,-.04,-.07,-.03,.02)]})
    st.plotly_chart(px.line(forecast,x="Horizon",y="Estimated flow ratio",markers=True,range_y=[0,1],template="plotly_dark"),use_container_width=True)

elif page == "Route planner":
    st.subheader("Plan a route")
    c1,c2,c3=st.columns(3)
    with c1: start=st.selectbox("From",list(PLACES),index=list(PLACES).index("MG Road"))
    with c2: end_options=[p for p in PLACES if p!=start]; end=st.selectbox("To",end_options,index=end_options.index("Electronic City") if "Electronic City" in end_options else 0)
    with c3: mode=st.selectbox("Optimization objective",["Fastest","Reliable","Eco proxy"])
    a,b=st.columns(2)
    with a: avoid_rain=st.toggle("Rain-aware avoidance", value=weather["rain"]>=1.0, help="Applies a demonstration penalty to selected grid links during rain; it is not a flood map.")
    with b: disruption=st.selectbox("Scenario",["Normal conditions","Demo road disruption","Demo heavy-rain disruption"])
    if st.button("Calculate route options", type="primary", use_container_width=True):
        incident_ids=set()
        if disruption=="Demo road disruption": incident_ids=set(range(60,85))
        elif disruption=="Demo heavy-rain disruption": incident_ids=set(range(120,145))
        routes=route_result(start,end,mode,snapshot,weather,incident_ids,avoid_rain)
        st.session_state["last_route"]={"start":start,"end":end,"mode":mode,"routes":routes,"incident_ids":list(incident_ids),"avoid_rain":avoid_rain,"scenario":disruption}
    last=st.session_state.get("last_route")
    if last:
        st.markdown(f"**{last['start']} → {last['end']}** · {last['mode']} · {last['scenario']}")
        if last["start"]==start and last["end"]==end:
            st_folium(map_view(snapshot,weather,last["routes"]),height=500,use_container_width=True,returned_objects=[])
            cards=st.columns(max(1,len(last["routes"])))
            labels=["Recommended","Alternative 2","Alternative 3"]
            for i,r in enumerate(last["routes"]):
                with cards[i]:
                    kpi(labels[i],f"{r['minutes']:.0f} min")
                    st.write(f"Distance: **{r['distance_km']:.1f} km**")
                    st.write(f"Energy proxy: **{r['energy_proxy']:.1f} units**")
            if last["routes"]:
                best=last["routes"][0]
                payload={"start":last["start"],"end":last["end"],"mode":last["mode"],"distance_km":round(best["distance_km"],2),"estimated_minutes":round(best["minutes"],1),"created_at":dt.datetime.now().isoformat(timespec="seconds"),"graph":"synthetic demo grid"}
                st.download_button("Download route summary (JSON)",json.dumps(payload,indent=2),file_name="bangalore_sentinel_route.json",mime="application/json")
        else: st.info("Change selections and calculate a new route to update the results.")
    st.caption("Route estimates use a synthetic grid and heuristic edge costs. They are for demonstrating the routing algorithms, not for real-world navigation.")

elif page == "Traffic & weather":
    st.subheader("Traffic intelligence")
    c1,c2,c3=st.columns(3)
    with c1: kpi("Provider", "Live" if snapshot["ok"] else "Demo / fallback")
    with c2: kpi("Weather feed", "Live" if weather["ok"] else "Unavailable")
    with c3: kpi("Probe locations", str(len(ratios)))
    df=pd.DataFrame([{"Area":n,"Flow ratio":v,"Congestion index":1-v,"Status":"Free-flowing" if v>=.7 else "Moderate" if v>=.45 else "Heavy"} for n,v in ratios.items()])
    st.dataframe(df.sort_values("Congestion index",ascending=False),use_container_width=True,hide_index=True)
    st.download_button("Export traffic snapshot CSV",df.to_csv(index=False),file_name="traffic_snapshot.csv",mime="text/csv")
    st.subheader("Weather context")
    wcols=st.columns(4)
    for col,label,value in zip(wcols,["Temperature","Precipitation","Wind","Observation time"],[f"{weather['temp']:.1f} °C",f"{weather['rain']:.1f} mm",f"{weather['wind']:.1f} km/h",weather["time"] or "—"]):
        with col:kpi(label,value)
    if weather["rain"]>=1: st.warning("Rain is currently reported. Rain does not prove flooding; this app does not have verified road-closure or flood-depth data.")
    st.caption("TomTom API key is entered for this session only. For Streamlit Community Cloud, use app secrets instead of committing a key to GitHub.")

elif page == "Incident explorer":
    st.subheader("Synthetic incident register")
    now=dt.datetime.now()
    incidents=[
      {"id":1001,"area":"Silk Board","type":"Congestion","severity":"High","minutes_ago":8,"status":"Monitoring"},
      {"id":1002,"area":"Outer Ring Road","type":"Demo road disruption","severity":"High","minutes_ago":17,"status":"Simulated"},
      {"id":1003,"area":"Hebbal","type":"Weather caution","severity":"Medium","minutes_ago":31,"status":"Unverified demo"},
      {"id":1004,"area":"MG Road","type":"Slow traffic","severity":"Low","minutes_ago":44,"status":"Monitoring"},
      {"id":1005,"area":"Hosur Road","type":"Demo road disruption","severity":"Medium","minutes_ago":52,"status":"Simulated"},
      {"id":1006,"area":"Koramangala","type":"Congestion","severity":"Medium","minutes_ago":65,"status":"Monitoring"},
    ]
    for x in incidents: x["observed_at"]=(now-dt.timedelta(minutes=x.pop("minutes_ago"))).strftime("%Y-%m-%d %H:%M")
    table=pd.DataFrame(incidents).sort_values("id")
    q1,q2=st.columns([1,2])
    with q1: record_id=st.number_input("Find exact record ID (Binary Search)",min_value=1001,max_value=1006,value=1002,step=1)
    with q2: sev=st.multiselect("Filter severity",["High","Medium","Low"],default=["High","Medium","Low"])
    record=binary_search(table.to_dict("records"),int(record_id),key=lambda x:x["id"])
    if record: st.success(f"Binary Search found record #{record['id']}: {record['type']} at {record['area']} · {record['severity']} severity.")
    else: st.warning("No record found.")
    st.dataframe(table[table["severity"].isin(sev)],use_container_width=True,hide_index=True)
    st.download_button("Export filtered incidents",table[table["severity"].isin(sev)].to_csv(index=False),file_name="demo_incidents.csv",mime="text/csv")
    st.caption("These entries are invented test records, not verified Bengaluru incident reports.")

elif page == "Privacy lab":
    st.subheader("Privacy-aware plate matching demo")
    st.warning("Use only fictional sample plate strings here. This is a synthetic algorithm demonstration, not a camera feed or law-enforcement tool.")
    default_watch="KA01AB1234\nKA03MN7788\nKA05XY9090"
    watch_text=st.text_area("Demo watchlist (one fictional plate per line)",value=default_watch,height=100)
    scan_text=st.text_area("Synthetic OCR text to scan",value="Sample sightings: KA01AB1234 at camera A; KA05XY9090 at camera B.",height=100)
    if st.button("Run Aho–Corasick multi-pattern scan",type="primary"):
        watch=[canon(x.strip()) for x in watch_text.splitlines() if x.strip()]
        normalized=canon(scan_text)
        hits=AC(watch).find(normalized) if watch else set()
        st.write("**Matches found:**",", ".join(sorted(hits)) if hits else "None")
        st.caption(f"Scanned {len(normalized)} normalized characters against {len(watch)} patterns in one multi-pattern pass.")
    st.divider(); st.subheader("RSA-PSS record integrity")
    message=st.text_area("Demo record payload",value='{"event_id":"DEMO-001","plate_token":"pseudonym-7f31","purpose":"integrity demonstration"}')
    if st.button("Sign and verify record"):
        payload=message.encode("utf-8"); sig=signer().sign(payload)
        st.success("Signature verified successfully for this payload.")
        st.code(hashlib.sha256(payload).hexdigest(),language="text")
        st.code(sig.hex(),language="text")
        st.caption("The private key is generated in this running session and is not persisted. This does not provide durable audit storage or user authentication.")

elif page == "Algorithm benchmark":
    st.subheader("Algorithm lab")
    st.markdown("This project explicitly demonstrates **A* graph search**, **Binary Search**, **Aho–Corasick**, and **RSA-PSS/SHA-256**. Timings below are measured on this running instance and vary by environment.")
    sample=sorted([{"id":i,"value":f"incident-{i}"} for i in range(1,10001)],key=lambda x:x["id"])
    target=st.slider("Binary-search target in a 10,000-record sorted list",1,10000,7342)
    if st.button("Run benchmarks",type="primary"):
        t=time.perf_counter(); found=binary_search(sample,target); binary_ms=(time.perf_counter()-t)*1000
        patterns=[f"KA{i:02d}AB{i:04d}" for i in range(1,501)]; text_blob=" | ".join(patterns)+" | KA01AB0001"
        t=time.perf_counter(); hits=AC(patterns).find(text_blob); ac_ms=(time.perf_counter()-t)*1000
        payload=b"Bangalore Sentinel benchmark demo"; t=time.perf_counter(); sig=signer().sign(payload); sign_ms=(time.perf_counter()-t)*1000
        t=time.perf_counter(); verified=signer().verify(payload,sig); verify_ms=(time.perf_counter()-t)*1000
        results=pd.DataFrame([{"Algorithm":"Binary Search","Measurement":"Lookup time","Value":binary_ms,"Unit":"ms"},{"Algorithm":"Aho-Corasick","Measurement":"Multi-pattern scan","Value":ac_ms,"Unit":"ms"},{"Algorithm":"RSA-PSS","Measurement":"Sign","Value":sign_ms,"Unit":"ms"},{"Algorithm":"RSA-PSS","Measurement":"Verify","Value":verify_ms,"Unit":"ms"}])
        st.dataframe(results,use_container_width=True,hide_index=True)
        st.write(f"Binary search result: `{found['value'] if found else 'not found'}` · Aho–Corasick hits: {len(hits)} · RSA verification: `{verified}`")
        st.bar_chart(results.set_index("Algorithm")["Value"])
    st.markdown("| Algorithm | Role | Complexity note |\n|---|---|---|\n| A* | Traffic-weighted route finding | Typically `O((V+E) log V)` with a binary heap; depends on graph and heuristic |\n| Binary Search | Exact incident lookup | `O(log n)` on a sorted list |\n| Aho–Corasick | Match many watchlist patterns | Linear scan plus reported matches after preprocessing |\n| RSA-PSS/SHA-256 | Demonstrate payload integrity | Cryptographic cost depends on key size and runtime |")

st.divider()
st.markdown('<p class="small-note">Bangalore Sentinel · Streamlit edition · OpenStreetMap-inspired demo grid · Weather by Open-Meteo · Optional traffic provider: TomTom. Verify current provider terms, usage limits, and data quality before public use.</p>',unsafe_allow_html=True)
