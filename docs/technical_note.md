# Technical note: crowdedness estimate for Delhi buses

**AI disclosure:** the pipeline code and the method design were produced with Claude (Anthropic's AI assistant). The team ran the code, checked the outputs and is responsible for the content. All numbers below come from the pipeline run on the October 2026 ticket data (`pipeline/run_pipeline.py`; log in `outputs/pipeline_log.txt`).

## 1. What this estimates
For a route, direction, stretch (two consecutive stops), day type (weekday or weekend/holiday) and hour, the pipeline gives a relative load score (`load_index`), a level (`seats`, `standing`, `crowded`) and a confidence (`high`, `medium`, `low`). It is a typical-day estimate built from one month of tickets. It does not describe a single bus trip.

## 2. Data
Ticket data for October 2026 (ETM and e-ticketing): 2,559,899 rows read, 2,535,335 used. 24,466 rows were dropped because the route name has no UP/DOWN direction (circular or special services such as OMS and YMS), and 98 for bad dates, amounts or identical start and end stops. One row is one ticket sale, not one trip.

About half of the rows have no vehicle number, and tickets that do have one average only about 7 per vehicle-day. Individual bus trips therefore cannot be rebuilt, so the estimate is made at route level and averaged over days of the same type (20 weekdays, 11 weekend/holiday days; holidays 2 and 20 October).

**Stop order.** GTFS was not available, so the stop order of every route (2,244 route-directions) was rebuilt from the tickets and is marked `order_source = tickets`. The method fits, for every ticket, position(end stop) minus position(start stop) to its fare in 4.5-unit steps, by least squares. The share of tickets whose end stop lies after the start stop has a median of 1.000, and 5 of 2,244 route-directions are below 0.85 and are forced to low confidence. This check uses the same tickets that built the order, so it is an internal consistency check, not independent proof that the order matches the real route. If GTFS arrives, the pipeline uses it for routes where stop names match and falls back to tickets otherwise.

## 3. Method
1. Each ticket adds its passengers to every stretch between its start and end stop, in the hour of sale. This is averaged per day of the same day type (`flow_pax_per_hour`).
2. **Pass holders** are not in ticket data. Load is multiplied by 1.30 in hours 7-10 and 17-20 and by 1.10 in other hours. This is an assumption, not a measurement.
3. **Frequency** is approximated by the number of distinct vehicle numbers seen on that route-direction in that hour, divided by the route's share of tickets that carry a vehicle number (minimum 1 bus). It is a proxy, not a timetable.
4. **Bus size** is not in the ticket data. Relative capacity is 1.0 for a 12 m bus and 0.6 for a 9 m bus (assumed). All routes use 12 m unless a route-size list is supplied (`data/raw/route_bus_size.csv`).
5. `load_index` = load per bus per capacity, divided by 1.999, the 90th percentile of that value over cells with at least 60 tickets. A value of 1.0 means "as busy as the busiest tenth of well-observed cells".
6. **Level:** `load_index` below 0.40 is `seats`, below 0.75 is `standing`, otherwise `crowded`. These thresholds are our conventions. Ticket data covers only part of real ridership, so levels are relative, not passenger counts, and they have not been calibrated against observed occupancy.
7. **Confidence** uses the number of tickets crossing the stretch in that day type and hour over the month: `high` at 60 or more, `medium` at 20 or more, `low` below. Cells with fewer than 5 tickets are not output (no data). Confidence is also set to `low` where the stop order is unreliable.

Output: 871,321 stretch-level rows (`outputs/crowding.csv`) and 35,372 route-hour rows (`outputs/crowding_route_hour.csv`), with definitions in `outputs/crowding_README.md`.

## 4. How the brief's four issues are handled
| Issue | Handling | Limit |
|---|---|---|
| (a) Pass holders missing from tickets | Documented uplift by hour (1.30 peak, 1.10 off-peak); sensitivity shown in section 6 | Uplift is assumed, not measured |
| (b) Riders alighting earlier or later than ticketed | Ticket end stop is taken as the alighting stop; an optional smoothing across neighbouring stretches exists (`--smooth`) but is off in the submitted outputs | Early or late alighting is not modelled |
| (c) Bus size (12 m vs 9 m) | Relative capacity in the load formula; route-size list supported; sensitivity shown | Size is unknown in the data, so 12 m is assumed for all routes |
| (d) Few tickets | Confidence thresholds; cells under 5 tickets omitted; the planner shows `low` as "not enough data" | 56.5% of output cells are low confidence |

## 5. Worked example
Route 944 DOWN, Shivaji Park to Moti Nagar Industrial Area, weekday (6 stretches). At 09:00 all 6 stretches have high confidence and 5 are `crowded`, 1 `standing`. At 18:00 all 6 have medium confidence, 3 are `seats` and 3 `standing`. Route 962 DOWN on the same journey at 18:00 has only low-confidence cells, which the planner reports as "not enough data on 7 of 7 stretches" instead of showing a level.

## 6. Sanity checks (`outputs/sanity/`)
- **Peaks:** the busiest hour for tickets is 9:00 in the morning and 18:00 in the evening, on weekdays and on weekends. Ticketed passengers per day are 101,306 on weekdays and 63,756 on weekends (ratio 0.63).
- **Busy corridors:** tables of the busiest route-directions and stretches at weekday peaks, limited to high-confidence cells with at least 10 cells (`2_busiest_*.csv`). These show highest relative load in this data, not the most crowded routes in Delhi.
- **Split-half check:** the rank correlation between flow from tickets with a vehicle number and flow from tickets without one is 0.58 over 268,962 stretch-hour cells with at least 30 tickets. This shows moderate agreement between the two halves, so the estimate is not driven only by the rows that lack a vehicle number. It is not a measurement of true crowding; no ground-truth passenger counts were available.
- **Level shares** among medium and high confidence cells:

| Day type | seats | standing | crowded |
|---|---|---|---|
| weekday | 48.8% | 37.0% | 14.2% |
| weekend | 48.8% | 39.9% | 11.4% |

**Sensitivity** (share of medium and high confidence cells per level, reference value held fixed):

| Scenario | seats | standing | crowded |
|---|---|---|---|
| no pass uplift (1.0 / 1.0) | 62.8% | 31.0% | 6.2% |
| base uplift (1.30 peak / 1.10 off-peak) | 48.9% | 37.8% | 13.3% |
| high uplift (1.6 / 1.25) | 37.5% | 39.8% | 22.7% |
| base uplift, all buses 9 m | 19.9% | 36.9% | 43.2% |

The assumptions about pass holders and bus size move many cells between levels, so levels should be read as relative and as depending on these assumptions.

## 7. Where estimates are least reliable
- **Few tickets:** 56.5% of output cells are low confidence (medium 28.7%, high 14.8%). Low-confidence cells are also biased towards `seats`, because few tickets give a small `load_index`. A low-confidence `seats` means no evidence, not an empty bus.
- **Pass holders** are unobserved and the uplift is an assumption.
- **Alighting:** riders leaving earlier or later than their ticket end stop are not modelled.
- **Timing:** load is assigned to the hour of ticket sale, so long trips are attributed to their boarding hour.
- **Bus count:** the proxy comes from sparsely tagged vehicle numbers. Routes with few tagged vehicles get a minimum of 1 bus per hour, which can overstate the load per bus and inflate some routes' ranking.
- **Bus size** is a default, not a measurement.
- **Stop order and names** come from ticket text and may include duplicates, spelling variants, branches or circular patterns that are only approximated.
- **Coverage:** routes without an UP/DOWN suffix are excluded, and one month of data gives one typical day per day type.
- **Calibration:** levels and thresholds should be calibrated with observed occupancy (for example conductor counts) before operational use.
