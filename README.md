# Garage61 MCP Server

A Model Context Protocol (MCP) server that connects Claude Desktop to Garage61's iRacing telemetry API. Ask about your lap times and telemetry in natural language, and — the main point — compare laps against each other to see exactly where the time goes.

## Features

- 📈 **Track your own progress**: compare any two of your own laps on the same car and track, and see precisely where you gained or lost time
- ⏱️ **Real delta-time analysis**: time gaps are integrated from the speed traces, not estimated — the same calculation as a delta bar in the sim
- 🏁 **Corner-by-corner**: corners are detected from the telemetry itself, with direction and type derived from GPS, so analysis reads "Turn 5 (slow right hairpin)" rather than "43-48% of lap"
- 🗂️ **Real sector splits**: uses the track's actual timing sectors, not arbitrary quarters
- 🧹 **Ignores compromised laps**: outlaps, spins and offs are detected and excluded from comparisons, with the reason reported
- 📊 **Consistency analysis**: spread, per-sector variability, and your theoretical best lap across every clean lap
- 🔬 **Raw data on demand**: pull the actual aligned channel values for any stretch of the lap when a summary isn't enough
- 🏁 **Team comparison**: measure yourself against your team's fastest lap
- 🔍 **Smart search**: fuzzy matching for car and track names
- 🏎️ **Modern cars**: automatically prioritizes current generation vehicles
- 🌡️ **Condition awareness**: flags when track temperature or fuel load differs enough to make a comparison unfair
- ⚡ **Graceful degradation**: works with both free and Pro Garage61 accounts

## Requirements

