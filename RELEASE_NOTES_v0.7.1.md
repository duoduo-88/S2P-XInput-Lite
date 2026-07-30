# S2P-XInput-Lite v0.7.1

Release date: 2026-07-30

This release contains the changes made after v0.7.0.

## English

### Gamepad Tester

- Completes the standalone Gamepad Tester launcher and build target. The
  high-refresh tester can now start independently without first opening the
  settings application.
- Adds the application version before the animated startup-banner text for
  both the settings application and Gamepad Tester.
- Expands input monitoring, vibration patterns, output-shape capture, Raw HID
  report-rate measurement, diagnostics, license information, and third-party
  notices.
- Adds startup support logs for both launchers and exports combined startup
  and controller-diagnostic information in support reports.
- Clarifies the supported data scope: Gamepad Tester is designed primarily
  for a Switch 2 Pro Controller connected through S2P-XInput-Lite. Other
  XInput, WinMM, and Raw HID controllers can run basic tests, but do not
  provide S2P shared telemetry, so mapping, sensor, transport, and ESP32
  information may be unavailable.

### Latency and rumble

- Keeps audio-haptics output at about 16.6 ms while game-originated changes and
  zero frames use the 7.5 ms priority path.
- Preserves pending priority when a newer audio state replaces an unsent game
  or stop state, while the payload and zero state remain latest-only.
- Re-reads the newest wired and ESP32 rumble state after send-lock waits,
  preventing stale output from being sent before a newer stop frame.
- Uses a QPC deadline and an active-window Windows 1 ms timer period for
  XInput tail refresh. A 120-interval Windows run measured P50 15.51 ms,
  P95 16.05 ms, P99 16.41 ms, and 16.55 ms maximum.
- Adds an ESP32 latest-only GATT write state machine with one in-flight write,
  completion/congestion handling, bounded retry, and immediate standalone USB
  rumble wake-up.

### Reliability, diagnostics, and packaging

- Improves ESP32 diagnostics with explicit target selection, firmware
  compatibility guidance, link-quality details, and safer worker shutdown.
- Makes live latency probes measure submission identity, pre-dispatch button
  edges, inline dispatch, and CDC batch boundaries more accurately.
- Adds reproducible runtime, native-helper, firmware, launcher, packaging, and
  release-verification scripts, plus continuous-integration coverage.
- Separates runtime command and status handling into tested components and
  tightens dependency pinning for portable builds.
- Refreshes the English and Traditional Chinese manuals and keeps the upstream
  ESP32-S3 `0.12.4` firmware as a reference and rollback option.

### Validation

- The automated suite runs 489 tests successfully; one optional full ESP-IDF
  test is skipped in the normal run.
- The ESP32 firmware was also built with ESP-IDF 5.5.4 and verified against
  the bundled release image.

### Updating

1. Extract v0.7.1 into a new folder instead of overwriting a running
   installation.
2. Copy the previous `src/config.ini` only if you want to retain personal
   settings.
3. ESP32-S3 users should flash the bundled firmware to receive the GATT
   scheduling and standalone-rumble improvements.

## 繁體中文

### 手把測試

- 完成獨立手把測試啟動器與建置目標，不必先開啟設定程式即可單獨啟動高更新率
  測試工具。
- 設定程式與手把測試的動態啟動橫幅，會在「啟動中」前顯示目前應用程式版本。
- 擴充輸入監看、震動樣式、輸出形狀、Raw HID 回報率、診斷、授權及第三方
  程式資訊。
- 主程式與測試器皆新增啟動支援紀錄；支援報告可整合啟動資訊與手把診斷。
- 明確說明資料適用範圍：手把測試主要針對由 S2P-XInput-Lite 連線的
  Switch 2 Pro Controller。其他 XInput、WinMM 及 Raw HID 手把仍可執行
  基本測試，但沒有 S2P 共享遙測，因此映射、感測器、傳輸及 ESP32 等資訊
  可能缺少。

### 延遲與震動

- 音訊震動維持約 16.6 ms；遊戲震動變化與歸零訊框使用 7.5 ms 優先路徑。
- 新音訊狀態覆蓋尚未送出的遊戲／停止狀態時，會保留 pending priority；
  實際內容與歸零狀態則仍以最新提交為準。
- 有線與 ESP32 在等待傳送鎖後會重新讀取最新震動狀態，避免新的停止訊框前
  仍送出過時震動。
- XInput 尾韻更新使用 QPC deadline，並只在連續震動期間開啟 Windows
  1 ms 計時解析度。Windows 120 次間隔實測為 P50 15.51 ms、
  P95 16.05 ms、P99 16.41 ms，最大 16.55 ms。
- ESP32 新增 latest-only GATT 寫入狀態機：同時只保留一筆 in-flight，
  處理完成／壅塞事件、有限次數重試，並可立即喚醒獨立 USB 震動輸出。

### 穩定性、診斷與封裝

- 改善 ESP32 診斷目標選擇、韌體相容性提示、連線品質資訊及背景工作安全
  關閉流程。
- 修正即時延遲量測工具的 submission identity、dispatcher 前按鍵邊緣、
  inline dispatch 與 CDC batch 邊界統計。
- 新增可重現的 runtime、原生工具、韌體、啟動器、封裝及發佈驗證腳本，
  並加入 CI。
- 將執行期命令與狀態發佈拆成可獨立測試的元件，並強化可攜版依賴鎖定。
- 更新中英文使用手冊，並保留上游 ESP32-S3 `0.12.4` 韌體供參考或回復。

### 驗證

- 自動化測試共執行 489 項並全部成功；一般測試會略過一項選用的完整
  ESP-IDF 建置測試。
- 另以 ESP-IDF 5.5.4 完整建置 ESP32 韌體，並確認輸出與隨附映像一致。

### 更新方式

1. 將 v0.7.1 解壓縮到新資料夾，不要覆寫正在執行的安裝。
2. 如需保留個人設定，再複製舊版的 `src/config.ini`。
3. ESP32-S3 使用者請刷入隨附韌體，才能使用新的 GATT 排程及獨立震動改善。
