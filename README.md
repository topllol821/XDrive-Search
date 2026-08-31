# XDrive-Search — 共用碟 / NAS / 雲端碟 專用秒搜工具

> Everything 搜不到 `X:` / `K:` 這種網路磁碟？這個就是為它做的。

**專為公司共用碟設計** — 以 `X:\` (`\\Ras-c2`) 為例，支援 **SolidWorks + AutoCAD** 圖檔秒搜、分類、拖曳複製。是 [awesome-agent-architecture](https://github.com/hardness1020/awesome-agent-architecture) 學習過程的實戰延伸：把「Harness 工程」用在解決真實痛點。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB) ![Platform](https://img.shields.io/badge/Platform-Windows-0078D6) ![License](https://img.shields.io/badge/License-MIT-green) ![Files](https://img.shields.io/badge/Tested-36,669%20files%20%7C%2026.8GB-blue)

## 為什麼做這個？

`Everything` 快是因為它直接讀硬碟的 MFT 總帳本。這招只對本機 `C:`/`D:` 有效，`X:` / `K:` 這種 **SMB 網路磁碟 / NAS / 雲端碟** 根本不給你 MFT，所以搜不到。

本工具換個思路：**自己抄一份名單**。

1. 第一次把 `X:\` 全掃一遍，存到本地 `SQLite`（`~/.kdrive_search/index.db`）
2. 之後打字只查這份名單 → 幾萬個檔案也是 **輸入即秒出**
3. 檔案有變動按 `F5` / `建立/更新索引` 1-2 分鐘就更新

## 功能特色

- **秒搜檔名**：支援中文、`*` `?` 萬用字元、空白多關鍵字（全部命中）
- **圖檔分類一鍵切**：`全部 / 3D圖 / 2D圖 / Excel / PPT / Word / PDF / 圖片`
  - `3D圖`: `.sldprt` `.sldasm` `.step` `.stp` `.iges` `.stl` `.obj` `.ipt` `.iam` 等 (SolidWorks / Creo / Inventor 優化)
  - `2D圖`: `.dwg` `.dxf` `.dwf` `.pdf` `.ai` 等 (AutoCAD 優化)
- **顯示完整路徑**：檔名、路徑、大小、修改日期、類型
- **拖曳即複製**：選好直接拖到桌面/資料夾，或 `Ctrl+C` → 桌面 `Ctrl+V`（支援多檔）
- **多選**：`Ctrl+點擊` 跳格選、`Shift+點擊` 選一排、`Ctrl+A` 全選
- **右鍵選單**：開啟檔案 / 開啟所在資料夾 / 複製到桌面 / 複製到... / 複製路徑
- **進度可視化**：掃描時藍色進度條 + `⏳ 已收錄 XXX 個檔案`，完成 `叮咚` 提示
- **自動偵測**：自動抓 `X:` / `K:` / `Z:` 等存在的網路碟

## 快速開始

### 直接用（推薦）

1. 到 [Releases](https://github.com/topllol821/XDrive-Search/releases) 下載 `XDriveSearch.exe` (或 `X槽搜尋器.exe`)
2. 放到桌面點兩下即開，不需安裝 Python
3. 上方選擇 `X:\` → 按 `建立/更新索引` → 等 1-3 分鐘（36k 檔約 26.8 GB 實測）
4. 在搜尋框輸入關鍵字，例如 `HDR110`、`支架`、`2310`

### 從原始碼跑

```bash
git clone https://github.com/topllol821/XDrive-Search
cd XDrive-Search
pip install pywin32 tkinterdnd2
python XDriveSearch.py
# 或
python "X槽搜尋器.py"
```

依賴：`Python 3.10+`，`pywin32` (剪貼簿 CF_HDROP)，`tkinterdnd2` (可選)

### 打包成 exe

```bash
pip install pyinstaller pywin32 tkinterdnd2
pyinstaller --onefile --noconsole --name "X槽搜尋器" \
  --hidden-import win32clipboard --hidden-import win32con --hidden-import tkinterdnd2 \
  XDriveSearch.py
