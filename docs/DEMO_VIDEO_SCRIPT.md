# Demo video script (target 2:30 to 3:00)

Record the screen at 1080p with your voice over it. Use the REAL data, not demo mode (no yellow "DEMO MODE" banner).
Open the app before recording and run each search once so everything is cached and fast.
Put your real numbers where you see `[...]`.

| Time | On screen | Say |
|---|---|---|
| 0:00 - 0:20 | Title slide or the app's front page | "In Delhi, a packed bus is slow to board, hard to stand in, and people skip it. But journey planners rank trips by time only. We built a planner that shows how crowded each bus usually is, so you can choose between faster and more comfortable." |
| 0:20 - 0:50 | Terminal or a simple diagram: tickets → pipeline → `crowding.csv` → planner | "We took about [2.56 million] bus tickets from October. Each ticket says where and when someone boarded and where they planned to get off. Our pipeline turns them into an estimate of people on board for every stretch of every route, by day type and hour. It then labels each stretch: seats likely, standing room or crowded, with a confidence based on how many tickets sit behind it." |
| 0:50 - 1:10 | App: select **From: Anand Vihar ISBT**, **To: Connaught Place** (or any strong pair), weekday, **18:00** | "Here is a weekday 6 pm trip from Anand Vihar to Connaught Place." |
| 1:10 - 1:40 | The option cards. Point at the colour badge, total time and "Busiest stretch" line on each | "The fastest option takes [56] minutes, but it is usually crowded, mainly between [Karkardooma and Connaught Place]. The second takes [59] minutes with standing room. The third takes [67] minutes, and there are usually seats. Each card shows the overall level, the busiest stretch, and how much we trust the number." |
| 1:40 - 2:00 | Click **Most comfortable trips first**; the order changes. Then click the map option for the crowded trip | "One click switches from fastest to most comfortable. On the map, each stretch is coloured by crowding, so you can see where the bus fills up." |
| 2:00 - 2:15 | Pick a route/hour with few tickets (or hover a dashed segment) | "Where we have few tickets, we do not pretend. The app shows 'Estimate unreliable: few tickets', and the map draws that stretch dashed." |
| 2:15 - 2:40 | Click the **Operator view** tab; show the overloaded table, then the hour-by-hour chart for one route | "Operators get the same data: which route-hours are most overloaded, and how crowding moves through the day on one route. The evening and morning peaks are clear, and weekends are lighter. These are the sanity checks we ran: [peaks, busy corridors, weekday vs weekend]." |
| 2:40 - 3:00 | Back to the app or a closing slide with the repo URL | "Limits: pass holders are not in ticket data, so real loads can be higher, and times are from the timetable, not live GPS. Everything is open source and the pipeline can be rerun with one command. Thank you." |

## Before you record

- Hide the "DEMO MODE" banner by having both `data/raw/gtfs/` and `outputs/crowding.csv` in place.
- Pick the journey pair AFTER the pipeline is done: use one that really shows three options with different crowding.
- Keep the browser window clean (no bookmarks bar), zoom to 110%.
- Say the AI disclosure line once in the video description: "Built with help from Claude (Anthropic)."

## If you have no real data yet (rehearsal only)

Run the app as is. It uses fake data. Do a full rehearsal, but do not submit that recording.
