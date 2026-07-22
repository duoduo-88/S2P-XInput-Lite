# S2P-XInput-Lite v0.5.2

v0.5.2 is compared with **v0.5.1**. This release focuses on rumble tuning, advanced audio haptics, parameter editing, window restoration, validation, and UI consistency.

## English

### Changes since v0.5.1

- Retuned all bundled rumble profiles around a maximum amplitude of `800`, with safer LF/HF frequencies, strengths, curves, and cross-channel compensation.
- Preserved the established v0.5.1 two-pulse connection and PIN feedback cue consistently across USB, ESP32, and native BLE transports.
- Expanded advanced audio haptics from five to six bands by splitting `2000–8000 Hz` into `2000–4000 Hz` and `4000–8000 Hz` bands.
- Added an LF/HF routing-balance control and revised the six-band routing weights to reduce excessive HF output and audible high-frequency artifacts in Audio and Mix modes.
- Added a six-band graph with Low, L-Mid, Mid, H-Mid, High, and Ultra labels, full hover help, drag editing, and per-band double-click reset.
- Added single-click numeric entry for displayed slider values. Dragging the value text adjusts it by the parameter step; discrete time controls follow their non-linear value lists.
- Added two right-click restore actions for parameter controls: restore the last saved value or restore the System Default value.
- Added right-click coordinate entry for stick-curve control points while preserving double-click reset.
- Refreshed stick-curve visuals with separate blue/red left/right styling, clearer coordinate labels, outlined text, and deadzone colors consistent with stick-direction mapping.
- Reworked parameter entry windows to use a fixed, compact width with wrapped help text.
- Improved minimize/restore handling so the window remains hidden until layout and composition are ready, preventing restore-time black frames without changing the saved window position.
- Centralized setting ranges and cross-field validation. Non-finite values such as `NaN` and `Infinity` are rejected, stick deadzones are limited to `0.99`, and center plus outer deadzone must remain below `1.00`.
- Corrected missing right-click menus on controls that start disabled and become active later.
- Fixed global reset so “Restore Last Saved” continues to reference the actual saved profile instead of the temporary System Default state.
- Corrected Traditional Chinese and English labels, help text, punctuation, parameter recommendations, firmware instructions, and protocol documentation.
- Added regression coverage for profiles, rumble frames, parameter editing, localization, window restoration, mappings, audio-band routing, and configuration persistence.

### Updating from v0.5.1

1. Extract v0.5.2 to a new folder instead of overwriting the old installation.
2. To retain personal settings and calibration, copy `src/config.ini` from v0.5.1 before starting v0.5.2.
3. Copy custom files under `src/profiles` and `src/layers` when required.
4. Keep `src/profiles/System Default.ini`; it is the canonical baseline for missing settings and restore operations.
5. The bundled ESP32-S3 `0.12.4` firmware is unchanged, so users already running that firmware do not need to flash again.
6. The release ZIP contains only the ESP32 flashing tool and required firmware binaries; ESP32 source, build output, and upstream backup files are intentionally excluded.

### Notes

- Maximum amplitude `800` is the bundled-profile ceiling, not a requirement. Reduce it to approximately `650–750` if a particular controller produces occasional mechanical impact noise.
- Frequency commands use nine valid bits (`0–511`) in ten-bit protocol slots. Their numerical values can be interpreted approximately in Hz, but the physical response depends on the controller and enclosure.
- Hardware-specific USB, BLE, ESP32, audio-loopback, and long-duration tests still require the corresponding physical device and Windows environment.

---

## 繁體中文

### 自 v0.5.1 起的更新

- 以最大振幅 `800` 為基準重新調整所有內建方案的 LF／HF 頻率、強度、曲線與交叉補償。
- USB、ESP32 與原生 BLE 統一保留 v0.5.1 的兩段式連線及 PIN 提示震動。
- 進階音訊震動由五頻段擴充為六頻段，將原本的 `2000～8000 Hz` 拆分為 `2000～4000 Hz` 與 `4000～8000 Hz`。
- 新增 LF／HF 分配重心，並調整六頻段路由權重，降低音訊與混合模式過量輸出 HF 所造成的高頻聲音。
- 新增六頻段圖表、Low／L-Mid／Mid／H-Mid／High／Ultra 標籤、完整游標提示、拖曳調整及雙擊還原單一頻段。
- 拉桿顯示數值可單擊開啟參數輸入視窗；拖曳數值文字可依步進增減，非線性時間參數會按照指定數值序列移動。
- 參數控制加入兩個右鍵選項：還原上次儲存數值，以及還原系統預設。
- 搖桿曲線控制點新增右鍵輸入 X／Y 座標，並保留雙擊還原功能。
- 更新搖桿曲線外觀：左右分別使用藍色／紅色、加強座標文字外框，並讓死區顏色與方向映射一致。
- 參數輸入視窗改為固定且較緊湊的寬度，較長說明會自動換行。
- 改善視窗最小化及還原流程；完成版面與合成準備前保持隱藏，避免還原時黑畫面，同時保留原本視窗位置。
- 集中管理參數範圍與交叉驗證；拒絕 `NaN`、`Infinity` 等異常數字，搖桿死區單項上限為 `0.99`，中心與外圍死區總和必須小於 `1.00`。
- 修正部分控制項啟動時為停用狀態，之後啟用仍缺少右鍵還原選單的問題。
- 修正全域還原後，「還原上次儲存」錯誤指向暫時載入的系統預設值。
- 修正繁體中文與英文標籤、提示、標點、參數建議、刷機說明及協議文件。
- 補充設定檔、震動封包、參數輸入、語言、視窗還原、映射、音訊頻段與設定持久化的回歸測試。

### 從 v0.5.1 更新

1. 將 v0.5.2 解壓縮到新資料夾，不要直接覆蓋舊版。
2. 若要保留個人設定與校正資料，請在第一次啟動 v0.5.2 前複製 v0.5.1 的 `src/config.ini`。
3. 視需要複製 `src/profiles` 與 `src/layers` 內的自訂檔案。
4. 必須保留 `src/profiles/System Default.ini`；缺少設定與還原操作都以此檔案為唯一基準。
5. 隨附的 ESP32-S3 `0.12.4` 韌體沒有變更；已使用此版本的使用者無須重新刷寫。
6. 發佈 ZIP 只包含 ESP32 刷機工具與必要韌體 BIN；ESP32 原始碼、編譯輸出與 upstream 備份不放入發佈包。

### 注意事項

- `800` 是內建方案使用的最大振幅上限，不是強制值；若個別控制器偶爾出現機械撞擊聲，可降至約 `650～750`。
- 頻率命令在 10-bit 協議欄位中使用 9 個有效位元，範圍為 `0～511`；數值可近似以 Hz 理解，但實際反應仍受控制器與外殼影響。
- USB、BLE、ESP32、音訊擷取與長時間穩定性仍需在對應 Windows 環境及實體硬體上測試。
