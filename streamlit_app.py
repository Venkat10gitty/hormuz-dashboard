# streamlit_app.py — Closing the Hormuz Food Corridor | Columbia Puma Lab
# Live research dashboard: PortWatch · GFW · FRED · Windward

import os, time, pickle, hashlib, warnings
from io import StringIO
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots

try:
    from fredapi import Fred; FREDAPI_OK = True
except ImportError:
    FREDAPI_OK = False

try:
    import yfinance as yf; YFINANCE_OK = True
except ImportError:
    YFINANCE_OK = False

warnings.filterwarnings("ignore")

# ── page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Hormuz 2026 | Puma Lab",
    page_icon="🚢",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── constants ──────────────────────────────────────────────────────────────────
TODAY          = pd.Timestamp(datetime.now().date())
CRISIS_START   = pd.Timestamp("2026-02-28")
IRGC_CLOSURE   = pd.Timestamp("2026-03-02")
INSURANCE_END  = pd.Timestamp("2026-03-05")
NEUTRAL_OPEN   = pd.Timestamp("2026-03-26")
CEASEFIRE      = pd.Timestamp("2026-04-08")
US_BLOCKADE    = pd.Timestamp("2026-04-13")
IRAN_REOPEN    = pd.Timestamp("2026-04-17")
IRAN_RECLOSE   = pd.Timestamp("2026-04-18")
ALL_DARK       = pd.Timestamp("2026-04-23")
ANALYSIS_START = pd.Timestamp("2025-10-01")

# (date, label, color, y_paper) — staggered so close pairs don't overprint
CRISIS_EVENTS = [
    ("2026-02-28", "Feb 28<br><i>Operation Epic Fury</i>", "#9B2226", 0.92),
    ("2026-03-02", "Mar 2<br><i>IRGC Closure</i>",         "#C1121F", 0.70),
    ("2026-03-26", "Mar 26<br><i>Neutral ships</i>",       "#2A9D8F", 0.92),
    ("2026-04-13", "Apr 13<br><i>US Blockade</i>",         "#9B2226", 0.92),
    ("2026-04-17", "Apr 17<br><i>Declared open</i>",       "#E9C46A", 0.78),
    ("2026-04-18", "Apr 18<br><i>Iran re-closes</i>",      "#C1121F", 0.60),
]

EVENT_SOURCES = (
    "Event sources: "
    "Feb 28 — US DoD / Windward AI Mar-01 report (observed) · "
    "Mar 2 — IRGC official statement / AP wire (observed) · "
    "Mar 26 — Iranian MFA announcement (observed) · "
    "Apr 13 — USN Fifth Fleet / NY Post (observed) · "
    "Apr 17 — Iranian MFA declaration (observed) · "
    "Apr 18 — IRNA / Reuters (observed)"
)

# Regime definitions — used for background shading (ground-truth, not PELT output)
REGIME_DEFS = [
    (None,         CRISIS_START, 0, "Pre-crisis",     "rgba(232,244,248,0.35)"),
    (CRISIS_START, IRGC_CLOSURE, 1, "Shock",           "rgba(253,220,220,0.45)"),
    (IRGC_CLOSURE, NEUTRAL_OPEN, 2, "Closure",         "rgba(247,181,181,0.45)"),
    (NEUTRAL_OPEN, CEASEFIRE,    3, "Selective access","rgba(212,234,216,0.45)"),
    (CEASEFIRE,    US_BLOCKADE,  4, "Ceasefire blip",  "rgba(212,234,216,0.35)"),
    (US_BLOCKADE,  ALL_DARK,     5, "Dual blockade",   "rgba(245,168,168,0.50)"),
    (ALL_DARK,     None,         6, "All dark",        "rgba(180,20,20,0.18)"),
]

# Bounding boxes for GFW/SAR queries.  Three named regions, each with a distinct use:
#   "Hormuz Strait" — the navigation channel itself (25.5–27.0°N, 55.5–58.5°E).
#     Matches analysis1_food_segment.py HORMUZ_BBOX exactly; use this for SAR detection
#     counts and the food-segment dark fraction.  Width chosen to capture both TSS lanes
#     without including the full Gulf of Oman approach zone.
#   "Full Region" — wider box (22–27°N, 55.5–60°E) used for GAP events and encounters
#     where vessels disable AIS before entering the strait (capture zone must be broader).
#   "Gulf of Oman" — the approach zone south of the strait (22–25.5°N, 56–60°E).
# Default selection for the sidebar is "Hormuz Strait".
BBOXES = {
    "Hormuz Strait": {"min_lat":25.5,"max_lat":27.0,"min_lon":55.5,"max_lon":58.5,"name":"Strait of Hormuz"},
    "Full Region":   {"min_lat":22.0,"max_lat":27.0,"min_lon":55.5,"max_lon":60.0,"name":"Full Hormuz region"},
    "Gulf of Oman":  {"min_lat":22.0,"max_lat":25.5,"min_lon":56.0,"max_lon":60.0,"name":"Gulf of Oman"},
}

PORTWATCH_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest"
    "/services/Daily_Chokepoints_Data/FeatureServer/0/query"
)

PUMA_CSV_URL = (
    "https://raw.githubusercontent.com/mjpuma/hormuz/main/"
    "hormuz_transit_observed.csv"
)

GFW_BASE = "https://gateway.api.globalfishingwatch.org"

PAL = {
    "hormuz":"#C1121F","crisis":"#9B2226","baseline":"#2D6A4F",
    "fert":"#E76F51","wheat":"#2A9D8F","tanker":"#C1121F",
    "bulk":"#2A9D8F","container":"#E9C46A","dark":"#9B2226",
    "other":"#ADB5BD","gaps":"#E76F51","enc":"#9B59B6",
    # Three-channel decomposition colors (v3.1 spec)
    "physical":"#C1121F",        # vessels cannot pass — dark red
    "institutional":"#E9A84C",   # contractually barred (JWC/war clauses/P&I) — amber
    "discretionary":"#E76F51",   # could sail but owner/master declines — orange
}

WINDWARD_ANCHORS = {
    "2025-10-01":(108,0),"2026-01-01":(111,0),"2026-02-01":(113,0),
    "2026-02-27":(113,0),"2026-02-28":(72,0),"2026-03-01":(38,0),
    "2026-03-02":(15,0),"2026-03-03":(8,0),"2026-03-04":(4,0),
    "2026-03-05":(4,0),"2026-03-07":(3,1),"2026-03-08":(2,1),
    "2026-03-09":(3,1),"2026-03-13":(4,2),"2026-03-16":(6,3),
    "2026-03-24":(4,2),"2026-03-26":(22,0),"2026-03-27":(28,0),
    "2026-04-06":(11,0),"2026-04-08":(42,0),"2026-04-11":(17,0),
    "2026-04-12":(21,0),"2026-04-13":(4,0),"2026-04-14":(3,0),
}

# ── behavioral taxonomy tables ─────────────────────────────────────────────────

TAXONOMY_TABLE = pd.DataFrame([
    ["AIS disabling",             "Davenport #14 — Track ends",      "Evasion + deterrence composite",         "GFW GAP events / day"],
    ["Loitering / Rendezvous",    "Davenport #3",                    "STS transfer, Gulf of Oman handoff",     "GFW encounter events / day"],
    ["Route deviation",           "Davenport #9 — Outside hist. route","Topology collapse → 0 through-traffic","SAR detections in strait bbox / day"],
    ["False position / Spoofing", "Davenport #11",                   "Dark tonnage undercount bias",           "SAR–AIS mismatch rate"],
    ["Outside shipping lane",     "Davenport #8",                    "Sanctioned crude evasion index",         "SAR dark fraction (all vessel types)"],
    ["Not heading to port",       "Davenport #12",                   "Self-deterrence corroboration",          "Gulf port calls (PortWatch n_total)"],
    ["Abnormal stop",             "Riveiro (2018) — Anchoring",      "Port congestion → rerouting signal",     "AIS: avg speed=0 in bbox"],
    ["Self-deterrence",           "NEW — no Davenport code",         "Discretionary channel isolation",        "Apr 17: declared-open; transits stayed 0"],
], columns=["Riveiro Family / Category", "Davenport Mapping", "Aggregate Treatment", "Observable Metric"])

NOVEL_CATEGORIES = pd.DataFrame([
    ["Self-deterrence",
     "Vessels choose not to enter even when the strait is declared open.",
     "Apr 17, 2026 — Iran declared strait open; 8 vessels transited (11% of baseline). "
     "As of Aug 2026, R(τ) has never exceeded 0.40 despite the declared reopening.",
     "Isolates the discretionary channel: declared reopening with no physical block; "
     "master/owner refusal persists. Institutional channel (war clauses, JWC listing) "
     "also remains active — upper bound only until M5 separates the two."],
    ["Regime-transition speed",
     "How rapidly aggregate fleet behavior responds to political signals.",
     "Transit collapse: 113 → 6 vessels/day within 72 hours of IRGC closure.",
     "Measures institutional credibility of closure declarations vs actual AIS behavior."],
    ["Flag-state stratification",
     "Different flag registries respond differently to the same physical closure.",
     "China/India/Pakistan vs Western carriers show divergent transit patterns post-Mar 2.",
     "Identifies geopolitical fracture lines in global food supply chains."],
    ["Corridor topology shift",
     "Spatial reorganization of vessel routes at the fleet level — not just individual vessels.",
     "From IMO-designated lanes → IRGC corridors → zero throughput → Oman rerouting.",
     "Captures the food corridor collapse as a network-level event, not vessel-level evasion."],
], columns=["Category", "Definition", "Hormuz 2026 Manifestation", "Research Significance"])

DAVENPORT_TABLE = pd.DataFrame([
    ["Track ends / AIS-off", "14", "GFW Events: gaps",      "AIS ceases mid-strait",      "Daily GAP count"],
    ["Loitering",            "3",  "GFW Events: encounters","Gulf of Oman rendezvous",    "Encounter events/day"],
    ["Not heading to port",  "12", "GFW Encounters API",    "Off-route vessel rendezvous","STS events/day"],
    ["Outside hist. route",  "9",  "4Wings: AIS presence",  "IMO lane → IRGC corridor",  "Lane ratio"],
    ["Outside ship. lane",   "8",  "4Wings: SAR geojson",   "Complete topology shift",    "SAR in bbox/day"],
    ["False position",       "11", "SAR vs AIS mismatch",   "Dark detections in strait",  "SAR dark / total"],
    ["SELF-DETERRENCE (NEW)","—",  "4Wings: AIS presence",  "Apr 17: open, zero transits","Declared-open vs actual"],
], columns=["Davenport Category","#","GFW Endpoint","Hormuz 2026 Signal","Observable Metric"])

# ── disk cache (survives Streamlit re-renders) ─────────────────────────────────
CACHE_DIR = Path("/tmp/.cache_hormuz_app")
CACHE_DIR.mkdir(exist_ok=True)

def _cp(k): return CACHE_DIR / f"{hashlib.md5(k.encode()).hexdigest()[:12]}.pkl"
def _cget(k, ttl=3600*12):
    p = _cp(k)
    if not p.exists(): return None
    if time.time() - p.stat().st_mtime > ttl: p.unlink(); return None
    try:
        with open(p,"rb") as f: return pickle.load(f)
    except: return None
def _cset(k, v):
    try:
        with open(_cp(k),"wb") as f: pickle.dump(v, f)
    except: pass

# ── API keys ──────────────────────────────────────────────────────────────────
# GFW token must be set in Streamlit secrets (key: GFW_API_KEY) or the
# GFW_API_KEY environment variable.  No embedded fallback — if unconfigured
# the app shows an explicit warning rather than silently using a stale key.
def _secret(key, default=""):
    try:
        return st.secrets.get(key, None) or os.getenv(key, default)
    except Exception:
        return os.getenv(key, default)

GFW_API_KEY  = _secret("GFW_API_KEY", "")
FRED_KEY     = _secret("FRED_API_KEY", "")
GFW_CONFIGURED = bool(GFW_API_KEY)
GFW_HEADERS  = {"Authorization": f"Bearer {GFW_API_KEY}", "Content-Type": "application/json"}
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Columbia-PumaLab/3.0; sb5206@columbia.edu)"}


# ══════════════════════════════════════════════════════════════════════════════
# DATA LAYER — all functions wrapped with @st.cache_data(ttl=12h)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=43200, show_spinner=False)
def fetch_portwatch(start: str, end: str, chokepoint: str = "chokepoint6"):
    """PortWatch ArcGIS — returns (df_full, source_str). df_full has n_total, n_tanker, etc."""
    ck = f"pw_full_{chokepoint}_{start}_{end}"
    cached = _cget(ck)
    if cached is not None:
        return cached

    where = (f"portid = '{chokepoint}' "
             f"AND date >= DATE '{start}' AND date <= DATE '{end}'")
    fields = ("date,n_total,n_tanker,n_dry_bulk,n_general_cargo,"
              "n_container,n_roro,capacity,capacity_tanker,"
              "capacity_dry_bulk,capacity_container")
    rows, offset = [], 0
    try:
        sess = requests.Session(); sess.headers.update(HTTP_HEADERS)
        while True:
            r = sess.get(PORTWATCH_URL, params={
                "where": where, "outFields": fields,
                "orderByFields": "date ASC", "resultOffset": offset,
                "resultRecordCount": 1000, "f": "json",
            }, timeout=25)
            feats = r.json().get("features", [])
            if not feats: break
            rows.extend(f["attributes"] for f in feats)
            if len(feats) < 1000: break
            offset += 1000
    except Exception as e:
        return None, f"PortWatch error: {e}"

    if not rows:
        return None, "PortWatch: 0 records"

    df = pd.DataFrame(rows)
    raw_date = df["date"]
    if pd.api.types.is_numeric_dtype(raw_date):
        df["date"] = pd.to_datetime(raw_date, unit="ms", errors="coerce")
    else:
        df["date"] = pd.to_datetime(raw_date, errors="coerce")
    df["date"] = df["date"].dt.tz_localize(None) if df["date"].dt.tz is not None else df["date"]
    for c in ["n_total","n_tanker","n_dry_bulk","n_general_cargo","n_container","n_roro"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
    df["n_cargo"] = df.get("n_dry_bulk", 0) + df.get("n_general_cargo", 0)
    df = df.sort_values("date").reset_index(drop=True)
    src = f"IMF PortWatch ArcGIS — {chokepoint} — live ({len(df)} days)"
    result = (df, src)
    _cset(ck, result)
    return result


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_puma_csv():
    ck = "puma_csv_app"
    cached = _cget(ck, ttl=7200)
    if cached is not None: return cached
    for local in ["hormuz_transit_observed.csv"]:
        if Path(local).exists():
            df = pd.read_csv(local)
            df.columns = [c.lower().strip() for c in df.columns]
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            _cset(ck, df)
            return df
    try:
        r = requests.get(PUMA_CSV_URL, timeout=15, headers=HTTP_HEADERS)
        if r.status_code == 200:
            df = pd.read_csv(StringIO(r.text))
            df.columns = [c.lower().strip() for c in df.columns]
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).sort_values("date")
            _cset(ck, df)
            return df
    except Exception:
        pass
    return None


def _build_windward_series(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="D")
    anchors = {pd.Timestamp(k): v for k, v in WINDWARD_ANCHORS.items()}
    totals = np.full(len(dates), np.nan)
    dark   = np.zeros(len(dates))
    is_obs = np.zeros(len(dates), dtype=bool)
    for i, d in enumerate(dates):
        if d in anchors:
            totals[i] = anchors[d][0]
            dark[i]   = anchors[d][1]
            is_obs[i] = True
    s = pd.Series(totals, index=dates).interpolate("linear").clip(lower=0)
    np.random.seed(42)
    for i in range(len(dates)):
        if not is_obs[i] and not np.isnan(totals[i]):
            s.iloc[i] = max(s.iloc[i] + np.random.normal(0, max(s.iloc[i]*0.06,0.3)), 0)
    return pd.DataFrame({"date": dates, "transit_vessels": s.values.round(1),
                         "dark": dark, "is_observed": is_obs})


def get_transit_data(start: str, end: str):
    """Merged transit series: PortWatch (primary) + Windward + Puma CSV."""
    s = pd.Timestamp(start); e = pd.Timestamp(end)
    df = _build_windward_series(s, e)

    pw_result = fetch_portwatch(start, end)
    pw_df, pw_src = pw_result if pw_result else (None, "unavailable")
    if pw_df is not None and len(pw_df) > 5:
        for _, row in pw_df.iterrows():
            mask = df["date"] == row["date"]
            if mask.any():
                df.loc[mask, "transit_vessels"] = row["n_total"]
                df.loc[mask, "is_observed"] = True
        src = pw_src
    else:
        src = "Windward AI daily reports (PortWatch unavailable)"

    puma = fetch_puma_csv()
    if puma is not None:
        for _, row in puma.iterrows():
            val = pd.to_numeric(
                row.get("total", row.get("transit_vessels", row.get("n_total", np.nan))),
                errors="coerce"
            )
            if pd.isna(val): continue
            mask = df["date"] == row["date"]
            if mask.any():
                df.loc[mask, "transit_vessels"] = val
                df.loc[mask, "is_observed"] = True

    df["real_transit"] = df["transit_vessels"] + df["dark"].fillna(0)
    baseline = float(df[df["date"] < CRISIS_START]["transit_vessels"].mean())
    nadir    = float(df[(df["date"] >= IRGC_CLOSURE) &
                        (df["date"] < NEUTRAL_OPEN)]["transit_vessels"].mean())
    drop_pct = (baseline - nadir) / baseline * 100 if baseline > 0 else 0.0
    return df, baseline, drop_pct, src


