# Crowding-aware Delhi bus journey planner

Transport Stack Hackathon 2026, Problem 2.

Most journey planners rank bus trips by time only. This one also shows **how crowded the bus is likely to be**
(seats likely / standing room likely / likely crowded, or "not enough data" where tickets are too few), so a commuter can choose between a faster trip and a more comfortable one.

- **Pipeline** (`pipeline/`): turns raw ticket records (ETM + e-ticketing) into `outputs/crowding.csv`.
- **Planner** (`planner/`): Streamlit app that reads the pipeline output, finds bus journeys, and shows time and crowding side by side.
- **Operator view** (second tab of the app): which route-hours have the highest relative load in this data.

## Quick start

Needs Python 3.10 to 3.13.

Windows:

```
run_planner.bat
```

Mac / Linux:

```
./run_planner.sh
```

This installs the packages, runs a self-test, and opens the app in your browser.
Manual version:

```
pip install -r requirements.txt
python planner/selftest.py
streamlit run planner/app.py
```

## Data modes (picked automatically)

| Mode | Needs | What you get |
|---|---|---|
| **LIVE** | `data/raw/gtfs/` + `outputs/crowding.csv` | scheduled times, map coloured by crowding |
| **TICKETS** | `outputs/crowding.csv` + `outputs/stop_order.csv` | no GTFS yet: stop order from tickets, rough ride times (about 2.5 min per stop, waiting not included), no map, a coloured crowding strip per stop instead |
| **DEMO** | nothing | fake timetable and fake crowding, clearly flagged in the app |

The first load of the 40 MB crowding file takes a few seconds. Use "Reload data files" in the sidebar after replacing files.
When GTFS arrives, put the files in `data/raw/gtfs/` (`stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`; `calendar.txt` optional),
run `python planner/check_match.py` to list route and stop names that do not match the pipeline's files, then restart the app.

## Folder layout

```
pipeline/             ticket records -> crowding estimates
planner/
  app.py              Streamlit app (planner tab + operator tab)
  journey.py          journey search (direct + 1 change) and crowding summary per option
  crowding.py         reads crowding.csv (+ route-hour fallback), answers "how crowded is this stretch at this hour?"
  gtfs_store.py       loads GTFS (or the ticket-derived stop order) into route patterns
  mapview.py          folium map and the no-map crowding strip
  check_match.py      lists route/stop names that do not match between GTFS and the pipeline files
  make_fake_data.py   generates the fake demo data
  selftest.py         checks the planner logic
  config.py           all settings
data/raw/gtfs/        real GTFS goes here
outputs/              pipeline output: crowding.csv, stop_order.csv, crowding_route_hour.csv, crowding_README.md
docs/                 technical note, demo video script
```

## Pipeline

```
python pipeline/run_pipeline.py            # full run on the October ticket data
python pipeline/run_pipeline.py --sample   # quick run on a small slice
```

Method, assumptions (pass holders, riders leaving earlier or later than their ticket says, bus size, low-ticket
routes) and where estimates are least reliable are in `docs/technical_note.md`. Column definitions and level thresholds are in `outputs/crowding_README.md`.

## What the planner reads

| file | use |
|---|---|
| `outputs/crowding.csv` | one row per stretch (two consecutive stops) x day type x hour: `route, direction, stop_seq_from, stop_seq_to, day_type, hour, load_index, level, confidence, n_tickets` |
| `outputs/stop_order.csv` | `route, direction, stop_seq, stop_name`: gives the stop order and names (ticket mode), or translates GTFS stop names to the pipeline's stop numbers (LIVE mode) |
| `outputs/crowding_route_hour.csv` | route-wide average per hour, used only when a stretch row is missing |

Optional columns `stop_name_from` and `stop_name_to` in `crowding.csv` are used if present.

## How the planner works

- **Places**: pick stops by name; stops sharing a name count as one place.
- **Journeys**: direct routes, plus journeys with one change (same stop name; in LIVE mode at most 400 m walk). Times come from the GTFS timetable when available; in ticket mode they are rough estimates and say so.
- **Crowding per ride**: each stretch is looked up by route, direction, day type and the hour the bus passes it (the departure hour, advanced along the ride). The planner shows the **overall level**, the **busiest stretch**, the **share of the ride at each level**, and a **confidence**.
- **Low confidence is never shown as a level.** Few tickets give a small load number, so a low-confidence "seats" is no evidence of an empty bus. Those stretches are shown as **not enough data**, in grey. Only medium and high confidence cells show seats / standing / crowded. Each journey says how many of its stretches have not enough data.
- **Missing data**: a missing stretch row also means "no usable data", never "empty bus". The route-wide average for that hour is tried first, but only if it is itself medium or high confidence (shown as medium at best). A journey is called "not enough data" if under half of it has data.
- **Levels are relative**: they compare with the busiest well-observed cells, based on one month of ticket data, not passenger counts. The app never shows headcounts.
- **Overall level**: time-weighted average over stretches with a level (seats = 0, standing = 1, crowded = 2): below 0.5 seats, below 1.5 standing, otherwise crowded.
- **Ranking**: "Fastest" sorts by arrival time. "Most comfortable" sorts by average crowding (+0.5 penalty if any stretch is crowded, +0.3 if unknown), then by time.

## Known limitations

- Ticket mode: no timetable, so ride times are rough and waiting is not modelled; no map. LIVE mode needs GTFS.
- Pass holders are not in ticket data. The pipeline applies an assumed uplift (x1.3 peak, x1.1 off-peak): an assumption, not a measurement.
- Riders may get off earlier or later than their ticket says. Bus size (9 m vs 12 m) is unknown, so all buses are treated as 12 m.
- Load is attributed to the hour of ticket sale, not the hour of passing each stretch.
- About half of all stretches rest on few tickets (low confidence). They are shown as "not enough data", so some journeys, especially at off-peak hours, show little or no level.
- Circular and special services without UP/DOWN in the route name (e.g. OMS, YMS) are not in the data.
- At most one change. No walking from or to arbitrary coordinates: origin and destination are stops.
- Crowding is a typical level for a day type and hour (about 4 weeks of data), not a live figure.
- Wording: this data shows "highest relative load", not "most crowded in Delhi".
- Levels are relative to the busiest well-observed cells, based on one month of ticket data. Low-confidence results are shown as "not enough data".

## Tests

```
python planner/selftest.py
```

## AI tools disclosure

This project was built with the help of **Claude (Anthropic)** as an AI assistant. Claude wrote most of the pipeline and planner
code, the technical note draft, the demo script and this README. The team reviewed, ran and is responsible for the
submitted work. No external data beyond the hackathon datasets is used. Open-source libraries: Streamlit, folium, pandas, numpy.

## Data and credits

Ticket data and static GTFS from the Delhi Transport Stack platform, provided for the Transport Stack Hackathon 2026.
