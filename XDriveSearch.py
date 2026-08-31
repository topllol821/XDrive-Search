#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K槽專用搜尋器 - 給共用碟/NAS/雲端碟用的 Everything 替代版
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
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import queue
import fnmatch
import shutil
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
APP_NAME = "K槽搜尋器"
DB_DIR = Path.home() / ".kdrive_search"
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
DEFAULT_ROOT = _detect_default_root()
try:
    if not Path(DEFAULT_ROOT).exists():
        DEFAULT_ROOT = str(Path.home())
except: 
    DEFAULT_ROOT = "X:\\"

# 分類定義 - 依你 SolidWorks + AutoCAD 優化
CATEGORIES = {
    "全部": None,
    "3D圖": {".sldprt",".sldasm",".sldprtdot",".sldasmdot",".prt",".asm",".step",".stp",".iges",".igs",".x_t",".x_b",".stl",".obj",".fbx",".3dm",".skp",".max",".ipt",".iam",".catpart",".catproduct"},
    "2D圖": {".dwg",".dxf",".dwf",".dwt",".pdf",".ai",".psd",".cdr",".svg"}, # dwg/dxf 同時算 2D，也會在 3D 圖中出現是正常的
    "Excel": {".xls",".xlsx",".xlsm",".csv"},
    "PPT": {".ppt",".pptx",".pptm"},
    "Word": {".doc",".docx",".docm"},
    "PDF": {".pdf"},
    "圖片": {".jpg",".jpeg",".png",".bmp",".tif",".tiff",".gif",".webp"},
}

for k,v in CATEGORIES.items():
    if v: CATEGORIES[k] = {x.lower() for x in v}



