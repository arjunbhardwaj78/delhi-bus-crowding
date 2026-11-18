"""Folium map of one journey option. Each hop between stops is coloured by crowding."""
import html

import config
from gtfs_store import fmt_clock


def _esc(x):
    return html.escape(str(x))


def share_bar_html(summary):
    """Stacked bar: share of the ride at each crowding level."""
    parts = []
    for k in ("seats", "standing", "crowded", "unknown"):
        v = summary.shares.get(k, 0.0)
        if v >= 0.005:
            parts.append(
                f"<div title='{_esc(config.LEVEL_LABEL[k])}: {v:.0%} of the ride' style='width:{v * 100:.1f}%;"
                f"background:{config.LEVEL_COLOUR[k]};height:12px'></div>")
    txt = " · ".join(f"{config.LEVEL_LABEL[k].replace(' (no data)', '')} {summary.shares[k]:.0%}"
                     for k in ("seats", "standing", "crowded", "unknown") if summary.shares.get(k, 0) >= 0.005)
    return (f"<div style='display:flex;border-radius:6px;overflow:hidden;margin:6px 0 2px 0'>{''.join(parts)}</div>"
            f"<div style='font-size:0.8rem;color:#777'>Share of the ride: {_esc(txt)}</div>")


def strip_html(store, opt):
    """No-map view: one coloured block per hop for each leg (used when there are no stop coordinates)."""
    out = []
    for leg in opt.legs:
        p = store.patterns[leg.pid]
        a = store.stops[p.stop_ids[leg.i]]["name"]
        b = store.stops[p.stop_ids[leg.j]]["name"]
        blocks = []
        for seg in leg.segments:
            n0 = store.stops[p.stop_ids[seg.i]]["name"]
            n1 = store.stops[p.stop_ids[seg.j]]["name"]
            low = seg.confidence == "low" or seg.level == "unknown"
            load = "n/a" if seg.load_index != seg.load_index else f"{seg.load_index:.2f}"
            if seg.level == "unknown":
                tip = f"{n0} → {n1}: not enough data ({seg.n_tickets} tickets)"
            else:
                tip = (f"{n0} → {n1}: {config.LEVEL_LABEL.get(seg.level, seg.level)} (relative load {load}); "
                       f"confidence {seg.confidence}, {seg.n_tickets} tickets"
                       + (" - route-wide average" if seg.source == "route-hour" else ""))
            colour = config.LEVEL_COLOUR.get(seg.level, config.LEVEL_COLOUR["unknown"])
            style = f"flex:1;min-width:6px;height:22px;background:{colour};margin-right:1px;"
            blocks.append(f"<div title='{_esc(tip)}' style='{style}'></div>")
        out.append(
            f"<div style='margin:10px 0 2px 0'><b>🚌 {_esc(p.label)}</b> · {leg.j - leg.i} stops</div>"
            f"<div style='display:flex'>{''.join(blocks)}</div>"
            f"<div style='display:flex;justify-content:space-between;font-size:0.85rem;color:#666'>"
            f"<span>{_esc(a)}</span><span>{_esc(b)}</span></div>")
    return "".join(out)


def build_map(store, opt):
    import folium  # imported here so the app also runs in ticket-only mode without a map

    pts = []
    for leg in opt.legs:
        p = store.patterns[leg.pid]
        for t in range(leg.i, leg.j + 1):
            s = store.stops[p.stop_ids[t]]
            pts.append((s["lat"], s["lon"]))
    if not pts:
        return None
    m = folium.Map(location=pts[0], zoom_start=12, tiles="OpenStreetMap", control_scale=True)

    for li, leg in enumerate(opt.legs):
        p = store.patterns[leg.pid]
        for seg in leg.segments:
            a = store.stops[p.stop_ids[seg.i]]
            b = store.stops[p.stop_ids[seg.j]]
            low = seg.confidence == "low" or seg.level == "unknown"
            load = "n/a" if seg.load_index != seg.load_index else f"{seg.load_index:.2f}"
            if seg.level == "unknown":
                tip = f"<b>Route {p.label}</b><br>{a['name']} → {b['name']}<br>Not enough data ({seg.n_tickets} tickets)"
            else:
                tip = (f"<b>Route {p.label}</b><br>{a['name']} → {b['name']}<br>"
                       f"{config.LEVEL_LABEL.get(seg.level, seg.level)} (relative load {load})<br>"
                       f"confidence: {seg.confidence}, tickets: {seg.n_tickets}")
            folium.PolyLine(
                [(a["lat"], a["lon"]), (b["lat"], b["lon"])],
                color=config.LEVEL_COLOUR.get(seg.level, config.LEVEL_COLOUR["unknown"]),
                weight=7, opacity=0.55 if low else 0.95,
                dash_array="8 8" if low else None,
                tooltip=folium.Tooltip(tip),
            ).add_to(m)

        s0 = store.stops[p.stop_ids[leg.i]]
        s1 = store.stops[p.stop_ids[leg.j]]
        folium.CircleMarker(
            (s0["lat"], s0["lon"]), radius=7, color="#111", fill=True, fill_color="#fff", fill_opacity=1,
            tooltip=f"{'Board' if li == 0 else 'Change here'}: {s0['name']} at {fmt_clock(leg.board_t)} (route {p.label})",
        ).add_to(m)
        last = li == len(opt.legs) - 1
        folium.CircleMarker(
            (s1["lat"], s1["lon"]), radius=7, color="#111", fill=True,
            fill_color="#111" if last else "#fff", fill_opacity=1,
            tooltip=f"{'Arrive' if last else 'Get off'}: {s1['name']} at {fmt_clock(leg.alight_t)}",
        ).add_to(m)

    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(30, 30))
    return m


def map_html(m):
    return m.get_root().render()
