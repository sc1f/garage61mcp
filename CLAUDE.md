# Garage61 MCP Server

This document uses ASD-STE100 Simplified Technical English.

## Overview

This is an MCP (Model Context Protocol) server. It gives Claude access to the
iRacing telemetry data in the Garage61 API. Claude can get lap times and
telemetry data. Claude can also compare two laps and calculate the correct time
difference between them. This comparison is the primary function of the server.

## Tools

Call these two tools first, to get the correct names of the cars and the tracks:

1. `list_cars` — shows the available cars. The list shows modern cars first.
2. `list_tracks` — shows the available tracks and all their variants.

These tools show your own laps:

3. `list_my_laps` — shows each lap that you drove with one car on one track.
   The data includes the date, the sector times, and the conditions. The tool
   also puts a flag on each bad lap.
4. `compare_my_laps` — compares two of your own laps, corner by corner. Use this
   tool to monitor your progress.
5. `analyze_consistency` — examines all of your laps together. The data includes
   the spread of the lap times, the variation in each sector, the theoretical
   best lap, and the trend between sessions.
6. `get_my_fastest_lap` — shows a summary of your best lap.

These tools show the laps of other drivers:

7. `list_drivers` — shows each driver who has laps that you can see for one car
   and one track. The data includes your time difference and your position.
8. `compare_to_driver` — compares your lap with the lap of one other driver,
   corner by corner.
9. `get_team_fastest_lap` — shows the fastest lap that you can see. The data
   includes your time difference to that lap.
10. `compare_my_telemetry_to_team` — compares your best lap with the fastest lap
    that you can see.

You must do a comparison before you use these tools:

11. `analyze_worst_sections` — shows the corners in the sequence of time lost.
12. `analyze_telemetry_sector` and `analyze_telemetry_range` — show a summary of
    one sector or of a distance range.
13. `analyze_corner` — shows all the data for one corner. The data includes
    the positions of the rotation events and the shape of the steering input.
    It also includes the overlap of the brake and the steering, the increase of
    the throttle, the offset of the line, and the ABS activity. If you set
    `all_laps=true`, the tool examines the same corner in all laps of the
    stint. It then also gives the spread of each measurement.
14. `get_channel_window` — shows the aligned channel values of both laps for a
    distance range. The channels are speed, throttle, brake, gear, rpm,
    steering, lateral acceleration, longitudinal acceleration, yaw rate, ABS,
    and the offset of the line. The tool gives the values as numbers.

## Division of the work: the server calculates, the caller analyzes

The server does the tasks that are difficult mathematically and that give the
same result each time. These tasks are alignment, resample, delta-time
integration, corner detection, and unit conversion.

The caller does the tasks that need an opinion. The caller decides why a corner
was slower, and what the driver must change.

We made this division of the work on purpose. An earlier version of the server
used if/else rules to make its own explanations. That version gave output such
as "lost 0.449s — minimum speed 5.5 km/h higher". This is not an explanation.
The server now sends only facts, and the caller does the analysis. The results
became better immediately.

Do not put rules that find causes into the server again. If the caller needs
more data, use `get_channel_window`. That tool gives the correct numbers for
any part of the lap.

## Transports

Two entry points use the same Server object from `server.build_server`:

- **stdio** (`src/__main__.py`) — for one user only. The token comes from the
  `GARAGE61_TOKEN` environment variable. Claude Desktop starts this entry point.
- **HTTP** (`src/http_server.py`, `garage61-mcp-http`) — for many users. It
  gives a streamable HTTP endpoint at `/mcp`. The endpoint is stateless and it
  sends JSON responses. Each request must contain the Garage61 token of the
  caller in the `Authorization: Bearer <token>` header. The server checks each
  token against the `/me` endpoint and keeps the result in a cache for 10
  minutes. The server then puts the token in a contextvar (`reqcontext.py`).
  The `create_client()` function reads this contextvar.

### Rules for many users

Do not change these rules:

- The comparison cache uses one key for each user. The `_cache_key` function
  includes `user_scope()`. This cache holds private telemetry data. Two users
  who compare the same car and track must never get the entries of the other
  user.
- The corner map cache is global on purpose. It uses `_combo_key`. The corner
  map contains the geometry of the track, which is the same for all users. A
  shared cache prevents unnecessary downloads of telemetry data.
- The cars and tracks cache fills at the time of the first request on the HTTP
  path (`ensure_cache`). No token is available when the process starts.
- The telemetry CSV cache (`api_client._telemetry_csv_cache`) uses a key of
  (user, lap). Laps do not change, thus this cache is safe. But the key must
  stay. The server must never give user B a CSV file that it got with the token
  of user A. That would be a failure of the Garage61 authorization.
