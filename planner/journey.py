"""
Journey search on static GTFS + crowding assessment.

Search model (kept simple on purpose):
  * Origin / destination are stops (all stops sharing a stop name count as one place).
  * Direct: any route pattern that visits origin, then destination.
  * One transfer: ride pattern 1 to a stop X, walk (<= MAX_TRANSFER_WALK_M, same stop name),
    ride pattern 2 to the destination.
  * Times come from the GTFS timetable (scheduled, not live).
"""
from collections import defaultdict
from dataclasses import dataclass, field

import config
from crowding import Seg
from gtfs_store import haversine_m


@dataclass
class Leg:
    pid: int
    trip: int
    i: int          # boarding position in pattern
    j: int          # alighting position in pattern
    board_t: float
    alight_t: float
    segments: list = field(default_factory=list)
    summary: object = None


@dataclass
class Option:
    legs: list
    depart_t: float
    walk_m: float = 0.0
    summary: object = None

    @property
    def arrive_t(self):
        return self.legs[-1].alight_t

    @property
    def total_s(self):
        return self.arrive_t - self.depart_t

    @property
    def wait_s(self):
        return self.legs[0].board_t - self.depart_t

    @property
    def n_transfers(self):
        return len(self.legs) - 1


@dataclass
class Summary:
    level: str
    score: float
    confidence: str
    low_share: float
    min_tickets: int
    worst_level: str
    worst_from: str
    worst_to: str
    worst_load: float
    fallback_used: bool
    shares: dict = field(default_factory=dict)   # share of ride time per level (incl. "unknown")
    routehour_share: float = 0.0                 # share of ride time that uses a route-wide average
    n_hops: int = 0                              # stretches on the ride
    n_nodata: int = 0                            # stretches with "not enough data"


# ---------------------------------------------------------------- search

def _make_leg(store, pid, i, j, ready_t, day_type):
    p = store.patterns[pid]
    r = p.first_trip(i, ready_t, day_type)
    if r is None:
        return None
    k, board = r
    if board - ready_t > config.MAX_WAIT_S:
        return None
    return Leg(pid=pid, trip=k, i=i, j=j, board_t=board, alight_t=p.arrival(k, board, i, j))


def find_options(store, origin, dest, depart_t, day_type, allow_transfer=True):
    """Return candidate options sorted by arrival time (not yet scored for crowding)."""
    if origin == dest or origin not in store.name_to_sids or dest not in store.name_to_sids:
        return []
    O = store.name_to_sids[origin]
    D = store.name_to_sids[dest]

    dpos = defaultdict(list)  # pattern id -> positions of destination stops
    for sid in D:
        for pid, j in store.stop_index.get(sid, []):
            dpos[pid].append(j)

    cands = {}

    def keep(key, opt):
        old = cands.get(key)
        if old is None or opt.arrive_t < old.arrive_t:
            cands[key] = opt

    # ---- direct
    for sid in O:
        for pid, i in store.stop_index.get(sid, []):
            js = [j for j in dpos.get(pid, []) if j > i]
            if not js:
                continue
            leg = _make_leg(store, pid, i, min(js), depart_t, day_type)
            if leg is not None:
                keep((store.patterns[pid].label,), Option([leg], depart_t))

    best_direct = min((o.arrive_t for o in cands.values()), default=None)

    # ---- one transfer
    if allow_transfer:
        dest_side = defaultdict(list)  # stop-name key -> [(pid2, boarding pos k, dest pos)]
        for pid2, js in dpos.items():
            p2 = store.patterns[pid2]
            for k in range(max(js)):
                after = [x for x in js if x > k]
                if after:
                    dest_side[store.stops[p2.stop_ids[k]]["key"]].append((pid2, k, min(after)))

        evals = 0
        for sid in O:
            for pid1, i in store.stop_index.get(sid, []):
                p1 = store.patterns[pid1]
                if any(j > i for j in dpos.get(pid1, [])):
                    continue  # this route already goes to the destination: no point changing
                r1 = p1.first_trip(i, depart_t, day_type)
                if r1 is None or r1[1] - depart_t > config.MAX_WAIT_S:
                    continue
                k1, board1 = r1
                for x in range(i + 1, len(p1.stop_ids)):
                    sx = p1.stop_ids[x]
                    hits = dest_side.get(store.stops[sx]["key"])
                    if not hits:
                        continue
                    t_arr = p1.arrival(k1, board1, i, x)
                    for pid2, kk, jj in hits:
                        evals += 1
                        if evals > 300000:
                            break
                        p2 = store.patterns[pid2]
                        if p2.base == p1.base:
                            continue
                        a, b = store.stops[sx], store.stops[p2.stop_ids[kk]]
                        if sx == p2.stop_ids[kk] or a["lat"] != a["lat"] or b["lat"] != b["lat"]:
                            walk_m = 0.0          # same stop, or no coordinates (ticket-only mode)
                        else:
                            walk_m = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
                        if walk_m > config.MAX_TRANSFER_WALK_M:
                            continue
                        ready = t_arr + walk_m / config.WALK_SPEED_MPS + config.MIN_TRANSFER_BUFFER_S
                        if not store.timetable:
                            ready += config.TICKET_MODE_TRANSFER_WAIT_S
                        key = (p1.label, p2.label)
                        old = cands.get(key)
                        leg2 = _make_leg(store, pid2, kk, jj, ready, day_type)
                        if leg2 is None or (old is not None and leg2.alight_t >= old.arrive_t):
                            continue
                        leg1 = Leg(pid=pid1, trip=k1, i=i, j=x, board_t=board1, alight_t=t_arr)
                        cands[key] = Option([leg1, leg2], depart_t, walk_m=walk_m)

    out = list(cands.values())
    if best_direct is not None:
        limit = depart_t + (best_direct - depart_t) * config.TRANSFER_SLOWER_LIMIT
        out = [o for o in out if o.n_transfers == 0 or o.arrive_t <= limit]
    out.sort(key=lambda o: o.arrive_t)
    return out


