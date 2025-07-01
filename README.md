# Garage61 MCP Server

A Model Context Protocol (MCP) server that connects Claude Desktop to Garage61's iRacing telemetry API.

## Features

- **Fastest Lap Lookup**: Get the fastest recorded lap for any car/track combination
- **Telemetry Summary**: View key metrics like top speed, max throttle, and max brake
- **Claude Integration**: Ask Claude natural language questions about lap times and telemetry

## Setup

### 1. Install Dependencies

```bash
pip install -e .
```

### 2. Get Garage61 API Token

1. Sign up at [Garage61](https://garage61.net)
2. Generate an API token from your account settings
3. Copy the `.env.example` file to `.env`:
   ```bash
   cp .env.example .env
   ```
4. Add your token to the `.env` file:
   ```
   GARAGE61_TOKEN=your-actual-token-here
   ```

### 3. Configure Claude Desktop

Add this to your Claude Desktop configuration file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "garage61": {
      "command": "python",
      "args": ["-m", "garage61_mcp"],
      "cwd": "/absolute/path/to/garage61_mcp"
    }
  }
}
```

Replace `/absolute/path/to/garage61_mcp` with the actual path to this directory.

### 4. Restart Claude Desktop

Close and reopen Claude Desktop to load the MCP server.

## Usage

Once configured, you can ask Claude questions like:

- "What's the fastest lap for Mercedes AMG GT3 at Spa-Francorchamps?"
- "Show me the lap record for BMW M4 GT3 at Nürburgring"
- "Get the fastest lap telemetry for Porsche 911 GT3 R at Monza"
- "What's my personal best lap at Spa with the Mercedes?"
- "Show me the overall fastest lap from my team at Nürburgring"

## API Tools

The server provides three MCP tools:

### `get_fastest_lap_telemetry`

**Parameters:**
- `car` (string, required): Car name (e.g., "Mercedes AMG GT3")
- `track` (string, required): Track name (e.g., "Spa-Francorchamps")

**Returns:**
- Driver name
- Lap time
- Top speed, max throttle, max brake values

### `get_user_fastest_lap`

**Parameters:**
- `car` (string, required): Car name (e.g., "Mercedes AMG GT3")
- `track` (string, required): Track name (e.g., "Spa-Francorchamps")

**Returns:**
- Your personal fastest lap information
- Lap time
- Full telemetry data in CSV format

### `get_overall_fastest_lap`

**Parameters:**
- `car` (string, required): Car name (e.g., "Mercedes AMG GT3")
- `track` (string, required): Track name (e.g., "Spa-Francorchamps")

**Returns:**
- Overall fastest lap from all accessible drivers/teams
- Driver name and lap time
- Full telemetry data in CSV format

## Development

### Project Structure

```
garage61_mcp/
├── src/garage61_mcp/
│   ├── __init__.py
│   ├── __main__.py      # Entry point
│   ├── server.py        # MCP server setup
│   ├── api_client.py    # Garage61 API client
│   └── tools.py         # MCP tool implementation
├── pyproject.toml
├── .env.example
└── README.md
```

### Testing

```bash
# Test the server directly
python -m garage61_mcp

# Or run with specific environment
GARAGE61_TOKEN=your-token python -m garage61_mcp
```

## Troubleshooting

- **"GARAGE61_TOKEN environment variable is required"**: Make sure your `.env` file exists and contains your API token
- **"Car 'X' not found"**: Check the exact car name in Garage61's database - names must match exactly
- **"Track 'X' not found"**: Check the exact track name in Garage61's database
- **"No laps found"**: The car/track combination may not have any recorded laps with telemetry data

## Requirements

- Python 3.10+
- Garage61 account with API access
- Claude Desktop