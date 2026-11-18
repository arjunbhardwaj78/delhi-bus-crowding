# pipeline/profile_data.py
# One-script profiler for the October Delhi ticket CSV. Chunked, low memory.
import os, sys, urllib.request
from collections import Counter
import pandas as pd
import zipfile

URL = "https://pub-091bd9e794bb4877b10b29e6df80f065.r2.dev/2026-10/October-Delhi.csv.zip"
PATH = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "data/raw/October-Delhi.csv.zip"
OUT = "outputs/profile_report.txt"
INVALID = {"", "null", "nan", "none", "na", "n/a", "0", "-"}

class Tee:
    def __init__(self, f): self.f = f
    def write(self, s): sys.__stdout__.write(s); self.f.write(s)
    def flush(self): sys.__stdout__.flush(); self.f.flush()

os.makedirs("outputs", exist_ok=True)
os.makedirs(os.path.dirname(PATH) or ".", exist_ok=True)
sys.stdout = Tee(open(OUT, "w", encoding="utf-8"))

if not os.path.exists(PATH):
    print(f"Downloading to {PATH} ...")
    urllib.request.urlretrieve(URL, PATH)

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", None)
pd.set_option("display.max_colwidth", 60)

n = 0
veh_blank = veh_textnull = veh_otherinv = veh_valid = 0
veh_other_vals = Counter()
routes_tot, routes_valid = Counter(), Counter()
stops, vehicles = set(), set()
day_rows, day_pax, day_valid = Counter(), Counter(), Counter()
hour_rows, pc_dist, tt_dist, ac_dist, dir_dist = Counter(), Counter(), Counter(), Counter(), Counter()
bad_dates = same_stop = amt_off = 0
dmin = dmax = None
veh_day = None
samples, samples_valid = None, None
cols_seen = None

_zf = zipfile.ZipFile(PATH)
_name = [x for x in _zf.namelist() if x.endswith(".csv") and not x.startswith("__MACOSX") and not os.path.basename(x).startswith("._")][0]
reader = pd.read_csv(_zf.open(_name), dtype=str, keep_default_na=False, na_values=[], chunksize=500_000)
for ch in reader:
    if cols_seen is None:
        cols_seen = list(ch.columns)
        samples = ch.head(5)
    n += len(ch)

    v = ch["vehicle_number"].str.strip()
    vl = v.str.lower()
    blank = vl == ""
    tnull = vl == "null"
    other = vl.isin(INVALID) & ~blank & ~tnull
    valid = ~vl.isin(INVALID)
    veh_blank += int(blank.sum()); veh_textnull += int(tnull.sum())
    veh_otherinv += int(other.sum()); veh_valid += int(valid.sum())
    veh_other_vals.update(v[other].tolist())
    vehicles.update(v[valid].unique().tolist())
    if samples_valid is None and valid.any():
        samples_valid = ch[valid].head(5)

    ts = pd.to_datetime(ch["created_at"], errors="coerce")
    bad_dates += int(ts.isna().sum())
    if ts.notna().any():
        a, b = ts.min(), ts.max()
        dmin = a if dmin is None or a < dmin else dmin
        dmax = b if dmax is None or b > dmax else dmax
    d = ts.dt.date
    pax = pd.to_numeric(ch["passenger_count"], errors="coerce").fillna(0)
    amt = pd.to_numeric(ch["amount"], errors="coerce")
    day_rows.update(d.value_counts().to_dict())
    for k, val in pax.groupby(d).sum().items(): day_pax[k] += val
    for k, val in valid.groupby(d).sum().items(): day_valid[k] += int(val)
    hour_rows.update(ts.dt.hour.value_counts().to_dict())
    pc_dist.update(pax.astype(int).value_counts().to_dict())
    tt_dist.update(ch["ticket_type_id"].value_counts().to_dict())
    ac_dist.update(ch["is_ac"].value_counts().to_dict())

    r = ch["route"].str.strip()
    routes_tot.update(r.value_counts().to_dict())
    routes_valid.update(r[valid].value_counts().to_dict())
    dir_dist.update(r.str.extract(r"(UP|DOWN)\s*$", expand=False).fillna("NO_SUFFIX").value_counts().to_dict())

    s1, s2 = ch["start_stop_name"].str.strip(), ch["end_stop_name"].str.strip()
    stops.update(s1.unique().tolist()); stops.update(s2.unique().tolist())
    same_stop += int((s1 == s2).sum())
    ratio = amt / 4.5
    amt_off += int(((ratio - ratio.round()).abs() > 0.01).sum())

    vd = pd.DataFrame({"v": v[valid], "d": d[valid]}).groupby(["v", "d"]).size()
    veh_day = vd if veh_day is None else veh_day.add(vd, fill_value=0)

def pct(x, tot=None): return f"{100.0 * x / (tot or n):.2f}%"

print("=" * 70); print("PROFILE:", PATH); print("=" * 70)
print("Columns:", cols_seen)
print(f"\nROWS: {n:,}")
print("\n--- vehicle_number ---")
print(f"real empty/NaN in file : {veh_blank:,} ({pct(veh_blank)})")
print(f"text 'NULL'            : {veh_textnull:,} ({pct(veh_textnull)})")
print(f"other invalid (nan/none/0/-): {veh_otherinv:,} ({pct(veh_otherinv)}) {dict(veh_other_vals.most_common(5))}")
print(f"VALID vehicle number   : {veh_valid:,} ({pct(veh_valid)})")
print(f"NULL SHARE (all invalid): {pct(n - veh_valid)}")
print(f"unique valid vehicles  : {len(vehicles):,}")
if veh_day is not None and len(veh_day):
    print("tickets per valid vehicle-day:", veh_day.describe().round(1).to_dict())

print("\n--- 5 sample rows (full) ---"); print(samples.to_string())
if samples_valid is not None:
    print("\n--- 5 sample rows WITH valid vehicle_number ---"); print(samples_valid.to_string())

print("\n--- date range (created_at) ---")
print("min:", dmin, "| max:", dmax, "| unparseable:", bad_dates)
print("\n--- uniques ---")
print(f"routes (with direction): {len(routes_tot):,} | stops (start+end names): {len(stops):,} | vehicles: {len(vehicles):,}")

print("\n--- tickets per day (rows | passengers | % rows w/ valid vehicle) ---")
for k in sorted(day_rows):
    wd = pd.Timestamp(k).day_name()[:3]
    print(f"{k} {wd}: {day_rows[k]:>8,} | {int(day_pax[k]):>8,} | {100.0*day_valid[k]/day_rows[k]:5.1f}%")

print("\n--- tickets by hour (rows) ---")
print({int(h): c for h, c in sorted(hour_rows.items()) if pd.notna(h)})
print("\n--- passenger_count dist (top 10) ---", dict(Counter(pc_dist).most_common(10)))
print("--- ticket_type_id dist ---", dict(tt_dist.most_common(15)))
print("--- is_ac dist ---", dict(ac_dist))
print("--- route direction suffix ---", dict(dir_dist))
print(f"--- rows where start_stop == end_stop: {same_stop:,} ({pct(same_stop)})")
print(f"--- rows where amount NOT a multiple of 4.5: {amt_off:,} ({pct(amt_off)})")

print("\n--- top 15 routes: rows | % with valid vehicle ---")
for r, c in routes_tot.most_common(15):
    print(f"{r:<12} {c:>8,} | {100.0*routes_valid.get(r,0)/c:5.1f}%")
low = sum(1 for r, c in routes_tot.items() if c < 500)
print(f"\nroutes with <500 tickets in the month: {low} of {len(routes_tot)}")
print(f"\nReport saved to {OUT}")