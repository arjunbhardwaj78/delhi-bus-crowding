#!/usr/bin/env python
"""
Crowding pipeline (Claude A) - Transport Stack Hackathon 2026, Problem 2.
Raw ETM/e-ticket CSV  ->  outputs/crowding.csv  (+ route-hour fallback, stop order, sanity checks).

Run from repo root:
    python pipeline/run_pipeline.py            # full run (~2.56M rows)
    python pipeline/run_pipeline.py --sample   # quick run on first 300k rows

Two stop-order modes (chosen per route, flagged in column order_source):
    gtfs     : data/raw/gtfs/{routes,trips,stop_times,stops}.txt present AND names match tickets
    tickets  : stop order rebuilt from the tickets themselves (fare-distance least squares)

AI disclosure: this code and design were produced with Claude (Anthropic) and run/verified by the team.
"""
import argparse
import os
import re
import sys
import time
import zipfile
from collections import Counter

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INPUT = os.path.join(ROOT, "data", "raw", "October-Delhi.csv.zip")
GTFS_DIR = os.path.join(ROOT, "data", "raw", "gtfs")
SIZE_FILE = os.path.join(ROOT, "data", "raw", "route_bus_size.csv")  # optional: route,size (9m|12m)
INVALID_VEH = {"", "null", "nan", "none", "na", "n/a", "0", "-"}

# ------------------------- ALL ASSUMPTIONS LIVE HERE -------------------------
CFG = dict(
    HOLIDAYS=["2026-10-02", "2026-10-20"],      # treated as weekend day type (Gandhi Jayanti, Dussehra)
    PEAK_HOURS=[7, 8, 9, 10, 17, 18, 19, 20],
    UPLIFT_PEAK=1.30,                            # ASSUMPTION: pass holders / unticketed riders, peak hours
    UPLIFT_OFF=1.10,                             # ASSUMPTION: off-peak
    CAP_12M=1.00,                                # relative capacity, 12 m bus
    CAP_9M=0.60,                                 # relative capacity, 9 m bus (ASSUMPTION ~ 35 vs 60 pax)
    DEFAULT_SIZE="12m",                          # used when route size unknown
    CONF_HIGH=60,                                # n_tickets (crossing the stretch in that day_type/hour, whole month)
    CONF_MED=20,
    MIN_TICKETS_OUT=5,                           # cells below this are not written (absent row = no data)
    LEVEL_SEATS=0.40,                            # load_index < 0.40  -> seats
    LEVEL_CROWDED=0.75,                          # load_index >= 0.75 -> crowded, between -> standing
    MIN_ORDER_CONSISTENCY=0.85,                  # below this the stop order of a route is unreliable -> confidence low
    MIN_BUSES=1.0,
    MIN_VALID_SHARE=0.20,
)
# -----------------------------------------------------------------------------

LOG = []


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.append(s)


class Interner:
    def __init__(self):
        self.d, self.keys, self.names = {}, [], []

    def encode(self, key, disp=None):
        n = len(key)
        if n == 0:
            return np.zeros(0, dtype=np.int32)
        codes, uniq = pd.factorize(key)
        _, fi = np.unique(codes, return_index=True)
        dv = (disp if disp is not None else key).to_numpy()
        m = np.empty(len(uniq), dtype=np.int32)
        for i, u in enumerate(uniq):
            j = self.d.get(u)
            if j is None:
                j = len(self.keys)
                self.d[u] = j
                self.keys.append(u)
                self.names.append(str(dv[fi[i]]))
            m[i] = j
        return m[codes]


def open_csv(path):
    if path.lower().endswith(".zip"):
        zf = zipfile.ZipFile(path)
        name = [n for n in zf.namelist() if n.lower().endswith(".csv")
                and not n.startswith("__MACOSX") and not os.path.basename(n).startswith("._")][0]
        return zf.open(name)
    return path


def norm_stop(s):
    return s.str.strip().str.replace(r"\s+", " ", regex=True)


# ------------------------------- 1. LOAD + CLEAN -------------------------------
def load_tickets(path, nrows):
    rt, sti, vi = Interner(), Interner(), Interner()
    parts, st = [], Counter()
    nodir = Counter()
    cols = ["passenger_count", "amount", "vehicle_number", "end_stop_name", "route", "start_stop_name", "created_at"]
    reader = pd.read_csv(open_csv(path), dtype=str, keep_default_na=False, na_values=[],
                         usecols=cols, chunksize=500_000, nrows=nrows)
    for ch in reader:
        st["rows_read"] += len(ch)
        r = ch["route"].str.strip().str.upper()
        m = r.str.extract(r"^(.+?)\s*(UP|DOWN)\s*(\d*)$")
        vsuf = ("-" + m[2]).where(m[2].fillna("") != "", "")
        has_dir = m[1].notna()
        nodir.update(r[~has_dir].value_counts().head(20).to_dict())
        pax = pd.to_numeric(ch["passenger_count"], errors="coerce")
        amt = pd.to_numeric(ch["amount"], errors="coerce")
        ca = ch["created_at"].str.strip()
        day = pd.to_datetime(ca.str[:10], format="%Y-%m-%d", errors="coerce")
        hr = pd.to_numeric(ca.str[11:13], errors="coerce")
        s1, s2 = norm_stop(ch["start_stop_name"]), norm_stop(ch["end_stop_name"])
        k1, k2 = s1.str.lower(), s2.str.lower()
        v = ch["vehicle_number"].str.strip()
        vok = ~v.str.lower().isin(INVALID_VEH)
        ok = has_dir & day.notna() & hr.between(0, 23) & (pax > 0) & amt.notna() & (k1 != "") & (k2 != "") & (k1 != k2)
        st["dropped_no_direction"] += int((~has_dir).sum())
        st["dropped_other_bad"] += int((has_dir & ~ok).sum())
        if not ok.any():
            continue
        vm = vok[ok].to_numpy()
        vid = np.full(int(ok.sum()), -1, dtype=np.int32)
        if vm.any():
            vid[vm] = vi.encode(v[ok][vm])
        parts.append(dict(
            rd=rt.encode((m[0] + vsuf + "|" + m[1])[ok]),
            a=sti.encode(k1[ok], s1[ok]),
            b=sti.encode(k2[ok], s2[ok]),
            pax=pax[ok].to_numpy().astype(np.int16),
            fare=(amt / pax / 4.5).clip(lower=1.0)[ok].to_numpy().astype(np.float32),
            day=day[ok].to_numpy().astype("datetime64[D]").astype(np.int64).astype(np.int32),
            hr=hr[ok].to_numpy().astype(np.int8),
            veh=vid,
        ))
        st["rows_kept"] += int(ok.sum())
        log(f"  read {st['rows_read']:,} rows ...")
    if not parts:
        sys.exit("ERROR: no usable rows found. Check the input file.")
    T = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    return T, rt, sti, vi, st, nodir


