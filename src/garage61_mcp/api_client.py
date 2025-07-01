"""Garage61 API client for fetching iRacing telemetry data."""

import os
import logging
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel
from .cache import get_cache

# Set up logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class TrackInfo(BaseModel):
    """Track information from API."""
    id: int
    name: str


class CarInfo(BaseModel):
    """Car information from API."""
    id: int
    name: str


class DriverInfo(BaseModel):
    """Driver information from API."""
    slug: str
    firstName: str
    lastName: str
    
    @property
    def name(self) -> str:
        """Get full name from firstName and lastName."""
        return f"{self.firstName} {self.lastName}"
    
    @property
    def id(self) -> str:
        """Use slug as ID for backward compatibility."""
        return self.slug


class LapData(BaseModel):
    """Represents a lap record from the API."""
    id: str
    lapTime: float
    lapNumber: int
    startTime: str
    driver: Optional[DriverInfo]
    car: CarInfo
    track: TrackInfo
    clean: bool
    canViewTelemetry: bool
    sessionType: int


class TelemetryChannel(BaseModel):
    """Represents a telemetry channel with metadata."""
    unit: str
    samples: int
    max: float
    min: float


class TelemetryData(BaseModel):
    """Represents telemetry data for a lap."""
    speed: TelemetryChannel
    throttle: TelemetryChannel
    brake: TelemetryChannel


