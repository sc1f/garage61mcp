# Garage61 MCP Server

## Overview
An MCP (Model Context Protocol) server that exposes iRacing telemetry from the
Garage61 API. It lets Claude fetch lap times and telemetry, and — the main point
— compare laps against each other with a real delta-time calculation.

## Tools

**Discovery** (call these first to get exact names)
1. **`list_cars`** — available cars, modern generations prioritised
2. **`list_tracks`** — available tracks with all variants

**Your own laps**
3. **`list_my_laps`** — every lap you've set on a car/track, with dates, sector
   splits, conditions, and compromised laps flagged
4. **`compare_my_laps`** — compare two of your *own* laps corner by corner. This
   is the tool for tracking progress over time.
5. **`analyze_consistency`** — all laps at once: spread, per-sector variability,
   theoretical best, session-to-session trend
6. **`get_my_fastest_lap`** — summary of your personal best

**Other drivers**
7. **`list_drivers`** — leaderboard of everyone whose laps you can see on a
   car/track, with your gap and rank
8. **`compare_to_driver`** — corner-by-corner against one named driver
9. **`get_team_fastest_lap`** — fastest accessible lap and your gap to it
10. **`compare_my_telemetry_to_team`** — your best vs the fastest accessible lap

**Drill-down** (require a comparison to have been run first)
11. **`analyze_worst_sections`** — corners ranked by time lost
12. **`analyze_telemetry_sector`** / **`analyze_telemetry_range`** — summary of
    one sector or an arbitrary distance range
13. **`analyze_corner`** — one corner in full: rotation-event convergence,
    steering shape, brake/steering overlap, throttle ramp, line offset, ABS;
    `all_laps=true` runs the same corner across the whole stint with spreads
14. **`get_channel_window`** — raw aligned channel values for both laps across a
    range (speed, throttle, brake, gear, rpm, steering, lat/long accel,
    yaw_rate, abs, line offset), as numbers

## Division of labour: server computes, caller reasons

The server does what is numerically hard and deterministic — alignment,
resampling, delta-time integration, corner detection, unit conversion. The
caller does what is semantically hard — deciding *why* a corner was slower and
what to change.

This split is deliberate and was arrived at the hard way. An earlier version
generated its own explanations from if/else heuristics and produced output like
"lost 0.449s — minimum speed 5.5 km/h **higher**", which is not an explanation at
all. Emitting facts and letting the caller reason produced better results
immediately. **Do not reintroduce heuristic cause-finding into the server.** If
the caller needs more evidence, the answer is `get_channel_window`, which hands
back real numbers for any stretch of the lap.

## Transports

Two entry points share one Server (`server.build_server`):
- **stdio** (`src/__main__.py`) — single user, token from `GARAGE61_TOKEN` env.
  This is what Claude Desktop launches.
- **HTTP** (`src/http_server.py`, `garage61-mcp-http`) — multi-user streamable
  HTTP on `/mcp` (stateless, JSON responses). Every request must carry the
  caller's own Garage61 PAT as `Authorization: Bearer <token>`; tokens are
  verified against `/me` with a 10-minute cache and bound to a contextvar
  (`reqcontext.py`) that `create_client()` reads.

Multi-tenancy rules that must not regress:
- The comparison cache is keyed per user (`_cache_key` includes `user_scope()`),
  because it holds private lap telemetry. Two users comparing the same
  car/track must never share entries.
- The corner map cache is deliberately global (`_combo_key`): it is track
  geometry, identical for everyone, and sharing saves telemetry downloads.
- The cars/tracks cache fills lazily on the HTTP path (`ensure_cache`), since
  no token exists at process startup.
- The telemetry CSV cache (`api_client._telemetry_csv_cache`) is keyed per
  (user, lap): laps are immutable so caching is safe, but serving user B a CSV
  fetched with user A's token would bypass Garage61's authorization.
- Lap lists get a 60s TTL cache (`_lap_list_cache`) — they must refresh as new
  laps appear, but within one conversation every tool otherwise refetches the
  same list. A 7-tool analysis conversation costs 6 API requests total.
- Never bake a token into the HTTP image or read the env token on the HTTP
  path for a request that carried none.

