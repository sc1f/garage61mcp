"""MCP server for Garage61 telemetry integration."""

import asyncio
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent
from tools import (
    get_my_fastest_lap,
    get_team_fastest_lap,
    list_cars,
    list_tracks,
    MY_FASTEST_LAP_TOOL,
    TEAM_FASTEST_LAP_TOOL,
    LIST_CARS_TOOL,
    LIST_TRACKS_TOOL
)
from api_client import initialize_cache

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """Main entry point for the MCP server."""
    logger.info("Starting Garage61 MCP server")
    
    # Initialize cache before starting the server
    try:
        await initialize_cache()
    except Exception as e:
        logger.error(f"Failed to initialize cache: {str(e)}")
        logger.warning("Server will continue but car/track resolution may fail")
    
    server = Server("garage61-mcp")
    
    @server.list_tools()
    async def list_tools():
        """List available tools."""
        logger.debug("Listing available tools")
        return [
            LIST_CARS_TOOL,
            LIST_TRACKS_TOOL,
            MY_FASTEST_LAP_TOOL,
            TEAM_FASTEST_LAP_TOOL
        ]
    
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Handle tool calls."""
        logger.info(f"Tool called: {name} with arguments: {arguments}")
        
        try:
            if name == "list_cars":
                logger.debug("Calling list_cars")
                search_term = arguments.get("search_term", "")
                show_legacy = arguments.get("show_legacy", False)
                return await list_cars(search_term, show_legacy)
            elif name == "list_tracks":
                logger.debug("Calling list_tracks")
                search_term = arguments.get("search_term", "")
                return await list_tracks(search_term)
            elif name in ["get_my_fastest_lap", "get_team_fastest_lap"]:
                # These tools require car and track parameters
                car = arguments.get("car", "")
                track = arguments.get("track", "")
                
                if not car or not track:
                    logger.error("Missing required parameters")
                    return [TextContent(
                        type="text", 
                        text="Error: Both 'car' and 'track' parameters are required. Use list_cars and list_tracks tools first to find exact names."
                    )]
                
                if name == "get_my_fastest_lap":
                    logger.debug("Calling get_my_fastest_lap")
                    return await get_my_fastest_lap(car, track)
                elif name == "get_team_fastest_lap":
                    logger.debug("Calling get_team_fastest_lap")
                    return await get_team_fastest_lap(car, track)
            else:
                logger.error(f"Unknown tool: {name}")
                return [TextContent(
                    type="text",
                    text=f"Unknown tool: {name}"
                )]
        except Exception as e:
            logger.error(f"Error in tool execution: {str(e)}", exc_info=True)
            raise
    
    # Run the server using stdio transport
    logger.info("Starting stdio server")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())