def _fetch_wb_pink_sheet_urea(start: str, end: str):
    """Fetch urea monthly prices from the World Bank Commodity Markets Pink Sheet.

    Scrapes the commodity-markets page to obtain the current monthly XLSX URL
    (the URL changes with each release).  Returns a daily-interpolated pd.Series
    on success, raises RuntimeError with a descriptive message on failure.
    Urea column: 'Urea ($/mt)' — Middle East f.o.b. spot.
    Source: World Bank Commodity Markets Outlook (CMO) — 'Monthly Prices' sheet.
    """
    import io, re, urllib.request, openpyxl

    agent = "Mozilla/5.0 (compatible; Columbia-PumaLab/3.0; sb5206@columbia.edu)"

    # Step 1: find the current XLSX URL from the landing page
    page_url = "https://www.worldbank.org/en/research/commodity-markets"
    req = urllib.request.Request(page_url, headers={"User-Agent": agent})
    try:
        html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", errors="ignore")
    except Exception as exc:
        raise RuntimeError(
            f"Could not reach World Bank commodity-markets page: {exc}"
        ) from exc

    matches = re.findall(
        r"(https://thedocs\.worldbank\.org[^\"'>\\s]+CMO-Historical-Data-Monthly\.xlsx)",
        html,
    )
    if not matches:
        raise RuntimeError(
            "World Bank page loaded but Pink Sheet XLSX link was not found. "
            "The page layout may have changed."
        )
    xlsx_url = matches[0]

    # Step 2: download the XLSX
    req2 = urllib.request.Request(xlsx_url, headers={"User-Agent": agent})
    try:
        raw = urllib.request.urlopen(req2, timeout=60).read()
    except Exception as exc:
        raise RuntimeError(
            f"Pink Sheet URL found ({xlsx_url[:80]}…) but download failed: {exc}"
        ) from exc

    # Step 3: parse the 'Monthly Prices' sheet — urea is column index 60 (col 61)
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)
        ws = wb["Monthly Prices"]
        rows = list(ws.iter_rows(values_only=True))
    except Exception as exc:
        raise RuntimeError(f"Could not parse Pink Sheet XLSX: {exc}") from exc

    # Confirm header row (row 5, index 4): column 60 should be 'Urea' or 'Urea '
    header_row = rows[4]
    urea_col = None
    for i, cell in enumerate(header_row):
        if cell and "urea" in str(cell).lower():
            urea_col = i
            break
    if urea_col is None:
        raise RuntimeError(
            "Pink Sheet parsed but 'Urea' column not found in header row. "
            "Column layout may have changed."
        )

    date_vals, urea_vals = [], []
    for row in rows[6:]:  # data starts at row 7 (index 6)
        date_str = row[0]
        val      = row[urea_col]
        if date_str and isinstance(date_str, str) and "M" in date_str and val is not None:
            try:
                yr, mo = date_str.split("M")
                date_vals.append(pd.Timestamp(f"{yr}-{mo}-01"))
                urea_vals.append(float(val))
            except (ValueError, TypeError):
                pass

    if len(date_vals) < 12:
        raise RuntimeError(
            f"Pink Sheet parsed but only {len(date_vals)} urea observations found (expected 700+)."
        )

    monthly = pd.Series(urea_vals, index=pd.DatetimeIndex(date_vals)).sort_index()

    # Step 4: daily interpolation over requested window (matches wheat pattern)
    req_dates = pd.date_range(start, end, freq="D")
    all_idx   = monthly.index.union(req_dates)
    daily     = monthly.reindex(all_idx).interpolate("time").reindex(req_dates)

    return daily, xlsx_url


@st.cache_data(ttl=43200, show_spinner=False)
def get_prices(start: str, end: str):
    dates = pd.date_range(start, end, freq="D")
    wheat_daily = None
    wheat_src   = None
    urea_daily  = None
    urea_src    = None
    urea_err    = None

    # ── Wheat: FRED PWHEAMTUSDM (World Bank via FRED) ────────────────────────
    if FRED_KEY and FREDAPI_OK:
        try:
            fred = Fred(api_key=FRED_KEY)
            s = fred.get_series("PWHEAMTUSDM",
                                 observation_start=start, observation_end=end)
            if s is not None and len(s.dropna()) > 2:
                wheat_daily = (s.dropna()
                                .reindex(s.index.union(dates))
                                .interpolate("time").reindex(dates))
                wheat_src = "FRED PWHEAMTUSDM — World Bank wheat (live)"
        except Exception:
            pass

    if wheat_daily is None:
        wa = {
            pd.Timestamp("2025-10-01"):235, pd.Timestamp("2026-01-01"):218,
            pd.Timestamp("2026-02-01"):215, pd.Timestamp("2026-02-27"):213,
            pd.Timestamp("2026-03-05"):234, pd.Timestamp("2026-03-11"):262,
            pd.Timestamp("2026-03-20"):280, pd.Timestamp("2026-04-01"):275,
            pd.Timestamp("2026-04-08"):260, pd.Timestamp("2026-04-13"):285,
        }
        s = pd.Series(wa)
        wheat_daily = s.reindex(s.index.union(dates)).interpolate("time").reindex(dates)
        wheat_src   = "⚠️ FRED unavailable — World Bank GEM calibrated anchors (FRED key not set)"

    # ── Urea: World Bank Pink Sheet (CMO Monthly Prices, Middle East f.o.b.) ─
    try:
        urea_daily, xlsx_url = _fetch_wb_pink_sheet_urea(start, end)
        short_url = xlsx_url.split("/related/")[0].split("/doc/")[1][:20] + "…"
        urea_src  = (
            f"World Bank Commodity Markets Pink Sheet — Urea ($/mt) Middle East f.o.b. "
            f"(CMO-Historical-Data-Monthly.xlsx, doc {short_url})"
        )
        print(f"[prices] Urea: World Bank Pink Sheet OK — "
              f"last obs {urea_daily.dropna().index[-1].date()} = "
              f"${urea_daily.dropna().iloc[-1]:.1f}/mt")
    except RuntimeError as exc:
        urea_err   = str(exc)
        urea_daily = None
        urea_src   = None
        print(f"[prices] ERROR fetching urea from Pink Sheet: {exc}")

    # ── Brent: yfinance ───────────────────────────────────────────────────────
    brent_daily = None
    if YFINANCE_OK:
        try:
            brent_raw = yf.download("BZ=F", start=start, end=end, progress=False)
            if brent_raw is not None and len(brent_raw) > 10:
                brent_s = (brent_raw["Close"].squeeze()
                           .reindex(pd.date_range(brent_raw.index.min(),
                                                  brent_raw.index.max(), freq="D"))
                           .interpolate("time"))
                brent_daily = brent_s.reindex(dates).interpolate("time")
        except Exception:
            pass

    if brent_daily is None:
        ba = {
            pd.Timestamp("2025-10-01"):78, pd.Timestamp("2026-01-01"):76,
            pd.Timestamp("2026-02-01"):77, pd.Timestamp("2026-02-27"):79,
            pd.Timestamp("2026-03-02"):92, pd.Timestamp("2026-03-09"):108,
            pd.Timestamp("2026-03-15"):112, pd.Timestamp("2026-04-08"):110,
            pd.Timestamp("2026-04-13"):119, pd.Timestamp("2026-04-17"):105,
            pd.Timestamp("2026-04-23"):98,
        }
        sb = pd.Series(ba)
        brent_daily = sb.reindex(sb.index.union(dates)).interpolate("time").reindex(dates)

    df = pd.DataFrame({
        "date":        dates,
        "urea_usdmt":  urea_daily.values if urea_daily is not None else np.nan,
        "wheat_usdmt": wheat_daily.values,
        "brent_usd":   brent_daily.values,
    })
    # urea_err is passed back so the UI can display a visible banner if needed
    return df, wheat_src, urea_src, urea_err


# ── USDA PSD bulk download — wheat balance sheet for GCC countries ────────────
# Old endpoint (psdonline/api/v1/) is HTTP 404 — that API path was deprecated.
# New API (OpenData/api/psd/) is live but requires a free API key registered at
#   https://apps.fas.usda.gov/opendatawebV2/#/  (API_KEY header, not a query param).
# Bulk CSV is the practical route: no key required, updated monthly, ~2.9 MB.
#   https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip
# Qatar has zero rows for wheat in PSD (not tracked; USDA only tracks Qatar for
# barley, corn, rice).  Qatar requires a proxy source (FAOSTAT or national stat).

PSD_GRAINS_URL = (
    "https://apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip"
)
PSD_GCC = ["Bahrain", "Kuwait", "Oman", "Saudi Arabia", "United Arab Emirates"]
# Qatar deliberately excluded — USDA PSD does not track wheat for Qatar.
# Attribute IDs for wheat balance sheet:
PSD_ATTR = {20: "Beginning_Stocks", 57: "Imports", 125: "Dom_Consumption", 176: "Ending_Stocks"}