Rate limiting (per https://garage61.net/developer/rate-limits): the API is a
continuously refilling token bucket per (application, user, operation); exact
allowances are unpublished. 429 bodies carry `details.retryAfterSeconds` (there
is no Retry-After header). Every request goes through `_api_get`, which:
retries once for waits ≤20s after sleeping retryAfterSeconds plus jitter;
fails fast with the wait time for longer ones; and records the block so
subsequent calls to that operation fail immediately client-side instead of
sending more requests ("pause the affected operation" per the docs). Never
bypass `_api_get` for a Garage61 call.

`Dockerfile` runs the HTTP entry point; no secrets in the image.

## Setup
1. Get a Garage61 API token from https://garage61.net
2. Create a `.env` file with `GARAGE61_TOKEN=your-token-here`, or set the
   variable in the MCP server config
3. `pip install -r requirements.txt`
4. Run `python3 src/__main__.py` (or `python -m garage61_mcp` after
   `pip install -e .`)

Set `GARAGE61_LOG_LEVEL=DEBUG` when troubleshooting; it defaults to `WARNING`
because debug logs include full telemetry payloads.

## Architecture
- **`src/api_client.py`** — Garage61 REST client and response models
- **`src/cache.py`** — car/track fuzzy matching and variant prioritisation
- **`src/telemetry.py`** — CSV parsing, resampling, delta-time, corner detection
- **`src/lapquality.py`** — deciding which laps are worth comparing
- **`src/formatting.py`** — turns analysis results into readable Markdown
- **`src/tools.py`** — MCP tool implementations and schemas
- **`src/server.py`** — MCP server setup and tool dispatch

## Things worth knowing before changing this code

**Telemetry CSV column order is not what it looks like.** `Speed` is column 0
and `LapDistPct` is column 1. Index into the CSV by *header name*, never by
position — an earlier version bucketed laps into sectors using column 0 and put
every sample in the last sector, because speed is always greater than 0.75.

**Units from the API are SI, not display units.** Speed is m/s (not km/h) and
`SteeringWheelAngle` is radians (not degrees). Convert only at the presentation
boundary; `telemetry.py` deliberately keeps everything in SI.

**`LapDistPct` is not monotonic.** Samples are time-ordered, so distance dips
around the start/finish line. `parse_lap_csv` sorts by distance and merges
duplicates before interpolating — don't assume ordering.

**Never return raw telemetry CSV from a tool.** A lap is roughly 8,000 rows /
1.3 MB, which blows past any model's context. Summary tools stay in the low
thousands of characters; `get_channel_window` is the sanctioned dense path and
is bounded by its point cap (250) — a maximal request (full lap, all channels,
both laps) is ~33k chars / ~8k tokens, which is deliberate and opt-in.

**Output is model-first, not renderer-first.** Every table is a fixed-width
block in a code fence (`formatting.fixed_table`); Markdown spends about a third
of each row on pipes. Uniform-grid series (`uniform_series`) state spacing once
instead of printing a position next to every value — same characters, twice the
resolution. Bold is stripped wholesale at the output boundary in `_ok()` so the
builder code stays readable; don't re-add emphasis or emoji. What stays, stays
for the model: `##` headers give addressable structure, fences mark verbatim
columns, and the `_…_` legends carry the metric definitions.

**Corner dynamics are defined measurements, not judgements.** Every metric in
`CornerDynamics` (coupling, build ratio, reversal, partial-hold, event spread)
has a one-line definition that ships in the output legend, and flags name their
condition ("brake released before turn-in"), never a verdict. The reversal
measure includes countersteer by design, so it can exceed peak steering — that
is signal, not a bug. Whether a shape is right for a given car is the caller's
call; keep it that way.

**Corner names are the caller's job.** The server numbers corners (stable via
the consensus map) and exposes the apex GPS in `analyze_corner`; attaching
human names to them is semantics and lives with the caller. A curated name
table was built and deliberately removed — don't reintroduce it.

**The line offset needs both laps' GPS.** `line_offset_series` projects the
position delta onto the reference tangent's normal (equirectangular metres), so
longitudinal misalignment doesn't contaminate the lateral figure. Positive =
left of the reference's direction of travel; the sign convention is stated in
every output that carries it.

**Conditions print with every comparison**, not only when they differ. Fuel and
track temperature are never normalised away — inventing a per-car correction
coefficient would be heuristic cause-finding; showing both laps' conditions next
to the delta is the honest version.

**Each braking event belongs to exactly one corner**
(`assign_brakes_to_corners`). A loose per-corner match made linked corners
share one event, so a corner taken flat showed its neighbour's brake shape as
its own. "No braking here" must stay visible as `-`.

**Track length is derived, not looked up.** `estimate_track_length` inverts
`lap_time = L * integral(dd/v)` to recover L from the speed trace. It lands
within about 1% (6936 m computed vs 7004 m actual at Spa) and self-calibrates
against whatever units the API returns. Delta-time is then a real integral, and
its endpoint is checked against the known lap-time gap — that discrepancy is
reported in the output rather than hidden.

**Sector boundaries come from the API.** Lap records carry real `sectors` times.
`build_segment_bounds` proportions them by cumulative time; it only falls back to
equal-sized chunks when sector data is missing.

**Delta-time normalises each lap to its own recorded time.** Do not "simplify"
this back to a single shared track length. The speed channel carries a per-lap
calibration bias — two F4 laps at Tsukuba imply track lengths 1.5% apart — and
using one lap's scale for both turns that bias into phantom time delta. It read
+1.094s on a real +0.265s gap. Normalising each lap by its own integral cancels
the bias and makes the endpoint exact by construction.