# ------------------------------- 2. STOP ORDER -------------------------------
def solve_order(a, b, fare):
    """Order stops of one route-direction from tickets: least squares on x[end]-x[start] = fare_units."""
    allst, inv = np.unique(np.concatenate([a, b]), return_inverse=True)
    inv = inv.reshape(-1)
    n = len(allst)
    la, lb = inv[:len(a)].astype(np.int64), inv[len(a):].astype(np.int64)
    uk, ui, cnt = np.unique(la * n + lb, return_inverse=True, return_counts=True)
    ui = ui.reshape(-1)
    d = np.bincount(ui, weights=fare) / cnt
    ia, ib = uk // n, uk % n
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, j in zip(ia.tolist(), ib.tolist()):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj
    roots = np.array([find(i) for i in range(n)])
    wsum = {}
    for r_, c_ in zip(roots[ia], cnt):
        wsum[r_] = wsum.get(r_, 0) + c_
    best = max(wsum, key=wsum.get)
    keep = np.flatnonzero(roots == best)
    if len(keep) < 2:
        return None
    remap = -np.ones(n, dtype=np.int64)
    remap[keep] = np.arange(len(keep))
    em = remap[ia] >= 0
    ea, eb, ec, ed = remap[ia[em]], remap[ib[em]], cnt[em].astype(float), d[em]
    m = len(keep)
    L = np.zeros((m, m))
    r = np.zeros(m)
    np.add.at(L, (ea, ea), ec)
    np.add.at(L, (eb, eb), ec)
    np.add.at(L, (ea, eb), -ec)
    np.add.at(L, (eb, ea), -ec)
    np.add.at(r, eb, ec * ed)
    np.add.at(r, ea, -ec * ed)
    x = np.linalg.lstsq(L, r, rcond=None)[0]
    order = np.argsort(x, kind="stable")
    return allst[keep][order]  # global stop ids in travel order


def ticket_order(a, b, fare):
    ordered = solve_order(a, b, fare)
    if ordered is None:
        return None
    seq = {int(s): i + 1 for i, s in enumerate(ordered)}
    sa = np.array([seq.get(int(x), 0) for x in a], dtype=np.int32)
    sb = np.array([seq.get(int(x), 0) for x in b], dtype=np.int32)
    names_ids = [int(s) for s in ordered]
    return sa, sb, names_ids


def load_gtfs():
    need = ["routes.txt", "trips.txt", "stop_times.txt", "stops.txt"]
    if not all(os.path.exists(os.path.join(GTFS_DIR, f)) for f in need):
        return None
    log("GTFS found -> trying GTFS stop order per route")
    rd = lambda f, **kw: pd.read_csv(os.path.join(GTFS_DIR, f), **kw)
    routes = rd("routes.txt", dtype=str, usecols=["route_id", "route_short_name"])
    tcols = pd.read_csv(os.path.join(GTFS_DIR, "trips.txt"), nrows=0).columns
    use = ["route_id", "trip_id"] + (["direction_id"] if "direction_id" in tcols else [])
    trips = rd("trips.txt", dtype=str, usecols=use)
    if "direction_id" not in trips:
        trips["direction_id"] = "0"
    trips["direction_id"] = trips["direction_id"].fillna("0")
    stops = rd("stops.txt", dtype=str, usecols=["stop_id", "stop_name"])
    stt = rd("stop_times.txt", dtype={"trip_id": str, "stop_id": str}, usecols=["trip_id", "stop_id", "stop_sequence"])
    n = stt.groupby("trip_id").size().rename("n")
    trips = trips.join(n, on="trip_id").dropna(subset=["n"]).merge(routes, on="route_id")
    best = trips.sort_values("n", ascending=False).drop_duplicates(["route_short_name", "direction_id"])
    stt = stt[stt["trip_id"].isin(best["trip_id"])].merge(stops, on="stop_id").sort_values(["trip_id", "stop_sequence"])
    info = best.set_index("trip_id")[["route_short_name", "direction_id"]]
    G = {}
    for tid, g in stt.groupby("trip_id"):
        short, did = info.loc[tid, "route_short_name"], info.loc[tid, "direction_id"]
        keys = g["stop_name"].str.strip().str.replace(r"\s+", " ", regex=True).str.lower().tolist()
        seqmap = {}
        for k in keys:
            if k not in seqmap:
                seqmap[k] = len(seqmap) + 1
        G[(str(short).strip().upper(), str(did))] = seqmap
    log(f"  GTFS: {len(G)} route-direction patterns loaded")
    return G


