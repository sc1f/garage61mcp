"""Telemetry parsing and lap-comparison maths.

Garage61 serves one CSV per lap. Channel units as delivered by the API:
    Speed               m/s
    LapDistPct          0..1 fraction of a lap
    SteeringWheelAngle  radians
    Throttle / Brake    0..1
    LatAccel/LongAccel  m/s^2
Everything below converts to km/h and degrees at the presentation boundary only;
the maths stays in SI.
"""

import csv
import io
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Resampling resolution for the common distance grid. 1000 points keeps the
# delta-time integral accurate to a few milliseconds without being unwieldy.
GRID_POINTS = 1000

MS_TO_KMH = 3.6


def _to_float(value: Any, default: float = 0.0) -> float:
    """CSV values are strings; booleans arrive as the literals 'true'/'false'."""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return 1.0
        if lowered == "false":
            return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class BrakeEvent:
    """One braking application, described the way a driver would describe it."""

    start_pct: float           # where the pedal first moves
    peak_pct: float            # where pressure peaks
    end_pct: float             # where the pedal is fully released
    peak_pressure: float       # 0..1
    duration_s: float          # seconds from application to release
    time_to_peak_s: float      # how fast the pedal was built up
    release_s: float           # seconds from peak to release (trail braking)
    entry_speed: float         # m/s at application
    exit_speed: float          # m/s at release
    distance_m: Optional[float] = None   # metres covered while braking

    @property
    def speed_scrubbed(self) -> float:
        return self.entry_speed - self.exit_speed


@dataclass
class LapTelemetry:
    """One lap resampled onto a uniform 0..1 distance grid."""

    distance: List[float]
    channels: Dict[str, List[float]]
    sample_count: int
    lap_time: Optional[float] = None
    label: str = ""
    # Elapsed time at each grid point, anchored so the last value is lap_time.
    # Populated lazily by elapsed_time(); braking is a time-domain phenomenon
    # and cannot be reasoned about on a distance axis alone.
    _elapsed: Optional[List[float]] = None

    def channel(self, name: str) -> List[float]:
        return self.channels.get(name, [])

    @property
    def speed(self) -> List[float]:
        return self.channels.get("speed", [])

    def elapsed_time(self) -> List[float]:
        if self._elapsed is None:
            self._elapsed = _elapsed_time_trace(self)
        return self._elapsed

    def time_at(self, index: int) -> Optional[float]:
        elapsed = self.elapsed_time()
        if not elapsed or index >= len(elapsed):
            return None
        return elapsed[index]


@dataclass
class Corner:
    """One detected corner, described in track terms rather than raw indices."""

    number: int
    apex_pct: float
    start_pct: float
    end_pct: float
    direction: str          # "left" | "right"
    turn_angle: float       # degrees of heading change through the corner
    kind: str               # "braking" | "lift" | "flat"
    apex_speed: float       # m/s
    label: str = ""
    # Fraction of laps that agreed this corner exists, when built by consensus.
    # 1.0 means every lap found it; a low value marks a marginal kink that some
    # drivers straight-line.
    support: float = 1.0

    @property
    def name(self) -> str:
        return self.label or f"Turn {self.number}"


@dataclass
class Segment:
    """A slice of the lap with its own time attribution."""

    name: str
    start_pct: float
    end_pct: float
    time_delta: float          # seconds; positive => `lap` slower than `reference`
    min_speed: float           # m/s, on `lap`
    ref_min_speed: float       # m/s, on `reference`
    avg_speed: float
    ref_avg_speed: float
    entry_speed: float
    ref_entry_speed: float
    brake_point_pct: Optional[float]
    ref_brake_point_pct: Optional[float]
    full_throttle_pct: float   # fraction of segment at >95% throttle
    ref_full_throttle_pct: float


