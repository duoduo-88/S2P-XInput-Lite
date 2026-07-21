# S2P-XInput-Lite v0.5.1

v0.5.1 is compared with **v0.5.0**, the previous public release. This version focuses on mapping correctness, profile and Mapping Layer state management, data safety, and connection-time gyro initialization.

## English

### Changes since v0.5.0

- Fixed main-profile mapping targets that could be saved in the GUI but were not executed correctly at runtime, including **Linear Trigger** and **Linear Scroll**.
- Unified mapping-source and mapping-target parsing between the main profile and Mapping Layers so identical targets behave consistently.
- Fixed keyboard mappings that share Ctrl, Shift, Alt, or Windows modifiers. Releasing one mapped key no longer releases a modifier still required by another mapping.
- Added correct Windows extended-key handling and prevented a cancelled custom-key recording window from saving an invalid value.
- Fixed profile switching. When the connection program is running, it now stops automatically, applies the selected profile to the latest on-disk configuration, refreshes the GUI, and reconnects automatically.
- Fixed advanced-rumble values being replaced by unrelated defaults while switching profiles. The selected profile's saved rumble mode and values are now restored correctly.
- Unified default and restore behavior around `src/profiles/System Default.ini` instead of relying on separate hard-coded values.
- Fixed per-profile Mapping Layer state. Each profile now correctly saves and restores the enabled Layer IDs and their priority order.
- Improved Mapping Layer scanning so loading, validation, file management, and completeness checks share one consistent scan result.
- An incomplete Layer scan no longer clears Layer IDs or order stored in `config.ini` or a profile. Missing Layer references are removed only after a complete scan confirms that the files are gone.
- Duplicate Layer IDs now load deterministically from the first matching file and block saving until the conflict is corrected.
- Damaged or unrelated JSON files under `src/layers` are preserved instead of being silently deleted. A damaged S2P Layer no longer prevents the GUI from opening; the error is shown and Save/Apply is disabled until it is corrected.
- Official S2P Mapping Layer JSON now validates the format version, required fields, and basic value types during automatic loading and manual import. Legacy Layer files remain importable.
- Fixed the Mapping Layer editor repeatedly reporting unsaved changes after **Save/Apply**. Saved values are normalized, reloaded from the actual JSON file, and synchronized with the editor baseline and file snapshot.
- Improved profile and Mapping Layer write safety with atomic writes, on-disk change checks, request matching, and rollback handling.
- Fixed validation-dialog and dynamic translation issues, including an undefined validation variable and Chinese messages appearing in English mode.
- Improved gyro connection initialization and status messages. After a connection, gyro output waits for at least **16 stable samples** and zero-bias sampling continues up to **64 samples** without printing per-sample progress messages.

### Updating from v0.5.0

1. Extract v0.5.1 to a new folder instead of overwriting the old installation.
2. To retain personal settings and calibration, copy `src/config.ini` from v0.5.0 before starting v0.5.1.
3. Copy custom files under `src/profiles` and `src/layers` when required.
4. Keep `src/profiles/System Default.ini` in the release folder. It is the baseline used for missing settings and restore operations.
5. The bundled ESP32-S3 protocol and `0.12.4` low-latency firmware are unchanged, so existing v0.5.0 users do not need to flash again.
6. The release package contains the required ESP32 flashing tool and firmware binaries only; the ESP32 source and build directories are not included in the packaged release.

### Known issues and notes

- After every controller connection, place the controller on a stable surface and keep it still for about **0.5 seconds**. Gyro output begins after at least **16 stable samples** are collected, while zero-bias sampling continues up to **64 samples**.
- Gyro zero-bias initialization currently runs again after every reconnection and is not stored as permanent per-controller calibration.
- Mapping Layer files remain global under `src/layers`. Profiles store which Layers are enabled and their priority order, but switching profiles does not delete or replace the Layer files.
- Only the highest-priority matching Mapping Layer is applied. A held Layer has priority over a toggled Layer; otherwise the order shown in the editor determines priority.

---

## 繁體中文

### 自 v0.5.0 起的更新

