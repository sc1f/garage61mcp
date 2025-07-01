"""Cache for cars and tracks data with fuzzy matching."""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from difflib import get_close_matches

logger = logging.getLogger(__name__)


class CarsTracksCache:
    """Cache for cars and tracks with fuzzy matching."""
    
    def __init__(self):
        self.cars: List[Dict[str, Any]] = []
        self.tracks: List[Dict[str, Any]] = []
        self._cars_by_name: Dict[str, Dict[str, Any]] = {}
        self._tracks_by_name: Dict[str, Dict[str, Any]] = {}
        
    def _get_car_generation_score(self, car_name: str) -> int:
        """Return a score indicating how recent/current a car is. Higher = more recent."""
        name_lower = car_name.lower()
        
        # Immediate legacy detection (very low score)
        if self._is_legacy_car(car_name):
            return 0
        
        # Porsche generation scoring (992 > 991)
        if 'porsche' in name_lower:
            if '992' in name_lower:
                return 100  # Newest Porsche generation
            elif '991' in name_lower:
                return 10   # Older generation
                
        # Year-based scoring - newer years get higher scores
        year_patterns = [
            (r'\b(2024|2025)\b', 90),
            (r'\b(2022|2023)\b', 80),
            (r'\b(2020|2021)\b', 70),
            (r'\b(2018|2019)\b', 50),
            (r'\b(2016|2017)\b', 30),
            (r'\b(2014|2015)\b', 20),
            (r'\b(2012|2013)\b', 15),
            (r'\b(2010|2011)\b', 10),
            (r'\b(2008|2009)\b', 5),
        ]
        
        for pattern, score in year_patterns:
            if re.search(pattern, name_lower):
                return score
        
        # Modern car indicators
        modern_patterns = [
            (r'\bevo\s*(2024|2023|2022|2021|2020)\b', 85),
            (r'\bevo\s*ii\b', 80),
            (r'\bevo\b', 70),
            (r'\bnext\s*gen\b', 90),
            (r'\bgen\s*3\b', 85),
            (r'\bhybrid\b', 80),
            (r'\bgtp\b', 85),  # Modern GTP class
            (r'\bgt3.*r\b', 75),  # GT3 R versions
            (r'\bgt3\b', 65),
            (r'\bgt4\b', 60),
            (r'\bgte\b', 70),
            (r'\btcr\b', 65),
            (r'\bcup.*\(99[2-9]\)', 80),  # Cup cars with newer generation
        ]
        
        base_score = 40  # Base score for modern cars
        for pattern, points in modern_patterns:
            if re.search(pattern, name_lower):
                base_score = max(base_score, points)
                
        return base_score
        
    def _is_legacy_car(self, car_name: str) -> bool:
        """Check if a car should be considered legacy/outdated based on real car data."""
        name_lower = car_name.lower()
        
        # Specific legacy car patterns from the actual data
        legacy_patterns = [
            r'\b(991)\b',  # Porsche 991 generation
            r'\b(2008|2009|2010|2012|2013|2014|2015|2016)\b',  # Older years
            r'\b(cot|car of tomorrow)\b',
            r'\b(legacy|retired|discontinued|old)\b',
            r'\bmk.*1\b',
            r'\bgen.*1\b',
            r'c6\.r',  # Corvette C6.R vs newer C8.R
            r'c7\s',   # C7 vs newer generations
            r'impala.*cot',
            r'fusion.*2016',
            r'2010|2012|2013|2014|2015|2016',  # General old year patterns
            r'legends.*1987',  # NASCAR Legends series
            r'nationwide.*2012',  # Old NASCAR series names
            r'gander.*2015',
            r'xfinity.*201[4-6]',
            r'truck.*2008|truck.*2018',  # Older truck generations
            r'mazda.*2010',  # Older MX-5
            r'formula\s*renault',  # Older formula series
            r'ir-05|ir18.*(?!2)',  # Older IndyCar
            r'falcon.*2009|falcon.*2014',  # V8 Supercar older generations
            r'commodore.*2014',
            r'f82.*2018',  # BMW older generation
        ]
        
        return any(re.search(pattern, name_lower) for pattern in legacy_patterns)
        
    def _sort_cars_by_relevance(self, cars: List[Dict[str, Any]], include_legacy: bool = False) -> List[Dict[str, Any]]:
        """Sort cars by relevance, prioritizing modern/current generation cars."""
        if include_legacy:
            # Include all cars but sort by generation score
            return sorted(cars, key=lambda car: self._get_car_generation_score(car["name"]), reverse=True)
        else:
            # Filter out legacy cars, then sort by generation score
            modern_cars = [car for car in cars if not self._is_legacy_car(car["name"])]
            return sorted(modern_cars, key=lambda car: self._get_car_generation_score(car["name"]), reverse=True)
            
    def _get_track_variant_score(self, variant: str) -> int:
        """Score track variants by preference. Higher = more preferred."""
        if not variant:
            return 50  # Base tracks get middle score
            
        variant_lower = variant.lower()
        
        # Preferred variants (racing-focused)
        preferred_patterns = [
            (r'\bgrand\s*prix\b', 100),
            (r'\bfull\s*course\b', 95),
            (r'\binternational\b', 90),
            (r'\bendurance\b', 85),
            (r'\boval\b', 80),
            (r'\bnational\b', 75),
            (r'\bclub\b', 70),
        ]
        
        # Less preferred variants
        less_preferred_patterns = [
            (r'\bmoto\b', 40),
            (r'\bbike\b', 35),
            (r'\brallycross\b', 30),
            (r'\blegends\b', 25),
            (r'\bschool\b', 20),
            (r'\breverse\b', 15),
            (r'\bshort\b', 45),
            (r'\balt\b', 40),
            (r'\bwithout\b|w/out\b', 35),
        ]
        
        # Check for preferred patterns first
        for pattern, score in preferred_patterns:
            if re.search(pattern, variant_lower):
                return score
                
        # Check for less preferred patterns
        for pattern, score in less_preferred_patterns:
            if re.search(pattern, variant_lower):
                return score
                
        return 50  # Default score
        
    def _group_tracks_by_base_name(self, tracks: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Group tracks by their base name (without variant)."""
        grouped = {}
        for track in tracks:
            base_name = track.get("name", "")
            if base_name not in grouped:
                grouped[base_name] = []
            grouped[base_name].append(track)
        return grouped
        
    def _get_best_track_variant(self, track_variants: List[Dict[str, Any]], prefer_racing: bool = True) -> Dict[str, Any]:
        """Get the best track variant from a list, preferring racing configurations."""
        if not track_variants:
            return None
            
        if len(track_variants) == 1:
            return track_variants[0]
            
        # Sort by variant preference score
        sorted_variants = sorted(
            track_variants, 
            key=lambda t: self._get_track_variant_score(t.get("variant", "")), 
            reverse=True
        )
        
        return sorted_variants[0]
        
    def set_cars(self, cars_data: Any) -> None:
        """Set cars data, handling API response format."""
        logger.debug(f"Setting cars cache with data type: {type(cars_data)}")
        
        # Handle API response format: {"items": [...], "total": N}
        if isinstance(cars_data, dict) and 'items' in cars_data:
            items = cars_data['items']
            logger.debug(f"Extracting {len(items)} cars from API response")
            self.cars = items
        elif isinstance(cars_data, list) and cars_data:
            # Handle direct list format (fallback)
            if isinstance(cars_data[0], str):
                # Convert list of strings to list of dicts
                self.cars = [{"id": i, "name": name} for i, name in enumerate(cars_data)]
            elif isinstance(cars_data[0], dict):
                self.cars = cars_data
            else:
                logger.error(f"Unexpected car data format: {type(cars_data[0])}")
                self.cars = []
        else:
            logger.warning(f"Unexpected cars data format: {type(cars_data)}")
            self.cars = []
            
        # Build name lookup
        self._cars_by_name = {car.get("name", "").lower(): car for car in self.cars}
        logger.info(f"Cached {len(self.cars)} cars")
        
    def set_tracks(self, tracks_data: Any) -> None:
        """Set tracks data, handling API response format."""
        logger.debug(f"Setting tracks cache with data type: {type(tracks_data)}")
        
        # Handle API response format: {"items": [...], "total": N}
        if isinstance(tracks_data, dict) and 'items' in tracks_data:
            items = tracks_data['items']
            logger.debug(f"Extracting {len(items)} tracks from API response")
            self.tracks = items
        elif isinstance(tracks_data, list) and tracks_data:
            # Handle direct list format (fallback)
            if isinstance(tracks_data[0], str):
                # Convert list of strings to list of dicts
                self.tracks = [{"id": i, "name": name} for i, name in enumerate(tracks_data)]
            elif isinstance(tracks_data[0], dict):
                self.tracks = tracks_data
            else:
                logger.error(f"Unexpected track data format: {type(tracks_data[0])}")
                self.tracks = []
        else:
            logger.warning(f"Unexpected tracks data format: {type(tracks_data)}")
            self.tracks = []
            
        # Build name lookup - include both base name and full name with variant
        self._tracks_by_name = {}
        for track in self.tracks:
            base_name = track.get("name", "").lower()
            variant = track.get("variant", "")
            
            # Store by base name
            if base_name and base_name not in self._tracks_by_name:
                self._tracks_by_name[base_name] = track
                
            # Also store by full name if variant exists
            if variant:
                full_name = f"{base_name} {variant.lower()}".strip()
                self._tracks_by_name[full_name] = track
                # Also try with different separators
                full_name_dash = f"{base_name} - {variant.lower()}".strip()
                self._tracks_by_name[full_name_dash] = track
                
        logger.info(f"Cached {len(self.tracks)} tracks with {len(self._tracks_by_name)} lookup keys")
        
        # Debug: log some track examples for troubleshooting
        sample_keys = list(self._tracks_by_name.keys())[:5]
        if sample_keys:
            logger.debug(f"Sample track lookup keys: {sample_keys}")
            
        # Debug: check for nürburgring specifically
        nurburgring_keys = [key for key in self._tracks_by_name.keys() if 'nürburg' in key or 'nurbur' in key]
        if nurburgring_keys:
            logger.debug(f"Found Nürburgring track keys: {nurburgring_keys}")
        
    def find_car(self, car_name: str, include_legacy: bool = False) -> Optional[Tuple[int, str]]:
        """Find car by name with fuzzy matching and modern car prioritization. Returns (id, exact_name) or None."""
        car_name_lower = car_name.lower()
        
        # Check if user explicitly wants legacy cars
        legacy_keywords = ['legacy', 'old', 'classic', 'vintage', '991', 'gen 1', 'generation 1']
        if any(keyword in car_name_lower for keyword in legacy_keywords):
            include_legacy = True
            logger.debug(f"Legacy keywords detected in '{car_name}', including legacy cars")
        
        # Try exact match first
        if car_name_lower in self._cars_by_name:
            car = self._cars_by_name[car_name_lower]
            return (car["id"], car["name"])
            
        # Try partial match with prioritization
        partial_matches = []
        input_words = car_name_lower.split()
        
        for name, car in self._cars_by_name.items():
            # Check if the search term is contained in the name
            if car_name_lower in name or name in car_name_lower:
                partial_matches.append(car)
            # Also check if all input words are contained in the car name
            elif all(word in name for word in input_words):
                partial_matches.append(car)
                
        if partial_matches:
            # Sort by relevance (modern cars first unless legacy requested)
            sorted_matches = self._sort_cars_by_relevance(partial_matches, include_legacy)
            
            if len(sorted_matches) == 1:
                logger.debug(f"Found partial match for car '{car_name}': {sorted_matches[0]['name']}")
                return (sorted_matches[0]["id"], sorted_matches[0]["name"])
            else:
                # Multiple matches - try exact word matching first
                for match in sorted_matches:
                    match_words = match["name"].lower().split()
                    input_words = car_name_lower.split()
                    if all(word in match_words for word in input_words):
                        logger.debug(f"Found best partial match for car '{car_name}': {match['name']} (generation score: {self._get_car_generation_score(match['name'])})")
                        return (match["id"], match["name"])
                
                # Return the most relevant match
                best_match = sorted_matches[0]
                logger.debug(f"Found prioritized match for car '{car_name}': {best_match['name']} (generation score: {self._get_car_generation_score(best_match['name'])})")
                return (best_match["id"], best_match["name"])
                
        # Try fuzzy match with prioritization
        car_names = list(self._cars_by_name.keys())
        matches = get_close_matches(car_name_lower, car_names, n=10, cutoff=0.3)  # Lowered cutoff for better fuzzy matching
        
        # Also try fuzzy matching individual words
        if not matches and len(input_words) > 1:
            word_matches = []
            for word in input_words:
                word_fuzzy = get_close_matches(word, car_names, n=5, cutoff=0.3)
                word_matches.extend(word_fuzzy)
            
            # Remove duplicates and combine with original matches
            matches = list(set(matches + word_matches))
        
        logger.debug(f"Fuzzy matching for '{car_name}' found: {matches[:3]}")
        
        if matches:
            # Convert to car objects and sort by relevance
            match_cars = [self._cars_by_name[match] for match in matches]
            sorted_matches = self._sort_cars_by_relevance(match_cars, include_legacy)
            
            if sorted_matches:
                best_match = sorted_matches[0]
                logger.debug(f"Found fuzzy match for car '{car_name}': {best_match['name']} (generation score: {self._get_car_generation_score(best_match['name'])})")
                return (best_match["id"], best_match["name"])
            
        logger.warning(f"No match found for car: {car_name}")
        return None
    
    def get_car_suggestions(self, car_name: str, limit: int = 5, include_legacy: bool = False) -> List[str]:
        """Get suggested car names for a given input, prioritizing modern cars."""
        car_name_lower = car_name.lower()
        
        # Check if user explicitly wants legacy cars
        legacy_keywords = ['legacy', 'old', 'classic', 'vintage', '991', 'gen 1', 'generation 1']
        if any(keyword in car_name_lower for keyword in legacy_keywords):
            include_legacy = True
        
        suggestion_cars = []
        
        # Get partial matches
        for name, car in self._cars_by_name.items():
            if car_name_lower in name or any(word in name for word in car_name_lower.split()):
                suggestion_cars.append(car)
                
        # Add fuzzy matches
        car_names = list(self._cars_by_name.keys())
        fuzzy_matches = get_close_matches(car_name_lower, car_names, n=limit * 2, cutoff=0.4)
        for match in fuzzy_matches:
            car = self._cars_by_name[match]
            if car not in suggestion_cars:
                suggestion_cars.append(car)
        
        # Sort by relevance and extract names
        sorted_cars = self._sort_cars_by_relevance(suggestion_cars, include_legacy)
        suggestions = [car["name"] for car in sorted_cars[:limit]]
                
        return suggestions
        
    def find_track(self, track_name: str, prefer_racing: bool = True) -> Optional[Tuple[int, str]]:
        """Find track by name with variant-aware fuzzy matching. Returns (id, exact_name) or None."""
        track_name_lower = track_name.lower()
        logger.debug(f"Searching for track: '{track_name}' (normalized: '{track_name_lower}')")
        
        # Try exact match first (full name including variant)
        if track_name_lower in self._tracks_by_name:
            track = self._tracks_by_name[track_name_lower]
            logger.debug(f"Found exact match for '{track_name}' -> {self._format_track_name_with_variant(track)}")
            return (track["id"], self._format_track_name_with_variant(track))
            
        # Find tracks that match the base name (ignoring variant)
        matching_tracks = []
        for track in self.tracks:
            base_name = track.get("name", "").lower()
            variant = track.get("variant", "").lower()
            full_name = f"{base_name} {variant}".strip().lower()
            
            # Check if search term matches base name or is in full name
            if (track_name_lower in base_name or 
                base_name in track_name_lower or
                track_name_lower in full_name or
                any(word in base_name for word in track_name_lower.split())):
                matching_tracks.append(track)
        
        if matching_tracks:
            # Group by base name
            grouped = self._group_tracks_by_base_name(matching_tracks)
            
            # If we have multiple base names, try to find the best match
            if len(grouped) == 1:
                base_name, variants = list(grouped.items())[0]
                best_variant = self._get_best_track_variant(variants, prefer_racing)
                logger.debug(f"Found track '{track_name}' -> '{base_name}' variant '{best_variant.get('variant', 'base')}' (score: {self._get_track_variant_score(best_variant.get('variant', ''))})")
                return (best_variant["id"], self._format_track_name_with_variant(best_variant))
            else:
                # Multiple base tracks match - return the best overall match
                all_best_variants = []
                for base_name, variants in grouped.items():
                    best_variant = self._get_best_track_variant(variants, prefer_racing)
                    all_best_variants.append(best_variant)
                
                # Sort by how well the base name matches the search
                def match_score(track):
                    base_name = track.get("name", "").lower()
                    if track_name_lower == base_name:
                        return 100
                    elif track_name_lower in base_name:
                        return 80
                    elif base_name in track_name_lower:
                        return 60
                    else:
                        return 20
                
                best_overall = max(all_best_variants, key=match_score)
                logger.debug(f"Found best overall track match for '{track_name}': {self._format_track_name_with_variant(best_overall)}")
                return (best_overall["id"], self._format_track_name_with_variant(best_overall))
                
        # Try fuzzy match on base names
        base_names = list(set(track.get("name", "") for track in self.tracks))
        matches = get_close_matches(track_name_lower, [name.lower() for name in base_names], n=3, cutoff=0.4)
        
        if matches:
            # Find the best variant for the fuzzy-matched base name
            matched_base = None
            for name in base_names:
                if name.lower() == matches[0]:
                    matched_base = name
                    break
                    
            if matched_base:
                variants = [t for t in self.tracks if t.get("name") == matched_base]
                best_variant = self._get_best_track_variant(variants, prefer_racing)
                logger.debug(f"Found fuzzy match for track '{track_name}': {self._format_track_name_with_variant(best_variant)}")
                return (best_variant["id"], self._format_track_name_with_variant(best_variant))
            
        logger.warning(f"No match found for track: {track_name}")
        return None
        
    def _format_track_name_with_variant(self, track: Dict[str, Any]) -> str:
        """Format track name with variant for display."""
        name = track.get("name", "")
        variant = track.get("variant", "")
        if variant:
            return f"{name} - {variant}"
        return name
    
    def get_track_suggestions(self, track_name: str, limit: int = 5) -> List[str]:
        """Get suggested track names for a given input."""
        track_name_lower = track_name.lower()
        suggestions = []
        
        # Get partial matches
        for name, track in self._tracks_by_name.items():
            if track_name_lower in name or any(word in name for word in track_name_lower.split()):
                formatted_name = self._format_track_name_with_variant(track)
                if formatted_name not in suggestions:
                    suggestions.append(formatted_name)
                
        # Add fuzzy matches
        track_names = list(self._tracks_by_name.keys())
        fuzzy_matches = get_close_matches(track_name_lower, track_names, n=limit, cutoff=0.4)
        for match in fuzzy_matches:
            track = self._tracks_by_name[match]
            formatted_name = self._format_track_name_with_variant(track)
            if formatted_name not in suggestions:
                suggestions.append(formatted_name)
                
        return suggestions[:limit]


# Global cache instance
_cache = CarsTracksCache()


def get_cache() -> CarsTracksCache:
    """Get the global cache instance."""
    return _cache