@dataclass
class CornerComparison:
    """Two laps measured against each other through one corner.

    Deliberately facts only -- no interpretation. Working out *why* entry speed
    is up but exit speed is down is the caller's job; heuristics at this layer
    produced actively misleading claims.
    """

    corner: "Corner"
    time_delta: float                    # seconds; positive => lap slower here
    entry_speed: float                   # m/s at corner start
    ref_entry_speed: float
    apex_speed: float                    # m/s at the apex
    ref_apex_speed: float
    exit_speed: float                    # m/s at corner end
    ref_exit_speed: float
    brake_point_pct: Optional[float]
    ref_brake_point_pct: Optional[float]
    throttle_pickup_pct: Optional[float]
    ref_throttle_pickup_pct: Optional[float]
    apex_gear: Optional[int]
    ref_apex_gear: Optional[int]
    # The braking application feeding this corner, if there is one. Carries the
    # pedal shape in seconds, which is where trail-braking differences live.
    brake: Optional[BrakeEvent] = None
    ref_brake: Optional[BrakeEvent] = None


@dataclass
class Comparison:
    """Result of comparing one lap against a reference lap."""

    reference_label: str
    lap_label: str
    reference_time: Optional[float]
    lap_time: Optional[float]
    total_delta: float                     # from the integral, seconds
    stated_delta: Optional[float]          # from lap times, seconds
    track_length_m: Optional[float]
    delta_trace: List[float] = field(default_factory=list)
    distance: List[float] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    corners: List[CornerComparison] = field(default_factory=list)
    reference: Optional[LapTelemetry] = None
    lap: Optional[LapTelemetry] = None

    @property
    def integration_error(self) -> Optional[float]:
        """How far the integrated gap lands from the gap implied by lap times."""
        if self.stated_delta is None:
            return None
        return self.total_delta - self.stated_delta


# --------------------------------------------------------------------------
# Parsing and resampling
# --------------------------------------------------------------------------

# Maps the CSV header names onto the internal channel names used everywhere else.
CHANNEL_MAP = {
    "Speed": "speed",
    "Throttle": "throttle",
    "Brake": "brake",
    "RPM": "rpm",
    "SteeringWheelAngle": "steering",
    "Gear": "gear",
    "LatAccel": "lat_accel",
    "LongAccel": "long_accel",
    # Position, used to derive corner direction and radius.
    "Lat": "lat",
    "Lon": "lon",
}

# Channels that are positions rather than measurements: interpolating them is
# fine, but they must never be averaged with a neighbouring lap's values.
POSITION_CHANNELS = ("lat", "lon")


def parse_lap_csv(csv_data: str, lap_time: Optional[float] = None, label: str = "") -> LapTelemetry:
    """Parse a Garage61 lap CSV and resample it onto a uniform distance grid.

    The raw samples are time-ordered, so LapDistPct is *not* guaranteed monotonic
    (it dips around the start/finish line and can jitter). Sorting by distance
    before interpolating is what makes the grid well-defined.
    """
    if not csv_data or not csv_data.strip():
        raise ValueError("No telemetry data provided")

    reader = csv.DictReader(io.StringIO(csv_data))
    rows = list(reader)
    if not rows:
        raise ValueError("Telemetry CSV contained no data rows")

    if "LapDistPct" not in (reader.fieldnames or []):
        raise ValueError(
            f"Telemetry CSV has no LapDistPct column (found: {reader.fieldnames})"
        )

    present = [name for name in CHANNEL_MAP if name in (reader.fieldnames or [])]

    samples: List[Tuple[float, Dict[str, float]]] = []
    for row in rows:
        dist = _to_float(row.get("LapDistPct"), -1.0)
        if not (0.0 <= dist <= 1.0):
            continue
        values = {CHANNEL_MAP[name]: _to_float(row.get(name)) for name in present}
        samples.append((dist, values))

    if len(samples) < 2:
        raise ValueError("Telemetry CSV had too few usable samples to analyse")

    samples.sort(key=lambda item: item[0])

    # Collapse duplicate distances (the car can sit still, or samples can repeat)
    # by averaging, so the interpolation has a strictly increasing x-axis.
    merged_dist: List[float] = []
    merged_values: List[Dict[str, float]] = []
    for dist, values in samples:
        if merged_dist and math.isclose(dist, merged_dist[-1], abs_tol=1e-9):
            previous = merged_values[-1]
            for key, val in values.items():
                previous[key] = (previous.get(key, 0.0) + val) / 2.0
            continue
        merged_dist.append(dist)
        merged_values.append(dict(values))

    grid = [i / GRID_POINTS for i in range(GRID_POINTS + 1)]
    channels: Dict[str, List[float]] = {}
    for name in CHANNEL_MAP.values():
        if name not in merged_values[0]:
            continue
        series = [values.get(name, 0.0) for values in merged_values]
        channels[name] = _interpolate(merged_dist, series, grid)

    return LapTelemetry(
        distance=grid,
        channels=channels,
        sample_count=len(samples),
        lap_time=lap_time,
        label=label,
    )


