"""MCP tools for Garage61 telemetry data."""

import logging
from typing import Any, Dict
from mcp.types import Tool, TextContent
from .api_client import create_client
from .cache import get_cache

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


async def get_fastest_lap_telemetry(car: str, track: str) -> list[TextContent]:
    """Get the fastest lap telemetry for a specific car and track combination."""
    try:
        client = create_client()
        result = await client.get_fastest_lap_with_telemetry(car, track)
        
        lap = result["lap"]
        telemetry = result.get("telemetry")
        pro_required = result.get("pro_required", False)
        
        # Build response starting with lap info
        response = f"""**Fastest Lap: {lap['car']} at {lap['track']}**

**Driver:** {lap['driver']}
**Lap Time:** {lap['lap_time']:.3f} seconds"""
        
        if telemetry:
            response += f"""
**Top Speed:** {telemetry['speed']['max']:.1f} km/h
**Max Throttle:** {telemetry['throttle']['max']:.1f}%
**Max Brake:** {telemetry['brake']['max']:.1f}%"""
        elif pro_required:
            response += """

**Note:** Detailed telemetry data requires a Garage61 Pro plan. Upgrade at https://garage61.net to access telemetry analysis."""
        
        return [TextContent(type="text", text=response)]
        
    except Exception as e:
        error_message = f"Error: {str(e)}"
        return [TextContent(type="text", text=error_message)]


async def get_user_fastest_lap(car: str, track: str) -> list[TextContent]:
    """Get the current user's fastest lap telemetry for a specific car and track combination."""
    logger.debug(f"get_user_fastest_lap tool called with car: {car}, track: {track}")
    try:
        logger.debug("Creating API client")
        client = create_client()
        
        logger.debug("Calling API client.get_user_fastest_lap")
        result = await client.get_user_fastest_lap(car, track)
        
        logger.debug(f"API result type: {type(result)}")
        logger.debug(f"API result: {result}")
        
        # Check if result is a dictionary as expected
        if not isinstance(result, dict):
            logger.error(f"Unexpected response type: {type(result).__name__}")
            return [TextContent(type="text", text=f"Unexpected response type: {type(result).__name__}. Response: {str(result)}")]
        
        lap = result.get("lap")
        telemetry_csv = result.get("telemetry_csv")
        pro_required = result.get("pro_required", False)
        
        logger.debug(f"Lap data present: {lap is not None}")
        logger.debug(f"Telemetry CSV present: {telemetry_csv is not None}")
        logger.debug(f"Pro required: {pro_required}")
        
        if not lap:
            logger.error(f"Missing lap data in response. Available keys: {list(result.keys())}")
            return [TextContent(type="text", text=f"Missing lap data in response. Available keys: {list(result.keys())}")]
        
        # Format response with lap info, include telemetry if available
        response = f"""**Your Fastest Lap: {lap['car']} at {lap['track']}**

**Driver:** {lap['driver']}
**Lap Time:** {lap['lap_time']:.3f} seconds
**Lap ID:** {lap['id']}"""
        
        if telemetry_csv:
            response += f"""

**Telemetry Data (CSV):**
```csv
{telemetry_csv}
```"""
        elif pro_required:
            response += """

**Note:** Detailed telemetry data requires a Garage61 Pro plan. Upgrade at https://garage61.net to access telemetry analysis."""
        
        logger.debug("Successfully formatted response")
        return [TextContent(type="text", text=response)]
        
    except Exception as e:
        logger.error(f"Exception in get_user_fastest_lap: {str(e)}", exc_info=True)
        error_message = f"Error: {str(e)}"
        return [TextContent(type="text", text=error_message)]