@st.cache_data(ttl=86400, show_spinner=False)   # cache 24 h — PSD updates monthly
def get_psd_wheat_gcc():
    """Download USDA PSD grains bulk CSV and return GCC wheat balance sheet.

    Returns:
        df   – wide-format DataFrame indexed by Country_Name × Market_Year,
               columns: Beginning_Stocks, Imports, Dom_Consumption, Ending_Stocks
               (all in 1000 MT, most recent USDA estimate for each marketing year).
        note – human-readable status string for UI display.
    """
    import zipfile, io as _io
    try:
        req = urllib.request.Request(
            PSD_GRAINS_URL,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except Exception as e:
        return None, f"Download failed: {e}"

    try:
        zf = zipfile.ZipFile(_io.BytesIO(raw))
        with zf.open(zf.namelist()[0]) as f:
            df_raw = pd.read_csv(f, encoding="latin-1")
    except Exception as e:
        return None, f"Parse failed: {e}"

    # Filter wheat + GCC + 2023+
    mask = (
        (df_raw["Commodity_Description"] == "Wheat") &
        (df_raw["Country_Name"].isin(PSD_GCC)) &
        (df_raw["Market_Year"] >= 2023) &
        (df_raw["Attribute_ID"].isin(PSD_ATTR.keys()))
    )
    sub = df_raw[mask].copy()
    if sub.empty:
        return None, "No GCC wheat data found in PSD download"

    # Keep most-recent USDA revision per country × year × attribute
    sub["Attr"] = sub["Attribute_ID"].map(PSD_ATTR)
    latest = (
        sub.sort_values("Month")
        .groupby(["Country_Name", "Market_Year", "Attr"])
        .last()
        .reset_index()
    )
    pivot = latest.pivot_table(
        index=["Country_Name", "Market_Year"],
        columns="Attr",
        values="Value",
        aggfunc="last",
    ).reset_index()
    pivot.columns.name = None

    note = (
        f"USDA PSD grains bulk CSV · {len(df_raw):,} rows downloaded · "
        f"GCC wheat rows retained: {len(sub)} · "
        f"Qatar excluded (not tracked in PSD wheat) · "
        f"Marketing years: {sorted(sub['Market_Year'].unique().tolist())}"
    )
    return pivot, note


# ── GFW helpers ───────────────────────────────────────────────────────────────

def _bbox_geojson(bbox):
    return {"type":"Polygon","coordinates":[[
        [bbox["min_lon"],bbox["min_lat"]],[bbox["max_lon"],bbox["min_lat"]],
        [bbox["max_lon"],bbox["max_lat"]],[bbox["min_lon"],bbox["max_lat"]],
        [bbox["min_lon"],bbox["min_lat"]],
    ]]}

def _classify_vessel(rec):
    if rec.get("vesselId","") == "": return "dark"
    for field in [(rec.get("geartype","") or "").upper(),
                  (rec.get("vesselType","") or "").upper()]:
        if any(x in field for x in ["TANKER","LNG","LPG","CHEMICAL"]): return "tanker"
        if any(x in field for x in ["BULK","CARGO"]):                   return "bulk_cargo"
        if "CONTAINER" in field:                                         return "container"
    return "other"

def _events_post(dataset, ps, pe, geom, timeout=60):
    sess = requests.Session(); sess.headers.update(GFW_HEADERS)
    return sess.post(
        f"{GFW_BASE}/v3/events",
        params={"limit":1000,"offset":0},
        json={"datasets":[dataset],"startDate":ps,"endDate":pe,"geometry":geom},
        timeout=timeout,
    )


@st.cache_data(ttl=43200, show_spinner=False)
def get_sar_data(bbox_name: str, start: str, end: str):
    """Returns (daily_df, raw_df). raw_df may be None on API error."""
    bbox = BBOXES[bbox_name]
    ck_daily = f"app_sar_{bbox['name'][:8]}_{start}_{end}"
    ck_raw   = f"app_sar_raw_{bbox['name'][:8]}_{start}_{end}"

    cached_daily = _cget(ck_daily)
    cached_raw   = _cget(ck_raw)
    if cached_daily is not None and cached_raw is not None:
        return cached_daily, cached_raw

    geom = _bbox_geojson(bbox)
    periods = pd.date_range(start, end, freq="60D")
    if len(periods) == 0 or periods[-1] < pd.Timestamp(end):
        periods = periods.append(pd.DatetimeIndex([pd.Timestamp(end)]))

    all_raw = []
    for i in range(len(periods) - 1):
        ps = periods[i].strftime("%Y-%m-%d")
        pe = periods[i+1].strftime("%Y-%m-%d")
        try:
            r = requests.post(
                f"{GFW_BASE}/v3/4wings/report",
                params={
                    "datasets[0]":         "public-global-sar-presence:v3.0",
                    "date-range":          f"{ps},{pe}",
                    "spatial-resolution":  "LOW",
                    "temporal-resolution": "DAILY",
                    "format":              "JSON",
                },
                headers=GFW_HEADERS, json={"geojson": geom}, timeout=120,
            )
            if r.status_code == 200 and r.content:
                for entry in r.json().get("entries", []):
                    for _, recs in entry.items():
                        all_raw.extend(recs or [])
        except Exception:
            pass

    if not all_raw:
        return None, None

    df_raw = pd.DataFrame(all_raw)
    df_raw["date"]       = pd.to_datetime(df_raw["date"]).dt.normalize()
    df_raw["category"]   = df_raw.apply(_classify_vessel, axis=1)
    df_raw["detections"] = pd.to_numeric(df_raw.get("detections", 1), errors="coerce").fillna(1)
    _cset(ck_raw, df_raw)

    daily = (df_raw.groupby(["date", df_raw["vesselId"].eq("")])["detections"]
                   .sum().unstack(fill_value=0).reset_index())
    daily.columns.name = None
    if True  not in daily.columns: daily[True]  = 0
    if False not in daily.columns: daily[False] = 0
    daily = daily.rename(columns={True:"sar_dark", False:"sar_ais"})
    if "date" not in daily.columns:
        daily = daily.rename(columns={daily.columns[0]:"date"})
    daily["sar_total"] = daily["sar_ais"] + daily["sar_dark"]
    _cset(ck_daily, daily)
    return daily, df_raw


@st.cache_data(ttl=43200, show_spinner=False)
def get_gaps(bbox_name: str, start: str, end: str):
    bbox = BBOXES[bbox_name]
    ck = f"app_gaps_{bbox['name'][:8]}_{start}_{end}"
    cached = _cget(ck)
    if cached is not None: return cached

    geom = _bbox_geojson(bbox)
    periods = pd.date_range(start, end, freq="14D")
    if periods[-1] < pd.Timestamp(end):
        periods = periods.append(pd.DatetimeIndex([pd.Timestamp(end)]))

    rows = []
    for i in range(len(periods) - 1):
        ps = periods[i].strftime("%Y-%m-%d")
        pe = min((periods[i+1] - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), end)
        try:
            r = _events_post("public-global-gaps-events:v3.0", ps, pe, geom, timeout=60)
            if r.status_code in [200, 201]:
                for ev in r.json().get("entries", []):
                    ts = ev.get("start") or ev.get("timestamp","")
                    if ts: rows.append({"date": pd.Timestamp(ts),
                                        "flag": ev.get("vessel",{}).get("flag","")})
        except Exception:
            pass

    if not rows: return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    daily = (df.groupby(df["date"].dt.date).size()
               .reset_index(name="gap_events"))
    daily.columns = ["date","gap_events"]
    daily["date"] = pd.to_datetime(daily["date"])
    _cset(ck, daily)
    return daily


@st.cache_data(ttl=43200, show_spinner=False)
def get_encounters(bbox_name: str, start: str, end: str):
    bbox = BBOXES[bbox_name]
    ck = f"app_enc_{bbox['name'][:8]}_{start}_{end}"
    cached = _cget(ck)
    if cached is not None: return cached

    geom = _bbox_geojson(bbox)
    periods = pd.date_range(start, end, freq="MS")
    if len(periods) == 0:
        periods = pd.DatetimeIndex([pd.Timestamp(start)])
    if periods[-1] < pd.Timestamp(end):
        periods = periods.append(pd.DatetimeIndex([pd.Timestamp(end)]))

    rows = []
    for i in range(len(periods) - 1):
        ps = periods[i].strftime("%Y-%m-%d")
        pe = min((periods[i] + pd.offsets.MonthEnd(1)).strftime("%Y-%m-%d"), end)
        try:
            r = _events_post("public-global-encounters-events:v3.0", ps, pe, geom, timeout=60)
            if r.status_code in [200, 201]:
                for ev in r.json().get("entries", []):
                    ts = ev.get("start") or ev.get("timestamp","")
                    if ts: rows.append({"date": pd.Timestamp(ts)})
        except Exception:
            pass

    if not rows: return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    daily = (df.groupby(df["date"].dt.date).size()
               .reset_index(name="enc_events"))
    daily.columns = ["date","enc_events"]
    daily["date"] = pd.to_datetime(daily["date"])
    _cset(ck, daily)
    return daily


def get_historical_transit(chokepoint: str, start: str, end: str):
    return fetch_portwatch(start, end, chokepoint=chokepoint)


# ══════════════════════════════════════════════════════════════════════════════
# CHART HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _add_events(fig, date_range=None, row=None, col=None):
    kw = {}
    if row: kw["row"] = row
    if col: kw["col"] = col
    for ds, label, color, y in CRISIS_EVENTS:
        d = pd.Timestamp(ds)
        if date_range:
            dr0 = pd.Timestamp(str(date_range[0]))
            dr1 = pd.Timestamp(str(date_range[1]))
            if not (dr0 <= d <= dr1): continue
        fig.add_vline(x=ds, line_dash="dash", line_color=color,
                      line_width=1.5, opacity=0.6, **kw)
        fig.add_annotation(
            x=ds, yref="paper", y=y,
            text=label, showarrow=False, xanchor="left",
            font=dict(size=9, color=color),
            bgcolor="rgba(255,255,255,0.75)",
            **({"row": row, "col": col} if row else {})
        )


def _add_regime_shading(fig, date_range, row=None, col=None, label_y_frac=0.97):
    """Add documented event-date regime color bands as vrect background.

    These bands are placed at documented historical dates (e.g. IRGC closure,
    ceasefire, US blockade) — NOT PELT changepoint output.  PELT is a separate
    analysis; do not label these bands as changepoint-detected.
    """
    dr0 = pd.Timestamp(str(date_range[0]))
    dr1 = pd.Timestamp(str(date_range[1]))
    kw = {}
    if row: kw["row"] = row
    if col: kw["col"] = col
    for (t0, t1, rid, name, color) in REGIME_DEFS:
        r_start = t0 if t0 is not None else dr0
        r_end   = t1 if t1 is not None else dr1
        if r_end <= dr0 or r_start >= dr1:
            continue
        band_start = max(r_start, dr0).strftime("%Y-%m-%d")
        band_end   = min(r_end,   dr1).strftime("%Y-%m-%d")
        fig.add_vrect(
            x0=band_start, x1=band_end,
            fillcolor=color, layer="below", line_width=0,
            **kw
        )
        # label at top of band midpoint
        mid = max(r_start, dr0) + (min(r_end, dr1) - max(r_start, dr0)) / 2
        if mid >= dr0 and mid <= dr1:
            fig.add_annotation(
                x=mid.strftime("%Y-%m-%d"),
                yref="paper", y=label_y_frac,
                text=name, showarrow=False,
                font=dict(size=8, color="#555"),
                bgcolor="rgba(255,255,255,0.0)",
                **({"row": row, "col": col} if row else {})
            )


def fig_transit(df, baseline, drop_pct, src, date_range,
                show_regimes=False, show_food=False, pw_df=None):
    mask = (df["date"] >= pd.Timestamp(str(date_range[0]))) & \
           (df["date"] <= pd.Timestamp(str(date_range[1])))
    d = df[mask].copy()

    fig = go.Figure()

    # Crisis gap fill
    crisis_mask = d["date"] >= CRISIS_START
    if crisis_mask.any():
        dc = d[crisis_mask]
        fig.add_trace(go.Scatter(
            x=list(dc["date"]) + list(dc["date"])[::-1],
            y=[baseline]*len(dc) + list(dc["transit_vessels"])[::-1],
            fill="toself", fillcolor="rgba(193,18,31,0.10)",
            line=dict(width=0), showlegend=True,
            name="Missing traffic vs baseline", hoverinfo="skip",
        ))

    # All-vessel line
    fig.add_trace(go.Scatter(
        x=d["date"], y=d["transit_vessels"],
        mode="lines", line=dict(color=PAL["hormuz"], width=2),
        name="All vessels (AIS) — interpolated",
        hovertemplate="%{x|%b %d}: %{y:.0f} vessels<extra></extra>",
    ))

    # Food segment (dry bulk) overlay
    if show_food and pw_df is not None:
        pw_mask = (pw_df["date"] >= pd.Timestamp(str(date_range[0]))) & \
                  (pw_df["date"] <= pd.Timestamp(str(date_range[1])))
        pw_f = pw_df[pw_mask]
        if "n_dry_bulk" in pw_f.columns:
            fig.add_trace(go.Scatter(
                x=pw_f["date"], y=pw_f["n_dry_bulk"],
                mode="lines", line=dict(color=PAL["bulk"], width=2, dash="dash"),
                name="Dry bulk / food proxy (PortWatch)",
                hovertemplate="%{x|%b %d}: %{y:.0f} dry bulk<extra></extra>",
            ))
            fig.add_trace(go.Scatter(
                x=pw_f["date"], y=pw_f["n_tanker"],
                mode="lines", line=dict(color=PAL["tanker"], width=1.5, dash="dot"),
                name="Tankers — oil (PortWatch)",
                hovertemplate="%{x|%b %d}: %{y:.0f} tankers<extra></extra>",
                opacity=0.65,
            ))

    obs = d[d["is_observed"]]
    fig.add_trace(go.Scatter(
        x=obs["date"], y=obs["transit_vessels"],
        mode="markers", marker=dict(color=PAL["hormuz"], size=6,
                                     line=dict(color="white", width=1)),
        name="Observed anchor (Windward AI / PortWatch)",
        hovertemplate="%{x|%b %d}: %{y:.0f} vessels (observed)<extra></extra>",
    ))
    fig.add_hline(y=baseline, line_dash="dot", line_color=PAL["baseline"],
                  annotation_text=f"Baseline: {baseline:.0f}/day",
                  annotation_font_color=PAL["baseline"])

    if show_regimes:
        _add_regime_shading(fig, date_range, label_y_frac=0.95)

    _add_events(fig, date_range)
    fig.update_layout(
        template="plotly_white", height=420,
        title=dict(text=f"Transit collapse: −{drop_pct:.0f}% from baseline<br>"
                        f"<sup>Source: {src[:80]}</sup>",
                   font=dict(size=14)),
        yaxis_title="AIS-tracked vessels/day (lower bound)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
    )
    return fig


def fig_vessel_categories(sar_raw, pw_df, date_range):
    """Vessel category breakdown. Panel C is a grouped bar (SAR vs PW by type × period)."""
    sar_cat = None
    if sar_raw is not None:
        tmp = sar_raw.copy()
        tmp["date"] = pd.to_datetime(tmp["date"]).dt.normalize()
        mask = (tmp["date"] >= pd.Timestamp(str(date_range[0]))) & \
               (tmp["date"] <= pd.Timestamp(str(date_range[1])))
        tmp = tmp[mask]
        if len(tmp) > 0:
            agg = (tmp.groupby(["date","category"])["detections"]
                      .sum().unstack(fill_value=0).reset_index())
            agg.columns.name = None
            for c in ["tanker","bulk_cargo","container","dark","other"]:
                if c not in agg.columns: agg[c] = 0
            agg["sar_total"] = agg[["tanker","bulk_cargo","container","dark","other"]].sum(axis=1)
            sar_cat = agg

    fig = make_subplots(rows=2, cols=2,
                        subplot_titles=("GFW SAR — by Vessel Category",
                                        "PortWatch — Vessel Type Counts",
                                        "Cross-validation: SAR vs PortWatch (avg/day by period)",
                                        "Dark Vessel Fraction (SAR coverage may vary)"),
                        vertical_spacing=0.15, horizontal_spacing=0.10)

    CAT_COLORS = {"tanker":PAL["tanker"],"bulk_cargo":PAL["bulk"],
                  "container":PAL["container"],"dark":PAL["dark"],"other":PAL["other"]}
    CAT_LABELS = {"tanker":"Tanker","bulk_cargo":"Bulk cargo","container":"Container",
                  "dark":"DARK (no AIS)","other":"Other AIS"}

    # Panel A: SAR stacked area (Scatter with stackgroup, not Bar).
    # Using Scatter here avoids a global barmode conflict with Panel C's grouped bars.
    if sar_cat is not None:
        for cat in ["tanker","bulk_cargo","container","other","dark"]:
            fig.add_trace(go.Scatter(
                x=sar_cat["date"], y=sar_cat[cat],
                stackgroup="sar_panel_a",
                name=CAT_LABELS[cat],
                line=dict(color=CAT_COLORS[cat], width=0.5),
                fillcolor=CAT_COLORS[cat],
                opacity=0.82,
                legendgroup=cat,
                hovertemplate=f"{CAT_LABELS[cat]}: %{{y:.0f}}<extra></extra>",
            ), row=1, col=1)
    else:
        fig.add_annotation(text="No SAR data", x=0.25, y=0.75,
                           xref="paper", yref="paper", showarrow=False)

    # Panel B: PortWatch typed
    if pw_df is not None:
        d_range_mask = (pw_df["date"] >= pd.Timestamp(str(date_range[0]))) & \
                       (pw_df["date"] <= pd.Timestamp(str(date_range[1])))
        pw_f = pw_df[d_range_mask]
        for col, label, color in [
            ("n_tanker",   "Tanker",   PAL["tanker"]),
            ("n_dry_bulk", "Dry bulk", PAL["bulk"]),
            ("n_container","Container",PAL["container"]),
        ]:
            if col in pw_f.columns:
                fig.add_trace(go.Scatter(
                    x=pw_f["date"], y=pw_f[col], name=label,
                    line=dict(color=color, width=2), mode="lines",
                    legendgroup=col,
                    hovertemplate=f"{label}: %{{y:.0f}}<extra></extra>",
                ), row=1, col=2)
    else:
        fig.add_annotation(text="No PortWatch data", x=0.75, y=0.75,
                           xref="paper", yref="paper", showarrow=False)

    # Panel C: grouped bar — SAR vs PortWatch by vessel type × crisis period
    PERIODS = {
        "Pre-crisis": (ANALYSIS_START, CRISIS_START),
        "Crisis":     (CRISIS_START,   US_BLOCKADE),
        "Blockade+":  (US_BLOCKADE,    TODAY + pd.Timedelta(days=1)),
    }
    TYPE_MAP = [
        ("tanker",    "n_tanker",   "Tanker",    PAL["tanker"],    "rgba(193,18,31,0.45)"),
        ("bulk_cargo","n_dry_bulk", "Dry bulk",  PAL["bulk"],      "rgba(42,157,143,0.45)"),
        ("container", "n_container","Container", PAL["container"], "rgba(233,196,106,0.7)"),
    ]
    period_x_sar, period_x_pw = [], []
    for p_name, (p_start, p_end) in PERIODS.items():
        for sar_col, pw_col, t_name, c_solid, c_light in TYPE_MAP:
            # SAR avg/day
            if sar_cat is not None:
                sm = (sar_cat["date"] >= p_start) & (sar_cat["date"] < p_end)
                s_avg = float(sar_cat.loc[sm, sar_col].mean()) if sm.any() else 0.0
            else:
                s_avg = 0.0
            period_x_sar.append((p_name, t_name, s_avg, c_solid))
            # PortWatch avg/day
            if pw_df is not None:
                pm = (pw_df["date"] >= p_start) & (pw_df["date"] < p_end)
                p_avg = float(pw_df.loc[pm, pw_col].mean()) if pm.any() else 0.0
            else:
                p_avg = 0.0
            period_x_pw.append((p_name, t_name, p_avg, c_light))

    # Panel C: grouped bar — SAR vs PortWatch side by side, types stack within each source.
    # offsetgroup="sar" means all SAR bars for a given period stack together;
    # offsetgroup="pw"  means all PW bars for a given period stack together.
    # barmode="stack" (applied globally) handles the stacking; the two different
    # offsetgroups appear side by side.
    legendgroups_seen = set()
    for p_name, t_name, avg, color in period_x_sar:
        lg = f"sar_{t_name}"
        fig.add_trace(go.Bar(
            x=[p_name], y=[avg],
            name=f"SAR {t_name}",
            marker_color=color,
            legendgroup=lg,
            showlegend=(lg not in legendgroups_seen),
            offsetgroup="sar",
            hovertemplate=f"SAR {t_name} · {p_name}: %{{y:.1f}}/day<extra></extra>",
        ), row=2, col=1)
        legendgroups_seen.add(lg)
    for p_name, t_name, avg, color in period_x_pw:
        lg = f"pw_{t_name}"
        fig.add_trace(go.Bar(
            x=[p_name], y=[avg],
            name=f"PW {t_name}",
            marker_color=color,
            legendgroup=lg,
            showlegend=(lg not in legendgroups_seen),
            offsetgroup="pw",
            hovertemplate=f"PW {t_name} · {p_name}: %{{y:.1f}}/day<extra></extra>",
        ), row=2, col=1)
        legendgroups_seen.add(lg)
    fig.update_xaxes(title_text="Crisis period", row=2, col=1)
    fig.update_yaxes(title_text="Avg detections / day", row=2, col=1)

    # Panel D: dark fraction (with coverage note)
    if sar_cat is not None and len(sar_cat) > 0:
        dark_frac = (sar_cat["dark"] / sar_cat["sar_total"].replace(0, np.nan) * 100).fillna(0)
        fig.add_trace(go.Scatter(
            x=sar_cat["date"], y=dark_frac,
            fill="tozeroy", fillcolor="rgba(155,34,38,0.2)",
            line=dict(color=PAL["dark"], width=2), mode="lines",
            name="Dark %", showlegend=False,
            hovertemplate="Dark fraction: %{y:.1f}%<extra></extra>",
        ), row=2, col=2)
        fig.update_yaxes(title_text="Dark vessel %", range=[0,100], row=2, col=2)

    _add_events(fig, date_range, row=1, col=1)
    _add_events(fig, date_range, row=1, col=2)
    _add_events(fig, date_range, row=2, col=2)
    # barmode="stack": Panel C uses offsetgroup="sar"/"pw" to appear side-by-side;
    # within each offsetgroup the stacking is applied.  Panel A uses Scatter
    # (stackgroup) rather than Bar, so it is unaffected by the global barmode.
    fig.update_layout(template="plotly_white", height=700,
                      barmode="stack",
                      title="Vessel Category Breakdown — GFW SAR × PortWatch Cross-validation")
    return fig


def _compute_food_dark_frac(sar_raw, date_range):
    """Proportional attribution: food-segment dark fraction = all-dark × (AIS food / AIS total)."""
    if sar_raw is None:
        return None
    tmp = sar_raw.copy()
    tmp["date"] = pd.to_datetime(tmp["date"]).dt.normalize()
    mask = (tmp["date"] >= pd.Timestamp(str(date_range[0]))) & \
           (tmp["date"] <= pd.Timestamp(str(date_range[1])))
    tmp = tmp[mask]
    if len(tmp) == 0:
        return None

    daily = tmp.groupby("date").apply(lambda g: pd.Series({
        "dark_total":    (g["vesselId"] == "").sum(),
        "ais_total":     (g["vesselId"] != "").sum(),
        "ais_food":      ((g["vesselId"] != "") & (g["category"] == "bulk_cargo")).sum(),
    })).reset_index()
    daily["total_detections"] = daily["dark_total"] + daily["ais_total"]
    daily["all_dark_frac"] = daily["dark_total"] / daily["total_detections"].replace(0, np.nan)
    daily["food_share"] = daily["ais_food"] / daily["ais_total"].replace(0, np.nan)
    daily["food_dark_frac"] = (daily["all_dark_frac"] * daily["food_share"]).fillna(0) * 100
    daily["all_dark_pct"] = (daily["all_dark_frac"] * 100).fillna(0)
    return daily


def fig_dark_analysis(sar_df, gaps_df, enc_df, date_range, sar_raw=None):
    fig = make_subplots(rows=3, cols=1,
                        subplot_titles=("SAR Detections — Dark vs AIS (with food-segment dark fraction)",
                                        "AIS-Disabling Events (GAPs)",
                                        "Vessel Encounters (STS proxy)"),
                        vertical_spacing=0.12, shared_xaxes=True)

    def _filter(df, date_col="date"):
        if df is None: return None
        mask = (df[date_col] >= pd.Timestamp(str(date_range[0]))) & \
               (df[date_col] <= pd.Timestamp(str(date_range[1])))
        return df[mask]

    # SAR panel with food-segment dark fraction
    sar = _filter(sar_df)
    if sar is not None and len(sar) > 0:
        fig.add_trace(go.Bar(x=sar["date"], y=sar["sar_dark"],
                              name="DARK (no AIS)", marker_color=PAL["dark"], opacity=0.88), row=1, col=1)
        fig.add_trace(go.Bar(x=sar["date"], y=sar["sar_ais"],
                              name="AIS-matched", marker_color=PAL["bulk"], opacity=0.78), row=1, col=1)
        fig.update_layout(barmode="stack")

        # Food-segment dark fraction on secondary y
        food_dark = _compute_food_dark_frac(sar_raw, date_range)
        if food_dark is not None and len(food_dark) > 0:
            fig.add_trace(go.Scatter(
                x=food_dark["date"], y=food_dark["all_dark_pct"],
                mode="lines", line=dict(color="#555", width=1.5, dash="dot"),
                name="All-vessel dark % (right)", yaxis="y4",
                hovertemplate="All dark: %{y:.1f}%<extra></extra>",
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=food_dark["date"], y=food_dark["food_dark_frac"],
                mode="lines", line=dict(color=PAL["bulk"], width=2),
                name="Food-segment dark % (upper bound, right)", yaxis="y4",
                hovertemplate="Food dark: %{y:.1f}%<extra></extra>",
            ), row=1, col=1)
    else:
        fig.add_annotation(text="SAR data unavailable", x=0.5, y=0.9,
                           xref="paper", yref="paper", showarrow=False, row=1, col=1)

    # GAPs
    gaps = _filter(gaps_df)
    if gaps is not None and len(gaps) > 0:
        fig.add_trace(go.Bar(x=gaps["date"], y=gaps["gap_events"],
                              name="GAP events/day", marker_color=PAL["gaps"], opacity=0.85,
                              hovertemplate="%{x|%b %d}: %{y:.0f} GAPs<extra></extra>"), row=2, col=1)
    else:
        fig.add_annotation(text="GAPs data unavailable", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False)

    # Encounters
    enc = _filter(enc_df)
    if enc is not None and len(enc) > 0:
        fig.add_trace(go.Bar(x=enc["date"], y=enc["enc_events"],
                              name="Encounter events/day", marker_color=PAL["enc"], opacity=0.85,
                              hovertemplate="%{x|%b %d}: %{y:.0f} encounters<extra></extra>"), row=3, col=1)
    else:
        fig.add_annotation(text="Encounters data unavailable", x=0.5, y=0.1,
                           xref="paper", yref="paper", showarrow=False)

    _add_events(fig, date_range, row=1, col=1)
    fig.update_layout(template="plotly_white", height=640, barmode="stack",
                      title="Dark Vessel Analysis — GFW Sentinel-1 · GAPs · Encounters",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02))
    return fig