def gtfs_order(G, route, a, b, stop_keys, stop_names):
    u = np.unique(np.concatenate([a, b]))
    best = None
    for did in ("0", "1"):
        seqmap = G.get((route, did))
        if not seqmap:
            continue
        seqs = np.array([seqmap.get(stop_keys[i], 0) for i in u], dtype=np.int32)
        sa, sb = seqs[np.searchsorted(u, a)], seqs[np.searchsorted(u, b)]
        mt = (sa > 0) & (sb > 0)
        if not mt.any():
            continue
        cov, cons = mt.mean(), float((sb[mt] > sa[mt]).mean())
        if best is None or cons > best[0]:
            best = (cons, cov, sa, sb, seqmap)
    if best is None or best[1] < 0.6 or best[0] < 0.7:
        return None
    cons, cov, sa, sb, seqmap = best
    inv_names = {k: stop_names[i] for i, k in zip(u, [stop_keys[i] for i in u])}
    ordered = [inv_names.get(k, k.title()) for k, _ in sorted(seqmap.items(), key=lambda kv: kv[1])]
    return sa, sb, ordered


# ------------------------------- 3. MAIN COMPUTE -------------------------------
def level_of(idx):
    return np.where(idx < CFG["LEVEL_SEATS"], "seats", np.where(idx < CFG["LEVEL_CROWDED"], "standing", "crowded"))


