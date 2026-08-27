"""MCP tools for Garage61 telemetry data."""

import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mcp.types import Tool, TextContent

from api_client import LapData, create_client
from cache import get_cache
from formatting import (
    MS_TO_KMH,
    comparison_report,
    fixed_table,
    uniform_series,
    corner_table,
    format_conditions,
    format_gap,
    format_lap_time,
    garage61_link_line,
    kmh,
    lap_summary,
)
from lapquality import split_usable
from reqcontext import user_scope
from telemetry import (
    Corner,
    build_corner_map,
    compare_laps,
    detect_brake_events,
    downsample,
    parse_lap_csv,
)

logger = logging.getLogger(__name__)

# Comparison results, kept so the analyze_* tools can drill into a comparison
# without re-fetching and re-parsing several megabytes of telemetry.
_comparison_cache: Dict[str, Any] = {}

# One canonical corner map per car/track, so Turn N names the same corner in
# every comparison regardless of who the reference driver is.
_corner_map_cache: Dict[str, List[Corner]] = {}

# How many laps to sample when building a corner map. Four is enough for a
# stable consensus -- dropping from six changed neither the corner count nor
# apex positions by more than 0.4pp -- and since the two laps being compared are
# already loaded it costs only two extra telemetry downloads.
CORNER_MAP_SAMPLE = 4


def _combo_key(car: str, track: str) -> str:
    return f"{car.strip().lower()}::{track.strip().lower()}"


def _cache_key(car: str, track: str) -> str:
    """Key for the comparison cache: scoped per user.

    Comparisons hold lap telemetry that is private to whoever fetched it. Over
    HTTP the server is multi-tenant, so two users comparing the same car/track
    must never read each other's entries. The corner map deliberately does NOT
    use this key -- it is track geometry (apex positions, angles), identical for
    everyone, and sharing it across users saves telemetry downloads.
    """
    return f"{user_scope()}::{_combo_key(car, track)}"




async def _get_corner_map(
    client, car: str, track: str, already_loaded: Sequence[Any] = ()
) -> List[Corner]:
    """Fetch (and cache) the canonical corner map for a car/track.

    Built from several drivers' laps rather than one, because a single lap's
    corner count depends on that driver's line -- at Tsukuba the same car gives
    11 to 13 corners across nine drivers.
    """
    key = _combo_key(car, track)
    if key in _corner_map_cache:
        return _corner_map_cache[key]

    samples = list(already_loaded)
    try:
        result = await client.get_accessible_laps(car, track, group="driver")
        candidates = [lap for lap in result["laps"] if lap.canViewTelemetry]
        for lap in candidates[:CORNER_MAP_SAMPLE]:
            if len(samples) >= CORNER_MAP_SAMPLE:
                break
            csv_data = await client.get_lap_telemetry_csv(lap.id)
            if csv_data:
                samples.append(parse_lap_csv(csv_data, lap.lapTime, "map"))
    except Exception as e:
        # A corner map is an enhancement, not a precondition; fall back to
        # whatever laps the caller already had rather than failing the tool.
        logger.warning(f"Could not sample laps for corner map: {e}")

    if not samples:
        return []

    corner_map = build_corner_map(samples)
    _corner_map_cache[key] = corner_map
    logger.info(
        f"Corner map for {key}: {len(corner_map)} corners from {len(samples)} laps"
    )
    return corner_map


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"Error: {message}")]


def _ok(message: str) -> list[TextContent]:
    # The consumer is a model, not a renderer: bold markers are four characters
    # of noise per emphasis. Structure (headers, fences, legends) stays; the
    # cosmetics go here, at the boundary, so the builders stay readable.
    return [TextContent(type="text", text=message.replace("**", ""))]


# --------------------------------------------------------------------------
# Lap selection
# --------------------------------------------------------------------------

def _describe_lap(lap: LapData, index: int) -> str:
    return f"#{index} {lap.startTime[:10]} {format_lap_time(lap.lapTime)}"


def select_lap(laps: Sequence[LapData], selector: str) -> Tuple[LapData, int]:
    """Resolve a lap selector against a chronologically sorted lap list.

    Accepts `fastest`, `slowest`, `latest`/`newest`, `oldest`/`first`, a 1-based
    index as shown by list_my_laps, an ISO date (`2026-04-04`), or a lap id.
    """
    if not laps:
        raise ValueError("No laps available to select from")

    key = (selector or "").strip().lower()

    if key in ("", "fastest", "best", "pb"):
        best = min(laps, key=lambda lap: lap.lapTime)
        return best, laps.index(best) + 1
    if key in ("slowest", "worst"):
        worst = max(laps, key=lambda lap: lap.lapTime)
        return worst, laps.index(worst) + 1
    if key in ("latest", "newest", "last", "recent"):
        return laps[-1], len(laps)
    if key in ("oldest", "first", "earliest"):
        return laps[0], 1

    if key.isdigit():
        index = int(key)
        if not 1 <= index <= len(laps):
            raise ValueError(
                f"Lap index {index} is out of range; there are {len(laps)} laps "
                "(use list_my_laps to see them)"
            )
        return laps[index - 1], index

    # An ISO date selects the fastest lap set on that day.
    matches = [lap for lap in laps if lap.startTime.startswith(key)]
    if matches:
        best = min(matches, key=lambda lap: lap.lapTime)
        return best, laps.index(best) + 1

    for position, lap in enumerate(laps, start=1):
        if lap.id.lower() == key:
            return lap, position

    raise ValueError(
        f"Could not resolve lap selector '{selector}'. Use fastest, slowest, "
        "latest, oldest, a lap number from list_my_laps, or a date like 2026-04-04."
    )


async def _load_telemetry(client, lap: LapData, label: str):
    """Fetch and parse one lap's telemetry, with a clear reason when it fails."""
    if not lap.canViewTelemetry:
        raise ValueError(
            f"Telemetry is not available for the {label} lap "
            f"({lap.startTime[:10]}, {format_lap_time(lap.lapTime)})."
        )
    csv_data = await client.get_lap_telemetry_csv(lap.id)
    if not csv_data:
        raise ValueError(
            f"Could not download telemetry for the {label} lap "
            f"({lap.startTime[:10]}). This usually means a Pro plan is required."
        )
    return parse_lap_csv(csv_data, lap.lapTime, label)


# --------------------------------------------------------------------------
# Own-lap history and progression
# --------------------------------------------------------------------------

async def list_my_laps(car: str, track: str, clean_only: bool = False) -> list[TextContent]:
    """List the user's own laps for a car/track, newest last, with conditions."""
    try:
        client = create_client()
        result = await client.get_my_laps(car, track, clean_only=clean_only)
        laps: List[LapData] = result["laps"]

        if not laps:
            return _err(
                f"No laps found for **{result['car_resolved']}** at "
                f"**{result['track_resolved']}**. You haven't driven this "
                "combination yet, or the laps aren't accessible with your plan."
            )

        usable, excluded = split_usable(laps)
        excluded_ids = {lap.id for lap, _ in excluded}
        best = min(usable, key=lambda lap: lap.lapTime)
        representative = [lap for lap in laps if lap.id not in excluded_ids]
        latest = representative[-1] if representative else laps[-1]

        lines = [
            f"## Your laps: {result['car_resolved']} at {result['track_resolved']}",
            "",
            f"**{len(laps)} laps** across "
            f"{len({lap.startTime[:10] for lap in laps})} days, "
            f"{len(laps) - len(excluded)} representative. "
            f"Personal best **{format_lap_time(best.lapTime)}** "
            f"({best.startTime[:10]}).",
            "",
        ]

        rows = []
        for index, lap in enumerate(laps, start=1):
            sectors = (
                "/".join(f"{t:.2f}" for t in lap.sector_times)
                if lap.sector_times else "-"
            )
            gap = lap.lapTime - best.lapTime
            if lap.id == best.id:
                marker = " *"
            elif lap.id in excluded_ids:
                marker = " !"
            else:
                marker = ""
            rows.append([
                str(index),
                lap.startTime[:16].replace("T", " "),
                format_lap_time(lap.lapTime) + marker,
                format_gap(gap) if gap else "-",
                sectors,
                "y" if lap.canViewTelemetry else "n",
                format_conditions(lap),
            ])
        lines.append("```\n" + fixed_table(
            ["#", "date", "time", "gapPB", "sectors", "tel", "conditions"], rows
        ) + "\n```")
        lines.append("_* = personal best, ! = compromised (excluded from comparisons)._")
        lines.append("")

        if excluded:
            lines.append("**Compromised laps (excluded from comparisons):**")
            lines.append("")
            for lap, verdict in excluded:
                position = laps.index(lap) + 1
                lines.append(f"- Lap {position} ({lap.startTime[:10]}): {verdict.summary}")
            lines.append("")

        if len(representative) > 1:
            trend = latest.lapTime - representative[0].lapTime
            direction = "faster" if trend < 0 else "slower"
            lines.append(
                f"**Progression**: across your representative laps, the most "
                f"recent is {abs(trend):.3f}s {direction} than the earliest. "
                f"Latest vs personal best: {format_gap(latest.lapTime - best.lapTime)}."
            )
            lines.append("")
            lines.append(
                "Use `compare_my_laps` for a corner-by-corner comparison of any "
                "two laps, or `analyze_consistency` to see the pattern across all "
                "of them."
            )
            lines.append("")
            lines.append(garage61_link_line(result))

        return _ok("\n".join(lines))

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"list_my_laps failed: {e}", exc_info=True)
        return _err(f"Could not list laps: {e}")


