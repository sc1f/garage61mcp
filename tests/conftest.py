"""Shared fixtures.

Most tests use a synthetic lap instead of live Garage61 data. A generated lap
is deterministic, needs no network and no token, and lets a test put a corner
at a known position and then assert that the code finds it there.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

CSV_HEADER = (
    "Speed,LapDistPct,Lat,Lon,Brake,Throttle,RPM,SteeringWheelAngle,Gear,"
    "Clutch,ABSActive,DRSActive,LatAccel,LongAccel,VertAccel,Yaw,YawRate,"
    "PositionType"
)

# (apex position 0..1, apex speed m/s, direction: +1 right, -1 left)
DEFAULT_CORNERS = [
    (0.12, 22.0, +1),
    (0.38, 30.0, -1),
    (0.64, 18.0, +1),
    (0.86, 35.0, -1),
]


TRACK_M = 2000.0


def implied_lap_time(speeds, track_m=TRACK_M):
    """The time to drive `track_m` at this speed profile.

    The generated data must agree with itself: the GPS path, the speed channel
    and the lap time all describe the same track. Otherwise a test of
    estimate_track_length measures the fault in the fixture.
    """
    total = 0.0
    n = len(speeds)
    for i in range(n - 1):
        v0, v1 = max(speeds[i], 1.0), max(speeds[i + 1], 1.0)
        total += 0.5 * (1.0 / v0 + 1.0 / v1) * (1.0 / (n - 1))
    return track_m * total


def make_lap(
    corners=DEFAULT_CORNERS,
    straight_speed=70.0,
    samples=1800,
    lap_time=None,
    width=0.05,
    speed_scale=1.0,
    lateral_offset_m=0.0,
):
    """Build one synthetic lap as CSV text.

    `speed_scale` multiplies every speed, which simulates the per-lap
    calibration error of a real speed channel. `lateral_offset_m` moves the
    whole path sideways, which gives a known racing-line offset.

    Returns (csv, lap_time). If no lap_time is given, the function calculates
    the time that agrees with the speed profile and a 2000 m track.
    """
    # First pass: the speed profile, which sets the lap time.
    speeds = []
    for i in range(samples):
        d = i / (samples - 1)
        speed = straight_speed
        for apex, apex_speed, direction in corners:
            gap = min(abs(d - apex), 1.0 - abs(d - apex))
            speed -= (straight_speed - apex_speed) * math.exp(-((gap / width) ** 2))
        speeds.append(speed * speed_scale)
    if lap_time is None:
        lap_time = implied_lap_time(speeds)

    rows = []
    lat0, lon0 = 50.0, 5.0
    m_per_deg_lat = 111_132.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))

    x = y = heading = 0.0
    prev_speed = None

    for i in range(samples):
        d = i / (samples - 1)

        speed = speeds[i]
        turn_rate = 0.0
        for apex, apex_speed, direction in corners:
            gap = min(abs(d - apex), 1.0 - abs(d - apex))   # wrap at the line
            turn_rate += direction * math.exp(-((gap / width) ** 2))

        step_m = TRACK_M / samples
        heading += turn_rate * 0.06        # radians accumulated through corners
        x += math.sin(heading) * step_m
        y += math.cos(heading) * step_m

        # Offset the whole path sideways, normal to the direction of travel.
        ox = x + math.cos(heading) * lateral_offset_m
        oy = y - math.sin(heading) * lateral_offset_m

        accel = 0.0 if prev_speed is None else (speed - prev_speed) * samples / lap_time
        prev_speed = speed

        brake = max(0.0, min(1.0, -accel / 8.0))
        throttle = max(0.0, min(1.0, accel / 4.0)) if accel > 0 else 0.0
        if brake < 0.02 and abs(accel) < 0.5:
            throttle = 1.0
        lat_accel = turn_rate * speed * 0.25
        yaw_rate = turn_rate * 0.35
        steering = turn_rate * 0.5          # radians
        gear = max(1, min(6, int(speed / 12) + 1))

        rows.append(",".join(str(v) for v in [
            round(speed, 4), round(d, 6),
            round(lat0 + oy / m_per_deg_lat, 8),
            round(lon0 + ox / m_per_deg_lon, 8),
            round(brake, 4), round(throttle, 4),
            round(2000 + speed * 90, 1), round(steering, 5), gear,
            1, "false", "false",
            round(lat_accel, 4), round(accel, 4), 9.81,
            round(heading, 5), round(yaw_rate, 5), 3,
        ]))
    return CSV_HEADER + "\n" + "\n".join(rows), lap_time


@pytest.fixture
def lap_csv():
    return make_lap()[0]


@pytest.fixture
def reference_lap():
    from telemetry import parse_lap_csv
    csv, seconds = make_lap()
    return parse_lap_csv(csv, lap_time=seconds, label="reference")


@pytest.fixture
def slower_lap():
    """The same track, driven slower, and on a line 2 m to one side."""
    from telemetry import parse_lap_csv
    csv, seconds = make_lap(
        corners=[(0.12, 20.0, +1), (0.38, 28.0, -1), (0.64, 16.0, +1), (0.86, 33.0, -1)],
        lateral_offset_m=2.0,
    )
    return parse_lap_csv(csv, lap_time=seconds, label="slower")