def conf_of(n, quality_ok):
    c = np.where(n >= CFG["CONF_HIGH"], "high", np.where(n >= CFG["CONF_MED"], "medium", "low"))
    return np.where(quality_ok, c, "low")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", nargs="?", const=300_000, type=int, default=None, help="use only first N rows (default 300000)")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--out", default=os.path.join(ROOT, "outputs"))
    ap.add_argument("--smooth", action="store_true", help="smooth load across adjacent stretches (alighting uncertainty)")
    args = ap.parse_args()
    t0 = time.time()
    out = args.out
    os.makedirs(out, exist_ok=True)
    os.makedirs(os.path.join(out, "sanity"), exist_ok=True)
    if not os.path.exists(args.input):
        sys.exit(f"ERROR: input not found: {args.input}\nDownload October-Delhi.csv.zip in your browser and put it in data/raw/")

    log(f"== Loading tickets (sample={args.sample}) ==")
    T, rt, sti, vi, st, nodir = load_tickets(args.input, args.sample)
    N = len(T["rd"])
    log(f"rows read {st['rows_read']:,} | kept {st['rows_kept']:,} | dropped no UP/DOWN suffix {st['dropped_no_direction']:,} "
        f"| dropped other bad {st['dropped_other_bad']:,}")
    if nodir:
        log("  top routes with no UP/DOWN suffix (dropped):", dict(nodir.most_common(8)))

    # day types
    d0, d1 = int(T["day"].min()), int(T["day"].max())
    daytype = np.zeros(d1 - d0 + 1, dtype=np.int8)
    present = np.unique(T["day"])
    hol = set(pd.to_datetime(CFG["HOLIDAYS"]).values.astype("datetime64[D]").astype(np.int64).tolist())
    for dd in range(d0, d1 + 1):
        wd = (dd + 3) % 7  # 1970-01-01 was Thursday(3); Monday=0
        daytype[dd - d0] = 1 if (wd >= 5 or dd in hol) else 0
    dt_row = daytype[T["day"] - d0]
    ndays = np.array([max(1, int((daytype[present - d0] == k).sum())) for k in (0, 1)], dtype=float)
    log(f"days in data: weekday-type {int(ndays[0])}, weekend/holiday-type {int(ndays[1])}")
    DT = np.array(["weekday", "weekend"])

    # uplift, capacity
    UP = np.array([CFG["UPLIFT_PEAK"] if h in CFG["PEAK_HOURS"] else CFG["UPLIFT_OFF"] for h in range(24)])
    sizes = {}
    if os.path.exists(SIZE_FILE):
        sz = pd.read_csv(SIZE_FILE, dtype=str)
        sizes = {r.strip().upper(): s.strip().lower() for r, s in zip(sz.iloc[:, 0], sz.iloc[:, 1])}
        log(f"bus size file used for {len(sizes)} routes")

    # observed-bus proxy: distinct tagged vehicles per route-direction/day-type/hour (avg per day)
    nrd = len(rt.keys)
    BUS = np.zeros((nrd, 2, 24))
    tag = T["veh"] >= 0
    vdf = pd.DataFrame({"rd": T["rd"][tag], "day": T["day"][tag], "hr": T["hr"][tag], "v": T["veh"][tag]}).drop_duplicates()
    g = vdf.groupby(["rd", "day", "hr"]).size().reset_index(name="k")
    g["dt"] = daytype[g["day"].to_numpy() - d0]
    s = g.groupby(["rd", "dt", "hr"])["k"].sum().reset_index()
    BUS[s["rd"].to_numpy(), s["dt"].to_numpy(), s["hr"].to_numpy()] = s["k"].to_numpy() / ndays[s["dt"].to_numpy()]
    tot_rd = np.bincount(T["rd"], minlength=nrd).astype(float)
    tag_rd = np.bincount(T["rd"], weights=tag.astype(float), minlength=nrd)
    vshare = np.where(tot_rd > 0, tag_rd / np.maximum(tot_rd, 1), 0)

    # GTFS (optional)
    G = load_gtfs()
    if G is None:
        log("MODE: NO GTFS found -> stop order rebuilt from tickets (flagged order_source=tickets)")

    log("== Stop order + load per route-direction ==")
    order = np.argsort(T["rd"], kind="stable")
    rd_sorted = T["rd"][order]
    bounds = np.concatenate([[0], np.flatnonzero(np.diff(rd_sorted)) + 1, [N]])

    cells, rh_rows, stop_rows, route_q = [], [], [], []
    n_done = 0
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        idx = order[lo:hi]
        rdid = int(rd_sorted[lo])
        route, direction = rt.keys[rdid].split("|")
        a, b, fare = T["a"][idx], T["b"][idx], T["fare"][idx]
        res, src = None, "tickets"
        if G is not None:
            gr = gtfs_order(G, route, a, b, sti.keys, sti.names)
            if gr is not None:
                sa, sb, ordered_names = gr
                res, src = (sa, sb, ordered_names), "gtfs"
        if res is None:
            tr = ticket_order(a, b, fare)
            if tr is None:
                continue
            sa, sb, ids = tr
            res, src = (sa, sb, [sti.names[i] for i in ids]), "tickets"
        sa, sb, names = res
        nseq = len(names)
        if nseq < 2:
            continue
        valid = (sa > 0) & (sb > sa)
        cons = float(valid.sum() / max(1, ((sa > 0) & (sb > 0)).sum()))
        quality_ok = cons >= CFG["MIN_ORDER_CONSISTENCY"]
        route_q.append((route, direction, src, nseq, len(idx), round(cons, 3)))
        for i, nm in enumerate(names):
            stop_rows.append((route, direction, i + 1, nm, src))
        if not valid.any():
            continue
        sf, sT = sa[valid].astype(np.int64), sb[valid].astype(np.int64)
        pax = T["pax"][idx][valid].astype(float)
        tg = (T["veh"][idx][valid] >= 0).astype(float)
        cell = dt_row[idx][valid].astype(np.int64) * 24 + T["hr"][idx][valid].astype(np.int64)
        W = nseq + 2

        def cumflow(w):
            m = (np.bincount(cell * W + sf, weights=w, minlength=48 * W)
                 - np.bincount(cell * W + sT, weights=w, minlength=48 * W))
            return np.cumsum(m.reshape(48, W), axis=1)[:, 1:nseq].reshape(2, 24, nseq - 1)

        f_all, f_tag, n_cross = cumflow(pax), cumflow(pax * tg), cumflow(np.ones_like(pax))
        if args.smooth:
            def sm(f):
                p = np.pad(f, ((0, 0), (0, 0), (1, 1)), mode="edge")
                return 0.25 * p[..., :-2] + 0.5 * p[..., 1:-1] + 0.25 * p[..., 2:]
            f_all, f_tag = sm(f_all), sm(f_tag)
        nd = ndays[:, None, None]
        flow = f_all / nd
        buses = np.maximum(CFG["MIN_BUSES"], BUS[rdid] / max(vshare[rdid], CFG["MIN_VALID_SHARE"]))[:, :, None]
        size = sizes.get(route.upper(), CFG["DEFAULT_SIZE"])
        cap = CFG["CAP_9M"] if size == "9m" else CFG["CAP_12M"]
        raw = flow * UP[None, :, None] / buses / cap

        m = n_cross >= CFG["MIN_TICKETS_OUT"]
        di, hi_, ki = np.nonzero(m)
        if len(di):
            nm_arr = np.array(names, dtype=object)
            cells.append(pd.DataFrame({
                "route": route, "direction": direction,
                "stop_seq_from": ki + 1, "stop_seq_to": ki + 2,
                "day_type": DT[di], "hour": hi_.astype(np.int8),
                "n_tickets": n_cross[m].astype(np.int32),
                "stop_name_from": nm_arr[ki], "stop_name_to": nm_arr[ki + 1],
                "flow_pax_per_hour": flow[m], "buses_est": np.broadcast_to(buses, raw.shape)[m],
                "bus_size": size, "raw": raw[m], "order_source": src, "order_consistency": round(cons, 3),
                "_quality_ok": quality_ok,
                "_flow_tag": (f_tag / nd)[m], "_flow_untag": ((f_all - f_tag) / nd)[m],
            }))
            # route-hour fallback
            wsum = n_cross.sum(axis=2)
            with np.errstate(invalid="ignore", divide="ignore"):
                rawm = np.where(wsum > 0, (raw * n_cross).sum(axis=2) / wsum, 0)
            nt = np.bincount(cell, minlength=48).reshape(2, 24)
            mm = nt >= CFG["MIN_TICKETS_OUT"]
            d2, h2 = np.nonzero(mm)
            if len(d2):
                rh_rows.append(pd.DataFrame({"route": route, "direction": direction, "day_type": DT[d2],
                                             "hour": h2.astype(np.int8), "n_tickets": nt[mm].astype(np.int32),
                                             "raw": rawm[mm], "_quality_ok": quality_ok}))
        n_done += 1
        if n_done % 500 == 0:
            log(f"  {n_done} route-directions done ...")

    if not cells:
        sys.exit("ERROR: no output cells produced.")
    C = pd.concat(cells, ignore_index=True)
    RH = pd.concat(rh_rows, ignore_index=True)
    RQ = pd.DataFrame(route_q, columns=["route", "direction", "order_source", "n_stops", "n_tickets", "order_consistency"])

    # index, level, confidence
    hi_mask = C["n_tickets"] >= CFG["CONF_HIGH"]
    ref = float(np.percentile(C.loc[hi_mask if hi_mask.sum() > 50 else slice(None), "raw"], 90))
    ref = ref if ref > 0 else 1.0
    log(f"REF (P90 of per-bus load over high-confidence cells) = {ref:.4f}")
    for D, extra in ((C, True), (RH, False)):
        D["load_index"] = (D["raw"] / ref).round(3)
        D["level"] = level_of(D["load_index"].to_numpy())
        D["confidence"] = conf_of(D["n_tickets"].to_numpy(), D["_quality_ok"].to_numpy())
    C["hour"] = C["hour"].astype(int)
    RH["hour"] = RH["hour"].astype(int)

    key = ["route", "direction", "day_type", "hour"]
    C = C.sort_values(key + ["stop_seq_from"]).reset_index(drop=True)
    RH = RH.sort_values(key).reset_index(drop=True)
    cols = ["route", "direction", "stop_seq_from", "stop_seq_to", "day_type", "hour", "load_index", "level",
            "confidence", "n_tickets", "stop_name_from", "stop_name_to", "flow_pax_per_hour", "buses_est",
            "bus_size", "order_source", "order_consistency"]
    Cout = C[cols].copy()
    Cout["flow_pax_per_hour"] = Cout["flow_pax_per_hour"].round(3)
    Cout["buses_est"] = Cout["buses_est"].round(2)
    contract = ["route", "direction", "stop_seq_from", "stop_seq_to", "day_type", "hour", "load_index", "level",
                "confidence", "n_tickets"]
    Cout[contract].to_csv(os.path.join(out, "crowding.csv"), index=False)          # slim, committed to git
    Cout.to_csv(os.path.join(out, "crowding_detail.csv.gz"), index=False, compression="gzip")  # extra columns incl. stop names
    RH[["route", "direction", "day_type", "hour", "load_index", "level", "confidence", "n_tickets"]].to_csv(
        os.path.join(out, "crowding_route_hour.csv"), index=False)
    pd.DataFrame(stop_rows, columns=["route", "direction", "stop_seq", "stop_name", "order_source"]).to_csv(
        os.path.join(out, "stop_order.csv"), index=False)
    RQ.to_csv(os.path.join(out, "route_order_quality.csv"), index=False)
    log(f"wrote crowding.csv ({len(Cout):,} rows), crowding_route_hour.csv ({len(RH):,} rows)")

    write_readme(out, ref, G is not None, args)
    stats = sanity(out, T, dt_row, ndays, C, RH, RQ, ref, UP, hol, args)
    write_note(out, stats, st, RQ, ndays, ref, len(Cout), len(RH), G is not None, args)
    log("wrote docs/technical_note.md (draft with real numbers)")
    log(f"DONE in {time.time() - t0:.0f}s. Outputs in {out}")
    with open(os.path.join(out, "pipeline_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG))


# ------------------------------- 4. DOCS + SANITY -------------------------------
def write_readme(out, ref, gtfs, args):
    c = CFG
    txt = f"""# crowding.csv data dictionary

Generated by `pipeline/run_pipeline.py` (code/design produced with Claude, an AI assistant by Anthropic; verified by the team).
Stop-order mode: **{'GTFS where names matched, tickets otherwise (see order_source)' if gtfs else 'NO GTFS: stop order rebuilt from tickets (order_source = tickets)'}**.
Smoothing across adjacent stretches: {'ON' if args.smooth else 'off'}.

## Columns (contract with planner)
| column | meaning |
|---|---|
| route | route number without direction, e.g. `8A`, `433` |
| direction | `UP` or `DOWN` (taken from the ticket route suffix) |
| stop_seq_from, stop_seq_to | one stretch = two consecutive stops in travel direction; seq starts at 1 |
| day_type | `weekday` or `weekend` (Sat, Sun and holidays {', '.join(c['HOLIDAYS'])}) |
| hour | hour of day 0-23 (IST) in which the ticket was sold |
| load_index | relative crowding score, see below (1.0 = as busy as the busiest 10% of well-observed cells) |
| level | `seats` / `standing` / `crowded` from load_index |
| confidence | `high` / `medium` / `low` from n_tickets |
| n_tickets | tickets (rows) in the whole month that crossed this stretch in this day_type+hour |
| (names) | stop names are NOT in crowding.csv (file size). Join `stop_order.csv` on route+direction+stop_seq, or use `crowding_detail.csv.gz` which has stop_name_from/stop_name_to and the columns below |
| flow_pax_per_hour (detail file) | avg ticketed passengers crossing the stretch per hour on a day of this type (before uplift) |
| buses_est (detail file) | estimated buses per hour (see below) |
| bus_size (detail file) | `12m` or `9m` (default `{c['DEFAULT_SIZE']}` when unknown) |
| order_source, order_consistency (detail file) | how stop order was obtained; share of tickets whose end stop is after the start stop |

**A missing row means: fewer than {c['MIN_TICKETS_OUT']} tickets, i.e. no usable data. Treat as unknown, not as empty bus.**
`crowding_route_hour.csv` has the same estimate per route/direction/day_type/hour (all stretches pooled) as a fallback.

## How load_index is computed
1. For every ticket, passengers are added to every stretch between its start and end stop, in the hour it was sold. Averaged over the days of that day_type -> `flow_pax_per_hour`.
2. Pass-holder uplift (ASSUMPTION): x{c['UPLIFT_PEAK']} in hours {c['PEAK_HOURS']}, x{c['UPLIFT_OFF']} otherwise. Ticket data cannot see pass holders.
3. Divide by `buses_est` = average distinct vehicle numbers seen per hour on that route-direction, divided by the route's share of tickets that carry a vehicle number (floor {c['MIN_BUSES']}). This is a frequency PROXY, not a timetable.
4. Divide by relative bus capacity (12 m = {c['CAP_12M']}, 9 m = {c['CAP_9M']}; ASSUMPTION).
5. Divide by REF = **{ref:.4f}**, the 90th percentile of step 4 over cells with n_tickets >= {c['CONF_HIGH']}.

## Levels (relative, not headcounts)
- load_index < {c['LEVEL_SEATS']} -> `seats`
- {c['LEVEL_SEATS']} <= load_index < {c['LEVEL_CROWDED']} -> `standing`
- load_index >= {c['LEVEL_CROWDED']} -> `crowded`

Ticket data covers only part of real ridership, so absolute passenger counts per bus cannot be recovered. Levels say "how busy compared with the busiest well-observed cells", and thresholds are conventions that should be calibrated with ground truth (e.g. counts from conductors) when available.

## Confidence
high: n_tickets >= {c['CONF_HIGH']}; medium: >= {c['CONF_MED']}; low: below. Also forced to `low` when the route's stop order is unreliable (order_consistency < {c['MIN_ORDER_CONSISTENCY']}). Never present `low` as fact. Note: low-confidence cells are biased towards `seats`, because few tickets produce a low load_index. `seats` + `low` means NO EVIDENCE, not an empty bus; the planner should show it as \"not enough data\".

## Known limitations
- Passengers assumed to alight at the ticket end stop; riders leaving earlier/later are not modelled (use `--smooth` to blur).
- Load is attributed to the hour of ticket sale, not the hour of passing each stretch.
- No bus trips can be rebuilt (tagged tickets per vehicle-day are about 7), so there is no per-trip load.
- Tickets without UP/DOWN in the route name (e.g. circular/special services such as OMS, YMS) are dropped. Variants like `522BDOWN2` are kept as route `522B-2`.
"""
    with open(os.path.join(out, "crowding_README.md"), "w", encoding="utf-8") as f:
        f.write(txt)


def sanity(out, T, dt_row, ndays, C, RH, RQ, ref, UP, hol, args):
    log("== Sanity checks ==")
    sd = os.path.join(out, "sanity")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        plt = None
        log("  (matplotlib not installed -> tables only; run: pip install matplotlib)")
    lines = []
    stats = {}

    def P(*a):
        s = " ".join(str(x) for x in a)
        lines.append(s)
        log(s)

    # 1. hourly profile
    hh = np.zeros((2, 24))
    np.add.at(hh, (dt_row, T["hr"].astype(int)), T["pax"].astype(float))
    hh = hh / ndays[:, None]
    hp = pd.DataFrame({"hour": range(24), "weekday_pax_per_day": hh[0].round(0), "weekend_pax_per_day": hh[1].round(0)})
    hp.to_csv(os.path.join(sd, "1_hourly_tickets.csv"), index=False)
    for nm, k in (("weekday", 0), ("weekend", 1)):
        am, pm = int(np.argmax(hh[k][5:13]) + 5), int(np.argmax(hh[k][15:22]) + 15)
        stats[nm + "_peaks"] = (am, pm)
        stats[nm + "_pax"] = int(hh[k].sum())
        P(f"[1] {nm}: morning peak hour {am}:00, evening peak hour {pm}:00 (ticketed passengers/day: {int(hh[k].sum()):,})")
    ci = C[C["confidence"] != "low"].groupby(["day_type", "hour"])["load_index"].mean().unstack(0).round(3)
    ci.to_csv(os.path.join(sd, "1_mean_load_index_by_hour.csv"))
    if plt:
        fig, ax = plt.subplots(1, 2, figsize=(11, 3.8))
        ax[0].plot(range(24), hh[0], label="weekday"); ax[0].plot(range(24), hh[1], label="weekend")
        ax[0].set_title("Ticketed passengers per day by hour"); ax[0].set_xlabel("hour"); ax[0].legend()
        for c_ in ci.columns:
            ax[1].plot(ci.index, ci[c_], label=c_)
        ax[1].set_title("Mean load_index by hour (medium+high confidence)"); ax[1].set_xlabel("hour"); ax[1].legend()
        plt.tight_layout(); plt.savefig(os.path.join(sd, "1_hourly_profile.png"), dpi=130); plt.close()

    # 2. busy corridors
    peak = C[(C["day_type"] == "weekday") & (C["hour"].isin([8, 9, 18, 19])) & (C["confidence"] == "high")]
    if len(peak):
        top = (peak.groupby(["route", "direction"]).agg(mean_load_index=("load_index", "mean"), cells=("load_index", "size"),
                                                          tickets=("n_tickets", "sum")).sort_values("mean_load_index", ascending=False)
               .round(3))
        top = top[top["cells"] >= 10].head(15)
        top.to_csv(os.path.join(sd, "2_busiest_routes_weekday_peak.csv"))
        P("[2] busiest route-directions, weekday peaks (high confidence):\n" + top.to_string())
        ts = peak.sort_values("load_index", ascending=False).head(15)[
            ["route", "direction", "hour", "stop_name_from", "stop_name_to", "load_index", "level", "n_tickets"]]
        ts.to_csv(os.path.join(sd, "2_busiest_stretches_weekday_peak.csv"), index=False)
        P("[2] busiest stretches:\n" + ts.to_string(index=False))
        if plt:
            fig, ax = plt.subplots(figsize=(7, 4.5))
            lab = [f"{r}{d}" for r, d in top.index][::-1]
            ax.barh(lab, top["mean_load_index"].to_numpy()[::-1]); ax.set_title("Busiest routes, weekday peak (mean load_index)")
            plt.tight_layout(); plt.savefig(os.path.join(sd, "2_busiest_routes.png"), dpi=130); plt.close()
    else:
        P("[2] no high-confidence peak cells (sample too small?)")

    # 3. weekday vs weekend
    wk = hh.sum(axis=1)
    stats["wk_ratio"] = float(wk[1] / wk[0])
    P(f"[3] ticketed passengers/day: weekday {int(wk[0]):,} vs weekend {int(wk[1]):,} (ratio weekend/weekday = {wk[1] / wk[0]:.2f})")
    sh = (C[C["confidence"] != "low"].groupby("day_type")["level"].value_counts(normalize=True).unstack().round(3))
    sh.to_csv(os.path.join(sd, "3_level_share_by_day_type.csv"))
    P("[3] share of medium/high-confidence cells per level:\n" + sh.to_string())

    # 4. split-half: tagged vs untagged tickets
    z = C[C["n_tickets"] >= 30]
    if len(z) > 100:
        r = z["_flow_tag"].rank().corr(z["_flow_untag"].rank())
        stats["r"], stats["r_n"] = float(r), int(len(z))
        P(f"[4] split-half check: rank correlation between flow from tickets WITH a vehicle number and flow from tickets WITHOUT one = {r:.3f} (n cells={len(z):,})")
        P("    High value => the vehicle-tagged half behaves like the rest, so route-level estimate is not an artefact of the NULL rows.")
    # 5. stop-order quality
    P("[5] stop-order quality: " + str(RQ["order_source"].value_counts().to_dict()) +
      f" | median consistency {RQ['order_consistency'].median():.3f} | routes below {CFG['MIN_ORDER_CONSISTENCY']}: "
      f"{int((RQ['order_consistency'] < CFG['MIN_ORDER_CONSISTENCY']).sum())} of {len(RQ)}")
    cc = C["confidence"].value_counts(normalize=True).round(3).to_dict()
    lv = C["level"].value_counts(normalize=True).round(3).to_dict()
    stats["cc"], stats["lv"] = cc, lv
    stats["share_mh"] = sh
    P(f"[5] output cells by confidence: {cc} | by level: {lv}")

    # 6. sensitivity
    base = C[C["confidence"] != "low"].copy()
    sens = []
    hrs = base["hour"].to_numpy()
    for nm, up_peak, up_off, capk in (("no pass uplift (1.0/1.0)", 1.0, 1.0, 1.0),
                                       ("base uplift (peak %.2f / off %.2f)" % (CFG["UPLIFT_PEAK"], CFG["UPLIFT_OFF"]), CFG["UPLIFT_PEAK"], CFG["UPLIFT_OFF"], 1.0),
                                       ("high uplift (1.6/1.25)", 1.6, 1.25, 1.0),
                                       ("base uplift, all buses 9 m", CFG["UPLIFT_PEAK"], CFG["UPLIFT_OFF"], CFG["CAP_12M"] / CFG["CAP_9M"])):
        up = np.array([up_peak if h in CFG["PEAK_HOURS"] else up_off for h in range(24)])[hrs]
        idx = base["raw"].to_numpy() / UP[hrs] * up * capk / ref
        lvl = pd.Series(level_of(idx)).value_counts(normalize=True)
        sens.append({"scenario": nm, **{k: round(float(lvl.get(k, 0)), 3) for k in ("seats", "standing", "crowded")}})
    S = pd.DataFrame(sens)
    stats["S"] = S
    S.to_csv(os.path.join(sd, "6_sensitivity.csv"), index=False)
    P("[6] sensitivity (share of medium/high-confidence cells per level, REF held fixed):\n" + S.to_string(index=False))
    with open(os.path.join(sd, "sanity_summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return stats


def write_note(out, stats, st, RQ, ndays, ref, ncells, nrh, gtfs, args):
    c = CFG
    docs = os.path.join(ROOT, "docs")
    os.makedirs(docs, exist_ok=True)
    S = stats["S"].to_string(index=False)
    sh = stats["share_mh"].round(3).to_string()
    r_line = (f"{stats['r']:.2f} over {stats['r_n']:,} stretch-hour cells with at least 30 tickets"
              if "r" in stats else "not computed (too few cells)")
    n_low = int((RQ["order_consistency"] < c["MIN_ORDER_CONSISTENCY"]).sum())
    txt = f"""# Technical note: crowdedness estimate (DRAFT generated from the pipeline run)

**AI disclosure:** the pipeline code and the method design were produced with Claude (Anthropic's AI assistant). The team ran the code, checked the outputs and is responsible for the content. Numbers below are written automatically by `pipeline/run_pipeline.py` from the actual run.

## 1. Data
Ticket data for Oct 2026 (ETM + e-ticketing): {st['rows_read']:,} rows read, {st['rows_kept']:,} used. {st['dropped_no_direction']:,} rows were dropped because the route name has no UP/DOWN direction (mostly circular or special services), and {st['dropped_other_bad']:,} for bad dates, amounts or identical start/end stops. One row is one ticket sale, not one trip. About half of the rows have no vehicle number, and tickets that do have one average only about 7 per vehicle-day, so individual bus trips cannot be reconstructed. The estimate is therefore a typical route-level estimate by route, direction, stretch, day type and hour.

Stop order: {'GTFS where stop names matched, otherwise rebuilt from tickets' if gtfs else 'GTFS was not available, so the stop order of every route was rebuilt from the tickets (order_source = tickets)'}. Ticket-based order is found by least squares on fare distance: for every ticket, position(end stop) - position(start stop) is fitted to its fare in 4.5 units. Order consistency (share of tickets whose end stop lies after the start stop) has a median of {RQ['order_consistency'].median():.3f}; {n_low} of {len(RQ)} route-directions are below {c['MIN_ORDER_CONSISTENCY']} and are forced to low confidence.

## 2. Method
1. Day type: weekday ({int(ndays[0])} days) or weekend/holiday ({int(ndays[1])} days; Saturdays, Sundays, and {', '.join(c['HOLIDAYS'])}).
2. Flow: each ticket adds its passengers to every stretch (two consecutive stops) between its start and end stop, in the hour of sale. Averaged per day of the same day type: `flow_pax_per_hour`.
3. Pass holders: multiplied by {c['UPLIFT_PEAK']} (hours {c['PEAK_HOURS']}) or {c['UPLIFT_OFF']} (other hours). This is an assumption, not measured.
4. Frequency: buses per hour are approximated by the number of distinct vehicle numbers seen on that route-direction in that hour, divided by the route's share of tickets that carry a vehicle number (minimum {c['MIN_BUSES']}). It is a proxy, not a timetable.
5. Bus size: relative capacity 12 m = {c['CAP_12M']}, 9 m = {c['CAP_9M']}. Size is not in the ticket data, so all routes use the default ({c['DEFAULT_SIZE']}) unless a route-size list is provided.
6. `load_index` = load per bus per capacity divided by {ref:.3f}, the 90th percentile over cells with at least {c['CONF_HIGH']} tickets. Level: below {c['LEVEL_SEATS']} seats, below {c['LEVEL_CROWDED']} standing, otherwise crowded. Levels are relative to the busiest well-observed cells, not headcounts, because ticket data covers only part of real ridership.
7. Confidence from tickets crossing the stretch in that day type and hour over the month: high at {c['CONF_HIGH']} or more, medium at {c['CONF_MED']} or more, low below. Cells with fewer than {c['MIN_TICKETS_OUT']} tickets are not output (no data).

Output: {ncells:,} stretch-level rows in `outputs/crowding.csv` and {nrh:,} route-hour rows in `outputs/crowding_route_hour.csv`.

## 3. Sanity checks (outputs/sanity/)
- Hourly profile: weekday morning peak at {stats['weekday_peaks'][0]}:00 and evening peak at {stats['weekday_peaks'][1]}:00; weekend peaks at {stats['weekend_peaks'][0]}:00 and {stats['weekend_peaks'][1]}:00. Ticketed passengers per day: {stats['weekday_pax']:,} on weekdays vs {stats['weekend_pax']:,} on weekends (ratio {stats['wk_ratio']:.2f}).
- Busy corridors: tables of the busiest route-directions and stretches at weekday peaks, restricted to high-confidence cells (`2_busiest_*.csv`).
- Split-half check: rank correlation of flow computed from tickets with a vehicle number versus tickets without one is {r_line}. This tests whether the estimate depends on which rows lack a vehicle number. It is not a measurement of true crowding; we have no ground-truth passenger counts.
- Share of medium/high-confidence cells per level:

```
{sh}
```

## 4. Sensitivity (share of medium/high-confidence cells per level, reference value held fixed)
```
{S}
```
Pass-holder uplift and bus size move many cells between levels, so levels should be read as relative, and the assumptions are visible in `crowding_README.md`.

## 5. Where estimates are least reliable
- Routes and hours with few tickets: {stats['cc'].get('low', 0):.0%} of output cells are low confidence and should not be shown as fact. Low-confidence cells are also biased towards `seats` (few tickets give a small load_index), so `seats` with low confidence means no evidence, not an empty bus; the planner shows these as \"not enough data\".
- Pass holders are unobserved; the uplift is an assumption.
- Riders alighting earlier or later than their ticket end stop are not modelled (optional smoothing exists but is off).
- Load is assigned to the hour of ticket sale, so long trips are attributed to their boarding hour.
- The bus count is a proxy from sparsely tagged vehicle numbers; routes with few tagged vehicles get a minimum of {c['MIN_BUSES']} bus per hour, which can overstate load per bus.
- Bus size is a default, not a measurement.
- Without GTFS, stop order and names come from ticket text and may contain duplicates or spelling variants; circular routes and branches are only approximated.
- Routes without an UP/DOWN suffix are excluded.
"""
    with open(os.path.join(docs, "technical_note.md"), "w", encoding="utf-8") as f:
        f.write(txt)


if __name__ == "__main__":
    main()