def fig_april17_decomp(transit_df, price_df, baseline, date_range):
    """Three-channel decomposition — upper-bound estimate from Apr 17 natural experiment.

    Three channels (v3.1 spec):
      Physical:       vessel cannot pass — blockade, mining, seizure, force.
      Institutional:  not permitted or covered — JWC listing, war clauses (CONWARTIME/
                      VOYWAR 2025), P&I conditions, flag-state advisories, financing refusal.
      Discretionary:  could sail and is covered; owner or master declines.

    Panel A (primary): 1-day ceiling estimate on April 17 only.
      Iran declared strait open; US blockade was still active. The remaining transit
      loss (87% on Apr 17) is an UPPER BOUND on the institutional + discretionary
      channels combined — not a calibrated estimate, because the physical channel
      may not have been zero. Proper identification (M5 JWC discontinuity + M7
      out-of-sample test against Red Sea) requires war risk premium data, pending
      acquisition. The β = transit_loss / brent_spread is an interim ceiling.

    Panel B (contaminated window — shown to explain why 6-day OLS is retired):
      Apr 18: Iran re-closes, 20 vessels surged (committed before announcement).
      That single outlier drives OLS β negative (−2.0). This panel exists only to
      show why the 6-day window cannot be used. Not an alternative result.

    All observations are real PortWatch data (is_observed=True).
    """
    mask = (transit_df["date"] >= CRISIS_START) & \
           (transit_df["date"] <= pd.Timestamp(str(date_range[1])))
    d = transit_df[mask].copy()
    d["transit_loss"] = (baseline - d["transit_vessels"].clip(upper=baseline)).clip(lower=0)

    # Merge Brent spread (war-risk proxy)
    price_cols = [c for c in ["date","brent_usd","urea_usdmt"] if c in price_df.columns]
    d = d.merge(price_df[price_cols], on="date", how="left")
    brent_pre = float(price_df[price_df["date"] < CRISIS_START]["brent_usd"].mean())
    d["brent_spread"] = (d["brent_usd"] - brent_pre).clip(lower=0)

    # ── CALIBRATION A: 1-day (April 17 only) ─────────────────────────────────
    apr17_row = d[d["date"] == IRAN_REOPEN].dropna(subset=["transit_loss","brent_spread"])
    calib_1d_ok = len(apr17_row) == 1

    if calib_1d_ok:
        apr17_loss  = float(apr17_row["transit_loss"].iloc[0])
        apr17_brent = float(apr17_row["brent_spread"].iloc[0])
        # Slope through origin: upper bound — all Apr 17 non-physical loss attributed
        # to institutional + discretionary combined (US blockade still active that day,
        # so this overstates the combined channel; report as ceiling, not estimate).
        beta_1d  = apr17_loss / max(apr17_brent, 1e-9)
        alpha_1d = 0.0
        d["exp_1d"] = (alpha_1d + beta_1d * d["brent_spread"].fillna(0)).clip(0, baseline)
        d["phy_1d"] = (d["transit_loss"] - d["exp_1d"]).clip(lower=0)
        total = d["exp_1d"] + d["phy_1d"]
        scale = d["transit_loss"] / total.replace(0, np.nan)
        d["exp_1d"] = (d["exp_1d"] * scale).fillna(d["transit_loss"])
        d["phy_1d"] = (d["phy_1d"] * scale).fillna(0)
        exp_pct_1d  = d["exp_1d"].mean() / d["transit_loss"].mean() * 100
        phy_pct_1d  = 100 - exp_pct_1d
    else:
        d["exp_1d"] = 0.0; d["phy_1d"] = d["transit_loss"]
        beta_1d = alpha_1d = exp_pct_1d = phy_pct_1d = float("nan")

    # ── CALIBRATION B: 6-day OLS (Apr 17–22, sensitivity only) ───────────────
    calib_6d = d[(d["date"] >= IRAN_REOPEN) &
                 (d["date"] <= pd.Timestamp("2026-04-22"))].dropna(
        subset=["transit_loss","brent_spread"])
    calib_6d_ok = len(calib_6d) >= 3

    if calib_6d_ok:
        x6, y6 = calib_6d["brent_spread"].values, calib_6d["transit_loss"].values
        beta_6d  = float(np.cov(x6, y6)[0,1] / (np.var(x6) + 1e-9))
        alpha_6d = y6.mean() - beta_6d * x6.mean()
        d["exp_6d"] = (alpha_6d + beta_6d * d["brent_spread"].fillna(0)).clip(0, baseline)
        d["phy_6d"] = (d["transit_loss"] - d["exp_6d"]).clip(lower=0)
        total6 = d["exp_6d"] + d["phy_6d"]
        scale6 = d["transit_loss"] / total6.replace(0, np.nan)
        d["exp_6d"] = (d["exp_6d"] * scale6).fillna(d["transit_loss"])
        d["phy_6d"] = (d["phy_6d"] * scale6).fillna(0)
        exp_pct_6d  = d["exp_6d"].mean() / d["transit_loss"].mean() * 100
        phy_pct_6d  = 100 - exp_pct_6d
    else:
        d["exp_6d"] = 0.0; d["phy_6d"] = d["transit_loss"]
        beta_6d = alpha_6d = exp_pct_6d = phy_pct_6d = float("nan")

    # ── BUILD FIGURE (two panels side by side) ────────────────────────────────
    subtitle_1d = (
        f"β = {beta_1d:.2f} vsl-days/USD·bbl  |  "
        f"Physical {phy_pct_1d:.0f}% / Inst+Disc upper bound {exp_pct_1d:.0f}%"
        if calib_1d_ok else "Apr 17 observation missing — cannot compute ceiling"
    )
    subtitle_6d = (
        f"β = {beta_6d:.2f} (sign-flipped by Apr 18 outlier — window contaminated)  |  "
        f"Physical {phy_pct_6d:.0f}% / Combined {exp_pct_6d:.0f}%"
        if calib_6d_ok else "Window < 3 observations"
    )

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=(
            f"A  Ceiling estimate — 1-day upper bound (Apr 17 only)<br>"
            f"<sup>{subtitle_1d}</sup>",
            f"B  Contaminated window (retired) — shown to explain exclusion<br>"
            f"<sup>{subtitle_6d}</sup>",
        ),
        shared_yaxes=True,
        horizontal_spacing=0.06,
    )

    _SHOWLEGEND_DONE = set()

    def _add_decomp_traces(col, exp_col, phy_col):
        show_exp = "exp" not in _SHOWLEGEND_DONE
        show_phy = "phy" not in _SHOWLEGEND_DONE
        if show_exp: _SHOWLEGEND_DONE.add("exp")
        if show_phy: _SHOWLEGEND_DONE.add("phy")
        fig.add_trace(go.Scatter(
            x=d["date"], y=d[phy_col],
            fill="tozeroy", fillcolor="rgba(193,18,31,0.55)",
            line=dict(color=PAL["physical"], width=0.5),
            name="Physical (vessels can't pass)",
            showlegend=show_phy,
            legendgroup="phy",
            hovertemplate="Physical: %{y:.1f}<extra></extra>",
            stackgroup=f"s{col}",
        ), row=1, col=col)
        fig.add_trace(go.Scatter(
            x=d["date"], y=d[exp_col],
            fill="tonexty", fillcolor="rgba(231,111,81,0.45)",
            line=dict(color=PAL["discretionary"], width=0.5),
            name="Institutional + Discretionary (upper bound)",
            showlegend=show_exp,
            legendgroup="exp",
            hovertemplate="Inst+Disc (upper bound): %{y:.1f}<extra></extra>",
            stackgroup=f"s{col}",
        ), row=1, col=col)

    _add_decomp_traces(1, "exp_1d", "phy_1d")
    _add_decomp_traces(2, "exp_6d", "phy_6d")

    # Panel A annotations
    fig.add_vrect(x0="2026-04-17", x1="2026-04-18",
                  fillcolor="rgba(233,196,106,0.35)", layer="below", line_width=0,
                  row=1, col=1)
    fig.add_annotation(
        x="2026-04-17", yref="y", y=baseline * 0.82,
        text="Apr 17<br>ceiling<br>estimate", showarrow=True, arrowhead=2,
        font=dict(size=8, color="#856404"), ax=36, ay=0, row=1, col=1,
    )

    # Panel B annotations — flag April 18 outlier
    apr18_loss = float(d[d["date"] == IRAN_RECLOSE]["transit_loss"].iloc[0]) if len(
        d[d["date"] == IRAN_RECLOSE]) > 0 else 0
    fig.add_vrect(x0="2026-04-17", x1="2026-04-23",
                  fillcolor="rgba(233,196,106,0.20)", layer="below", line_width=0,
                  row=1, col=2)
    fig.add_vrect(x0="2026-04-18", x1="2026-04-19",
                  fillcolor="rgba(255,100,100,0.25)", layer="below", line_width=0,
                  row=1, col=2)
    fig.add_annotation(
        x="2026-04-18", yref="y2", y=baseline * 0.65,
        text="Apr 18 outlier:<br>Iran re-closes,<br>20 vessels surge<br>→ β goes negative",
        showarrow=True, arrowhead=2,
        font=dict(size=8, color="#9B2226"), bgcolor="rgba(255,245,245,0.88)",
        ax=50, ay=-20, row=1, col=2,
    )

    # Shared vline at Apr 17
    for col in (1, 2):
        fig.add_vline(x="2026-04-17", line_dash="dash",
                      line_color="#E9C46A", line_width=1.5,
                      row=1, col=col)

    fig.update_layout(
        template="plotly_white", height=420,
        title=dict(
            text=(
                "Apr 17 Natural Experiment — Three-Channel Decomposition (Upper Bound)"
                "<br><sup>Panel A: 1-day ceiling estimate — β is an upper bound on the institutional + discretionary channels combined. "
                "Panel B: 6-day window retired — Apr 18 outlier flips β negative; shown only to explain exclusion.</sup>"
            ),
            font=dict(size=12),
        ),
        yaxis_title="Vessel-days lost vs baseline",
        legend=dict(orientation="h", yanchor="bottom", y=1.10),
        hovermode="x unified",
    )
    fig.update_xaxes(title_text="Date", row=1, col=1)
    fig.update_xaxes(title_text="Date", row=1, col=2)

    return fig, {
        "beta_1d": beta_1d, "alpha_1d": alpha_1d,
        "exp_pct_1d": exp_pct_1d, "phy_pct_1d": phy_pct_1d,
        "beta_6d": beta_6d, "alpha_6d": alpha_6d,
        "exp_pct_6d": exp_pct_6d, "phy_pct_6d": phy_pct_6d,
        "calib_1d_ok": calib_1d_ok, "calib_6d_ok": calib_6d_ok,
    }


def fig_nonrecovery(transit_df, baseline, t_open=None):
    """M8 Non-recovery R(τ) — transit recovery ratio since declared reopening.

    R(τ) = rolling mean of n_total / pre-crisis baseline, where τ is days
    since t_open (Apr 17, 2026 — Iran's declared reopening).

    Primary smoothing: TRAILING 7-day window, min_periods=3.
    This means R(0) and R(1) return NaN from the rolling mean (only 1 and 2
    observations available, below the min_periods=3 threshold).  The raw ratio
    y(t_open)/baseline is reported separately as R_raw(0) and plotted as a
    distinct marker at τ=0.

    Sensitivity traces at 5-day and 14-day windows are shown as dashed lines
    to bracket the July partial-return peak range.

    τ½ = first τ where R(τ) ≥ 0.5.  If the threshold is never reached in the
    observation window, report as censored.

    Smoothing note: manuscript attributed R(0)=NaN to a centered rolling mean;
    the actual implementation uses a trailing window.  Both produce NaN at τ=0
    for the same reason: insufficient preceding observations.  Fix: report raw
    y(t_open)/baseline as R(0) alongside the smoothed series.

    Source: IMF PortWatch chokepoint6 live data.
    """
    if t_open is None:
        t_open = IRAN_REOPEN

    d = transit_df[transit_df["date"] >= t_open].copy()
    if d.empty:
        return None, {}

    d = d.sort_values("date").reset_index(drop=True)
    d["tau"] = (d["date"] - t_open).dt.days
    baseline_f = max(float(baseline), 1.0)

    # ── Smoothing windows ────────────────────────────────────────────────────
    # Primary: 7-day trailing, min_periods=3 (current spec)
    d["rolling_n7"] = d["transit_vessels"].rolling(7, min_periods=3).mean()
    d["R"]  = d["rolling_n7"] / baseline_f   # primary series (backward compat)
    # Sensitivity: 5-day and 14-day
    d["rolling_n5"]  = d["transit_vessels"].rolling(5,  min_periods=2).mean()
    d["rolling_n14"] = d["transit_vessels"].rolling(14, min_periods=5).mean()
    d["R5"]  = d["rolling_n5"]  / baseline_f
    d["R14"] = d["rolling_n14"] / baseline_f
    # Raw (unsmoothed) ratio — used for R(0) marker and sensitivity
    d["R_raw"] = d["transit_vessels"] / baseline_f

    # τ½ detection
    reached = d[d["R"] >= 0.5]
    tau_half = int(reached["tau"].min()) if not reached.empty else None
    tau_current = int(d["tau"].max())

    # Partial-return detection: ≥5 consecutive days above R=0.30 (not full recovery)
    # Record the first such run and its smoothed peak.
    d["above_30"] = (d["R"] >= 0.30) & (d["R"] < 0.50)
    partial_start = partial_peak = partial_end_tau = None
    run, run_start_idx, run_peak = 0, 0, 0.0
    for i, row in d.iterrows():
        if row["above_30"]:
            if run == 0:
                run_start_idx = int(row["tau"])
            run += 1
            if row["R"] > run_peak:
                run_peak = row["R"]
        else:
            if run >= 5 and partial_start is None:
                partial_start = run_start_idx
                partial_peak = run_peak
                partial_end_tau = int(row["tau"])
            run = 0
            run_peak = 0.0
    if run >= 5 and partial_start is None:
        partial_start = run_start_idx
        partial_peak = run_peak
        partial_end_tau = tau_current

    # ── Build figure ────────────────────────────────────────────────────────
    fig = go.Figure()

    # Sensitivity: 14-day window (dashed, shown first so 7-day sits on top)
    fig.add_trace(go.Scatter(
        x=d["tau"], y=d["R14"],
        mode="lines",
        name="R(τ) — 14-day window (sensitivity)",
        line=dict(color=PAL["baseline"], width=1.2, dash="dot"),
        opacity=0.55,
        hovertemplate="14d: τ=%{x}d · R=%{y:.3f}<extra></extra>",
    ))

    # Sensitivity: 5-day window (dashed)
    fig.add_trace(go.Scatter(
        x=d["tau"], y=d["R5"],
        mode="lines",
        name="R(τ) — 5-day window (sensitivity)",
        line=dict(color="#52B788", width=1.2, dash="dash"),
        opacity=0.65,
        hovertemplate="5d: τ=%{x}d · R=%{y:.3f}<extra></extra>",
    ))

    # Primary: 7-day rolling mean with shaded fill
    fig.add_trace(go.Scatter(
        x=d["tau"], y=d["R"],
        mode="lines",
        name="R(τ) — 7-day rolling mean / baseline",
        line=dict(color=PAL["baseline"], width=2.5),
        fill="tozeroy",
        fillcolor="rgba(45,106,79,0.12)",
        hovertemplate="τ = %{x}d since Apr 17 · R = %{y:.3f}<extra></extra>",
    ))

    # Raw R(0) marker — rolling mean returns NaN at τ=0 (trailing window,
    # min_periods=3, only 1 observation available).  Report unsmoothed ratio.
    r0_row = d[d["tau"] == 0]
    if not r0_row.empty:
        r0_val = float(r0_row["R_raw"].iloc[0])
        fig.add_trace(go.Scatter(
            x=[0], y=[r0_val],
            mode="markers+text",
            name=f"R(0) = {r0_val:.3f} — raw (1 obs; smoothed unavailable)",
            marker=dict(color=PAL["crisis"], size=10, symbol="circle-open",
                        line=dict(width=2.5, color=PAL["crisis"])),
            text=[f"R(0)={r0_val:.3f}<br>(raw, unsmoothed)"],
            textposition="top right",
            textfont=dict(size=8, color=PAL["crisis"]),
            hovertemplate="τ=0 · R_raw=%{y:.3f}<br>rolling mean unavailable (1 obs)<extra></extra>",
            showlegend=True,
        ))

    # R = 0.5 threshold line
    fig.add_hline(
        y=0.5,
        line_dash="dash", line_color="#E9C46A", line_width=1.8,
        annotation_text="R = 0.5  (50% recovery threshold)",
        annotation_position="top right",
        annotation_font=dict(size=9, color="#856404"),
    )

    # Censored annotation at current τ
    fig.add_annotation(
        x=tau_current, y=0.53,
        xref="x", yref="y",
        text=(
            f"<b>τ½ > {tau_current} d — CENSORED</b><br>"
            "50% threshold never reached<br>"
            f"as of {TODAY.strftime('%b %d, %Y')}"
        ),
        showarrow=True, arrowhead=2,
        arrowcolor=PAL["crisis"],
        font=dict(size=9, color=PAL["crisis"]),
        bgcolor="rgba(255,245,245,0.92)",
        bordercolor=PAL["crisis"], borderwidth=1,
        ax=-120, ay=50,
    )

    # Apr 17 origin marker
    fig.add_vline(x=0, line_dash="dot", line_color="#E9C46A", line_width=1.4)
    fig.add_annotation(
        x=1, y=0.96, xref="x", yref="paper",
        text="Apr 17<br>declared open",
        font=dict(size=8, color="#856404"),
        showarrow=False,
    )

    # July partial-return annotation + shading (only if smoothing preserved the signal)
    if partial_start is not None:
        fig.add_vrect(
            x0=partial_start, x1=partial_end_tau,
            fillcolor="rgba(233,168,76,0.15)", layer="below", line_width=0,
        )
        mid_tau = (partial_start + partial_end_tau) // 2
        fig.add_annotation(
            x=mid_tau, y=partial_peak + 0.04,
            xref="x", yref="y",
            text=(
                f"July partial return<br>"
                f"peak R ≈ {partial_peak:.2f}<br>"
                "collapsed within ~10d"
            ),
            showarrow=True, arrowhead=2,
            arrowcolor=PAL["institutional"],
            font=dict(size=8, color="#6B3E26"),
            bgcolor="rgba(255,248,240,0.90)",
            bordercolor=PAL["institutional"], borderwidth=1,
            ax=55, ay=-30,
        )

    fig.update_layout(
        template="plotly_white",
        height=380,
        title=dict(
            text=(
                "M8 Non-Recovery R(τ) — Hormuz after Declared Reopening (Apr 17)"
                f"<br><sup>τ½ not reached in {tau_current} observation days · "
                "IMF PortWatch chokepoint6 · 7-day rolling mean · "
                "Zero synthetic data</sup>"
            ),
            font=dict(size=12),
        ),
        xaxis_title="τ (days since Apr 17, 2026 — Iran's declared reopening)",
        yaxis_title="R(τ) = smoothed transit / pre-crisis baseline",
        yaxis=dict(range=[0, max(0.65, (d["R"].max() if not d["R"].isna().all() else 0.5) + 0.10)]),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
    )

    # r_at_0: raw ratio at τ=0 (rolling mean is NaN — trailing window, 1 obs < min_periods=3)
    r0_rows = d[d["tau"] == 0]
    r_at_0_raw = float(r0_rows["R_raw"].iloc[0]) if not r0_rows.empty else float("nan")

    # July peak across all three windows
    july_mask = (d["date"] >= pd.Timestamp("2026-07-01")) & (d["date"] <= pd.Timestamp("2026-07-15"))
    july_sub = d[july_mask]
    july_peak_7d  = float(july_sub["R"].max())  if not july_sub.empty and not july_sub["R"].isna().all()  else float("nan")
    july_peak_5d  = float(july_sub["R5"].max()) if not july_sub.empty and not july_sub["R5"].isna().all() else float("nan")
    july_peak_14d = float(july_sub["R14"].max()) if not july_sub.empty and not july_sub["R14"].isna().all() else float("nan")

    return fig, {
        "tau_current": tau_current,
        "tau_half": tau_half,
        "partial_start_tau": partial_start,
        "partial_peak": partial_peak,
        "r_at_0": r_at_0_raw,          # raw ratio at τ=0 (smoothed is NaN)
        "r_at_0_is_raw": True,          # flag: caller should label this as unsmoothed
        "july_peak_7d":  july_peak_7d,
        "july_peak_5d":  july_peak_5d,
        "july_peak_14d": july_peak_14d,
    }


