# S2P-XInput-Lite v0.7.5

## English

v0.7.5 improves battery accuracy and adds a shared four-level battery display
to the application, controller player LEDs, and ESP32 standalone mode. The
bundled firmware is upgraded to S2P-FW 1.0.2.

- Replaces the old coarse voltage thresholds with an empirical discharge curve
  measured for about 24.6 hours under continuous Audio Haptics load, from a
  full reading near 3.687 V to controller cutoff near 2.589 V.
- Reports battery percentage in stable 5% steps and shows four UI levels: low,
  medium-low, medium-high, and high.
- Uses the four controller player LEDs as a cumulative battery bar across wired
  USB, ESP32 bridge, and native Windows BLE connections.
- Adds the same battery LED bar to ESP32 standalone XInput and mobile USB HID
  modes, so it remains available without the Windows application running.
- Adds 15 mV firmware hysteresis to prevent rumble load from making the battery
  LEDs flicker around a level boundary.
- Preserves the raw power-status and current fields for diagnostics and reports
  the observed external-power state in the UI.
- Removes periodic automatic battery telemetry from normal runtime; the manual
  live probe remains available for controlled diagnostics.
- Fixes ESP32 pairing and initialization state machines so a command rejected
  by a busy BLE stack is retried instead of being treated as sent.
- Waits for controller acknowledgements before considering player-LED updates
  complete, prevents overlapping native BLE LED commands, and retries failures.
- Updates firmware source, bundled binaries, CI identity checks, documentation,
  and release hashes for S2P-FW 1.0.2.
- Passes a clean ESP-IDF 5.5.4 build and all 537 automated regression tests.

The measured desktop battery curve was validated with a real controller. The
new standalone LED behavior passed build and automated contract validation;
post-flash controller verification is still recommended for each ESP32 board.

## 繁體中文

v0.7.5 改善電量準確度，並在程式介面、控制器玩家燈及 ESP32 獨立模式加入
共用的四級電量顯示。隨附韌體升級為 S2P-FW 1.0.2。

- 以約 24.6 小時連續音訊震動實測放電曲線取代原本粗略的電壓門檻；滿電約
  3.687 V，控制器關機前約 2.589 V。
- 電量百分比採穩定的 5% 級距，介面分成低、中低、中高及高四級。
- USB 有線、ESP32 橋接及 Windows 原生 BLE 都會把控制器四顆玩家燈當成
  累進式電量條。
- ESP32 的 PC XInput 與手機 USB HID 獨立模式也支援相同燈號，不需要持續
  開啟 Windows 程式。
- 韌體加入 15 mV 遲滯，避免震動負載造成電壓短暫下降時燈號反覆跳動。
- 保留原始供電狀態及電流欄位供診斷使用，並在介面呈現實測的外部供電狀態。
- 正常執行時不再定期自動記錄電量遙測；受控診斷仍可使用手動實測工具。
- 修正 ESP32 配對與初始化狀態機：BLE 堆疊忙碌而拒絕命令時會重新嘗試，
  不會誤判為已送出。
- 玩家燈命令必須收到控制器 ACK 才視為完成；原生 BLE 也會避免命令重疊並
  在失敗後重試。
- 更新 S2P-FW 1.0.2 的韌體原始碼、隨附映像、CI 身分檢查、文件及雜湊。
- 通過 ESP-IDF 5.5.4 乾淨建置及全部 537 項自動化回歸測試。

桌面端電量曲線已使用實體控制器驗證；新的獨立模式燈號已通過建置及自動化
契約驗證，仍建議每款 ESP32 板在刷入後進行控制器實測。