class Garage61Client:
    """Async client for Garage61 API."""
    
    def __init__(self, token: str, base_url: str = "https://garage61.net/api/v1"):
        self.token = token
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
    
    async def get_cars(self) -> List[Dict[str, Any]]:
        """Fetch available cars."""
        async with httpx.AsyncClient() as client:
            try:
                logger.debug(f"Fetching cars from {self.base_url}/cars")
                response = await client.get(
                    f"{self.base_url}/cars",
                    headers=self.headers
                )
                response.raise_for_status()
                data = response.json()
                logger.debug(f"Cars API response type: {type(data)}")
                
                if isinstance(data, dict) and 'items' in data:
                    items = data['items']
                    logger.debug(f"Cars API returned {len(items)} items")
                    logger.debug(f"Cars API sample (first 3): {items[:3] if len(items) > 0 else 'No items'}")
                    return data
                elif isinstance(data, list):
                    logger.debug(f"Cars API returned list with {len(data)} items")
                    logger.debug(f"Cars API sample (first 3): {data[:3]}")
                    return data
                else:
                    logger.debug(f"Cars API unexpected format: {data}")
                    return data
            except httpx.HTTPStatusError as e:
                logger.error(f"Failed to fetch cars: {e.response.status_code}")
                raise ValueError(f"Failed to fetch cars: {e.response.status_code}")
    
    async def get_tracks(self) -> List[Dict[str, Any]]:
        """Fetch available tracks."""
        async with httpx.AsyncClient() as client:
            try:
                logger.debug(f"Fetching tracks from {self.base_url}/tracks")
                response = await client.get(
                    f"{self.base_url}/tracks",
                    headers=self.headers
                )
                response.raise_for_status()
                data = response.json()
                logger.debug(f"Tracks API response type: {type(data)}")
                
                if isinstance(data, dict) and 'items' in data:
                    items = data['items']
                    logger.debug(f"Tracks API returned {len(items)} items")
                    logger.debug(f"Tracks API sample (first 3): {items[:3] if len(items) > 0 else 'No items'}")
                    return data
                elif isinstance(data, list):
                    logger.debug(f"Tracks API returned list with {len(data)} items")
                    logger.debug(f"Tracks API sample (first 3): {data[:3]}")
                    return data
                else:
                    logger.debug(f"Tracks API unexpected format: {data}")
                    return data
            except httpx.HTTPStatusError as e:
                logger.error(f"Failed to fetch tracks: {e.response.status_code}")
                raise ValueError(f"Failed to fetch tracks: {e.response.status_code}")
    
    async def find_car_id(self, car_name: str) -> Optional[int]:
        """Find car ID by name using cache."""
        cache = get_cache()
        result = cache.find_car(car_name)
        if result:
            car_id, exact_name = result
            logger.info(f"Resolved car '{car_name}' to '{exact_name}' (ID: {car_id})")
            return car_id
        return None
    
    async def find_track_id(self, track_name: str) -> Optional[int]:
        """Find track ID by name using cache."""
        cache = get_cache()
        result = cache.find_track(track_name)
        if result:
            track_id, exact_name = result
            logger.info(f"Resolved track '{track_name}' to '{exact_name}' (ID: {track_id})")
            return track_id
        return None
    
    async def get_laps(self, car_ids: List[int], track_ids: List[int], limit: int = 50, try_telemetry: bool = True) -> List[LapData]:
        """Fetch laps for specific car and track IDs. Tries with telemetry first, falls back without on 403."""
        async with httpx.AsyncClient() as client:
            # First try with telemetry if requested
            if try_telemetry:
                try:
                    params = {
                        "cars": car_ids,
                        "tracks": track_ids,
                        "limit": limit,
                        "group": "none",  # Return all laps, not just personal bests
                        "seeTelemetry": True,  # Only return laps with telemetry
                        "lapTypes": [1]  # Only normal (full) laps
                    }
                    
                    logger.debug(f"Trying to fetch laps with telemetry: {params}")
                    response = await client.get(
                        f"{self.base_url}/laps",
                        headers=self.headers,
                        params=params
                    )
                    response.raise_for_status()
                    
                    data = response.json()
                    laps = [LapData(**lap) for lap in data.get('items', [])]
                    logger.debug(f"Successfully fetched {len(laps)} laps with telemetry")
                    
                    # Sort by lap time to get fastest first
                    return sorted(laps, key=lambda x: x.lapTime)
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 403:
                        logger.info("Pro plan required for telemetry, falling back to lap times only")
                        # Fall through to try without telemetry
                    else:
                        raise
            
            # Try without telemetry requirement
            try:
                params = {
                    "cars": car_ids,
                    "tracks": track_ids,
                    "limit": limit,
                    "group": "none",  # Return all laps, not just personal bests
                    "lapTypes": [1]  # Only normal (full) laps
                    # No seeTelemetry parameter
                }
                
                logger.debug(f"Fetching laps without telemetry requirement: {params}")
                response = await client.get(
                    f"{self.base_url}/laps",
                    headers=self.headers,
                    params=params
                )
                response.raise_for_status()
                
                data = response.json()
                laps = [LapData(**lap) for lap in data.get('items', [])]
                logger.debug(f"Successfully fetched {len(laps)} laps without telemetry requirement")
                
                # Sort by lap time to get fastest first
                return sorted(laps, key=lambda x: x.lapTime)
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise ValueError(f"No laps found for the specified car/track combination")
                elif e.response.status_code == 401:
                    raise ValueError("Invalid authentication token")
                else:
                    raise ValueError(f"API error: {e.response.status_code} - {e.response.text}")
            except httpx.RequestError as e:
                raise ValueError(f"Network error: {str(e)}")
    
    async def get_lap_telemetry_csv(self, lap_id: str) -> Optional[str]:
        """Fetch telemetry data for a specific lap as CSV. Returns None if Pro plan required."""
        async with httpx.AsyncClient() as client:
            try:
                logger.debug(f"Attempting to fetch telemetry for lap {lap_id}")
                response = await client.get(
                    f"{self.base_url}/laps/{lap_id}/csv",
                    headers=self.headers
                )
                response.raise_for_status()
                
                logger.debug(f"Successfully fetched telemetry for lap {lap_id}")
                return response.text
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403:
                    logger.info(f"Pro plan required for telemetry data (lap {lap_id})")
                    return None  # Pro plan required, return None instead of raising
                elif e.response.status_code == 404:
                    logger.warning(f"Telemetry data not found for lap ID '{lap_id}'")
                    return None
                elif e.response.status_code == 401:
                    raise ValueError("Invalid authentication token")
                else:
                    raise ValueError(f"API error: {e.response.status_code} - {e.response.text}")
            except httpx.RequestError as e:
                raise ValueError(f"Network error: {str(e)}")
    
    def parse_csv_telemetry(self, csv_data: str) -> TelemetryData:
        """Parse CSV telemetry data and extract summary statistics."""
        lines = csv_data.strip().split('\n')
        if len(lines) < 2:
            raise ValueError("Invalid CSV format")
        
        # Parse header to find column indices
        header = lines[0].split(',')
        speed_idx = throttle_idx = brake_idx = None
        
        for i, col in enumerate(header):
            col_lower = col.lower().strip()
            if 'speed' in col_lower:
                speed_idx = i
            elif 'throttle' in col_lower:
                throttle_idx = i
            elif 'brake' in col_lower:
                brake_idx = i
        
        # Parse data rows
        speed_values = []
        throttle_values = []
        brake_values = []
        
        for line in lines[1:]:
            values = line.split(',')
            if len(values) > max(speed_idx or 0, throttle_idx or 0, brake_idx or 0):
                try:
                    if speed_idx is not None:
                        speed_values.append(float(values[speed_idx]))
                    if throttle_idx is not None:
                        throttle_values.append(float(values[throttle_idx]))
                    if brake_idx is not None:
                        brake_values.append(float(values[brake_idx]))
                except (ValueError, IndexError):
                    continue
        
        # Create telemetry channels
        def create_channel(values: List[float], unit: str) -> TelemetryChannel:
            if not values:
                return TelemetryChannel(unit=unit, samples=0, max=0, min=0)
            return TelemetryChannel(
                unit=unit,
                samples=len(values),
                max=max(values),
                min=min(values)
            )
        
        return TelemetryData(
            speed=create_channel(speed_values, "km/h"),
            throttle=create_channel(throttle_values, "percentage"),
            brake=create_channel(brake_values, "percentage")
        )
    
    async def get_fastest_lap_with_telemetry(self, car_name: str, track_name: str) -> Dict[str, Any]:
        """Get the fastest lap and its telemetry data for a car/track combination."""
        # Find car and track IDs
        car_id = await self.find_car_id(car_name)
        if not car_id:
            cache = get_cache()
            suggestions = cache.get_car_suggestions(car_name)
            if suggestions:
                raise ValueError(f"Car '{car_name}' not found. Did you mean one of these? {', '.join(suggestions[:3])}. Use the list_cars tool to see all available cars.")
            else:
                raise ValueError(f"Car '{car_name}' not found. Use the list_cars tool to see all available cars.")
        
        track_id = await self.find_track_id(track_name)
        if not track_id:
            cache = get_cache()
            suggestions = cache.get_track_suggestions(track_name)
            if suggestions:
                raise ValueError(f"Track '{track_name}' not found. Did you mean one of these? {', '.join(suggestions[:3])}. Use the list_tracks tool to see all available tracks with variants.")
            else:
                raise ValueError(f"Track '{track_name}' not found. Use the list_tracks tool to see all available tracks with variants.")
        
        # Get laps (try with telemetry first, fall back without)
        laps = await self.get_laps([car_id], [track_id])
        
        if not laps:
            # Car and track names are valid (we found IDs), but no laps exist for this combination
            raise ValueError(f"No lap data found for '{car_name}' at '{track_name}'. This means no laps are available for this car/track combination in your accessible data.")
        
        # Get the fastest lap
        fastest_lap = laps[0]
        
        # Try to get telemetry data
        telemetry = None
        telemetry_csv = None
        pro_required = False
        
        if fastest_lap.canViewTelemetry:
            csv_data = await self.get_lap_telemetry_csv(fastest_lap.id)
            if csv_data:
                telemetry = self.parse_csv_telemetry(csv_data)
            else:
                pro_required = True
        
        result = {
            "lap": {
                "id": fastest_lap.id,
                "driver": fastest_lap.driver.name if fastest_lap.driver else "Unknown",
                "lap_time": fastest_lap.lapTime,
                "car": fastest_lap.car.name,
                "track": fastest_lap.track.name
            },
            "pro_required": pro_required
        }
        
        if telemetry:
            result["telemetry"] = telemetry.model_dump()
        
        return result
    
    async def get_user_fastest_lap(self, car_name: str, track_name: str) -> Dict[str, Any]:
        """Get the current user's fastest lap and telemetry for a car/track combination."""
        logger.debug(f"get_user_fastest_lap called with car: {car_name}, track: {track_name}")
        
        # Find car and track IDs
        logger.debug(f"Looking up car ID for: {car_name}")
        car_id = await self.find_car_id(car_name)
        if not car_id:
            cache = get_cache()
            suggestions = cache.get_car_suggestions(car_name)
            logger.error(f"Car '{car_name}' not found. Suggestions: {suggestions}")
            if suggestions:
                raise ValueError(f"Car '{car_name}' not found. Did you mean one of these? {', '.join(suggestions[:3])}. Use the list_cars tool to see all available cars.")
            else:
                raise ValueError(f"Car '{car_name}' not found. Use the list_cars tool to see all available cars.")
        logger.debug(f"Found car ID: {car_id}")
        
        logger.debug(f"Looking up track ID for: {track_name}")
        track_id = await self.find_track_id(track_name)
        if not track_id:
            cache = get_cache()
            suggestions = cache.get_track_suggestions(track_name)
            logger.error(f"Track '{track_name}' not found. Suggestions: {suggestions}")
            if suggestions:
                raise ValueError(f"Track '{track_name}' not found. Did you mean one of these? {', '.join(suggestions[:3])}. Use the list_tracks tool to see all available tracks with variants.")
            else:
                raise ValueError(f"Track '{track_name}' not found. Use the list_tracks tool to see all available tracks with variants.")
        logger.debug(f"Found track ID: {track_id}")
        
        # Get laps for current user only
        async with httpx.AsyncClient() as client:
            # Try with telemetry first, then without if 403
            for try_telemetry in [True, False]:
                try:
                    params = {
                        "cars": [car_id],
                        "tracks": [track_id],
                        "drivers": ["me"],  # Only current user's laps
                        "limit": 1,
                        "group": "driver",  # Personal best lap
                        "lapTypes": [1]  # Only normal (full) laps
                    }
                    
                    if try_telemetry:
                        params["seeTelemetry"] = True
                        logger.debug("Trying user lap request with telemetry requirement")
                    else:
                        logger.debug("Trying user lap request without telemetry requirement")
                    
                    logger.debug(f"Making API request to {self.base_url}/laps with params: {params}")
                    response = await client.get(
                        f"{self.base_url}/laps",
                        headers=self.headers,
                        params=params
                    )
                    logger.debug(f"API response status: {response.status_code}")
                    response.raise_for_status()
                    
                    data = response.json()
                    logger.debug(f"API response data: {data}")
                    laps = [LapData(**lap) for lap in data.get('items', [])]
                    logger.debug(f"Found {len(laps)} laps")
                    
                    if not laps:
                        # Car and track names are valid (we found IDs), but no laps exist for this combination
                        raise ValueError(f"No lap data found for your account with '{car_name}' at '{track_name}'. This means you haven't driven this car/track combination yet, or your lap data isn't accessible with the current API access level.")
                    
                    fastest_lap = laps[0]
                    
                    # Try to get telemetry data
                    telemetry_csv = None
                    pro_required = False
                    
                    if fastest_lap.canViewTelemetry:
                        csv_data = await self.get_lap_telemetry_csv(fastest_lap.id)
                        if csv_data:
                            telemetry_csv = csv_data
                        else:
                            pro_required = True
                    
                    result = {
                        "lap": {
                            "id": fastest_lap.id,
                            "driver": fastest_lap.driver.name if fastest_lap.driver else "Current User",
                            "lap_time": fastest_lap.lapTime,
                            "car": fastest_lap.car.name,
                            "track": fastest_lap.track.name
                        },
                        "pro_required": pro_required
                    }
                    
                    if telemetry_csv:
                        result["telemetry_csv"] = telemetry_csv
                    
                    return result
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 403 and try_telemetry:
                        logger.info("Pro plan required for user laps with telemetry, retrying without")
                        continue  # Try again without telemetry
                    elif e.response.status_code == 404:
                        raise ValueError(f"No laps found for current user")
                    elif e.response.status_code == 401:
                        raise ValueError("Invalid authentication token")
                    else:
                        raise ValueError(f"API error: {e.response.status_code} - {e.response.text}")
                except httpx.RequestError as e:
                    raise ValueError(f"Network error: {str(e)}")
            
            # If we get here, both attempts failed
            raise ValueError("Unable to fetch user laps")
    
    async def get_overall_fastest_lap(self, car_name: str, track_name: str) -> Dict[str, Any]:
        """Get the overall fastest lap from all accessible drivers/teams for a car/track combination."""
        # Find car and track IDs
        car_id = await self.find_car_id(car_name)
        if not car_id:
            cache = get_cache()
            suggestions = cache.get_car_suggestions(car_name)
            if suggestions:
                raise ValueError(f"Car '{car_name}' not found. Did you mean one of these? {', '.join(suggestions[:3])}. Use the list_cars tool to see all available cars.")
            else:
                raise ValueError(f"Car '{car_name}' not found. Use the list_cars tool to see all available cars.")
        
        track_id = await self.find_track_id(track_name)
        if not track_id:
            cache = get_cache()
            suggestions = cache.get_track_suggestions(track_name)
            if suggestions:
                raise ValueError(f"Track '{track_name}' not found. Did you mean one of these? {', '.join(suggestions[:3])}. Use the list_tracks tool to see all available tracks with variants.")
            else:
                raise ValueError(f"Track '{track_name}' not found. Use the list_tracks tool to see all available tracks with variants.")
        
        # Get laps from all accessible drivers (me + teams)
        async with httpx.AsyncClient() as client:
            # Try with telemetry first, then without if 403
            for try_telemetry in [True, False]:
                try:
                    params = {
                        "cars": [car_id],
                        "tracks": [track_id],
                        # No drivers parameter means it includes me + all teammates
                        "limit": 1000,  # Get more laps to find the absolute fastest
                        "group": "none",  # Get all laps, not just personal bests
                        "lapTypes": [1]  # Only normal (full) laps
                    }
                    
                    if try_telemetry:
                        params["seeTelemetry"] = True
                        logger.debug("Trying overall fastest lap request with telemetry requirement")
                    else:
                        logger.debug("Trying overall fastest lap request without telemetry requirement")
                    
                    response = await client.get(
                        f"{self.base_url}/laps",
                        headers=self.headers,
                        params=params
                    )
                    response.raise_for_status()
                    
                    data = response.json()
                    laps = [LapData(**lap) for lap in data.get('items', [])]
                    
                    if not laps:
                        # Car and track names are valid (we found IDs), but no laps exist for this combination
                        raise ValueError(f"No lap data found for '{car_name}' at '{track_name}'. This means no laps are available for this car/track combination in your accessible data (includes your laps and team laps).")
                    
                    # Sort by lap time to get the absolute fastest
                    laps_sorted = sorted(laps, key=lambda x: x.lapTime)
                    fastest_lap = laps_sorted[0]
                    
                    # Try to get telemetry data
                    telemetry_csv = None
                    pro_required = False
                    
                    if fastest_lap.canViewTelemetry:
                        csv_data = await self.get_lap_telemetry_csv(fastest_lap.id)
                        if csv_data:
                            telemetry_csv = csv_data
                        else:
                            pro_required = True
                    
                    result = {
                        "lap": {
                            "id": fastest_lap.id,
                            "driver": fastest_lap.driver.name if fastest_lap.driver else "Unknown",
                            "lap_time": fastest_lap.lapTime,
                            "car": fastest_lap.car.name,
                            "track": fastest_lap.track.name
                        },
                        "pro_required": pro_required
                    }
                    
                    if telemetry_csv:
                        result["telemetry_csv"] = telemetry_csv
                    
                    return result
                    
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 403 and try_telemetry:
                        logger.info("Pro plan required for overall fastest lap with telemetry, retrying without")
                        continue  # Try again without telemetry
                    elif e.response.status_code == 404:
                        raise ValueError(f"No laps found")
                    elif e.response.status_code == 401:
                        raise ValueError("Invalid authentication token")
                    else:
                        raise ValueError(f"API error: {e.response.status_code} - {e.response.text}")
                except httpx.RequestError as e:
                    raise ValueError(f"Network error: {str(e)}")
            
            # If we get here, both attempts failed
            raise ValueError("Unable to fetch overall fastest lap")


