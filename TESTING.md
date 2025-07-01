# Testing Garage61 MCP Server

Complete guide for testing the MCP server locally and debugging issues.

## Quick Start Testing

### 1. Set Up Environment
```bash
# Clone and install
git clone <your-repo>
cd garage61_mcp
pip install -e .

# Set your API token
export GARAGE61_TOKEN=your-garage61-token-from-garage61.net
```

### 2. Test with MCP Inspector (Recommended)
```bash
# Option A: Run from src directory (recommended)
cd src
npx @modelcontextprotocol/inspector python3 __main__.py

# Option B: Use direct path from root
npx @modelcontextprotocol/inspector python3 src/__main__.py

# Option C: After pip install -e .
pip install -e .
npx @modelcontextprotocol/inspector garage61-mcp
```

This opens `http://localhost:5173` with a web interface to:
- ✅ Test all MCP tools interactively
- 📊 View real-time logs and responses  
- 🔍 Debug tool parameters and outputs
- 📈 Monitor performance

### 3. Test Tool Flow

#### Step 1: Test Discovery Tools
```bash
# In MCP Inspector, try these tools:

list_cars:
  search_term: "porsche"
  
list_tracks:
  search_term: "spa"
```

#### Step 2: Test Telemetry Tools
```bash
# Use exact names from the discovery tools:

get_my_fastest_lap:
  car: "Porsche 911 GT3 R (992)"
  track: "Spa-Francorchamps - Grand Prix"

get_world_fastest_lap:
  car: "Porsche 911 GT3 R (992)" 
  track: "Spa-Francorchamps - Grand Prix"
```

## Manual Testing

### Direct Server Testing
```bash
# Test without MCP Inspector
cd src
GARAGE61_TOKEN=your-token python3 __main__.py

# Should show:
# "Starting Garage61 MCP server"
# "Cache initialization complete"
# Server running and waiting for connections
```

### Component Testing
```bash
# Test individual components
cd src

# API Client
python3 -c "
from api_client import create_client
import asyncio
async def test():
    client = create_client()
    cars = await client.get_cars()
    print(f'Found {len(cars.get('items', []))} cars')
asyncio.run(test())
"

# Cache System  
python3 -c "
from cache import get_cache
cache = get_cache()
print('Cache initialized')
"

# Tools
python3 -c "
from tools import list_cars
import asyncio
result = asyncio.run(list_cars('porsche'))
print(f'Tool result: {len(result)} responses')
"
```

## Debugging Common Issues

### 0. MCP Inspector Issues

**"Command not found" or module errors:**
```bash
# Install mcp-inspector first
npm install -g @modelcontextprotocol/inspector

# Or use npx (recommended)
npx @modelcontextprotocol/inspector --help

# Make sure you're in the right directory
cd src  # Then run: npx @modelcontextprotocol/inspector python3 __main__.py
# OR from root: npx @modelcontextprotocol/inspector python3 src/__main__.py
```

**"Cannot connect to server":**
```bash
# Check Python path
which python3

# Test the server manually first
cd src
python3 __main__.py
# Should show "Starting Garage61 MCP server" and stay running

# Then in another terminal:
cd src  
npx @modelcontextprotocol/inspector python3 __main__.py
```

### 1. "GARAGE61_TOKEN environment variable is required"
```bash
# Check token is set
echo $GARAGE61_TOKEN

# Set token for current session
export GARAGE61_TOKEN=your-actual-token

# Or set permanently in ~/.bashrc / ~/.zshrc
echo 'export GARAGE61_TOKEN=your-token' >> ~/.bashrc
```

### 2. "spawn python ENOENT" (Claude Desktop)
```bash
# Find your Python path
which python3
# Use full path in Claude config: "/usr/bin/python3"

# Or use the install script
python install.py
# This automatically detects the correct Python path
```

### 3. Module Import Errors
```bash
# Check dependencies
pip list | grep -E "(mcp|httpx|pydantic|dotenv)"

# Reinstall if needed  
pip install -e .

# Test imports
python3 -c "import mcp, httpx, pydantic; print('All imports OK')"
```