async def compare_my_laps(
    car: str,
    track: str,
    reference: str = "fastest",
    compared: str = "latest",
) -> list[TextContent]:
    """Compare two of the user's own laps and attribute the gap across the lap."""
    try:
        client = create_client()
        result = await client.get_my_laps(car, track)
        laps: List[LapData] = result["laps"]

        if len(laps) < 2:
            return _err(
                f"Need at least two laps to compare, but found {len(laps)} for "
                f"**{result['car_resolved']}** at **{result['track_resolved']}**."
            )

        ref_lap, ref_index = select_lap(laps, reference)
        cmp_lap, cmp_index = select_lap(laps, compared)

        if ref_lap.id == cmp_lap.id:
            return _err(
                f"Both selectors resolved to the same lap "
                f"({_describe_lap(ref_lap, ref_index)}). Pick two different laps — "
                "run `list_my_laps` to see the options."
            )

        ref_label = f"Lap {ref_index} ({ref_lap.startTime[:10]})"
        cmp_label = f"Lap {cmp_index} ({cmp_lap.startTime[:10]})"

        ref_telemetry = await _load_telemetry(client, ref_lap, ref_label)
        cmp_telemetry = await _load_telemetry(client, cmp_lap, cmp_label)

        corner_map = await _get_corner_map(
            client, car, track, [ref_telemetry, cmp_telemetry]
        )
        comparison = compare_laps(
            ref_telemetry,
            cmp_telemetry,
            sector_times=ref_lap.sector_times,
            corner_map=corner_map,
        )

        _comparison_cache[_cache_key(car, track)] = {
            "comparison": comparison,
            "reference_name": ref_label,
            "lap_name": cmp_label,
        }

        notes = []
        if ref_lap.sector_times and cmp_lap.sector_times:
            splits = []
            for i, (ref_t, cmp_t) in enumerate(
                zip(ref_lap.sector_times, cmp_lap.sector_times), start=1
            ):
                splits.append(f"S{i} {cmp_t - ref_t:+.3f}s")
            notes.append(f"**Sector splits**: {'  '.join(splits)}")

        # Conditions are printed with every comparison, not only when they
        # differ: a clean-looking delta on different track temperature or fuel
        # is a different comparison, and the reader should never have to ask.
        notes.append(
            f"**Conditions**: this lap {format_conditions(cmp_lap)} / "
            f"reference {format_conditions(ref_lap)}"
        )
        conditions = []
        if ref_lap.trackTemp is not None and cmp_lap.trackTemp is not None:
            drift = cmp_lap.trackTemp - ref_lap.trackTemp
            if abs(drift) >= 2.0:
                conditions.append(
                    f"track temperature differs by {drift:+.1f}°C, which affects grip"
                )
        if ref_lap.fuelLevel is not None and cmp_lap.fuelLevel is not None:
            fuel = cmp_lap.fuelLevel - ref_lap.fuelLevel
            if abs(fuel) >= 5.0:
                conditions.append(f"fuel load differs by {fuel:+.1f}L")
        if conditions:
            notes.append(f"**Caveat**: {'; '.join(conditions)}.")

        report = comparison_report(
            comparison,
            title=(
                f"{result['car_resolved']} at {result['track_resolved']} — "
                f"your lap {cmp_index} vs your lap {ref_index}"
            ),
            reference_name=ref_label,
            lap_name=cmp_label,
            notes=notes,
        )
        report += (
            "\n\n_Drill into any part of the lap with `get_channel_window` "
            "(pass corner_number for a corner and its braking approach), or "
            "`analyze_worst_sections`._"
            f"\n\n{garage61_link_line(result)}"
        )
        return _ok(report)

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"compare_my_laps failed: {e}", exc_info=True)
        return _err(f"Comparison failed: {e}")


async def analyze_consistency(car: str, track: str) -> list[TextContent]:
    """Analyse every representative lap: variability, theoretical best, trend."""
    try:
        client = create_client()
        result = await client.get_my_laps(car, track)
        laps: List[LapData] = result["laps"]

        if len(laps) < 3:
            return _err(
                f"Consistency analysis needs at least 3 laps; found {len(laps)} "
                f"for **{result['car_resolved']}** at **{result['track_resolved']}**."
            )

        usable, excluded = split_usable(laps)
        times = [lap.lapTime for lap in usable]
        best = min(usable, key=lambda lap: lap.lapTime)
        mean = statistics.mean(times)
        spread = max(times) - min(times)
        stdev = statistics.pstdev(times) if len(times) > 1 else 0.0

        lines = [
            f"## Consistency: {result['car_resolved']} at {result['track_resolved']}",
            "",
            f"Across **{len(usable)} representative laps** "
            f"({len(excluded)} excluded as compromised):",
            "",
            f"- Personal best: **{format_lap_time(best.lapTime)}**",
            f"- Median: **{format_lap_time(statistics.median(times))}**, "
            f"mean {format_lap_time(mean)}",
            f"- Spread best to worst: **{spread:.3f}s**",
            f"- Standard deviation: **{stdev:.3f}s**",
            "",
        ]

        # Per-sector variability shows *where* inconsistency lives, which the
        # whole-lap standard deviation cannot.
        sector_laps = [lap for lap in usable if lap.sector_times]
        if sector_laps:
            width = min(len(lap.sector_times) for lap in sector_laps)
            lines.append("### Sector consistency")
            lines.append("")
            lines.append(
                "| Sector | Best | Median | Worst | Spread | Std dev |"
            )
            lines.append("|---|---|---|---|---|---|")

            theoretical = 0.0
            worst_sector = (None, 0.0)
            for index in range(width):
                values = [lap.sector_times[index] for lap in sector_laps]
                s_best, s_worst = min(values), max(values)
                s_spread = s_worst - s_best
                s_dev = statistics.pstdev(values) if len(values) > 1 else 0.0
                theoretical += s_best
                if s_spread > worst_sector[1]:
                    worst_sector = (index + 1, s_spread)
                lines.append(
                    f"| S{index + 1} | {s_best:.3f}s | {statistics.median(values):.3f}s "
                    f"| {s_worst:.3f}s | **{s_spread:.3f}s** | {s_dev:.3f}s |"
                )
            lines.append("")

            gain = best.lapTime - theoretical
            lines.append(
                f"**Theoretical best** (your fastest sectors combined): "
                f"**{format_lap_time(theoretical)}** — "
                f"{gain:.3f}s under your actual personal best."
            )
            if worst_sector[0]:
                lines.append("")
                lines.append(
                    f"**Least consistent**: sector {worst_sector[0]}, varying by "
                    f"{worst_sector[1]:.3f}s between your best and worst attempt."
                )
            lines.append("")

        # Trend by day, so improvement isn't confused with session-to-session noise.
        by_day: Dict[str, List[float]] = {}
        for lap in usable:
            by_day.setdefault(lap.startTime[:10], []).append(lap.lapTime)
        if len(by_day) > 1:
            lines.append("### By session date")
            lines.append("")
            lines.append("| Date | Laps | Best | Median | Track temp |")
            lines.append("|---|---|---|---|---|")
            for day in sorted(by_day):
                day_laps = [lap for lap in usable if lap.startTime[:10] == day]
                temps = [lap.trackTemp for lap in day_laps if lap.trackTemp is not None]
                lines.append(
                    f"| {day} | {len(by_day[day])} | {min(by_day[day]):.3f}s "
                    f"| {statistics.median(by_day[day]):.3f}s "
                    f"| {f'{statistics.mean(temps):.1f}°C' if temps else '—'} |"
                )
            lines.append("")

            days = sorted(by_day)
            first_best, last_best = min(by_day[days[0]]), min(by_day[days[-1]])
            change = last_best - first_best
            lines.append(
                f"**Trend**: your best lap went from {first_best:.3f}s on "
                f"{days[0]} to {last_best:.3f}s on {days[-1]} "
                f"({change:+.3f}s)."
            )
            lines.append("")

        if excluded:
            lines.append(
                f"_Excluded {len(excluded)} lap(s): "
                + "; ".join(
                    f"{lap.startTime[:10]} ({verdict.summary})"
                    for lap, verdict in excluded[:4]
                )
                + "._"
            )

        return _ok("\n".join(lines))

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"analyze_consistency failed: {e}", exc_info=True)
        return _err(f"Consistency analysis failed: {e}")


# --------------------------------------------------------------------------
# Other drivers
# --------------------------------------------------------------------------

def _driver_name(lap: LapData) -> str:
    return lap.driver.name if lap.driver else "Unknown"


def _driver_slug(lap: LapData) -> str:
    return lap.driver.slug if lap.driver else ""