def create_client() -> Garage61Client:
    """Create a Garage61 client from environment variables."""
    logger.debug("Creating Garage61 client")
    token = os.getenv("GARAGE61_TOKEN")
    if not token:
        logger.error("GARAGE61_TOKEN environment variable is not set")
        raise ValueError("GARAGE61_TOKEN environment variable is required")
    
    logger.debug(f"Token found: {token[:10]}..." if len(token) > 10 else "Token found")
    base_url = os.getenv("GARAGE61_BASE_URL", "https://garage61.net/api/v1")
    logger.debug(f"Using base URL: {base_url}")
    return Garage61Client(token, base_url)


async def initialize_cache() -> None:
    """Initialize the cache with cars and tracks data."""
    logger.info("Initializing cars and tracks cache")
    
    try:
        client = create_client()
        cache = get_cache()
        
        # Fetch and cache cars
        logger.debug("Fetching cars for cache")
        cars = await client.get_cars()
        cache.set_cars(cars)
        
        # Fetch and cache tracks
        logger.debug("Fetching tracks for cache")
        tracks = await client.get_tracks()
        cache.set_tracks(tracks)
        
        logger.info("Cache initialization complete")
        
    except Exception as e:
        logger.error(f"Failed to initialize cache: {str(e)}", exc_info=True)
        raise