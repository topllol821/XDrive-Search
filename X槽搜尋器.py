#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X搜索器 - 給共用碟/NAS/雲端碟用的 Everything 替代版
幾萬個檔案秒搜，支援 3D/2D 分類
作者：Muse Spark | 2026-08-31
"""
import os
import sys
import time
import threading
import sqlite3
import subprocess
from pathlib import Path
import pathlib
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import queue
import fnmatch
import shutil
import csv
import re
from functools import lru_cache
from collections import OrderedDict
from PIL import Image, ImageTk, ImageOps
# 拖曳到桌面需要的 win32
try:
    import win32clipboard
    import win32con
    HAS_WIN32 = True
except:
    HAS_WIN32 = False
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except:
    HAS_DND = False
    TkinterDnD = None
    DND_FILES = None

# ========== 設定 ==========
APP_NAME = "X搜索器 v1.3"
DB_DIR = Path(os.environ.get("XDRIVE_SEARCH_DB_DIR", str(Path.home() / ".kdrive_search")))
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "index.db"
# 預設掃描路徑 - 自動偵測 X:\ / K:\
def _detect_default_root():
    for cand in ["X:\\", "K:\\", "Z:\\", "Y:\\", "W:\\"]:
        try:
            if Path(cand).exists() and any(Path(cand).iterdir()):
                return cand
        except: continue
    return "X:\\"
DEFAULT_ROOT = os.environ.get("XDRIVE_SEARCH_ROOT") or _detect_default_root()
# A disconnected share must never silently redirect the scan to the user profile.

# 分類定義 - 依你 SolidWorks + AutoCAD 優化
CATEGORIES = {
    "全部": None,
    "3D圖": {".sldprt",".sldasm",".sldprtdot",".sldasmdot",".prt",".asm",".step",".stp",".iges",".igs",".x_t",".x_b",".stl",".obj",".fbx",".3dm",".skp",".max",".ipt",".iam",".catpart",".catproduct"},
    "2D圖": {".dwg",".dxf",".dwf",".dwt",".pdf",".ai",".psd",".cdr",".svg",".slddrw",".slddrt",".drwdot"}, # dwg/dxf 同時算 2D，也會在 3D 圖中出現是正常的
    "Excel": {".xls",".xlsx",".xlsm",".csv"},
    "PPT": {".ppt",".pptx",".pptm"},
    "Word": {".doc",".docx",".docm"},
    "PDF": {".pdf"},
    "圖片": {".jpg",".jpeg",".png",".bmp",".tif",".tiff",".gif",".webp"},
}

for k,v in CATEGORIES.items():
    if v: CATEGORIES[k] = {x.lower() for x in v}

# 黑名單：索引時跳過（增量更新時亦生效）
EXCLUDE_DIR_NAMES = {"$RECYCLE.BIN", "System Volume Information", "$Recycle.Bin", ".git", "__pycache__", ".svn", "node_modules", ".vscode"}
EXCLUDE_FILE_NAMES = {"Thumbs.db", "desktop.ini", "ehthumbs.db", ".DS_Store"}
EXCLUDE_PREFIXES = ("~$", ".~")
EXCLUDE_SUFFIXES = {".tmp", ".log", ".bak", ".swp", ".lock", ".lck"}

def _should_exclude(path_str, filename=None):
    """判斷是否該跳過（黑名單）"""
    fn = filename if filename is not None else pathlib.Path(path_str).name
    if fn in EXCLUDE_FILE_NAMES:
        return True
    if fn.startswith(EXCLUDE_PREFIXES):
        return True
    if pathlib.Path(fn).suffix.lower() in EXCLUDE_SUFFIXES:
        return True
    return False



SORT_COLUMNS = {"name": "name COLLATE NATURAL_NAME", "dir": "dir COLLATE NATURAL_NAME",
                "size": "size", "mtime": "mtime", "ext": "ext COLLATE NATURAL_NAME"}
SORT_LABELS = {"name":"檔名", "dir":"路徑", "size":"大小", "mtime":"修改日期", "ext":"類型"}

@lru_cache(maxsize=32768)
def _natural_key(value):
    return tuple((1, int(x)) if x.isdecimal() else (0, x.casefold())
                 for x in re.split(r"([0-9]+)", value or ""))

def natural_compare(a, b):
    x, y = _natural_key(a), _natural_key(b)
    return (x > y) - (x < y)

def shell_thumbnail(path, edge=256):
    """Read a genuine Windows thumbnail, never substitute the generic file icon."""
    if sys.platform != "win32":
        return None
    import ctypes as c
    from ctypes import wintypes as w
    import uuid
    class GUID(c.Structure):
        _fields_ = [("data", c.c_ubyte * 16)]
    class SIZE(c.Structure):
        _fields_ = [("cx", w.LONG), ("cy", w.LONG)]
    class BITMAP(c.Structure):
        _fields_ = [("bmType", w.LONG), ("bmWidth", w.LONG), ("bmHeight", w.LONG),
                    ("bmWidthBytes", w.LONG), ("bmPlanes", w.WORD), ("bmBitsPixel", w.WORD), ("bmBits", c.c_void_p)]
    class BMIHEADER(c.Structure):
        _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG),
                    ("biPlanes", w.WORD), ("biBitCount", w.WORD), ("biCompression", w.DWORD),
                    ("biSizeImage", w.DWORD), ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG),
                    ("biClrUsed", w.DWORD), ("biClrImportant", w.DWORD)]
    class BMI(c.Structure):
        _fields_ = [("bmiHeader", BMIHEADER), ("bmiColors", w.DWORD * 1)]
    ole, shell, gdi, user = c.windll.ole32, c.windll.shell32, c.windll.gdi32, c.windll.user32
    ole.CoInitializeEx.argtypes = [c.c_void_p, w.DWORD]
    ole.CoInitializeEx.restype = c.c_long
    hr = ole.CoInitializeEx(None, 2)
    if hr < 0:
        return None
    item, hbmp, dc = c.c_void_p(), c.c_void_p(), None
    release = None
    try:
        iid = GUID.from_buffer_copy(uuid.UUID("bcc18b79-ba16-442f-80c4-8a59c30c463b").bytes_le)
        shell.SHCreateItemFromParsingName.argtypes = [w.LPCWSTR, c.c_void_p, c.POINTER(GUID), c.POINTER(c.c_void_p)]
        shell.SHCreateItemFromParsingName.restype = c.c_long
        if shell.SHCreateItemFromParsingName(os.path.abspath(path), None, c.byref(iid), c.byref(item)) < 0 or not item.value:
            return None
        table = c.cast(item, c.POINTER(c.POINTER(c.c_void_p))).contents
        release = c.WINFUNCTYPE(w.ULONG, c.c_void_p)(table[2])
        get_image = c.WINFUNCTYPE(c.c_long, c.c_void_p, SIZE, c.c_int, c.POINTER(c.c_void_p))(table[3])
        # THUMBNAILONLY: providers may return no image; do not show generic icons.
        if get_image(item, SIZE(edge, edge), 0x8, c.byref(hbmp)) < 0 or not hbmp.value:
            return None
        gdi.GetObjectW.argtypes = [c.c_void_p, c.c_int, c.c_void_p]
        gdi.GetObjectW.restype = c.c_int
        bm = BITMAP()
        if not gdi.GetObjectW(hbmp, c.sizeof(bm), c.byref(bm)):
            return None
        width, height = bm.bmWidth, abs(bm.bmHeight)
        if not (0 < width <= 2048 and 0 < height <= 2048):
            return None
        bmi = BMI()
        bmi.bmiHeader.biSize = c.sizeof(BMIHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        buf = c.create_string_buffer(width * height * 4)
        user.GetDC.argtypes = [c.c_void_p]
        user.GetDC.restype = c.c_void_p
        user.ReleaseDC.argtypes = [c.c_void_p, c.c_void_p]
        dc = user.GetDC(None)
        if not dc:
            return None
        gdi.GetDIBits.argtypes = [c.c_void_p, c.c_void_p, w.UINT, w.UINT, c.c_void_p, c.POINTER(BMI), w.UINT]
        gdi.GetDIBits.restype = c.c_int
        if gdi.GetDIBits(dc, hbmp, 0, height, buf, c.byref(bmi), 0) != height:
            return None
        return Image.frombytes("RGB", (width, height), buf.raw, "raw", "BGRX")
    finally:
        if dc:
            user.ReleaseDC(None, dc)
        if hbmp.value:
            gdi.DeleteObject.argtypes = [c.c_void_p]
            gdi.DeleteObject(hbmp)
        if release and item.value:
            release(item)
        ole.CoUninitialize()

def load_thumbnail(path, edge=256):
    ext = Path(path).suffix.lower()
    if ext in CATEGORIES["圖片"]:
        try:
            with Image.open(path) as img:
                img.thumbnail((edge, edge))
                return ImageOps.exif_transpose(img).convert("RGBA")
        except Exception:
            pass
    if ext == ".pdf":
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(path)
            try:
                if len(doc):
                    page = doc[0]
                    try:
                        width, height = page.get_size()
                        bitmap = page.render(scale=min(edge / width, edge / height))
                        try:
                            return bitmap.to_pil().convert("RGB")
                        finally:
                            bitmap.close()
                    finally:
                        page.close()
            finally:
                doc.close()
        except Exception:
            pass
    return shell_thumbnail(path, edge)

class Indexer:
    def __init__(self, db_path=DB_PATH):
        self.db_path = Path(db_path)
        self._stop = threading.Event()
        self._scan_lock = threading.Lock()
        self.fts_enabled = False
        self.progress_q = queue.Queue()
        self.init_db()

    def init_db(self):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("BEGIN IMMEDIATE")
            con.execute("""CREATE TABLE IF NOT EXISTS files(
                id INTEGER PRIMARY KEY, name TEXT, name_lower TEXT,
                path TEXT, dir TEXT, ext TEXT, size INTEGER, mtime REAL)""")
            for col in ("name_lower", "ext", "dir", "path", "mtime"):
                con.execute(f"CREATE INDEX IF NOT EXISTS idx_{col} ON files({col})")
            con.execute("CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY, value TEXT)")
            con.execute("SAVEPOINT fts_migration")
            try:
                cols = [r[1] for r in con.execute("PRAGMA table_info(files_fts)")]
                migrate = "name_lower" not in cols
                if migrate:
                    for trigger in ("files_ai", "files_ad", "files_au"):
                        con.execute("DROP TRIGGER IF EXISTS " + trigger)
                    con.execute("DROP TABLE IF EXISTS files_fts")
                con.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                    name_lower, content='files', content_rowid='id', tokenize='trigram')""")
                con.execute("""CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
                    INSERT INTO files_fts(rowid,name_lower) VALUES(new.id,new.name_lower); END""")
                con.execute("""CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
                    INSERT INTO files_fts(files_fts,rowid,name_lower) VALUES('delete',old.id,old.name_lower); END""")
                con.execute("""CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
                    INSERT INTO files_fts(files_fts,rowid,name_lower) VALUES('delete',old.id,old.name_lower);
                    INSERT INTO files_fts(rowid,name_lower) VALUES(new.id,new.name_lower); END""")
                if migrate:
                    con.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
                con.execute("RELEASE fts_migration")
                self.fts_enabled = True
            except sqlite3.Error:
                con.execute("ROLLBACK TO fts_migration")
                con.execute("RELEASE fts_migration")
                # Fall back to LIKE without depending on FTS triggers.
                for trigger in ("files_ai", "files_ad", "files_au"):
                    con.execute("DROP TRIGGER IF EXISTS " + trigger)
                con.execute("INSERT OR REPLACE INTO index_meta(key,value) VALUES('fts_dirty','1')")
                self.fts_enabled = False
            if self.fts_enabled and con.execute("SELECT value FROM index_meta WHERE key='fts_dirty'").fetchone():
                con.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
                con.execute("DELETE FROM index_meta WHERE key='fts_dirty'")
            con.commit()
        finally:
            con.close()

    @staticmethod
    def root_key(root):
        return os.path.normcase(os.path.abspath(os.path.normpath(str(root))))

    def indexed_root(self):
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute("SELECT value FROM index_meta WHERE key='root'").fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def build_index(self, root_path, on_done=None, incremental=True):
        """Atomic scan: cancellation or unreadable directories preserve the old index."""
        if not self._scan_lock.acquire(blocking=False):
            self.progress_q.put(("error", "已有索引更新正在執行"))
            return False
        self._stop.clear()
        con = None
        start = time.time()
        try:
            root = Path(os.path.abspath(root_path))
            root_key = self.root_key(root)
            if not root.is_dir():
                raise OSError(f"路徑不存在或無法讀取: {root}")
            con = sqlite3.connect(self.db_path, timeout=10)
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("BEGIN IMMEDIATE")
            cur = con.cursor()
            prev = cur.execute("SELECT value FROM index_meta WHERE key='root'").fetchone()
            use_incremental = bool(incremental and prev and prev[0] == root_key)
            if use_incremental:
                existing_map = {r[0]: (r[1], r[2]) for r in cur.execute("SELECT path,size,mtime FROM files")}
            else:
                cur.execute("DELETE FROM files")
                existing_map = {}
            count = new_cnt = upd_cnt = excluded_cnt = scanned_dirs = 0
            batch_insert, batch_update = [], []
            stack = [str(root)]
            def cancelled():
                if self._stop.is_set():
                    raise InterruptedError("使用者停止掃描")
            while stack:
                cancelled()
                dirpath = stack.pop()
                with os.scandir(dirpath) as entries:
                    scanned_dirs += 1
                    for entry in entries:
                        cancelled()
                        fn = entry.name
                        if entry.is_dir(follow_symlinks=False):
                            if fn in EXCLUDE_DIR_NAMES or fn.startswith(".") or getattr(os.path, "isjunction", lambda _: False)(entry.path):
                                excluded_cnt += 1
                            else:
                                stack.append(entry.path)
                            continue
                        if entry.is_symlink() or _should_exclude(dirpath, fn):
                            excluded_cnt += 1
                            continue
                        st = entry.stat(follow_symlinks=False)
                        size, mtime = st.st_size, st.st_mtime
                        full, ext = entry.path, Path(fn).suffix.lower()
                        old = existing_map.pop(full, None)
                        if old is not None:
                            if old != (size, mtime):
                                batch_update.append((fn, fn.lower(), dirpath, ext, size, mtime, full))
                                upd_cnt += 1
                        else:
                            batch_insert.append((fn, fn.lower(), full, dirpath, ext, size, mtime))
                            new_cnt += 1
                        count += 1
                        if len(batch_insert) >= 2000:
                            cur.executemany("INSERT INTO files(name,name_lower,path,dir,ext,size,mtime) VALUES (?,?,?,?,?,?,?)", batch_insert)
                            batch_insert.clear()
                        if len(batch_update) >= 2000:
                            cur.executemany("UPDATE files SET name=?,name_lower=?,dir=?,ext=?,size=?,mtime=? WHERE path=?", batch_update)
                            batch_update.clear()
                        if count % 1500 == 0:
                            self.progress_q.put(("progress", f"掃描中... {count} 個檔案"))
                if scanned_dirs % 30 == 0:
                    self.progress_q.put(("progress", f"掃描中... {dirpath} | {count} 個檔案"))
            cancelled()
            if batch_insert:
                cur.executemany("INSERT INTO files(name,name_lower,path,dir,ext,size,mtime) VALUES (?,?,?,?,?,?,?)", batch_insert)
            if batch_update:
                cur.executemany("UPDATE files SET name=?,name_lower=?,dir=?,ext=?,size=?,mtime=? WHERE path=?", batch_update)
            with os.scandir(root) as entries:
                next(entries, None)
            del_cnt = len(existing_map)
            if existing_map:
                cur.executemany("DELETE FROM files WHERE path=?", ((p,) for p in existing_map))
            cancelled()
            cur.execute("INSERT OR REPLACE INTO index_meta(key,value) VALUES('root',?)", (root_key,))
            con.commit()
            mode = "增量" if use_incremental else "完整重建"
            self.progress_q.put(("done", f"{mode}完成！掃描 {count} 個，新增{new_cnt}/更新{upd_cnt}/刪除{del_cnt}/略過{excluded_cnt}，耗時 {time.time()-start:.1f}s"))
            return True
        except InterruptedError:
            if con is not None:
                con.rollback()
            self.progress_q.put(("cancelled", "已停止掃描，原有索引完整保留"))
            return False
        except Exception as e:
            if con is not None:
                con.rollback()
            self.progress_q.put(("error", f"索引未更新，原有索引完整保留：{e}"))
            return False
        finally:
            if con is not None:
                con.close()
            self._scan_lock.release()
            if on_done:
                on_done(0)

    def stop(self):
        self._stop.set()

    def search(self, keyword, category="全部", limit=5000, date_from=None, date_to=None, size_from=None, size_to=None, sort_col="mtime", descending=True):
        con = sqlite3.connect(self.db_path)
        con.create_collation("NATURAL_NAME", natural_compare)
        where, params = [], []
        ext_filter = CATEGORIES.get(category)
        for t in (keyword or "").strip().lower().split():
            pat = t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("*", "%").replace("?", "_")
            use_fts = False
            if self.fts_enabled and len(t) >= 3 and not any(c in t for c in "*?%_\\"):
                # A bounded probe avoids slow FTS sorting for broad searches.
                candidate_count = con.execute(
                    "SELECT count(*) FROM (SELECT rowid FROM files_fts WHERE name_lower LIKE ? LIMIT 1025)",
                    (f"%{t}%",)).fetchone()[0]
                use_fts = candidate_count <= 1024
            if use_fts:
                where.append("id IN (SELECT rowid FROM files_fts WHERE name_lower LIKE ?)")
                params.append(f"%{t}%")
            else:
                where.append("name_lower LIKE ? ESCAPE '\\'")
                params.append(f"%{pat}%")
        if ext_filter is not None:
            where.append("ext IN (" + ",".join("?" for _ in ext_filter) + ")")
            params.extend(list(ext_filter))
        for col, op, value in (("mtime", ">=", date_from), ("mtime", "<=", date_to),
                               ("size", ">=", size_from), ("size", "<=", size_to)):
            if value is not None:
                where.append(f"{col} {op} ?")
                params.append(value)
        sql = "SELECT name,path,dir,ext,size,mtime FROM files"
        if where:
            sql += " WHERE " + " AND ".join(where)
        order = SORT_COLUMNS.get(sort_col, SORT_COLUMNS["mtime"])
        direction = "DESC" if descending else "ASC"
        sql += f" ORDER BY {order} {direction}, path COLLATE NOCASE ASC, id ASC LIMIT ?"
        params.append(limit)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def stats(self):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        try:
            cur.execute("SELECT COUNT(*), SUM(size) FROM files")
            n, s = cur.fetchone()
        except:
            n,s = 0,0
        con.close()
        return n or 0, s or 0


