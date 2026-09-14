#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XDrive-Search V2 相容入口。

V2 快速索引引擎已正式整合為 XDriveSearch 預設引擎
（Indexer.build_index 直接委派 fast_indexer.build_index_fast）。

維護成本最低做法：保留此檔為相容啟動器，直接沿用 XDriveSearch.App，
不再做 monkey-patch，不再維護第二套 scanner 來源。
"""

import XDriveSearch


if __name__ == "__main__":
    app = XDriveSearch.App()
    app.mainloop()
