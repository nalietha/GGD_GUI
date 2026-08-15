"""Node → pixel mapping.

**The only place in the codebase that knows how the strip is wired.**

V1 had ``15 - x if x < 8 else x - 8`` copy-pasted into ``led_utilities.py``,
``Utilities.cs``, and ``Index.cshtml.cs``, plus a fourth stub in
``LogicalNodeFromPreviewIndex`` that silently did nothing. Changing the wiring
meant finding all four and hoping. Here it is derived once from configuration,
so a second stand or a different node count is a config edit.

Vocabulary
----------

**Logical node** — what the user sees and configures: node 0 is the leftmost
slot on the stand.

**Physical position** — where that node sits along the data line, counting from
the controller.

**Pixel index** — the individual LED, which is what the driver addresses.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from livepulse.config.settings import LayoutSettings
from livepulse.core.errors import LayoutError

__all__ = ["Layout"]


def _serpentine_sequence(
    nodes: int,
    rows: int,
    *,
    start_at_last_row: bool,
    first_row_reversed: bool,
) -> list[int]:
    """Logical node ids in physical order for a boustrophedon layout.

    The data line snakes: it runs along one row, drops down, and runs back the
    other way. ``start_at_last_row`` says which end it enters from.

    Returns:
        ``sequence[physical_position] == logical_node_id``.
    """
    columns = nodes // rows
    row_order = list(range(rows))
    if start_at_last_row:
        row_order.reverse()

    sequence: list[int] = []
    for emitted, row in enumerate(row_order):
        row_nodes = list(range(row * columns, (row + 1) * columns))
        # Alternate direction each row; the flag flips where the snake starts.
        if (emitted % 2 == 1) != first_row_reversed:
            row_nodes.reverse()
        sequence.extend(row_nodes)
    return sequence


class Layout:
    """Immutable node ↔ pixel map built once at startup.

    All lookups are list indexes, because this sits in the render loop.
    """

    __slots__ = ("_leds_per_node", "_physical_of_logical", "_ranges", "_settings")

    def __init__(self, settings: LayoutSettings) -> None:
        self._settings = settings
        self._leds_per_node = settings.leds_per_node

        sequence = self._build_sequence(settings)
        if sorted(sequence) != list(range(settings.nodes)):
            raise LayoutError(
                f"layout produced an invalid mapping for order={settings.order!r}: "
                f"expected a permutation of 0..{settings.nodes - 1}"
            )

        # sequence[physical] = logical; invert it, since callers ask by logical.
        physical_of_logical = [0] * settings.nodes
        for physical, logical in enumerate(sequence):
            physical_of_logical[logical] = physical
        self._physical_of_logical = tuple(physical_of_logical)

        # Precompute pixel ranges — the render loop must not build these per frame.
        self._ranges = tuple(
            range(physical * settings.leds_per_node, (physical + 1) * settings.leds_per_node)
            for physical in self._physical_of_logical
        )

    @staticmethod
    def _build_sequence(settings: LayoutSettings) -> list[int]:
        if settings.order == "linear":
            return list(range(settings.nodes))
        if settings.order == "custom":
            if settings.custom_order is None:  # pragma: no cover - validated upstream
                raise LayoutError("order='custom' requires custom_order")
            return list(settings.custom_order)
        return _serpentine_sequence(
            settings.nodes,
            settings.rows,
            start_at_last_row=settings.start_at_last_row,
            first_row_reversed=settings.first_row_reversed,
        )

    @property
    def node_count(self) -> int:
        return self._settings.nodes

    @property
    def leds_per_node(self) -> int:
        return self._leds_per_node

    @property
    def pixel_count(self) -> int:
        return self._settings.nodes * self._leds_per_node

    def physical_position(self, node_id: int) -> int:
        """Where logical ``node_id`` sits along the data line.

        Raises:
            LayoutError: the node does not exist. V1 indexed out of range and
                either wrote past the buffer or silently did nothing.
        """
        self._require_node(node_id)
        return self._physical_of_logical[node_id]

    def pixels(self, node_id: int) -> range:
        """Pixel indices belonging to logical ``node_id``."""
        self._require_node(node_id)
        return self._ranges[node_id]

    def nodes(self) -> Iterator[int]:
        """Logical node ids, ascending."""
        return iter(range(self._settings.nodes))

    def _require_node(self, node_id: int) -> None:
        if not 0 <= node_id < self._settings.nodes:
            raise LayoutError(f"node {node_id} out of range (0..{self._settings.nodes - 1})")

    def describe(self) -> Sequence[tuple[int, int, range]]:
        """``(logical, physical, pixels)`` for every node.

        Backs ``livepulse leds identify``, so wiring can be verified against
        the config without guessing.
        """
        return [
            (node, self._physical_of_logical[node], self._ranges[node]) for node in self.nodes()
        ]

    def __repr__(self) -> str:
        return (
            f"Layout(nodes={self.node_count}, leds_per_node={self._leds_per_node}, "
            f"order={self._settings.order!r})"
        )
