"""
Reads outputs/crowding.csv (contract with the pipeline) and answers:
"how crowded is route R, direction D, on this stretch, at this hour, on this day type?"

Contract columns:
  route, direction, stop_seq_from, stop_seq_to, day_type, hour,
  load_index, level, confidence, n_tickets
Optional: stop_name_from, stop_name_to.

Optional side files:
  crowding_route_hour.csv  route, direction, day_type, hour, load_index, level, confidence, n_tickets
      used ONLY when a stretch row is missing (always shown as low confidence, "route-wide average").
  stop_order.csv           route, direction, stop_seq, stop_name, order_source
      used to translate GTFS stop names into the pipeline's stop numbers (GTFS mode).

A missing row means "no usable data" (unknown), never "empty bus".
"""
import numpy as np
import pandas as pd

import config
from gtfs_store import norm_name, split_route

REQUIRED = ["route", "direction", "stop_seq_from", "stop_seq_to", "day_type",
            "hour", "load_index", "level", "confidence", "n_tickets"]
LEVELS = ["seats", "standing", "crowded"]
CONFS = ["low", "medium", "high"]


class Seg:
    """Crowding for one hop between two consecutive stops."""
    __slots__ = ("level", "load_index", "confidence", "n_tickets", "hour_used", "source", "dur", "i", "j")

    def __init__(self, level="unknown", load_index=float("nan"), confidence="low",
                 n_tickets=0, hour_used=None, source="none", dur=1.0, i=0, j=1):
        self.level = level
        self.load_index = load_index
        self.confidence = confidence
        self.n_tickets = n_tickets
        self.hour_used = hour_used
        self.source = source      # stretch | route-hour | nearby-hour | none
        self.dur = dur
        self.i = i
        self.j = j

    @property
    def fallback(self):
        return self.source in ("route-hour", "nearby-hour")


def _norm_routes(df):
    uniq = {r: split_route(r) for r in df["route"].dropna().unique()}
    base = df["route"].map(lambda r: uniq[r][0])
    suffix = df["route"].map(lambda r: uniq[r][1])
    d = df["direction"].astype(str).str.strip().str.upper()
    d = d.where(d.isin(["UP", "DOWN"]), suffix.fillna("UP"))
    return base, d


