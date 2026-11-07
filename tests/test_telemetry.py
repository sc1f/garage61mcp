"""Tests for the telemetry mathematics and the corner detection."""

import math
import random

import pytest
from conftest import make_lap

from telemetry import (
    MS_TO_KMH,
    assign_brakes_to_corners,
    build_corner_map,
    compare_laps,
    detect_brake_events,
    detect_corners,
    estimate_track_length,
    line_offset_series,
    parse_lap_csv,
)


class TestParse:
    def test_channels_present(self, lap_csv):
        lap = parse_lap_csv(lap_csv, 95.0, "x")
        for name in ("speed", "brake", "throttle", "gear", "steering",
                     "lat_accel", "yaw_rate", "abs", "lat", "lon"):
            assert name in lap.channels, f"{name} missing"

    def test_grid_is_uniform_and_complete(self, lap_csv):
        lap = parse_lap_csv(lap_csv, 95.0, "x")
        assert lap.distance[0] == 0.0
        assert lap.distance[-1] == 1.0
        assert all(b > a for a, b in zip(lap.distance, lap.distance[1:]))

    def test_unsorted_input_still_parses(self, lap_csv):
        """LapDistPct is not monotonic in real data, thus order must not matter."""
        head, *rows = lap_csv.split("\n")
        random.Random(0).shuffle(rows)
        shuffled = parse_lap_csv(head + "\n" + "\n".join(rows), 95.0, "x")
        ordered = parse_lap_csv(lap_csv, 95.0, "x")
        assert len(shuffled.speed) == len(ordered.speed)
        worst = max(abs(a - b) for a, b in zip(shuffled.speed, ordered.speed))
        assert worst < 0.5, f"shuffled input changed the speed data by {worst}"

    def test_rejects_csv_without_distance(self):
        with pytest.raises(ValueError, match="LapDistPct"):
            parse_lap_csv("Speed,Brake\n10,0\n11,0\n", 95.0, "x")


class TestTrackLength:
    def test_close_to_the_real_length(self, reference_lap):
        """The generated track is 2000 m. The calculation inverts the lap time."""
        length = estimate_track_length(reference_lap)
        assert length is not None
        assert abs(length - 2000.0) / 2000.0 < 0.05


class TestDeltaTime:
    def test_a_lap_against_itself_is_zero(self, reference_lap):
        result = compare_laps(reference_lap, reference_lap)
        assert abs(result.total_delta) < 1e-9

    def test_total_equals_the_lap_time_difference(self, reference_lap, slower_lap):
        result = compare_laps(reference_lap, slower_lap)
        expected = slower_lap.lap_time - reference_lap.lap_time
        assert abs(result.total_delta - expected) < 1e-6

    def test_segments_sum_to_the_total(self, reference_lap, slower_lap):
        result = compare_laps(reference_lap, slower_lap)
        assert result.segments
        assert abs(sum(s.time_delta for s in result.segments) - result.total_delta) < 1e-6

    def test_a_speed_channel_error_does_not_make_a_false_difference(self):
        """The regression test for the phantom delta.

        Each lap has its own small error in the speed channel. If the code used
        one track length for both laps, that error would become an incorrect
        time difference. Each lap must use its own recorded time.
        """
        fast = parse_lap_csv(make_lap(speed_scale=1.000)[0], 95.0, "fast")
        slow = parse_lap_csv(make_lap(speed_scale=1.015)[0], 95.6, "slow")  # 1.5% error
        result = compare_laps(fast, slow)
        assert abs(result.total_delta - 0.6) < 1e-6, (
            f"a 1.5% speed error produced {result.total_delta:+.3f}s "
            "instead of the true +0.600s"
        )


