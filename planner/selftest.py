"""
Quick check that the planner core works. No Streamlit needed.

    python planner/selftest.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import journey  # noqa: E402
import make_fake_data  # noqa: E402
from crowding import Crowding  # noqa: E402
from gtfs_store import fmt_clock, load_store  # noqa: E402


def show(opts):
    for k, o in enumerate(opts, 1):
        s = o.summary
        legs = " + ".join(f"{load_label(o, l)}" for l in o.legs)
        print(f"  #{k} {legs} | {round(o.total_s / 60)} min (wait {round(o.wait_s / 60)}) | "
              f"{s.level} conf={s.confidence} worst={s.worst_level} {s.worst_from}->{s.worst_to}")


def load_label(o, leg):
    return STORE.patterns[leg.pid].label


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


if __name__ == "__main__":
    make_fake_data.ensure_sample(force=True)
    STORE = load_store(config.SAMPLE_GTFS_DIR)
    CROWD = Crowding.from_csv(config.SAMPLE_CROWDING)
    print(f"stops={len(STORE.stop_names)} patterns={len(STORE.patterns)} trips={STORE.n_trips} crowding rows={CROWD.n_rows}")

    # 1. direct trip, weekday evening peak
    opts = journey.plan(STORE, CROWD, "Anand Vihar ISBT", "Connaught Place", 18 * 3600, "weekday")
    print("Anand Vihar -> Connaught Place, weekday 18:00")
    show(opts)
    check(len(opts) >= 1, "direct option found")
    check(opts[0].legs[0].board_t >= 18 * 3600, "boards after requested time")

    # 2. peak vs off-peak crowding
    peak = journey.plan(STORE, CROWD, "Anand Vihar ISBT", "Connaught Place", 9 * 3600, "weekday")[0]
    late = journey.plan(STORE, CROWD, "Anand Vihar ISBT", "Connaught Place", 22 * 3600, "weekday")[0]
    check(peak.summary.score >= late.summary.score, "9am at least as crowded as 10pm")
    check(peak.total_s >= late.total_s, "9am trip slower than 10pm trip (timetable has peak slowdowns)")

    # 3. weekend lighter than weekday
    wk = journey.plan(STORE, CROWD, "Anand Vihar ISBT", "Connaught Place", 18 * 3600, "weekday")[0]
    we = journey.plan(STORE, CROWD, "Anand Vihar ISBT", "Connaught Place", 18 * 3600, "weekend")[0]
    check(we.summary.score <= wk.summary.score, "weekend no more crowded than weekday")

    # 4. transfer journey (Kashmere Gate -> Nehru Place)
    opts = journey.plan(STORE, CROWD, "Kashmere Gate ISBT", "Nehru Place", 8 * 3600 + 30 * 60, "weekday")
    print("Kashmere Gate -> Nehru Place, weekday 08:30")
    show(opts)
    check(any(o.n_transfers == 1 for o in opts), "one-transfer option found")
    check(any(o.n_transfers == 0 for o in opts), "direct option found")

    # 5. ranking modes
    f = journey.plan(STORE, CROWD, "Kashmere Gate ISBT", "Nehru Place", 18 * 3600, "weekday", mode="fastest")
    c = journey.plan(STORE, CROWD, "Kashmere Gate ISBT", "Nehru Place", 18 * 3600, "weekday", mode="comfortable")
    check(all(f[i].arrive_t <= f[i + 1].arrive_t for i in range(len(f) - 1)), "fastest sorted by arrival")
    check(all(journey.comfort_key(c[i]) <= journey.comfort_key(c[i + 1]) for i in range(len(c) - 1)),
          "comfortable sorted by comfort")

    # 6. hour with no crowding rows -> unknown / low confidence, no crash
    night = journey.plan(STORE, CROWD, "Anand Vihar ISBT", "Connaught Place", 2 * 3600, "weekday")
    print("night 02:00 ->", [fmt_clock(o.legs[0].board_t) for o in night[:1]], [o.summary.level for o in night[:1]])
    check(True, "night query did not crash")

    # 7. low-ticket route flagged unreliable
    low = journey.plan(STORE, CROWD, "Kashmere Gate ISBT", "Nehru Place", 12 * 3600, "weekday", allow_transfer=False)
    r764 = [o for o in low if STORE.patterns[o.legs[0].pid].base == "764"]
    check(bool(r764) and any(sg.level == "unknown" for lg in r764[0].legs for sg in lg.segments),
          "route 764 (few tickets): low-confidence stretches show 'not enough data', not a level")
    check(r764[0].summary.n_nodata > 0, "journey summary counts stretches without data")

    # 8. empty crowding file -> still plans, everything unknown
    empty = journey.plan(STORE, Crowding(None), "Anand Vihar ISBT", "Connaught Place", 18 * 3600, "weekday")
    check(len(empty) >= 1 and empty[0].summary.level == "unknown", "works with no crowding data (shows 'no data')")

    # 9. operator table
    t = CROWD.overloaded_table("weekday")
    check(len(t) > 0, "operator table builds")
    # 10. optional stop names: correct names + WRONG stop numbers still match; no names + wrong numbers -> no data
    import pandas as pd
    df = pd.read_csv(config.SAMPLE_CROWDING)
    df["stop_seq_from"] += 100
    df["stop_seq_to"] += 100
    with_names = journey.plan(STORE, Crowding(df), "Anand Vihar ISBT", "Connaught Place", 18 * 3600, "weekday")
    without = journey.plan(STORE, Crowding(df.drop(columns=["stop_name_from", "stop_name_to"])),
                           "Anand Vihar ISBT", "Connaught Place", 18 * 3600, "weekday")
    check(with_names[0].summary.level != "unknown", "stop names used when stop numbers do not line up")
    check(without[0].summary.level == "unknown", "wrong stop numbers and no names -> 'no data' (not a wrong answer)")
    # 11. ticket-only mode (no GTFS): stop order from a stop_order.csv, no timetable
    import tempfile
    from gtfs_store import load_ticket_store
    rows = ["route,direction,stop_seq,stop_name,order_source"]
    for r, stops in make_fake_data.ROUTES.items():
        for d, seq in (("UP", stops), ("DOWN", stops[::-1])):
            rows += [f"{r},{d},{k + 1},{make_fake_data.STOPS[sid][0]},tickets" for k, sid in enumerate(seq)]
    tmp = Path(tempfile.mkdtemp()) / "stop_order.csv"
    tmp.write_text("\n".join(rows) + "\n")
    TS = load_ticket_store(tmp)
    check(not TS.timetable and not TS.has_coords, "ticket store has no timetable and no coordinates")
    o = journey.plan(TS, CROWD, "Anand Vihar ISBT", "Connaught Place", 18 * 3600, "weekday")
    check(len(o) >= 2, "ticket mode finds options")
    check(abs(sum(o[0].summary.shares.values()) - 1.0) < 1e-6, "level shares add up to 100%")
    o = journey.plan(TS, CROWD, "Red Fort", "Nehru Place", 8 * 3600, "weekday")   # no direct bus: needs a change
    check(any(x.n_transfers == 1 for x in o), "ticket mode finds a one-change trip (no coordinates needed)")

    # 12. missing stretch row -> route-wide average (low confidence); no row at all -> unknown, never 'seats'
    df = pd.read_csv(config.SAMPLE_CROWDING)
    rh = df.groupby(["route", "direction", "day_type", "hour"], as_index=False).agg(
        load_index=("load_index", "mean"), n_tickets=("n_tickets", "sum"))
    rh["level"] = "crowded"
    rh["confidence"] = "high"
    rh["route"] = rh["route"].str.replace("UP", "").str.replace("DOWN", "")
    no_stretch = df[df["route"] != "8UP"]
    c_rh = Crowding(no_stretch, route_hour=rh)
    seg = c_rh.segment("8", "UP", "weekday", 18, 1, 2)
    check(seg.level == "crowded" and seg.source == "route-hour" and seg.confidence == "medium",
          "missing stretch -> route-hour fallback (capped at medium confidence)")
    rh_low = rh.copy()
    rh_low["confidence"] = "low"
    check(Crowding(no_stretch, route_hour=rh_low).segment("8", "UP", "weekday", 18, 1, 2).level == "unknown",
          "low-confidence route-hour fallback -> not enough data")
    lowdf = pd.DataFrame([["8UP", "UP", 1, 2, "weekday", 18, 0.1, "seats", "low", 5]], columns=[
        "route", "direction", "stop_seq_from", "stop_seq_to", "day_type", "hour", "load_index", "level", "confidence", "n_tickets"])
    seg = Crowding(lowdf).segment("8", "UP", "weekday", 18, 1, 2)
    check(seg.level == "unknown" and seg.source == "lowdata", "low confidence 'seats' is shown as not enough data, never seats")
    seg = Crowding(no_stretch).segment("8", "UP", "weekday", 18, 1, 2)
    check(seg.level == "unknown", "missing stretch and no fallback -> unknown")

    # 13. real files, if present (outputs/crowding.csv + outputs/stop_order.csv)
    if config.REAL_CROWDING.exists() and config.REAL_STOP_ORDER.exists():
        RS = load_ticket_store(config.REAL_STOP_ORDER)
        RC = Crowding.from_csv(config.REAL_CROWDING, config.REAL_ROUTE_HOUR if config.REAL_ROUTE_HOUR.exists() else None)
        p = max(RS.patterns, key=lambda q: len(q.stop_ids))
        a, b = RS.stops[p.stop_ids[2]]["name"], RS.stops[p.stop_ids[12]]["name"]
        o = journey.plan(RS, RC, a, b, 9 * 3600, "weekday")
        check(len(o) >= 1, f"real data: options found for {a} -> {b}")
        print(f"  real data: {len(RS.patterns)} patterns, {RC.n_rows} crowding rows; first option {o[0].summary.level} ({o[0].summary.confidence})")
    print("\nAll checks passed.")