def match_driver(laps: Sequence[LapData], query: str) -> str:
    """Resolve a free-text driver name to a slug present in the lap set.

    Accepts a slug, a full name, or any distinctive fragment ("adnan",
    "patterson"). Raises with the candidate list when the query is ambiguous,
    rather than silently picking one.
    """
    wanted = (query or "").strip().lower()
    if not wanted:
        raise ValueError("No driver specified.")

    by_slug: Dict[str, str] = {}
    for lap in laps:
        slug = _driver_slug(lap)
        if slug:
            by_slug[slug] = _driver_name(lap)

    if wanted in by_slug:
        return wanted

    exact = [slug for slug, name in by_slug.items() if name.lower() == wanted]
    if len(exact) == 1:
        return exact[0]

    partial = [
        slug for slug, name in by_slug.items()
        if wanted in name.lower() or wanted in slug
    ]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        names = ", ".join(sorted(by_slug[s] for s in partial))
        raise ValueError(
            f"'{query}' matches several drivers: {names}. Be more specific."
        )

    available = ", ".join(sorted(by_slug.values())[:12])
    raise ValueError(
        f"No driver matching '{query}' has laps here. Available: {available}"
        + (" ..." if len(by_slug) > 12 else "")
        + ". Use `list_drivers` to see everyone."
    )


async def list_drivers(car: str, track: str) -> list[TextContent]:
    """Leaderboard of every driver the user can see on a car/track."""
    try:
        client = create_client()
        me = await client.get_me()
        result = await client.get_accessible_laps(car, track, group="driver")
        laps: List[LapData] = result["laps"]

        if not laps:
            return _err(
                f"No laps visible for **{result['car_resolved']}** at "
                f"**{result['track_resolved']}** from you or your teams."
            )

        my_slug = me.get("slug")
        mine = next((lap for lap in laps if _driver_slug(lap) == my_slug), None)

        lines = [
            f"## Drivers: {result['car_resolved']} at {result['track_resolved']}",
            "",
            f"**{len(laps)} drivers** with laps you can access "
            f"(you and your {len(me.get('teams', []))} team(s)).",
            "",
        ]

        rows = []
        for position, lap in enumerate(laps, start=1):
            is_me = _driver_slug(lap) == my_slug
            gap = (
                lap.lapTime - mine.lapTime
                if mine and not is_me else None
            )
            rows.append([
                str(position),
                _driver_name(lap) + (" (you)" if is_me else ""),
                format_lap_time(lap.lapTime),
                format_gap(gap) if gap is not None else "-",
                "y" if lap.canViewTelemetry else "n",
                lap.startTime[:10],
            ])
        lines.append("```\n" + fixed_table(
            ["#", "driver", "best", "gap", "tel", "set"], rows
        ) + "\n```")
        lines.append("")
        if mine:
            faster = [lap for lap in laps if lap.lapTime < mine.lapTime]
            lines.append(
                f"You are **P{laps.index(mine) + 1} of {len(laps)}**"
                + (
                    f", {len(faster)} driver(s) ahead. Closest is "
                    f"**{_driver_name(faster[-1])}** at "
                    f"{format_gap(faster[-1].lapTime - mine.lapTime)}."
                    if faster else " — you're quickest here. *"
                )
            )
        else:
            lines.append("_You have no lap here yet._")

        lines.append("")
        lines.append(
            "Use `compare_to_driver` with any name above for a corner-by-corner "
            "comparison against them."
        )
        lines.append("")
        lines.append(
            "_Garage61 has no global lap search: this is everyone across your "
            "teams, which is the widest pool the API exposes._"
        )
        lines.append("")
        lines.append(garage61_link_line(result))

        return _ok("\n".join(lines))

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"list_drivers failed: {e}", exc_info=True)
        return _err(f"Could not list drivers: {e}")


async def compare_to_driver(car: str, track: str, driver: str) -> list[TextContent]:
    """Compare the user's best lap against a specific other driver's best."""
    try:
        client = create_client()
        me = await client.get_me()
        result = await client.get_accessible_laps(car, track)
        laps: List[LapData] = result["laps"]

        if not laps:
            return _err(
                f"No laps visible for **{result['car_resolved']}** at "
                f"**{result['track_resolved']}**."
            )

        my_slug = me.get("slug")
        target_slug = match_driver(laps, driver)

        if target_slug == my_slug:
            return _err(
                "That's you. Use `compare_my_laps` to compare your own laps "
                "against each other."
            )

        mine = [lap for lap in laps if _driver_slug(lap) == my_slug]
        theirs = [lap for lap in laps if _driver_slug(lap) == target_slug]

        if not mine:
            return _err(
                f"You have no lap for **{result['car_resolved']}** at "
                f"**{result['track_resolved']}**, so there is nothing to compare."
            )
        if not theirs:
            return _err(f"No laps found for that driver here.")

        # Their compromised laps matter as much as the user's: comparing against
        # someone's spin teaches nothing.
        my_usable, _ = split_usable(mine)
        their_usable, _ = split_usable(theirs)

        my_lap = min(my_usable, key=lambda lap: lap.lapTime)
        their_lap = min(their_usable, key=lambda lap: lap.lapTime)
        their_name = _driver_name(their_lap)

        if not their_lap.canViewTelemetry:
            gap = my_lap.lapTime - their_lap.lapTime
            return _ok(
                f"## You vs {their_name}\n\n"
                f"**{their_name}**: {format_lap_time(their_lap.lapTime)}  \n"
                f"**You**: {format_lap_time(my_lap.lapTime)}  \n"
                f"**Gap**: {format_gap(gap)}\n\n"
                "_Their telemetry isn't shared, so only lap times can be "
                "compared. Their privacy settings control this._"
            )

        their_telemetry = await _load_telemetry(client, their_lap, their_name)
        my_telemetry = await _load_telemetry(client, my_lap, "You")

        corner_map = await _get_corner_map(
            client, car, track, [their_telemetry, my_telemetry]
        )
        comparison = compare_laps(
            their_telemetry,
            my_telemetry,
            sector_times=my_lap.sector_times,
            corner_map=corner_map,
        )

        _comparison_cache[_cache_key(car, track)] = {
            "comparison": comparison,
            "reference_name": their_name,
            "lap_name": "You",
        }

        notes = []
        if my_lap.sector_times and their_lap.sector_times:
            splits = "  ".join(
                f"S{i} {mine_t - theirs_t:+.3f}s"
                for i, (theirs_t, mine_t) in enumerate(
                    zip(their_lap.sector_times, my_lap.sector_times), start=1
                )
            )
            notes.append(f"**Sector splits**: {splits}")

        notes.append(
            f"**Conditions**: you {format_conditions(my_lap)} / "
            f"{their_name} {format_conditions(their_lap)}"
        )
        conditions = []
        if my_lap.trackTemp is not None and their_lap.trackTemp is not None:
            drift = my_lap.trackTemp - their_lap.trackTemp
            if abs(drift) >= 3.0:
                conditions.append(
                    f"track temperature differs by {drift:+.1f}°C, which affects grip"
                )
        if conditions:
            notes.append(f"**Caveat**: {'; '.join(conditions)}.")

        ranked = sorted(laps, key=lambda lap: lap.lapTime)
        seen: List[str] = []
        for lap in ranked:
            slug = _driver_slug(lap)
            if slug and slug not in seen:
                seen.append(slug)
        if my_slug in seen and target_slug in seen:
            notes.append(
                f"_Standings here: {their_name} is P{seen.index(target_slug) + 1}, "
                f"you are P{seen.index(my_slug) + 1}, of {len(seen)} drivers._"
            )

        report = comparison_report(
            comparison,
            title=(
                f"{result['car_resolved']} at {result['track_resolved']} — "
                f"you vs {their_name}"
            ),
            reference_name=their_name,
            lap_name="You",
            notes=notes,
        )
        report += (
            "\n\n_Drill in with `analyze_worst_sections`, or `get_channel_window` "
            "for the raw traces._"
            f"\n\n{garage61_link_line(result)}"
        )
        return _ok(report)

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"compare_to_driver failed: {e}", exc_info=True)
        return _err(f"Comparison failed: {e}")


# --------------------------------------------------------------------------
# Single-lap views
# --------------------------------------------------------------------------

async def get_my_fastest_lap(car: str, track: str) -> list[TextContent]:
    """Summarise the user's fastest lap for a car/track combination."""
    try:
        client = create_client()
        result = await client.get_my_laps(car, track)
        laps: List[LapData] = result["laps"]

        if not laps:
            return _err(
                f"No laps found for **{result['car_resolved']}** at "
                f"**{result['track_resolved']}**. You haven't driven this "
                "combination yet."
            )

        best = min(laps, key=lambda lap: lap.lapTime)
        title = (
            f"Your fastest lap: {result['car_resolved']} at "
            f"{result['track_resolved']}"
        )

        if not best.canViewTelemetry:
            return _ok(
                f"## {title}\n\n"
                f"**Lap time**: {format_lap_time(best.lapTime)}  \n"
                f"**Set on**: {best.startTime[:16].replace('T', ' ')}  \n"
                f"**Conditions**: {format_conditions(best)}\n\n"
                "_No telemetry available for this lap (a Pro plan is usually "
                "required)._"
            )

        telemetry = await _load_telemetry(client, best, "fastest")
        report = lap_summary(telemetry, title, best.sector_times)
        report += (
            f"\n\n**Set on**: {best.startTime[:16].replace('T', ' ')}  \n"
            f"**Conditions**: {format_conditions(best)}  \n"
            f"**Lap ID**: `{best.id}`"
        )
        if len(laps) > 1:
            report += (
                f"\n\n_You have {len(laps)} laps here. Use `list_my_laps` to see "
                "them all, or `compare_my_laps` to see how this compares to your "
                "other attempts._"
            )
        return _ok(report)

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"get_my_fastest_lap failed: {e}", exc_info=True)
        return _err(f"Could not fetch your fastest lap: {e}")