# ---------------------------------------------------------------- crowding

def _seg_numbers(p, t):
    if config.STOP_SEQ_MODE == "position":
        return t + 1, t + 2
    return int(p.seqs[t]), int(p.seqs[t + 1])


def summarise(store, segs_with_names):
    """segs_with_names: list of (Seg, from_stop_name, to_stop_name)."""
    total = sum(s.dur for s, _, _ in segs_with_names) or 1.0
    shares = {k: 0.0 for k in ("seats", "standing", "crowded", "unknown")}
    for s, _, _ in segs_with_names:
        shares[s.level if s.level in config.LEVEL_SCORE else "unknown"] += s.dur / total
    known_t = sum(s.dur for s, _, _ in segs_with_names if s.level in config.LEVEL_SCORE)
    rh_share = sum(s.dur for s, _, _ in segs_with_names if s.fallback) / total
    n_hops = len(segs_with_names)
    n_nodata = sum(1 for s, _, _ in segs_with_names if s.level not in config.LEVEL_SCORE)
    if known_t <= 0:
        return Summary("unknown", 1.0, "low", 1.0, 0, "unknown", "", "", float("nan"), False,
                       shares=shares, routehour_share=0.0, n_hops=n_hops, n_nodata=n_nodata)

    # average only over stretches that have data; if under half the ride has data, say "unknown"
    score = sum(s.dur * config.LEVEL_SCORE[s.level] for s, _, _ in segs_with_names
                if s.level in config.LEVEL_SCORE) / known_t
    if known_t / total < 0.5:
        level = "unknown"
    else:
        level = "seats" if score < 0.5 else ("standing" if score < 1.5 else "crowded")

    # worst stretch = highest level, ties by load_index, extended over neighbouring hops of the same level
    def rank(idx):
        s = segs_with_names[idx][0]
        if s.level not in config.LEVEL_SCORE:
            return (-1, 0.0)
        li = s.load_index if s.load_index == s.load_index else 0.0
        return (config.LEVEL_SCORE[s.level], li)

    w = max(range(len(segs_with_names)), key=rank)
    wl = segs_with_names[w][0].level
    a = b = w
    while a > 0 and segs_with_names[a - 1][0].level == wl:
        a -= 1
    while b < len(segs_with_names) - 1 and segs_with_names[b + 1][0].level == wl:
        b += 1

    low_share = sum(s.dur for s, _, _ in segs_with_names if s.confidence == "low" or s.level == "unknown") / total
    any_medium = any(s.confidence == "medium" for s, _, _ in segs_with_names)
    conf = "low" if low_share >= 0.34 else ("medium" if (low_share > 0 or any_medium) else "high")
    known = [s for s, _, _ in segs_with_names if s.level in config.LEVEL_SCORE]
    return Summary(
        level=level, score=score, confidence=conf, low_share=low_share,
        min_tickets=min(s.n_tickets for s in known), worst_level=wl,
        worst_from=segs_with_names[a][1], worst_to=segs_with_names[b][2],
        worst_load=segs_with_names[w][0].load_index,
        fallback_used=rh_share > 0 or any(s.source == "nearby-hour" for s, _, _ in segs_with_names),
        shares=shares, routehour_share=rh_share, n_hops=n_hops, n_nodata=n_nodata,
    )


def assess(store, crowd, opt, day_type):
    allsegs = []
    for leg in opt.legs:
        p = store.patterns[leg.pid]
        dep, arr = p.dep[leg.trip], p.arr[leg.trip]
        # timetable: dep is absolute clock time. ticket-only mode: dep is time since the first stop.
        base_t = 0.0 if store.timetable else leg.board_t - float(dep[leg.i])
        rows = []
        for t in range(leg.i, leg.j):
            a, b = _seg_numbers(p, t)
            hour = int((base_t + float(dep[t])) // 3600) % 24
            n0 = store.stops[p.stop_ids[t]]["name"]
            n1 = store.stops[p.stop_ids[t + 1]]["name"]
            s = crowd.segment(p.base, p.direction, day_type, hour, a, b, n0, n1)
            s.dur = max(float(arr[t + 1] - dep[t]), 1.0)
            s.i, s.j = t, t + 1
            rows.append((s, n0, n1))
        leg.segments = [r[0] for r in rows]
        leg.summary = summarise(store, rows)
        allsegs.extend(rows)
    opt.summary = summarise(store, allsegs)
    return opt


def comfort_key(opt):
    s = opt.summary
    penalty = (0.5 if s.worst_level == "crowded" else 0.0) + (0.3 if s.level == "unknown" else 0.0)
    return (s.score + penalty, opt.total_s)


def plan(store, crowd, origin, dest, depart_t, day_type, allow_transfer=True, mode="fastest"):
    """Return up to MAX_SHOWN options, scored for crowding and ranked by `mode`."""
    cands = find_options(store, origin, dest, depart_t, day_type, allow_transfer)[: config.MAX_CANDIDATES]
    for o in cands:
        assess(store, crowd, o, day_type)
    if mode == "comfortable":
        cands.sort(key=comfort_key)
    else:
        cands.sort(key=lambda o: (o.arrive_t, o.summary.score))
    out, n_tr = [], 0
    for o in cands:
        if o.n_transfers > 0:
            if n_tr >= config.MAX_TRANSFER_SHOWN:
                continue
            n_tr += 1
        out.append(o)
        if len(out) >= config.MAX_SHOWN:
            break
    return out