- The lap lists use a cache with a time limit of 60 seconds
  (`_lap_list_cache`). These lists must refresh when the user drives new laps.
  In one conversation, almost all tools get the same list. A conversation that
  uses 7 tools makes only 6 API requests in total.
- Never put a token in the HTTP image. On the HTTP path, never read the token
  from the environment for a request that did not contain one.

### Rate limits

The Garage61 documentation for the rate limits is at
https://garage61.net/developer/rate-limits.

The API uses a token bucket that fills continuously. There is one bucket for
each combination of application, user, and operation. Garage61 does not publish
the quantities.

A 429 response contains `details.retryAfterSeconds` in the body. There is no
Retry-After header.

All requests go through the `_api_get` function. This function does three
things:

- If the wait is 20 seconds or less, it waits for `retryAfterSeconds` plus a
  random time, then makes one more request.
- If the wait is more than 20 seconds, it stops immediately and reports the
  time to wait.
- It records the block. The next calls to that operation stop immediately on
  the client. They do not make more requests. The Garage61 documentation gives
  this instruction: "pause the affected operation".

Never make a Garage61 request that does not use `_api_get`.

### Deployment

The `Dockerfile` starts the HTTP entry point. The image contains no secrets.

The remote deployment is AWS Lambda with an API Gateway HTTP API in front of it.
The `scripts/deploy-lambda.sh` script builds the deployment. It makes a zip file
and uses the Lambda Web Adapter layer. The ASGI application does not change.

AWS App Runner was the first target for the deployment. But App Runner stopped
acceptance of new customers in April 2026. Its replacement is ECS Express Mode.
ECS Express Mode needs an Application Load Balancer, which costs about 16 to 25 USD each month when it is not in use. These costs are too high for
this project.

Lambda decreases to zero instances when there is no traffic. This agrees with
the stateless transport, and the costs are near zero for personal use.
There are two disadvantages. API Gateway stops a request after 30 seconds. Also,
the caches in memory do not continue through a cold start. Thus a detailed
examination after a long period of no use can need a new comparison.

### Credentials

Usually the credentials are in the `Authorization` and `X-MCP-Access-Key`
headers. The server also accepts them as the `?token=` and `?key=` query
parameters. The claude.ai connectors supply the Claude mobile application, and
these connectors cannot send custom headers.

The Lambda start command includes `--no-access-log` because of these query
parameters. If the access log stayed on, the tokens would go into CloudWatch.

### Protection against abuse

The HTTP path (`http_server.py`) has three protections:

- The server rejects a body of more than 256 KB. It reads the Content-Length
  header and does not read the body.
- Verification of an unknown token goes through a token bucket. The burst is
  10 tokens and the refill rate is 1 token each second. Thus many random tokens
  cannot make a large quantity of `/me` traffic to Garage61. Tokens in the
  cache are not affected.
- If you set `GARAGE61_MCP_ACCESS_KEY`, each request must contain the
  `X-MCP-Access-Key` header. The comparison uses a constant time. This makes
  the endpoint private.

The `/healthz` endpoint stays open for the checks of the load balancer. It
returns only `{"status":"ok"}`.

On Lambda, the cost increases with the quantity of invocations, not with the
hours of operation. Thus the access key is the practical control of the costs.
The server rejects a person who does not have the key before it makes a
Garage61 request. Set a reserved-concurrency limit if you want an absolute
maximum.

## Installation

1. Get a Garage61 API token from https://garage61.net.
2. Make a `.env` file that contains `GARAGE61_TOKEN=your-token-here`. As an
   alternative, set this variable in the MCP server configuration.
3. Run `pip install -r requirements.txt`.
4. Run `python3 src/__main__.py`. As an alternative, run
   `python -m garage61_mcp` after you run `pip install -e .`.

Set `GARAGE61_LOG_LEVEL=DEBUG` to find a fault. The default level is `WARNING`,
because the debug log contains all the telemetry data.

## Architecture

- `src/api_client.py` — the Garage61 REST client and the response models
- `src/cache.py` — the fuzzy match of car and track names, and the sequence of
  the variants
- `src/telemetry.py` — the CSV parser, the resample, the delta-time, and the
  corner detection
- `src/lapquality.py` — the decision about which laps are good enough to compare
- `src/formatting.py` — the conversion of the results into Markdown
- `src/tools.py` — the MCP tools and their schemas
- `src/server.py` — the MCP server and the dispatch of the tools

## What you must know before you change this code

### The sequence of the CSV columns is not usual

`Speed` is column 0. `LapDistPct` is column 1. Always use the header name to
find a column. Never use the position of the column. An earlier version used
column 0 to divide a lap into sectors. It put all the samples in the last
sector, because the speed is always more than 0.75.

### The units from the API are SI units