async def get_team_fastest_lap(car: str, track: str) -> list[TextContent]:
    """Summarise the fastest accessible team lap, with the user's gap to it."""
    try:
        client = create_client()
        team_result = await client.get_overall_fastest_lap(car, track)
        team_lap = team_result.get("lap")
        if not team_lap:
            return _err("The API returned no team lap for this combination.")

        my_lap = None
        try:
            my_result = await client.get_my_laps(car, track)
            if my_result["laps"]:
                my_lap = min(my_result["laps"], key=lambda lap: lap.lapTime)
        except ValueError:
            pass

        mine_is_fastest = bool(
            my_lap
            and abs(my_lap.lapTime - team_lap["lap_time"]) < 1e-6
        )

        header = (
            f"## Team fastest lap: {team_lap['car']} at {team_lap['track']}"
            f"{' (yours *)' if mine_is_fastest else ''}"
        )
        parts = [
            header,
            "",
            f"**Driver**: {team_lap['driver']}"
            f"{' (you)' if mine_is_fastest else ''}  ",
            f"**Lap time**: {format_lap_time(team_lap['lap_time'])}  ",
            f"**Lap ID**: `{team_lap['id']}`",
            "",
        ]

        if my_lap and not mine_is_fastest:
            gap = my_lap.lapTime - team_lap["lap_time"]
            parts.append(
                f"**Your personal best**: {format_lap_time(my_lap.lapTime)} "
                f"({format_gap(gap)})"
            )
            parts.append("")
            parts.append(
                "_Use `compare_my_telemetry_to_team` to see where that gap comes from._"
            )
        elif not my_lap:
            parts.append("_You haven't recorded a lap here yet._")

        telemetry_csv = team_result.get("telemetry_csv")
        if telemetry_csv:
            try:
                telemetry = parse_lap_csv(
                    telemetry_csv, team_lap["lap_time"], "team best"
                )
                parts.append("")
                parts.append(
                    lap_summary(telemetry, "Team best lap characteristics")
                    .split("\n", 2)[2]  # drop the duplicate heading and blank line
                )
            except ValueError as e:
                logger.warning(f"Could not parse team telemetry: {e}")
        elif team_result.get("pro_required"):
            parts.append("")
            parts.append("_Telemetry for this lap requires a Garage61 Pro plan._")

        return _ok("\n".join(parts))

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"get_team_fastest_lap failed: {e}", exc_info=True)
        return _err(f"Could not fetch the team fastest lap: {e}")


async def compare_my_telemetry_to_team(car: str, track: str) -> list[TextContent]:
    """Compare the user's fastest lap against the fastest accessible team lap."""
    try:
        client = create_client()

        my_result = await client.get_my_laps(car, track)
        my_laps: List[LapData] = my_result["laps"]
        if not my_laps:
            return _err(
                f"You have no laps for **{my_result['car_resolved']}** at "
                f"**{my_result['track_resolved']}**, so there is nothing to compare."
            )
        my_lap = min(my_laps, key=lambda lap: lap.lapTime)

        team_result = await client.get_overall_fastest_lap(car, track)
        team_lap = team_result.get("lap")
        if not team_lap:
            return _err("No team lap is available for this combination.")

        if abs(team_lap["lap_time"] - my_lap.lapTime) < 1e-6:
            return _err(
                "You already hold the fastest accessible lap here, so there is "
                "no team lap to compare against. Use `compare_my_laps` to "
                "compare against your own earlier laps instead."
            )

        team_csv = team_result.get("telemetry_csv")
        if not team_csv:
            reason = (
                "a Pro plan is required"
                if team_result.get("pro_required")
                else "no telemetry was recorded"
            )
            return _err(
                f"The team's fastest lap has no telemetry available ({reason}), "
                "so a detailed comparison isn't possible. Lap times are still "
                "visible via `get_team_fastest_lap`."
            )

        team_label = f"{team_lap['driver']} (team best)"
        my_label = "You"

        team_telemetry = parse_lap_csv(team_csv, team_lap["lap_time"], team_label)
        my_telemetry = await _load_telemetry(client, my_lap, my_label)

        corner_map = await _get_corner_map(
            client, car, track, [team_telemetry, my_telemetry]
        )
        comparison = compare_laps(
            team_telemetry,
            my_telemetry,
            sector_times=my_lap.sector_times,
            corner_map=corner_map,
        )

        _comparison_cache[_cache_key(car, track)] = {
            "comparison": comparison,
            "reference_name": team_label,
            "lap_name": my_label,
        }

        report = comparison_report(
            comparison,
            title=(
                f"{my_result['car_resolved']} at {my_result['track_resolved']} — "
                f"you vs {team_lap['driver']}"
            ),
            reference_name=team_label,
            lap_name=my_label,
        )
        report += (
            "\n\n_Drill in with `get_channel_window` or "
            "`analyze_worst_sections`._"
            f"\n\n{garage61_link_line(my_result)}"
        )
        return _ok(report)

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"compare_my_telemetry_to_team failed: {e}", exc_info=True)
        return _err(f"Comparison failed: {e}")


# --------------------------------------------------------------------------
# Drill-down into a stored comparison
# --------------------------------------------------------------------------

def _require_comparison(car: str, track: str) -> Dict[str, Any]:
    entry = _comparison_cache.get(_cache_key(car, track))
    if not entry:
        raise ValueError(
            "No comparison is loaded for this car/track. Run `compare_my_laps` "
            "or `compare_my_telemetry_to_team` first."
        )
    return entry


async def analyze_telemetry_range(
    car: str, track: str, start_pct: float, end_pct: float
) -> list[TextContent]:
    """Zoom into one stretch of the lap from the most recent comparison."""
    try:
        entry = _require_comparison(car, track)
        comparison = entry["comparison"]

        if start_pct > end_pct:
            start_pct, end_pct = end_pct, start_pct
        start = max(0.0, min(1.0, start_pct / 100.0))
        end = max(0.0, min(1.0, end_pct / 100.0))
        if end - start < 0.005:
            return _err("That range is too narrow to analyse; use at least 0.5% of the lap.")

        grid = comparison.distance
        last = len(grid) - 1
        start_idx = max(0, min(last, int(round(start * last))))
        end_idx = max(0, min(last, int(round(end * last))))
        if end_idx <= start_idx:
            return _err("That range contains no telemetry samples.")

        trace = comparison.delta_trace
        time_delta = (
            trace[min(end_idx, len(trace) - 1)] - trace[min(start_idx, len(trace) - 1)]
            if trace else 0.0
        )

        overlapping = [
            seg for seg in comparison.segments
            if seg.end_pct > start and seg.start_pct < end
        ]

        lines = [
            f"## Range analysis: {start * 100:.1f}% – {end * 100:.1f}% of the lap",
            "",
            f"**{entry['lap_name']}** vs **{entry['reference_name']}**",
            "",
            f"**Time {'lost' if time_delta > 0 else 'gained'} in this range**: "
            f"**{time_delta:+.3f}s** "
            f"(of {comparison.total_delta:+.3f}s across the full lap)",
            "",
        ]

        if overlapping:
            lines.append("### Segments covered")
            lines.append("")
            for seg in overlapping:
                lines.append(
                    f"- **{seg.name}** "
                    f"({seg.start_pct * 100:.0f}-{seg.end_pct * 100:.0f}%): "
                    f"{seg.time_delta:+.3f}s, "
                    f"min speed {kmh(seg.min_speed)} vs "
                    f"{kmh(seg.ref_min_speed)} km/h"
                )
            lines.append("")

        cumulative = trace[start_idx:end_idx + 1] if trace else []
        if cumulative:
            rebased = [v - cumulative[0] for v in cumulative]
            thinned = downsample(rebased, 40)
            series = uniform_series(
                thinned, grid[start_idx] * 100, grid[end_idx] * 100, fmt="{:+.3f}"
            )
            lines.append("### Gap accumulating through the range")
            lines.append("")
            lines.append(f"```\n{series}\n```")

        return _ok("\n".join(lines))

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"analyze_telemetry_range failed: {e}", exc_info=True)
        return _err(f"Range analysis failed: {e}")


async def analyze_telemetry_sector(car: str, track: str, sector: int) -> list[TextContent]:
    """Zoom into one sector of the lap from the most recent comparison."""
    try:
        entry = _require_comparison(car, track)
        segments = entry["comparison"].segments
        if not segments:
            return _err("The loaded comparison has no segment data.")

        if not 1 <= sector <= len(segments):
            return _err(
                f"This track has {len(segments)} segments in the loaded "
                f"comparison; sector {sector} is out of range."
            )

        target = segments[sector - 1]
        return await analyze_telemetry_range(
            car, track, target.start_pct * 100, target.end_pct * 100
        )

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"analyze_telemetry_sector failed: {e}", exc_info=True)
        return _err(f"Sector analysis failed: {e}")


