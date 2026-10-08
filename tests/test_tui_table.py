from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import DataTable

from openwrt_cli.tui.app import _same_cell, repaint_datatable


class _TableApp(App):
    def compose(self) -> ComposeResult:
        yield DataTable(id="t")


def test_same_cell_ignores_fresh_text_instances():
    assert _same_cell(Text("0.00%", style="#67c23a"), Text("0.00%", style="#67c23a"))
    assert not _same_cell(Text("0.00%", style="#67c23a"), Text("12.00%", style="#f0a000"))
    assert _same_cell("eth0", "eth0")
    assert not _same_cell("eth0", "wan")


def test_repaint_keeps_scroll_and_cursor():
    app = _TableApp()

    async def body() -> None:
        async with app.run_test(size=(80, 12)) as pilot:
            table = app.query_one(DataTable)
            table.cursor_type = "row"
            table.add_columns("PID", "CPU")
            repaint_datatable(table, [(str(i), "0.00%") for i in range(40)])
            await pilot.pause()
            table.move_cursor(row=28)
            await pilot.pause()
            assert table.scroll_y > 0
            scroll_y = table.scroll_y
            repaint_datatable(table, [(str(i), "0.10%") for i in range(40)], follow_row=28)
            await pilot.pause()
            assert table.scroll_y == scroll_y
            assert table.cursor_row == 28
            assert table.get_row_at(28)[1] == "0.10%"
            assert table.row_count == 40

    asyncio.run(body())


def test_repaint_follows_row_without_jumping_the_viewport():
    app = _TableApp()

    async def body() -> None:
        async with app.run_test(size=(80, 12)) as pilot:
            table = app.query_one(DataTable)
            table.cursor_type = "row"
            table.add_columns("PID", "CPU")
            repaint_datatable(table, [(str(i), "0.00%") for i in range(40)], follow_row=30)
            await pilot.pause()
            scroll_y = table.scroll_y
            assert scroll_y > 0
            # The followed PID moves to the top of a new sort, but the viewport stays.
            repaint_datatable(table, [("30", "9.00%"), *[(str(i), "0.00%") for i in range(30)]], follow_row=0)
            await pilot.pause()
            assert table.cursor_row == 0
            assert table.scroll_y == scroll_y
            assert table.get_row_at(0)[0] == "30"

    asyncio.run(body())


def test_repaint_can_drop_rows_without_returning_to_the_top():
    app = _TableApp()

    async def body() -> None:
        async with app.run_test(size=(80, 12)) as pilot:
            table = app.query_one(DataTable)
            table.cursor_type = "row"
            table.add_columns("PID", "CPU")
            repaint_datatable(table, [(str(i), "0.00%") for i in range(40)], follow_row=24)
            await pilot.pause()
            scroll_y = table.scroll_y
            assert scroll_y > 0
            repaint_datatable(table, [(str(i), "0.00%") for i in range(30)], follow_row=24)
            await pilot.pause()
            assert table.row_count == 30
            assert table.cursor_row == 24
            assert table.scroll_y == scroll_y

    asyncio.run(body())