### 4. API Connection Issues
```bash
# Test API connectivity
python3 -c "
import httpx
try:
    response = httpx.get('https://garage61.net/api/v1/cars', 
                         headers={'Authorization': 'Bearer $GARAGE61_TOKEN'})
    print(f'API Status: {response.status_code}')
    if response.status_code == 401:
        print('❌ Invalid token')
    elif response.status_code == 200:
        print('✅ API connection OK')
except Exception as e:
    print(f'❌ Connection error: {e}')
"
```

### 5. "No laps found" vs "Car/Track not found"
```bash
# Test name resolution
cd src
python3 -c "
from cache import get_cache
from api_client import initialize_cache
import asyncio

async def test_names():
    await initialize_cache()
    cache = get_cache()
    
    # Test car search
    car_result = cache.find_car('porsche')
    print(f'Car search result: {car_result}')
    
    # Test track search  
    track_result = cache.find_track('spa')
    print(f'Track search result: {track_result}')

asyncio.run(test_names())
"
```

## Performance Testing

### Test Tool Response Times
```bash
# In MCP Inspector, time these operations:
# - list_cars: < 2 seconds
# - list_tracks: < 2 seconds  
# - get_my_fastest_lap: < 5 seconds
# - get_world_fastest_lap: < 5 seconds
```

### Test Error Handling
```bash
# Test with invalid inputs in MCP Inspector:

get_my_fastest_lap:
  car: "NonExistentCar"
  track: "InvalidTrack"
  
# Should return helpful error messages, not crashes
```

## Testing with Claude Desktop

### 1. Configure Claude Desktop

**Option A: Direct script execution (recommended)**
```json
{
  "mcpServers": {
    "garage61": {
      "command": "/usr/bin/python3",
      "args": ["__main__.py"],
      "cwd": "/absolute/path/to/garage61_mcp/src",
      "env": {
        "GARAGE61_TOKEN": "your-garage61-token"
      }
    }
  }
}
```

**Option B: After pip install -e .**
```json
{
  "mcpServers": {
    "garage61": {
      "command": "garage61-mcp",
      "env": {
        "GARAGE61_TOKEN": "your-garage61-token"
      }
    }
  }
}
```

**Option C: Full path (if python3 not in PATH)**
```json
{
  "mcpServers": {
    "garage61": {
      "command": "/opt/homebrew/bin/python3",
      "args": ["__main__.py"],
      "cwd": "/absolute/path/to/garage61_mcp/src",
      "env": {
        "GARAGE61_TOKEN": "your-garage61-token"
      }
    }
  }
}
```

### 2. Test Natural Language Queries
```
Ask Claude:
- "What cars are available that match 'porsche'?"
- "Show me track variants for Nürburgring"  
- "What's my fastest lap with the Mazda MX-5 at Lime Rock Park?"
- "Get the world record for Mercedes AMG GT3 at Silverstone"
```

### 3. Verify Tool Integration
- Claude should automatically call `list_cars`/`list_tracks` first
- Then use exact names for telemetry tools
- Should handle errors gracefully with helpful messages

## Automated Testing Script

Create a test script to run all checks:

```bash
#!/bin/bash
# test_all.sh

echo "🧪 Testing Garage61 MCP Server"

# Check environment
echo "📋 Checking environment..."
python3 --version
echo "Token set: $([ -n "$GARAGE61_TOKEN" ] && echo "✅" || echo "❌")"

# Test dependencies
echo "📦 Testing dependencies..."
python3 -c "import mcp, httpx, pydantic; print('✅ Dependencies OK')" || echo "❌ Dependency error"

# Test API
echo "🌐 Testing API connection..."  
cd src
python3 -c "
from api_client import create_client
import asyncio
async def test():
    try:
        client = create_client()
        cars = await client.get_cars()
        print('✅ API connection OK')
    except Exception as e:
        print(f'❌ API error: {e}')
asyncio.run(test())
"

# Test MCP server
echo "🖥️  Testing MCP server..."
timeout 10s python3 __main__.py &
sleep 5
kill $! 2>/dev/null
echo "✅ Server starts OK"

echo "🎉 Testing complete!"
```

Make it executable:
```bash
chmod +x test_all.sh
./test_all.sh
```

This comprehensive testing setup will help you debug any issues quickly and ensure the MCP server works reliably with Claude Desktop.