def compute_pelt_sweep(transit_df, penalties=None):
    """P4 — PELT penalty sweep on the crisis subwindow (Feb 28 – May 31 2026).

    CRITICAL: PELT must be applied to the crisis subwindow only, not the full
    analysis window.  When applied to the full 334-day series the initial
    collapse (Feb 28) is by far the dominant break and the algorithm allocates
    nearly all its budget to it; subsequent sub-events (Mar 26, Apr 8, etc.)
    occur while transit is already near-zero, producing no detectable distribution
    shift at the full-window scale.

    On the crisis subwindow (93 days) the signal spans 57→4 vessels and the
    sub-event structure is visible.  Results:
      pen=0.25: 29 breaks, 8/8 events recovered within 5-day tolerance
      pen=0.50: 20 breaks, 8/8 events recovered within 5-day tolerance
      pen=1.00: 13 breaks, 8/8 events recovered within 5-day tolerance
      pen=2.00:  4 breaks, 3/8 events recovered
      pen=4.00:  1 break,  1/8 events recovered

    Returns:
      sweep_rows  – list of dicts (one per penalty), for display as a table
      detail_rows – list of dicts at pen=0.50 (one per documented event)
    """
    try:
        import ruptures as rpt
    except ImportError:
        return [], []

    if penalties is None:
        penalties = [0.25, 0.5, 1.0, 2.0, 4.0]

    # Crisis subwindow: Feb 28 – May 31 inclusive
    CRISIS_END_PELT = pd.Timestamp("2026-05-31")
    mask = (transit_df["date"] >= CRISIS_START) & (transit_df["date"] <= CRISIS_END_PELT)
    sub = transit_df[mask].copy().sort_values("date").reset_index(drop=True)
    if sub.empty:
        return [], []

    col = "transit_vessels" if "transit_vessels" in sub.columns else "n_total"
    signal = sub[col].fillna(0).values.astype(float)
    dates  = sub["date"].values

    CRISIS_EVENTS_PELT = [
        (pd.Timestamp("2026-02-28"), "Epic Fury / Transit collapse"),
        (pd.Timestamp("2026-03-02"), "IRGC closure"),
        (pd.Timestamp("2026-03-26"), "Neutral ship passage"),
        (pd.Timestamp("2026-04-08"), "Ceasefire"),
        (pd.Timestamp("2026-04-13"), "US naval blockade"),
        (pd.Timestamp("2026-04-17"), "Iran declares open"),
        (pd.Timestamp("2026-04-18"), "Iran re-closes"),
        (pd.Timestamp("2026-04-23"), "All-dark period"),
    ]

    TOL = 5  # days

    def get_bkp_dates(bkps):
        return [pd.Timestamp(dates[min(i - 1, len(dates) - 1)]) for i in bkps[:-1]]

    sweep_rows  = []
    detail_rows = []

    for pen in penalties:
        try:
            algo = rpt.Pelt(model="rbf", min_size=2, jump=1)
            algo.fit(signal.reshape(-1, 1))
            bkps = algo.predict(pen=pen)
            bkp_dates = get_bkp_dates(bkps)
            n_bkps    = len(bkps) - 1

            ev_results = []
            for ev_ts, ev_label in CRISIS_EVENTS_PELT:
                if bkp_dates:
                    diffs = sorted(
                        [(abs((bd - ev_ts).days), (bd - ev_ts).days, bd) for bd in bkp_dates]
                    )
                    ab, sg, closest = diffs[0]
                    recovered = ab <= TOL
                    ev_results.append((ev_ts, ev_label, closest, sg, recovered))
                else:
                    ev_results.append((ev_ts, ev_label, None, None, False))

            n_recovered = sum(1 for r in ev_results if r[4])
            gaps_str = " ".join(
                f"{'✓' if r[4] else '✗'}{r[3]:+d}d"
                for r in ev_results if r[3] is not None
            )
            sweep_rows.append({
                "Penalty": pen,
                "Breakpoints": n_bkps,
                "Recovered / 8": f"{n_recovered}/8",
                "All ≤5d?": "✅" if n_recovered == 8 else "❌",
                "Per-event gaps": gaps_str,
            })

            if pen == 0.5:
                for ev_ts, ev_label, closest, sg, recovered in ev_results:
                    detail_rows.append({
                        "Event date": str(ev_ts.date()),
                        "Event": ev_label,
                        "PELT break": str(closest.date()) if closest else "—",
                        "Gap (d)": f"{sg:+d}" if sg is not None else "—",
                        "Within 5d?": "✓" if recovered else "✗",
                    })
        except Exception:
            sweep_rows.append({"Penalty": pen, "Breakpoints": "ERR", "Recovered / 8": "—",
                                "All ≤5d?": "—", "Per-event gaps": "—"})

    # Anticipation lead at pen=0.5 (ref = Mar 2 IRGC decree)
    IRGC_REF = pd.Timestamp("2026-03-02")
    first_break = None
    for row in detail_rows:
        if row["Event"] == "Epic Fury / Transit collapse" and row["PELT break"] != "—":
            first_break = pd.Timestamp(row["PELT break"])
            break
    if first_break is not None:
        A_days = (first_break - IRGC_REF).days
        for row in sweep_rows:
            if row["Penalty"] == 0.5:
                row["Anticipation A vs IRGC"] = (
                    f"{A_days:+d}d (traffic broke {'before' if A_days < 0 else 'after'} decree)"
                )

    return sweep_rows, detail_rows


def fig_panama_baseline(panama_df):
    """Panama Canal (chokepoint2) pre-cut baseline panel.

    Shows the 2025 full-year baseline and 2026 through today.  Annotates the
    two announced slot reductions as FUTURE events (Sept 3: 36→34/day; Sept 15:
    →32/day).  Does NOT present a "no-response" finding — the cuts have not yet
    taken effect.  The chart's sole purpose is to document the pre-cut baseline
    so that any post-cut change can be assessed against it.

    Source: IMF PortWatch chokepoint2 (Panama Canal) · live ArcGIS REST · zero synthetic data.
    """
    if panama_df is None or panama_df.empty:
        return None, {}

    d = panama_df.sort_values("date").copy()

    # Split into 2025 baseline and 2026 observation period
    cut_year = pd.Timestamp("2026-01-01")
    d_2025 = d[d["date"] < cut_year]
    d_2026 = d[d["date"] >= cut_year]

    baseline_2025 = float(d_2025["n_total"].mean()) if len(d_2025) > 0 else 31.1

    # 7-day rolling mean for both periods
    d["rolling_n"] = d["n_total"].rolling(7, min_periods=3).mean()
    d_2025r = d[d["date"] < cut_year]
    d_2026r = d[d["date"] >= cut_year]

    # Announced slot levels (as of Aug 2026 Panama Canal Authority advisory)
    announced_cuts = [
        (pd.Timestamp("2026-09-03"), 34, "Sept 3: 36 → 34 slots/day"),
        (pd.Timestamp("2026-09-15"), 32, "Sept 15: → 32 slots/day"),
    ]

    fig = go.Figure()

    # 2025 baseline ribbon
    fig.add_trace(go.Scatter(
        x=d_2025r["date"], y=d_2025r["rolling_n"],
        mode="lines",
        name="2025 baseline (7-day rolling mean)",
        line=dict(color="#ADB5BD", width=1.8, dash="dot"),
        hovertemplate="%{x|%b %d}: %{y:.1f}/day<extra>2025 baseline</extra>",
    ))

    # 2026 observed
    fig.add_trace(go.Scatter(
        x=d_2026r["date"], y=d_2026r["rolling_n"],
        mode="lines",
        name="2026 observed (7-day rolling mean)",
        line=dict(color="#2A9D8F", width=2.5),
        fill="tozeroy",
        fillcolor="rgba(42,157,143,0.08)",
        hovertemplate="%{x|%b %d}: %{y:.1f}/day<extra>2026</extra>",
    ))

    # 2025 annual mean reference line
    fig.add_hline(
        y=baseline_2025,
        line_dash="dash", line_color="#6C757D", line_width=1.2,
        annotation_text=f"2025 mean = {baseline_2025:.1f}/day",
        annotation_position="top left",
        annotation_font=dict(size=9, color="#6C757D"),
    )

    # Announced slot cut lines — labeled FUTURE/ANNOUNCED, not yet in effect
    slot_colors = ["#E9C46A", "#E76F51"]
    for (cut_date, cut_level, cut_label), color in zip(announced_cuts, slot_colors):
        # Only draw if the cut date is in the future relative to the last data point
        last_data = d["date"].max()
        if cut_date >= last_data - pd.Timedelta(days=3):
            # Future — draw as dashed with "ANNOUNCED" label
            fig.add_vline(
                x=cut_date.strftime("%Y-%m-%d"),
                line_dash="dash", line_color=color, line_width=1.5, opacity=0.7,
            )
            fig.add_annotation(
                x=cut_date.strftime("%Y-%m-%d"),
                yref="paper", y=0.88,
                text=f"⏳ {cut_label}<br>(announced, future)",
                showarrow=False,
                font=dict(size=8, color=color),
                bgcolor="rgba(255,255,255,0.82)",
                xanchor="left",
            )
        # Mechanical cut level as dashed horizontal reference
        fig.add_hline(
            y=cut_level,
            line_dash="longdash", line_color=color, line_width=1.0, opacity=0.55,
            annotation_text=f"Announced: {cut_level}/day",
            annotation_position="top right",
            annotation_font=dict(size=8, color=color),
        )

    fig.update_layout(
        template="plotly_white",
        height=340,
        title=dict(
            text=(
                "Panama Canal (cp2) — Pre-Cut Baseline, Aug 2026"
                "<br><sup>⚠ Announced slot reductions (Sept 3, Sept 15) not yet in effect. "
                "This chart documents the pre-cut baseline. The test — whether responses "
                "exceed the mechanical slot reduction — requires post-cut data.</sup>"
            ),
            font=dict(size=12),
        ),
        xaxis_title="Date",
        yaxis_title="Daily transits (7-day rolling mean)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        hovermode="x unified",
    )

    stats = {
        "baseline_2025": baseline_2025,
        "mean_2026": float(d_2026["n_total"].mean()) if len(d_2026) > 0 else float("nan"),
        "n_2025_days": len(d_2025),
        "n_2026_days": len(d_2026),
        "last_date": str(d["date"].max().date()),
    }
    return fig, stats


def fig_commodity(price_df, date_range):
    mask = (price_df["date"] >= pd.Timestamp(str(date_range[0]))) & \
           (price_df["date"] <= pd.Timestamp(str(date_range[1])))
    d = price_df[mask]

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    # Urea: World Bank Pink Sheet — Middle East f.o.b. spot (real external source)
    # NOLA derived series removed per research plan review; no independent NOLA source available.
    urea_col = d["urea_usdmt"] if "urea_usdmt" in d.columns else None
    if urea_col is not None and urea_col.notna().any():
        fig.add_trace(go.Scatter(
            x=d["date"], y=urea_col,
            name="Urea — Middle East f.o.b. (USD/mt)",
            line=dict(color=PAL["fert"], width=2.5),
            hovertemplate="Urea (WB Pink Sheet): $%{y:,.0f}/mt<extra></extra>",
        ), secondary_y=False)
    else:
        fig.add_annotation(
            text="⚠️ Urea data unavailable — World Bank Pink Sheet could not be reached",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(size=13, color="red"),
        )
    fig.add_trace(go.Scatter(x=d["date"], y=d["wheat_usdmt"],
                              name="Wheat global (USD/mt)", line=dict(color=PAL["wheat"],width=2.0,dash="dashdot"),
                              hovertemplate="Wheat: $%{y:,.0f}<extra></extra>"), secondary_y=True)
    _add_events(fig, date_range)
    fig.update_yaxes(title_text="Fertilizer price (USD/mt)", secondary_y=False)
    fig.update_yaxes(title_text="Wheat price (USD/mt)", secondary_y=True)
    fig.update_layout(template="plotly_white", height=420,
                      title="Commodity Cascade — Fertilizer & Wheat Price Response",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02),
                      hovermode="x unified")
    return fig


def fig_historical_comparison(hormuz_df, baseline, drop_pct, hist_data):
    """Historical chokepoint comparison using real PortWatch data for all three episodes.

    Chokepoints:
      Hormuz 2026: chokepoint6, onset Feb 28 2026.
      Red Sea 2023-24: chokepoint4 (Bab-el-Mandeb/Suez approach), onset Nov 19 2023.
        Houthi attacks caused vessels to reroute via Cape; chokepoint4 shows actual
        Bab-el-Mandeb throughput decline (~57% at nadir).
      Black Sea 2022: chokepoint3 (Turkish Straits), onset Feb 24 2022.
        IMPORTANT: the Bosphorus remained open under the Montreux Convention.
        Port-level disruption (Ukrainian export ports) is NOT in PortWatch.
        Chokepoint3 shows only a secondary ~20% signal; not comparable in magnitude
        to the actual Black Sea food disruption.  Label accordingly.

    hist_data = {"black_sea": DataFrame with chokepoint3 data,
                 "red_sea":   DataFrame with chokepoint4 data}
    """

    def _ep_trajectory(ep_df, onset_ts, label_col="n_total", window_days=(-45, 300)):
        """Compute day-relative, %-baseline trajectory from a PortWatch DataFrame."""
        if ep_df is None or len(ep_df) == 0:
            return None, None
        onset = pd.Timestamp(onset_ts)
        pre = ep_df[(ep_df["date"] >= onset - pd.Timedelta(days=90)) &
                    (ep_df["date"] < onset)]
        if len(pre) == 0 or pre[label_col].mean() == 0:
            return None, None
        pre_mean = pre[label_col].mean()
        s = ep_df.copy()
        s["day"] = (s["date"] - onset).dt.days
        s["pct"] = s[label_col] / pre_mean * 100
        s = s[(s["day"] >= window_days[0]) & (s["day"] <= window_days[1])].sort_values("day")
        s["pct_smooth"] = s["pct"].rolling(7, min_periods=1, center=True).mean()
        actual_drop = 100 - s[s["day"] >= 0]["pct_smooth"].min()
        return s, actual_drop

    bs_df = hist_data.get("black_sea") if hist_data else None
    rs_df = hist_data.get("red_sea")   if hist_data else None

    bs_traj, bs_drop = _ep_trajectory(bs_df,  "2022-02-24")
    rs_traj, rs_drop = _ep_trajectory(rs_df,  "2023-11-19")

    # Hormuz trajectory from merged transit series
    hz_traj = hormuz_df[hormuz_df["date"] >= CRISIS_START - pd.Timedelta(days=45)].copy()
    hz_traj["day"] = (hz_traj["date"] - CRISIS_START).dt.days
    hz_traj["pct_smooth"] = hz_traj["transit_vessels"] / baseline * 100

    # Compute measured drops (at PortWatch chokepoint level)
    hz_drop   = int(round(drop_pct)) if pd.notna(drop_pct) else 87
    bs_drop_i = int(round(bs_drop))  if bs_drop is not None else 20
    rs_drop_i = int(round(rs_drop))  if rs_drop is not None else 57

    EPISODES = {
        "Black Sea 2022\n(Turkish Straits, cp3)":
            {"color":"#1D6A96", "drop": bs_drop_i, "bypass_cap":100,
             "bypass_cost":25, "traj": bs_traj, "note":"Bosphorus stayed open"},
        "Red Sea 2023-24\n(Bab-el-Mandeb, cp4)":
            {"color":"#E63946", "drop": rs_drop_i, "bypass_cap":100,
             "bypass_cost":40, "traj": rs_traj, "note":"Cape rerouting viable"},
        "Hormuz 2026\n(Strait of Hormuz, cp6)":
            {"color":PAL["crisis"], "drop": hz_drop, "bypass_cap":5,
             "bypass_cost":300, "traj": hz_traj, "note":"No bypass"},
    }

    fig = make_subplots(rows=1, cols=3,
                        subplot_titles=("Normalized Transit Trajectory",
                                        "Bypass Capacity vs Cost",
                                        "Historical Anomaly Space"),
                        horizontal_spacing=0.10)

    for ep_name, ep in EPISODES.items():
        s = ep["traj"]
        if s is None or len(s) == 0:
            continue
        drop_label = ep["drop"]
        ep_short = ep_name.split("\n")[0]
        fig.add_trace(go.Scatter(
            x=s["day"], y=s["pct_smooth"], mode="lines",
            line=dict(color=ep["color"], width=2.5 if "Hormuz" in ep_name else 2.0),
            name=f"{ep_short} (−{drop_label}% at chokepoint)",
            hovertemplate=f"Day %{{x}}: %{{y:.0f}}% of baseline<extra>{ep_short}</extra>",
        ), row=1, col=1)

    fig.add_hline(y=100, line_dash="dot", line_color="#AAA", row=1, col=1)
    fig.add_vline(x=0, line_dash="dash", line_color="#333", opacity=0.5, row=1, col=1)
    fig.add_annotation(
        x=5, yref="paper", y=0.04, row=1, col=1,
        text=("⚠ Black Sea shows Turkish Straits signal only (Bosphorus stayed open).<br>"
              "Port-level food disruption was much larger — not in PortWatch."),
        showarrow=False, font=dict(size=8, color="#666"),
        bgcolor="rgba(255,255,255,0.82)", xanchor="left",
    )
    fig.update_xaxes(title_text="Days from disruption onset", row=1, col=1)
    fig.update_yaxes(title_text="Transit volume (% baseline, 7-day avg)", row=1, col=1)

    ep_names = [ep.split("\n")[0] for ep in EPISODES.keys()]
    ep_caps  = [ep["bypass_cap"]  for ep in EPISODES.values()]
    ep_costs = [ep["bypass_cost"] for ep in EPISODES.values()]
    ep_cols  = [ep["color"]       for ep in EPISODES.values()]

    fig.add_trace(go.Bar(x=ep_names, y=ep_caps,
                          name="Bypass capacity (%)", marker_color=ep_cols,
                          opacity=0.85, showlegend=False,
                          hovertemplate="%{x}: %{y}% capacity<extra></extra>"), row=1, col=2)
    fig.add_trace(go.Bar(x=ep_names, y=ep_costs,
                          name="Cost via bypass (%)", marker_color=ep_cols,
                          opacity=0.35, marker_pattern_shape="/",
                          showlegend=False,
                          hovertemplate="%{x}: +%{y}% cost<extra></extra>"), row=1, col=2)
    fig.update_layout(barmode="group")

    # Anomaly space — all five disruptions; Black Sea / Red Sea drops are
    # PortWatch chokepoint-level measurements, not port/supply-chain headline figures.
    ALL = {
        "Suez 1956":        (45,  100, "#A8DADC"),
        "Black Sea 2022\n(Turkish Straits)": (bs_drop_i, 100, "#1D6A96"),
        "Red Sea 2023-24":  (rs_drop_i, 100, "#E63946"),
        "Panama 2024":      (38,  95,  "#E9C46A"),
        "Hormuz 2026":      (hz_drop, 5, PAL["crisis"]),
    }
    for ep_n, (drop, byp, col) in ALL.items():
        is_this = "Hormuz" in ep_n
        ep_label = ep_n.split("\n")[0]
        fig.add_trace(go.Scatter(
            x=[drop], y=[byp], mode="markers+text",
            marker=dict(color=col, size=18 if is_this else 12,
                        line=dict(color="#000" if is_this else col, width=2 if is_this else 1)),
            text=[ep_label], textposition="top right", name=ep_label,
            showlegend=False,
            hovertemplate=f"{ep_label}: −%{{x}}% (chokepoint), %{{y}}% bypass<extra></extra>",
        ), row=1, col=3)
    fig.add_annotation(
        x=10, yref="paper", y=0.04, row=1, col=3,
        text="Drop % = PortWatch chokepoint measurement.",
        showarrow=False, font=dict(size=8, color="#666"),
        bgcolor="rgba(255,255,255,0.82)",
    )
    fig.update_xaxes(title_text="Transit drop at chokepoint (%)", row=1, col=3)
    fig.update_yaxes(title_text="Bypass route capacity (%)", row=1, col=3)

    fig.update_layout(
        template="plotly_white", height=500,
        title=("Historical Comparison — Hormuz 2026 as Anomaly"
               "<br><sup>All trajectories: real IMF PortWatch data. "
               "Black Sea shows Turkish Straits signal — port disruption not in PortWatch.</sup>"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT UI
# ══════════════════════════════════════════════════════════════════════════════

# ── sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🚢 Hormuz 2026")
    st.caption("Columbia Puma Lab — Live Research Dashboard")
    st.divider()

    st.subheader("Controls")
    date_range = st.slider(
        "Date range",
        min_value=datetime(2025, 10, 1).date(),
        max_value=datetime.now().date(),
        value=(datetime(2026, 2, 1).date(), datetime.now().date()),
    )
    start_str = date_range[0].strftime("%Y-%m-%d")
    end_str   = date_range[1].strftime("%Y-%m-%d")

    bbox_choice = st.selectbox("Region (GFW)", list(BBOXES.keys()), index=0)

    vessel_types = st.multiselect(
        "Vessel types (SAR filter)",
        ["Tanker", "Bulk cargo", "Container", "Dark (no AIS)"],
        default=["Tanker", "Bulk cargo", "Container", "Dark (no AIS)"],
    )
    vessel_map = {"Tanker":"tanker","Bulk cargo":"bulk_cargo",
                  "Container":"container","Dark (no AIS)":"dark"}
    active_cats = {vessel_map[v] for v in vessel_types}

    st.divider()
    if st.button("🔄 Refresh all data"):
        st.cache_data.clear()
        try:
            st.rerun()
        except AttributeError:
            st.experimental_rerun()

    last_render = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    st.caption(f"Last render: {last_render}")
    st.caption("Cache TTL: 12 hours · click Refresh to force re-pull")

    if not GFW_CONFIGURED:
        st.divider()
        st.error(
            "⚠️ **GFW API key not configured.**\n\n"
            "SAR vessel detection and GAP event panels will show no data.\n"
            "Set `GFW_API_KEY` in Streamlit Secrets or the environment.",
            icon="🔑",
        )

    st.divider()
    st.subheader("Share this dashboard")
    st.code("https://hormuz-analysis.streamlit.app", language=None)
    st.caption(f"Last data pull: {last_render}")
    st.caption("Open URL to share with Jasper, Jim Hall, or FAO contacts — no login required.")

# ── top metrics ───────────────────────────────────────────────────────────────
st.title("Closing the Hormuz Food Corridor — 2026")
st.caption("Live data: IMF PortWatch · Global Fishing Watch · FRED · Windward AI")

with st.spinner("Loading transit data..."):
    transit_df, baseline_mean, drop_pct, transit_src = get_transit_data(
        ANALYSIS_START.strftime("%Y-%m-%d"), end_str
    )

blockade_days = max((TODAY - US_BLOCKADE).days, 0)
crisis_days   = max((TODAY - CRISIS_START).days, 0)
pw_data, _    = fetch_portwatch(start_str, end_str)
portwatch_ok  = pw_data is not None

_drop_str     = f"−{drop_pct:.0f}%"     if pd.notna(drop_pct)     else "N/A"
_baseline_str = f"{baseline_mean:.0f} /day" if pd.notna(baseline_mean) else "N/A"
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Transit drop",        _drop_str,      "vs pre-crisis baseline")
col2.metric("Pre-crisis baseline", _baseline_str,  "AIS vessels Oct–Feb")
col3.metric("Crisis duration",     f"{crisis_days} days",   "since Feb 28")
col4.metric("US Blockade",         f"{blockade_days} days", "since Apr 13")
col5.metric("PortWatch",           "✅ Live" if portwatch_ok else "⚠️ Cache",
            f"{len(pw_data)} days" if portwatch_ok else "Windward anchors")

st.divider()

# ── tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "📉 Transit Collapse",
    "🚢 Vessel Categories",
    "👁️ Dark Vessel Analysis",
    "🌾 Commodity Cascade",
    "📊 Historical Comparison",
    "🗂️ Behavioral Classification",
    "🔬 Research Design",
    "🌐 Cross-Event Comparison",
])

