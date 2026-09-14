#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Faster, conservative index scanner for XDrive-Search.

Designed for NAS/SMB shares:
- os.scandir()/DirEntry instead of Path creation + Path.stat per file
- larger SQLite batches
- time-throttled UI progress messages
- conservative delete reconciliation if a directory could not be scanned
- cancellation-safe full rebuild
"""

import os
import sqlite3
import time

EXCLUDE_DIR_NAMES = {
    "$RECYCLE.BIN",
    "System Volume Information",
    "$Recycle.Bin",
    ".git",
    "__pycache__",
    ".svn",
    "node_modules",
    ".vscode",
}
EXCLUDE_DIR_NAMES_LOWER = {name.lower() for name in EXCLUDE_DIR_NAMES}
EXCLUDE_FILE_NAMES_LOWER = {"thumbs.db", "desktop.ini", "ehthumbs.db", ".ds_store"}
EXCLUDE_PREFIXES = ("~$", ".~")
EXCLUDE_SUFFIXES = {".tmp", ".log", ".bak", ".swp", ".lock", ".lck"}

BATCH_SIZE = 5000
PROGRESS_INTERVAL_SECONDS = 0.75


def _exclude_dir(name):
    return name.startswith(".") or name.lower() in EXCLUDE_DIR_NAMES_LOWER


def _exclude_file(name):
    lower = name.lower()
    if lower in EXCLUDE_FILE_NAMES_LOWER:
        return True
    if name.startswith(EXCLUDE_PREFIXES):
        return True
    return os.path.splitext(lower)[1] in EXCLUDE_SUFFIXES


def _flush_batches(cur, batch_insert, batch_update):
    if batch_insert:
        cur.executemany(
            "INSERT INTO files(name,name_lower,path,dir,ext,size,mtime) VALUES (?,?,?,?,?,?,?)",
            batch_insert,
        )
        batch_insert.clear()
    if batch_update:
        cur.executemany(
            "UPDATE files SET name=?, name_lower=?, dir=?, ext=?, size=?, mtime=? WHERE path=?",
            batch_update,
        )
        batch_update.clear()


def build_index_fast(self, root_path, on_done=None, incremental=True):
    """Drop-in replacement for Indexer.build_index.

    Correctness policy:
    - Incremental scan: if a directory is unreadable, keep old rows for paths
      that could not be verified instead of falsely deleting them.
    - Full rebuild: if cancelled or any directory cannot be scanned, roll back
      to the previous index rather than committing a partial index.
    """
    self._stop.clear()
    root = os.path.abspath(os.fspath(root_path))

    if not os.path.isdir(root):
        self.progress_q.put(("error", f"路徑不存在或無法讀取: {root}"))
        if on_done:
            on_done(0)
        return

    started = time.monotonic()
    con = sqlite3.connect(self.db_path)
    try:
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass

        cur = con.cursor()
        try:
            cur.execute("SELECT COUNT(*) FROM files")
            existing_cnt = cur.fetchone()[0] or 0
            use_incremental = bool(incremental and existing_cnt > 0)
        except sqlite3.DatabaseError:
            use_incremental = False

        if use_incremental:
            cur.execute("SELECT path, size, mtime FROM files")
            existing_map = {path: (size, mtime) for path, size, mtime in cur.fetchall()}
        else:
            # Remains transactional. A failed/cancelled full rebuild is rolled back.
            cur.execute("DELETE FROM files")
            existing_map = {}

        count = 0
        new_cnt = 0
        upd_cnt = 0
        skip_cnt = 0
        del_cnt = 0
        excluded_cnt = 0
        dir_error_cnt = 0
        file_error_cnt = 0
        scanned_dirs = 0
        cancelled = False
        batch_insert = []
        batch_update = []
        stack = [root]
        last_progress = 0.0

        while stack:
            if self._stop.is_set():
                cancelled = True
                break

            dirpath = stack.pop()
            try:
                scan_it = os.scandir(dirpath)
            except OSError:
                dir_error_cnt += 1
                continue

            scanned_dirs += 1
            try:
                with scan_it:
                    for entry in scan_it:
                        if self._stop.is_set():
                            cancelled = True
                            break

                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if not _exclude_dir(entry.name):
                                    stack.append(entry.path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                        except OSError:
                            file_error_cnt += 1
                            continue

                        fn = entry.name
                        if _exclude_file(fn):
                            excluded_cnt += 1
                            continue

                        full = entry.path
                        try:
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            # Preserve old row on transient NAS/stat failures.
                            if use_incremental:
                                existing_map.pop(full, None)
                            file_error_cnt += 1
                            continue

                        size = st.st_size
                        mtime = st.st_mtime
                        ext = os.path.splitext(fn)[1].lower()
                        count += 1

                        old = existing_map.pop(full, None) if use_incremental else None
                        if old is not None:
                            old_size, old_mtime = old
                            if old_size == size and old_mtime == mtime:
                                skip_cnt += 1
                            else:
                                batch_update.append(
                                    (fn, fn.lower(), dirpath, ext, size, mtime, full)
                                )
                                upd_cnt += 1
                        else:
                            batch_insert.append(
                                (fn, fn.lower(), full, dirpath, ext, size, mtime)
                            )
                            new_cnt += 1

                        if len(batch_insert) + len(batch_update) >= BATCH_SIZE:
                            _flush_batches(cur, batch_insert, batch_update)

                        now = time.monotonic()
                        if now - last_progress >= PROGRESS_INTERVAL_SECONDS:
                            self.progress_q.put(
                                (
                                    "progress",
                                    f"⏳ 快速掃描... {dirpath} | "
                                    f"已檢查 {count} (新增{new_cnt}/更新{upd_cnt}/未變{skip_cnt})",
                                )
                            )
                            last_progress = now
            except OSError:
                # Errors while iterating an already-open directory.
                dir_error_cnt += 1

            if cancelled:
                break

        _flush_batches(cur, batch_insert, batch_update)

        if not use_incremental and (cancelled or dir_error_cnt):
            con.rollback()
            reason = "使用者停止" if cancelled else f"{dir_error_cnt} 個資料夾無法讀取"
            self.progress_q.put(
                ("error", f"完整重建未套用：{reason}；已保留原索引，避免產生不完整資料。")
            )
            if on_done:
                on_done(0)
            return

        if use_incremental and not cancelled and dir_error_cnt == 0 and existing_map:
            del_paths = list(existing_map)
            for i in range(0, len(del_paths), 900):
                chunk = del_paths[i : i + 900]
                cur.executemany("DELETE FROM files WHERE path=?", [(p,) for p in chunk])
            del_cnt = len(del_paths)
        # If cancelled or a directory failed, old unmatched rows are deliberately
        # kept because we cannot prove they were deleted.

        con.commit()

        if not use_incremental:
            cur.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
            con.commit()

        elapsed = time.monotonic() - started
        warning = ""
        if cancelled:
            warning = "；已停止，未執行刪除對帳"
        elif dir_error_cnt:
            warning = f"；{dir_error_cnt} 個資料夾讀取失敗，為安全起見未刪除未確認舊索引"
        elif file_error_cnt:
            warning = f"；{file_error_cnt} 個檔案讀取失敗，已保留可辨識的舊索引"

        if use_incremental:
            msg = (
                f"快速增量完成！檢查 {count} 個，新增{new_cnt}/更新{upd_cnt}/"
                f"刪除{del_cnt}/未變{skip_cnt}/排除{excluded_cnt}，耗時 {elapsed:.1f}s{warning}"
            )
        else:
            msg = (
                f"快速完整重建完成！共 {count} 個檔案，排除{excluded_cnt}，"
                f"耗時 {elapsed:.1f}s{warning}"
            )
        self.progress_q.put(("done", msg))

        if on_done:
            on_done(count)
    finally:
        con.close()
