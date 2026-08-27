# Garage61 MCP Server

This document uses ASD-STE100 Simplified Technical English.

An MCP (Model Context Protocol) server that connects Claude to the Garage61
iRacing telemetry API. You can ask about your lap times and your telemetry data
in usual language. The primary function is a comparison of two laps that shows
you where you lose time.

## Functions

- **Monitor your progress.** Compare any two of your own laps for the same car
  and track. The output shows where you gained time and where you lost time.
- **Correct delta-time.** The server calculates the time differences from the
  speed data. It does not estimate them. This is the same calculation that the
  delta bar in the simulator uses.
- **Corner by corner.** The server finds the corners in the telemetry data. It
  gets the direction and the type from the GPS data. Thus the output shows
  "Turn 5 (slow right hairpin)" and not "43-48% of lap".
- **The true sector times.** The server uses the timing sectors of the track. It
  does not divide the lap into equal parts.
- **Removal of bad laps.** The server finds outlaps, spins, and offs. It removes
  them from the comparisons and gives the reason for each one.
- **Consistency data.** The output shows the spread, the variation in each
  sector, and your theoretical best lap. It uses all of your good laps.
- **Unprocessed data when you need it.** You can get the aligned channel values
  for any part of the lap when a summary is not enough.
- **Comparison with other drivers.** You can see all the drivers in your teams
  in the sequence of lap time. You can also see your position, and you can
  compare yourself with any driver by name.
- **Search that finds near matches.** You do not need the exact name of a car or
  a track.
- **Modern cars first.** The list of cars shows the current models first.
- **Data about the conditions.** The server gives a caution when the track
  temperature or the fuel quantity makes a comparison unequal.
- **Operation without a Pro plan.** The server operates with free accounts and
  with Pro accounts.

## Requirements