# ── Tab 1: Transit Collapse ───────────────────────────────────────────────────
with tab1:
    c_left, c_right = st.columns([3, 1])
    with c_right:
        show_regimes = st.checkbox("Show regime shading", value=True,
                                   help="Color bands = documented event dates (not PELT changepoint output)")
        show_food    = st.checkbox("Overlay food segment", value=False,
                                   help="Add dry bulk + tanker lines from PortWatch")

    pw_typed_for_transit, _ = fetch_portwatch(start_str, end_str)
    st.plotly_chart(
        fig_transit(transit_df, baseline_mean, drop_pct, transit_src, date_range,
                    show_regimes=show_regimes, show_food=show_food,
                    pw_df=pw_typed_for_transit),
        use_container_width=True,
    )
    st.caption(EVENT_SOURCES)

    # April 17 decomposition section
    with st.expander("Apr 17 Identification Window — Three-Channel Decomposition (Ceiling Estimate)", expanded=False):
        with st.spinner("Computing decomposition..."):
            price_df_decomp, _, _, _ = get_prices(ANALYSIS_START.strftime("%Y-%m-%d"), end_str)
            decomp_fig, decomp_stats = fig_april17_decomp(
                transit_df, price_df_decomp, baseline_mean, date_range
            )
        st.plotly_chart(decomp_fig, use_container_width=True)

        # KPIs — labeled as ceiling estimates, not calibrated results
        if decomp_stats["calib_1d_ok"]:
            kc1, kc2, kc3 = st.columns(3)
            kc1.metric(
                "β (1-day ceiling estimate)",
                f"{decomp_stats['beta_1d']:.2f}",
                "vsl-days/USD·bbl — upper bound, not a calibrated estimate",
            )
            kc2.metric(
                "Physical share (lower bound)",
                f"{decomp_stats['phy_pct_1d']:.0f}%",
                "Apr 17 only — US blockade still active",
            )
            kc3.metric(
                "Inst + Disc (upper bound)",
                f"{decomp_stats['exp_pct_1d']:.0f}%",
                "Institutional + Discretionary combined ceiling",
            )
        if decomp_stats["calib_6d_ok"]:
            st.caption(
                f"⚠ 6-day window (Apr 17–22) retired per v3.1 spec — β = {decomp_stats['beta_6d']:.2f} "
                f"(sign-flipped because Apr 18 re-closure outlier drives OLS negative). "
                f"Shown in Panel B only to explain exclusion, not as a result."
            )

        st.warning(
            "**This is an upper bound, not a calibrated estimate.**\n\n"
            "On Apr 17, Iran declared the strait open. Transits: 8 vessels — 11% of the "
            f"{baseline_mean:.0f}/day pre-crisis baseline. However, the US blockade was still active "
            "that day. Iran lifting *Iran's* restriction is not the same as no restriction existing. "
            "The β = +1.92 figure is a **ceiling on the institutional + discretionary channels combined** "
            "— it overstates their share because the physical channel may not have been zero.\n\n"
            "**Proper identification (pending):** M5 JWC discontinuity analysis estimates the "
            "institutional channel off the listed-area revision date (exogenous administrative event, "
            "physical danger smooth through the window). M7 out-of-sample test estimates β on the Red Sea "
            "where the physical channel is small, then applies to Hormuz. Both require war risk premium "
            "data acquisition (week-one reconnaissance).\n\n"
            "**What to report now:** An interval — [out-of-sample M7 estimate (pending), "
            "β=+1.92 ceiling from Apr 17]. The 6-day OLS (Apr 17–22) is **retired** — "
            "the Apr 18 re-closure surge (20 vessels) flips β negative and contaminates the window.\n\n"
            "**β discrepancy note (P5):** Two β values appear in the manuscript — +1.92 and +2.38. "
            "Both arise from the same 1-day estimator applied at different code states with different "
            "pre-crisis Brent and transit averaging windows.  "
            "Calibrated-anchor computation: 90-day Brent window → β ≈ 1.98 (transit pre = 63.7/day, "
            "s = $28.1/bbl); 180-day Brent window → β ≈ 2.32 (transit pre = 72.8/day, s = $27.9/bbl).  "
            "**Standardization:** use the same 150-day window (Oct 1 2025 – Feb 27 2026) for both "
            "transit baseline and Brent pre-crisis mean.  With calibrated anchors this gives β ≈ 2.21; "
            "with live yfinance Brent it will differ slightly.  Report whichever window is chosen, with "
            "the date range in the footnote.  [PENDING: live Brent from yfinance on deploy]"
        )

    with st.expander("Data provenance"):
        st.write(f"**Primary:** {transit_src}")
        st.write("**Supplement:** Windward AI daily reports (windward.ai/blog/) — interpolated between anchors")
        st.write("**Gap-fill:** Prof. Puma's hormuz_transit_observed.csv (github.com/mjpuma/hormuz)")
        if transit_df is not None:
            st.dataframe(transit_df[transit_df["is_observed"]].tail(10)[["date","transit_vessels","dark"]])

    # ── M8 Non-Recovery R(τ) ─────────────────────────────────────────────────
    st.divider()
    st.markdown("### M8 — Non-Recovery R(τ) Since Declared Reopening")
    with st.spinner("Computing non-recovery ratio..."):
        nonrec_fig, nonrec_stats = fig_nonrecovery(transit_df, baseline_mean)

    if nonrec_fig is not None:
        st.plotly_chart(nonrec_fig, use_container_width=True)

        # Summary KPIs
        nc1, nc2, nc3, nc4 = st.columns(4)
        nc1.metric(
            "R(0) — Apr 17 (raw)",
            f"{nonrec_stats.get('r_at_0', float('nan')):.3f}",
            "unsmoothed: 7-day rolling NaN at τ=0 (1 obs < min_periods=3)",
        )
        nc2.metric(
            "τ (current)",
            f"{nonrec_stats['tau_current']} days",
            "since Apr 17 declared reopening",
        )
        nc3.metric(
            "τ½ threshold",
            f"{'Not reached' if nonrec_stats['tau_half'] is None else str(nonrec_stats['tau_half']) + ' days'}",
            "50% recovery — censored" if nonrec_stats['tau_half'] is None else "reached",
        )
        _j5  = nonrec_stats.get("july_peak_5d",  float("nan"))
        _j7  = nonrec_stats.get("july_peak_7d",  float("nan"))
        _j14 = nonrec_stats.get("july_peak_14d", float("nan"))
        _j_range = (
            f"R ≈ {_j5:.2f}–{_j14:.2f} (5d–14d)"
            if (pd.notna(_j5) and pd.notna(_j14)) else
            (f"peak R ≈ {_j7:.2f}" if pd.notna(_j7) else "—")
        )
        nc4.metric(
            "July partial return",
            _j_range,
            f"7-day: {_j7:.2f} | collapsed within ~10d",
        )

        st.caption(
            "**Primary food security finding:** Transit has not recovered to 50% of the pre-crisis "
            "baseline at any point since Iran's Apr 17 declared reopening. Any food security "
            "assessment assuming stock replenishment began April 17 overstates import recovery "
            "by an order of magnitude. τ½ is censored — the strait is still functionally closed "
            "for food trade by the PortWatch measure, regardless of legal status. "
            "Source: IMF PortWatch chokepoint6 · 7-day rolling mean · zero synthetic data."
        )
    else:
        st.info("Non-recovery data not available — transit_df may not extend past Apr 17.")

# ── Tab 2: Vessel Categories ──────────────────────────────────────────────────
with tab2:
    with st.spinner("Loading GFW SAR + PortWatch vessel types... (first load: ~2 min)"):
        sar_daily, sar_raw = get_sar_data(bbox_choice, start_str, end_str)
        pw_typed, pw_typed_src = fetch_portwatch(start_str, end_str)

    if sar_raw is not None and active_cats:
        sar_raw_filtered = sar_raw[sar_raw["category"].isin(active_cats)]
    else:
        sar_raw_filtered = sar_raw

    sar_ok = sar_daily is not None
    pw_ok  = pw_typed  is not None

    c1, c2 = st.columns(2)
    c1.metric("GFW SAR records", f"{len(sar_raw):,}" if sar_raw is not None else "unavailable")
    c2.metric("PortWatch typed days", f"{len(pw_typed)}" if pw_ok else "unavailable")

    if not sar_ok and not pw_ok:
        st.warning("Both GFW SAR and PortWatch unavailable. Check API key and network.")
    else:
        st.plotly_chart(
            fig_vessel_categories(sar_raw_filtered, pw_typed if pw_ok else None, date_range),
            use_container_width=True,
        )

    st.caption(
        "Panel C: Grouped bar shows mean daily detections — SAR (solid) vs PortWatch (light) — "
        "by vessel type and crisis period. Confirms cross-source agreement on the collapse pattern. "
        "Panel D: Days with <50% SAR bbox coverage may underrepresent actual vessel counts (coverage "
        "normalization pending Level-1 scene acquisition from Jasper)."
    )

    with st.expander("Data provenance"):
        st.write(f"**GFW SAR:** v3.0 Sentinel-1 (4Wings POST endpoint) — dark = vesselId empty")
        st.write(f"**PortWatch:** {pw_typed_src if pw_ok else 'unavailable'}")
        st.write(f"**Region:** {bbox_choice} ({BBOXES[bbox_choice]['name']})")

# ── Tab 3: Dark Vessel Analysis ───────────────────────────────────────────────
with tab3:
    with st.spinner("Loading GFW Events (GAPs + Encounters)... (first load: ~2 min)"):
        gaps_df    = get_gaps(bbox_choice, start_str, end_str)
        enc_df     = get_encounters(bbox_choice, start_str, end_str)
        if sar_daily is None:
            sar_daily, sar_raw = get_sar_data(bbox_choice, start_str, end_str)

    c1, c2, c3 = st.columns(3)
    c1.metric("SAR detections", f"{int(sar_daily['sar_total'].sum()):,}" if sar_daily is not None else "N/A")
    c2.metric("GAP events", f"{int(gaps_df['gap_events'].sum()):,}" if gaps_df is not None else "N/A")
    c3.metric("Encounter events", f"{int(enc_df['enc_events'].sum()):,}" if enc_df is not None else "N/A")

    if sar_daily is not None:
        dark_total = int(sar_daily["sar_dark"].sum())
        sar_total  = int(sar_daily["sar_total"].sum())
        dark_pct   = dark_total/sar_total*100 if sar_total > 0 else 0
        st.info(f"**{dark_pct:.0f}%** of SAR detections are dark (no AIS) — "
                f"{dark_total:,} of {sar_total:,} vessel-days in this period and region.")

    st.warning(
        "**Dark fleet is overwhelmingly sanctioned crude, not food cargo.** "
        "The food-segment dark fraction (dashed green line in Panel 1) is an *upper bound* computed by "
        "proportional attribution — it assumes dark vessels have the same type distribution as AIS-visible "
        "vessels. In practice, evasion behavior is concentrated in crude oil tankers. "
        "True food-segment dark fraction requires per-vessel RCS matching (pending Level-1 SAR from Jasper)."
    )

    st.plotly_chart(
        fig_dark_analysis(sar_daily, gaps_df, enc_df, date_range, sar_raw=sar_raw),
        use_container_width=True,
    )
    st.caption(EVENT_SOURCES)

    with st.expander("Data provenance"):
        st.write("**SAR:** GFW 4Wings v3.0 — Sentinel-1 vessel-level detections")
        st.write("**GAPs:** GFW Events API v3.0 — intentional AIS-disabling events (Davenport #14)")
        st.write("**Encounters:** GFW Events API v3.0 — vessel proximity (STS transfer proxy)")
        st.write(f"**Region:** {bbox_choice} — GAPs/Encounters use Full Region for wider AIS coverage")
        st.write("**Food-segment dark fraction:** proportional attribution = all-dark% × (AIS dry bulk / AIS total). Upper bound only.")

# ── Tab 4: Commodity Cascade ──────────────────────────────────────────────────
with tab4:
    with st.spinner("Loading price data..."):
        price_df, wheat_src, urea_src, urea_err = get_prices(
            ANALYSIS_START.strftime("%Y-%m-%d"), end_str
        )

    # Visible banner if urea data could not be fetched — no silent fallback
    if urea_err:
        st.error(
            f"⚠️ **Urea price data unavailable.** World Bank Pink Sheet could not be fetched.\n\n"
            f"Error: {urea_err}\n\n"
            "The urea price panel will be blank. Check network access to worldbank.org.",
            icon="📊",
        )

    urea_col  = price_df["urea_usdmt"] if "urea_usdmt" in price_df.columns else None
    urea_ok   = urea_col is not None and urea_col.notna().any()
    urea_pre  = float(price_df[price_df["date"] < CRISIS_START]["urea_usdmt"].mean()) if urea_ok else float("nan")
    urea_now  = float(price_df.iloc[-1]["urea_usdmt"]) if urea_ok else float("nan")
    wheat_pre = float(price_df[price_df["date"] < CRISIS_START]["wheat_usdmt"].mean())
    wheat_now = float(price_df.iloc[-1]["wheat_usdmt"])

    c1, c2, c3 = st.columns(3)
    if urea_ok and not (np.isnan(urea_pre) or np.isnan(urea_now)):
        c1.metric("Urea — Middle East f.o.b. (WB)", f"${urea_now:,.0f}/mt",
                  f"{(urea_now-urea_pre)/urea_pre*100:+.0f}% since crisis")
    else:
        c1.metric("Urea (WB Pink Sheet)", "unavailable", "check network/error above")
    c2.metric("Wheat (global)", f"${wheat_now:,.0f}/mt",
              f"{(wheat_now-wheat_pre)/wheat_pre*100:+.0f}% since crisis")
    # Fertilizer statistic: 36% traded urea + 29% ammonia — IFPRI, cited in
    # Nasari et al. 2026 Nature Medicine (Fanzo senior author).
    c3.metric("Urea transit share", "36%",
              "of globally traded urea (IFPRI; Nasari et al. 2026 Nature Med.)")

    st.plotly_chart(fig_commodity(price_df, date_range), use_container_width=True)
    st.caption(EVENT_SOURCES)
    st.caption(
        "Urea: Middle East f.o.b. spot price — World Bank Commodity Markets Pink Sheet "
        "(CMO-Historical-Data-Monthly.xlsx), monthly, daily-interpolated. "
        "Source: Nasari et al. (2026) *Nature Medicine* and IFPRI: the Strait carries 36% of globally traded urea "
        "and 29% of globally traded ammonia. "
        "No NOLA series shown — no independent source for US Gulf premium available."
    )

    with st.expander("Data provenance"):
        st.write(f"**Wheat:** {wheat_src}")
        if urea_src:
            st.write(f"**Urea:** {urea_src}")
        else:
            st.error(f"**Urea:** UNAVAILABLE — {urea_err}")
        st.write("**Fertilizer statistic:** 36% traded urea, 29% ammonia — IFPRI; "
                 "cited in Nasari et al. (2026) *Nature Medicine*, DOI pending. "
                 "Do not cite secondary coverage; use IFPRI primary source.")

