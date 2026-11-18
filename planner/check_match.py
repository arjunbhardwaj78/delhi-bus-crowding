"""
Compare the real GTFS with the pipeline's files and list names that do not match.
Run this after GTFS is placed in data/raw/gtfs/:

    python planner/check_match.py

Prints a report and saves it to planner/.cache/match_report.txt (send it to Claude A).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

import config  # noqa: E402
from gtfs_store import norm_name, split_route  # noqa: E402


def main():
    g = config.REAL_GTFS_DIR
    if not (g / "routes.txt").exists():
        print("No GTFS found in", g)
        return
    out = []

    def say(x=""):
        print(x)
        out.append(str(x))

    routes = pd.read_csv(g / "routes.txt", dtype=str)
    col = "route_short_name" if "route_short_name" in routes else "route_long_name"
    gtfs_routes = {split_route(x)[0] for x in routes[col].dropna()}
    crowd = pd.read_csv(config.REAL_CROWDING, usecols=["route"], dtype=str)["route"].dropna().unique()
    crowd_routes = {split_route(x)[0] for x in crowd}
    both = gtfs_routes & crowd_routes
    say(f"ROUTES: GTFS {len(gtfs_routes)} | crowding.csv {len(crowd_routes)} | matched {len(both)} "
        f"({len(both) / max(len(crowd_routes), 1):.0%} of crowding.csv routes)")
    say("  in crowding.csv but not in GTFS (first 30): " + ", ".join(sorted(crowd_routes - gtfs_routes)[:30]))
    say("  in GTFS but not in crowding.csv (first 30): " + ", ".join(sorted(gtfs_routes - crowd_routes)[:30]))

    if config.REAL_STOP_ORDER.exists():
        stops = pd.read_csv(g / "stops.txt", dtype=str)
        gtfs_names = {norm_name(x) for x in stops["stop_name"].dropna()}
        so = pd.read_csv(config.REAL_STOP_ORDER, dtype=str)
        so_names = {norm_name(x) for x in so["stop_name"].dropna()}
        both_n = gtfs_names & so_names
        say("")
        say(f"STOP NAMES: GTFS {len(gtfs_names)} | stop_order.csv {len(so_names)} | matched {len(both_n)} "
            f"({len(both_n) / max(len(so_names), 1):.0%} of stop_order names)")
        say("  in stop_order.csv but not in GTFS (first 30): " + "; ".join(sorted(so_names - gtfs_names)[:30]))
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (config.CACHE_DIR / "match_report.txt").write_text("\n".join(out) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