- **Python 3.10+** (required for MCP package)
- Node.js (for MCP Inspector testing)
- Garage61 account ([garage61.net](https://garage61.net))

**Check your Python version:**
```bash
python3 --version  # Should be 3.10 or higher
```

**If you need to upgrade Python with pyenv:**
```bash
# Install a newer Python version
pyenv install 3.11.8

# Set as global default
pyenv global 3.11.8

# Verify the version
python3 --version
```

## Quick Install

### Option 1: Virtual Environment Setup (Recommended)

```bash
# Clone and set up virtual environment
git clone <your-repo-url>
cd garage61-mcp

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Upgrade pip for better pyproject.toml support
pip install --upgrade pip

# Install the package and its dependencies
pip install -e .
```

> **Note:** `mcp` is pinned below 2.0. Version 2.x removed the low-level
> `Server` decorator API this server is built on, and an unpinned install
> crashes on startup with
> `AttributeError: 'Server' object has no attribute 'list_tools'`.

**Test your installation:**
```bash
# Test with MCP Inspector (from project root)
export GARAGE61_TOKEN=your-token
npx @modelcontextprotocol/inspector garage61-mcp
```

### Option 2: Automated Setup

```bash
# Clone and install
git clone <your-repo-url>
cd garage61-mcp
python install.py
```

This will:
- Install the package 
- Set up Claude Desktop configuration
- Guide you through the token setup

### Option 3: Manual Setup (if editable install fails)

If you get "editable mode currently requires a setuptools-based build" error:

```bash
# Install dependencies directly
pip3 install "httpx>=0.25.0" "pydantic>=2.0.0" "python-dotenv>=1.0.0"
pip3 install git+https://github.com/modelcontextprotocol/python-sdk.git

# Run directly from src directory
cd src
export GARAGE61_TOKEN=your-token
python3 __main__.py
```

2. **Get your Garage61 API token** from [garage61.net](https://garage61.net)

3. **Configure Claude Desktop**:
   
   Edit your Claude Desktop config file:
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
   - **Linux**: `~/.config/Claude/claude_desktop_config.json`

   Add this configuration:
   ```json
   {
     "mcpServers": {
       "garage61": {
         "command": "python3",
         "args": ["__main__.py"],
         "cwd": "/absolute/path/to/garage61_mcp/src",
         "env": {
           "GARAGE61_TOKEN": "your-garage61-token-here"
         }
       }
     }
   }
   ```

   **Option A: Virtual Environment (recommended):**
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

   **Option B: Global Install:**
   ```json
   {
     "mcpServers": {
       "garage61": {
         "command": "garage61-mcp",
         "env": {
           "GARAGE61_TOKEN": "your-garage61-token-here"
         }
       }
     }
   }
   ```

4. **Restart Claude Desktop**

## Usage

Ask Claude natural language questions about iRacing data:

### Tracking your own progress
- *"Show me all my laps in the Porsche 992 GT3 R at Spa"*
- *"Why was my last lap at Spa slower than my personal best?"*
- *"Compare my most recent Monza lap to my fastest one"*
- *"Am I getting quicker at the Nordschleife in the M4 GT3?"*
- *"Compare my laps from April to the ones I set last week"*

### Personal performance
- *"What's my fastest lap with the Mazda MX-5 at Lime Rock Park?"*
- *"Show me my personal best at Nürburgring with the BMW M4 GT3"*

### Team records and comparisons
- *"What's the team record for Mercedes AMG GT3 at Silverstone?"*
- *"Show me where I'm losing time compared to my teammate's fastest lap"*
- *"Compare my telemetry to the team fastest lap at Spa with the Porsche 992 GT3"*

### Drilling into a comparison
- *"Which corners am I losing the most time in?"*
- *"Look at sector 2 in detail"*
- *"What's happening between 45% and 60% of the lap?"*

### Discovery
- *"What cars are available that match 'porsche'?"*
- *"Show me all track variants for Nürburgring"*

## MCP Tools

The server provides these tools for Claude:

### `list_cars`
Find available cars with fuzzy search and modern car prioritization.

**Parameters:**
- `search_term` (optional): Filter cars (e.g., "porsche", "gt3")
- `show_legacy` (optional): Include older car versions

### `list_tracks`  
Find available tracks with all variants and exact names.

**Parameters:**
- `search_term` (optional): Filter tracks (e.g., "spa", "silverstone")

### `list_my_laps`
List every lap you've set on a car/track combination.

**Parameters:**
- `car`: Exact car name from `list_cars`
- `track`: Exact track name from `list_tracks`
- `clean_only` (optional): Only include laps flagged clean

**Returns:**
- A table of every lap: date, lap time, gap to your personal best, sector splits,
  whether telemetry is available, and the conditions it was set in
- A progression summary comparing your latest lap to your earliest and your best

### `compare_my_laps`
Compare two of your own laps and attribute the gap across the lap. This is the
main tool for tracking progress over time.

**Parameters:**
- `car`: Exact car name from `list_cars`
- `track`: Exact track name from `list_tracks`
- `reference` (optional): The benchmark lap. Defaults to `fastest`.
- `compared` (optional): The lap measured against it. Defaults to `latest`.

Both selectors accept `fastest`, `slowest`, `latest`, `oldest`, a lap number
from `list_my_laps`, or a date such as `2026-04-04`.

**Returns:**
- Lap times, the gap, and per-sector splits
- A ranked explanation of where the time went, with the likely cause
- A segment table with minimum speeds, braking points, and full-throttle share
- The cumulative time gap around the lap
- A warning when track temperature or fuel load differed enough to make the
  comparison unfair

### `get_my_fastest_lap`
Get a summary of your personal best lap.

**Parameters:**
- `car`: Exact car name from `list_cars`
- `track`: Exact track name from `list_tracks` 

**Returns:**
- Lap time and sector splits
- Top, minimum, and average speed; full-throttle and braking share
- A coarse speed trace around the lap
- The conditions it was set in

### `get_team_fastest_lap`
Get the team record lap from accessible data.

**Parameters:**
- `car`: Exact car name from `list_cars`
- `track`: Exact track name from `list_tracks`

**Returns:**
- Team record lap time
- Driver info and lap ID  
- Comparison with your personal best
- Telemetry data (if available/Pro plan)

### `compare_my_telemetry_to_team`
Compare your fastest lap telemetry to the team fastest lap with detailed analysis.

**Parameters:**
- `car`: Exact car name from `list_cars`
- `track`: Exact track name from `list_tracks`

**Requirements:**
- You must have a recorded lap for this car/track combination
- Team must have a recorded lap for this car/track combination  
- You cannot be the team fastest lap holder (no comparison needed)
- Both laps must have telemetry data available (Pro plan typically required)

**Returns:** the same analysis as `compare_my_laps`, against the team's best lap.

### `analyze_consistency`
Analyse every representative lap at once.

**Parameters:** `car`, `track`

**Returns:**
- Lap time spread and standard deviation across all clean laps
- Per-sector best/median/worst and which sector you're least consistent in
- Your theoretical best lap from your fastest sectors
- Pace by session date, with track temperature, and the overall trend

### `analyze_worst_sections`, `analyze_telemetry_sector`, `analyze_telemetry_range`, `get_channel_window`
Drill into the most recent comparison for a car/track. Run `compare_my_laps` or
`compare_my_telemetry_to_team` first — these read the stored comparison rather
than re-downloading telemetry.

- `analyze_worst_sections` ranks the corners where time is lost
- `analyze_telemetry_sector` takes a `sector` number
- `analyze_telemetry_range` takes `start_pct` and `end_pct` (0-100)
- `get_channel_window` returns the raw aligned values for both laps across a
  range, as a numeric table. Takes `start_pct`, `end_pct`, an optional
  `channels` list (speed, throttle, brake, gear, rpm, steering, lat_accel,
  long_accel) and an optional `points` count. Use it when the summaries don't
  settle a question and you want to read the traces yourself.

## How the analysis is split

The server does what is numerically hard — alignment, resampling, delta-time
integration, corner detection, unit conversion. It deliberately does **not** try
to explain *why* a corner was slower; it reports measurements and leaves the
reasoning to the model reading them.

That split is why `get_channel_window` exists. An earlier version generated its
own explanations from heuristics and produced things like "lost 0.449s — minimum
speed 5.5 km/h *higher*", which explains nothing. Facts plus the ability to pull
real numbers on demand works far better.

## How the time comparison works

Time gaps are not estimated from speed differences — they are integrated from the
speed traces. Time around a lap is `t = L * ∫ dd/v`, so the gap between two laps
is `L * ∫ (1/v_b - 1/v_a) dd`. Track length `L` is recovered from each lap by
inverting that same relation against the known lap time, which self-calibrates
against the API's units instead of relying on a track-length table.

The integrated gap is checked against the gap implied by the lap times, and the
discrepancy is reported in every comparison rather than hidden. In practice it
lands within about 0.1s over a 3.5s gap, and the derived track length comes
within about 1% of the real figure.

## Telemetry Access

- **Free Account**: Lap times and basic data
- **Pro Account**: Full CSV telemetry data with detailed analysis
- **Graceful Degradation**: Always shows what's available

## Troubleshooting

### Common Issues

**"spawn python ENOENT"**
- Python not found in PATH
- Find your Python path: `which python3`
- Use full path in Claude config: `/usr/bin/python3`
- Make sure `cwd` points to your `src/` directory
- Use `args: ["__main__.py"]` not `args: ["-m", "__main__"]`

**"GARAGE61_TOKEN environment variable is required"**
- Token not set in Claude config
- Make sure the `env` section has your actual token

**"Car/Track not found"**
- Use `list_cars` and `list_tracks` tools first
- Names must match exactly (fuzzy search provides suggestions)

**"No lap data found"**
- You haven't driven this car/track combination yet
- Or the data isn't accessible with your account level

### Local Testing with MCP Inspector

The best way to test your MCP server locally is using the official MCP Inspector:

#### 1. Install MCP Inspector
```bash
npx @modelcontextprotocol/inspector
```

#### 2. Test the Server
```bash
# From the project root directory
cd /path/to/garage61_mcp

# Set your API token
export GARAGE61_TOKEN=your-garage61-token-here

# Option A: Run from src directory (recommended)
cd src
npx @modelcontextprotocol/inspector python3 __main__.py

# Option B: Use direct path from root
npx @modelcontextprotocol/inspector python3 src/__main__.py

# Option C: After pip install -e .
pip install -e .
npx @modelcontextprotocol/inspector garage61-mcp
```

This will:
- Start your MCP server
- Open a web interface at `http://localhost:5173`
- Let you test all tools interactively
- Show real-time logs and responses

#### 3. Test Individual Tools

In the MCP Inspector web interface, you can:

1. **Test discovery tools**:
   - `list_cars` with search terms like "porsche" or "gt3"
   - `list_tracks` with search terms like "spa" or "nurburgring"

2. **Test telemetry tools**:
   - `get_my_fastest_lap` with exact car/track names
   - `list_my_laps` and `compare_my_laps` with exact car/track names

3. **View detailed logs** to debug any issues

#### 4. Alternative: Direct Server Testing
```bash
# Test the server directly (without inspector)
cd src
GARAGE61_TOKEN=your-token python3 __main__.py

# Or after pip install -e .
GARAGE61_TOKEN=your-token garage61-mcp
```

#### 5. Debug Common Issues

**Server won't start:**
```bash
# Check your token is set
echo $GARAGE61_TOKEN

# Test Python can import dependencies
python3 -c "import mcp, httpx, pydantic; print('Dependencies OK')"

# Check the server loads
python3 -c "from src import server; print('Server loads OK')"
```

**API connection issues:**
```bash
# Test API connectivity
python3 -c "
import httpx
response = httpx.get('https://garage61.net/api/v1/cars', 
                     headers={'Authorization': 'Bearer YOUR_TOKEN'})
print(f'API Status: {response.status_code}')
"
```

## Development

### Project Structure
```
garage61_mcp/
├── src/                 # installs as the `garage61_mcp` package
│   ├── __init__.py
│   ├── __main__.py      # Entry point
│   ├── server.py        # MCP server and tool dispatch
│   ├── api_client.py    # Garage61 API client
│   ├── cache.py         # Car/track fuzzy search
│   ├── telemetry.py     # Parsing, resampling, delta-time maths
│   ├── formatting.py    # Analysis results -> Markdown
│   └── tools.py         # MCP tool implementations
├── pyproject.toml       # Package config
├── install.py           # Auto-installer
├── CLAUDE.md            # Development docs
└── README.md
```

### Testing Workflow

1. **Install dependencies:**
   ```bash
   pip install -e .
   ```

2. **Test with MCP Inspector:**
   ```bash
   export GARAGE61_TOKEN=your-token
   cd src
   npx @modelcontextprotocol/inspector python3 __main__.py
   ```

3. **Test individual components:**
   ```bash
   # Test API client
   cd src
   python3 -c "from api_client import create_client; print('API client OK')"
   
   # Test cache system
   python3 -c "from cache import get_cache; print('Cache OK')"
   
   # Test tools
   python3 -c "from tools import list_cars; print('Tools OK')"
   ```

4. **Test with Claude Desktop:**
   - Add to Claude Desktop config
   - Restart Claude Desktop
   - Test with natural language queries

### Requirements
- **Python 3.10+** (required for MCP package)
- Node.js (for MCP Inspector)
- Garage61 account ([garage61.net](https://garage61.net))
- Claude Desktop

### Contributing
1. Fork the repository
2. Create a feature branch
3. Make your changes
4. **Test with MCP Inspector** 
5. Test with Claude Desktop
6. Submit a pull request

## License

MIT License - see LICENSE file for details.