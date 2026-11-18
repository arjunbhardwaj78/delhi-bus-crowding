"""
Generate a tiny FAKE Delhi-like GTFS feed and a FAKE crowding.csv, so the planner can be
demoed and tested without the real data. Everything here is invented. It is only used when
the real files are missing.

    python planner/make_fake_data.py
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

STOPS = {
    "S01": ("Anand Vihar ISBT", 28.6469, 77.3159),
    "S02": ("Karkardooma", 28.6480, 77.3050),
    "S03": ("Laxmi Nagar", 28.6304, 77.2773),
    "S04": ("ITO", 28.6289, 77.2406),
    "S06": ("Connaught Place", 28.6315, 77.2167),
    "S07": ("Kashmere Gate ISBT", 28.6675, 77.2284),
    "S08": ("Red Fort", 28.6562, 77.2410),
    "S09": ("Nehru Place", 28.5491, 77.2519),
    "S10": ("Lajpat Nagar", 28.5677, 77.2433),
    "S11": ("AIIMS", 28.5672, 77.2100),
    "S13": ("Sarai Kale Khan", 28.5894, 77.2575),
    "S14": ("Moolchand", 28.5646, 77.2342),
}
# route -> list of stop ids (direction 0). Direction 1 is the reverse.
ROUTES = {
    "8": ["S01", "S02", "S03", "S04", "S06"],
    "34": ["S01", "S13", "S10", "S09"],
    "405": ["S07", "S08", "S04"],
    "534": ["S04", "S14", "S10", "S09"],
    "764": ["S07", "S06", "S11", "S14", "S09"],
    "620": ["S01", "S03", "S04", "S06"],
    "26": ["S01", "S02", "S08", "S06"],
}
LOW_DATA_ROUTES = {"764"}
SPEED_MULT = {"620": 0.8}   # a slower, quieter route (for the demo)


def hav(a, b):
    r = 6371000.0
    p1, p2 = math.radians(STOPS[a][1]), math.radians(STOPS[b][1])
    dl = math.radians(STOPS[b][2] - STOPS[a][2])
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def hms(sec):
    sec = int(round(sec))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def speed_kmh(hour):
    return 15.0 if (8 <= hour < 11 or 17 <= hour < 20) else 22.0


def headway_min(hour, weekend):
    if weekend:
        return 20
    return 6 if (8 <= hour < 11 or 17 <= hour < 20) else 12


def write_gtfs(out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stops.txt").write_text(
        "stop_id,stop_name,stop_lat,stop_lon\n"
        + "".join(f"{k},{v[0]},{v[1]},{v[2]}\n" for k, v in STOPS.items()))
    (out_dir / "routes.txt").write_text(
        "route_id,route_short_name,route_type\n" + "".join(f"R{r},{r},3\n" for r in ROUTES))
    (out_dir / "calendar.txt").write_text(
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WD,1,1,1,1,1,0,0,20261001,20261231\nWE,0,0,0,0,0,1,1,20261001,20261231\n")
    trips, times = ["route_id,service_id,trip_id,direction_id"], ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    n = 0
    for r, stops in ROUTES.items():
        for d, seq in ((0, stops), (1, stops[::-1])):
            for svc, weekend in (("WD", False), ("WE", True)):
                t = 5 * 3600
                while t < 23 * 3600:
                    n += 1
                    tid = f"T{n}"
                    trips.append(f"R{r},{svc},{tid},{d}")
                    cur = t
                    for k, s in enumerate(seq):
                        if k > 0:
                            dist = hav(seq[k - 1], s) * 1.3
                            cur += dist / (speed_kmh(int(cur // 3600)) * SPEED_MULT.get(r, 1.0) / 3.6)
                        times.append(f"{tid},{hms(cur)},{hms(cur + 30)},{s},{k + 1}")
                        cur += 30
                    t += headway_min(int(t // 3600), weekend) * 60
    (out_dir / "trips.txt").write_text("\n".join(trips) + "\n")
    (out_dir / "stop_times.txt").write_text("\n".join(times) + "\n")


def write_crowding(path):
    rnd = random.Random(7)
    rows = ["route,direction,stop_seq_from,stop_seq_to,day_type,hour,load_index,level,confidence,n_tickets,stop_name_from,stop_name_to"]
    route_factor = {"8": 1.25, "34": 0.9, "405": 1.0, "534": 0.8, "764": 0.6, "620": 0.55, "26": 0.85}
    for r, stops in ROUTES.items():
        ns = len(stops)
        for direction in ("UP", "DOWN"):
            order = stops if direction == "UP" else stops[::-1]
            for day_type in ("weekday", "weekend"):
                for hour in range(5, 24):   # hours 0-4 deliberately missing: tests "no data"
                    peak_morning = 8 <= hour < 11
                    peak_evening = 17 <= hour < 20
                    base = 0.35
                    if peak_morning:
                        base += 0.65 if direction == "UP" else 0.3
                    if peak_evening:
                        base += 0.65 if direction == "DOWN" else 0.3
                    if day_type == "weekend":
                        base *= 0.6
                    for k in range(ns - 1):
                        middle = 1.0 + 0.45 * math.sin(math.pi * (k + 0.5) / (ns - 1))
                        load = max(0.05, base * route_factor[r] * middle + rnd.uniform(-0.06, 0.06))
                        level = "seats" if load < 0.55 else ("standing" if load < 0.95 else "crowded")
                        n = int(load * 260 * (0.3 if r in LOW_DATA_ROUTES else 1.0))
                        conf = "high" if n >= 150 else ("medium" if n >= 40 else "low")
                        rows.append(f"{r}{direction},{direction},{k + 1},{k + 2},{day_type},{hour},{load:.3f},{level},{conf},{n},{STOPS[order[k]][0]},{STOPS[order[k + 1]][0]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n")


def ensure_sample(force=False):
    if force or not (config.SAMPLE_GTFS_DIR / "stop_times.txt").exists():
        write_gtfs(config.SAMPLE_GTFS_DIR)
    if force or not config.SAMPLE_CROWDING.exists():
        write_crowding(config.SAMPLE_CROWDING)


if __name__ == "__main__":
    ensure_sample(force=True)
    print("Fake GTFS ->", config.SAMPLE_GTFS_DIR)
    print("Fake crowding ->", config.SAMPLE_CROWDING)