class Crowding:
    def __init__(self, df=None, route_hour=None, stop_order=None):
        if df is None or len(df) == 0:
            df = pd.DataFrame(columns=REQUIRED)
        missing = [c for c in REQUIRED if c not in df.columns]
        if missing:
            raise ValueError(f"crowding.csv is missing columns: {missing}")
        src = df
        df = df[REQUIRED].copy()
        df["base"], df["direction"] = _norm_routes(df)
        df["day_type"] = df["day_type"].astype(str).str.strip().str.lower()
        df["level"] = df["level"].astype(str).str.strip().str.lower()
        df["confidence"] = df["confidence"].astype(str).str.strip().str.lower()
        for c in ("stop_seq_from", "stop_seq_to", "hour", "n_tickets"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(int)
        df["load_index"] = pd.to_numeric(df["load_index"], errors="coerce")
        df = df.drop(columns=["route"])

        # optional stop names (only if the pipeline wrote them)
        self.has_names = all(c in src.columns for c in ("stop_name_from", "stop_name_to"))
        self._na = self._nb = None
        if self.has_names:
            self._na = src["stop_name_from"].fillna("").map(norm_name).to_numpy(dtype=object)
            self._nb = src["stop_name_to"].fillna("").map(norm_name).to_numpy(dtype=object)

        # compact arrays for fast lookup (871k rows stay small)
        self._a = df["stop_seq_from"].to_numpy(dtype=np.int32)
        self._b = df["stop_seq_to"].to_numpy(dtype=np.int32)
        self._load = df["load_index"].to_numpy(dtype=np.float32)
        self._lvl = df["level"].map({k: i for i, k in enumerate(LEVELS)}).fillna(-1).to_numpy(dtype=np.int8)
        self._conf = df["confidence"].map({k: i for i, k in enumerate(CONFS)}).fillna(0).to_numpy(dtype=np.int8)
        self._n = df["n_tickets"].to_numpy(dtype=np.int32)
        self._idx = df.groupby(["base", "direction", "day_type", "hour"], sort=False).indices if len(df) else {}
        for c in ("base", "direction", "day_type", "level", "confidence"):
            df[c] = df[c].astype("category")
        self.df = df.reset_index(drop=True)
        self.n_rows = len(df)

        # route-hour fallback
        self._rh = {}
        if route_hour is not None and len(route_hour):
            rh = route_hour.copy()
            rh["base"], rh["direction"] = _norm_routes(rh)
            for r in rh.itertuples(index=False):
                lvl = str(r.level).strip().lower()
                if lvl in LEVELS:
                    self._rh[(r.base, r.direction, str(r.day_type).strip().lower(), int(r.hour))] = (
                        float(r.load_index), lvl, int(r.n_tickets))
        self.n_route_hour = len(self._rh)

        # stop name -> pipeline stop number (used with a real GTFS)
        self._order = None
        if stop_order is not None and len(stop_order):
            so = stop_order.copy()
            so["base"], so["direction"] = _norm_routes(so)
            so["key"] = so["stop_name"].map(norm_name)
            so = so.sort_values(["base", "direction", "stop_seq"])
            order = {}
            for (b, d), g in so.groupby(["base", "direction"], sort=False):
                keys, seqs = g["key"].tolist(), g["stop_seq"].tolist()
                for k in range(len(keys) - 1):
                    if seqs[k + 1] == seqs[k] + 1:
                        order.setdefault((b, d, keys[k], keys[k + 1]), int(seqs[k]))
            self._order = order

    @classmethod
    def from_csv(cls, path, route_hour_path=None, stop_order_path=None):
        df = pd.read_csv(path, dtype={"route": str, "direction": str, "day_type": str, "level": str, "confidence": str})
        rh = None
        if route_hour_path is not None and _exists(route_hour_path):
            rh = pd.read_csv(route_hour_path, dtype={"route": str, "direction": str, "day_type": str, "level": str})
        so = None
        if stop_order_path is not None and _exists(stop_order_path):
            so = pd.read_csv(stop_order_path, dtype={"route": str, "direction": str, "stop_name": str})
        return cls(df, route_hour=rh, stop_order=so)

    # ------------------------------------------------------------ lookup
    def _find(self, base, direction, day_type, hour, a, b, na, nb):
        idx = self._idx.get((base, direction, day_type, hour))
        if idx is None:
            return None
        if self.has_names and na and nb:
            m = (self._na[idx] == na) & (self._nb[idx] == nb)
            if m.any():
                return int(idx[m][0])
        if a is None:
            return None
        m = (self._a[idx] <= a) & (self._b[idx] >= b)
        if not m.any():
            return None
        cand = idx[m]
        return int(cand[int(np.argmin(self._b[cand] - self._a[cand]))])

    def segment(self, base, direction, day_type, hour, a, b, name_a=None, name_b=None):
        """Crowding for the hop with stop numbers a -> b (see STOP_SEQ_MODE in config).

        With a stop_order file, the hop is found by stop NAMES and translated to the pipeline's stop numbers.
        If crowding.csv has stop_name_from/to, a row with exactly those names is preferred.
        A missing stretch row falls back to the route-wide average (low confidence), else unknown.
        """
        na = norm_name(name_a) if name_a else ""
        nb = norm_name(name_b) if name_b else ""
        if self._order is not None and na and nb:
            s0 = self._order.get((base, direction, na, nb))
            a, b = (None, None) if s0 is None else (s0, s0 + 1)
        for d in [0] + [x for k in range(1, config.HOUR_FALLBACK + 1) for x in (k, -k)]:
            h = (hour + d) % 24
            r = self._find(base, direction, day_type, h, a, b, na, nb)
            if r is not None:
                lvl = int(self._lvl[r])
                return Seg(level=LEVELS[lvl] if lvl >= 0 else "unknown", load_index=float(self._load[r]),
                           confidence=CONFS[int(self._conf[r])] if d == 0 else "low",
                           n_tickets=int(self._n[r]), hour_used=h,
                           source="stretch" if d == 0 else "nearby-hour")
            if d == 0:
                rh = self._rh.get((base, direction, day_type, h))
                if rh is not None:
                    return Seg(level=rh[1], load_index=rh[0], confidence="low", n_tickets=rh[2],
                               hour_used=h, source="route-hour")
        return Seg()

    # ------------------------------------------------ operator view helpers
    def overloaded_table(self, day_type, top=25, min_tickets=200):
        d = self.df[self.df["day_type"] == day_type]
        if d.empty:
            return pd.DataFrame()
        d = d.assign(is_crowded=(d["level"] == "crowded").astype(int), is_low=(d["confidence"] == "low").astype(int))
        t = d.groupby(["base", "direction", "hour"], observed=True).agg(
            stretches=("level", "size"), crowded=("is_crowded", "sum"), low=("is_low", "sum"),
            max_load=("load_index", "max"), tickets=("n_tickets", "sum")).reset_index()
        t = t[(t["tickets"] >= min_tickets) & (t["stretches"] >= 3)]
        t["share_crowded"] = (t["crowded"] / t["stretches"]).round(2)
        t["share_low_conf"] = (t["low"] / t["stretches"]).round(2)
        t = t.sort_values(["share_crowded", "max_load"], ascending=False).head(top)
        t = t.rename(columns={"base": "route"})
        t["direction"] = t["direction"].astype(str)
        t["route"] = t["route"].astype(str)
        return t[["route", "direction", "hour", "share_crowded", "max_load", "tickets", "share_low_conf"]].reset_index(drop=True)

    def routes(self):
        p = self.df[["base", "direction"]].drop_duplicates()
        return sorted(zip(p["base"].astype(str), p["direction"].astype(str)))

    def hour_profile(self, base, direction):
        d = self.df[(self.df["base"] == base) & (self.df["direction"] == direction)].copy()
        if d.empty:
            return pd.DataFrame()
        d["score"] = d["level"].astype(str).map(config.LEVEL_SCORE)
        p = d.groupby(["hour", "day_type"], observed=True)["score"].mean().unstack("day_type")
        return p.reindex(range(24))


def _exists(p):
    from pathlib import Path
    return Path(p).exists()