The speed is in m/s, not in km/h. The `SteeringWheelAngle` is in radians, not
in degrees. Convert the units only when you make the output. The `telemetry.py`
module keeps all values in SI units on purpose.

### `LapDistPct` does not always increase

The samples are in the sequence of time. Thus the distance decreases near the
start/finish line. The `parse_lap_csv` function sorts the samples by distance
and merges the duplicates before it interpolates. Do not assume a sequence.

### Never send the unprocessed telemetry CSV from a tool

One lap has about 8,000 rows and 1.3 MB of data. This is more than the
context of any model. The summary tools give a few thousand characters.

The `get_channel_window` tool is the approved method to get dense data. Its
maximum is 250 points. The largest possible request is for one full lap, all
channels, and both laps. That request gives about 33,000 characters,
or about 8,000 tokens. This is intentional, and the caller must ask
for it.

### The output is for a model, not for a renderer

Each table is a fixed-width block in a code fence
(`formatting.fixed_table`). A Markdown table uses about one third of
each row for the pipe characters.

A series on a uniform grid (`uniform_series`) gives the spacing one time. It
does not print a position with each value. The same quantity of characters thus
gives two times the resolution.

The `_ok()` function removes all bold markers at the output boundary. The code
that makes the output stays easy to read. Do not add emphasis or emoji again.

Three Markdown elements stay, because they help the model. The `##` headings
give a structure that the model can refer to. The code fences show which
columns are exact. The `_..._` legends give the definitions of the
measurements.

### The corner dynamics are measurements, not opinions

Each measurement in `CornerDynamics` has a definition of one line. These
measurements are the coupling, the build ratio, the reversal, the partial hold,
and the event spread. The output legend contains each definition.

A flag gives the name of a condition, for example "brake released before
turn-in". A flag never gives an opinion.

The reversal measurement includes the countersteer. This is intentional. Thus
the reversal can be more than the maximum steering angle. This is correct data,
not a fault.

Only the caller can decide if a shape is correct for a given car. Do not change
this.

### The caller supplies the names of the corners

The server gives a number to each corner. The consensus map makes these numbers
stable. The `analyze_corner` tool also gives the GPS position of the apex.

A name for a corner is an opinion, and it is the responsibility of the caller.
A table of corner names existed in this code. We removed it on purpose. Do not
add it again.

### The offset of the line needs the GPS data of both laps

The `line_offset_series` function projects the difference in position onto the
normal of the tangent of the reference lap. It uses equirectangular metres.
Thus an error in the longitudinal direction does not change the lateral value.

A positive value is to the left of the direction of movement of the reference
lap. Each output that contains this value also gives the sign convention.

### Each comparison shows the conditions

The output shows the conditions of both laps, not only when they are different.

The server never adjusts the values for the fuel quantity or the track
temperature. A correction coefficient for each car would be a rule that finds
causes. Instead, the server shows the conditions of both laps with the time
difference.

### Each brake event belongs to only one corner

The `assign_brakes_to_corners` function does this. An earlier match was not
exact, and two connected corners used the same brake event. Thus a corner
without brake input showed the brake data of the adjacent corner. The output
must show `-` when there is no brake input.

### The server calculates the length of the track

The `estimate_track_length` function inverts the equation
`lap_time = L * integral(dd/v)`. It calculates L from the speed data.

The result is accurate to about 1%. At Spa the calculated value is
6936 m and the actual value is 7004 m. The function also adjusts itself for the
units that the API sends.

The delta-time is then a true integral. The server compares its final value
with the known difference of the lap times. The output shows this difference.
The server does not hide it.

### The sector limits come from the API

The lap records contain the true `sectors` times. The `build_segment_bounds`
function divides the lap by the cumulative time. It uses equal parts only when
the sector data is not available.

### The delta-time uses the recorded time of each lap

Do not change this to one shared track length. The speed channel has a small
error for each lap. Two F4 laps at Tsukuba give track lengths that are 1.5%
different. If you use the scale of one lap for both laps, this error becomes an
incorrect time difference. The output showed +1.094s for an actual difference
of +0.265s. When each lap uses its own integral, the error cancels and the
final value is always correct.

### The server finds corners with the lateral acceleration, not with the brake

Brake events alone do not find half of the corners at Tsukuba. A light F4 car
goes through many corners with only a lift of the throttle. Each corner has a
maximum of |LatAccel|. The brake and the throttle only show the type of corner.

### The corner numbers must come from `build_corner_map`

Do not find the corners in one lap only. Then the quantity of corners changes
with the driver. The same F4 car at Tsukuba gives 11, 12, or 13 corners for
nine different drivers. Small corners are near the threshold, and some drivers
go through them in a straight line.

Then "Turn 11" is a different corner in each comparison. This makes all advice
about a turn number incorrect, and there is no warning.