- Python 3.10 or later. The MCP package needs this version.
- A Garage61 account. See [garage61.net](https://garage61.net).
- Node.js, only if you want to use the MCP Inspector to do tests.

To see your Python version:

```bash
python3 --version
```

## Installation

```bash
git clone <your-repo-url>
cd garage61-mcp

python3 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate

pip install --upgrade pip
pip install -e .
```

**Note:** the `mcp` package must stay below version 2.0. Version 2.x removed the
low-level `Server` decorators that this server uses. An installation without a
version limit stops at startup with this message:
`AttributeError: 'Server' object has no attribute 'list_tools'`. The
`pyproject.toml` file contains the correct limit. Do not install `mcp` from
its source repository, because that also gives version 2.x.

To do a test with the MCP Inspector:

```bash
export GARAGE61_TOKEN=your-token
npx @modelcontextprotocol/inspector garage61-mcp
```

## Configuration of Claude Desktop

1. Get your Garage61 API token from [garage61.net](https://garage61.net).
2. Open the Claude Desktop configuration file:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
   - Linux: `~/.config/Claude/claude_desktop_config.json`
3. Add this configuration:

```json
{
  "mcpServers": {
    "garage61": {
      "command": "/path/to/your/project/.venv/bin/python",
      "args": ["-m", "garage61_mcp"],
      "env": {
        "GARAGE61_TOKEN": "your-garage61-token-here"
      }
    }
  }
}
```

4. Start Claude Desktop again.

**Caution:** Claude Desktop keeps this file in memory. If you change the file
while the application operates, the application can write over your changes.
Close Claude Desktop before you change the file.

## Use

Ask Claude these questions in usual language:

### Your own progress

- "Show me all my laps in the Porsche 992 GT3 R at Spa"
- "Why was my last lap at Spa slower than my personal best?"
- "Compare my most recent Monza lap with my fastest one"
- "Am I quicker at the Nordschleife in the M4 GT3 than last month?"

### Your best lap

- "What is my fastest lap with the Mazda MX-5 at Lime Rock Park?"
- "Show me my personal best at Nurburgring with the BMW M4 GT3"

### Other drivers

- "Who else has driven the 992 GT3 R at Spa, and what is my position?"
- "Compare me with Alex Patterson at Spa"
- "Who is immediately in front of me, and where do they gain the time?"

### More detail about a comparison

- "In which corners do I lose the most time?"
- "Show me sector 2 in detail"
- "What happens between 45% and 60% of the lap?"

### The available cars and tracks

- "Which cars have 'porsche' in the name?"
- "Show me all the track variants for Nurburgring"

## The MCP tools

### `list_cars`

Shows the available cars. The search finds near matches. The list shows modern
cars first.

Parameters:

- `search_term` (optional) — a filter for the car names, for example "porsche"
  or "gt3"
- `show_legacy` (optional) — include the older versions of the cars

### `list_tracks`

Shows the available tracks with all their variants and their exact names.

Parameters:

- `search_term` (optional) — a filter for the track names, for example "spa"

### `list_my_laps`

Shows each lap that you drove with one car on one track.

Parameters:

- `car` — the exact car name from `list_cars`
- `track` — the exact track name from `list_tracks`
- `clean_only` (optional) — include only the laps with a clean flag

Output:

- A table of each lap. It shows the date, the lap time, the difference to your
  personal best, the sector times, the availability of telemetry data, and the
  conditions.
- A summary of your progress. It compares your most recent lap with your first
  lap and with your best lap.

### `compare_my_laps`

Compares two of your own laps and shows where the time difference occurs. This
is the primary tool to monitor your progress.

Parameters:

- `car` — the exact car name from `list_cars`
- `track` — the exact track name from `list_tracks`
- `reference` (optional) — the lap to compare against. The default is `fastest`.
- `compared` (optional) — the lap to examine. The default is `latest`.

Both parameters accept `fastest`, `slowest`, `latest`, `oldest`, a lap number
from `list_my_laps`, or a date such as `2026-04-04`.

Output:

- The lap times, the difference, and the sector times
- The corners in the sequence of time lost
- A table of the corners with the minimum speeds, the brake points, and the
  quantity of full throttle
- The accumulated time difference around the lap
- A caution when the track temperature or the fuel quantity makes the
  comparison unequal

### `get_my_fastest_lap`

Shows a summary of your personal best lap.

Parameters: `car`, `track`

Output:

- The lap time and the sector times
- The maximum, minimum, and average speed
- The quantity of full throttle and of brake
- A speed profile of the lap
- The conditions of the lap

### `list_drivers`

Shows each driver who has laps that you can see for one car and one track.

Parameters: `car`, `track`

Output: the best lap of each driver, your time difference to each one, the
availability of their telemetry data, and your position in the group.

### `compare_to_driver`

Compares your best lap with the lap of one other driver, corner by corner.

Parameters: `car`, `track`, `driver` (the full name, the family name, or the
slug)

Output: the same analysis as `compare_my_laps`, but against that driver. It also
shows the position of each driver in the group.

## Which laps can you see?

Garage61 has no global lap search. The API documentation says that the laps of
other teams are private.

You get your own laps and the laps of all the drivers in all of your teams.
This group is usually much larger than one team. For one account, this gives 30
drivers for one combination of car and track. All of them had shared telemetry
data.

The `list_drivers` tool shows all of these drivers. The
`compare_my_telemetry_to_team` tool compares you with only the fastest one.

### `get_team_fastest_lap`

Shows the fastest lap from your teams, and your own laps.

Parameters: `car`, `track`

Output: the fastest lap time, the name of the driver, and your time difference.

### `compare_my_telemetry_to_team`

Compares your fastest lap with the fastest lap in your teams.

Parameters: `car`, `track`

Requirements:

- You must have a lap for this car and track.
- Another driver must have a lap for this car and track.
- You must not be the driver with the fastest lap.
- Telemetry data must be available for both laps. This usually needs a Pro plan.

Output: the same analysis as `compare_my_laps`, against the fastest lap.

### `analyze_consistency`

Examines all of your good laps together.

Parameters: `car`, `track`

Output:

- The spread and the standard deviation of your lap times
- The best, median, and worst time in each sector, and the sector with the
  largest variation
- Your theoretical best lap from your fastest sectors
- Your speed on each date, with the track temperature, and the trend

### More detail: `analyze_worst_sections`, `analyze_corner`, `analyze_telemetry_sector`, `analyze_telemetry_range`, `get_channel_window`

These tools examine the most recent comparison. Do a comparison first with
`compare_my_laps` or `compare_my_telemetry_to_team`. These tools use the stored
comparison. They do not get the telemetry data again.

- `analyze_worst_sections` — shows the corners in the sequence of time lost.
- `analyze_corner` — needs a `corner_number`. It shows all the data for that
  corner. This includes the positions of the brake release, the maximum
  steering, the maximum yaw rate, the minimum speed, and the first throttle. It
  also shows how near these positions are to each other. The output contains
  the shape of the steering input and the overlap of the brake and the
  steering. It also shows the increase of the throttle, the offset of the line,
  and the ABS activity. Set `all_laps: true` to examine the same corner in all laps of the
  stint. Then the output gives the spread of the turn-in point, the maximum
  brake pressure, the minimum speed, and the size of the corrections. Use this
  to find which corner is not consistent, when the sector data does not
  give enough detail.
- `analyze_telemetry_sector` — needs a `sector` number.
- `analyze_telemetry_range` — needs `start_pct` and `end_pct` (0 to 100).
- `get_channel_window` — gives the aligned values of both laps as a table of
  numbers. Parameters are `start_pct`, `end_pct`, an optional list of
  `channels` (speed, throttle, brake, gear, rpm, steering, lat_accel,
  long_accel, yaw_rate, abs, line), and an optional `points` count. Use this
  tool when the summaries do not answer your question.

## The division of the work

The server does the tasks that are difficult mathematically. These are
alignment, resample, delta-time integration, corner detection, and unit
conversion. The server does not try to explain why a corner was slower. It
gives measurements, and the model that reads them does the analysis.

This is the reason for the `get_channel_window` tool. An earlier version used
simple rules to make its own explanations. It gave output such as "lost 0.449s
— minimum speed 5.5 km/h higher", which explains nothing. Facts and access to
the correct numbers give much better results.

## How the time comparison operates

The server does not estimate the time differences from the speed differences.
It integrates them from the speed data.

The time for one lap is `t = L * integral(dd/v)`. Thus the difference between
two laps is `L * integral(1/v_b - 1/v_a) dd`.

The server calculates the track length `L` for each lap. It inverts the same
equation with the known lap time. Thus it does not need a table of track
lengths, and it adjusts itself to the units of the API.

Each lap uses its own recorded time for this calculation. Thus the total of the
corner differences is always equal to the difference of the lap times. The
calculated track length is accurate to about 1%.

## Remote operation (HTTP transport)

The server can also operate as a remote MCP server with streamable HTTP:

```bash
pip install -e .
garage61-mcp-http          # gives http://127.0.0.1:8080/mcp
```

Each request must contain the Garage61 token of the caller:

```
Authorization: Bearer <your-garage61-token>
```

The server holds no token. Each user supplies their own token. Thus one server
can supply many users, and each user sees only their own Garage61 data.

To add the server to Claude Code:

```bash
claude mcp add --transport http garage61 http://127.0.0.1:8080/mcp \
  --header "Authorization: Bearer <token>"
```

The repository contains a `Dockerfile`. The image listens on `0.0.0.0:$PORT`.
It operates on any host that accepts containers.

The `scripts/deploy-lambda.sh` script sends the server to AWS Lambda. Use an
API Gateway HTTP API in front of the function.

Two environment variables give more protection:

- `GARAGE61_MCP_ACCESS_KEY` — each request must then also contain the
  `X-MCP-Access-Key` header. This makes the endpoint private.
- `GARAGE61_MCP_ALLOWED_HOSTS` — a list of hosts, separated by commas. This
  starts the protection against DNS rebind attacks.

The `/healthz` endpoint has no protection. A load balancer uses it. It returns
only `{"status":"ok"}`.

## Access to the telemetry data

- **A free account** gives the lap times and the basic data.
- **A Pro account** gives the full CSV telemetry data.
- If the telemetry data is not available, the server shows the data that it can
  get. It does not stop with an error.

## How to find a fault

### "spawn python ENOENT"

Python is not in the PATH. Find the correct path with `which python3`. Then use
the full path in the Claude Desktop configuration.

### "GARAGE61_TOKEN environment variable is required"

The token is not in the Claude Desktop configuration. Make sure that the `env`
section contains your token.

### "Car/Track not found"

Use the `list_cars` and `list_tracks` tools first. The names must be exact. The
error message contains the names that are near your text.

### "No lap data found"

You did not drive this combination of car and track. As an alternative, your
account level does not permit access to the data.

### `AttributeError: 'Server' object has no attribute 'list_tools'`

The installed `mcp` package is version 2.x. Install a version below 2.0.

### More log data

Set `GARAGE61_LOG_LEVEL=DEBUG`. The default level is `WARNING`, because the
debug log contains all the telemetry data.

## Development

### The structure of the project

```
garage61_mcp/
├── src/                 # installs as the `garage61_mcp` package
│   ├── __init__.py
│   ├── __main__.py      # the stdio entry point
│   ├── http_server.py   # the HTTP entry point
│   ├── server.py        # the MCP server and the dispatch of the tools
│   ├── api_client.py    # the Garage61 API client
│   ├── cache.py         # the search for car and track names
│   ├── reqcontext.py    # the token context for each request
│   ├── telemetry.py     # the parser, the resample, and the delta-time
│   ├── lapquality.py    # the removal of bad laps
│   ├── formatting.py    # the conversion of results into text
│   └── tools.py         # the MCP tools
├── scripts/
│   └── deploy-lambda.sh # the deployment to AWS Lambda
├── Dockerfile
├── pyproject.toml
├── CLAUDE.md            # the notes for developers
└── README.md
```

### The test procedure

1. Install the package: `pip install -e .`
2. Do a test with the MCP Inspector:
   ```bash
   export GARAGE61_TOKEN=your-token
   npx @modelcontextprotocol/inspector garage61-mcp
   ```
3. Do a test of the HTTP transport:
   ```bash
   garage61-mcp-http &
   curl http://127.0.0.1:8080/healthz
   ```
4. Do a test with Claude Desktop. Add the server to the configuration, then
   start Claude Desktop again.

Read `CLAUDE.md` before you change the code. That document contains the
technical decisions and the faults that we corrected.

### How to contribute

1. Make a fork of the repository.
2. Make a branch for your change.
3. Make your change.
4. Do a test with the MCP Inspector and with Claude Desktop.
5. Send a pull request.

## Licence

MIT Licence. See the LICENSE file.
