# v0.7.16 — More reliable settings and safer device operations

Desktop **0.7.16** improves everyday settings workflows and protects saved
profiles when files change outside the application. Existing settings remain
compatible. This version is prepared locally; no GitHub Release has been published.

- Saving, switching, renaming and importing profiles keep the selected profile and its settings consistent.
- Invalid values and unsupported mappings are rejected before replacing good settings. External file changes retain their conflict reminders.
- Mapping Layer number controls update the correct stick. Open editors, rename and delete actions keep targeting the intended layer after a folder refresh.
- Repeated editor use cleans up old controls and hover tips. Short mapping names and hover-only full names are retained.
- Connection, calibration, firmware flashing and ESP32 settings writes avoid competing for the same device. Canceling an operation preserves the intended connection restart.
- ESP32 completion messages and automatic reconnection are more reliable. Tester vibration stops when its output connection changes.
- Both user guides describe the updated behavior. Bundled firmware remains **S2P-FW 1.0.7**.

Extract the full Windows ZIP, then open **S2P-XInput-Lite.exe**. Keep the **app** folder beside both EXEs. When upgrading, extract into a new folder and use **Import Settings** to select the old installation.

Local validation: **770 tests passed, 1 optional check skipped**, including five
complete Settings workflows using real window controls and isolated files.
Physical controller connections, calibration, rumble and ESP32 writes have not
been validated on hardware in this preparation. No new GitHub CI run is claimed.

## 繁體中文

v0.7.16 改善日常設定操作，並加強方案檔被外部修改時的保護。既有設定可繼續使用。
目前只在本機準備，尚未建立 GitHub Release。

### 更新重點

- 儲存、切換、重新命名及匯入方案時，保持目前方案與實際設定一致。
- 無效數值與不支援的映射會先被擋下，避免覆蓋正常設定；外部檔案變更仍會提醒衝突。
- 映射層的數值欄位會更新正確的左右搖桿。資料夾重新整理後，編輯、改名與刪除仍會對應原本要操作的映射層。
- 反覆開關編輯視窗時會清理舊控制項與懸浮提示。保留簡短映射名稱，以及游標指到才顯示完整名稱的方式。
- 連線、校正、刷機與 ESP32 設定寫入會避免搶用同一裝置；取消操作後保留原本的重連安排。
- 改善 ESP32 完成提示及自動重連；測試工具的震動會在輸出連線改變時停止。
- 中英文手冊同步更新；隨附韌體維持 **S2P-FW 1.0.7**。

### 使用方式

完整解壓縮 Windows ZIP 後開啟 **S2P-XInput-Lite.exe**，請將 **app** 資料夾與兩個 EXE 一起保留。
升級時請解壓至新資料夾，再用「匯入設定」選擇舊版程式根目錄。

### 驗證狀態

- 本機測試 **770 項通過，1 項選用檢查略過**，包含使用實際視窗控制項及獨立檔案的五條完整設定操作流程。
- 實體手把連線、校正、震動及 ESP32 寫入尚未在本次準備中進行實機驗證。
- 尚未發佈，不宣稱已有新的 GitHub CI 驗證結果。
