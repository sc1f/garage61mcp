# Garage61 MCP Server

A Model Context Protocol (MCP) server that connects Claude Desktop to Garage61's iRacing telemetry API. Get your personal best laps, world records, and telemetry data through natural language queries.

## Features

- 🏁 **Personal Best Laps**: Get your fastest times for any car/track combination
- 🌍 **World Records**: Access fastest laps from all accessible drivers/teams  
- 📊 **Telemetry Data**: View detailed CSV telemetry when available
- 🔍 **Smart Search**: Fuzzy matching for car and track names
- 🏎️ **Modern Cars**: Automatically prioritizes current generation vehicles
- 🏁 **Track Variants**: Intelligent handling of track configurations
- ⚡ **Graceful Degradation**: Works with both free and Pro Garage61 accounts

## Quick Install

### Option 1: Automated Setup (Recommended)

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

**Test your installation:**
```bash
# Test with MCP Inspector
export GARAGE61_TOKEN=your-token
npx @modelcontextprotocol/inspector python3 src/__main__.py
```

### Option 2: Manual Setup

1. **Install the package**:
   ```bash
   pip install -e .
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
         "args": ["-m", "__main__"],
         "cwd": "/absolute/path/to/garage61_mcp/src",
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

### Personal Performance
- *"What's my fastest lap with the Mazda MX-5 at Lime Rock Park?"*
- *"Show me my personal best at Nürburgring with the BMW M4 GT3"*
- *"Get my fastest lap telemetry for Porsche at Spa"*

### World Records & Comparisons  
- *"What's the world record for Mercedes AMG GT3 at Silverstone?"*
- *"Show me the fastest lap overall at Monza with the McLaren 720S"*
- *"Who has the fastest lap at Road America with the Audi R8?"*

### Discovery
- *"What cars are available that match 'porsche'?"*
- *"Show me all track variants for Nürburgring"*
- *"List modern GT3 cars"*

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

### `get_my_fastest_lap`
Get your personal fastest lap and telemetry.

**Parameters:**
- `car`: Exact car name from `list_cars`
- `track`: Exact track name from `list_tracks` 

**Returns:**
- Your fastest lap time
- Driver info and lap ID
- Telemetry data (if available/Pro plan)

### `get_world_fastest_lap`
Get the world record lap from accessible data.

**Parameters:**
- `car`: Exact car name from `list_cars`
- `track`: Exact track name from `list_tracks`

**Returns:**
- World record lap time
- Driver info and lap ID  
- Telemetry data (if available/Pro plan)

## Telemetry Access

- **Free Account**: Lap times and basic data
- **Pro Account**: Full CSV telemetry data with detailed analysis
- **Graceful Degradation**: Always shows what's available

## Troubleshooting

### Common Issues

**"spawn python ENOENT"**
- Python not found in PATH
- Try using full path: `/usr/bin/python3` or `/opt/homebrew/bin/python3`
- Run `which python3` to find your Python path

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

# Launch MCP Inspector with the server
npx @modelcontextprotocol/inspector python3 src/__main__.py
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
   - `get_world_fastest_lap` with exact car/track names

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
├── src/
│   ├── __init__.py
│   ├── __main__.py      # Entry point  
│   ├── server.py        # MCP server
│   ├── api_client.py    # Garage61 API
│   ├── cache.py         # Smart search
│   └── tools.py         # MCP tools
├── pyproject.toml       # Package config
├── install.py          # Auto-installer
├── CLAUDE.md           # Development docs
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
   npx @modelcontextprotocol/inspector python3 src/__main__.py
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
- Python 3.10+
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