BaseTk = TkinterDnD.Tk if HAS_DND else tk.Tk
class App(BaseTk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}")
        self.geometry("1100x680")
        self.minsize(900, 520)
        # 讓視窗在工作列有圖示
        try: self.iconbitmap("")
        except: pass
        self.indexer = Indexer()
        self.current_category = "全部"
        self.search_after_id = None
        self._index_running = False
        self.sort_col = "mtime"
        self.sort_desc = True
        self._preview_request = 0
        self._preview_after = None
        self._preview_jobs = queue.Queue(maxsize=1)
        self._preview_results = queue.Queue()
        self._preview_cache = OrderedDict()
        self._preview_img = None
        self._preview_photo = None
        self._preview_path = None
        self._preview_edge = 0
        self._preview_resize_after = None
        self._preview_drag = None
        self._preview_default_height = 84
        self._row_meta = {}
        self.root_path_var = tk.StringVar(value=self.indexer.indexed_root() or DEFAULT_ROOT)
        self.keyword_var = tk.StringVar()
        self.status_var = tk.StringVar(value="準備就緒")
        self.create_ui()
        threading.Thread(target=self._preview_worker, daemon=True).start()
        self.after(60, self._poll_preview)
        self.check_progress()
        # 啟動時自動載入統計
        self.update_stats()
        self.bind_events()
        # 如果 DB 是空的，提示掃描
        n,_ = self.indexer.stats()
        if n == 0:
            self.status_var.set("索引為空，請按「建立/更新索引」開始掃描 K:\\")
        else:
            self.do_search()

    def create_ui(self):
        style = ttk.Style()
        try: style.theme_use("vista")
        except: style.theme_use("clam")
        style.configure("TButton", padding=6)
        style.configure("Tool.TButton", font=("Microsoft JhengHei", 9))
        # 頂部工具列
        top = ttk.Frame(self, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text="索引掃描路徑:").pack(side=tk.LEFT)
        ent_path = ttk.Entry(top, textvariable=self.root_path_var, width=28)
        ent_path.pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="瀏覽...", command=self.browse).pack(side=tk.LEFT)
        ttk.Button(top, text="建立 / 更新索引", command=self.start_index).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="停止", command=self.indexer.stop).pack(side=tk.LEFT)
        self.incremental_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="增量更新", variable=self.incremental_var).pack(side=tk.LEFT, padx=(10,2))
        ttk.Label(top, text="(黑名單 Thumbs.db/~$/.tmp 已排除)", foreground="#6b7280", font=("Microsoft JhengHei", 7)).pack(side=tk.LEFT)
        # 搜尋列
        search_frame = ttk.Frame(self, padding=(10,0,10,8))
        search_frame.pack(fill=tk.X)
        ttk.Label(search_frame, text="🔍", font=("Segoe UI Emoji", 14)).pack(side=tk.LEFT)
        self.entry_search = ttk.Entry(search_frame, textvariable=self.keyword_var, font=("Microsoft JhengHei", 12))
        self.entry_search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.entry_search.focus_set()
        ttk.Button(search_frame, text="清除", command=lambda: (self.keyword_var.set(""), self.do_search())).pack(side=tk.LEFT)
        # 分類按鈕
        control_area = ttk.Frame(self, padding=(10,0,10,8))
        control_area.pack(fill=tk.X)
        control_area.columnconfigure(0, weight=0)
        control_area.columnconfigure(1, weight=1, minsize=170)
        controls = ttk.Frame(control_area)
        controls.grid(row=0, column=0, sticky="nw")
        cat_frame = ttk.Frame(controls, padding=(0,0,0,8))
        cat_frame.pack(fill=tk.X)
        ttk.Label(cat_frame, text="分類:").pack(side=tk.LEFT)
        self.cat_buttons = {}
        for cat in ["全部","3D圖","2D圖","Excel","PPT","Word","PDF","圖片"]:
            b = ttk.Button(cat_frame, text=cat, width=7, command=lambda c=cat: self.set_category(c))
            b.pack(side=tk.LEFT, padx=3)
            self.cat_buttons[cat] = b
        self.highlight_cat()
        # 篩選列：日期 / 大小 + 匯出
        filter_frame = ttk.Frame(controls, padding=(0,0,0,8))
        filter_frame.pack(fill=tk.X)
        # 日期篩選
        ttk.Label(filter_frame, text="日期:").pack(side=tk.LEFT)
        self.date_from_var = tk.StringVar()
        self.date_to_var = tk.StringVar()
        ent_from = ttk.Entry(filter_frame, textvariable=self.date_from_var, width=12, font=("Microsoft JhengHei", 9))
        ent_from.pack(side=tk.LEFT, padx=(4,2))
        ent_from.insert(0, "")
        ent_from.bind("<FocusIn>", lambda e: self._show_hint(ent_from, "YYYY-MM-DD"))
        ttk.Label(filter_frame, text="~").pack(side=tk.LEFT)
        ent_to = ttk.Entry(filter_frame, textvariable=self.date_to_var, width=12, font=("Microsoft JhengHei", 9))
        ent_to.pack(side=tk.LEFT, padx=2)
        # 快捷按鈕
        ttk.Button(filter_frame, text="今天", width=5, command=lambda: self.set_date_preset("today")).pack(side=tk.LEFT, padx=2)
        ttk.Button(filter_frame, text="7天", width=5, command=lambda: self.set_date_preset("7d")).pack(side=tk.LEFT, padx=2)
        ttk.Button(filter_frame, text="30天", width=5, command=lambda: self.set_date_preset("30d")).pack(side=tk.LEFT, padx=2)
        ttk.Button(filter_frame, text="清除", width=5, command=lambda: self.clear_date_filter()).pack(side=tk.LEFT, padx=2)
        # 大小篩選 + 匯出 同一行右側
        size_frame = ttk.Frame(controls, padding=(0,0,0,0))
        size_frame.pack(fill=tk.X)
        ttk.Label(size_frame, text="大小:").pack(side=tk.LEFT)
        self.size_min_var = tk.StringVar()
        self.size_max_var = tk.StringVar()
        ent_min = ttk.Entry(size_frame, textvariable=self.size_min_var, width=8, font=("Microsoft JhengHei", 9))
        ent_min.pack(side=tk.LEFT, padx=(4,2))
        ttk.Label(size_frame, text="~").pack(side=tk.LEFT)
        ent_max = ttk.Entry(size_frame, textvariable=self.size_max_var, width=8, font=("Microsoft JhengHei", 9))
        ent_max.pack(side=tk.LEFT, padx=2)
        ttk.Label(size_frame, text="MB").pack(side=tk.LEFT)
        ttk.Button(size_frame, text="<10M", width=6, command=lambda: self.set_size_preset("<10")).pack(side=tk.LEFT, padx=2)
        ttk.Button(size_frame, text="10-100M", width=8, command=lambda: self.set_size_preset("10-100")).pack(side=tk.LEFT, padx=2)
        ttk.Button(size_frame, text=">100M", width=6, command=lambda: self.set_size_preset(">100")).pack(side=tk.LEFT, padx=2)
        ttk.Button(size_frame, text="清除", width=5, command=lambda: self.clear_size_filter()).pack(side=tk.LEFT, padx=2)
        ttk.Separator(size_frame, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(size_frame, text="匯出 CSV", command=self.export_csv).pack(side=tk.LEFT, padx=4)
        ttk.Button(size_frame, text="匯出 Excel", command=self.export_excel).pack(side=tk.LEFT, padx=2)
        preview = ttk.LabelFrame(control_area, text="縮圖預覽", padding=(4,2,4,4))
        preview.grid(row=0, column=1, sticky="nsew", padx=(12,0))
        self.preview_canvas = tk.Canvas(preview, width=170, height=self._preview_default_height, background="#fafafa", highlightthickness=0)
        self.preview_handle = tk.Label(preview, text="━━━━  上下拖曳調整預覽大小  ━━━━", bg="#e4e7eb", fg="#4b5563", cursor="sb_v_double_arrow", font=("Microsoft JhengHei", 8), pady=2)
        self.preview_handle.pack(side=tk.BOTTOM, fill=tk.X)
        self.preview_canvas.pack(fill=tk.BOTH, expand=True)
        self.preview_handle.bind("<ButtonPress-1>", self._preview_drag_start)
        self.preview_handle.bind("<B1-Motion>", self._preview_drag_move)
        self.preview_handle.bind("<ButtonRelease-1>", self._preview_drag_end)
        self.preview_handle.bind("<Double-Button-1>", self._preview_reset_size)
        self.preview_canvas.bind("<Configure>", self._preview_canvas_changed)
        self.bind("<Configure>", self._preview_window_changed, add="+")
        self._preview_text = "選取檔案後顯示縮圖"
        self._render_preview()
        # 綁定自動搜尋
        for var in [self.date_from_var, self.date_to_var, self.size_min_var, self.size_max_var]:
            var.trace_add("write", lambda *_: self.after(600, self.do_search))

        # 結果表格
        mid = ttk.Frame(self, padding=(10,0,10,0))
        self.results_frame = mid
        mid.pack(fill=tk.BOTH, expand=True)
        cols = ("name","dir","size","mtime","ext")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        self._update_sort_headings()
        self.tree.column("name", width=340, anchor=tk.W)
        self.tree.column("dir", width=420, anchor=tk.W)
        self.tree.column("size", width=90, anchor=tk.E)
        self.tree.column("mtime", width=130, anchor=tk.CENTER)
        self.tree.column("ext", width=70, anchor=tk.CENTER)
        vsb = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(mid, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)
        # 進度條 + 狀態列
        prog_frame = ttk.Frame(self, padding=(10,4,10,0))
        prog_frame.pack(fill=tk.X, side=tk.BOTTOM)
        self.progress = ttk.Progressbar(prog_frame, mode="indeterminate")
        self.progress.pack(fill=tk.X)
        status = ttk.Frame(self, padding=6)
        status.pack(fill=tk.X, side=tk.BOTTOM)
        # 左側放會閃的指示燈
        self.dot_var = tk.StringVar(value="●")
        self.dot_label = ttk.Label(status, textvariable=self.dot_var, foreground="#16a34a", font=("Segoe UI", 10, "bold"))
        self.dot_label.pack(side=tk.LEFT, padx=(0,6))
        ttk.Label(status, textvariable=self.status_var, foreground="#333").pack(side=tk.LEFT)
        self.count_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.count_var, foreground="#666").pack(side=tk.RIGHT)
        # 右鍵選單
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="開啟檔案", command=self.open_selected)
        self.menu.add_command(label="開啟所在資料夾", command=self.open_folder)
        self.menu.add_separator()
        self.menu.add_command(label="複製到桌面", command=self.copy_to_desktop)
        self.menu.add_command(label="複製到... (選資料夾)", command=self.copy_to_folder)
        self.menu.add_separator()
        self.menu.add_command(label="複製完整路徑", command=self.copy_path)
        self.menu.add_command(label="複製檔名", command=self.copy_name)
        self.menu.add_separator()
        self.menu.add_command(label="刪除選取檔案", command=self.delete_selected)
        # 拖曳提示 - 更新為多選
        hint = "💡 提示：按住 Ctrl 點選多個，或 Shift 選一排 | 選好後直接拖到桌面 或 Ctrl+C→桌面Ctrl+V 一次複製多個"
        ttk.Label(self, text=hint, foreground="#6b7280", font=("Microsoft JhengHei", 8)).pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0,2))

    def browse(self):
        p = filedialog.askdirectory(title="選擇要索引的資料夾 (例如 K:\\)", initialdir=self.root_path_var.get())
        if p:
            self.root_path_var.set(p)

    def set_category(self, cat):
        self.current_category = cat
        self.highlight_cat()
        self.do_search()

    def highlight_cat(self):
        for cat, btn in self.cat_buttons.items():
            if cat == self.current_category:
                btn.configure(style="Accent.TButton")
            else:
                btn.configure(style="TButton")
        # 動態建立 Accent 樣式
        s = ttk.Style()
        # 選中時：黑字 + 藍框（不要白字）
        s.configure("Accent.TButton", background="white", foreground="black", bordercolor="#2563eb", borderwidth=2, relief="solid", font=("Microsoft JhengHei", 9, "bold"))
        s.map("Accent.TButton", foreground=[("active","black"),("pressed","black"),("selected","black"),("!disabled","black")], background=[("active","#eff6ff"),("pressed","#dbeafe")], bordercolor=[("active","#1d4ed8"),("pressed","#1e40af")], relief=[("pressed","sunken")])

    def bind_events(self):
        self.keyword_var.trace_add("write", lambda *_: self.schedule_search())
        self.tree.bind("<Double-1>", lambda e: self.open_selected() if self.tree.identify_region(e.x,e.y) in ("cell","tree") else None)
        self.tree.bind("<<TreeviewSelect>>", self._schedule_preview, add="+")
        self.tree.bind("<Button-3>", self.show_menu)
        self.tree.bind("<Return>", lambda e: self.open_selected())
        self.bind("<Control-f>", lambda e: self.entry_search.focus_set())
        self.bind("<F5>", lambda e: self.start_index())
        # === 拖曳到桌面 ===
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.drag_paths = []
        self.tree.bind("<ButtonPress-1>", self.on_drag_press, add="+")
        self.tree.bind("<Control-ButtonPress-1>", self.on_ctrl_click)
        self.tree.bind("<B1-Motion>", self.on_drag_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self.on_drag_release, add="+")
        # 也支援 Ctrl+C 複製檔案到剪貼簿（可直接到桌面 Ctrl+V）
        self.tree.bind("<Control-c>", lambda e: self.copy_files_to_clipboard())
        self.tree.bind("<Control-C>", lambda e: self.copy_files_to_clipboard())
        # 額外支援 Ctrl+A 全選
        self.tree.bind("<Control-a>", lambda e: (self.tree.selection_set(self.tree.get_children()), self._update_drag_paths(), "break")[2])
        self.tree.bind("<Control-A>", lambda e: (self.tree.selection_set(self.tree.get_children()), self._update_drag_paths(), "break")[2])
        self.tree.bind("<Delete>", lambda e: (self.delete_selected(), "break")[1])
        # Delete only when the result list has keyboard focus.

    def schedule_search(self):
        if self.search_after_id:
            self.after_cancel(self.search_after_id)
        self.search_after_id = self.after(180, self.do_search)

    def do_search(self):
        kw = self.keyword_var.get()
        date_from, date_to = self.get_date_range()
        size_from, size_to = self.get_size_range()
        try:
            rows = self.indexer.search(kw, self.current_category, limit=8000, date_from=date_from, date_to=date_to, size_from=size_from, size_to=size_to, sort_col=self.sort_col, descending=self.sort_desc)
        except sqlite3.Error as exc:
            self.status_var.set(f"搜尋失敗（保留上次結果）：{exc}")
            return
        selected_paths = {self.tree.item(i, "tags")[0] for i in self.tree.selection() if self.tree.item(i, "tags")}
        focused = self.tree.focus()
        focused_tags = self.tree.item(focused, "tags") if focused else ()
        focus_path = focused_tags[0] if focused_tags else None
        self.tree.delete(*self.tree.get_children())
        self._row_meta = {}
        selected_ids = []
        for name, path, dirpath, ext, size, mtime in rows:
            try:
                size_str = self.human_size(size)
                mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else ""
            except:
                size_str, mtime_str = "", ""
            iid = self.tree.insert("", tk.END, values=(name, dirpath, size_str, mtime_str, ext), tags=(path,))
            self._row_meta[iid] = (path, size, mtime)
            if path in selected_paths:
                selected_ids.append(iid)
            if path == focus_path:
                self.tree.focus(iid)
        if selected_ids:
            self.tree.selection_set(selected_ids)
        self._schedule_preview()
        cap = "（最多 8000 筆，請縮小條件）" if len(rows) >= 8000 else ""
        self.count_var.set(f"顯示 {len(rows)} 筆{cap}" + (f" / 關鍵字: {kw}" if kw else ""))
        if not rows and kw:
            self.status_var.set(f"沒有找到「{kw}」在「{self.current_category}」中，試試切到「全部」或減少關鍵字")

    def human_size(self, n):
        if n is None: return ""
        for unit in ["B","KB","MB","GB"]:
            if abs(n) < 1024: return f"{n:.1f} {unit}" if unit!="B" else f"{n} B"
            n/=1024
        return f"{n:.1f} TB"

    def start_index(self):
        if self._index_running:
            self.status_var.set("索引更新正在執行，請勿重複啟動")
            return
        root = self.root_path_var.get().strip().replace("/", "\\")
        self.root_path_var.set(root)
        if not Path(root).exists():
            messagebox.showerror("路徑錯誤", f"找不到路徑:\n{root}\n\n請確認 X: / K: 已連線，或按「瀏覽」重選。")
            return
        previous = self.indexer.indexed_root()
        inc = bool(self.incremental_var.get() and previous == self.indexer.root_key(root))
        mode = "增量更新" if inc else "完整重建（成功後取代原索引）"
        if messagebox.askyesno("建立索引", f"即將{mode}掃描:\n{root}\n\n掃描時間取決於網路及檔案量。成功後才更新索引，期間可使用舊索引。\n\n黑名單已排除 Thumbs.db/~$/.tmp 等暫存檔。\n開始嗎？"):
            self.status_var.set(f"⏳ 開始掃描 {root} ... 請稍候，正在讀取檔案清單")
            self.dot_var.set("⏳")
            self.dot_label.configure(foreground="#f59e0b")
            try: self.progress.start(12)
            except: pass
            self.update_idletasks()
            self._index_running = True
            th = threading.Thread(target=self.indexer.build_index, args=(root, None, inc), daemon=True)
            th.start()

    def on_index_done(self):
        try: self.progress.stop()
        except: pass
        self.dot_var.set("●")
        self.dot_label.configure(foreground="#16a34a")
        self.update_stats()
        self.do_search()
        # 完成提示閃一下
        self.bell()

    def update_stats(self):
        n, s = self.indexer.stats()
        if n:
            indexed = self.indexer.indexed_root() or "舊版索引（路徑待確認）"
            self.status_var.set(f"索引就緒：{indexed} | {n} 個檔案 | 約 {self.human_size(s)} | F5 更新")

    def check_progress(self):
        try:
            while not self.indexer.progress_q.empty():
                typ, msg = self.indexer.progress_q.get_nowait()
                self.status_var.set(msg)
                # 讓進度條保持動態
                if typ == "progress":
                    try: self.progress.start(12)
                    except: pass
                    self.dot_var.set("⏳")
                if typ == "done":
                    self._index_running = False
                    try: self.progress.stop()
                    except: pass
                    self.dot_var.set("●")
                    self.dot_label.configure(foreground="#16a34a")
                    self.update_stats()
                    self.do_search()
                    self.status_var.set(msg)
                if typ in ("error", "cancelled"):
                    self._index_running = False
                    try: self.progress.stop()
                    except: pass
                    self.dot_var.set("●")
        except queue.Empty:
            pass
        self.after(400, self.check_progress)

    def _show_hint(self, entry, hint):
        if not entry.get():
            entry.insert(0, hint)
            entry.configure(foreground="#9ca3af")
            def on_focus_in(e):
                if entry.get() == hint:
                    entry.delete(0, tk.END)
                    entry.configure(foreground="black")
            def on_focus_out(e):
                if not entry.get():
                    entry.insert(0, hint)
                    entry.configure(foreground="#9ca3af")
            entry.bind("<FocusIn>", on_focus_in)
            entry.bind("<FocusOut>", on_focus_out)

    def get_date_range(self):
        """解析日期篩選，回傳 timestamp 或 None"""
        from datetime import datetime as _dt
        def parse(s):
            s=s.strip()
            if not s or s=="YYYY-MM-DD":
                return None
            for fmt in ("%Y-%m-%d","%Y/%m/%d","%Y.%m.%d"):
                try:
                    return _dt.strptime(s, fmt).timestamp()
                except: pass
            return None
        df = parse(self.date_from_var.get()) if hasattr(self, "date_from_var") else None
        dt = parse(self.date_to_var.get()) if hasattr(self, "date_to_var") else None
        # 若只有日期，to 設為當天 23:59:59
        if dt is not None:
            # parse 已是 00:00，補 86399 秒
            dt += 86399
        return df, dt

    def get_size_range(self):
        """解析大小篩選 MB -> bytes"""
        def parse_mb(s):
            s=s.strip()
            if not s:
                return None
            try:
                return int(float(s) * 1024 * 1024)
            except:
                return None
        sf = parse_mb(self.size_min_var.get()) if hasattr(self, "size_min_var") else None
        st = parse_mb(self.size_max_var.get()) if hasattr(self, "size_max_var") else None
        return sf, st

    def set_date_preset(self, preset):
        from datetime import datetime as _dt, timedelta
        today = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if preset=="today":
            self.date_from_var.set(today.strftime("%Y-%m-%d"))
            self.date_to_var.set(today.strftime("%Y-%m-%d"))
        elif preset=="7d":
            self.date_from_var.set((today - timedelta(days=6)).strftime("%Y-%m-%d"))
            self.date_to_var.set(today.strftime("%Y-%m-%d"))
        elif preset=="30d":
            self.date_from_var.set((today - timedelta(days=29)).strftime("%Y-%m-%d"))
            self.date_to_var.set(today.strftime("%Y-%m-%d"))
        self.do_search()

    def clear_date_filter(self):
        self.date_from_var.set("")
        self.date_to_var.set("")
        self.do_search()

    def set_size_preset(self, preset):
        if preset=="<10":
            self.size_min_var.set("")
            self.size_max_var.set("10")
        elif preset=="10-100":
            self.size_min_var.set("10")
            self.size_max_var.set("100")
        elif preset==">100":
            self.size_min_var.set("100")
            self.size_max_var.set("")
        self.do_search()

    def clear_size_filter(self):
        self.size_min_var.set("")
        self.size_max_var.set("")
        self.do_search()

    def export_csv(self):
        rows = list(self.tree.get_children())
        if not rows:
            messagebox.showinfo("匯出 CSV", "目前沒有資料可匯出")
            return
        path = filedialog.asksaveasfilename(title="匯出 CSV", defaultextension=".csv", filetypes=[("CSV","*.csv")], initialfile="X搜索結果.csv")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["檔名","完整路徑","資料夾","類型","大小","修改日期"])
                for iid in rows:
                    vals = self.tree.item(iid, "values")
                    tags = self.tree.item(iid, "tags")
                    full = tags[0] if tags else ""
                    # vals: name, dir, size_str, mtime_str, ext
                    name = vals[0] if len(vals)>0 else ""
                    dirp = vals[1] if len(vals)>1 else ""
                    size_s = vals[2] if len(vals)>2 else ""
                    mtime_s = vals[3] if len(vals)>3 else ""
                    ext = vals[4] if len(vals)>4 else ""
                    w.writerow([name, full, dirp, ext, size_s, mtime_s])
            self.status_var.set(f"已匯出 {len(rows)} 筆到 {path}")
            try: os.startfile(path)
            except: pass
        except Exception as e:
            messagebox.showerror("匯出失敗", str(e))

    def export_excel(self):
        rows = list(self.tree.get_children())
        if not rows:
            messagebox.showinfo("匯出 Excel", "目前沒有資料可匯出")
            return
        try:
            import openpyxl
        except ImportError:
            if messagebox.askyesno("需要 openpyxl", "尚未安裝 openpyxl，是否改用 CSV 匯出？"):
                self.export_csv()
            return
        path = filedialog.asksaveasfilename(title="匯出 Excel", defaultextension=".xlsx", filetypes=[("Excel","*.xlsx")], initialfile="X搜索結果.xlsx")
        if not path:
            return
        try:
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "搜尋結果"
            ws.append(["檔名","完整路徑","資料夾","類型","大小","修改日期"])
            for iid in rows:
                vals = self.tree.item(iid, "values")
                tags = self.tree.item(iid, "tags")
                full = tags[0] if tags else ""
                name = vals[0] if len(vals)>0 else ""
                dirp = vals[1] if len(vals)>1 else ""
                size_s = vals[2] if len(vals)>2 else ""
                mtime_s = vals[3] if len(vals)>3 else ""
                ext = vals[4] if len(vals)>4 else ""
                ws.append([name, full, dirp, ext, size_s, mtime_s])
            # 自動欄寬
            for col in ws.columns:
                max_len = max(len(str(c.value)) if c.value else 0 for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max_len+2, 50)
            wb.save(path)
            self.status_var.set(f"已匯出 {len(rows)} 筆到 {path}")
            try: os.startfile(path)
            except: pass
        except Exception as e:
            messagebox.showerror("匯出失敗", str(e))

    def get_selected_path(self):
        sel = self.tree.selection()
        if not sel: return None
        # 我們把 path 存在 tags
        tags = self.tree.item(sel[0], "tags")
        return tags[0] if tags else None

    def open_selected(self):
        p = self.get_selected_path()
        if not p: return
        try:
            if sys.platform == "win32":
                os.startfile(p)
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception as e:
            messagebox.showerror("無法開啟", f"{p}\n\n{e}")

    def open_folder(self):
        p = self.get_selected_path()
        if not p: return
        folder = str(Path(p).parent)
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror("無法開啟資料夾", str(e))

    def copy_path(self):
        p = self.get_selected_path()
        if not p: return
        self.clipboard_clear()
        self.clipboard_append(p)
        self.status_var.set(f"已複製: {p}")

    def copy_name(self):
        sel = self.tree.selection()
        if not sel: return
        name = self.tree.item(sel[0], "values")[0]
        self.clipboard_clear()
        self.clipboard_append(name)
        self.status_var.set(f"已複製檔名: {name}")

    def delete_selected(self):
        if self._index_running:
            messagebox.showwarning("索引更新中", "請先停止或完成索引更新。")
            return
        """刪除選取的檔案（含多選），含二次確認並同步更新索引與列表"""
        sels = self.tree.selection()
        paths = []
        for iid in sels:
            tags = self.tree.item(iid, "tags")
            if tags:
                paths.append(tags[0])
        if not paths:
            p = self.get_selected_path()
            if p:
                paths = [p]
        if not paths:
            return
        # 確認對話框
        if len(paths) == 1:
            msg = f"確定要刪除以下檔案嗎？\n\n{paths[0]}\n\n此動作無法復原（網路磁碟不會進資源回收筒）！"
        else:
            preview = "\n".join(paths[:10])
            if len(paths) > 10:
                preview += f"\n...還有 {len(paths)-10} 個"
            msg = f"確定要刪除 {len(paths)} 個檔案嗎？\n\n{preview}\n\n此動作無法復原！"
        if not messagebox.askyesno("確認刪除", msg, icon="warning", default="no"):
            return
        if not messagebox.askyesno("再次確認", "此操作可能永久刪除共用磁碟的原始檔案。確定繼續？", icon="warning", default="no"):
            return
        errors = []
        deleted = []
        for p in paths:
            try:
                pp = Path(p)
                if pp.is_dir():
                    raise OSError("禁止透過搜尋器刪除資料夾")
                elif pp.exists():
                    try:
                        from send2trash import send2trash
                        send2trash(str(pp))
                    except ImportError:
                        pp.unlink()
                    except Exception:
                        if pp.exists():
                            pp.unlink()
                else:
                    pass
                deleted.append(p)
            except Exception as e:
                errors.append(f"{p}: {e}")
        index_error = None
        if deleted:
            try:
                con = sqlite3.connect(self.indexer.db_path)
                try:
                    con.executemany("DELETE FROM files WHERE path=?", [(d,) for d in deleted])
                    con.commit()
                finally:
                    con.close()
            except sqlite3.Error as exc:
                index_error = str(exc)
            for iid in list(sels):
                try:
                    self.tree.delete(iid)
                except Exception:
                    pass
            self._update_drag_paths()
            self.count_var.set(f"剩餘 {len(self.tree.get_children())} 筆")
        if errors:
            messagebox.showerror("部分刪除失敗", "\n".join(errors))
            self.status_var.set(f"已刪除 {len(deleted)} 個，{len(errors)} 個失敗")
        else:
            self.status_var.set(f"已刪除 {len(deleted)} 個檔案")
        if index_error:
            messagebox.showwarning("索引尚未同步", f"檔案操作已結束，索引更新失敗：{index_error}\n請按 F5 更新索引。")

    def show_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            # 若右鍵點在已選取的多選項目上，保留多選；否則單選該列
            if iid not in self.tree.selection():
                self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _update_sort_headings(self):
        for col, label in SORT_LABELS.items():
            arrow = (" ▼" if self.sort_desc else " ▲") if self.sort_col == col else ""
            self.tree.heading(col, text=label + arrow, command=lambda c=col: self.sort_by(c))

    def sort_by(self, col):
        if col not in SORT_COLUMNS:
            return
        self.sort_desc = not self.sort_desc if self.sort_col == col else False
        self.sort_col = col
        self._update_sort_headings()
        self.do_search()

    def _preview_max_height(self):
        # Reserve space for the results table and bottom status area, even in a small window.
        top = self.preview_canvas.winfo_rooty() - self.winfo_rooty()
        return max(self._preview_default_height, self.winfo_height() - top - 205)

    def _set_preview_height(self, height):
        height = max(self._preview_default_height, min(int(height), self._preview_max_height()))
        self.preview_canvas.configure(height=height)

    def _preview_drag_start(self, event):
        self._preview_drag = (event.y_root, self.preview_canvas.winfo_height())
        self.preview_handle.configure(bg="#cbd5e1")
        return "break"

    def _preview_drag_move(self, event):
        if self._preview_drag is not None:
            y, height = self._preview_drag
            self._set_preview_height(height + event.y_root - y)
        return "break"

    def _preview_drag_end(self, event):
        self._preview_drag = None
        self.preview_handle.configure(bg="#e4e7eb")
        self._schedule_resolution_refresh()
        return "break"

    def _preview_reset_size(self, event=None):
        self._preview_drag = None
        self.preview_handle.configure(bg="#e4e7eb")
        self._set_preview_height(self._preview_default_height)
        self._schedule_resolution_refresh()
        return "break"

    def _preview_window_changed(self, event):
        if event.widget is self and hasattr(self, "preview_canvas"):
            requested = int(self.preview_canvas.cget("height"))
            if requested > self._preview_max_height():
                self._set_preview_height(requested)

    def _preview_canvas_changed(self, event=None):
        self._render_preview()
        if self._preview_drag is None:
            self._schedule_resolution_refresh()

    def _desired_preview_edge(self):
        width = max(1, self.preview_canvas.winfo_width() - 8)
        height = max(1, self.preview_canvas.winfo_height() - 8)
        if self._preview_img is not None:
            iw, ih = self._preview_img.size
            scale = min(width / iw, height / ih)
            need = max(iw, ih) * scale
        else:
            need = min(max(width,height), min(width,height)*2)
        return max(256, min(1536, int((need + 255) // 256) * 256))

    def _schedule_resolution_refresh(self):
        if self._preview_resize_after is not None:
            self.after_cancel(self._preview_resize_after)
        self._preview_resize_after = self.after(240, self._refresh_preview_resolution)

    def _refresh_preview_resolution(self):
        self._preview_resize_after = None
        if self._preview_path and self._preview_drag is None:
            edge = self._desired_preview_edge()
            # A higher resolution already loaded is sufficient when shrinking.
            if edge > self._preview_edge:
                self._request_preview(self._preview_path, edge, keep_image=True)

    def _schedule_preview(self, event=None):
        selected = self.tree.selection()
        focus = self.tree.focus()
        iid = focus if focus in selected else (selected[0] if selected else None)
        key = self._row_meta.get(iid)
        if key == self._preview_path:
            return
        self._preview_path = key
        self._preview_img = None
        self._preview_edge = 0
        self._request_preview(key, self._desired_preview_edge() if key else 0)

    def _request_preview(self, key, edge, keep_image=False):
        self._preview_request += 1
        token = self._preview_request
        self._preview_edge = edge
        if self._preview_after is not None:
            self.after_cancel(self._preview_after)
            self._preview_after = None
        if not keep_image:
            self._preview_img = None
            self._preview_text = "讀取縮圖中…" if key else "選取檔案後顯示縮圖"
        self._render_preview()
        if key:
            self._preview_after = self.after(180, lambda: self._queue_preview(token, key, edge))

    def _queue_preview(self, token, key, edge):
        self._preview_after = None
        try:
            self._preview_jobs.get_nowait()
        except queue.Empty:
            pass
        self._preview_jobs.put_nowait((token, key, edge))

    def _preview_worker(self):
        # One worker; rapid selections or resizing never build an unbounded job queue.
        while True:
            token, key, edge = self._preview_jobs.get()
            try:
                cache_key = (key, edge)
                if cache_key in self._preview_cache:
                    image = self._preview_cache.pop(cache_key)
                    self._preview_cache[cache_key] = image
                else:
                    image = load_thumbnail(key[0], edge)
                    if image is not None:
                        self._preview_cache[cache_key] = image
                        while len(self._preview_cache) > 8:
                            self._preview_cache.popitem(last=False)
                self._preview_results.put((token, image, None))
            except Exception:
                self._preview_results.put((token, None, "無法讀取縮圖"))

    def _poll_preview(self):
        try:
            while True:
                token, image, error = self._preview_results.get_nowait()
                if token == self._preview_request:
                    # Keep the smaller valid preview if a higher-resolution provider request fails.
                    if image is not None or self._preview_img is None:
                        self._preview_img = image
                    self._preview_text = error or ("此檔案沒有可用縮圖" if self._preview_img is None else "")
                    self._render_preview()
                    if image is not None:
                        self._schedule_resolution_refresh()
        except queue.Empty:
            pass
        self.after(60, self._poll_preview)

    def _render_preview(self):
        canvas = self.preview_canvas
        width, height = max(canvas.winfo_width(), 1), max(canvas.winfo_height(), 1)
        canvas.delete("all")
        if self._preview_img is not None and width > 8 and height > 8:
            iw, ih = self._preview_img.size
            scale = min((width-8)/iw, (height-8)/ih)
            size = (max(1, round(iw*scale)), max(1, round(ih*scale)))
            image = self._preview_img.resize(size, Image.Resampling.LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(image, master=self)
            canvas.create_image(width//2, height//2, image=self._preview_photo)
        else:
            self._preview_photo = None
            canvas.create_text(width//2, height//2, text=getattr(self, "_preview_text", ""), fill="#6b7280",
                               width=max(width-12, 1), justify="center")


    # ========== 拖曳相關 ==========
    def get_selected_paths(self):
        sels = self.tree.selection()
        paths = []
        for iid in sels:
            tags = self.tree.item(iid, "tags")
            if tags: paths.append(tags[0])
        if not paths:
            p = self.get_selected_path()
            if p: paths = [p]
        return [p for p in paths if Path(p).exists()]

    def on_drag_press(self, event):
        # 欄位標題點擊只用於排序，不能觸發拖曳或開啟舊選取檔案。
        if self.tree.identify_region(event.x, event.y) not in ("cell", "tree"):
            self.drag_paths = []
            return
        self.drag_start_x = event.x_root
        self.drag_start_y = event.y_root
        self.drag_paths = []
        self.after(30, self._update_drag_paths)

    def on_ctrl_click(self, event):
        # Ctrl+點擊：跳格多選（手動接管，因為原生在綁定後失效）
        iid = self.tree.identify_row(event.y)
        if iid:
            if iid in self.tree.selection():
                self.tree.selection_remove(iid)
            else:
                self.tree.selection_add(iid)
            self.drag_start_x = event.x_root
            self.drag_start_y = event.y_root
            self.after(30, self._update_drag_paths)
        return "break"

    def _update_drag_paths(self):
        self.drag_paths = self.get_selected_paths()
        if len(self.drag_paths) > 1:
            self.status_var.set(f"已選 {len(self.drag_paths)} 個檔案，可直接拖到桌面或按 Ctrl+C → Ctrl+V 一次複製")
        elif len(self.drag_paths) == 1:
            self.status_var.set(f"已選 1 個檔案，可按住 Ctrl 再點選多個一起拖曳")

    def on_drag_motion(self, event):
        if not self.drag_paths: return
        dx = abs(event.x_root - self.drag_start_x)
        dy = abs(event.y_root - self.drag_start_y)
        if dx < 8 and dy < 8: return
        # 開始拖曳：把檔案放到剪貼簿的 CF_HDROP，Explorer 就能接收
        paths = self.drag_paths
        # 視覺回饋：改游標
        try: self.tree.configure(cursor="hand2")
        except: pass
        # 放入剪貼簿，讓你拖到桌面後放開就能複製（也支援直接 Ctrl+V 貼上）
        self.copy_files_to_clipboard(paths)
        self.status_var.set(f"拖曳中... 已準備 {len(paths)} 個檔案，拖到桌面或資料夾放開即可複製 (放開後按 Ctrl+V 也能貼上)")

    def on_drag_release(self, event):
        try: self.tree.configure(cursor="")
        except: pass
        # 如果滑鼠已經拖到視窗外，Explorer 會用 CF_HDROP 完成複製，不需額外動作
        # 這裡只做提示
        if self.drag_paths and (abs(event.x_root - self.drag_start_x) > 30 or abs(event.y_root - self.drag_start_y) > 30):
            self.status_var.set(f"已準備 {len(self.drag_paths)} 個檔案到剪貼簿，拖到桌面放開即可完成複製 (或到桌面按 Ctrl+V)")
        self.drag_paths = []

    def copy_files_to_clipboard(self, paths=None):
        if paths is None: paths = self.get_selected_paths()
        if not paths:
            messagebox.showinfo("提示", "請先選取要複製的檔案")
            return
        if not HAS_WIN32:
            # 退化：只複製路徑文字
            self.clipboard_clear()
            self.clipboard_append("\n".join(paths))
            self.status_var.set(f"已複製 {len(paths)} 個路徑到剪貼簿 (需手動複製)")
            return
        try:
            # CF_HDROP 需要 DROPFILES 結構
            import struct
            files = [os.path.abspath(p) for p in paths]
            # 用 pywin32 的方式
            buf = b"\0".join([f.encode("utf-8") + b"\0" for f in files])  # 佔位，下方改用正確編碼
            # 正確做法：用 win32clipboard 直接設定
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            # 構造 DROPFILES - 使用 Unicode (pFiles=20, pt=0,0, fNC=0, fWide=1)
            # 格式：DROPFILES 結構 + 檔案列表(utf-16le 以 \0 分隔 + 雙 \0 結束)
            file_list = "\0".join(files) + "\0\0"
            data = file_list.encode("utf-16le")
            # DROPFILES header 20 bytes
            header = struct.pack("IIIii", 20, 0, 0, 0, 1)  # pFiles, x, y, fNC, fWide
            full = header + data
            win32clipboard.SetClipboardData(win32con.CF_HDROP, full)
            win32clipboard.CloseClipboard()
            self.status_var.set(f"已複製 {len(paths)} 個檔案到剪貼簿，現在到桌面/資料夾按 Ctrl+V 即可複製")
        except Exception as e:
            try: win32clipboard.CloseClipboard()
            except: pass
            # 退化
            self.clipboard_clear()
            self.clipboard_append("\n".join(paths))
            self.status_var.set(f"已複製路徑 (剪貼簿錯誤: {e})")

    def copy_to_desktop(self):
        paths = self.get_selected_paths()
        if not paths: return
        desktop = Path.home() / "Desktop"
        if not desktop.exists():
            desktop = Path.home() / "桌面"
        if not desktop.exists():
            desktop = Path(filedialog.askdirectory(title="選擇桌面位置"))
        for p in paths:
            try:
                shutil.copy2(p, desktop / Path(p).name)
            except Exception as e:
                messagebox.showerror("複製失敗", f"{p}\n{e}")
                return
        self.status_var.set(f"已複製 {len(paths)} 個檔案到桌面: {desktop}")
        # 自動開啟桌面
        try: os.startfile(str(desktop))
        except: pass

    def copy_to_folder(self):
        paths = self.get_selected_paths()
        if not paths: return
        dst = filedialog.askdirectory(title="選擇要複製到的資料夾")
        if not dst: return
        for p in paths:
            try:
                if Path(p).is_dir():
                    shutil.copytree(p, Path(dst) / Path(p).name, dirs_exist_ok=True)
                else:
                    shutil.copy2(p, Path(dst) / Path(p).name)
            except Exception as e:
                messagebox.showerror("複製失敗", f"{p}\n{e}")
                return
        self.status_var.set(f"已複製 {len(paths)} 個檔案到: {dst}")
        try: os.startfile(dst)
        except: pass

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        import tempfile
        import json
        report = Path(sys.argv[2])
        try:
            with tempfile.TemporaryDirectory(prefix="xdrive-test-") as tmp:
                root = Path(tmp) / "files"
                root.mkdir()
                (root / "HDR110_motor.step").write_bytes(b"sample")
                idx = Indexer(Path(tmp) / "test.db")
                assert idx.fts_enabled, "FTS unavailable"
                assert idx.build_index(root)
                assert len(idx.search("HDR110", "3D圖")) == 1
                assert len(idx.search("HD?110")) == 1
                assert HAS_WIN32 and HAS_DND, "clipboard/drag modules unavailable"
                assert tk.Tcl().eval("info patchlevel")
                from openpyxl import Workbook
                assert Workbook().active is not None
                (root / "Part10.step").write_bytes(b"10")
                (root / "Part2.step").write_bytes(b"22")
                assert idx.build_index(root)
                assert [r[0] for r in idx.search("Part",sort_col="name",descending=False)] == ["Part2.step","Part10.step"]
                png = root / "thumb.png"
                Image.new("RGB",(120,80),(255,0,0)).save(png)
                assert load_thumbnail(str(png)).size == (120,80)
                assert shell_thumbnail(str(png)) is not None
                pdf = root / "thumb.pdf"
                Image.new("RGB",(120,80),(0,255,0)).save(pdf)
                small = load_thumbnail(str(pdf),256)
                large = load_thumbnail(str(pdf),1024)
                assert small is not None and large is not None
                assert max(large.size) >= 1000 and max(large.size) > max(small.size)*3
                report.write_text(json.dumps({"status":"PASS","fts":True,"clipboard":HAS_WIN32,"drag":HAS_DND,"sqlite":sqlite3.sqlite_version,"excel":True,"sorting":True,"image_preview":True,"shell_preview":True,"pdf_preview":True,"hires_pdf":True}), encoding="utf-8")
        except Exception as exc:
            report.write_text(json.dumps({"status":"FAIL","error":str(exc)}), encoding="utf-8")
            sys.exit(1)
    else:
        app = App()
        app.mainloop()