# 產物在 dist/X槽搜尋器.exe
```

## 使用方法

| 動作 | 操作 |
|------|------|
| 搜尋 | 直接打字，支援 `HDR110 支架` 多關鍵字、`*.dwg` 萬用字元 |
| 分類 | 點上方 `3D圖` / `2D圖` 等按鈕 |
| 開檔 | 雙擊 或 右鍵→開啟檔案 |
| 開資料夾 | 右鍵→開啟所在資料夾 |
| 複製到桌面 | 右鍵→複製到桌面，或 選取後 `Ctrl+C` → 桌面 `Ctrl+V` |
| 拖曳複製 | 選好檔案直接拖到桌面/資料夾放開 |
| 多選 | `Ctrl+點` 跳選、`Shift+點` 選一排、`Ctrl+A` 全選 |
| 重新整理 | `F5` 或 `建立/更新索引` |

> 索引位置：`C:\Users\你的帳號\.kdrive_search\index.db`，刪掉即重置。

## 踩過的坑 & 解決方式

開發中把 Everything 為何不能搜網路碟、以及 Tkinter 的地雷都踩了一遍，整理如下：

### 坑 1：Everything 為何搜不到 X: / K:？

- **現象**：Everything 對 `X:\` 無結果。
- **原因**：Everything 讀 **NTFS MFT + USN Journal**，只對本機 NTFS 有效。`X:` 是 SMB 掛載，無 MFT。
- **解決**：改為 `os.walk` 全掃 + `SQLite` 索引 + `LIKE '%keyword%'` 搜尋。幾萬檔 `SQLite` 完全扛得住，搜尋 <50ms。

### 坑 2：SQLite WAL 不能在 transaction 內設定

- **現象**：`sqlite3.OperationalError: cannot change into wal mode from within a transaction`
- **原因**：`PRAGMA journal_mode=WAL` 執行時已在 `DELETE` 的 transaction 內。
- **解決**：先 `con.execute("PRAGMA journal_mode=WAL")` 再 `cur = con.cursor(); cur.execute("DELETE")`。把 PRAGMA 移到 `BEGIN` 之前。

### 坑 3：掃描時視窗像當掉

- **現象**：按「建立/更新索引」後左下角只停在 `開始掃描 X:/...`，幾分鐘無回饋，使用者以為當機。
- **原因**：進度只在每 200 個資料夾回報一次，網路碟掃描慢，長時間無訊息。
- **解決**：改為每 30 個資料夾或每 1500 個檔案就 `progress_q.put(...)`，加上 `ttk.Progressbar(indeterminate)` + `⏳` 動畫 + `start(12)/stop()`。

### 坑 4：Tkinter Treeview 的 Ctrl 多選被覆蓋

- **現象**：`Ctrl+點擊` 無法跳格多選，`Shift` 選一排正常。
- **原因**：綁定 `<ButtonPress-1>` 並在 handler 內 `selection_set/remove`，與 Treeview 預設的 `extended` 選取邏輯衝突。`add="+"` 下自家邏輯和預設邏輯都會跑，先清後選導致 Ctrl 失效。
- **解決**：放棄自家邏輯，讓一般點擊走原生；`Ctrl+點擊` 單獨綁 `<Control-ButtonPress-1>` 並 `return "break"` 手動 `selection_add/remove`，`Shift` 也類似處理。最後 `after(30ms)` 再更新 `drag_paths`。

### 坑 5：拖曳到桌面無法真正複製

- **現象**：Tkinter 沒有原生「拖檔案到 Explorer」API，單純 `clipboard_append` 只是文字。
- **原因**：Explorer 拖放需要 `CF_HDROP` 格式的 `DROPFILES` 結構（`fWide=1` 的 UTF-16LE），不是普通文字。
- **解決**：用 `pywin32` 的 `win32clipboard`：
  ```python
  header = struct.pack("IIIii", 20, 0,0,0,1)  # pFiles=20, fWide=1
  data = ("\0".join(files) + "\0\0").encode("utf-16le")
  win32clipboard.SetClipboardData(win32con.CF_HDROP, header+data)
  ```
  放入剪貼簿後，使用者「拖到桌面放開」或「`Ctrl+V`」Explorer 都能識別為檔案複製。另外提供 `shutil.copy2` 的 `複製到桌面/資料夾` 作為兜底。

### 坑 6：PyInstaller 中文檔名亂碼 & 占用

- **現象**：`pyinstaller --name "X槽搜尋器"` 產生的 `X槽搜尋器.exe` 在 PowerShell 顯示 `X�ѷj�M��.exe`，且二次打包時 `PermissionError: [WinError 5]`。
- **原因**：Windows PowerShell OEM codepage 與 UTF-8 不一致；`exe` 仍在執行中被鎖住。
- **解決**：打包前 `Stop-Process -Name "X槽搜尋器"` + `Remove-Item`，打包後用 `-LiteralPath` 的 `Move-Item` 重命名，發佈時同時提供英文名 `XDriveSearch.exe` 避免環境問題。

### 坑 7：網路碟 `stat()` 偶爾失敗 & Thumbs.db 噪音

- **現象**：`fp.stat()` 在網路瞬斷時拋異常；結果混雜大量 `Thumbs.db` `~$` 暫存檔。
- **解決**：`try: stat() except: size=0,mtime=0` 容錯；目前保留 `Thumbs.db` 以便排查，後續可加入黑名單過濾。

## 專案結構

```
XDrive-Search/
├── XDriveSearch.py          # 主程式（英文檔名，推薦）
├── X槽搜尋器.py              # 同內容，中文檔名
├── XDriveSearch.exe         # 打包成品（Release）
├── README.md
├── .gitignore
└── assets/                  # (可放截圖)
```

## 實測數據

- 環境：`X:\` (\\Ras-c2) 網路碟
- 數量：**36,669 個檔案 / 約 26.8 GB**
- 掃描：約 **90-180 秒**（視網路）
- 搜尋：輸入即回，`LIMIT 8000` 內 <50ms

## 致謝

- 靈感：`voidtools Everything`
- 學習框架：[awesome-agent-architecture](https://github.com/hardness1020/awesome-agent-architecture) — 從 Harness 觀念到「索引、進度、權限、拖曳、打包」的組合
- 依賴：`pywin32`, `tkinterdnd2`, `PyInstaller`

## License

MIT — 可自由用於公司內部與二次開發。
