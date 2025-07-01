"""MCP tools for Garage61 telemetry data."""

import logging
from typing import Any, Dict
from mcp.types import Tool, TextContent
from api_client import create_client
from cache import get_cache

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


async def get_my_fastest_lap(car: str, track: str) -> list[TextContent]:
    """Get your personal fastest lap and telemetry for a specific car and track combination."""
    logger.debug(f"get_my_fastest_lap tool called with car: {car}, track: {track}")
    try:
        client = create_client()
        result = await client.get_user_fastest_lap(car, track)
        
        lap = result.get("lap")
        telemetry_csv = result.get("telemetry_csv")
        pro_required = result.get("pro_required", False)
        
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

**Telemetry Data Available** ✅
*Full CSV telemetry data is accessible for detailed analysis.*

**Telemetry Preview:**
```csv
{chr(10).join(telemetry_csv.split(chr(10))[:5]) if telemetry_csv else ''}...
```"""
        elif pro_required:
            response += """

**Telemetry Requires Pro Plan** 🔒
*Upgrade to Garage61 Pro at https://garage61.net to access detailed telemetry data.*"""
        else:
            response += """

**No Telemetry Available** ℹ️
*Telemetry data is not available for this lap.*"""
        
        return [TextContent(type="text", text=response)]
        
    except Exception as e:
        logger.error(f"Exception in get_my_fastest_lap: {str(e)}", exc_info=True)
        error_message = f"Error: {str(e)}"
        return [TextContent(type="text", text=error_message)]



async def get_team_fastest_lap(car: str, track: str) -> list[TextContent]:
    """Get the fastest lap from your team (including yourself) for a specific car and track combination."""
    try:
        client = create_client()
        
        # Get both team fastest and user fastest laps
        team_result = await client.get_overall_fastest_lap(car, track)
        
        # Try to get user's fastest lap for comparison
        user_result = None
        try:
            user_result = await client.get_user_fastest_lap(car, track)
        except ValueError:
            # User hasn't driven this car/track combination
            pass
        
        # Check if team result is valid
        if not isinstance(team_result, dict):
            return [TextContent(type="text", text=f"Unexpected response type: {type(team_result).__name__}. Response: {str(team_result)}")]
        
        team_lap = team_result.get("lap")
        telemetry_csv = team_result.get("telemetry_csv")
        pro_required = team_result.get("pro_required", False)
        
        if not team_lap:
            return [TextContent(type="text", text=f"Missing lap data in response. Available keys: {list(team_result.keys())}")]
        
        # Determine if user is the fastest
        user_is_fastest = False
        user_lap_time = None
        
        if user_result and isinstance(user_result, dict) and user_result.get("lap"):
            user_lap_time = user_result["lap"]["lap_time"]
            user_driver_name = user_result["lap"]["driver"]
            
            # Check if the fastest team lap is actually the user's lap
            if (team_lap["lap_time"] == user_lap_time and 
                team_lap["driver"] == user_driver_name):
                user_is_fastest = True
        
        # Build the response title and driver info
        if user_is_fastest:
            title = f"**Team Fastest Lap: {team_lap['car']} at {team_lap['track']} (Your Lap!)**"
            driver_info = f"**Driver:** {team_lap['driver']} (You) 🏆"
        else:
            title = f"**Team Fastest Lap: {team_lap['car']} at {team_lap['track']}**"
            driver_info = f"**Driver:** {team_lap['driver']} (Teammate)"
        
        response = f"""{title}

{driver_info}
**Lap Time:** {team_lap['lap_time']:.3f} seconds
**Lap ID:** {team_lap['id']}"""
        
        # Add comparison with user's lap if available and user is not fastest
        if user_result and not user_is_fastest and user_lap_time:
            time_diff = user_lap_time - team_lap['lap_time']
            response += f"""

**Your Personal Best:** {user_lap_time:.3f} seconds (+{time_diff:.3f}s)"""
        elif not user_result:
            response += f"""

**Note:** You haven't recorded a lap with this car/track combination yet."""
        
        if telemetry_csv:
            response += f"""

**Telemetry Data Available** ✅
*Full CSV telemetry data is accessible for detailed analysis.*

**Telemetry Preview:**
```csv
{chr(10).join(telemetry_csv.split(chr(10))[:5]) if telemetry_csv else ''}...
```"""
        elif pro_required:
            response += """

**Telemetry Requires Pro Plan** 🔒
*Upgrade to Garage61 Pro at https://garage61.net to access detailed telemetry data.*"""
        else:
            response += """

**No Telemetry Available** ℹ️
*Telemetry data is not available for this lap.*"""
        
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
MY_FASTEST_LAP_TOOL = Tool(
    name="get_my_fastest_lap",
    description="Get your personal fastest lap and telemetry data for a specific car and track combination. Shows your personal best time with available telemetry data. Use list_cars and list_tracks tools first to get exact names.",
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

TEAM_FASTEST_LAP_TOOL = Tool(
    name="get_team_fastest_lap",
    description="Get the fastest lap from your team (including yourself) for a specific car and track combination. Shows whether you or a teammate holds the team record, with comparison data. Use list_cars and list_tracks tools first to get exact names.",
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