class TestCornerDetection:
    def test_finds_each_planted_corner(self, reference_lap):
        planted = [0.12, 0.38, 0.64, 0.86]
        found = [c.apex_pct for c in detect_corners(reference_lap)]
        assert len(found) == len(planted)
        for want in planted:
            assert min(abs(want - got) for got in found) < 0.02

    def test_direction_comes_from_the_gps_data(self, reference_lap):
        """The corners alternate right, left, right, left."""
        corners = detect_corners(reference_lap)
        assert [c.direction for c in corners] == ["right", "left", "right", "left"]

    def test_apexes_increase_and_do_not_repeat(self, reference_lap):
        apexes = [c.apex_pct for c in detect_corners(reference_lap)]
        assert apexes == sorted(apexes)
        assert len(apexes) == len(set(apexes))

    def test_the_extent_of_a_corner_has_limits(self, reference_lap):
        for corner in detect_corners(reference_lap):
            assert corner.end_pct - corner.start_pct < 0.20, (
                f"T{corner.number} includes {(corner.end_pct - corner.start_pct):.0%} "
                "of the lap"
            )


class TestCornerMap:
    def _laps(self):
        return [
            parse_lap_csv(make_lap(speed_scale=s)[0], 95.0, f"lap{i}")
            for i, s in enumerate((1.0, 0.98, 1.02, 0.99, 1.01))
        ]

    def test_the_sequence_of_the_laps_does_not_change_the_map(self):
        laps = self._laps()
        first = build_corner_map(laps)
        for seed in range(3):
            shuffled = laps[:]
            random.Random(seed).shuffle(shuffled)
            other = build_corner_map(shuffled)
            assert len(other) == len(first)
            for a, b in zip(first, other):
                assert abs(a.apex_pct - b.apex_pct) < 1e-9
                assert a.number == b.number

    def test_fewer_laps_give_the_same_corners(self):
        laps = self._laps()
        full = build_corner_map(laps)
        part = build_corner_map(laps[:3])
        assert len(part) == len(full)

    def test_the_numbers_follow_the_track_order(self):
        corners = build_corner_map(self._laps())
        assert [c.number for c in corners] == list(range(1, len(corners) + 1))


class TestBrakeEvents:
    def test_each_event_belongs_to_only_one_corner(self, reference_lap):
        corners = detect_corners(reference_lap)
        events = detect_brake_events(reference_lap, track_length_m=2000.0)
        mapping = assign_brakes_to_corners(events, corners)
        used = [id(e) for e in mapping.values()]
        assert len(used) == len(set(used)), "two corners share one brake event"

    def test_the_shape_of_an_event_is_correct(self, reference_lap):
        for event in detect_brake_events(reference_lap, track_length_m=2000.0):
            assert event.start_pct <= event.peak_pct <= event.end_pct
            assert 0.0 < event.peak_pressure <= 1.0
            assert event.duration_s > 0
            assert event.entry_speed >= event.exit_speed


class TestLineOffset:
    def test_no_offset_between_a_lap_and_itself(self, reference_lap):
        offsets = line_offset_series(reference_lap, reference_lap)
        assert offsets
        assert max(abs(v) for v in offsets) < 0.01

    def test_a_known_offset_is_measured(self, reference_lap):
        """The second lap is 2 m to one side of the first."""
        moved = parse_lap_csv(make_lap(lateral_offset_m=2.0)[0], 95.0, "moved")
        offsets = line_offset_series(reference_lap, moved)
        middle = sorted(abs(v) for v in offsets)[len(offsets) // 2]
        assert 1.0 < middle < 3.0, f"measured {middle:.2f} m for a 2 m offset"


class TestCornerComparison:
    def test_values_come_from_the_apex(self, reference_lap, slower_lap):
        """The gear channel decreases to 0 during a downshift, thus a minimum
        across the corner window would give an incorrect gear."""
        result = compare_laps(reference_lap, slower_lap)
        for corner in result.corners:
            if corner.apex_gear is not None:
                assert corner.apex_gear >= 1, "gear 0 is a downshift, not the apex"

    def test_every_corner_gets_a_time_difference(self, reference_lap, slower_lap):
        result = compare_laps(reference_lap, slower_lap)
        assert result.corners
        assert all(isinstance(c.time_delta, float) for c in result.corners)
