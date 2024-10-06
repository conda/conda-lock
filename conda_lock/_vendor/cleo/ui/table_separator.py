from __future__ import annotations

from conda_lock._vendor.cleo.ui.table_cell import TableCell


class TableSeparator(TableCell):
    def __init__(self) -> None:
        super().__init__("")