class Indexer:
    def __init__(self, db_path=DB_PATH):
        self.db_path = Path(db_path)
        self._stop = threading.Event()
        self.progress_q = queue.Queue()
        self.init_db()

    def init_db(self):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS files(
                id INTEGER PRIMARY KEY,
                name TEXT, name_lower TEXT,
                path TEXT, dir TEXT,
                ext TEXT, size INTEGER, mtime REAL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_name_lower ON files(name_lower)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ext ON files(ext)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_dir ON files(dir)")
        # FTS5 for fast keyword search
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                name, path, content='files', content_rowid='id', tokenize='trigram'
            )
        """)
        # triggers
        cur.execute("CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN INSERT INTO files_fts(rowid,name,path) VALUES (new.id,new.name,new.path); END")
        cur.execute("CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN INSERT INTO files_fts(files_fts,rowid,name,path) VALUES('delete',old.id,old.name,old.path); END")
        cur.execute("CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN INSERT INTO files_fts(files_fts,rowid,name,path) VALUES('delete',old.id,old.name,old.path); INSERT INTO files_fts(rowid,name,path) VALUES (new.id,new.name,new.path); END")
        con.commit()
        con.close()

    def build_index(self, root_path, on_done=None):
        """背景執行緒掃描"""
        self._stop.clear()
        root = Path(root_path)
        if not root.exists():
            self.progress_q.put(("error", f"路徑不存在: {root}"))
            if on_done: on_done(0)
            return
        start = time.time()
        con = sqlite3.connect(self.db_path)
        # WAL 必須在 transaction 外設定
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=OFF")
        except: pass
        cur = con.cursor()
        cur.execute("DELETE FROM files")
        count = 0
        batch = []
        BATCH_SIZE = 2000
        scanned_dirs = 0
        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=lambda e: None):
                if self._stop.is_set():
                    break
                # 跳過隱藏/系統資料夾加速（可選）
                # dirnames[:] = [d for d in dirnames if not d.startswith('.') and d not in ['$RECYCLE.BIN','System Volume Information']]
                scanned_dirs += 1
                # 每 30 個資料夾或每 2000 檔就回報一次，讓進度條會動
                if scanned_dirs % 30 == 0 or count % 1500 == 0:
                    self.progress_q.put(("progress", f"⏳ 掃描中... {dirpath} | 已收錄 {count} 個檔案 | {count//1000}k"))
                # 每批次提交後也提示
                if count % 2000 == 0 and count>0:
                    self.progress_q.put(("progress", f"⏳ 掃描中... 已收錄 {count} 個檔案，請稍候..."))
                for fn in filenames:
                    if self._stop.is_set():
                        break
                    try:
                        fp = Path(dirpath) / fn
                        ext = fp.suffix.lower()
                        # 用 lstat 避免跟隨捷徑
                        try:
                            st = fp.stat()
                            size = st.st_size
                            mtime = st.st_mtime
                        except:
                            size = 0
                            mtime = 0
                        batch.append((fn, fn.lower(), str(fp), dirpath, ext, size, mtime))
                        count += 1
                        if len(batch) >= BATCH_SIZE:
                            cur.executemany("INSERT INTO files(name,name_lower,path,dir,ext,size,mtime) VALUES (?,?,?,?,?,?,?)", batch)
                            batch.clear()
                    except Exception:
                        continue
            if batch:
                cur.executemany("INSERT INTO files(name,name_lower,path,dir,ext,size,mtime) VALUES (?,?,?,?,?,?,?)", batch)
            con.commit()
            # 重建 FTS
            cur.execute("INSERT INTO files_fts(files_fts) VALUES('rebuild')")
            con.commit()
        finally:
            con.close()
        elapsed = time.time() - start
        self.progress_q.put(("done", f"完成！共 {count} 個檔案，耗時 {elapsed:.1f} 秒 | 已建立索引"))
        if on_done:
            on_done(count)

    def stop(self):
        self._stop.set()

    def search(self, keyword, category="全部", limit=5000):
        con = sqlite3.connect(self.db_path)
        cur = con.cursor()
        keyword = (keyword or "").strip()
        ext_filter = CATEGORIES.get(category)
        # 組 SQL
        where = []
        params = []
        if keyword:
            # 支援空白多關鍵字：全部都要命中
            terms = keyword.lower().split()
            for t in terms:
                # 支援 * ? 萬用字元轉 LIKE
                if "*" in t or "?" in t:
                    pat = t.replace("%","\\%").replace("_","\\_").replace("*","%").replace("?","_")
                    where.append("name_lower LIKE ? ESCAPE '\\'")
                    params.append(f"%{pat}%")
                else:
                    where.append("name_lower LIKE ?")
                    params.append(f"%{t}%")
        if ext_filter is not None:
            placeholders = ",".join("?" for _ in ext_filter)
            where.append(f"ext IN ({placeholders})")
            params.extend(list(ext_filter))
        sql = "SELECT name, path, dir, ext, size, mtime FROM files"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY mtime DESC LIMIT ?"
        params.append(limit)
        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
        except sqlite3.OperationalError as e:
            rows = []
        con.close()
        return rows

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
        self.title(f"{APP_NAME} — 共用碟秒搜版 (幾萬檔專用) | 支援拖曳到桌面")
        self.geometry("1100x680")
        self.minsize(900, 520)
        # 讓視窗在工作列有圖示
        try: self.iconbitmap("")
        except: pass
        self.indexer = Indexer()
        self.current_category = "全部"
        self.search_after_id = None
        self.root_path_var = tk.StringVar(value=DEFAULT_ROOT)
        self.keyword_var = tk.StringVar()
        self.status_var = tk.StringVar(value="準備就緒")
        self.create_ui()
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
        ttk.Label(top, text="搜尋路徑:").pack(side=tk.LEFT)
        ent_path = ttk.Entry(top, textvariable=self.root_path_var, width=28)
        ent_path.pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="瀏覽...", command=self.browse).pack(side=tk.LEFT)
        ttk.Button(top, text="建立 / 更新索引", command=self.start_index).pack(side=tk.LEFT, padx=8)
        ttk.Button(top, text="停止", command=self.indexer.stop).pack(side=tk.LEFT)
        # 搜尋列
        search_frame = ttk.Frame(self, padding=(10,0,10,8))
        search_frame.pack(fill=tk.X)
        ttk.Label(search_frame, text="🔍", font=("Segoe UI Emoji", 14)).pack(side=tk.LEFT)
        self.entry_search = ttk.Entry(search_frame, textvariable=self.keyword_var, font=("Microsoft JhengHei", 12))
        self.entry_search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self.entry_search.focus_set()
        ttk.Button(search_frame, text="清除", command=lambda: (self.keyword_var.set(""), self.do_search())).pack(side=tk.LEFT)
        # 分類按鈕
        cat_frame = ttk.Frame(self, padding=(10,0,10,8))
        cat_frame.pack(fill=tk.X)
        ttk.Label(cat_frame, text="分類:").pack(side=tk.LEFT)
        self.cat_buttons = {}
        for cat in ["全部","3D圖","2D圖","Excel","PPT","Word","PDF","圖片"]:
            b = ttk.Button(cat_frame, text=cat, width=7, command=lambda c=cat: self.set_category(c))
            b.pack(side=tk.LEFT, padx=3)
            self.cat_buttons[cat] = b
        self.highlight_cat()
        # 結果表格
        mid = ttk.Frame(self, padding=(10,0,10,0))
        mid.pack(fill=tk.BOTH, expand=True)
        cols = ("name","dir","size","mtime","ext")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("name", text="檔名 ▼", command=lambda: self.sort_by("name"))
        self.tree.heading("dir", text="路徑")
        self.tree.heading("size", text="大小")
        self.tree.heading("mtime", text="修改日期")
        self.tree.heading("ext", text="類型")
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
        s.configure("Accent.TButton", background="#2563eb", foreground="white")

    def bind_events(self):
        self.keyword_var.trace_add("write", lambda *_: self.schedule_search())
        self.tree.bind("<Double-1>", lambda e: self.open_selected())
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

    def schedule_search(self):
        if self.search_after_id:
            self.after_cancel(self.search_after_id)
        self.search_after_id = self.after(180, self.do_search)

    def do_search(self):
        kw = self.keyword_var.get()
        rows = self.indexer.search(kw, self.current_category, limit=8000)
        self.tree.delete(*self.tree.get_children())
        for name, path, dirpath, ext, size, mtime in rows:
            try:
                size_str = self.human_size(size)
                mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else ""
            except:
                size_str, mtime_str = "", ""
            self.tree.insert("", tk.END, values=(name, dirpath, size_str, mtime_str, ext), tags=(path,))
        self.count_var.set(f"顯示 {len(rows)} 筆" + (f" / 關鍵字: {kw}" if kw else ""))
        if not rows and kw:
            self.status_var.set(f"沒有找到「{kw}」在「{self.current_category}」中，試試切到「全部」或減少關鍵字")

    def human_size(self, n):
        if n is None: return ""
        for unit in ["B","KB","MB","GB"]:
            if abs(n) < 1024: return f"{n:.1f} {unit}" if unit!="B" else f"{n} B"
            n/=1024
        return f"{n:.1f} TB"

    def start_index(self):
        root = self.root_path_var.get().strip().replace("/", "\\")
        self.root_path_var.set(root)
        if not Path(root).exists():
            messagebox.showerror("路徑錯誤", f"找不到路徑:\n{root}\n\n請確認 X: / K: 已連線，或按「瀏覽」重選。")
            return
        if messagebox.askyesno("建立索引", f"即將掃描:\n{root}\n\n幾萬個檔案約 1-3 分鐘完成，期間可正常使用舊索引。\n開始嗎？"):
            self.status_var.set(f"⏳ 開始掃描 {root} ... 請稍候，正在讀取檔案清單")
            self.dot_var.set("⏳")
            self.dot_label.configure(foreground="#f59e0b")
            try: self.progress.start(12)
            except: pass
            self.update_idletasks()
            th = threading.Thread(target=self.indexer.build_index, args=(root, lambda c: self.after(0, self.on_index_done)), daemon=True)
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
            self.status_var.set(f"索引就緒：{n} 個檔案 | 總大小約 {self.human_size(s)} | 按 F5 重新整理")
        self.check_progress()

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
                    try: self.progress.stop()
                    except: pass
                    self.dot_var.set("●")
                    self.dot_label.configure(foreground="#16a34a")
                    self.update_stats()
                    self.do_search()
                if typ == "error":
                    try: self.progress.stop()
                    except: pass
                    self.dot_var.set("●")
        except queue.Empty:
            pass
        self.after(400, self.check_progress)

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

    def show_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self.tree.selection_set(iid)
            self.menu.tk_popup(event.x_root, event.y_root)

    def sort_by(self, col):
        # 簡單排序：按檔名
        pass

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
        # 一般點擊（不含 Ctrl）讓 Treeview 原生處理，這裡只記錄拖曳起點
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
    app = App()
    app.mainloop()