async def get_overall_fastest_lap(car: str, track: str) -> list[TextContent]:
    """Get the overall fastest lap from all accessible drivers/teams for a specific car and track combination."""
    try:
        client = create_client()
        result = await client.get_overall_fastest_lap(car, track)
        
        # Check if result is a dictionary as expected
        if not isinstance(result, dict):
            return [TextContent(type="text", text=f"Unexpected response type: {type(result).__name__}. Response: {str(result)}")]
        
        lap = result.get("lap")
        telemetry_csv = result.get("telemetry_csv")
        pro_required = result.get("pro_required", False)
        
        if not lap:
            return [TextContent(type="text", text=f"Missing lap data in response. Available keys: {list(result.keys())}")]
        
        # Format response with lap info, include telemetry if available
        response = f"""**Overall Fastest Lap: {lap['car']} at {lap['track']}**

**Driver:** {lap['driver']}
**Lap Time:** {lap['lap_time']:.3f} seconds
**Lap ID:** {lap['id']}"""
        
        if telemetry_csv:
            response += f"""

**Telemetry Data (CSV):**
```csv
{telemetry_csv}
```"""
        elif pro_required:
            response += """

**Note:** Detailed telemetry data requires a Garage61 Pro plan. Upgrade at https://garage61.net to access telemetry analysis."""
        
        return [TextContent(type="text", text=response)]
        
    except Exception as e:
        error_message = f"Error: {str(e)}"
        return [TextContent(type="text", text=error_message)]


async def list_cars(search_term: str = "", show_legacy: bool = False) -> list[TextContent]:
    """List available cars, optionally filtered by search term. Modern cars are prioritized unless legacy is requested."""
    logger.debug(f"list_cars called with search_term: '{search_term}', show_legacy: {show_legacy}")
    try:
        cache = get_cache()
        
        # Check if search term indicates legacy cars wanted
        if search_term:
            search_lower = search_term.lower()
            legacy_keywords = ['legacy', 'old', 'classic', 'vintage', '991', 'gen 1', 'generation 1']
            if any(keyword in search_lower for keyword in legacy_keywords):
                show_legacy = True
                logger.debug("Legacy keywords detected in search term")
        
        if search_term:
            # Filter cars by search term
            filtered_cars = []
            search_lower = search_term.lower()
            
            for car in cache.cars:
                car_name = car.get("name", "").lower()
                if search_lower in car_name or any(word in car_name for word in search_lower.split()):
                    filtered_cars.append(car)
            
            if filtered_cars:
                # Sort by relevance (modern cars first unless legacy requested)
                sorted_cars = cache._sort_cars_by_relevance(filtered_cars, show_legacy)
                car_names = [car["name"] for car in sorted_cars[:20]]
                
                priority_note = " (modern cars prioritized)" if not show_legacy else " (including legacy cars)"
                response = f"**Cars matching '{search_term}'{priority_note}:**\n\n" + "\n".join(f"• {car}" for car in car_names)
                
                if len(sorted_cars) > 20:
                    response += f"\n\n... and {len(sorted_cars) - 20} more cars"
                    
                if not show_legacy and any(cache._is_legacy_car(car["name"]) for car in filtered_cars):
                    response += "\n\n*Note: Some legacy cars were filtered out. Use 'legacy' in search term to see all versions.*"
            else:
                # Try fuzzy matching
                suggestions = cache.get_car_suggestions(search_term, limit=10, include_legacy=show_legacy)
                if suggestions:
                    response = f"**No exact matches for '{search_term}'. Did you mean:**\n\n" + "\n".join(f"• {car}" for car in suggestions)
                else:
                    response = f"No cars found matching '{search_term}'"
        else:
            # List all cars with prioritization
            if show_legacy:
                sorted_cars = cache._sort_cars_by_relevance(cache.cars, True)
                note = " (all cars including legacy)"
            else:
                sorted_cars = cache._sort_cars_by_relevance(cache.cars, False)
                note = " (modern cars prioritized)"
                
            if sorted_cars:
                car_names = [car["name"] for car in sorted_cars[:50]]
                response = f"**Available Cars ({len(sorted_cars)} total{note}):**\n\n" + "\n".join(f"• {car}" for car in car_names)
                if len(sorted_cars) > 50:
                    response += f"\n\n... and {len(sorted_cars) - 50} more cars. Use a search term to filter."
                    
                if not show_legacy:
                    legacy_count = len([car for car in cache.cars if cache._is_legacy_car(car["name"])])
                    if legacy_count > 0:
                        response += f"\n\n*Note: {legacy_count} legacy cars filtered out. Add 'legacy' to search to see all versions.*"
            else:
                response = "No cars available"
        
        return [TextContent(type="text", text=response)]
        
    except Exception as e:
        logger.error(f"Exception in list_cars: {str(e)}", exc_info=True)
        return [TextContent(type="text", text=f"Error listing cars: {str(e)}")]