def _interpolate(xs: Sequence[float], ys: Sequence[float], targets: Sequence[float]) -> List[float]:
    """Linear interpolation of ys(xs) at each target, clamped at both ends.

    Walks both sequences once instead of scanning from the start for every
    target -- with a 1000-point grid over ~8000 samples the naive version is
    what made the original implementation slow.
    """
    out: List[float] = []
    idx = 0
    last = len(xs) - 1
    for target in targets:
        if target <= xs[0]:
            out.append(ys[0])
            continue
        if target >= xs[last]:
            out.append(ys[last])
            continue
        while idx < last and xs[idx + 1] < target:
            idx += 1
        x0, x1 = xs[idx], xs[idx + 1]
        y0, y1 = ys[idx], ys[idx + 1]
        span = x1 - x0
        if span <= 0:
            out.append(y0)
        else:
            out.append(y0 + (target - x0) * (y1 - y0) / span)
    return out


# --------------------------------------------------------------------------
# Delta-time
# --------------------------------------------------------------------------

def estimate_track_length(lap: LapTelemetry) -> Optional[float]:
    """Back out track length in metres from the speed trace and the lap time.

    Time around the lap is t = integral(ds / v) = L * integral(dd / v) where d is
    the 0..1 distance fraction. We know t (the lap time), so L falls out. This
    self-calibrates against whatever units and sampling the API happens to use,
    which beats hard-coding a track-length table.
    """
    speed = lap.speed
    if not speed or not lap.lap_time:
        return None

    inverse_integral = _integrate_inverse_speed(lap.distance, speed)
    if inverse_integral <= 0:
        return None
    return lap.lap_time / inverse_integral


def _integrate_inverse_speed(distance: Sequence[float], speed: Sequence[float]) -> float:
    """Trapezoidal integral of 1/v over the normalised distance axis."""
    total = 0.0
    floor = 1.0  # m/s; avoids a division blow-up if the car is stopped
    for i in range(len(distance) - 1):
        v0 = max(speed[i], floor)
        v1 = max(speed[i + 1], floor)
        width = distance[i + 1] - distance[i]
        total += 0.5 * (1.0 / v0 + 1.0 / v1) * width
    return total


def _elapsed_time_trace(lap: LapTelemetry) -> List[float]:
    """Cumulative elapsed time at each grid point, anchored to the known lap time.

    Rather than scaling by a shared track length, each lap is normalised by its
    OWN integral so the trace ends exactly at that lap's recorded time. This
    matters: the speed channel carries a small per-lap calibration bias (at
    Tsukuba two laps of the same car imply track lengths 1.5% apart), and using
    one lap's scale for both turns that bias into phantom time delta. Comparing
    two self-consistent traces cancels it, and makes the endpoint exact by
    construction instead of approximately right.
    """
    speed = lap.speed
    if not speed or not lap.lap_time:
        return []

    floor = 1.0
    cumulative = [0.0]
    running = 0.0
    for i in range(len(lap.distance) - 1):
        width = lap.distance[i + 1] - lap.distance[i]
        inv = 0.5 * (1.0 / max(speed[i], floor) + 1.0 / max(speed[i + 1], floor))
        running += inv * width
        cumulative.append(running)

    if running <= 0:
        return []

    scale = lap.lap_time / running
    return [value * scale for value in cumulative]


def delta_time_trace(reference: LapTelemetry, lap: LapTelemetry) -> List[float]:
    """Cumulative time gap at each grid point, in seconds.

    Positive means `lap` has lost time relative to `reference` up to that point.
    The final value equals the difference in recorded lap times exactly.
    """
    ref_trace = _elapsed_time_trace(reference)
    lap_trace = _elapsed_time_trace(lap)
    if not ref_trace or not lap_trace:
        return []

    count = min(len(ref_trace), len(lap_trace))
    return [lap_trace[i] - ref_trace[i] for i in range(count)]