**Corners are detected from lateral acceleration, not braking.** Brake events
alone miss half of Tsukuba, where a light F4 takes many corners on a lift. Every
corner has a |LatAccel| peak by definition; brake and throttle only classify
what kind it is.

**Corner numbering must come from `build_corner_map`, never one lap.** Detecting
on a single lap makes the count depend on whose lap it is — the same F4 at
Tsukuba yields 11, 12 or 13 corners across nine drivers, because marginal kinks
sit at the threshold and some drivers straight-line them. That makes "Turn 11"
name a different corner in different comparisons, which silently invalidates any
advice keyed to a turn number. The consensus map keeps an apex only when a
majority of sampled laps find it, and is cached per car/track. It is verified
stable under shuffling and subsetting.

**Detected turn numbers are not the circuit's official numbers.** The map finds
driving-relevant corners (11 at both Spa and Tsukuba), whereas Spa officially has
19-20 and Tsukuba 12-14. Numbering is internally consistent and stable, which is
what comparisons need, but don't cross-reference it against a track guide.

**Corner direction comes from GPS, not LatAccel.** The accelerometer's sign
convention disagrees with reality — it reports Spa's La Source, a right-hand
hairpin, as a left. `_heading_change` derives direction from Lat/Lon, which is
unambiguous and cross-checks correctly against the real circuit.

**Corner extents must stay bounded.** The extent walk stops at the midpoint to
each neighbouring apex and at an absolute width cap. Without those, a continuous
complex never drops below the edge threshold, one "corner" swallows two-thirds
of the lap, and the GPS heading measured across it becomes meaningless.

**Read corner values at the apex, not as a window extreme.** `min()` over a
corner window is wrong twice over: the gear channel dips to 0 during downshift
blips, and where a corner flows into the next braking zone the slowest point
sits at the window edge rather than the apex.

**Compromised laps are excluded, never silently.** `lapquality.split_usable`
flags outlaps, offs and spins by comparing each sector against the field median
(4 of 10 Tsukuba laps had a sector 1.5-1.8x normal). Excluded laps are always
reported with the reason, and the filter backs off entirely rather than leave
fewer than two laps to compare.

**`mcp` must stay below 2.0.** Version 2.x removed the low-level
`@server.list_tools()` / `@server.call_tool()` decorator API that `server.py` is
built on. An unpinned install picks up 2.0 and crashes on startup with
`AttributeError: 'Server' object has no attribute 'list_tools'`. Migrating to the
2.x `MCPServer` API is a separate piece of work.

## Repository layout note
The sources live in `src/`, which installs as the `garage61_mcp` package via
`package-dir` in `pyproject.toml`. An older duplicate copy of the code may still
exist at `garage61_mcp/`; it is stale and shadows the installed package when
Python runs from the repository root. Remove it if present.

## API Integration
- Base URL: `https://garage61.net/api/v1`
- Authentication: Bearer token
- Lap queries require both `cars` and `tracks` parameters; there is no
  unfiltered "all my laps" query
- `group=none` returns every lap, `group=driver` collapses to personal bests
- **There is no global lap search, by design.** The API documents that laps
  outside your own teams are private. Omitting `drivers` returns you plus
  everyone across all your teams, which is the widest pool available — at Spa in
  the 992 that is 30 drivers, not one teammate. Don't collapse that to a single
  "team best"; `list_drivers` exposes the whole field.
- **`drivers` only accepts the literal `"me"`.** Slugs and driver IDs both
  return 400, so narrowing to one specific teammate has to be done client-side
  after fetching the accessible set.
- Unknown query parameters are silently ignored rather than rejected, so a
  200 response is not evidence that a parameter did anything. Validate a guessed
  parameter by passing a deliberately bogus value and checking for a 400.
- `/me` returns the token owner's slug, plan and teams. Use its slug to identify
  the user's own laps; matching on driver name or lap time is fragile.

## Linking into the Garage61 web app

Verified route patterns (read from the Angular route table in `chunk-GPEUW4LY.js`,
not guessed — the SPA returns 200 for every path, so probing URLs proves nothing):

- `/app/laps/{trackId}/{carId}` — lap browser filtered to a car/track, with an
  **Analyze** button per lap. This is what `garage61_laps_url` emits.
- `/app/analysis/laps/{id};v={view}` — the comparison view, but `{id}` is a
  **saved analysis ULID** created server-side through the UI. It cannot be
  synthesised from two lap ids, and the public API exposes no endpoint to create
  one, so there is no deep link to an arbitrary comparison.
- `/app/analyze/{platform}/{track}/{car}` exists in the route table but redirects
  away when given numeric ids.

Worth knowing: the web UI's lap browser shows **global** lap data (5,251 laps at
Tsukuba in the F4 versus the 9 drivers the API returns). The restriction is on
the API, not the product, so the link is the only route to the global field.
- Telemetry (`/laps/{id}/csv`) returns 403 without a Pro plan; the client
  degrades to lap times rather than failing
