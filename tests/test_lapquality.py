"""Tests for the selection of the laps that a comparison can use.

A lap list from the API mixes good laps with outlaps, offs and spins. The
filter must find those laps, and it must always give the reason.
"""

from dataclasses import dataclass, field
from typing import List

import pytest

from lapquality import (
    LAP_OUTLIER_RATIO,
    SECTOR_OUTLIER_RATIO,
    assess_laps,
    split_usable,
)


@dataclass
class FakeLap:
    """The parts of a lap record that the filter reads."""

    lapTime: float
    sector_times: List[float] = field(default_factory=list)
    clean: bool = True
    offtrack: bool = False
    incomplete: bool = False
    pitIn: bool = False
    pitOut: bool = False
    label: str = ""


def clean_field(count=6, base=95.0):
    """A group of laps that are all good, with a small spread."""
    return [
        FakeLap(lapTime=base + i * 0.15,
                sector_times=[30.0 + i * 0.05, 33.0 + i * 0.05, 32.0 + i * 0.05],
                label=f"lap{i}")
        for i in range(count)
    ]


class TestCleanLaps:
    def test_all_good_laps_stay(self):
        laps = clean_field()
        usable, excluded = split_usable(laps)
        assert len(usable) == len(laps)
        assert excluded == []

    def test_a_good_lap_gives_no_reason(self):
        (lap, verdict), *_ = assess_laps(clean_field())
        assert verdict.usable
        assert verdict.summary == "representative"

    def test_an_empty_list_gives_an_empty_result(self):
        assert assess_laps([]) == []


class TestFlags:
    @pytest.mark.parametrize("flag,text", [
        ("offtrack", "off track"),
        ("incomplete", "incomplete"),
        ("pitIn", "in/out"),
        ("pitOut", "in/out"),
    ])
    def test_each_flag_removes_the_lap(self, flag, text):
        laps = clean_field()
        setattr(laps[2], flag, True)
        usable, excluded = split_usable(laps)
        assert laps[2] not in usable
        assert len(excluded) == 1
        assert text in excluded[0][1].summary

    def test_a_lap_that_is_not_clean_is_removed(self):
        laps = clean_field()
        laps[1].clean = False
        usable, excluded = split_usable(laps)
        assert laps[1] not in usable
        assert "not clean" in excluded[0][1].summary


class TestSectorOutliers:
    def test_one_bad_sector_removes_the_lap(self):
        """This is the Tsukuba condition: a spin in one sector only."""
        laps = clean_field()
        laps[3].sector_times = [30.0, 55.0, 32.0]      # 1.7x the usual time
        laps[3].lapTime = 117.0
        usable, excluded = split_usable(laps)
        assert laps[3] not in usable
        assert "sector 2" in excluded[0][1].summary

    def test_the_reason_gives_both_times(self):
        laps = clean_field()
        laps[3].sector_times = [30.0, 55.0, 32.0]
        _, excluded = split_usable(laps)
        summary = excluded[0][1].summary
        assert "55.00s" in summary and "33." in summary

    def test_a_sector_below_the_ratio_stays(self):
        laps = clean_field()
        margin = SECTOR_OUTLIER_RATIO - 0.05
        laps[3].sector_times = [30.0, 33.0 * margin, 32.0]
        usable, _ = split_usable(laps)
        assert laps[3] in usable

    def test_a_faster_sector_is_not_an_outlier(self):
        """A quick lap must not look like a fault."""
        laps = clean_field()
        laps[3].sector_times = [27.0, 30.0, 29.0]
        laps[3].lapTime = 86.0
        usable, _ = split_usable(laps)
        assert laps[3] in usable


class TestLapTimeOutliers:
    def test_a_slow_lap_without_sectors_is_removed(self):
        laps = [FakeLap(lapTime=95.0 + i * 0.1) for i in range(6)]
        laps[4].lapTime = 95.0 * (LAP_OUTLIER_RATIO + 0.10)
        usable, excluded = split_usable(laps)
        assert laps[4] not in usable
        assert "lap time" in excluded[0][1].summary

    def test_sector_data_is_used_before_lap_time(self):
        """A lap with sectors is judged on its sectors only."""
        laps = clean_field()
        # Slow overall, but every sector is inside the ratio.
        laps[2].sector_times = [33.0, 36.0, 35.0]
        laps[2].lapTime = 200.0
        usable, _ = split_usable(laps)
        assert laps[2] in usable


class TestTheFilterStops:
    def test_fewer_than_two_good_laps_keeps_them_all(self):
        """A driver with messy laps still gets a comparison."""
        laps = clean_field(count=3)
        laps[0].offtrack = True
        laps[1].offtrack = True
        usable, excluded = split_usable(laps)
        assert len(usable) == 3, "the filter removed too many laps"
        assert len(excluded) == 2, "the reasons must stay"

    def test_exactly_two_good_laps_are_enough(self):
        laps = clean_field(count=4)
        laps[0].offtrack = True
        laps[1].offtrack = True
        usable, excluded = split_usable(laps)
        assert len(usable) == 2
        assert len(excluded) == 2

    def test_the_reasons_are_given_even_when_the_filter_stops(self):
        laps = clean_field(count=2)
        laps[0].offtrack = True
        laps[1].incomplete = True
        usable, excluded = split_usable(laps)
        assert len(usable) == 2
        assert len(excluded) == 2, "a removed lap must always give its reason"


class TestManyReasons:
    def test_one_lap_can_give_more_than_one_reason(self):
        laps = clean_field()
        laps[2].offtrack = True
        laps[2].clean = False
        _, excluded = split_usable(laps)
        assert "; " in excluded[0][1].summary