async def list_tracks(search_term: str = "") -> list[TextContent]:
    """List available tracks with variant grouping, optionally filtered by search term."""
    logger.debug(f"list_tracks called with search_term: '{search_term}'")
    try:
        cache = get_cache()
        
        if search_term:
            # Filter tracks by search term
            filtered_tracks = []
            search_lower = search_term.lower()
            
            for track in cache.tracks:
                track_name = track.get("name", "").lower()
                variant = track.get("variant", "").lower()
                full_name = f"{track_name} {variant}".strip().lower()
                
                if (search_lower in track_name or 
                    any(word in track_name for word in search_lower.split()) or
                    search_lower in full_name):
                    filtered_tracks.append(track)
            
            if filtered_tracks:
                # Group by base name
                grouped = cache._group_tracks_by_base_name(filtered_tracks)
                
                # Show all variants with exact names to use
                response_lines = [f"**Tracks matching '{search_term}':**\n"]
                for base_name, variants in sorted(grouped.items()):
                    response_lines.append(f"**{base_name}:**")
                    # Sort variants by preference score
                    sorted_variants = sorted(variants, key=lambda v: cache._get_track_variant_score(v.get("variant", "")), reverse=True)
                    for variant in sorted_variants:
                        formatted_name = cache._format_track_name_with_variant(variant)
                        score = cache._get_track_variant_score(variant.get("variant", ""))
                        # Show the exact name to use for telemetry tools
                        response_lines.append(f"  • **{formatted_name}** (preference: {score})")
                    response_lines.append("")
                
                response = "\n".join(response_lines)
                if len(grouped) > 15:
                    response += f"\n\n... showing first 15 tracks. Use a more specific search term to filter."
            else:
                # Try fuzzy matching
                suggestions = cache.get_track_suggestions(search_term, limit=10)
                if suggestions:
                    response = f"**No exact matches for '{search_term}'. Did you mean:**\n\n" + "\n".join(f"• {track}" for track in suggestions)
                else:
                    response = f"No tracks found matching '{search_term}'"
        else:
            # List all tracks grouped by base name
            grouped = cache._group_tracks_by_base_name(cache.tracks)
            
            # Show unique tracks with all variants and exact names
            response_lines = [f"**Available Tracks ({len(grouped)} unique tracks, {len(cache.tracks)} total variants):**\n"]
            for base_name, variants in sorted(list(grouped.items())[:30]):
                if len(variants) == 1:
                    # Single variant
                    track = variants[0]
                    formatted_name = cache._format_track_name_with_variant(track)
                    response_lines.append(f"• **{formatted_name}**")
                else:
                    # Multiple variants - show all with exact names
                    response_lines.append(f"**{base_name}:**")
                    sorted_variants = sorted(variants, key=lambda v: cache._get_track_variant_score(v.get("variant", "")), reverse=True)
                    for variant in sorted_variants[:3]:  # Show top 3 variants
                        formatted_name = cache._format_track_name_with_variant(variant)
                        response_lines.append(f"  • **{formatted_name}**")
                    if len(variants) > 3:
                        response_lines.append(f"  • ... and {len(variants) - 3} more variants")
                    response_lines.append("")
            
            if len(grouped) > 30:
                response_lines.append(f"\n... and {len(grouped) - 30} more tracks")
                
            response = "\n".join(response_lines)
            response += "\n\n*Tip: Use the exact track names shown above (in bold) for telemetry tools. Search for specific tracks to see all variants.*"
        
        return [TextContent(type="text", text=response)]
        
    except Exception as e:
        logger.error(f"Exception in list_tracks: {str(e)}", exc_info=True)
        return [TextContent(type="text", text=f"Error listing tracks: {str(e)}")]


