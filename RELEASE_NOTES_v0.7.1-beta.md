# S2P-XInput-Lite v0.7.1 beta

Development build date: 2026-07-29

> This is a beta build from the `development` branch. Keep a copy of your
> existing installation and settings when testing it.

## English

### Highlights

- Identifies the desktop application and GamepadTester as `v0.7.1 beta`.
- Completes the standalone GamepadTester launcher and build target so the
  high-refresh tester can start independently from the settings application.
- Adds startup support logs for both launchers and lets the tester export a
  combined support report with startup and controller diagnostics.
- Improves ESP32 diagnostics with explicit target selection, firmware
  compatibility guidance, link-quality details, and safer worker shutdown.
- Expands GamepadTester coverage for input monitoring, vibration patterns,
  Raw HID report-rate measurement, diagnostics, license information, and
  third-party notices.
- Refreshes the English and Traditional Chinese user-guide figures with
  annotated screenshots for every GamepadTester page.
- Includes the upstream ESP32-S3 `0.12.4` firmware binaries as a reference and
  rollback option alongside the S2P firmware.

### Testing

- The automated suite passes 461 tests.
- One optional full ESP-IDF build test remains skipped unless
  `S2P_RUN_IDF_BUILD=1` is enabled.

### Updating

1. Extract the beta build into a new folder instead of overwriting an active
   installation.
2. Copy the previous `src/config.ini` only if you want to retain personal
   settings.
3. Keep the previous release available for rollback while evaluating the beta.

## 繁體中文

### 更新重點

- 桌面主程式與 GamepadTester 的版本識別更新為 `v0.7.1 beta`。
- 完成獨立 GamepadTester 啟動器與建置目標，高更新率測試工具可與設定程式
  分開啟動。
- 主程式與測試器皆新增啟動支援紀錄；測試器可匯出整合啟動資訊與手把診斷的
  支援報告。
- 改善 ESP32 診斷的目標選擇、韌體相容性提示、連線品質資訊與背景工作安全
  關閉流程。
- 完整涵蓋輸入監看、震動樣式、Raw HID 回報率、診斷、授權與第三方軟體頁。
- 中英文使用手冊補齊所有 GamepadTester 頁面的編號截圖與圖說。
- 附上游 ESP32-S3 `0.12.4` 韌體檔，供參考或回復使用，並保留 S2P 韌體。

### 測試

- 自動化測試共 461 項通過。
- 完整 ESP-IDF 建置測試為選用項目；需設定 `S2P_RUN_IDF_BUILD=1` 才會執行。

### 更新方式

1. 將 beta 版解壓縮到新資料夾，不要覆蓋正在使用中的安裝。
2. 若要保留個人設定，再複製舊版的 `src/config.ini`。
3. 評估 beta 期間請保留上一個版本，以便需要時回復。
