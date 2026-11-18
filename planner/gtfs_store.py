"""
Load static GTFS and turn it into "patterns".

A pattern = one route + direction + exact ordered list of stops. All scheduled
trips that follow it are stored as two arrays (trips x stops): departure and
arrival seconds after midnight. That is all the planner needs.

GTFS files assumed (standard names, in data/raw/gtfs/):
  stops.txt       stop_id, stop_name, stop_lat, stop_lon
  routes.txt      route_id, route_short_name (or route_long_name if short is empty)
  trips.txt       route_id, service_id, trip_id, direction_id (optional)
  stop_times.txt  trip_id, stop_id, stop_sequence, arrival_time, departure_time
  calendar.txt    (optional) service_id, monday..sunday -> weekday/weekend split
shapes.txt is NOT needed (map draws stop-to-stop lines).
"""
import hashlib
import math
import pickle
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import config

_SUFFIX = re.compile(r"^(.*?)(UP|DOWN)$")


def split_route(name):
    """'8ADOWN' -> ('8A', 'DOWN'); '8A' -> ('8A', None)."""
    s = re.sub(r"\s+", "", str(name)).upper()
    m = _SUFFIX.match(s)
    if m and m.group(1):
        return m.group(1), m.group(2)
    return s, None


def norm_name(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fmt_clock(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m = rem // 60
    return f"{h % 24:02d}:{m:02d}" + (" (+1d)" if h >= 24 else "")


# ---------------------------------------------------------------- parsing

def _to_seconds(col):
    parts = col.astype("string").str.strip().str.split(":", expand=True)
    if parts.shape[1] < 3:
        return np.full(len(col), np.nan)
    h = pd.to_numeric(parts[0], errors="coerce")
    m = pd.to_numeric(parts[1], errors="coerce")
    s = pd.to_numeric(parts[2], errors="coerce")
    return (h * 3600 + m * 60 + s).to_numpy(dtype="float64", na_value=np.nan)


def _service_flags(gtfs_dir):
    cal = Path(gtfs_dir) / "calendar.txt"
    if not cal.exists():
        return {}
    df = pd.read_csv(cal, dtype={"service_id": str})
    wk = df[["monday", "tuesday", "wednesday", "thursday", "friday"]].sum(axis=1) > 0
    we = df[["saturday", "sunday"]].sum(axis=1) > 0
    return {sid: (bool(a), bool(b)) for sid, a, b in zip(df["service_id"], wk, we)}


def build_raw(gtfs_dir):
    gtfs_dir = Path(gtfs_dir)
    stops_df = pd.read_csv(
        gtfs_dir / "stops.txt", dtype={"stop_id": str},
        usecols=lambda c: c in {"stop_id", "stop_name", "stop_lat", "stop_lon"},
    )
    routes = pd.read_csv(
        gtfs_dir / "routes.txt", dtype={"route_id": str},
        usecols=lambda c: c in {"route_id", "route_short_name", "route_long_name"},
    )
    short = routes["route_short_name"] if "route_short_name" in routes else pd.Series([None] * len(routes))
    long_ = routes["route_long_name"] if "route_long_name" in routes else pd.Series([None] * len(routes))
    routes["rname"] = short.where(short.notna() & (short.astype(str).str.strip() != ""), long_)

    trips = pd.read_csv(
        gtfs_dir / "trips.txt", dtype={"route_id": str, "trip_id": str, "service_id": str},
        usecols=lambda c: c in {"route_id", "service_id", "trip_id", "direction_id"},
    )
    if "direction_id" not in trips:
        trips["direction_id"] = 0
    trips = trips.merge(routes[["route_id", "rname"]], on="route_id", how="left")

    flags = _service_flags(gtfs_dir)
    meta = {}
    for t in trips.itertuples(index=False):
        if pd.isna(t.rname):
            continue
        base, suffix = split_route(t.rname)
        did = 0 if pd.isna(t.direction_id) else int(t.direction_id)
        direction = suffix or config.DIRECTION_MAP.get(did, "UP")
        meta[t.trip_id] = (base, direction, flags.get(t.service_id, (True, True)))

    st = pd.read_csv(
        gtfs_dir / "stop_times.txt", dtype={"trip_id": str, "stop_id": str},
        usecols=lambda c: c in {"trip_id", "stop_id", "stop_sequence", "arrival_time", "departure_time"},
    )
    for a, b in (("arrival_time", "departure_time"), ("departure_time", "arrival_time")):
        if a not in st:
            st[a] = st[b]
    st = st.sort_values(["trip_id", "stop_sequence"], kind="stable")
    tid = st["trip_id"].to_numpy()
    sid = st["stop_id"].to_numpy()
    seq = st["stop_sequence"].to_numpy()
    arr_s = _to_seconds(st["arrival_time"])
    dep_s = _to_seconds(st["departure_time"])

    bounds = np.flatnonzero(tid[1:] != tid[:-1]) + 1
    starts = np.r_[0, bounds]
    ends = np.r_[bounds, len(tid)]

    groups = {}
    for a, b in zip(starts, ends):
        m = meta.get(tid[a])
        if m is None or b - a < 2:
            continue
        arr = arr_s[a:b].copy()
        dep = dep_s[a:b].copy()
        arr = np.where(np.isnan(arr), dep, arr)
        dep = np.where(np.isnan(dep), arr, dep)
        if np.isnan(dep).any():  # fill gaps by interpolation along the trip
            ok = ~np.isnan(dep)
            if ok.sum() < 2:
                continue
            x = np.arange(b - a)
            dep = np.interp(x, x[ok], dep[ok])
            arr = np.interp(x, x[ok], arr[ok])
        key = (m[0], m[1], tuple(sid[a:b]))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"seq": seq[a:b].astype(np.int64), "dep": [], "arr": [], "wd": [], "we": []}
        g["dep"].append(dep)
        g["arr"].append(arr)
        g["wd"].append(m[2][0])
        g["we"].append(m[2][1])

    patterns = []
    for (base, direction, stop_ids), g in groups.items():
        dep = np.vstack(g["dep"]).astype(np.float32)
        arr = np.vstack(g["arr"]).astype(np.float32)
        order = np.argsort(dep[:, 0], kind="stable")
        patterns.append({
            "base": base, "direction": direction, "stop_ids": list(stop_ids),
            "seqs": g["seq"], "dep": dep[order], "arr": arr[order],
            "wd": np.array(g["wd"], dtype=bool)[order], "we": np.array(g["we"], dtype=bool)[order],
        })

    stops = {
        str(r.stop_id): (str(r.stop_name), float(r.stop_lat), float(r.stop_lon))
        for r in stops_df.itertuples(index=False)
    }
    return {"stops": stops, "patterns": patterns}


# ---------------------------------------------------------------- objects

class Pattern:
    def __init__(self, pid, d):
        self.pid = pid
        self.base = d["base"]
        self.direction = d["direction"]
        self.stop_ids = d["stop_ids"]
        self.seqs = d["seqs"]
        self.dep = d["dep"]
        self.arr = d["arr"]
        self.wd = d["wd"]
        self.we = d["we"]
        self.timetable = d.get("timetable", True)   # False = no schedule, only stop order + rough timing

    @property
    def label(self):
        return f"{self.base} {self.direction}"

    def first_trip(self, i, t, day_type):
        """(trip index, boarding time) for the next bus at stop position i, or None."""
        if not self.timetable:
            return 0, float(t)          # no schedule: assume the bus is there when you arrive
        k = self.next_trip(i, t, day_type)
        if k is None:
            return None
        return k, float(self.dep[k, i])

    def arrival(self, k, board_t, i, j):
        """Arrival time at position j for a ride boarded at position i at board_t."""
        if not self.timetable:
            return board_t + float(self.arr[0, j] - self.dep[0, i])
        return float(self.arr[k, j])

    def next_trip(self, i, t, day_type):
        """Index of the first trip departing stop position i at/after time t (seconds), or None."""
        mask = self.wd if day_type == "weekday" else self.we
        col = self.dep[:, i]
        ok = mask & (col >= t - 1e-6)
        if not ok.any():
            return None
        idx = np.flatnonzero(ok)
        return int(idx[np.argmin(col[idx])])


class Store:
    def __init__(self, raw, source=""):
        self.source = source
        self.timetable = raw.get("timetable", True)
        self.has_coords = raw.get("has_coords", True)
        self.stops = {}
        for sid, (name, lat, lon) in raw["stops"].items():
            self.stops[sid] = {"name": name, "lat": lat, "lon": lon, "key": norm_name(name)}
        self.patterns = [Pattern(i, d) for i, d in enumerate(raw["patterns"])]
        self.stop_index = defaultdict(list)  # stop_id -> [(pattern id, position)]
        for p in self.patterns:
            for i, sid in enumerate(p.stop_ids):
                self.stop_index[sid].append((p.pid, i))
        name_to_sids = defaultdict(list)
        for sid, s in self.stops.items():
            if sid in self.stop_index:
                name_to_sids[s["name"]].append(sid)
        self.name_to_sids = dict(name_to_sids)
        self.stop_names = sorted(self.name_to_sids)
        self.n_trips = int(sum(len(p.dep) for p in self.patterns))
        self.routes = sorted({p.base for p in self.patterns})


def _signature(gtfs_dir):
    h = hashlib.sha1(str(config.STOP_SEQ_MODE).encode())
    h.update(repr(sorted(config.DIRECTION_MAP.items())).encode())
    for name in ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt", "calendar.txt"):
        p = Path(gtfs_dir) / name
        if p.exists():
            s = p.stat()
            h.update(f"{name}:{s.st_size}:{s.st_mtime_ns}".encode())
    return h.hexdigest()[:16]


def load_store(gtfs_dir):
    gtfs_dir = Path(gtfs_dir)
    cache = config.CACHE_DIR / f"gtfs_{_signature(gtfs_dir)}.pkl"
    if cache.exists():
        try:
            with open(cache, "rb") as f:
                return Store(pickle.load(f), source=str(gtfs_dir))
        except Exception:
            pass
    raw = build_raw(gtfs_dir)
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache, "wb") as f:
            pickle.dump(raw, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        pass
    return Store(raw, source=str(gtfs_dir))


# ---------------------------------------------------------------- ticket-only mode (no GTFS)

def build_raw_from_stop_order(path):
    """Patterns from outputs/stop_order.csv (route, direction, stop_seq, stop_name). No timetable, no coordinates."""
    so = pd.read_csv(path, dtype={"route": str, "direction": str, "stop_name": str})
    so = so.dropna(subset=["route", "direction", "stop_name"])
    so = so.sort_values(["route", "direction", "stop_seq"], kind="stable")
    nan = float("nan")
    stops, patterns = {}, []
    for (route, direction), g in so.groupby(["route", "direction"], sort=False):
        if len(g) < 2:
            continue
        names = g["stop_name"].tolist()
        for n in names:
            stops.setdefault(n, (n, nan, nan))
        rel = (np.arange(len(names)) * config.TICKET_MODE_SEC_PER_STOP).astype(np.float32).reshape(1, -1)
        patterns.append({
            "base": split_route(route)[0], "direction": str(direction).strip().upper(),
            "stop_ids": names, "seqs": g["stop_seq"].to_numpy(dtype=np.int64),
            "dep": rel, "arr": rel.copy(),
            "wd": np.array([True]), "we": np.array([True]), "timetable": False,
        })
    return {"stops": stops, "patterns": patterns, "timetable": False, "has_coords": False}


def load_ticket_store(stop_order_path):
    return Store(build_raw_from_stop_order(stop_order_path), source=str(stop_order_path))