The consensus map keeps an apex only if the majority of the sample laps find
it. There is one map in the cache for each car and track. Tests show that the
map is stable when the sequence of the laps changes and when fewer laps are
used.

### The corner numbers are not the official numbers of the circuit

The map finds the corners that are important to the driver. It finds 11 corners
at Spa and 11 at Tsukuba. Spa officially has 19 or 20 corners, and Tsukuba has
12 to 14. The numbers are internally consistent and stable, which is what a
comparison needs. But do not compare them with a track guide.

### The direction of a corner comes from the GPS data, not from LatAccel

The sign convention of the accelerometer is not correct. It shows La Source at
Spa as a left corner, but La Source is a right hairpin. The `_heading_change`
function calculates the direction from the Lat and Lon channels. This value is
correct, and tests against the actual circuit agree with it.

### The extent of a corner must have limits

The function that finds the extent stops at the middle point between two
adjacent apexes. It also stops at a maximum width. Without these limits, a
continuous group of corners never goes below the threshold. Then one "corner"
includes two thirds of the lap, and the GPS heading across it has no meaning.

### Read the corner values at the apex

Do not use the minimum value in the corner window. This is incorrect for two
reasons. The gear channel decreases to 0 during a downshift. Also, when a
corner continues into the next brake zone, the slowest point is at the edge of
the window and not at the apex.

### The server always reports the bad laps that it removes

The `lapquality.split_usable` function finds outlaps, offs, and spins. It
compares each sector with the median of all the laps. At Tsukuba, 4 of 10 laps
had one sector that was 1.5 to 1.8 times the usual time.

The output always gives the reason for each removed lap. If fewer than two laps
would remain, the function stops the filter and keeps all the laps.

### The `mcp` package must stay below version 2.0

Version 2.x removed the `@server.list_tools()` and `@server.call_tool()`
decorators. The `server.py` module uses these decorators. An installation
without a version limit gets version 2.0. The server then stops at startup with
this message: `AttributeError: 'Server' object has no attribute 'list_tools'`.
A change to the 2.x `MCPServer` API is a separate task.

## The layout of the repository

The source code is in `src/`. The `package-dir` setting in `pyproject.toml`
installs it as the `garage61_mcp` package.

An older copy of the code can exist at `garage61_mcp/`. That copy is not
current. It hides the installed package when Python starts in the top directory
of the repository. Delete that directory if it exists.

## The Garage61 API

- Base URL: `https://garage61.net/api/v1`
- Authentication: a Bearer token
- A lap query must have the `cars` parameter and the `tracks` parameter. There
  is no query for all of your laps.
- `group=none` gives each lap. `group=driver` gives one best lap for each
  driver.

### There is no global lap search

This is intentional. The API documentation says that the laps of other teams are
private.

If you do not send the `drivers` parameter, the API gives your laps and the laps
of all the drivers in all of your teams. This is the largest set that is
available. For the 992 car at Spa, this is 30 drivers, not one teammate.

Do not reduce this set to one "team best" lap. The `list_drivers` tool shows all
the drivers.

### The `drivers` parameter accepts only the value `"me"`

A slug and a driver ID both give a 400 response. Thus you must select one
teammate on the client, after you get the full set.

### The API ignores unknown query parameters

The API does not reject them. Thus a 200 response is not evidence that a
parameter had an effect. To test a parameter, send an incorrect value and make
sure that the response is 400.

### The `/me` endpoint

This endpoint gives the slug, the plan, and the teams of the owner of the token.
Use the slug to find the laps of the user. A match on the driver name or on the
lap time is not reliable.

### Telemetry data needs a Pro plan

The `/laps/{id}/csv` endpoint gives a 403 response without a Pro plan. The
client then uses only the lap times. It does not stop with an error.

## Links to the Garage61 web application

These routes are verified. We read them from the Angular route table in
`chunk-GPEUW4LY.js`. We did not guess them. The web application gives a 200
response for each path, thus a test of a URL gives no information.

- `/app/laps/{trackId}/{carId}` — the lap browser for one car and one track.
  Each lap has an **Analyze** button. The `garage61_laps_url` function makes
  this link.
- `/app/analysis/laps/{id};v={view}` — the comparison view. The `{id}` value is
  a ULID of an analysis that the user saved through the web interface. You
  cannot make this value from two lap IDs. The public API has no endpoint to
  make one. Thus there is no direct link to a comparison.
- `/app/analyze/{platform}/{track}/{car}` — this route is in the route table.
  But it goes to a different page if you give it numeric IDs.

Note: the lap browser in the web application shows global lap data. At Tsukuba
it shows 5,251 laps in the F4 car, but the API gives only 9 drivers. The limit
is in the API, not in the product. Thus this link is the only method to see all
the drivers.
