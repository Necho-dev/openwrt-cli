"""Tables whose periodic refresh follows a row without moving the viewport."""

from collections.abc import Iterator
from contextlib import contextmanager

from textual.coordinate import Coordinate
from textual.widgets import DataTable


class RefreshingDataTable(DataTable):
    _preserving_viewport = False

    @contextmanager
    def preserve_viewport(self, enabled: bool) -> Iterator[None]:
        previous = self._preserving_viewport
        self._preserving_viewport = enabled
        try:
            yield
        finally:
            self._preserving_viewport = previous

    def watch_cursor_coordinate(self, old_coordinate: Coordinate, new_coordinate: Coordinate) -> None:
        if not self._preserving_viewport or self.cursor_type != "row":
            super().watch_cursor_coordinate(old_coordinate, new_coordinate)
        elif old_coordinate != new_coordinate:
            # Keep Textual's row-highlight message without queuing its cursor scroll.
            self.refresh_row(old_coordinate.row)
            self._highlight_row(new_coordinate.row)