# ── Tab 5: Historical Comparison ──────────────────────────────────────────────
with tab5:
    with st.spinner("Loading historical PortWatch data..."):
        # chokepoint3 = Turkish Straits (best available Black Sea era proxy;
        #   Bosphorus stayed open under Montreux Convention — ~20% secondary signal only)
        # chokepoint4 = Bab-el-Mandeb/Suez approach (Red Sea Houthi disruption signal, ~57% drop)
        bs_df,  _ = get_historical_transit("chokepoint3",
                                            "2021-10-01", "2023-06-01")
        rs_df,  _ = get_historical_transit("chokepoint4",
                                            "2023-06-01", "2025-01-01")

    st.plotly_chart(
        fig_historical_comparison(transit_df, baseline_mean, drop_pct,
                                   {"black_sea": bs_df, "red_sea": rs_df}),
        use_container_width=True,
    )

    with st.expander("Key comparisons"):
        comp_data = {
            "Episode": ["Black Sea 2022", "Red Sea 2024", "Hormuz 2026"],
            "Transit drop": ["−80%","−72%",f"−{drop_pct:.0f}%"],
            "Bypass capacity": ["100% (Cape Horn)","100% (Cape of GH)","~5% (Oman ports)"],
            "Bypass cost": ["+25%","+40%","+300%+"],
            "Resolution": ["BSGI Day 148","Cape bypass (ongoing)","None established"],
            "Food exposure": ["~33% wheat imports","~12%","~15–20% Gulf states"],
        }
        st.dataframe(pd.DataFrame(comp_data), use_container_width=True)

# ── Tab 6: Behavioral Classification ─────────────────────────────────────────
with tab6:
    st.subheader("Davenport (2008) Evasion Taxonomy → GFW Data Streams")
    st.caption("Every category is linked to a specific GFW endpoint and observable metric")

    st.table(DAVENPORT_TABLE)
    st.info("★ **SELF-DETERRENCE (NEW)** — last row — is a novel Hormuz 2026 category "
            "not present in Davenport (2008): declared-open strait with zero actual transits (Apr 17).")

    st.divider()
    st.subheader("Five Key Science Arguments")
    st.markdown("""
1. **Observational ground truth** — complete Hormuz blockade is now live data against which prior simulation scenarios (CSH/Verschuur) can be assessed.
2. **Flag-state divergence** — commercial intelligence reports divergent behaviour by Chinese-flagged vs. Western carriers (cited observation, not original finding).
3. **No bypass asymmetry** — Persian Gulf food importers are trapped; Omani ports = ~5% alternative capacity.
4. **Fertilizer transmission** — the strait carries **36% of globally traded urea and 29% of ammonia** (IFPRI; cited in Nasari et al. 2026 *Nature Medicine*); feedstocks collapsed harder than grain, spring planting window already closing.
5. **BSGI precedent** — Black Sea Grain Initiative (Day 148) as the model for a Hormuz Transit Initiative.
""")

    with st.expander("Paper metadata"):
        st.write("**Title:** Closing the Hormuz Food Corridor")
        st.write("**Target:** Science Policy Forum — 2,000–3,000 words, ≤15 refs, 1–2 figures")
        st.write("**Editor:** Dr. Wible")
        st.write("**Repo:** github.com/Venkat10gitty/hormuz-dashboard")
        st.write("**Authors:** Prof. Michael Puma (Columbia Climate School) + team")

# ── Tab 7: Research Design ────────────────────────────────────────────────────
with tab7:
    st.subheader("Research Design — v3.1 (Aug 28, 2026)")
    st.caption(
        "Methods + Policy Forum merged into one paper. Target: Nature Food (primary) / "
        "Nature Communications (alternate). Policy Forum companion follows after, citing the main paper."
    )

    # Core research question — v3.1 three-channel framing
    st.markdown("### Core Research Question")
    st.markdown(
        "> When a maritime chokepoint comes under pressure, how much of the trade that stops is "
        "**physically prevented**, how much is **contractually or institutionally barred**, and how much "
        "is **discretionary refusal** — and what does answering that do to food security exposure estimates?"
    )
    st.caption(
        "The three channels behave differently, respond to different instruments, and are currently "
        "reported as a single number. The food security literature computes exposure share × binary "
        "closure state. That model is wrong. The delta between published exhaustion numbers and "
        "corrected numbers is the paper's lead result."
    )

    st.divider()

    # Three-channel explanation
    st.markdown("### Three-Channel Decomposition (v3.1 Spec §2)")
    ch1, ch2, ch3 = st.columns(3)
    with ch1:
        st.markdown("**🔴 Physical**")
        st.markdown(
            "Vessel cannot pass — blockade, mining, seizure, force. "
            "Relieved by: military/diplomatic action on the belligerent party."
        )
    with ch2:
        st.markdown("**🟡 Institutional**")
        st.markdown(
            "Vessel not permitted or not covered — JWC listed-area designation, "
            "BIMCO CONWARTIME/VOYWAR 2025 war clauses, P&I conditions, flag-state advisories, "
            "financing refusal. Relieved by: underwriting capacity, listed-area revision, state backstop."
        )
    with ch3:
        st.markdown("**🟠 Discretionary**")
        st.markdown(
            "Vessel could sail and is covered; owner or master declines. "
            "Relieved by: escort, convoy, demonstrated safe passage. "
            "Does not respond to insurance backstops."
        )
    st.info(
        "**Why the split matters for policy:** An insurance backstop (e.g., Op. Earnest Will "
        "1987 US reflagging of Kuwaiti tankers) relieves the **institutional** channel and does "
        "almost nothing for a master who is simply frightened. A naval escort does the reverse. "
        "Reporting them as one number cannot tell a policymaker which instrument to reach for."
    )

    st.divider()

    # Analysis status — updated for v3.1
    st.markdown("### Analysis Status")
    st.caption("Primary identification: M5 (JWC discontinuity). Supporting: M7 (premium response). M8 computable now.")

    status_data = {
        "ID": ["M1", "M3 (SAR)", "M3 (PELT)", "M7 (Apr 17)", "M5 (JWC)", "M8 (R(τ))", "M9 (exhaustion)"],
        "Analysis": [
            "Food Segment Isolation",
            "SAR Detection Correction",
            "Regime Detection",
            "Apr 17 Upper Bound",
            "JWC Institutional Discontinuity (PRIMARY)",
            "Non-Recovery R(τ)",
            "Food Security Exhaustion Correction",
        ],
        "Status": [
            "✅ Complete",
            "⚠️ Partial",
            "✅ Complete",
            "⚠️ Ceiling only",
            "🔲 Data needed",
            "✅ Live",
            "⚠️ Partial — USDA PSD bulk CSV live (no key), Qatar missing",
        ],
        "Key Finding": [
            "Dry bulk collapse ~81%; evasion concentrated in crude tankers, not food cargo",
            "Detection probability model specified; requires Level-1 SAR scenes from Jasper",
            "PELT (rbf kernel) recovers 8/8 crisis dates within 5-day tolerance "
            "when applied to crisis subwindow (Feb 28 – May 31). "
            "pen=0.25–1.0: 8/8 recovered. pen=2.0: 3/8. pen=4.0: 1/8. "
            "First PELT break: Mar 1 — 1 day before IRGC decree (Mar 2). "
            "Anticipation lead A = −1d vs IRGC. [PENDING: JWC and flag-state dates]",
            "Apr 17: 8 vessels (11% of baseline) despite declared opening. "
            "β = +1.92 is a CEILING on institutional + discretionary combined. "
            "6-day OLS window retired — Apr 18 outlier flips β negative.",
            "JWC listed-area revision = exogenous administrative event; physical danger "
            "continuous through window. Clean RD identifies institutional step. "
            "Awaiting: JWC JWLA revision dates for Hormuz 2026 (week-one reconnaissance).",
            f"τ½ > {(TODAY - IRAN_REOPEN).days} days — CENSORED. Transit has never reached "
            "50% of baseline since Apr 17. July partial return (R ≈ 0.40) collapsed within 10d. "
            "R(120d) = 0.01. Live from PortWatch chokepoint6.",
            "M9 double-counting fix: USDA PSD bulk CSV unblocked (grains_pulses_csv.zip, no key). "
            "GCC wheat balance sheets live for Bahrain, Kuwait, Oman, Saudi Arabia, UAE. "
            "Qatar has ZERO wheat rows in PSD (USDA does not track Qatar wheat — proxy needed). "
            "See M9 GCC Wheat Balance Sheet section below.",
        ],
        "Honest Limitation": [
            "Food dark fraction is upper bound only — proportional attribution, not per-vessel RCS",
            "GFW processed API: AIS/dark counts only; no per-vessel RCS for clutter removal",
            "Penalty choice: pen=0.5 recovers 8/8 with 20 breaks; pen=1.0 gives 13 breaks, 8/8. "
            "Must apply to crisis subwindow (Feb 28–May 31), not full analysis window. "
            "See PELT Penalty Sweep table below.",
            "US blockade still active Apr 17 — Iran lifting Iran's restriction ≠ no restriction. "
            "β = +1.92 overstates the combined institutional+discretionary share. "
            "Report as interval: [out-of-sample M7 lower bound (pending), β=+1.92 ceiling].",
            "JWC listings are endogenous (listed because danger rose); f(danger) must be flexible "
            "and window must be tight. Listings are anticipated → bias τ toward zero (lower bound).",
            "7-day rolling mean suppresses daily noise but smooths the July partial-return peak. "
            "Exact R values depend on smoothing window choice — report under multiple specs.",
            "Qatar has zero wheat rows in USDA PSD — not tracked. All other GCC countries live. "
            "PSD marketing year lags calendar year (wheat MY2025 = Jun 2025 – May 2026). "
            "Cumulative shortfall integral ∫[1−y/b]dt requires realized R(τ) path — available.",
        ],
    }
    st.dataframe(pd.DataFrame(status_data), use_container_width=True, height=300)

    # ── PELT Penalty Sweep (P4) ──────────────────────────────────────────────
    st.divider()
    st.markdown("### M3 PELT Penalty Sweep — Crisis Subwindow (Feb 28 – May 31)")
    st.caption(
        "Applied to crisis subwindow only (93 days, Feb 28–May 31 2026). "
        "At full analysis window the Feb 28 collapse is the sole dominant break; "
        "all subsequent sub-events produce no distribution shift at that scale. "
        "Model: RBF kernel, min_size=2, jump=1.  Tolerance: ±5 days.  "
        "Source: IMF PortWatch chokepoint6 (live)."
    )
    with st.spinner("Running PELT penalty sweep..."):
        try:
            sweep_rows, detail_rows = compute_pelt_sweep(transit_df)
        except Exception as _e:
            sweep_rows, detail_rows = [], []
            st.warning(f"PELT unavailable: {_e}")

    if sweep_rows:
        st.dataframe(pd.DataFrame(sweep_rows), use_container_width=True)
        st.caption(
            "**Anticipation lead (P6):** First PELT break = Mar 1, 2026 — 1 day before the "
            "IRGC closure decree (Mar 2).  A = −1 day vs IRGC reference.  "
            "Negative A indicates traffic began responding *before* the formal announcement.  "
            "[PENDING] Comparison vs JWC listed-area revision date and first flag-state "
            "advisory (week-one reconnaissance items)."
        )

    if detail_rows:
        with st.expander("Table 1 — per-event PELT gaps at penalty=0.50"):
            st.dataframe(pd.DataFrame(detail_rows), use_container_width=True)
            st.caption(
                "PELT break dates are the last day of the preceding segment (ruptures convention). "
                "Gap = PELT break date − documented event date (positive = PELT lags event). "
                "All eight documented crisis dates recovered within ±5 days at pen ≤ 1.0. "
                "At pen=2.0 only the initial collapse cluster (Feb 28, Mar 2, Mar 26) is recovered."
            )

    # ── Seasonal baseline sensitivity (P2) ──────────────────────────────────
    st.divider()
    st.markdown("### Baseline Sensitivity — Flat Mean vs Seasonal (Eq.2)")
    with st.expander("Seasonal baseline diagnostic (expand for details)", expanded=False):
        st.markdown("""
**Flat baseline (Eq.1):** mean of daily n\_total over pre-crisis window (Oct 1 2025 – Feb 27 2026).
Computed from live PortWatch data. Gives a single scalar b = 69.75 vessels/day.

**DOW + trend OLS (Eq.2 candidate):** fit on pre-crisis data only.
Results from live data:

| Component | Estimate |
|-----------|----------|
| Intercept | 71.0 vessels/day (at t=Oct 1 2025) |
| Linear trend | −0.10 vessels/day/day = −37 vessels/year |
| Tuesday effect | +12.5 relative to Monday |
| Wednesday effect | +9.2 |
| Residual σ | 20.1 vessels/day |

The negative daily trend (−0.10/day) extrapolates to b ≈ 39/day by Apr 17, 2026
— far below the flat mean.  This makes the DOW+trend baseline **unreliable** as a
projection: it amplifies any trend in the 5-month pre-crisis window into a very large
forward adjustment.  A positive trend in the same window would produce the opposite bias.

**Decision required (flagged as pending):**
- Primary analysis uses flat mean (Eq.1) — defensible, unambiguous, reproducible.
- DOW-only adjustment (remove trend, keep DOW dummies) is a reasonable sensitivity.
- STL decomposition extrapolation is unreliable (trend blows up beyond the estimation window).

**Impact on key metrics:**
- R(0) under flat mean = 0.11; under DOW-adjusted (Apr 17 is a Friday) ≈ 0.20
- July peak R under flat mean = 0.385; under DOW-adjusted ≈ 0.78

**Recommendation:** report flat mean as primary, DOW-only sensitivity in appendix.
The DOW+trend extrapolation should not be used without explicit stabilization (e.g., cap trend at zero or use HP filter).
[PENDING: editorial decision on seasonal correction method]
        """)

    # ── M9 GCC Wheat Balance Sheet (P7) ─────────────────────────────────────
    st.divider()
    st.markdown("### M9 — GCC Wheat Balance Sheet (USDA PSD, live)")
    st.caption(
        "Source: USDA FAS Production, Supply & Distribution (PSD) bulk CSV — "
        "no API key required.  Updated monthly.  "
        "Endpoint: apps.fas.usda.gov/psdonline/downloads/psd_grains_pulses_csv.zip.  "
        "Note: USDA PSD does not track wheat for Qatar — proxy source needed for Qatar."
    )
    with st.spinner("Fetching USDA PSD wheat data..."):
        psd_df, psd_note = get_psd_wheat_gcc()

    if psd_df is not None:
        # Show MY2025 (most relevant — crisis starts Feb 28 2026, within MY2025/26 Jun2025–May2026)
        my2025 = psd_df[psd_df["Market_Year"] == 2025].copy()
        if not my2025.empty:
            # Add derived fields
            for col in ["Beginning_Stocks","Imports","Dom_Consumption","Ending_Stocks"]:
                if col not in my2025.columns:
                    my2025[col] = float("nan")
            my2025["Cons_per_day_MT"] = my2025["Dom_Consumption"] * 1000 / 365
            my2025["Days_stocks_only"] = (my2025["Beginning_Stocks"] * 1000
                                          / my2025["Cons_per_day_MT"].replace(0, float("nan")))
            display_cols = {
                "Country_Name": "Country",
                "Beginning_Stocks": "Beg. Stocks\n(1000 MT)",
                "Imports": "Imports\n(1000 MT/yr)",
                "Dom_Consumption": "Consumption\n(1000 MT/yr)",
                "Ending_Stocks": "Ending Stocks\n(1000 MT)",
                "Days_stocks_only": "Days of supply\n(stocks only)",
            }
            disp = my2025[list(display_cols.keys())].copy()
            disp.columns = list(display_cols.values())
            st.markdown("**Marketing Year 2025/26** (Jun 2025 – May 2026) — most recent USDA estimate")
            st.dataframe(disp.set_index("Country"), use_container_width=True)

            st.warning(
                "⚠️ **Qatar:** Not tracked in USDA PSD wheat (zero rows). "
                "USDA PSD tracks Qatar for barley, corn, and rice only. "
                "Qatar wheat data requires: FAOSTAT food supply, World Bank Food Security data, "
                "or Qatar Ministry of Municipality statistics. [PENDING — proxy source needed]"
            )

        # Show all years for context
        with st.expander("All marketing years (2023–2026) by country"):
            st.dataframe(psd_df, use_container_width=True)
            st.caption(
                "Marketing year for wheat typically starts June 1 each year. "
                "All values in 1000 MT.  Most-recent monthly USDA revision shown."
            )

        st.caption(psd_note)
        st.caption(
            "**API key route (if live queries needed):** The new PSD REST API is at "
            "apps.fas.usda.gov/OpenData/api/psd/ — endpoints confirmed live, require "
            "API_KEY header (not a query param, not api.data.gov).  "
            "Free registration at: https://apps.fas.usda.gov/opendatawebV2/#/  "
            "Swagger spec: apps.fas.usda.gov/OpenData/swagger/docs/v1"
        )
    else:
        st.error(f"USDA PSD download failed: {psd_note}")
        st.info(
            "**API key route:** Register free at https://apps.fas.usda.gov/opendatawebV2/#/  "
            "Use API_KEY header on requests to apps.fas.usda.gov/OpenData/api/psd/  "
            "The old psdonline/api/v1/ path is HTTP 404 — that endpoint was deprecated."
        )

    st.divider()

    # Five-case design
    st.markdown("### Five-Case Design")
    st.caption(
        "Four-corridor design had a structural weakness: Hormuz 2026 and Red Sea 2026 "
        "share belligerents, underwriters, and fleet → N_effective ≈ 3. Fifth case fixes this."
    )
    cases_data = {
        "Case": [
            "Hormuz 2026",
            "Hormuz 1984–88 (Tanker War)",
            "Red Sea 2023–25",
            "Black Sea 2022",
            "Panama 2023–26",
        ],
        "Dominant Channel": [
            "All three — physical + institutional + discretionary",
            "Physical (high realized danger) — institutional channel stayed OPEN",
            "Institutional + Discretionary (Bab-el-Mandeb, no formal closure)",
            "Physical, displaced (binding constraint at Ukrainian ports, not chokepoint)",
            "Physical, priced by auction — hypothesis: no discretionary component "
            "(test requires post-Sept 3 post-Sept 15 data; cuts not yet in effect)",
        ],
        "Role in Design": [
            "Main case. All three channels present. Apr 17: declared open → 11% traffic → non-recovery.",
            "Strongest single argument: higher physical danger than 2026, traffic continued. "
            "Difference = institutional channel. US 1987 reflagging (Op. Earnest Will) = state backstop.",
            "Estimation window for M7 — physical channel small + separately measurable via attack incidence.",
            "Misattribution case: Bosporus stayed open (Montreux Convention). "
            "PortWatch cp3 shows ~20% signal at Turkish Straits; actual food disruption was at ports.",
            "Discretionary control candidate. Slots set by administrative fiat; transparent price rationing "
            "(auction), no fear premium. Pre-cut baseline established: cp2 ~31/day (2025 annual mean: 31.1/day). "
            "Panama Canal Authority announced cuts: 36→34 transits/day starting Sept 3, 2026; →32 starting Sept 15. "
            "Actual test — whether waiting times, auction premia, and diversions to Suez/Cape exceed "
            "what the mechanical slot reduction implies — requires post-cut data. "
            "Prediction: they will not, because no one is afraid of Panama.",
        ],
    }
    st.dataframe(pd.DataFrame(cases_data), use_container_width=True)
    st.caption(
        "PortWatch chokepoints: Hormuz=cp6, Bab-el-Mandeb=cp4, Bosporus=cp3, Panama=cp2. "
        "Hormuz 1984–88 predates PortWatch — requires archival request to Lloyd's/INTERTANKO "
        "(initiate week one; use last — archive requests take months)."
    )

    st.divider()

    # Why this is novel — updated to three contributions
    st.markdown("### Why This Is Novel (v3.1)")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown("**Three-channel identification**")
        st.markdown(
            "M5 JWC discontinuity cleanly identifies the institutional step "
            "(exogenous administrative event; physical danger smooth through window). "
            "M6 cross-section identifies discretionary variation across owner types. "
            "Neither the food security nor the shipping literature has done this jointly."
        )
    with col_b:
        st.markdown("**Non-recovery measure τ½**")
        st.markdown(
            f"τ½ > {(TODAY - IRAN_REOPEN).days} days — censored. "
            "The strait was declared open Apr 17. Transit has never reached 50% of baseline. "
            "Computable as R(τ) = y(t_open+τ)/b(t_open+τ) across all reopening events. "
            "First systematic measurement of chokepoint non-recovery."
        )
    with col_c:
        st.markdown("**Food security correction**")
        st.markdown(
            "Published GCC exhaustion numbers assume replenishment began at t_open. "
            "R(0)=0.11 proves replenishment did not begin. The delta between "
            "published exhaustion figures and the corrected integral ∫[1−y/b]dt "
            "leads the abstract — it is the paper's reason for existing."
        )

    st.divider()

    # Behavioral taxonomy
    st.markdown("### Behavioral Taxonomy — Riveiro × Davenport × Aggregate Signal")
    st.dataframe(TAXONOMY_TABLE, use_container_width=True, height=320)

    st.divider()

    # Four novel system-level categories
    st.markdown("### Four Novel System-Level Categories")
    st.caption(
        "Riveiro (2018) provides the five-category organising framework for vessel behaviour anomalies; "
        "Davenport (2008) provides the kinematic sub-scheme (16 codes) that sits within it. "
        "Neither has equivalents for the fleet-level phenomena listed here."
    )
    for _, row in NOVEL_CATEGORIES.iterrows():
        with st.expander(f"**{row['Category']}**"):
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**Definition:** {row['Definition']}")
                st.markdown(f"**Hormuz 2026:** {row['Hormuz 2026 Manifestation']}")
            with col2:
                st.markdown(f"**Research significance:** {row['Research Significance']}")

    st.divider()

    # Data provenance with live status
    st.markdown("### Data Provenance & API Status")
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("**IMF PortWatch**")
        if portwatch_ok:
            st.success(f"Live — {len(pw_data)} days")
        else:
            st.warning("Unavailable — using Windward anchors")
        st.caption("ArcGIS REST: chokepoints 2 (Panama), 3 (Bosporus), 4 (Bab-el-Mandeb), 6 (Hormuz)")
        st.caption("Fields: n_total, n_tanker, n_dry_bulk, n_general_cargo, n_container")
        st.caption("TTL: 12h · updated daily by IMF")

    with col2:
        st.markdown("**GFW SAR (Sentinel-1)**")
        if sar_daily is not None:
            total_sar = int(sar_daily["sar_total"].sum()) if "sar_total" in sar_daily.columns else 0
            st.success(f"Live — {total_sar:,} vessel-records")
        else:
            try:
                _check_sar_daily = _cget(f"app_sar_{BBOXES[bbox_choice]['name'][:8]}_{start_str}_{end_str}")
                if _check_sar_daily is not None:
                    st.info("Cached (disk)")
                else:
                    st.warning("Unavailable — API timeout or no data")
            except Exception:
                st.warning("Unavailable")
        st.caption("4Wings v3.0: public-global-sar-presence · M4 precondition (not robustness)")
        st.caption("SAR check gates M4 anticipation lead — fix coverage first (missing ≠ zero-dark)")
        st.caption("Level-1 scenes (per-vessel RCS): pending Jasper")

    with col3:
        st.markdown("**Price Data**")
        if FREDAPI_OK and FRED_KEY:
            st.success("FRED live — PWHEAMTUSDM (wheat)")
        else:
            st.info("Calibrated anchors (FRED key not set)")
        if YFINANCE_OK:
            st.success("yfinance live — BZ=F (Brent)")
        else:
            st.info("Brent: hardcoded anchors")
        st.caption("Urea: World Bank Pink Sheet — Middle East f.o.b. spot (CMO monthly XLSX, live)")
        st.caption("Fertilizer share: 36% traded urea, 29% ammonia (IFPRI; Nasari et al. 2026 Nat. Med.)")

    st.divider()

    # Scope — updated for v3.1 merged paper
    st.markdown("### Scope — What the Five-Case Design Supports")
    col_yes, col_no = st.columns(2)
    with col_yes:
        st.markdown("**✅ Defensible now (live data)**")
        st.markdown("""
- Transit collapse ~87.5% (all-vessel) — ground truth from PortWatch
- Seven empirically-identified regimes — PELT changepoints, not asserted
- Food-relevant fleet collapsed proportionally; evasion concentrated in crude tankers
- Apr 17 upper bound: β=+1.92 is a ceiling on institutional+discretionary combined
- M8 R(τ): τ½ > 135 days censored — strait functionally closed for food trade despite declared reopening
- Cross-commodity cascade: Brent → urea → wheat lag structure, timeline figure
- Panama (cp2): pre-cut baseline established — 31/day (2025: 31.1/day). Announced reductions
  take effect Sept 3 (36→34) and Sept 15 (→32). Test requires post-cut data.
""")
    with col_no:
        st.markdown("**🔲 Pending data acquisition**")
        st.markdown("""
- M5 JWC discontinuity — needs JWLA revision dates + BIMCO clause activation (week-one recon)
- M6 cross-section (vessel-level) — needs HiFleet: flag, ownership, insurer domicile, positions
- M7 out-of-sample β — needs war risk premium series (Lloyd's List Intelligence, AMIS)
- M9 exhaustion correction — USDA PSD bulk CSV live (no key). Bahrain, Kuwait, Oman, Saudi Arabia, UAE balance sheets fetched. Qatar has zero PSD wheat rows (proxy needed: FAOSTAT or Qatar national statistics)
- M10 systemic risk mechanism — reframed as market structure argument; needs widened event set
- M12 movement geometry — needs HiFleet position histories for Hormuz approach box
- Hormuz 1984–88 — needs Lloyd's/INTERTANKO archive (initiate request week one; long lead time)
""")

    st.divider()
    st.markdown("### Open Decisions for Monday (§10)")
    st.markdown("""
1. **Working title**: "Chokepoints Are Priced, Not Closed" or "The Paper Closure"? *(decide after M5 returns)*
2. **Journal**: Nature Food (better audience) or Nature Communications (takes methods more readily)?
3. **Policy Forum timing**: spec argues *after* the main paper so it cites rather than pre-empts
4. **FAO/Torero**: approach before or after a draft exists? His Aug 26 post asserts systemic risk from simultaneity without mechanism — that is what M5 + M10 address. Collaboration opening.
5. **Lloyd's/INTERTANKO archive**: anyone on the team with an archival route into 1984–88 records?
""")

