#!/usr/bin/env python
"""
Check data quality behind a demo journey, or suggest good demo journeys.
Run from repo root (needs outputs/crowding.csv and outputs/stop_order.csv):

  python pipeline/check_journey.py --from "Shivaji Park" --to "Moti Nagar Industrial Area"
  python pipeline/check_journey.py --suggest
Options: --day weekday|weekend   --hours 9 18   --routes 991 944   (all optional)
Stop names are matched case-insensitively by 'contains'. Output also saved to outputs/journey_check.txt
"""
import argparse, os, sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "outputs")
LV = {"seats": 0, "standing": 1, "crowded": 2}
LOGL = []


def P(s=""):
    print(s)
    LOGL.append(str(s))


def load():
    c = pd.read_csv(os.path.join(OUT, "crowding.csv"), dtype={"route": str})
    s = pd.read_csv(os.path.join(OUT, "stop_order.csv"), dtype={"route": str})
    return c, s


def eval_span(cw, route, direction, day, hour, a, b):
    """stretches a..b-1 of one route-direction; returns dict."""
    n = b - a
    sub = cw[(cw.route == route) & (cw.direction == direction) & (cw.day_type == day) & (cw.hour == hour)
             & (cw.stop_seq_from >= a) & (cw.stop_seq_from < b)]
    present = len(sub)
    conf = sub.confidence.value_counts().to_dict()
    lv = sub.level.value_counts().to_dict()
    worst = sub.level.map(LV).max() if present else None
    worst = {0: "seats", 1: "standing", 2: "crowded"}[int(worst)] if worst is not None else "unknown"
    return dict(stretches=n, missing=n - present, high=conf.get("high", 0), medium=conf.get("medium", 0),
                low=conf.get("low", 0), seats=lv.get("seats", 0), standing=lv.get("standing", 0),
                crowded=lv.get("crowded", 0), worst=worst, cov_good=(conf.get("high", 0) + conf.get("medium", 0)) / n)


def journey(c, s, frm, to, day, hours, routes):
    frm, to = frm.lower(), to.lower()
    found = False
    for (r, d), g in s.groupby(["route", "direction"]):
        if routes and r not in routes:
            continue
        nm = g.stop_name.str.lower()
        A, B = g[nm.str.contains(frm, regex=False)], g[nm.str.contains(to, regex=False)]
        if A.empty or B.empty:
            continue
        a, b = int(A.stop_seq.iloc[0]), int(B.stop_seq.iloc[-1])
        if b <= a:
            continue
        found = True
        P(f"\nRoute {r} {d}: {A.stop_name.iloc[0]} (seq {a}) -> {B.stop_name.iloc[-1]} (seq {b}), {b - a} stretches, order_source={g.order_source.iloc[0]}")
        for h in hours:
            e = eval_span(c, r, d, day, h, a, b)
            flag = "GOOD" if e["cov_good"] >= 0.8 else ("OK" if e["cov_good"] >= 0.5 else "WEAK")
            P(f"  {day} {h:02d}:00  [{flag}] high={e['high']} medium={e['medium']} low={e['low']} missing={e['missing']} | "
              f"seats={e['seats']} standing={e['standing']} crowded={e['crowded']} | worst={e['worst']}")
    if not found:
        P("No route found that has both stops in that order. Check spelling; try a shorter part of the name.")


def suggest(c, s, day, hours, span=8, top=12):
    hours = hours or [9, 14, 18]
    c = c[c.day_type == day]
    tot = c.groupby(["route", "direction"]).n_tickets.sum().sort_values(ascending=False).head(200)
    rows = []
    for (r, d), _ in tot.items():
        g = s[(s.route == r) & (s.direction == d)].sort_values("stop_seq")
        nst = len(g)
        if nst < span + 2:
            continue
        sub = c[(c.route == r) & (c.direction == d)]
        for a in range(1, nst - span + 1, 3):
            b = a + span
            ev = [eval_span(sub, r, d, day, h, a, b) for h in hours]
            if min(e["cov_good"] for e in ev) < 0.9:
                continue
            worsts = [e["worst"] for e in ev]
            rows.append(dict(route=r, direction=d, frm=g.stop_name.iloc[a - 1], to=g.stop_name.iloc[b - 1],
                             worst_by_hour=" / ".join(f"{h}:{w}" for h, w in zip(hours, worsts)),
                             variety=len(set(worsts)), tickets=int(tot[(r, d)])))
    if not rows:
        P("No journey met the quality bar (>=90% of stretches medium/high at all hours).")
        return
    df = pd.DataFrame(rows).sort_values(["variety", "tickets"], ascending=False).drop_duplicates(["route", "direction"]).head(top)
    P(f"Suggested demo journeys ({day}, {span} stretches, >=90% medium/high confidence at hours {hours}); variety = number of different 'worst' levels across hours:")
    P(df[["route", "direction", "frm", "to", "worst_by_hour", "tickets"]].to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm")
    ap.add_argument("--to")
    ap.add_argument("--day", default="weekday")
    ap.add_argument("--hours", type=int, nargs="+")
    ap.add_argument("--routes", nargs="+")
    ap.add_argument("--suggest", action="store_true")
    a = ap.parse_args()
    c, s = load()
    if a.suggest:
        suggest(c, s, a.day, a.hours)
    elif a.frm and a.to:
        journey(c, s, a.frm, a.to, a.day, a.hours or [9, 18], a.routes)
    else:
        sys.exit("Use --from X --to Y   or   --suggest")
    with open(os.path.join(OUT, "journey_check.txt"), "a", encoding="utf-8") as f:
        f.write("\n".join(LOGL) + "\n\n")


if __name__ == "__main__":
    main()