- 修正主設定檔部分映射目標可在 GUI 儲存、但執行端未正確處理的問題，包含 **線性扳機** 與 **線性滾輪**。
- 統一主設定檔與 Mapping Layers 的映射來源及目標解析方式，確保相同目標在兩條功能路徑中會有一致結果。
- 修正多個鍵盤映射共用 Ctrl、Shift、Alt 或 Windows 修飾鍵時，放開其中一個映射可能提早放開共用修飾鍵的問題。
- 補齊 Windows Extended Key 處理，並避免取消自訂鍵盤錄製視窗後仍儲存無效值。
- 修正設定檔切換流程。連線程式執行中切換設定檔時，現在會自動停止連線、將選定設定檔套用至磁碟上的最新設定、刷新 GUI，並自動重新連線。
- 修正切換設定檔時，進階震動模式與數值可能被其他預設值覆蓋的問題；現在會正確載入所選設定檔保存的震動設定。
- 統一預設值與還原行為，以 `src/profiles/System Default.ini` 作為基準，不再依賴分散的硬寫預設值。
- 修正 Mapping Layer 狀態未完整跟隨設定檔的問題。各設定檔現在會正確保存及恢復啟用的 Layer ID 與優先順序。
- 改善 Mapping Layer 掃描流程，載入、格式驗證、檔案管理及掃描完整性現在共用同一次掃描結果。
- Layer 掃描不完整時，不再清除 `config.ini` 或設定檔內保存的 Layer ID 與排列順序；只有完整掃描確認檔案已消失後，才會清理失效的 Layer 紀錄。
- Layer ID 重複時固定載入第一個符合檔案，並在衝突修正前禁止儲存，避免載入結果不固定。
- `src/layers` 內損壞或非本程式格式的 JSON 不再被靜默刪除。損壞的 S2P Layer 也不會阻止 GUI 啟動；GUI 會顯示錯誤，並在修正前停用儲存／套用。
- 正式 S2P Mapping Layer JSON 在自動載入與手動匯入時，會檢查格式版本、必要欄位及基本型別；舊版 Layer 檔案仍可相容匯入。
- 修正 Mapping Layer 編輯後，即使按下 **儲存／套用**，關閉視窗仍持續顯示未儲存警告的問題。儲存後會統一數值格式、重新讀取實際 JSON，並同步編輯器基準、檔案快照及清單狀態。
- 改善設定檔與 Mapping Layer 的寫入安全，加入原子寫入、磁碟變更檢查、請求核對及失敗回復。
- 修正驗證視窗及動態翻譯問題，包含未定義驗證變數造成的例外，以及英文模式混入中文訊息。
- 改善陀螺儀連線初始化與狀態訊息。每次連線後會先收集至少 **16 個穩定樣本**才開始輸出，零偏取樣會繼續累積至 **64 個樣本**，且不再逐筆輸出進度訊息。

### 從 v0.5.0 更新

1. 將 v0.5.1 解壓縮到新資料夾，不要直接覆蓋舊版。
2. 若要保留個人設定與校正資料，請在第一次啟動 v0.5.1 前複製 v0.5.0 的 `src/config.ini`。
3. 視需要複製 `src/profiles` 與 `src/layers` 內的自訂檔案。
4. 發佈包內必須保留 `src/profiles/System Default.ini`；缺少設定及還原操作都會以此檔案為基準。
5. 隨附的 ESP32-S3 protocol 與 `0.12.4` 低延遲韌體未變更，v0.5.0 使用者無須重新燒錄。
6. 發佈包只保留 ESP32 燒錄工具及韌體 BIN，不包含 ESP32 源碼與編譯目錄。

### 已知問題與注意事項

- 每次控制器連線後，請將控制器平放並保持靜止約 **0.5 秒**。收集至少 **16 個穩定樣本**後才會開始陀螺儀輸出，零偏取樣會繼續累積至 **64 個樣本**。
- 目前每次重新連線都會重新進行陀螺儀零偏初始化，不會保存為永久的每支控制器校正資料。
- Mapping Layer 檔案仍是 `src/layers` 內的全域資源。各設定檔會保存啟用哪些 Layers 及其優先順序，但切換設定檔不會刪除或取代 Layer 檔案。
- 同一時間只會套用優先權最高的符合 Mapping Layer。按住啟用的 Layer 優先於切換啟用的 Layer；其餘情況依編輯器中的排列順序決定。
