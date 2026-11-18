# Demo video script (target 2:30 to 3:00)

Record the screen at 1080p with your voice over it. Use the REAL data, not demo mode (no yellow "DEMO MODE" banner). Without GTFS the blue ticket-mode note is expected; say so in the video.
Open the app before recording and run each search once so everything is cached and fast.
Put your real numbers where you see `[...]`.

| Time | On screen | Say |
|---|---|---|
| 0:00 - 0:20 | Title slide or the app's front page | "In Delhi, a packed bus is slow to board, hard to stand in, and people skip it. But journey planners rank trips by time only. We built a planner that shows how crowded each bus usually is, so you can choose between faster and more comfortable." |
| 0:20 - 0:50 | Terminal or a simple diagram: tickets → pipeline → `crowding.csv` → planner | "We took about [2.56 million] bus tickets from October. Each ticket says where and when someone boarded and where they planned to get off. Our pipeline turns them into an estimate of people on board for every stretch of every route, by day type and hour. It then labels each stretch: seats likely, standing room or crowded, with a confidence based on how many tickets sit behind it." |
| 0:50 - 1:10 | App: select **From: Shivaji Park**, **To: Moti Nagar Industrial Area**, weekday, **09:00** (checked on the real data) | "Here is a weekday 9 am trip from Shivaji Park to Moti Nagar." |
| 1:10 - 1:40 | The option cards. Point at the badge, the share-of-ride bar and the "Busiest stretch" line on each | "Route 991 is the quickest, about [10] minutes, and is likely to have standing room. Route 944 takes [15] minutes and is likely crowded. Route 962 takes [18] minutes and there are likely seats. Each card shows the overall level, the busiest stretch, the share of the ride at each level, and how much we trust the number. Levels are relative to the busiest well-observed cells, based on one month of ticket data." |
| 1:40 - 2:00 | Click **Most comfortable trips first**; the order changes. Then pick the crowded option under "Show details for" | "One click switches from fastest to most comfortable. The strip shows every stretch between stops, coloured by crowding, so you can see where the bus fills up. Change the time to 11 pm and compare." |
| 2:00 - 2:15 | Change the time to **18:00** on the same trip. 944 drops to standing room; **962 DOWN** shows "Not enough data" (grey strip). Optional second example: Mohammed Pur → Aiims (route 623: crowded at 9, seats at 2 pm) | "Where we have few tickets, we do not pretend. Few tickets would look like an empty bus, so low-confidence results are shown as 'not enough data'. Look at route 962 at 6 pm: every stretch is grey, and the card says not enough data on 7 of 7 stretches." |
| 2:15 - 2:40 | Click the **Operator view** tab; show the overloaded table, then the hour-by-hour chart for one route | "Operators get the same data: which route-hours are most overloaded, and how crowding moves through the day on one route. The evening and morning peaks are clear, and weekends are lighter. These are the sanity checks we ran: [peaks, busy corridors, weekday vs weekend]." |
| 2:40 - 3:00 | Back to the app or a closing slide with the repo URL | "Limits: pass holders are not in ticket data, so we apply an assumed uplift; riders may get off earlier or later than their ticket says; and until the GTFS timetable is added, ride times are rough estimates, with no map. Everything is open source and the pipeline can be rerun with one command. Thank you." |

## Before you record

- Make sure `outputs/crowding.csv` and `outputs/stop_order.csv` are in place so the app is not in DEMO mode.
- Journeys checked on the real data with Claude A: Shivaji Park → Moti Nagar Industrial Area, Spm College → Moti Nagar Industrial Area, Mohammed Pur → Aiims (route 623), Sewa Nagar Flyover → National Museum (route 522). Re-check on screen before recording, because the numbers can change if the pipeline is rerun.
- Keep the browser window clean (no bookmarks bar), zoom to 110%.
- Say once in the video: "Levels are relative to the busiest well-observed cells, based on one month of ticket data. Low-confidence results are shown as 'not enough data'." Say the AI disclosure line once in the video description: "Built with help from Claude (Anthropic)."

## If you have no real data yet (rehearsal only)

Run the app as is. It uses fake data. Do a full rehearsal, but do not submit that recording.
