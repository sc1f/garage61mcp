"""Tests for the output format.

A model reads this output, not a person and not a renderer. Bold markers and
emoji are only noise. Three Markdown elements stay, because they give the model
a structure: the ## headings, the code fences, and the _..._ legends.
"""

import ast
import pathlib
import re

import pytest

from formatting import (
    fixed_table,
    format_gap,
    format_lap_time,
    garage61_laps_url,
    kmh,
    uniform_series,
)
from tools import _err, _ok, sanitize, strip_markup

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


class TestLapTime:
    @pytest.mark.parametrize("seconds,text", [
        (95.123, "1:35.123"),
        (59.5, "59.500s"),
        (60.0, "1:00.000"),
        (125.456, "2:05.456"),
    ])
    def test_the_usual_form(self, seconds, text):
        assert format_lap_time(seconds) == text

    def test_no_time_gives_a_dash(self):
        assert format_lap_time(None) == "-"

    def test_a_negative_time_keeps_its_sign(self):
        assert format_lap_time(-95.123).startswith("-1:35")


class TestGap:
    def test_a_gap_always_shows_its_sign(self):
        assert format_gap(0.25) == "+0.250s"
        assert format_gap(-0.25) == "-0.250s"

    def test_no_gap_gives_a_dash(self):
        assert format_gap(None) == "-"


class TestSpeed:
    def test_the_conversion_is_to_kilometres(self):
        """The API sends metres each second. Only the output converts."""
        assert kmh(50.0) == "180.0"


class TestFixedTable:
    def test_the_columns_align(self):
        block = fixed_table(["a", "bbbb"], [["1", "2"], ["333", "4"]])
        lines = block.split("\n")
        assert len({len(line) for line in lines}) == 1, "the rows are not equal"

    def test_there_are_no_pipe_characters(self):
        """A Markdown table spends one third of each row on the separators."""
        block = fixed_table(["a", "b"], [["1", "2"]])
        assert "|" not in block

    def test_no_rows_gives_an_empty_result(self):
        assert fixed_table(["a"], []) == ""


class TestUniformSeries:
    def test_the_spacing_is_given_one_time(self):
        text = uniform_series([0.1, 0.2, 0.3], 0.0, 100.0)
        assert text.count("spacing") == 1

    def test_the_values_do_not_carry_a_position(self):
        text = uniform_series([0.1, 0.2, 0.3], 0.0, 100.0)
        body = text.split("\n", 1)[1]
        assert "%" not in body, "a position was printed with each value"

    def test_the_header_gives_the_quantity(self):
        assert "3 values" in uniform_series([0.1, 0.2, 0.3], 0.0, 100.0)

    def test_no_values_gives_an_empty_result(self):
        assert uniform_series([], 0.0, 100.0) == ""


class TestLink:
    def test_the_url_uses_the_track_and_then_the_car(self):
        assert garage61_laps_url(18, 42).endswith("/app/laps/18/42")

    def test_an_absent_id_gives_no_url(self):
        assert garage61_laps_url(None, 42) is None
        assert garage61_laps_url(18, None) is None


class TestTheOutputBoundary:
    def test_ok_removes_the_bold_markers(self):
        content = _ok("**Sector 1** was **fast**")
        assert content[0].text == "Sector 1 was fast"

    def test_ok_keeps_the_headings(self):
        assert "## Corner 3" in _ok("## Corner 3")[0].text

    def test_ok_keeps_the_code_fences(self):
        assert "```" in _ok("```\n1 2 3\n```")[0].text

    def test_ok_keeps_the_legends(self):
        assert "_km/h_" in _ok("_km/h_")[0].text

    def test_an_error_gives_no_bold(self):
        assert "**" not in _err("no laps found")[0].text


def direct_builders(path):
    """The name of each innermost function that constructs TextContent."""
    tree = ast.parse(path.read_text())
    found = set()

    def visit(node, enclosing):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)          # the nearest function wins
                continue
            if isinstance(child, ast.Call) and getattr(child.func, "id", "") == "TextContent":
                found.add(enclosing)
            visit(child, enclosing)

    visit(tree, "<module>")
    return found


class TestTheChokepoint:
    """server.call_tool applies the rule to every response.

    A tool that builds its own TextContent cannot send markup. This is the
    fault that list_cars and list_tracks had, and it was visible only through
    a client.
    """

    def test_a_tool_that_skips_the_helper_is_still_corrected(self):
        from mcp.types import TextContent
        raw = [TextContent(type="text", text="**Ford Mustang** and **F4**")]
        assert sanitize(raw)[0].text == "Ford Mustang and F4"

    def test_every_item_is_corrected(self):
        from mcp.types import TextContent
        raw = [
            TextContent(type="text", text="**one**"),
            TextContent(type="text", text="**two**"),
        ]
        assert [c.text for c in sanitize(raw)] == ["one", "two"]

    def test_the_structure_stays(self):
        text = strip_markup("## Corner 3\n```\n1 2 3\n```\n_km/h_")
        assert "## Corner 3" in text and "```" in text and "_km/h_" in text

    def test_the_dispatch_result_passes_through_the_rule(self):
        """The call in server.py must wrap the dispatch, not follow it."""
        source = (SRC / "server.py").read_text()
        assert "sanitize(await _dispatch(" in source, (
            "call_tool no longer applies the boundary rule to the tool output"
        )


class TestNothingSkipsTheBoundary:
    """A second guard, so the fault is visible at the place that causes it."""

    def test_only_the_helpers_build_text_content(self):
        allowed = {"_ok", "_err"}
        extra = direct_builders(SRC / "tools.py") - allowed
        assert not extra, (
            f"tools.py: {sorted(extra)} build TextContent directly and thus "
            "skip the removal of the markup"
        )

    def test_the_server_error_paths_carry_no_markup(self):
        """server.py builds its own text, thus each string must be plain."""
        tree = ast.parse((SRC / "server.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "**" not in node.value, (
                    f"server.py line {node.lineno} carries a bold marker"
                )

    def test_no_builder_writes_a_bold_marker(self):
        """The output goes to a model. Bold was written and then removed.

        Now no builder writes it. strip_markup stays as the backstop.
        """
        for path in sorted(SRC.glob("*.py")):
            tree = ast.parse(path.read_text())

            # strip_markup holds "**" as data, not as output.
            allowed = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "strip_markup":
                    allowed.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                    continue
                if "**" in node.value and node.lineno not in allowed:
                    pytest.fail(f"{path.name}:{node.lineno} writes a bold marker")

    def test_no_source_file_writes_an_emoji(self):
        emoji = re.compile("[\U0001F300-\U0001FAFF\u2700-\u27BF\u2600-\u26FF]")
        for path in sorted(SRC.glob("*.py")):
            for number, line in enumerate(path.read_text().split("\n"), start=1):
                if emoji.search(line):
                    pytest.fail(f"{path.name}:{number} contains an emoji")
