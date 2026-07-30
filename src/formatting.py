"""Rendering helpers shared by the MCP tools.

The guiding constraint: a tool result is read by a model with a finite context.
Returning eight thousand rows of CSV is the same as returning nothing, so
everything here summarises rather than dumps.
"""

from typing import List, Optional, Sequence

from telemetry import (
    MS_TO_KMH,
    Comparison,
    CornerComparison,
    LapTelemetry,
    Segment,
    downsample,
)


def format_lap_time(seconds: Optional[float]) -> str:
    """Render seconds as m:ss.mmm, the way lap times are normally read."""
    if seconds is None:
        return "-"
    minutes, remainder = divmod(abs(seconds), 60)
    sign = "-" if seconds < 0 else ""
    if minutes:
        return f"{sign}{int(minutes)}:{remainder:06.3f}"
    return f"{sign}{remainder:.3f}s"


def format_gap(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    return f"{seconds:+.3f}s"


def kmh(speed_ms: float) -> str:
    return f"{speed_ms * MS_TO_KMH:.1f}"


def format_conditions(lap) -> str:
    """One-line summary of the conditions a lap was set in."""
    bits: List[str] = []
    if lap.trackTemp is not None:
        bits.append(f"track {lap.trackTemp:.1f}°C")
    if lap.airTemp is not None:
        bits.append(f"air {lap.airTemp:.1f}°C")
    if lap.trackUsage is not None:
        bits.append(f"usage {lap.trackUsage}")
    if lap.fuelLevel is not None:
        bits.append(f"fuel {lap.fuelLevel:.1f}L")
    return ", ".join(bits) if bits else "-"


def segment_table(segments: Sequence[Segment], reference_name: str, lap_name: str) -> str:
    """Sector-level time attribution, matching the sim's own timing splits."""
    if not segments:
        return "_No sector data available._"

    lines = [
        "| Sector | Time delta | Min speed | Avg speed | Full throttle |",
        "|---|---|---|---|---|",
    ]
    for seg in segments:
        lines.append(
            f"| {seg.name} ({seg.start_pct * 100:.0f}-{seg.end_pct * 100:.0f}%) "
            f"| **{seg.time_delta:+.3f}s** "
            f"| {kmh(seg.min_speed)} vs {kmh(seg.ref_min_speed)} km/h "
            f"| {kmh(seg.avg_speed)} vs {kmh(seg.ref_avg_speed)} km/h "
            f"| {seg.full_throttle_pct * 100:.0f}% vs {seg.ref_full_throttle_pct * 100:.0f}% |"
        )
    lines.append("")
    lines.append(f"_Each cell reads `{lap_name} vs {reference_name}`._")
    return "\n".join(lines)


def _pct(value: Optional[float]) -> str:
    return f"{value * 100:.2f}%" if value is not None else "—"


def _delta_pp(a: Optional[float], b: Optional[float]) -> str:
    """Signed difference between two lap-distance fractions, in percentage points."""
    if a is None or b is None:
        return "—"
    return f"{(a - b) * 100:+.2f}pp"


def corner_table(corners: Sequence[CornerComparison], limit: Optional[int] = None) -> str:
    """Per-corner facts. No interpretation -- that is the reader's job."""
    if not corners:
        return "_No corners detected on this lap._"

    ordered = sorted(corners, key=lambda c: abs(c.time_delta), reverse=True)
    if limit:
        ordered = ordered[:limit]
    ordered.sort(key=lambda c: c.corner.apex_pct)

    lines = [
        "| Corner | Delta | Entry km/h | Apex km/h | Exit km/h | Brake pt | Throttle pt | Gear |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in ordered:
        gear = (
            f"{c.apex_gear} vs {c.ref_apex_gear}"
            if c.apex_gear is not None and c.ref_apex_gear is not None
            else "—"
        )
        lines.append(
            f"| **{c.corner.name}** @{c.corner.apex_pct * 100:.0f}% "
            f"| **{c.time_delta:+.3f}s** "
            f"| {c.entry_speed * MS_TO_KMH:.0f} vs {c.ref_entry_speed * MS_TO_KMH:.0f} "
            f"| {c.apex_speed * MS_TO_KMH:.0f} vs {c.ref_apex_speed * MS_TO_KMH:.0f} "
            f"| {c.exit_speed * MS_TO_KMH:.0f} vs {c.ref_exit_speed * MS_TO_KMH:.0f} "
            f"| {_pct(c.brake_point_pct)} ({_delta_pp(c.brake_point_pct, c.ref_brake_point_pct)}) "
            f"| {_pct(c.throttle_pickup_pct)} ({_delta_pp(c.throttle_pickup_pct, c.ref_throttle_pickup_pct)}) "
            f"| {gear} |"
        )
    lines.append("")
    lines.append(
        "_Each cell reads `this lap vs reference`. Brake point is first brake "
        "input, throttle point is first full throttle; both as lap distance, so "
        "a negative delta means earlier._"
    )
    return "\n".join(lines)


def biggest_losses(corners: Sequence[CornerComparison], limit: int = 3) -> str:
    """Rank where time went, stating the measurements without explaining them."""
    losses = sorted(
        [c for c in corners if c.time_delta > 0.01],
        key=lambda c: c.time_delta,
        reverse=True,
    )[:limit]
    if not losses:
        return "_No corner accounts for a meaningful loss._"

    lines = []
    for c in losses:
        facts = [
            f"entry {(c.entry_speed - c.ref_entry_speed) * MS_TO_KMH:+.1f}",
            f"apex {(c.apex_speed - c.ref_apex_speed) * MS_TO_KMH:+.1f}",
            f"exit {(c.exit_speed - c.ref_exit_speed) * MS_TO_KMH:+.1f} km/h",
        ]
        if c.brake_point_pct is not None and c.ref_brake_point_pct is not None:
            facts.append(f"brake {_delta_pp(c.brake_point_pct, c.ref_brake_point_pct)}")
        lines.append(
            f"- **{c.corner.name}** at {c.corner.apex_pct * 100:.0f}%: "
            f"**{c.time_delta:+.3f}s** ({', '.join(facts)})"
        )
    return "\n".join(lines)


def worst_segments_summary(segments: Sequence[Segment], limit: int = 3) -> str:
    """Sector-level fallback for when no corners could be detected."""
    losses = sorted(
        [s for s in segments if s.time_delta > 0.005],
        key=lambda s: s.time_delta,
        reverse=True,
    )[:limit]
    if not losses:
        return "_No significant time loss in any sector._"
    return "\n".join(
        f"- **{seg.name}** ({seg.start_pct * 100:.0f}-{seg.end_pct * 100:.0f}%): "
        f"lost {seg.time_delta:.3f}s"
        for seg in losses
    )


def delta_trace_sparkline(comparison: Comparison, points: int = 20) -> str:
    """A coarse cumulative-gap trace, small enough to read at a glance."""
    if not comparison.delta_trace:
        return ""
    thinned = downsample(comparison.delta_trace, points)
    marks = []
    for i, value in enumerate(thinned):
        pct = i / (len(thinned) - 1) * 100 if len(thinned) > 1 else 0
        marks.append(f"{pct:3.0f}%:{value:+.2f}")
    return "  ".join(marks)


def comparison_report(
    comparison: Comparison,
    title: str,
    reference_name: str,
    lap_name: str,
    notes: Optional[List[str]] = None,
) -> str:
    """Full comparison write-up: headline gap, where it went, then the detail."""
    parts = [f"## {title}", ""]

    parts.append(
        f"**{reference_name}**: {format_lap_time(comparison.reference_time)}  \n"
        f"**{lap_name}**: {format_lap_time(comparison.lap_time)}  \n"
        f"**Gap**: {format_gap(comparison.stated_delta)}"
    )
    parts.append("")

    if notes:
        parts.extend(notes + [""])

    parts.append("### Where the time goes")
    parts.append("")
    if comparison.corners:
        parts.append(biggest_losses(comparison.corners))
    else:
        parts.append(worst_segments_summary(comparison.segments))
    parts.append("")

    parts.append("### Sector breakdown")
    parts.append("")
    parts.append(segment_table(comparison.segments, reference_name, lap_name))
    parts.append("")

    if comparison.corners:
        parts.append(f"### Corner detail ({len(comparison.corners)} corners)")
        parts.append("")
        parts.append(corner_table(comparison.corners))
        parts.append("")

    trace = delta_trace_sparkline(comparison)
    if trace:
        parts.append("### Cumulative gap around the lap")
        parts.append("")
        parts.append("Seconds lost (+) or gained (-) by lap distance:")
        parts.append("")
        parts.append(f"```\n{trace}\n```")
        parts.append("")

    footnotes = [
        "Time deltas are integrated from the speed traces, with each lap "
        "normalised to its own recorded time, so the segment deltas sum exactly "
        "to the overall gap. Where the gap accumulates *within* a segment is "
        "resolved to roughly 0.1% of lap distance."
    ]
    if comparison.track_length_m:
        footnotes.append(
            f"Track length derived from telemetry: "
            f"{comparison.track_length_m:.0f} m."
        )
    error = comparison.integration_error
    if error is not None and abs(error) > 0.01:
        # Should not happen with per-lap normalisation; surface it if it does
        # rather than quietly presenting numbers that don't reconcile.
        footnotes.append(
            f"⚠️ Integrated gap {comparison.total_delta:+.3f}s does not match the "
            f"recorded gap {comparison.stated_delta:+.3f}s ({error:+.3f}s) — "
            "treat the breakdown with caution."
        )
    parts.append("_" + " ".join(footnotes) + "_")

    return "\n".join(parts)


def lap_summary(lap: LapTelemetry, title: str, sector_times: Sequence[float] = ()) -> str:
    """Single-lap overview -- no comparison, just what the lap looked like."""
    speed = lap.speed
    parts = [f"## {title}", ""]
    parts.append(f"**Lap time**: {format_lap_time(lap.lap_time)}")

    if sector_times:
        splits = "  ".join(
            f"S{i}: {t:.3f}s" for i, t in enumerate(sector_times, start=1)
        )
        parts.append(f"**Sectors**: {splits}")
    parts.append("")

    if speed:
        throttle = lap.channel("throttle")
        brake = lap.channel("brake")
        gear = lap.channel("gear")
        parts.append("### Lap characteristics")
        parts.append("")
        parts.append(f"- Top speed: **{kmh(max(speed))} km/h**")
        parts.append(f"- Minimum speed: **{kmh(min(speed))} km/h**")
        parts.append(f"- Average speed: **{kmh(sum(speed) / len(speed))} km/h**")
        if throttle:
            full = sum(1 for v in throttle if v > 0.95) / len(throttle) * 100
            parts.append(f"- Full throttle: **{full:.0f}%** of the lap")
        if brake:
            braking = sum(1 for v in brake if v > 0.05) / len(brake) * 100
            parts.append(f"- On the brakes: **{braking:.0f}%** of the lap")
            parts.append(f"- Peak brake input: **{max(brake) * 100:.0f}%**")
        if gear:
            parts.append(f"- Highest gear: **{int(max(gear))}**")
        parts.append(f"- Telemetry samples: {lap.sample_count}")
        parts.append("")

        parts.append("### Speed trace (km/h by lap distance)")
        parts.append("")
        thinned = downsample(speed, 25)
        marks = [
            f"{i / (len(thinned) - 1) * 100:3.0f}%:{v * MS_TO_KMH:5.0f}"
            for i, v in enumerate(thinned)
        ]
        parts.append(f"```\n{'  '.join(marks)}\n```")

    return "\n".join(parts)
