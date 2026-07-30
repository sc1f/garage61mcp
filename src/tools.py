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
    corner_table,
    format_conditions,
    format_gap,
    format_lap_time,
    kmh,
    lap_summary,
)
from lapquality import split_usable
from telemetry import compare_laps, downsample, parse_lap_csv

logger = logging.getLogger(__name__)

# Comparison results, kept so the analyze_* tools can drill into a comparison
# without re-fetching and re-parsing several megabytes of telemetry.
_comparison_cache: Dict[str, Any] = {}


def _cache_key(car: str, track: str) -> str:
    return f"{car.strip().lower()}::{track.strip().lower()}"


def _err(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=f"**Error**: {message}")]


def _ok(message: str) -> list[TextContent]:
    return [TextContent(type="text", text=message)]


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
            "| # | Date | Lap time | Gap to PB | Sectors | Telemetry | Conditions |",
            "|---|---|---|---|---|---|---|",
        ]

        for index, lap in enumerate(laps, start=1):
            sectors = (
                " / ".join(f"{t:.2f}" for t in lap.sector_times)
                if lap.sector_times else "—"
            )
            gap = lap.lapTime - best.lapTime
            if lap.id == best.id:
                marker = " 🏆"
            elif lap.id in excluded_ids:
                marker = " ⚠️"
            else:
                marker = ""
            lines.append(
                f"| {index} | {lap.startTime[:16].replace('T', ' ')} "
                f"| **{format_lap_time(lap.lapTime)}**{marker} "
                f"| {format_gap(gap) if gap else '—'} "
                f"| {sectors} "
                f"| {'yes' if lap.canViewTelemetry else 'no'} "
                f"| {format_conditions(lap)} |"
            )

        lines.append("")

        if excluded:
            lines.append("**⚠️ Compromised laps (excluded from comparisons):**")
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

        comparison = compare_laps(
            ref_telemetry, cmp_telemetry, sector_times=ref_lap.sector_times
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

        conditions = []
        if ref_lap.trackTemp is not None and cmp_lap.trackTemp is not None:
            drift = cmp_lap.trackTemp - ref_lap.trackTemp
            if abs(drift) >= 2.0:
                conditions.append(
                    f"track temperature differs by {drift:+.1f}°C "
                    f"({ref_lap.trackTemp:.1f}°C vs {cmp_lap.trackTemp:.1f}°C), "
                    "which affects grip"
                )
        if ref_lap.fuelLevel is not None and cmp_lap.fuelLevel is not None:
            fuel = cmp_lap.fuelLevel - ref_lap.fuelLevel
            if abs(fuel) >= 5.0:
                conditions.append(f"fuel load differs by {fuel:+.1f}L")
        if conditions:
            notes.append(f"⚠️ **Caveat**: {'; '.join(conditions)}.")

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
            "\n\n_Drill into any part of the lap with `analyze_telemetry_range` "
            "(for example 45 to 60 percent), or `analyze_worst_sections`._"
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
            f"{' (yours 🏆)' if mine_is_fastest else ''}"
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

        comparison = compare_laps(
            team_telemetry, my_telemetry, sector_times=my_lap.sector_times
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
            "\n\n_Drill in with `analyze_telemetry_range` or "
            "`analyze_worst_sections`._"
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
            step = max(1, len(cumulative) // 15)
            marks = []
            for i in range(0, len(cumulative), step):
                pct = grid[start_idx + i] * 100
                marks.append(f"{pct:5.1f}%:{cumulative[i] - cumulative[0]:+.3f}")
            lines.append("### Gap accumulating through the range")
            lines.append("")
            lines.append(f"```\n{'  '.join(marks)}\n```")

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
}


async def get_channel_window(
    car: str,
    track: str,
    start_pct: float,
    end_pct: float,
    channels: Optional[Sequence[str]] = None,
    points: int = 40,
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

        points = max(5, min(120, points))
        indices = [
            start_idx + int(round(i * (end_idx - start_idx) / (points - 1)))
            for i in range(points)
        ] if points > 1 else [start_idx]

        header = ["dist%"]
        for name in resolved:
            unit = CHANNEL_RENDER[name][0]
            suffix = f" {unit}" if unit else ""
            header.append(f"{name}{suffix} (lap)")
            header.append(f"{name}{suffix} (ref)")
        header.append("delta s")

        rows = []
        for i in indices:
            row = [f"{grid[i] * 100:.2f}"]
            for name in resolved:
                _, convert, digits = CHANNEL_RENDER[name]
                lap_values = lap.channel(name)
                ref_values = reference.channel(name)
                row.append(f"{convert(lap_values[i]):.{digits}f}" if lap_values else "—")
                row.append(f"{convert(ref_values[i]):.{digits}f}" if ref_values else "—")
            trace = comparison.delta_trace
            row.append(
                f"{trace[i] - trace[start_idx]:+.3f}"
                if trace and i < len(trace) else "—"
            )
            rows.append(row)

        lines = [
            f"## Telemetry {start * 100:.1f}% – {end * 100:.1f}%",
            "",
            f"**{entry['lap_name']}** (lap) vs **{entry['reference_name']}** (ref), "
            f"{len(indices)} samples.",
            "",
            "| " + " | ".join(header) + " |",
            "|" + "---|" * len(header),
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in rows)
        lines.append("")
        lines.append(
            "_`delta s` is time gained (−) or lost (+) since the start of this "
            "window. Steering is degrees, positive to the right._"
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

GET_CHANNEL_WINDOW_TOOL = Tool(
    name="get_channel_window",
    description=(
        "Return the raw aligned telemetry values for both laps across a distance "
        "range of the most recent comparison, as a numeric table. Use this when "
        "the summaries don't settle a question and you want to read the actual "
        "traces — for example to see whether a slower exit came from an early "
        "brake release, a lift mid-corner, or a gear choice. Run compare_my_laps "
        "or compare_my_telemetry_to_team first."
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
            "channels": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Channels to return. Any of: speed, throttle, brake, gear, "
                    "rpm, steering, lat_accel, long_accel. "
                    "Defaults to speed, brake, throttle."
                ),
            },
            "points": {
                "type": "number",
                "description": "How many samples to return (5-120, default 40)",
            },
        },
        "required": ["car", "track", "start_pct", "end_pct"],
    },
)

ALL_TOOLS = [
    LIST_CARS_TOOL,
    LIST_TRACKS_TOOL,
    LIST_MY_LAPS_TOOL,
    COMPARE_MY_LAPS_TOOL,
    ANALYZE_CONSISTENCY_TOOL,
    MY_FASTEST_LAP_TOOL,
    TEAM_FASTEST_LAP_TOOL,
    COMPARE_TELEMETRY_TOOL,
    ANALYZE_WORST_SECTIONS_TOOL,
    ANALYZE_TELEMETRY_RANGE_TOOL,
    ANALYZE_TELEMETRY_SECTOR_TOOL,
    GET_CHANNEL_WINDOW_TOOL,
]
