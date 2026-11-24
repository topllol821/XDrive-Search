# XDrive-Search｜X搜索器 v1.3

Windows 共用磁碟、SMB / NAS 與工程圖檔的本機索引搜尋工具。掃描檔名及基本檔案屬性，將索引儲存在本機 SQLite，不讀取檔案全文。

## 下載與使用

- [下載正式 Windows EXE](https://github.com/topllol821/XDrive-Search/raw/refs/heads/main/XDriveSearch.exe)；中文名稱的 EXE 為同一份執行檔。
- 關閉舊版後開啟新版。選擇網路磁碟或資料夾，首次建立索引，之後按 F5 增量更新。
- 舊版索引可遷移；若沒有已記錄的根目錄，第一次更新會完整重建。
- 本機索引位於使用者家目錄下的 .kdrive_search/index.db。不要把這個資料庫或任何私有檔案上傳至公開 Repository。

## 功能

- 多關鍵字、中文、萬用字元、工程圖檔分類、日期及大小篩選。
- 五欄標題點擊排序：檔名、路徑、大小、修改日期、類型；再次點擊反轉方向。數字及日期以原始值排序，檔名使用自然數字排序。最多顯示排序後的 8,000 筆結果。
- 單擊結果顯示預覽，底部拖曳線可上下調整預覽高度；雙擊恢復預設高度。
- 一般圖片與 PDF 首頁可預覽，PDF 放大時以更高解析度重新渲染；SolidWorks 零件、組合件、工程圖及 DWG 可利用已安裝的 Windows 縮圖提供者。STEP 等格式若沒有可用提供者則顯示提示。
- 多選、開啟所在資料夾、剪貼簿、拖曳及 CSV/Excel 匯出。
- 掃描中斷或無法讀取目錄時回退索引交易，保留原索引；避免重複掃描。切換根目錄成功後才替換原索引。
- 刪除檔案需要兩次確認且預設取消；網路磁碟可能永久刪除，請審慎使用。

## 開發與建置

正式主程式：XDriveSearch.py。X槽搜尋器.py 是相容副本，XDriveSearch_v2.py 是舊名稱相容入口。
正式掃描器為主程式內的 Indexer.build_index，採 os.scandir 與 SQLite 原子交易。舊 fast_indexer.py 及其測試保留供歷史比較，不是 v1.3 的執行路徑。

已驗證環境：Windows 11、Python 3.14、SQLite FTS5 Trigram。

    py -3.14 -m pip install -r requirements.txt
    py -3.14 XDriveSearch.py

打包（隔離虛擬環境）：

    powershell -ExecutionPolicy Bypass -File .\build.ps1

輸出為 dist/XDriveSearch.exe。可用 --self-test <報告路徑> 執行 EXE 自檢；測試時請將 XDRIVE_SEARCH_DB_DIR 與 XDRIVE_SEARCH_ROOT 設為隔離目錄，不使用正式索引。

## 版本紀錄

- **v1.3（2026-11-24）**：可上下拖曳調整預覽區，雙擊復原，依視窗尺寸提升 PDF 預覽解析度，保留結果列表可操作空間。
- v1.2：五欄排序、非同步縮圖、PDF 首頁預覽。
- v1.1：取消/中斷交易回退、FTS5/LIKE 混合搜尋、索引一致性及刪除安全修正。

本版以實際驗收的 Windows EXE 為準。EXE SHA-256：

    B10AF1E866C4CC8D4AA7BD34732F33876D7B3605F4B130099ABC5B0EC073B754

MIT License。
