"""MCP server for Garage61 telemetry integration."""

import asyncio
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent

from api_client import initialize_cache
from tools import (
    ALL_TOOLS,
    analyze_consistency,
    analyze_telemetry_range,
    analyze_telemetry_sector,
    analyze_worst_sections,
    compare_my_laps,
    compare_my_telemetry_to_team,
    compare_to_driver,
    get_channel_window,
    get_my_fastest_lap,
    get_team_fastest_lap,
    list_cars,
    list_drivers,
    list_my_laps,
    list_tracks,
)

logger = logging.getLogger(__name__)


def _missing_car_track() -> list[TextContent]:
    return [
        TextContent(
            type="text",
            text=(
                "**Error**: Both 'car' and 'track' are required. Use the "
                "list_cars and list_tracks tools first to find the exact names."
            ),
        )
    ]


async def _dispatch(name: str, arguments: dict) -> list[TextContent]:
    """Route one tool call to its implementation."""
    if name == "list_cars":
        return await list_cars(
            arguments.get("search_term", ""), arguments.get("show_legacy", False)
        )

    if name == "list_tracks":
        return await list_tracks(arguments.get("search_term", ""))

    # Everything below is scoped to a car/track combination.
    car = (arguments.get("car") or "").strip()
    track = (arguments.get("track") or "").strip()
    if not car or not track:
        return _missing_car_track()

    if name == "list_my_laps":
        return await list_my_laps(car, track, arguments.get("clean_only", False))

    if name == "compare_my_laps":
        return await compare_my_laps(
            car,
            track,
            arguments.get("reference") or "fastest",
            arguments.get("compared") or "latest",
        )

    if name == "list_drivers":
        return await list_drivers(car, track)

    if name == "compare_to_driver":
        target = (arguments.get("driver") or "").strip()
        if not target:
            return [
                TextContent(
                    type="text",
                    text=(
                        "**Error**: 'driver' is required. Use `list_drivers` to "
                        "see who has laps on this car/track."
                    ),
                )
            ]
        return await compare_to_driver(car, track, target)

    if name == "analyze_consistency":
        return await analyze_consistency(car, track)

    if name == "get_channel_window":
        corner_number = arguments.get("corner_number")
        return await get_channel_window(
            car,
            track,
            arguments.get("start_pct", 0),
            arguments.get("end_pct", 100),
            arguments.get("channels"),
            int(arguments.get("points", 40)),
            int(corner_number) if corner_number is not None else None,
        )

    if name == "get_my_fastest_lap":
        return await get_my_fastest_lap(car, track)

    if name == "get_team_fastest_lap":
        return await get_team_fastest_lap(car, track)

    if name == "compare_my_telemetry_to_team":
        return await compare_my_telemetry_to_team(car, track)

    if name == "analyze_telemetry_range":
        return await analyze_telemetry_range(
            car, track, arguments.get("start_pct", 0), arguments.get("end_pct", 100)
        )

    if name == "analyze_telemetry_sector":
        return await analyze_telemetry_sector(car, track, int(arguments.get("sector", 1)))

    if name == "analyze_worst_sections":
        return await analyze_worst_sections(car, track)

    logger.error(f"Unknown tool requested: {name}")
    return [TextContent(type="text", text=f"**Error**: Unknown tool '{name}'")]


async def main():
    """Main entry point for the MCP server."""
    logger.info("Starting Garage61 MCP server")

    try:
        await initialize_cache()
    except Exception as e:
        logger.error(f"Failed to initialize cache: {e}")
        logger.warning("Server will continue but car/track resolution may fail")

    server = Server("garage61-mcp")

    @server.list_tools()
    async def list_tools():
        """List available tools."""
        return ALL_TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Handle tool calls."""
        logger.info(f"Tool called: {name} with arguments: {arguments}")
        try:
            return await _dispatch(name, arguments or {})
        except Exception as e:
            # Surfacing the failure beats raising, which shows the user an
            # opaque transport error with no indication of what went wrong.
            logger.error(f"Error in tool execution: {e}", exc_info=True)
            return [TextContent(type="text", text=f"**Error**: {name} failed: {e}")]

    logger.info("Starting stdio server")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
