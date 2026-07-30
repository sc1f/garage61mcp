"""Deciding which laps are worth comparing.

A lap list straight from the API mixes representative laps with outlaps, spins
and offs. At Tsukuba four of ten laps had a sector 2-3x the normal time. Ranking
or averaging those alongside clean laps produces nonsense, so they are detected
and set aside -- but always reported, never silently dropped.
"""

import statistics
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

# A sector this much worse than the field median means something went wrong
# (off, spin, traffic), not that the driver was slightly slower.
SECTOR_OUTLIER_RATIO = 1.35

# Same idea at whole-lap level, for laps with no sector data.
LAP_OUTLIER_RATIO = 1.15


@dataclass
class LapQuality:
    """Verdict on a single lap."""

    usable: bool
    reasons: List[str]

    @property
    def summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "representative"


def _median_sectors(laps: Sequence) -> Optional[List[float]]:
    """Median time for each sector across every lap that reports them."""
    sector_lists = [lap.sector_times for lap in laps if lap.sector_times]
    if not sector_lists:
        return None
    width = min(len(s) for s in sector_lists)
    if width == 0:
        return None
    return [
        statistics.median(s[i] for s in sector_lists)
        for i in range(width)
    ]


def assess_laps(laps: Sequence) -> List[Tuple[object, LapQuality]]:
    """Judge every lap against the field, returning (lap, verdict) pairs."""
    if not laps:
        return []

    median_sectors = _median_sectors(laps)
    median_lap = statistics.median(lap.lapTime for lap in laps)

    assessed: List[Tuple[object, LapQuality]] = []
    for lap in laps:
        reasons: List[str] = []

        if getattr(lap, "incomplete", False):
            reasons.append("incomplete lap")
        if getattr(lap, "offtrack", False):
            reasons.append("went off track")
        if getattr(lap, "pitIn", False) or getattr(lap, "pitOut", False):
            reasons.append("in/out lap")
        if not getattr(lap, "clean", True):
            reasons.append("flagged not clean")

        # Per-sector comparison localises the problem, which whole-lap time
        # cannot: a lap can be within a second overall yet contain a spin
        # offset by a tow elsewhere.
        if median_sectors and lap.sector_times:
            for index, (actual, expected) in enumerate(
                zip(lap.sector_times, median_sectors), start=1
            ):
                if expected > 0 and actual > expected * SECTOR_OUTLIER_RATIO:
                    reasons.append(
                        f"sector {index} {actual:.2f}s vs {expected:.2f}s typical "
                        f"({actual / expected:.1f}x)"
                    )
        elif median_lap > 0 and lap.lapTime > median_lap * LAP_OUTLIER_RATIO:
            reasons.append(
                f"lap time {lap.lapTime:.2f}s vs {median_lap:.2f}s typical"
            )

        assessed.append((lap, LapQuality(usable=not reasons, reasons=reasons)))

    return assessed


def split_usable(laps: Sequence) -> Tuple[List, List[Tuple[object, LapQuality]]]:
    """Partition laps into (usable, [(excluded_lap, why), ...]).

    Falls back to returning everything when the filter would leave fewer than
    two laps -- a driver with three messy laps still deserves a comparison, with
    the caveat surfaced, rather than an empty result.
    """
    assessed = assess_laps(laps)
    usable = [lap for lap, verdict in assessed if verdict.usable]
    excluded = [(lap, verdict) for lap, verdict in assessed if not verdict.usable]

    if len(usable) < 2:
        return list(laps), excluded

    return usable, excluded