CHANNEL_ALIASES = {
    "speed": "speed",
    "throttle": "throttle",
    "brake": "brake",
    "gear": "gear",
    "rpm": "rpm",
    "steering": "steering",
    "lat_accel": "lat_accel",
    "long_accel": "long_accel",
    "yaw_rate": "yaw_rate",
    "abs": "abs",
}

# Rendered in the caller's units rather than raw SI, so the numbers can be read
# directly without the caller having to know the API's conventions.
CHANNEL_RENDER = {
    "speed": ("km/h", lambda v: v * MS_TO_KMH, 1),
    "throttle": ("%", lambda v: v * 100, 0),
    "brake": ("%", lambda v: v * 100, 0),
    "gear": ("", lambda v: v, 0),
    "rpm": ("rpm", lambda v: v, 0),
    "steering": ("deg", lambda v: math.degrees(v), 1),
    "lat_accel": ("m/s2", lambda v: v, 1),
    "long_accel": ("m/s2", lambda v: v, 1),
    "yaw_rate": ("deg/s", lambda v: math.degrees(v), 1),
    "abs": ("", lambda v: v, 0),
}


async def get_channel_window(
    car: str,
    track: str,
    start_pct: float = 0.0,
    end_pct: float = 100.0,
    channels: Optional[Sequence[str]] = None,
    points: int = 60,
    corner_number: Optional[int] = None,
) -> list[TextContent]:
    """Return aligned numeric telemetry for a range of the loaded comparison.

    This is the escape hatch from pre-computed summaries: when the caller has a
    hypothesis the standard report cannot settle, it can read the actual traces
    for both laps side by side.
    """
    try:
        entry = _require_comparison(car, track)
        comparison = entry["comparison"]
        reference, lap = comparison.reference, comparison.lap
        if reference is None or lap is None:
            return _err(
                "The loaded comparison has no telemetry attached. Re-run "
                "`compare_my_laps` or `compare_my_telemetry_to_team`."
            )

        # A corner number is the natural way to ask for a braking zone, and
        # numbering is stable per track, so resolve it to a range that includes
        # the approach where the braking actually happens.
        if corner_number is not None:
            match = next(
                (c for c in comparison.corners if c.corner.number == corner_number),
                None,
            )
            if match is None:
                available = ", ".join(
                    str(c.corner.number) for c in comparison.corners
                ) or "none"
                return _err(
                    f"This track has no Turn {corner_number} in the loaded "
                    f"comparison. Available turns: {available}."
                )
            start_pct = max(0.0, match.corner.start_pct * 100 - 5.0)
            end_pct = min(100.0, match.corner.end_pct * 100 + 3.0)

        if start_pct > end_pct:
            start_pct, end_pct = end_pct, start_pct
        start = max(0.0, min(1.0, start_pct / 100.0))
        end = max(0.0, min(1.0, end_pct / 100.0))

        grid = comparison.distance
        last = len(grid) - 1
        start_idx = max(0, min(last, int(round(start * last))))
        end_idx = max(0, min(last, int(round(end * last))))
        if end_idx <= start_idx:
            return _err("That range contains no samples; widen it.")

        requested = [c.strip().lower() for c in (channels or ["speed", "brake", "throttle"])]
        if "all" in requested:
            requested = list(CHANNEL_ALIASES) + ["line"]
        want_line = "line" in requested and bool(comparison.line_offset)
        requested = [c for c in requested if c != "line"]
        resolved, unknown = [], []
        for name in requested:
            target = CHANNEL_ALIASES.get(name)
            if target and target in lap.channels:
                resolved.append(target)
            else:
                unknown.append(name)
        if not resolved:
            return _err(
                f"No usable channels requested. Available: "
                f"{', '.join(sorted(lap.channels))}."
            )

        points = max(5, min(250, points))
        indices = [
            start_idx + int(round(i * (end_idx - start_idx) / (points - 1)))
            for i in range(points)
        ] if points > 1 else [start_idx]

        # Elapsed time relative to the window start, for both laps. Braking is a
        # time-domain phenomenon -- how long the pedal is held, how quickly
        # pressure is built -- and none of that is legible on a distance axis.
        lap_clock = lap.elapsed_time()
        ref_clock = reference.elapsed_time()
        lap_t0 = lap_clock[start_idx] if lap_clock else 0.0
        ref_t0 = ref_clock[start_idx] if ref_clock else 0.0

        short = {"speed": "spd", "throttle": "thr", "brake": "brk", "gear": "gr",
                 "rpm": "rpm", "steering": "str", "lat_accel": "latg",
                 "long_accel": "lngg", "yaw_rate": "yaw", "abs": "abs"}
        header = ["dist%", "t"]
        for name in resolved:
            header.append(f"{short.get(name, name)}L")
            header.append(f"{short.get(name, name)}R")
        if want_line:
            header.append("lineM")
        header.append("\u0394s")

        rows = []
        for i in indices:
            row = [f"{grid[i] * 100:.2f}"]
            row.append(f"{lap_clock[i] - lap_t0:.2f}" if lap_clock else "-")
            for name in resolved:
                _, convert, digits = CHANNEL_RENDER[name]
                lap_values = lap.channel(name)
                ref_values = reference.channel(name)
                row.append(f"{convert(lap_values[i]):.{digits}f}" if lap_values else "-")
                row.append(f"{convert(ref_values[i]):.{digits}f}" if ref_values else "-")
            if want_line:
                line = comparison.line_offset
                row.append(f"{line[i]:+.1f}" if i < len(line) else "-")
            trace = comparison.delta_trace
            row.append(
                f"{trace[i] - trace[start_idx]:+.3f}"
                if trace and i < len(trace) else "-"
            )
            rows.append(row)

        lines = [
            f"## Telemetry {start * 100:.1f}% – {end * 100:.1f}%",
            "",
            f"**{entry['lap_name']}** (L) vs **{entry['reference_name']}** (R), "
            f"{len(indices)} samples.",
            "",
            f"```\n{fixed_table(header, rows)}\n```",
            "",
            "_spd=speed km/h, thr=throttle %, brk=brake %, gr=gear, str=steering deg, latg/lngg=accel m/s2, yaw=yaw rate deg/s, abs=ABS active 0/1, lineM=metres left(+)/right(-) of the reference line. "
            "`t` = seconds elapsed on this lap since the window start; the "
            "reference lap's elapsed time is `t − \u0394s`. `\u0394s` = cumulative gap "
            "since the window start, + means this lap is slower._",
        ]

        # A per-event summary of the pedal shape, so the caller doesn't have to
        # reconstruct it from the sampled rows.
        brake_lines = []
        for label, source in ((entry["lap_name"], lap), (entry["reference_name"], reference)):
            events = [
                e for e in detect_brake_events(
                    source, track_length_m=comparison.track_length_m
                )
                if e.end_pct >= start and e.start_pct <= end
            ]
            for e in events:
                brake_lines.append([
                    label,
                    f"{e.start_pct * 100:.2f}", f"{e.peak_pct * 100:.2f}",
                    f"{e.end_pct * 100:.2f}", f"{e.peak_pressure * 100:.0f}",
                    f"{e.duration_s:.2f}", f"{e.time_to_peak_s:.2f}",
                    f"{e.release_s:.2f}",
                    f"{e.entry_speed * MS_TO_KMH:.0f}>{e.exit_speed * MS_TO_KMH:.0f}",
                ])
        if brake_lines:
            lines.append("")
            lines.append("### Braking applications in this window")
            lines.append("")
            brake_headers = ["lap", "apply%", "peak%", "rel%", "press%",
                             "dur_s", "topk_s", "trail_s", "spd km/h"]
            lines.append(f"```\n{fixed_table(brake_headers, brake_lines)}\n```")
            lines.append(
                "_topk = time to peak pressure; trail = bleed-off after the peak._"
            )
        if unknown:
            lines.append("")
            lines.append(
                f"_Ignored unknown channel(s): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(lap.channels))}._"
            )

        return _ok("\n".join(lines))

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"get_channel_window failed: {e}", exc_info=True)
        return _err(f"Channel window failed: {e}")




