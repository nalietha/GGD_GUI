"""Node → pixel mapping.

The most important test in this file is
:meth:`TestV1Compatibility.test_reproduces_the_v1_transform`: it pins the new
data-driven layout to the hardcoded expression the existing stand is wired for,
so migrating a real device cannot silently light the wrong node.
"""

from __future__ import annotations

import pytest

from livepulse.config.settings import LayoutSettings
from livepulse.core.errors import LayoutError
from livepulse.leds.layout import Layout


def v1_transform(node_id: int) -> int:
    """The V1 expression, verbatim from ``led_utilities.transform_node_id``."""
    return 15 - node_id if node_id < 8 else node_id - 8


class TestV1Compatibility:
    def test_reproduces_the_v1_transform(self) -> None:
        """Defaults must map identically to the deployed stand's wiring.

        V1 duplicated this expression across three files in two languages.
        Here it is derived from config — but it has to derive the same answer.
        """
        layout = Layout(LayoutSettings())
        actual = {node: layout.physical_position(node) for node in layout.nodes()}
        assert actual == {node: v1_transform(node) for node in range(16)}

    def test_pixel_ranges_match_v1_arithmetic(self) -> None:
        """V1 computed ``transform(node) * LEDS_PER_NODE`` inline in five effects."""
        layout = Layout(LayoutSettings())
        for node in range(16):
            start = v1_transform(node) * 7
            assert layout.pixels(node) == range(start, start + 7)

    def test_default_pixel_count_matches_v1(self) -> None:
        assert Layout(LayoutSettings()).pixel_count == 112  # V1's LED_COUNT


class TestSerpentine:
    def test_every_node_maps_to_a_unique_position(self) -> None:
        layout = Layout(LayoutSettings(nodes=24, rows=3, leds_per_node=4))
        positions = [layout.physical_position(node) for node in layout.nodes()]
        assert sorted(positions) == list(range(24))

    def test_pixel_ranges_tile_the_strip_exactly(self) -> None:
        """No gaps, no overlaps — every pixel belongs to exactly one node."""
        layout = Layout(LayoutSettings(nodes=16, leds_per_node=7))
        covered = [index for node in layout.nodes() for index in layout.pixels(node)]
        assert sorted(covered) == list(range(112))

    def test_rows_alternate_direction(self) -> None:
        layout = Layout(LayoutSettings(nodes=8, rows=2, start_at_last_row=False))
        # Row 0 (nodes 0-3) forward, then row 1 (nodes 4-7) reversed.
        assert [layout.physical_position(n) for n in range(8)] == [0, 1, 2, 3, 7, 6, 5, 4]

    def test_start_at_last_row_flips_which_row_is_first(self) -> None:
        layout = Layout(LayoutSettings(nodes=8, rows=2, start_at_last_row=True))
        assert [layout.physical_position(n) for n in range(8)] == [7, 6, 5, 4, 0, 1, 2, 3]

    def test_first_row_reversed_inverts_the_snake(self) -> None:
        layout = Layout(
            LayoutSettings(nodes=8, rows=2, start_at_last_row=False, first_row_reversed=True)
        )
        assert [layout.physical_position(n) for n in range(8)] == [3, 2, 1, 0, 4, 5, 6, 7]

    def test_single_row_is_linear(self) -> None:
        layout = Layout(LayoutSettings(nodes=8, rows=1))
        assert [layout.physical_position(n) for n in range(8)] == list(range(8))


class TestOtherOrders:
    def test_linear_is_identity(self) -> None:
        layout = Layout(LayoutSettings(nodes=16, order="linear"))
        assert [layout.physical_position(n) for n in range(16)] == list(range(16))

    def test_custom_order_is_honoured(self) -> None:
        """The escape hatch for wiring no generator predicts."""
        layout = Layout(LayoutSettings(nodes=4, order="custom", custom_order=[2, 0, 3, 1]))
        # custom_order[physical] = logical, so logical 2 sits at physical 0.
        assert layout.physical_position(2) == 0
        assert layout.physical_position(0) == 1
        assert layout.physical_position(3) == 2
        assert layout.physical_position(1) == 3


class TestBounds:
    @pytest.mark.parametrize("node_id", [-1, 16, 999])
    def test_out_of_range_node_raises(self, node_id: int) -> None:
        """V1 indexed past the buffer or silently did nothing."""
        layout = Layout(LayoutSettings())
        with pytest.raises(LayoutError, match="out of range"):
            layout.pixels(node_id)

    @pytest.mark.parametrize("node_id", [-1, 16])
    def test_out_of_range_physical_lookup_raises(self, node_id: int) -> None:
        layout = Layout(LayoutSettings())
        with pytest.raises(LayoutError, match="out of range"):
            layout.physical_position(node_id)


class TestIntrospection:
    def test_describe_covers_every_node(self) -> None:
        layout = Layout(LayoutSettings(nodes=16))
        described = layout.describe()
        assert len(described) == 16
        assert [row[0] for row in described] == list(range(16))

    def test_repr_is_informative(self) -> None:
        assert "nodes=16" in repr(Layout(LayoutSettings()))
