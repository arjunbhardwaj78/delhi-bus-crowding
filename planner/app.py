"""
Crowding-aware Delhi bus journey planner (Streamlit).

Run from the project root:
    streamlit run planner/app.py

Data modes (picked automatically):
  LIVE     data/raw/gtfs/ + outputs/crowding.csv          -> scheduled times + map
  TICKETS  outputs/crowding.csv + outputs/stop_order.csv  -> no GTFS yet: stop order from tickets,
                                                             rough ride times, no map (crowding strip instead)
  DEMO     nothing real found                             -> fake data, clearly flagged
"""
import html
import sys
from datetime import time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402

import config  # noqa: E402
import journey  # noqa: E402
import make_fake_data  # noqa: E402
import mapview  # noqa: E402
from crowding import Crowding  # noqa: E402
from gtfs_store import fmt_clock, load_store, load_ticket_store  # noqa: E402

st.set_page_config(page_title="Delhi bus planner: crowding-aware", page_icon="🚌", layout="wide")


# ------------------------------------------------------------------ data

def _real_gtfs_ok():
    return all((config.REAL_GTFS_DIR / f).exists() for f in ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt"))


@st.cache_resource(show_spinner="Loading data... (the first load of the big crowding file takes a few seconds)")
def load_everything():
    real_gtfs = _real_gtfs_ok()
    real_crowd = config.REAL_CROWDING.exists()
    has_order = config.REAL_STOP_ORDER.exists()
    rh = config.REAL_ROUTE_HOUR if config.REAL_ROUTE_HOUR.exists() else None
    so = config.REAL_STOP_ORDER if has_order else None

    if real_gtfs and real_crowd:
        return {"store": load_store(config.REAL_GTFS_DIR),
                "crowd": Crowding.from_csv(config.REAL_CROWDING, rh, so), "mode": "LIVE", "orphan": False}
    if real_crowd and has_order:
        return {"store": load_ticket_store(config.REAL_STOP_ORDER),
                "crowd": Crowding.from_csv(config.REAL_CROWDING, rh), "mode": "TICKETS", "orphan": False}
    if real_gtfs:
        return {"store": load_store(config.REAL_GTFS_DIR), "crowd": Crowding(None), "mode": "NO_CROWDING", "orphan": False}
    make_fake_data.ensure_sample()
    return {"store": load_store(config.SAMPLE_GTFS_DIR), "crowd": Crowding.from_csv(config.SAMPLE_CROWDING),
            "mode": "DEMO", "orphan": real_crowd}


# ------------------------------------------------------------------ small UI helpers

def esc(x):
    return html.escape(str(x))


def badge(level, low_data=False):
    colour = config.LEVEL_COLOUR.get(level, config.LEVEL_COLOUR["unknown"])
    label = config.LEVEL_LABEL.get(level, level)
    if low_data and level != "unknown":
        return (f"<span style='background:#eeeeee;color:#555;border:2px solid {colour};padding:2px 12px;"
                f"border-radius:14px;font-weight:600;font-size:0.95rem;white-space:nowrap'>"
                f"{esc(label)} · low data, rough guess</span>")
    return (f"<span style='background:{colour};color:#fff;padding:3px 12px;border-radius:14px;"
            f"font-weight:600;font-size:0.95rem;white-space:nowrap'>{esc(label)}</span>")


def conf_note(s):
    if s.level == "unknown":
        return "<span style='color:#8a8f98'>Not enough ticket data for this route at this time.</span>"
    extra = " Part of this uses a route-wide average." if s.routehour_share > 0.05 else ""
    if s.confidence == "low":
        return f"<span style='color:#d6362f;font-weight:600'>⚠ Estimate unreliable: few tickets.{extra}</span>"
    if s.confidence == "medium":
        return f"<span style='color:#a06a00'>Confidence: medium.{extra}</span>"
    return f"<span style='color:#2e9e4f'>Confidence: high.{extra}</span>"


def minutes(sec):
    return f"{max(round(sec / 60), 0):d} min"


def render_option(store, opt, n):
    s = opt.summary
    with st.container(border=True):
        routes = " → change → ".join(f"🚌 **{store.patterns[l.pid].label}**" for l in opt.legs)
        if store.timetable:
            timing = (f"Leave **{fmt_clock(opt.legs[0].board_t)}** · arrive **{fmt_clock(opt.arrive_t)}** · "
                      f"**{minutes(opt.total_s)}** total (wait {minutes(opt.wait_s)})")
        else:
            timing = f"Ride about **{minutes(opt.total_s)}** (rough estimate: no timetable yet, waiting time not included)"
        st.markdown(f"**Option {n}** · {routes}  \n{timing}")
        low = s.confidence == "low" and s.level != "unknown"
        extra = ""
        if s.level != "unknown" or s.worst_level != "unknown":
            if s.worst_level in config.LEVEL_SCORE:
                extra = (f"<br><span style='font-size:0.9rem'>Busiest stretch: <b>{esc(s.worst_from)} → {esc(s.worst_to)}</b> "
                         f"({esc(config.LEVEL_LABEL.get(s.worst_level, s.worst_level))})</span>")
        st.markdown(badge(s.level, low) + "&nbsp;&nbsp;" + conf_note(s) + extra
                    + mapview.share_bar_html(s), unsafe_allow_html=True)
        for li, leg in enumerate(opt.legs):
            p = store.patterns[leg.pid]
            a = store.stops[p.stop_ids[leg.i]]["name"]
            b = store.stops[p.stop_ids[leg.j]]["name"]
            ls = leg.summary
            if store.timetable:
                body = (f"board {esc(a)} {fmt_clock(leg.board_t)} → get off {esc(b)} {fmt_clock(leg.alight_t)} · "
                        f"{leg.j - leg.i} stops · {minutes(leg.alight_t - leg.board_t)}")
            else:
                body = (f"board {esc(a)} → get off {esc(b)} · {leg.j - leg.i} stops · "
                        f"about {minutes(leg.alight_t - leg.board_t)}")
            st.markdown(f"{config.LEVEL_ICON.get(ls.level, '⚪')} **{p.label}**: {body}")
            if li < len(opt.legs) - 1:
                nxt = opt.legs[li + 1]
                if store.timetable:
                    walk = f"walk {round(opt.walk_m)} m, " if opt.walk_m > 1 else ""
                    st.caption(f"Change here: {walk}wait {minutes(nxt.board_t - leg.alight_t)} for the next bus.")
                else:
                    st.caption("Change here (assumed 5 min wait for the next bus).")


def legend():
    items = [("seats", "Seats likely"), ("standing", "Standing room likely"), ("crowded", "Likely crowded"),
             ("unknown", "No data")]
    chips = "".join(
        f"<span style='display:inline-block;width:26px;height:8px;background:{config.LEVEL_COLOUR[k]};"
        f"border-radius:4px;margin:0 6px 1px 12px'></span>{t}" for k, t in items)
    st.markdown(chips + "<span style='margin-left:12px;color:#888'>(faded or dashed = low data, rough guess)</span>",
                unsafe_allow_html=True)


# ------------------------------------------------------------------ page

data = load_everything()
store, crowd, mode = data["store"], data["crowd"], data["mode"]

st.title("🚌 Delhi bus planner: fast or comfortable?")
st.caption("Journey options with travel time **and** how crowded the bus is likely to be.")

if mode == "DEMO":
    st.warning("**DEMO MODE**: fake timetable and fake crowding numbers. Real data is used automatically once "
               "`outputs/crowding.csv` and `outputs/stop_order.csv` (or `data/raw/gtfs/`) exist.")
    if data["orphan"]:
        st.warning("`outputs/crowding.csv` exists but `outputs/stop_order.csv` and `data/raw/gtfs/` do not, "
                   "so the demo data is used.")
elif mode == "TICKETS":
    st.info("**Ticket-only mode (no GTFS yet).** Stop order is rebuilt from ticket records. Ride times are rough "
            "estimates (about 2.5 min per stop), waiting time is not included, and there is no map: the coloured "
            "strip shows crowding stop by stop. Real timetables and the map switch on automatically when GTFS "
            "is added to `data/raw/gtfs/`.")
elif mode == "NO_CROWDING":
    st.warning("`outputs/crowding.csv` not found: showing scheduled times only. Run the pipeline to create it.")

with st.sidebar:
    st.header("Trip settings")
    day_type = st.radio("Day type", ["weekday", "weekend"], horizontal=True)
    dep = st.time_input("Departure time" + (" (picks the crowding hour)" if not store.timetable else ""),
                        value=dtime(18, 0), step=300)
    allow_transfer = st.checkbox("Include trips with 1 change", value=True)
    st.divider()
    st.caption(f"Data: **{mode}**")
    st.caption(f"{len(store.stop_names):,} stops · {len(store.routes):,} routes"
               + (f" · {store.n_trips:,} scheduled trips" if store.timetable else ""))
    st.caption(f"{crowd.n_rows:,} crowding rows" + (f" · {crowd.n_route_hour:,} route-hour fallbacks" if crowd.n_route_hour else ""))
    if st.button("Reload data files"):
        st.cache_resource.clear()
        st.rerun()

tab_plan, tab_ops = st.tabs(["🧭 Journey planner", "📊 Operator view"])

with tab_plan:
    names = store.stop_names
    d_o = names.index("Anand Vihar ISBT") if (mode == "DEMO" and "Anand Vihar ISBT" in names) else None
    d_d = names.index("Connaught Place") if (mode == "DEMO" and "Connaught Place" in names) else None
    c1, c2 = st.columns(2)
    origin = c1.selectbox("From", names, index=d_o, placeholder="Type a stop name...")
    dest = c2.selectbox("To", names, index=d_d, placeholder="Type a stop name...")

    pick = st.radio("Show me the", ["Fastest trips first", "Most comfortable trips first"], horizontal=True)
    rank_mode = "comfortable" if pick.startswith("Most") else "fastest"

    if not origin or not dest:
        st.info("Choose a starting stop and a destination.")
    elif origin == dest:
        st.info("Pick two different stops.")
    else:
        depart_t = dep.hour * 3600 + dep.minute * 60
        opts = journey.plan(store, crowd, origin, dest, depart_t, day_type, allow_transfer, rank_mode)
        if not opts:
            st.error("No bus found between these stops" + (" within 90 minutes of that time." if store.timetable else
                     ". Try other stops, or allow a change."))
        else:
            for n, o in enumerate(opts, 1):
                render_option(store, o, n)
            st.markdown("#### Where it gets crowded" if not store.has_coords else "#### Map")
            which = st.radio("Show details for", [f"Option {i}" for i in range(1, len(opts) + 1)], horizontal=True)
            chosen = opts[int(which.split()[1]) - 1]
            legend()
            if store.has_coords:
                m = mapview.build_map(store, chosen)
                if m is not None:
                    components.html(mapview.map_html(m), height=520)
            else:
                st.markdown(mapview.strip_html(store, chosen), unsafe_allow_html=True)
                st.caption("Each block is one stretch between two stops. Hover a block for details.")
            st.caption("Crowding levels are **relative** (compared with the busiest well-observed route-hours), not "
                       "passenger counts. They are the typical level for this day type and hour, estimated from ticket "
                       "data; pass holders are not in ticket data.")

    with st.expander("How is crowding estimated?"):
        st.markdown(
            "- Ticket records (where people boarded and where they were going) are turned into an estimated flow of "
            "people through each stretch between stops, by route, direction, day type and hour.\n"
            "- That is scaled for bus frequency and an assumed pass-holder uplift, then compared with the busiest "
            "well-observed route-hours. The result is a level: **seats likely**, **standing room likely** or "
            "**likely crowded**.\n"
            "- Each stretch carries a confidence based on how many tickets it rests on. About half of all stretches "
            "have little data, and the app says so: those show as a greyed, rough guess.\n"
            "- If a stretch has no usable data, the route-wide average for that hour is used (always marked low "
            "confidence). If there is none either, it shows as unknown. Unknown is never shown as empty.\n"
            "- A journey's overall level averages the stretches that have data. The **busiest stretch** and the share "
            "of the ride at each level are shown separately so a short crush is not hidden."
        )

with tab_ops:
    st.markdown("**For operators and planners:** which route-hours have the highest relative load in this data?")
    if crowd.n_rows == 0:
        st.info("No crowding data loaded.")
    else:
        dt = st.radio("Day type", ["weekday", "weekend"], horizontal=True, key="op_day")
        st.dataframe(crowd.overloaded_table(dt), hide_index=True)
        st.caption("Only route-hours with at least 200 tickets and 3 stretches. share_crowded = share of stretches "
                   "that are 'likely crowded'. max_load is the highest relative load on any stretch. "
                   "share_low_conf = share of stretches with few tickets.")
        st.markdown("**Crowding through the day for one route**")
        pairs = crowd.routes()
        choice = st.selectbox("Route", [f"{b} {d}" for b, d in pairs], key="op_route")
        base, direction = choice.rsplit(" ", 1)
        prof = crowd.hour_profile(base, direction)
        if not prof.empty:
            st.line_chart(prof)
            st.caption("Average over stretches with data. 0 = seats likely, 1 = standing room, 2 = crowded.")