async def analyze_corner(
    car: str,
    track: str,
    corner_number: int,
    all_laps: bool = False,
    max_laps: int = 8,
) -> list[TextContent]:
    """Everything measured about one corner: both laps in detail, or the whole
    stint's spread through it when all_laps is set."""
    try:
        import math as _math

        def deg(v, spec="{:.1f}"):
            return spec.format(_math.degrees(v)) if v is not None else "-"

        def num(v, spec="{:.2f}"):
            return spec.format(v) if v is not None else "-"

        entry = _require_comparison(car, track)
        comparison = entry["comparison"]
        match = next(
            (c for c in comparison.corners if c.corner.number == corner_number), None
        )
        if match is None:
            available = ", ".join(str(c.corner.number) for c in comparison.corners) or "none"
            return _err(
                f"No Turn {corner_number} in the loaded comparison. Available: {available}."
            )
        corner = match.corner

        apex_gps = ""
        ref_lap_t = comparison.reference
        if ref_lap_t is not None:
            glat, glon = ref_lap_t.channel("lat"), ref_lap_t.channel("lon")
            if glat and glon:
                gi = int(round(corner.apex_pct * (len(glat) - 1)))
                # For the caller to attach a real-world corner name if it wants
                # one; the server only numbers corners.
                apex_gps = f" Apex GPS {glat[gi]:.5f}, {glon[gi]:.5f}."

        lines = [
            f"## {corner.name} — {corner.apex_pct * 100:.1f}% apex, "
            f"extent {corner.start_pct * 100:.1f}–{corner.end_pct * 100:.1f}%",
            "",
            f"{corner.kind} corner, {corner.direction}, "
            f"{abs(corner.turn_angle):.0f}° heading change, "
            f"detected on {corner.support:.0%} of sampled laps.{apex_gps}",
            "",
        ]

        if not all_laps:
            d, r = match.dynamics, match.ref_dynamics
            lines.append(f"**{entry['lap_name']}** (L) vs **{entry['reference_name']}** (R), "
                         f"Δ {match.time_delta:+.3f}s in this corner.")
            lines.append("")
            ev_rows = []
            for label, dyn in (("L", d), ("R", r)):
                if dyn is None:
                    continue
                ev_rows.append([
                    label,
                    num(dyn.ev_brake_release_pct and dyn.ev_brake_release_pct * 100),
                    num(dyn.ev_steer_peak_pct and dyn.ev_steer_peak_pct * 100),
                    num(dyn.ev_yaw_peak_pct and dyn.ev_yaw_peak_pct * 100),
                    num(dyn.ev_min_speed_pct and dyn.ev_min_speed_pct * 100),
                    num(dyn.ev_throttle_pct and dyn.ev_throttle_pct * 100),
                    num(dyn.event_spread_m, "{:.0f}"),
                ])
            if ev_rows:
                lines.append("### Rotation events (lap-distance % of each)")
                lines.append("")
                lines.append("```\n" + fixed_table(
                    ["lap", "brkRel", "stPeak", "yawPeak", "minSpd", "thr1", "spread_m"],
                    ev_rows) + "\n```")
                lines.append(
                    "_Where brake release, peak steering, peak yaw rate, minimum "
                    "speed and first throttle each fall; spread is the metres "
                    "covering all five._"
                )
                lines.append("")

            det_rows = []
            for label, dyn, brake_ev, line_a in (
                ("L", d, match.brake, match.line_apex_m),
                ("R", r, match.ref_brake, None),
            ):
                if dyn is None:
                    continue
                det_rows.append([
                    label,
                    num(dyn.turn_in_pct and dyn.turn_in_pct * 100),
                    deg(dyn.steer_peak_rad), num(dyn.steer_mid_ratio),
                    deg(dyn.reversal_rad), num(dyn.reversal_s),
                    num(dyn.coupling),
                    num(dyn.brake_at_turn_in and dyn.brake_at_turn_in * 100, "{:.0f}"),
                    deg(dyn.steer_at_release_rad),
                    num(brake_ev.peak_pressure * 100 if brake_ev else None, "{:.0f}"),
                    num(brake_ev.time_to_peak_s if brake_ev else None),
                    num(brake_ev.release_s if brake_ev else None),
                    num(dyn.thr_t50_s), num(dyn.thr_t100_s),
                    str(dyn.thr_dips), num(dyn.partial_hold_s),
                    deg(dyn.yaw_peak_rate) if dyn.yaw_peak_rate is not None else "-",
                    num(dyn.abs_fraction * 100, "{:.0f}"),
                ])
            lines.append("### Input shapes")
            lines.append("")
            lines.append("```\n" + fixed_table(
                ["lap", "tIn%", "pkSt", "mid", "rev", "rev_s", "cpl", "b@tI%",
                 "s@rl", "pb%", "topk", "trail", "t50", "t100", "dips", "hold",
                 "yawPk", "abs%"],
                det_rows) + "\n```")
            lines.append(
                "_tIn first sustained steering; pkSt peak steering deg; mid build "
                "shape (1 linear, <1 progressive); rev largest drop in steering "
                "toward the corner (countersteer included) and its duration; cpl "
                "share of brake release with steering present; b@tI brake % at "
                "turn-in; s@rl steering deg at release; pb peak brake; topk/trail "
                "pedal rise/bleed seconds; t50/t100 seconds to 50/100% throttle; "
                "dips re-lifts; hold seconds at partial throttle with steering "
                "loaded and no acceleration; yawPk peak yaw rate deg/s; abs% of "
                "corner with ABS active._"
            )
            if match.line_entry_m is not None:
                lines.append("")
                lines.append(
                    f"**Line vs reference**: entry {match.line_entry_m:+.1f} m, "
                    f"apex {match.line_apex_m:+.1f} m, exit {match.line_exit_m:+.1f} m "
                    "(+ = left of the reference's direction of travel)."
                )
            flags = (d.flags if d else []) + [f"(ref) {f}" for f in (r.flags if r else [])]
            if flags:
                lines.append("")
                lines.append("**Flagged**: " + " | ".join(flags))
            lines.append("")
            lines.append(
                f"_Raw traces: `get_channel_window` with corner_number={corner_number}._"
            )
            return _ok("\n".join(lines))

        # ---- stint mode: this corner across every representative lap ----
        client = create_client()
        result = await client.get_my_laps(car, track)
        usable, excluded = split_usable(result["laps"])
        chosen = usable[-max_laps:]
        best = min(usable, key=lambda lap: lap.lapTime)
        if best.id not in {lap.id for lap in chosen}:
            chosen = [best] + chosen[-(max_laps - 1):]

        from telemetry import (
            detect_brake_events, assign_brakes_to_corners,
            compute_corner_dynamics, estimate_track_length,
        )
        rows = []
        tin_vals, pb_vals, min_vals, rev_vals = [], [], [], []
        for lap_rec in chosen:
            csv_data = await client.get_lap_telemetry_csv(lap_rec.id)
            if not csv_data:
                continue
            telem = parse_lap_csv(csv_data, lap_rec.lapTime, lap_rec.startTime[:10])
            length = estimate_track_length(telem)
            brake_map = assign_brakes_to_corners(
                detect_brake_events(telem, track_length_m=length), [corner]
            )
            dyn = compute_corner_dynamics(telem, corner, brake_map.get(corner.number), length)
            last = len(telem.distance) - 1
            lo = max(0, min(last, int(round(corner.start_pct * last))))
            hi = max(0, min(last, int(round(corner.end_pct * last))))
            min_spd = min(telem.speed[lo:hi + 1]) * MS_TO_KMH if telem.speed else None
            pb = brake_map.get(corner.number)
            if dyn.turn_in_pct is not None:
                tin_vals.append(dyn.turn_in_pct * 100)
            if pb:
                pb_vals.append(pb.peak_pressure * 100)
            if min_spd is not None:
                min_vals.append(min_spd)
            rev_vals.append(_math.degrees(dyn.reversal_rad))
            rows.append([
                lap_rec.startTime[5:16].replace("T", " "),
                format_lap_time(lap_rec.lapTime) + (" *" if lap_rec.id == best.id else ""),
                num(dyn.turn_in_pct and dyn.turn_in_pct * 100),
                num(pb.peak_pressure * 100 if pb else None, "{:.0f}"),
                num(pb.release_s if pb else None),
                num(dyn.coupling),
                num(min_spd, "{:.0f}"),
                deg(dyn.reversal_rad, "{:.0f}"),
                num(dyn.thr_t100_s),
                num(dyn.partial_hold_s),
                ";".join(dyn.flags) if dyn.flags else "-",
            ])

        lines.append(f"### {corner.name} across {len(rows)} laps "
                     f"({len(excluded)} compromised laps excluded, * = personal best)")
        lines.append("")
        lines.append("```\n" + fixed_table(
            ["lap", "time", "tIn%", "pb%", "trail", "cpl", "minSpd", "rev", "t100", "hold", "flags"],
            rows) + "\n```")

        def spread(vals):
            if len(vals) < 2:
                return "n/a"
            mean = statistics.mean(vals)
            return f"spread {max(vals) - min(vals):.2f}, sd {statistics.pstdev(vals):.2f}, mean {mean:.2f}"

        lines.append("")
        lines.append(
            f"**Variation**: turn-in {spread(tin_vals)} (pct-points) · "
            f"peak brake {spread(pb_vals)} (%) · min speed {spread(min_vals)} (km/h) · "
            f"reversal {spread(rev_vals)} (deg)."
        )
        lines.append("")
        lines.append(
            "_Same measurement definitions as the single-corner view; laps in "
            "session order._"
        )
        return _ok("\n".join(lines))

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"analyze_corner failed: {e}", exc_info=True)
        return _err(f"Corner analysis failed: {e}")


