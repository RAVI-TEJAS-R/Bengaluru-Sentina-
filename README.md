# Bangalore Sentinel — Streamlit Edition

Bangalore Sentinel is an interactive Streamlit dashboard for demonstrating traffic-aware route optimization, congestion analytics, weather context, disruption scenarios, incident lookup, privacy-aware multi-pattern matching, and record-integrity signatures for Bengaluru.

> **Important data notice:** The default road graph, incident register, and vehicle/plate examples are synthetic demonstrations. Live traffic requires a TomTom API key. Weather is retrieved from Open-Meteo when available. Rain is not proof of flooding, and the route estimates are not suitable for real-world navigation.

## Features

- **Interactive Bengaluru dashboard** with area-level traffic indicators, map markers, and congestion chart.
- **Optional live traffic feed** through TomTom Traffic Flow API; clearly labelled simulated fallback if no key or requests fail.
- **Live weather context** from Open-Meteo (no key needed), with graceful fallback if unavailable.
- **A* route finding** and alternative routes, with fastest, reliability-weighted, and energy-proxy objectives.
- **Disruption scenarios** and a rain-aware demonstration penalty to show how route costs can adapt.
- **Incident explorer** with exact ID retrieval using Binary Search, filtering, and CSV export.
- **Privacy lab** with Aho–Corasick multi-pattern matching and RSA-PSS/SHA-256 payload signatures.
- **Algorithm benchmark** to measure local demo timings.
- **JSON/CSV exports** for route summaries, traffic snapshots, and demo incidents.

## Deploy on Streamlit Community Cloud (browser-only)

1. Push this repository to GitHub.
2. Open [Streamlit Community Cloud](https://share.streamlit.io/) and sign in with GitHub.
3. Choose **Create app**, select the repository and branch, and set **Main file path** to `app.py`.
4. Deploy. Streamlit installs dependencies from `requirements.txt`.
5. Optional: in the app's **Settings → Secrets**, add the key as TOML:

   ```toml
   TOMTOM_KEY = "your_tomtom_api_key"
   ```

   Then restart the app. To use Streamlit secrets automatically, add this key to the app configuration only; never commit secrets to GitHub. In the current app, the sidebar key field can also be used for a session without saving it.

Streamlit Community Cloud quotas and terms can change; check the current official documentation before deployment.

## Run locally

Python 3.10+ is recommended.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Project files

- `app.py` — Streamlit app and dashboard pages.
- `algos.py` — A*, alternative-route helper, Binary Search, Aho–Corasick, RSA-PSS utilities.
- `build_graph.py` — optional OpenStreetMap graph builder from the original API edition; requires OSMnx.
- `tests/test_algorithms.py` — unit tests for the algorithm helpers.
- `static/index.html` — original standalone frontend retained for reference; Streamlit serves the main UI from `app.py`.
- `render.yaml` — legacy Render configuration from the original API edition; not used by Streamlit Community Cloud.

## Traffic and route limitations

- TomTom traffic is queried for a limited set of probe locations. A provider reading is not a complete road-wide traffic picture. Keep an eye on API terms and quota.
- The map's route graph is a small **synthetic grid**, not the actual Bengaluru street network. The route geometry is for algorithm demonstrations only. `build_graph.py` is retained as a starting point for creating an OSMnx graph, but the current Streamlit route planner uses its synthetic grid.
- Alternative routes are generated with an edge-penalty heuristic; they are not guaranteed globally optimal k-shortest paths.
- The forecast display is a heuristic scenario visualization, not a trained or validated prediction model.
- “Eco proxy” is a relative distance/congestion indicator, not an emissions measurement.

## Privacy and security

- Do not enter real registration plates, camera footage, or personal data. The Privacy Lab is a fictional demonstration.
- RSA private keys are generated in memory for the running process and are not persisted. Signatures do not constitute a durable audit trail.
- The sidebar TomTom key is not written to disk by this app. For deployed use, prefer Streamlit Secrets; never store credentials in source control.
- This app has no user authentication, role-based access, persistent incident database, or operational authorization controls. Do not use it for actual surveillance or emergency response.

## Algorithms

1. **A***: traffic-weighted graph search using non-negative edge costs.
2. **Binary Search**: exact record lookup over incident records sorted by ID.
3. **Aho–Corasick**: scans text against multiple patterns in one pass after pattern preprocessing.
4. **RSA-PSS with SHA-256**: signs/verifies demonstration payload integrity using the `cryptography` library.

## Attribution

Map display uses Folium with CartoDB dark map tiles and place coordinates intended for demonstration. Weather is sourced from Open-Meteo when available. Optional traffic data is sourced from TomTom. Review each provider's current attribution and usage requirements before sharing publicly.
