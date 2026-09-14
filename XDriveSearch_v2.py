#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XDrive-Search V2 launcher with the faster NAS/SMB index scanner."""

import XDriveSearch as legacy
from fast_indexer import build_index_fast


# Keep the existing UI/search/database schema. Only replace the index update engine.
legacy.Indexer.build_index = build_index_fast


if __name__ == "__main__":
    app = legacy.App()
    app.mainloop()