# Tool definitions for MCP
FASTEST_LAP_TELEMETRY_TOOL = Tool(
    name="get_fastest_lap_telemetry",
    description="Get the fastest lap and basic telemetry data for a specific car and track combination. IMPORTANT: Use list_cars and list_tracks tools first to get exact names with proper formatting.",
    inputSchema={
        "type": "object",
        "properties": {
            "car": {
                "type": "string",
                "description": "Exact car name from list_cars tool (e.g., 'Global Mazda MX-5 Cup', 'BMW M4 GT3')"
            },
            "track": {
                "type": "string", 
                "description": "Exact track name with variant from list_tracks tool (e.g., 'Lime Rock Park - Grand Prix', 'Nürburgring Nordschleife - Combined')"
            }
        },
        "required": ["car", "track"]
    }
)

USER_FASTEST_LAP_TOOL = Tool(
    name="get_user_fastest_lap",
    description="Get the current user's personal fastest lap and telemetry data for a specific car and track combination. IMPORTANT: Use list_cars and list_tracks tools first to get exact names with proper formatting (including variants like 'Track Name - Variant').",
    inputSchema={
        "type": "object",
        "properties": {
            "car": {
                "type": "string",
                "description": "Exact car name from list_cars tool (e.g., 'Global Mazda MX-5 Cup', 'BMW M4 GT3')"
            },
            "track": {
                "type": "string", 
                "description": "Exact track name with variant from list_tracks tool (e.g., 'Lime Rock Park - Grand Prix', 'Nürburgring Nordschleife - Combined')"
            }
        },
        "required": ["car", "track"]
    }
)

OVERALL_FASTEST_LAP_TOOL = Tool(
    name="get_overall_fastest_lap",
    description="Get the overall fastest lap from all accessible drivers and teams for a specific car and track combination. IMPORTANT: Use list_cars and list_tracks tools first to get exact names with proper formatting.",
    inputSchema={
        "type": "object",
        "properties": {
            "car": {
                "type": "string",
                "description": "Exact car name from list_cars tool (e.g., 'Global Mazda MX-5 Cup', 'BMW M4 GT3')"
            },
            "track": {
                "type": "string", 
                "description": "Exact track name with variant from list_tracks tool (e.g., 'Lime Rock Park - Grand Prix', 'Nürburgring Nordschleife - Combined')"
            }
        },
        "required": ["car", "track"]
    }
)

LIST_CARS_TOOL = Tool(
    name="list_cars",
    description="List available cars with modern cars prioritized by default. Call this FIRST when user mentions a car to find exact names. Modern cars like '992' are preferred over legacy '991' unless user specifically requests legacy.",
    inputSchema={
        "type": "object",
        "properties": {
            "search_term": {
                "type": "string",
                "description": "Optional search term to filter cars (e.g., 'porsche', 'BMW', 'GT3'). Add 'legacy' to include older car versions. Leave empty to see all modern cars."
            },
            "show_legacy": {
                "type": "boolean",
                "description": "Set to true to include legacy/older car versions. Default false shows only modern cars.",
                "default": False
            }
        },
        "required": []
    }
)

LIST_TRACKS_TOOL = Tool(
    name="list_tracks", 
    description="List available tracks with all variants and exact names to use for telemetry tools. Call this FIRST when user mentions a track to get the properly formatted track names with variants (e.g., 'Track Name - Variant').",
    inputSchema={
        "type": "object",
        "properties": {
            "search_term": {
                "type": "string",
                "description": "Optional search term to filter tracks (e.g., 'lime rock', 'nurburgring', 'silverstone'). Leave empty to see all tracks."
            }
        },
        "required": []
    }
)