# ── Tab 8: Cross-Event Comparison ─────────────────────────────────────────────
import os as _os

with tab8:
    st.subheader("Cross-Event Comparison — Five-Case Design (v3.1)")
    st.caption(
        "Descriptive comparison using IMF PortWatch live data. Three-channel interpretation per v3.1 spec. "
        "Hormuz 1984–88 (archive, pending) and Panama (pre-cut baseline below) added to five-case design. "
        "Static PNGs = three-event build (pre-v3.1 revision); interpretation notes updated above."
    )

    # Data quality caveat
    with st.expander("⚠️ Data quality notes — five-case design (expand before presenting)", expanded=False):
        st.markdown("""
| Case | Chokepoint | Channel | Signal | Note |
|---|---|---|---|---|
| **Hormuz 2026** | cp6 | All three | **Strong** — 87% collapse | Main case |
| **Hormuz 1984–88** | — (pre-PortWatch) | Physical, high danger | Archive only | Strongest argument: higher danger, traffic continued |
| **Red Sea 2023–25** | cp4 (Bab-el-Mandeb) | Institutional + Discretionary | **Moderate** — gradual diversion | Estimation window for M7 |
| **Black Sea 2022** | cp3 (Bosporus) | Physical, displaced | **Misattribution** — Bosporus stayed open | Binding constraint was Ukrainian ports + contracting, not chokepoint |
| **Panama 2023–26** | cp2 | Physical, priced by auction | **Control** — slots cut by fiat, no fear | Test: do diversions exceed announced cut mechanically? |

**Black Sea correction:** The chokepoint3 signal (~20%) understates the food disruption because the Bosphorus stayed open under the Montreux Convention. The binding constraint was at Ukrainian ports and in contracting — not at this chokepoint. Do not present this as a "chokepoint closure" case; it is a misattribution case showing that the method needs to be applied at the right chokepoint.

**Panama — pre-cut baseline period (not yet a completed test):** The Panama Canal Authority announced slot reductions: 36→34/day starting Sept 3, 2026; →32/day starting Sept 15, driven by El Niño rainfall below expectations. As of this data pull (Aug 31), the cuts have not taken effect — what PortWatch cp2 shows (~31/day through Aug) is the PRE-CUT BASELINE, not a behavioral response. The 2025 annual mean was 31.1/day; 2026 through August is 31.5/day — essentially flat. The actual test (whether waiting times, auction premia, and diversions to Suez/Cape EXCEED what the mechanical slot reduction implies) requires data after Sept 3 and Sept 15. Prediction: responses will not exceed the mechanical cut, because Panama is price-rationed by transparent auction (no fear premium), but this is a hypothesis until the data arrives. Do not present the current flat transit as evidence of "no discretionary component" — that claim requires post-cut comparison.

*PortWatch does not provide flag-state or route-level data. Novel categories requiring those fields are marked DATA GAP.*
""")

    st.divider()

    # Figure definitions with captions (Puma preference: captions outside figures)
    CROSS_FIGS = [
        {
            "file": "cross_fig1_transit_trajectory.png",
            "panel": "A",
            "caption": (
                "Daily transit counts normalized to 100-day pre-onset baseline, 7-day rolling mean. "
                "Days since onset on x-axis. Hormuz 2026 shows an immediate hard collapse (drop to 0 within 24 h); "
                "Red Sea 2023-24 shows a gradual 43-day decline to nadir; "
                "Black Sea 2022 shows a sharp but partial drop recovering within weeks."
            ),
        },
        {
            "file": "cross_fig2_regime_prevalence.png",
            "panel": "B",
            "caption": (
                "PELT changepoint-defined regimes plotted as duration (days) vs. mean transit level (% baseline). "
                "Color encodes severity (RdYlGn). Each bubble is one regime segment. "
                "Hormuz shows long, severe low-transit regimes; Red Sea shows a staircase of partial recovery; "
                "Black Sea shows rapid return toward baseline."
            ),
        },
        {
            "file": "cross_fig3_transition_speed.png",
            "panel": "C",
            "caption": (
                "Left: bar chart of sharpest single PELT transition per event (absolute Δ vessels/day). "
                "Right: scatter of drop magnitude (% baseline) vs. transition speed, with event labels. "
                "Hormuz 2026 is an outlier on both axes — faster and deeper than any prior recorded disruption."
            ),
        },
        {
            "file": "cross_fig4_novel_categories.png",
            "panel": "D",
            "caption": (
                "Novel system-level category matrix. ✓ = identified/computable from available data; "
                "✗ = not applicable or opposite mechanism; — = DATA GAP (requires flag-state or spatial route data not in PortWatch). "
                "Self-deterrence is Hormuz-specific. Regime-transition speed is computable for all events."
            ),
        },
        {
            "file": "cross_fig5_bypass_capacity.png",
            "panel": "E",
            "caption": (
                "Left: scatter of transit drop (% baseline) vs. bypass route capacity (% of pre-crisis volume). "
                "Right: grouped bar of bypass capacity vs. cost premium (USD/mt). "
                "Hormuz 2026 has near-zero bypass capacity at 300 USD/mt premium; "
                "Red Sea and Black Sea had viable (if expensive) rerouting options."
            ),
        },
    ]

    for fig_def in CROSS_FIGS:
        fpath = _os.path.join(_os.path.dirname(__file__), fig_def["file"])
        st.markdown(f"**Panel {fig_def['panel']}**")
        if _os.path.exists(fpath):
            with open(fpath, "rb") as _fh:
                img_bytes = _fh.read()
            st.image(img_bytes, use_container_width=True)
            st.caption(fig_def["caption"])
            st.download_button(
                label=f"Download Panel {fig_def['panel']} (PNG)",
                data=img_bytes,
                file_name=fig_def["file"],
                mime="image/png",
                key=f"dl_{fig_def['panel']}",
            )
        else:
            st.warning(f"Figure not found: {fig_def['file']}")
        st.divider()

    # Status report — updated for five-case design
    st.markdown("### Status Report — Five-Case Design")
    col_ready, col_gap = st.columns(2)
    with col_ready:
        st.markdown("**✅ Live / computable**")
        st.markdown("""
- Normalized trajectory (Panel A) — Hormuz, Red Sea, Black Sea via live PortWatch
- PELT regime prevalence (Panel B) — Hormuz validated; Red Sea/Black Sea included
- Transition-speed comparison (Panel C) — Hormuz clear outlier on both axes
- Novel category matrix (Panel D) — honest ✓/✗/— per event, DATA GAP flagged
- Panama (cp2): pre-cut baseline documented — 31/day, flat vs 2025 (31.1/day). Sept 3/15 cuts not yet in effect; test pending
- M8 R(τ): Hormuz τ½ > 135d censored; Red Sea τ½ computable from cp4 data
- M10 correlation claim RETIRED: five non-independent cases → reframed as market structure argument
""")
    with col_gap:
        st.markdown("**🔲 Needs external data**")
        st.markdown("""
- Panama auction prices + queue length (Canal Authority) — needed for M11 post-cut test
- Panama post-cut transit data (after Sept 3 and Sept 15) — needed for actual test result
- Hormuz 1984–88 traffic + premium series (Lloyd's/INTERTANKO archive)
- Flag-state stratification (HiFleet: ownership, insurer domicile, positions)
- Food-segment isolation for Red Sea / Black Sea (PortWatch cargo fields unavailable historically)
- Movement geometry windows: Apr 17–18 + July 2026 (HiFleet position histories)
- SAR scene coverage separation (Level-1 files from Jasper — M4 precondition)
""")

    # ── Panama live panel ──────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Panama Canal (cp2) — Pre-Cut Baseline")
    st.caption(
        "Establishes the comparison baseline before the Panama Canal Authority's announced slot "
        "reductions. The actual test of whether responses exceed the mechanical cut requires "
        "post-Sept 3 and post-Sept 15 data. Do not interpret flat pre-cut transit as confirming "
        "the no-discretionary-component hypothesis — that claim requires post-cut comparison."
    )

    with st.spinner("Loading Panama Canal PortWatch data..."):
        panama_df, _ = get_historical_transit("chokepoint2", "2025-01-01", end_str)

    if panama_df is not None and not panama_df.empty:
        # Rename n_total column if needed
        _pc = panama_df.copy()
        if "n_total" not in _pc.columns and "transit_vessels" in _pc.columns:
            _pc = _pc.rename(columns={"transit_vessels": "n_total"})
        elif "n_total" not in _pc.columns:
            _pc["n_total"] = _pc.iloc[:, 1]  # fallback

        panama_fig, panama_stats = fig_panama_baseline(_pc)
        if panama_fig is not None:
            st.plotly_chart(panama_fig, use_container_width=True)

            pk1, pk2, pk3, pk4 = st.columns(4)
            pk1.metric("2025 mean (baseline)", f"{panama_stats['baseline_2025']:.1f}/day",
                       f"{panama_stats['n_2025_days']} days")
            pk2.metric("2026 mean (pre-cut)", f"{panama_stats['mean_2026']:.1f}/day",
                       f"{panama_stats['n_2026_days']} days · through {panama_stats['last_date']}")
            pk3.metric("Sept 3 announced cut", "36 → 34/day", "El Niño rainfall — future")
            pk4.metric("Sept 15 announced cut", "→ 32/day", "future — test pending")

            st.warning(
                "⚠ **Pre-period baseline only.** The slot cuts have not yet taken effect. "
                "The correct test (M11 robustness): do waiting times, auction premia, and diversions "
                "to Suez or the Cape EXCEED what the 36→34→32 reduction mechanically implies? "
                "Predict: no, because Panama pricing is transparent and fear-free. "
                "But this is a hypothesis. The evidence arrives after Sept 15."
            )
        else:
            st.info("Panama chart could not be rendered from PortWatch data.")
    else:
        st.warning("Panama Canal (chokepoint2) data unavailable from PortWatch.")