async def analyze_worst_sections(car: str, track: str) -> list[TextContent]:
    """Rank the segments where the most time is lost in the loaded comparison."""
    try:
        entry = _require_comparison(car, track)
        comparison = entry["comparison"]

        losses = sorted(
            [s for s in comparison.segments if s.time_delta > 0],
            key=lambda s: s.time_delta,
            reverse=True,
        )
        if not losses:
            return _ok(
                f"## No time lost\n\n**{entry['lap_name']}** is at least as quick "
                f"as **{entry['reference_name']}** in every segment."
            )

        # Prefer corners when available: a sector spanning a quarter of the lap
        # can't tell you which corner cost the time.
        if comparison.corners:
            corner_losses = sorted(
                [c for c in comparison.corners if c.time_delta > 0.005],
                key=lambda c: c.time_delta,
                reverse=True,
            )
            if corner_losses:
                total = sum(c.time_delta for c in corner_losses)
                lines = [
                    f"## Where {entry['lap_name']} loses time to "
                    f"{entry['reference_name']}",
                    "",
                    f"Net gap **{comparison.total_delta:+.3f}s**; "
                    f"{total:.3f}s lost across {len(corner_losses)} corners.",
                    "",
                    corner_table(corner_losses, limit=6),
                    "",
                    "_Use `get_channel_window` on a corner's distance range to "
                    "inspect the raw traces, or `analyze_telemetry_range` for a "
                    "summary of any stretch._",
                ]
                return _ok("\n".join(lines))

        gains = [s for s in comparison.segments if s.time_delta < 0]
        total_lost = sum(s.time_delta for s in losses)
        total_gained = sum(s.time_delta for s in gains)

        lines = [
            f"## Where {entry['lap_name']} loses time to {entry['reference_name']}",
            "",
            f"Net gap: **{comparison.total_delta:+.3f}s** "
            f"— {total_lost:.3f}s lost across {len(losses)} "
            f"segment{'s' if len(losses) != 1 else ''}"
            + (
                f", {abs(total_gained):.3f}s clawed back in "
                f"{len(gains)} other{'s' if len(gains) != 1 else ''}."
                if gains else "."
            ),
            "",
        ]

        for rank, seg in enumerate(losses[:5], start=1):
            # Share of the losses, not of the net gap: with time gained
            # elsewhere the net can be smaller than a single segment's loss.
            share = seg.time_delta / total_lost * 100 if total_lost else 0
            lines.append(
                f"### {rank}. {seg.name} "
                f"({seg.start_pct * 100:.0f}-{seg.end_pct * 100:.0f}%) — "
                f"{seg.time_delta:+.3f}s ({share:.0f}% of all time lost)"
            )
            lines.append("")
            lines.append(
                f"- Minimum speed: **{kmh(seg.min_speed)} km/h** vs "
                f"**{kmh(seg.ref_min_speed)} km/h** "
                f"({(seg.min_speed - seg.ref_min_speed) * MS_TO_KMH:+.1f} km/h)"
            )
            lines.append(
                f"- Average speed: **{kmh(seg.avg_speed)} km/h** vs "
                f"**{kmh(seg.ref_avg_speed)} km/h**"
            )
            if seg.brake_point_pct is not None and seg.ref_brake_point_pct is not None:
                shift = (seg.brake_point_pct - seg.ref_brake_point_pct) * 100
                lines.append(
                    f"- First brake input at **{seg.brake_point_pct * 100:.2f}%** vs "
                    f"**{seg.ref_brake_point_pct * 100:.2f}%** "
                    f"({abs(shift):.2f}pp {'later' if shift > 0 else 'earlier'})"
                )
            lines.append(
                f"- Full throttle: **{seg.full_throttle_pct * 100:.0f}%** vs "
                f"**{seg.ref_full_throttle_pct * 100:.0f}%** of the segment"
            )
            lines.append("")

        lines.append(
            "_Use `analyze_telemetry_range` with a segment's percentages to zoom "
            "in further._"
        )
        return _ok("\n".join(lines))

    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        logger.error(f"analyze_worst_sections failed: {e}", exc_info=True)
        return _err(f"Worst-section analysis failed: {e}")


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

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
                modern_cars = [car for car in cache.cars if not cache._is_legacy_car(car["name"])]
                sorted_cars = cache._sort_cars_by_relevance(modern_cars, False)
                note = " (modern cars only)"

            car_names = [car["name"] for car in sorted_cars[:30]]
            response = f"**Available cars{note}:**\n\n" + "\n".join(f"• {car}" for car in car_names)

            if len(sorted_cars) > 30:
                response += f"\n\n... and {len(sorted_cars) - 30} more cars. Use a search term to filter."

            if not show_legacy:
                legacy_count = len([car for car in cache.cars if cache._is_legacy_car(car["name"])])
                if legacy_count > 0:
                    response += f"\n\n*Note: {legacy_count} legacy cars hidden. Use 'legacy' in search to see all versions.*"

        return [TextContent(type="text", text=response)]

    except Exception as e:
        logger.error(f"Exception in list_cars: {str(e)}", exc_info=True)
        return [TextContent(type="text", text=f"Error listing cars: {str(e)}")]


async def list_tracks(search_term: str = "") -> list[TextContent]:
    """List available tracks with all variants, optionally filtered by search term."""
    logger.debug(f"list_tracks called with search_term: '{search_term}'")
    try:
        cache = get_cache()

        if search_term:
            # Filter tracks by search term
            filtered_tracks = []
            search_lower = search_term.lower()

            for track in cache.tracks:
                track_name = track.get("name", "").lower()
                variant_name = track.get("variant", "").lower()
                full_name = f"{track_name} {variant_name}".strip()

                if (search_lower in track_name or
                    search_lower in variant_name or
                    search_lower in full_name or
                    any(word in full_name for word in search_lower.split())):
                    filtered_tracks.append(track)

            if filtered_tracks:
                # Group by base track name and show variants
                track_groups = {}
                for track in filtered_tracks:
                    base_name = track.get("name", "")
                    if base_name not in track_groups:
                        track_groups[base_name] = []
                    track_groups[base_name].append(track)

                response = f"**Tracks matching '{search_term}':**\n\n"

                for base_name, variants in track_groups.items():
                    if len(variants) == 1:
                        track = variants[0]
                        full_name = cache._format_track_name_with_variant(track)
                        response += f"• **{full_name}**\n"
                    else:
                        response += f"**{base_name}:**\n"
                        # Sort variants by preference
                        sorted_variants = sorted(
                            variants,
                            key=lambda t: cache._get_track_variant_score(t.get("variant", "")),
                            reverse=True,
                        )
                        for track in sorted_variants:
                            full_name = cache._format_track_name_with_variant(track)
                            preference = cache._get_track_variant_score(track.get("variant", ""))
                            response += f"  • **{full_name}** (preference: {preference})\n"
                    response += "\n"
            else:
                # Try fuzzy matching
                suggestions = cache.get_track_suggestions(search_term, limit=10)
                if suggestions:
                    response = f"**No exact matches for '{search_term}'. Did you mean:**\n\n" + "\n".join(f"• {track}" for track in suggestions)
                else:
                    response = f"No tracks found matching '{search_term}'"
        else:
            # List all tracks grouped by base name
            track_groups = {}
            for track in cache.tracks:
                base_name = track.get("name", "")
                if base_name not in track_groups:
                    track_groups[base_name] = []
                track_groups[base_name].append(track)

            response = "**Available tracks:**\n\n"
            count = 0

            for base_name in sorted(track_groups.keys()):
                if count >= 25:  # Limit output
                    remaining = len(track_groups) - count
                    response += f"\n... and {remaining} more tracks. Use a search term to filter."
                    break

                variants = track_groups[base_name]
                if len(variants) == 1:
                    track = variants[0]
                    full_name = cache._format_track_name_with_variant(track)
                    response += f"• **{full_name}**\n"
                else:
                    response += f"**{base_name}** ({len(variants)} variants)\n"

                count += 1

        return [TextContent(type="text", text=response)]

    except Exception as e:
        logger.error(f"Exception in list_tracks: {str(e)}", exc_info=True)
        return [TextContent(type="text", text=f"Error listing tracks: {str(e)}")]


# --------------------------------------------------------------------------
# Tool definitions for MCP
# --------------------------------------------------------------------------

_CAR_DESC = "Exact car name from list_cars (e.g. 'Porsche 911 GT3 R (992)')"
_TRACK_DESC = (
    "Exact track name including variant from list_tracks "
    "(e.g. 'Circuit de Spa-Francorchamps - Grand Prix Pits')"
)
_SELECTOR_DESC = (
    "Which lap to use: 'fastest', 'slowest', 'latest', 'oldest', a lap number "
    "from list_my_laps, or a date such as '2026-04-04'"
)

LIST_CARS_TOOL = Tool(
    name="list_cars",
    description=(
        "List available cars with modern cars prioritized by default. Call this "
        "FIRST when the user mentions a car, to find the exact name. Modern cars "
        "like '992' are preferred over legacy '991' unless legacy is requested."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "search_term": {
                "type": "string",
                "description": "Filter cars by name (e.g. 'porsche', 'gt3')",
            },
            "show_legacy": {
                "type": "boolean",
                "description": "Include older car versions",
            },
        },
    },
)

LIST_TRACKS_TOOL = Tool(
    name="list_tracks",
    description=(
        "List available tracks with all variants and exact names. Call this FIRST "
        "when the user mentions a track, to get the correctly formatted name with "
        "its variant (e.g. 'Track Name - Variant')."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "search_term": {
                "type": "string",
                "description": "Filter tracks by name (e.g. 'spa', 'monza')",
            },
        },
    },
)

