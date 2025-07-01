# Garage61 MCP Server

## Overview
This is an MCP (Model Context Protocol) server that provides access to iRacing telemetry data through the Garage61 API. It allows Claude and other AI assistants to fetch lap times, telemetry data, and racing statistics for cars and tracks in iRacing.

## Features
- **Smart Car/Track Discovery**: Use `list_cars` and `list_tracks` to find available vehicles and circuits
- **Fuzzy Name Matching**: Don't need exact names - the system will suggest close matches
- **Modern Car Prioritization**: Automatically prioritizes current generation cars (e.g., 992 over 991 Porsche)
- **Track Variant Support**: Handles multiple track configurations with intelligent preference scoring
- **Telemetry Access**: Get lap times and telemetry data (Pro plan features gracefully degrade)
- **Multiple Data Sources**: Access your personal laps, team laps, or overall fastest laps

## Tools Available
1. **`list_cars`** - Find available cars with modern prioritization
2. **`list_tracks`** - Find available tracks with all variants
3. **`get_my_fastest_lap`** - Your personal fastest lap with telemetry data
4. **`get_world_fastest_lap`** - World record lap with telemetry data

## Setup
1. Get a Garage61 API token from https://garage61.net
2. Create a `.env` file:
   ```
   GARAGE61_TOKEN=your-token-here
   ```
3. Install dependencies: `pip install -r requirements.txt`
4. Run: `python -m garage61_mcp`

## Usage Tips
- Always use `list_cars` and `list_tracks` first to get exact names
- Track names include variants: "Lime Rock Park - Grand Prix"
- Car suggestions prioritize modern versions automatically
- Error messages will guide you if names don't match
- Telemetry requires Pro plan but lap times work with free accounts

## Development
- **API Client**: `src/api_client.py` - Handles Garage61 API integration
- **Cache System**: `src/cache.py` - Smart fuzzy matching and prioritization
- **Tools**: `src/tools.py` - MCP tool implementations
- **Server**: `src/server.py` - MCP server setup

## Testing Commands
```bash
# Test with specific commands
python -m garage61_mcp

# Run linting (if available)
npm run lint

# Run type checking (if available)  
npm run typecheck
```

## Error Handling
The system provides clear error messages:
- **Car/Track not found**: Suggests similar names and directs to list tools
- **No lap data**: Explains this means you haven't driven that combination yet
- **Pro plan required**: Gracefully degrades while still showing lap times

## API Integration
- Base URL: `https://garage61.net/api/v1`
- Authentication: Bearer token
- Rate limiting: Handled by API
- Caching: Smart local cache for cars/tracks data