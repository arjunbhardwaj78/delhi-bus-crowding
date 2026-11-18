# Technical note: crowdedness estimate (DRAFT generated from the pipeline run)

**AI disclosure:** the pipeline code and the method design were produced with Claude (Anthropic's AI assistant). The team ran the code, checked the outputs and is responsible for the content. Numbers below are written automatically by `pipeline/run_pipeline.py` from the actual run.

## 1. Data
Ticket data for Oct 2026 (ETM + e-ticketing): 2,559,899 rows read, 2,535,335 used. 24,466 rows were dropped because the route name has no UP/DOWN direction (mostly circular or special services), and 98 for bad dates, amounts or identical start/end stops. One row is one ticket sale, not one trip. About half of the rows have no vehicle number, and tickets that do have one average only about 7 per vehicle-day, so individual bus trips cannot be reconstructed. The estimate is therefore a typical route-level estimate by route, direction, stretch, day type and hour.

Stop order: GTFS was not available, so the stop order of every route was rebuilt from the tickets (order_source = tickets). Ticket-based order is found by least squares on fare distance: for every ticket, position(end stop) - position(start stop) is fitted to its fare in 4.5 units. Order consistency (share of tickets whose end stop lies after the start stop) has a median of 1.000; 5 of 2244 route-directions are below 0.85 and are forced to low confidence.

## 2. Method
1. Day type: weekday (20 days) or weekend/holiday (11 days; Saturdays, Sundays, and 2026-10-02, 2026-10-20).
2. Flow: each ticket adds its passengers to every stretch (two consecutive stops) between its start and end stop, in the hour of sale. Averaged per day of the same day type: `flow_pax_per_hour`.
3. Pass holders: multiplied by 1.3 (hours [7, 8, 9, 10, 17, 18, 19, 20]) or 1.1 (other hours). This is an assumption, not measured.
4. Frequency: buses per hour are approximated by the number of distinct vehicle numbers seen on that route-direction in that hour, divided by the route's share of tickets that carry a vehicle number (minimum 1.0). It is a proxy, not a timetable.
5. Bus size: relative capacity 12 m = 1.0, 9 m = 0.6. Size is not in the ticket data, so all routes use the default (12m) unless a route-size list is provided.
6. `load_index` = load per bus per capacity divided by 1.999, the 90th percentile over cells with at least 60 tickets. Level: below 0.4 seats, below 0.75 standing, otherwise crowded. Levels are relative to the busiest well-observed cells, not headcounts, because ticket data covers only part of real ridership.
7. Confidence from tickets crossing the stretch in that day type and hour over the month: high at 60 or more, medium at 20 or more, low below. Cells with fewer than 5 tickets are not output (no data).

Output: 871,321 stretch-level rows in `outputs/crowding.csv` and 35,372 route-hour rows in `outputs/crowding_route_hour.csv`.

## 3. Sanity checks (outputs/sanity/)
- Hourly profile: weekday morning peak at 9:00 and evening peak at 18:00; weekend peaks at 9:00 and 18:00. Ticketed passengers per day: 101,306 on weekdays vs 63,756 on weekends (ratio 0.63).
- Busy corridors: tables of the busiest route-directions and stretches at weekday peaks, restricted to high-confidence cells (`2_busiest_*.csv`).
- Split-half check: rank correlation of flow computed from tickets with a vehicle number versus tickets without one is 0.58 over 268,962 stretch-hour cells with at least 30 tickets. This tests whether the estimate depends on which rows lack a vehicle number. It is not a measurement of true crowding; we have no ground-truth passenger counts.
- Share of medium/high-confidence cells per level:

```
level     crowded  seats  standing
day_type                          
weekday     0.142  0.488     0.370
weekend     0.114  0.488     0.399
```

## 4. Sensitivity (share of medium/high-confidence cells per level, reference value held fixed)
```
                          scenario  seats  standing  crowded
          no pass uplift (1.0/1.0)  0.628     0.310    0.062
base uplift (peak 1.30 / off 1.10)  0.489     0.378    0.133
            high uplift (1.6/1.25)  0.375     0.398    0.227
        base uplift, all buses 9 m  0.199     0.369    0.432
```
Pass-holder uplift and bus size move many cells between levels, so levels should be read as relative, and the assumptions are visible in `crowding_README.md`.

## 5. Where estimates are least reliable
- Routes and hours with few tickets: 56% of output cells are low confidence and should not be shown as fact.
- Pass holders are unobserved; the uplift is an assumption.
- Riders alighting earlier or later than their ticket end stop are not modelled (optional smoothing exists but is off).
- Load is assigned to the hour of ticket sale, so long trips are attributed to their boarding hour.
- The bus count is a proxy from sparsely tagged vehicle numbers; routes with few tagged vehicles get a minimum of 1.0 bus per hour, which can overstate load per bus.
- Bus size is a default, not a measurement.
- Without GTFS, stop order and names come from ticket text and may contain duplicates or spelling variants; circular routes and branches are only approximated.
- Routes without an UP/DOWN suffix are excluded.