LIST_MY_LAPS_TOOL = Tool(
    name="list_my_laps",
    description=(
        "List ALL of the user's own laps for a car/track combination, with dates, "
        "lap times, sector splits, and the conditions each was set in. Use this to "
        "answer questions about progress over time, or to pick two laps to feed "
        "into compare_my_laps."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
            "clean_only": {
                "type": "boolean",
                "description": "Only include laps flagged clean (no offtracks)",
            },
        },
        "required": ["car", "track"],
    },
)

COMPARE_MY_LAPS_TOOL = Tool(
    name="compare_my_laps",
    description=(
        "**PRIMARY TOOL FOR TRACKING YOUR OWN PROGRESS** - Compare two of the "
        "user's OWN laps on the same car and track and show exactly where the time "
        "differs, using a real delta-time calculation from the speed traces. Use "
        "this when the user wants to know how they've improved over time, why a "
        "recent lap was slower than their personal best, or where they gain and "
        "lose time against their own benchmark. Defaults to comparing the most "
        "recent lap against the personal best."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
            "reference": {
                "type": "string",
                "description": f"Benchmark lap. {_SELECTOR_DESC}. Defaults to 'fastest'.",
            },
            "compared": {
                "type": "string",
                "description": f"Lap measured against the benchmark. {_SELECTOR_DESC}. Defaults to 'latest'.",
            },
        },
        "required": ["car", "track"],
    },
)

MY_FASTEST_LAP_TOOL = Tool(
    name="get_my_fastest_lap",
    description=(
        "Get a summary of the user's personal best lap for a car/track: lap time, "
        "sector splits, speed and pedal characteristics, and the conditions. For "
        "comparing laps use compare_my_laps or compare_my_telemetry_to_team instead."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
        },
        "required": ["car", "track"],
    },
)

TEAM_FASTEST_LAP_TOOL = Tool(
    name="get_team_fastest_lap",
    description=(
        "Get the fastest lap from the user's team (including their own laps) for a "
        "car/track, showing who holds it and the user's gap to it."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
        },
        "required": ["car", "track"],
    },
)

COMPARE_TELEMETRY_TOOL = Tool(
    name="compare_my_telemetry_to_team",
    description=(
        "Compare the user's fastest lap against the team's fastest lap, showing "
        "where the gap is created with a real delta-time calculation. Use this when "
        "the user wants to know how to catch a faster teammate. To compare against "
        "their own earlier laps instead, use compare_my_laps."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
        },
        "required": ["car", "track"],
    },
)

ANALYZE_TELEMETRY_RANGE_TOOL = Tool(
    name="analyze_telemetry_range",
    description=(
        "Zoom into a specific distance range of the lap (e.g. 45% to 60%) from the "
        "most recent comparison. Run compare_my_laps or "
        "compare_my_telemetry_to_team first."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
            "start_pct": {
                "type": "number",
                "description": "Start of the range as a percentage of lap distance (0-100)",
            },
            "end_pct": {
                "type": "number",
                "description": "End of the range as a percentage of lap distance (0-100)",
            },
        },
        "required": ["car", "track", "start_pct", "end_pct"],
    },
)

ANALYZE_TELEMETRY_SECTOR_TOOL = Tool(
    name="analyze_telemetry_sector",
    description=(
        "Zoom into one sector of the lap from the most recent comparison. Sectors "
        "follow the track's real timing splits. Run compare_my_laps or "
        "compare_my_telemetry_to_team first."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
            "sector": {
                "type": "number",
                "description": "Sector number, starting at 1",
            },
        },
        "required": ["car", "track", "sector"],
    },
)

ANALYZE_WORST_SECTIONS_TOOL = Tool(
    name="analyze_worst_sections",
    description=(
        "Rank the parts of the lap where the most time is lost in the most recent "
        "comparison, with the likely cause for each. Run compare_my_laps or "
        "compare_my_telemetry_to_team first."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
        },
        "required": ["car", "track"],
    },
)

LIST_DRIVERS_TOOL = Tool(
    name="list_drivers",
    description=(
        "List every driver whose laps the user can see for a car/track — "
        "themselves plus everyone across their Garage61 teams — as a leaderboard "
        "with each driver's best lap, the user's gap to them, and whether their "
        "telemetry is shared. Use this to answer 'who else has driven this?', "
        "'where do I rank?', or to find someone to compare against. Note that "
        "Garage61 has no global lap search, so this is limited to the user's "
        "teams by design."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
        },
        "required": ["car", "track"],
    },
)

COMPARE_TO_DRIVER_TOOL = Tool(
    name="compare_to_driver",
    description=(
        "Compare the user's best lap against a SPECIFIC other driver's best, "
        "corner by corner, with a real delta-time calculation. Use this when the "
        "user names someone they want to measure themselves against. The driver "
        "can be given as a full name or a fragment such as a surname. Call "
        "list_drivers first if you don't know who is available. For the fastest "
        "driver overall use compare_my_telemetry_to_team instead."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
            "driver": {
                "type": "string",
                "description": (
                    "Who to compare against: full name, surname, or slug "
                    "(e.g. 'Alex Patterson', 'patterson', 'alex-patterson')"
                ),
            },
        },
        "required": ["car", "track", "driver"],
    },
)

ANALYZE_CONSISTENCY_TOOL = Tool(
    name="analyze_consistency",
    description=(
        "Analyse ALL of the user's laps for a car/track at once: lap-time spread "
        "and standard deviation, which sector they are least consistent in, their "
        "theoretical best lap from their fastest sectors, and how their pace has "
        "moved session to session. Use this for questions about consistency, "
        "whether they are actually improving, or how much time is available "
        "without going faster anywhere new."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
        },
        "required": ["car", "track"],
    },
)

ANALYZE_CORNER_TOOL = Tool(
    name="analyze_corner",
    description=(
        "Everything measured about ONE corner. Default: both laps of the most "
        "recent comparison in full detail — rotation-event positions (brake "
        "release, peak steering, peak yaw rate, min speed, first throttle) and "
        "their convergence, steering shape (turn-in, peak, build ratio, largest "
        "reversal), brake/steering overlap, pedal shape, throttle ramp, line "
        "offset vs the reference, and ABS. With all_laps=true: the same corner "
        "across every representative lap in the stint, with the spread of "
        "turn-in, peak pressure, min speed and correction size — use this to "
        "find WHERE inconsistency lives when sector-level numbers are too "
        "coarse. Run a comparison tool first."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
            "corner_number": {
                "type": "number",
                "description": "Turn number from the comparison output",
            },
            "all_laps": {
                "type": "boolean",
                "description": (
                    "Analyse this corner across the whole stint instead of the "
                    "two compared laps"
                ),
            },
            "max_laps": {
                "type": "number",
                "description": "Stint mode: how many recent laps to include (default 8)",
            },
        },
        "required": ["car", "track", "corner_number"],
    },
)

GET_CHANNEL_WINDOW_TOOL = Tool(
    name="get_channel_window",
    description=(
        "Return the raw aligned telemetry for both laps across part of the most "
        "recent comparison, as a numeric table with BOTH a distance and an "
        "elapsed-time axis, plus a summary of every braking application in the "
        "window. Use this to read brake shape and pedal technique directly: how "
        "long each lap is on the brakes, how fast pressure is built, how long it "
        "is trailed off, and whether a slower exit came from an early release, a "
        "mid-corner lift, or a gear choice. Pass corner_number to frame the "
        "window on a turn and its braking approach automatically. Run "
        "compare_my_laps, compare_to_driver or compare_my_telemetry_to_team first."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "car": {"type": "string", "description": _CAR_DESC},
            "track": {"type": "string", "description": _TRACK_DESC},
            "corner_number": {
                "type": "number",
                "description": (
                    "Frame the window on this turn number and its braking "
                    "approach. Takes precedence over start_pct/end_pct. Turn "
                    "numbers are stable for a given car/track."
                ),
            },
            "start_pct": {
                "type": "number",
                "description": "Start of the range as a percentage of lap distance (0-100)",
            },
            "end_pct": {
                "type": "number",
                "description": "End of the range as a percentage of lap distance (0-100)",
            },
            "channels": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Channels to return. Any of: speed, throttle, brake, gear, "
                    "rpm, steering, lat_accel, long_accel, yaw_rate, abs, line — or 'all' for every "
                    "channel at once. Defaults to speed, brake, throttle."
                ),
            },
            "points": {
                "type": "number",
                "description": "How many samples to return (5-250, default 60)",
            },
        },
        "required": ["car", "track"],
    },
)

ALL_TOOLS = [
    LIST_CARS_TOOL,
    LIST_TRACKS_TOOL,
    LIST_MY_LAPS_TOOL,
    COMPARE_MY_LAPS_TOOL,
    ANALYZE_CONSISTENCY_TOOL,
    MY_FASTEST_LAP_TOOL,
    LIST_DRIVERS_TOOL,
    COMPARE_TO_DRIVER_TOOL,
    TEAM_FASTEST_LAP_TOOL,
    COMPARE_TELEMETRY_TOOL,
    ANALYZE_WORST_SECTIONS_TOOL,
    ANALYZE_TELEMETRY_RANGE_TOOL,
    ANALYZE_TELEMETRY_SECTOR_TOOL,
    ANALYZE_CORNER_TOOL,
    GET_CHANNEL_WINDOW_TOOL,
]