# --------------------------------------------------------------------------
# Corner detection
# --------------------------------------------------------------------------

def _smooth(values: Sequence[float], window: int = 9) -> List[float]:
    """Boxcar smoothing; the raw accelerometer channels are too noisy to peak-find."""
    half = window // 2
    out = []
    for i in range(len(values)):
        lo, hi = max(0, i - half), min(len(values), i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing between two GPS points, in degrees."""
    dlon = math.radians(lon2 - lon1)
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    y = math.sin(dlon) * math.cos(lat2_r)
    x = (math.cos(lat1_r) * math.sin(lat2_r)
         - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon))
    return math.degrees(math.atan2(y, x))


def _heading_change(lap: LapTelemetry, start_idx: int, end_idx: int) -> Optional[float]:
    """Signed heading change across a span, in degrees. Positive is a right turn.

    Derived from GPS rather than LatAccel: the accelerometer's sign convention
    disagrees with reality here (it reports Spa's La Source, a right-hand
    hairpin, as a left), whereas position is unambiguous.
    """
    lat, lon = lap.channel("lat"), lap.channel("lon")
    if not lat or not lon:
        return None
    last = len(lat) - 1
    a = max(0, min(last, start_idx))
    b = max(0, min(last, end_idx))
    if b - a < 4:
        return None

    mid = (a + b) // 2
    before = _bearing(lat[a], lon[a], lat[mid], lon[mid])
    after = _bearing(lat[mid], lon[mid], lat[b], lon[b])
    return (after - before + 540.0) % 360.0 - 180.0


def _classify_corner(turn_angle: Optional[float], apex_speed: float, kind: str) -> str:
    """Human-readable descriptor, e.g. 'tight right hairpin' or 'fast left'."""
    if turn_angle is None:
        shape = "corner"
        side = ""
    else:
        side = "right" if turn_angle > 0 else "left"
        magnitude = abs(turn_angle)
        if magnitude > 110:
            shape = "hairpin"
        elif magnitude > 60:
            shape = "corner"
        else:
            shape = "kink" if kind == "flat" else "corner"

    speed_kmh = apex_speed * MS_TO_KMH
    if speed_kmh < 90:
        pace = "slow"
    elif speed_kmh < 160:
        pace = "medium-speed"
    else:
        pace = "fast"

    parts = [pace, side, shape]
    return " ".join(p for p in parts if p)


def detect_corners(
    lap: LapTelemetry, sensitivity: float = 0.35, min_separation_pct: float = 0.025
) -> List[Corner]:
    """Find every corner on the lap, including ones taken without braking.

    Braking events alone miss a lot: a light car like the F4 takes half of
    Tsukuba on a lift, so those corners never show up in the brake trace.
    Lateral acceleration is present in every corner by definition, so peaks in
    |LatAccel| are the detection signal, and brake/throttle only classify what
    kind of corner it is.
    """
    lat_accel = lap.channel("lat_accel")
    speed = lap.speed
    if not lat_accel or not speed:
        return []

    magnitude = _smooth([abs(v) for v in lat_accel], 9)
    peak = max(magnitude) if magnitude else 0.0
    if peak <= 0:
        return []

    threshold = peak * sensitivity
    grid = len(lap.distance) - 1
    min_gap = max(1, int(min_separation_pct * grid))

    candidates = [
        i for i in range(1, len(magnitude) - 1)
        if magnitude[i] >= threshold
        and magnitude[i] >= magnitude[i - 1]
        and magnitude[i] >= magnitude[i + 1]
    ]
    candidates.sort(key=lambda i: magnitude[i], reverse=True)

    brake = lap.channel("brake")
    throttle = lap.channel("throttle")

    # Resolve each lateral peak to the slowest point near it -- that is the
    # apex a driver would recognise -- then dedupe on apex, not on the peak.
    # Peaks either side of a direction change otherwise yield the same corner
    # twice with opposite directions.
    apexes: List[int] = []
    for i in candidates:
        lo, hi = max(0, i - min_gap), min(len(speed), i + min_gap)
        window = speed[lo:hi]
        if not window:
            continue
        apex = lo + window.index(min(window))
        if any(abs(apex - existing) < min_gap for existing in apexes):
            continue
        apexes.append(apex)

    apexes.sort()

    # Hard bounds keep a corner from swallowing the lap: the walk below stops at
    # the midpoint to each neighbouring apex, and at an absolute width cap.
    # Without them a continuous complex never drops below the edge threshold and
    # the extent runs away, which also wrecks the GPS heading measured across it.
    max_half_width = max(min_gap, int(0.03 * grid))

    corners: List[Corner] = []
    for number, apex in enumerate(apexes, start=1):
        previous_apex = apexes[number - 2] if number >= 2 else None
        next_apex = apexes[number] if number < len(apexes) else None

        floor_idx = max(
            0,
            apex - max_half_width,
            (apex + previous_apex) // 2 if previous_apex is not None else 0,
        )
        ceiling_idx = min(
            len(magnitude) - 1,
            apex + max_half_width,
            (apex + next_apex) // 2 if next_apex is not None else len(magnitude) - 1,
        )

        edge = magnitude[apex] * 0.4 if magnitude[apex] > 0 else threshold
        start = apex
        while start > floor_idx and magnitude[start] > edge:
            start -= 1
        end = apex
        while end < ceiling_idx and magnitude[end] > edge:
            end += 1

        approach = brake[max(0, apex - min_gap * 2):apex + 2] if brake else []
        peak_brake = max(approach) if approach else 0.0
        entry_throttle = throttle[max(0, apex - min_gap):apex + 2] if throttle else []
        min_throttle = min(entry_throttle) if entry_throttle else 1.0

        if peak_brake > 0.15:
            kind = "braking"
        elif min_throttle < 0.85:
            kind = "lift"
        else:
            kind = "flat"

        turn_angle = _heading_change(lap, start, end)
        corners.append(
            Corner(
                number=number,
                apex_pct=lap.distance[apex],
                start_pct=lap.distance[start],
                end_pct=lap.distance[end],
                direction=(
                    "right" if (turn_angle or 0) > 0 else "left"
                ) if turn_angle is not None else "unknown",
                turn_angle=turn_angle or 0.0,
                kind=kind,
                apex_speed=speed[apex],
                label=f"Turn {number} ({_classify_corner(turn_angle, speed[apex], kind)})",
            )
        )

    return corners


def detect_brake_events(
    lap: LapTelemetry,
    threshold: float = 0.03,
    min_peak: float = 0.10,
    track_length_m: Optional[float] = None,
) -> List[BrakeEvent]:
    """Find each braking application and describe its shape in the time domain.

    `threshold` is where the pedal counts as moving at all, `min_peak` filters
    out incidental dabs. Times come from the lap's own elapsed-time trace, so
    "0.4s to peak pressure" means real seconds rather than lap distance.
    """
    brake = lap.channel("brake")
    speed = lap.speed
    if not brake or not speed:
        return []

    elapsed = lap.elapsed_time()
    events: List[BrakeEvent] = []
    start: Optional[int] = None

    for i, value in enumerate(brake):
        if value > threshold and start is None:
            start = i
        elif value <= threshold and start is not None:
            events.append((start, i - 1))
            start = None
    if start is not None:
        events.append((start, len(brake) - 1))

    out: List[BrakeEvent] = []
    for begin, finish in events:
        window = brake[begin:finish + 1]
        if not window or max(window) < min_peak:
            continue
        peak_idx = begin + window.index(max(window))

        def at(index: int) -> float:
            return elapsed[index] if elapsed and index < len(elapsed) else 0.0

        distance_m = None
        if track_length_m:
            distance_m = (lap.distance[finish] - lap.distance[begin]) * track_length_m

        out.append(
            BrakeEvent(
                start_pct=lap.distance[begin],
                peak_pct=lap.distance[peak_idx],
                end_pct=lap.distance[finish],
                peak_pressure=max(window),
                duration_s=at(finish) - at(begin),
                time_to_peak_s=at(peak_idx) - at(begin),
                release_s=at(finish) - at(peak_idx),
                entry_speed=speed[begin],
                exit_speed=speed[finish],
                distance_m=distance_m,
            )
        )
    return out


def build_corner_map(
    laps: Sequence[LapTelemetry], min_support: float = 0.5, tolerance_pct: float = 0.015
) -> List[Corner]:
    """Build ONE canonical corner map for a track from several laps.

    Detecting on a single lap makes the corner count depend on whose lap it is:
    at Tsukuba the same car yields 11, 12 or 13 corners across nine drivers,
    because marginal kinks sit right at the detection threshold and some drivers
    straight-line them. That makes turn numbers unusable across comparisons --
    "Turn 11" would name a different corner depending on the reference lap.

    Taking the consensus fixes that. An apex is kept when it appears in at least
    `min_support` of the laps, and its canonical position is the median of the
    laps that found it, so numbering is stable for a given car/track.
    """
    detections = [detect_corners(lap) for lap in laps]
    detections = [d for d in detections if d]
    if not detections:
        return []
    if len(detections) == 1:
        return detections[0]

    # Cluster apexes across laps by proximity in lap distance.
    flat = sorted(
        ((corner.apex_pct, index, corner)
         for index, found in enumerate(detections) for corner in found),
        key=lambda item: item[0],
    )

    clusters: List[List[Tuple[float, int, Corner]]] = []
    for entry in flat:
        if clusters and entry[0] - clusters[-1][-1][0] <= tolerance_pct:
            clusters[-1].append(entry)
        else:
            clusters.append([entry])

    required = max(2, int(round(min_support * len(detections))))
    canonical: List[Corner] = []
    for cluster in clusters:
        supporters = {index for _, index, _ in cluster}
        if len(supporters) < required:
            continue

        # One vote per lap; a lap that split a complex into two shouldn't
        # count twice toward its own consensus.
        per_lap: Dict[int, Corner] = {}
        for apex, index, corner in cluster:
            per_lap.setdefault(index, corner)
        members = list(per_lap.values())

        apexes = sorted(c.apex_pct for c in members)
        median_apex = apexes[len(apexes) // 2]
        representative = min(members, key=lambda c: abs(c.apex_pct - median_apex))

        turn_angles = sorted(c.turn_angle for c in members)
        median_angle = turn_angles[len(turn_angles) // 2]
        speeds = sorted(c.apex_speed for c in members)
        median_speed = speeds[len(speeds) // 2]

        kinds = [c.kind for c in members]
        kind = max(set(kinds), key=kinds.count)

        canonical.append(
            Corner(
                number=0,  # assigned below, in track order
                apex_pct=median_apex,
                start_pct=min(c.start_pct for c in members),
                end_pct=max(c.end_pct for c in members),
                direction="right" if median_angle > 0 else "left",
                turn_angle=median_angle,
                kind=kind,
                apex_speed=median_speed,
                label="",
                support=len(supporters) / len(detections),
            )
        )

    canonical.sort(key=lambda c: c.apex_pct)
    for number, corner in enumerate(canonical, start=1):
        corner.number = number
        corner.label = (
            f"Turn {number} "
            f"({_classify_corner(corner.turn_angle, corner.apex_speed, corner.kind)})"
        )
    return canonical


# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------

def build_segment_bounds(
    sector_times: Optional[Sequence[float]], lap_time: Optional[float], segment_count: int
) -> List[Tuple[str, float, float]]:
    """Choose segment boundaries, preferring the track's real sectors.

    Garage61 returns per-sector times on the lap record. Sector splits are by
    time rather than distance, so proportioning by cumulative time is an
    approximation -- but it lands far closer to the real split points than
    dividing the lap into equal quarters, and it lets output name real sectors.
    """
    if sector_times and lap_time and lap_time > 0 and len(sector_times) > 1:
        bounds: List[Tuple[str, float, float]] = []
        cumulative = 0.0
        for index, sector_time in enumerate(sector_times, start=1):
            start = cumulative / lap_time
            cumulative += sector_time
            end = min(cumulative / lap_time, 1.0)
            if end > start:
                bounds.append((f"Sector {index}", start, end))
        if bounds:
            bounds[-1] = (bounds[-1][0], bounds[-1][1], 1.0)
            return bounds

    step = 1.0 / segment_count
    return [
        (f"{i * step * 100:.0f}-{(i + 1) * step * 100:.0f}%", i * step, min((i + 1) * step, 1.0))
        for i in range(segment_count)
    ]


def _index_for(distance: Sequence[float], pct: float) -> int:
    idx = int(round(pct * (len(distance) - 1)))
    return max(0, min(len(distance) - 1, idx))


def _brake_point(lap: LapTelemetry, start_idx: int, end_idx: int) -> Optional[float]:
    """Distance fraction where the driver first meaningfully touches the brakes."""
    brake = lap.channel("brake")
    if not brake:
        return None
    for i in range(start_idx, min(end_idx + 1, len(brake))):
        if brake[i] > 0.05:
            return lap.distance[i]
    return None


def _full_throttle_fraction(lap: LapTelemetry, start_idx: int, end_idx: int) -> float:
    throttle = lap.channel("throttle")
    if not throttle or end_idx <= start_idx:
        return 0.0
    window = throttle[start_idx:end_idx + 1]
    if not window:
        return 0.0
    return sum(1 for value in window if value > 0.95) / len(window)


def build_segments(
    reference: LapTelemetry,
    lap: LapTelemetry,
    trace: Sequence[float],
    bounds: Sequence[Tuple[str, float, float]],
) -> List[Segment]:
    segments: List[Segment] = []
    for name, start_pct, end_pct in bounds:
        start_idx = _index_for(lap.distance, start_pct)
        end_idx = _index_for(lap.distance, end_pct)
        if end_idx <= start_idx:
            continue

        if trace:
            time_delta = trace[min(end_idx, len(trace) - 1)] - trace[min(start_idx, len(trace) - 1)]
        else:
            time_delta = 0.0

        lap_window = lap.speed[start_idx:end_idx + 1] or [0.0]
        ref_window = reference.speed[start_idx:end_idx + 1] or [0.0]

        segments.append(
            Segment(
                name=name,
                start_pct=start_pct,
                end_pct=end_pct,
                time_delta=time_delta,
                min_speed=min(lap_window),
                ref_min_speed=min(ref_window),
                avg_speed=sum(lap_window) / len(lap_window),
                ref_avg_speed=sum(ref_window) / len(ref_window),
                entry_speed=lap_window[0],
                ref_entry_speed=ref_window[0],
                brake_point_pct=_brake_point(lap, start_idx, end_idx),
                ref_brake_point_pct=_brake_point(reference, start_idx, end_idx),
                full_throttle_pct=_full_throttle_fraction(lap, start_idx, end_idx),
                ref_full_throttle_pct=_full_throttle_fraction(reference, start_idx, end_idx),
            )
        )
    return segments


def _first_crossing(
    lap: LapTelemetry, channel: str, start_idx: int, end_idx: int, threshold: float,
    rising: bool = True,
) -> Optional[float]:
    """Distance at which a channel first crosses a threshold within a window."""
    values = lap.channel(channel)
    if not values:
        return None
    lo = max(0, start_idx)
    hi = min(len(values) - 1, end_idx)
    for i in range(lo, hi + 1):
        if (values[i] > threshold) if rising else (values[i] < threshold):
            return lap.distance[i]
    return None


def _brake_for_corner(
    events: Sequence[BrakeEvent], corner: Corner
) -> Optional[BrakeEvent]:
    """The braking application feeding a corner: the last one ending by the apex."""
    candidates = [
        e for e in events
        if e.start_pct <= corner.apex_pct and e.end_pct >= corner.start_pct - 0.05
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.start_pct)


def compare_corners(
    reference: LapTelemetry,
    lap: LapTelemetry,
    corners: Sequence[Corner],
    trace: Sequence[float],
    track_length_m: Optional[float] = None,
) -> List[CornerComparison]:
    """Measure both laps through each detected corner."""
    results: List[CornerComparison] = []
    last = len(lap.distance) - 1

    lap_brakes = detect_brake_events(lap, track_length_m=track_length_m)
    ref_brakes = detect_brake_events(reference, track_length_m=track_length_m)

    for corner in corners:
        start_idx = _index_for(lap.distance, corner.start_pct)
        apex_idx = _index_for(lap.distance, corner.apex_pct)
        end_idx = _index_for(lap.distance, corner.end_pct)
        if end_idx <= start_idx:
            continue

        # Braking happens before the corner proper, so look back from the apex.
        approach_start = max(0, start_idx - int(0.04 * last))

        def at_apex(source: LapTelemetry, name: str) -> Optional[float]:
            """Value at the apex, not the window extreme.

            Taking a minimum across the window picks up the wrong thing twice
            over: the gear channel dips to 0 during downshift blips, and in a
            corner that flows into the next braking zone the slowest point sits
            at the window edge rather than at the apex.
            """
            values = source.channel(name)
            if not values or apex_idx >= len(values):
                return None
            return values[apex_idx]

        lap_speed = lap.speed
        ref_speed = reference.speed

        time_delta = 0.0
        if trace:
            time_delta = (
                trace[min(end_idx, len(trace) - 1)]
                - trace[min(start_idx, len(trace) - 1)]
            )

        apex_gear = at_apex(lap, "gear")
        ref_apex_gear = at_apex(reference, "gear")

        results.append(
            CornerComparison(
                corner=corner,
                time_delta=time_delta,
                entry_speed=lap_speed[start_idx],
                ref_entry_speed=ref_speed[start_idx],
                apex_speed=lap_speed[apex_idx],
                ref_apex_speed=ref_speed[apex_idx],
                exit_speed=lap_speed[end_idx],
                ref_exit_speed=ref_speed[end_idx],
                brake_point_pct=_first_crossing(lap, "brake", approach_start, apex_idx, 0.05),
                ref_brake_point_pct=_first_crossing(
                    reference, "brake", approach_start, apex_idx, 0.05
                ),
                throttle_pickup_pct=_first_crossing(
                    lap, "throttle", apex_idx, min(end_idx + int(0.03 * last), last), 0.95
                ),
                ref_throttle_pickup_pct=_first_crossing(
                    reference, "throttle", apex_idx,
                    min(end_idx + int(0.03 * last), last), 0.95
                ),
                apex_gear=int(round(apex_gear)) if apex_gear is not None else None,
                ref_apex_gear=int(round(ref_apex_gear)) if ref_apex_gear is not None else None,
                brake=_brake_for_corner(lap_brakes, corner),
                ref_brake=_brake_for_corner(ref_brakes, corner),
            )
        )

    return results


def compare_laps(
    reference: LapTelemetry,
    lap: LapTelemetry,
    sector_times: Optional[Sequence[float]] = None,
    segment_count: int = 12,
    corner_map: Optional[Sequence[Corner]] = None,
) -> Comparison:
    """Compare `lap` against `reference`, attributing the gap across the lap.

    Pass `corner_map` (from build_corner_map) so turn numbers stay identical
    across every comparison on this track. Falling back to detecting on the
    reference lap makes numbering depend on who the reference is.
    """
    track_length = estimate_track_length(reference) or estimate_track_length(lap)
    trace = delta_time_trace(reference, lap)

    bounds = build_segment_bounds(sector_times, reference.lap_time, segment_count)
    segments = build_segments(reference, lap, trace, bounds)

    corners = compare_corners(
        reference, lap, corner_map or detect_corners(reference), trace, track_length
    )

    stated = None
    if reference.lap_time is not None and lap.lap_time is not None:
        stated = lap.lap_time - reference.lap_time

    return Comparison(
        reference_label=reference.label,
        lap_label=lap.label,
        reference_time=reference.lap_time,
        lap_time=lap.lap_time,
        total_delta=trace[-1] if trace else 0.0,
        stated_delta=stated,
        track_length_m=track_length,
        delta_trace=trace,
        distance=list(reference.distance),
        segments=segments,
        corners=corners,
        reference=reference,
        lap=lap,
    )


def downsample(values: Sequence[float], count: int) -> List[float]:
    """Evenly thin a series down to at most `count` points, keeping both ends."""
    if not values:
        return []
    if len(values) <= count:
        return list(values)
    step = (len(values) - 1) / (count - 1)
    return [values[int(round(i * step))] for i in range(count)]
