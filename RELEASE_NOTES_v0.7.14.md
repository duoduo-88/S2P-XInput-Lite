# v0.7.14 — USB HID standardization

Formal GitHub Release. New firmware hardware validation remains pending.
Desktop **0.7.14**, bundled **S2P-FW 1.0.7**, protocol **s2p_bridge 1.0.0**.
The existing standalone profile schema is retained.

- The portable ZIP opens to **S2P-XInput-Lite.exe**, **GamepadTester.exe**,
  **START_HERE.txt**, and **app/**. Runtime, firmware, drivers, manuals,
  licenses and checksum metadata are grouped under app. Keep this folder
  beside the EXEs. Settings import accepts both earlier and new layouts.
- ESP32 now has two modes: CDC Bridge (`303A:4001`) and CDC + Android USB HID
  (`303A:4005`, `S2P Mobile Gamepad`). Physical PC USB XInput, XUSB20 and Auto
  probing are removed. Windows desktop XInput through ViGEmBus, mappings,
  game rumble and audio haptics remain on the Bridge path.
- The activation menu provides **Write and enable Android USB HID standalone**,
  plus return to Bridge.
- Old NVS `1=XInput` and `3=Auto` migrate to HID with a desktop notice;
  `0=Bridge` and `2=HID` retain their meanings. Unknown/read-error values start
  in Bridge. Migration does not itself reboot; persistence failure preserves a
  safe mode and CDC for retry. Removed mode commands return unsupported errors.
- Hold HOME+A for 3 seconds and release for HID, or HOME+X for 3 seconds and
  release for Bridge. HOME+Y is removed. Raw recovery chords bypass profiles;
  same-mode or failed persistence requests do not reboot. CDC `mode bridge`
  and board-specific ROM BOOT/RESET recovery remain available without a controller.
- USB identifiers are shared Espressif/TinyUSB defaults, not a dedicated PID
  allocation or USB certification. Desktop S2P operations require verified
  firmware identity and compatible capabilities. HID special handling requires
  association to the exact verified CDC device; otherwise generic HID behavior
  is retained. Legacy `CAFE:4020/4021/4022` are upgrade candidates only.
- HID reuses calibration, stick curves/deadzones, mapping layers, gyro-to-stick,
  idle disconnect and BLE reconnect. HID remains input-only: Android game rumble
  is not promised. Windows-only keyboard/mouse, audio and process features are
  not executed on the ESP32.
- Firmware builds use reproducible ESP-IDF 5.5.4 with locked esp_tinyusb 1.7.6~2
  and tinyusb 0.19.0~3. Bundle image hashes are recorded in
  [`esp32s3/firmware/SHA256SUMS.txt`](esp32s3/firmware/SHA256SUMS.txt).

No new hardware validation has occurred. Windows/Android descriptors and inputs,
physical ViGEmBus, rumble/audio, unplug/replug, sleep/remote wake, idle/reconnect,
actual NVS migration, profile restart, chords, CDC rescue and ROM rescue remain
pending. Earlier performance measurements do not validate this firmware.
See [mode details and the hardware checklist](docs/usb-hid-standardization.md).
Automated/build/package evidence must report actual results separately and must
not be presented as hardware validation.

Local verification: **676 automated tests passed, zero skips**, including the
enabled ESP-IDF build and exact bundled-image comparison. Compiled ELF/BIN USB
descriptors, retained HID layout, Python source compilation and Ruff passed.
See the linked evidence report for hashes and hardware limitations.
Both root-level EXEs also opened their expected desktop windows from an
extracted ZIP in a path containing spaces and Chinese characters.

## 繁體中文

v0.7.14 隨附 **S2P-FW 1.0.7**，通訊協定維持 `s2p_bridge 1.0.0`，既有獨立模式設定檔格式不變。

### 更新重點

- 重新整理 ZIP 根目錄，只保留 **S2P-XInput-Lite.exe**、**GamepadTester.exe**、**START_HERE.txt** 與 **app/**。執行環境、韌體、驅動、手冊、授權及校驗資料統一收進 app；請與兩個 EXE 一起保留。匯入設定同時支援新舊配置。
- ESP32-S3 僅提供一般 USB CDC Bridge（`303A:4001`）與 Android USB HID＋CDC 獨立模式（`303A:4005`，產品名稱 `S2P Mobile Gamepad`）。
- ESP32 獨立 PC XInput、XUSB20 描述符及 Auto 自動判斷／重新列舉已移除。Windows 桌面程式仍可在 Bridge 模式透過 ViGEmBus 輸出 XInput，並保留按鍵映射、遊戲震動與音訊震動。
- Android HID 可沿用既有校正、按鍵映射、搖桿曲線與死區、映射層、陀螺儀轉搖桿、閒置斷線及 BLE 重新連線功能。

### 舊版設定與模式切換

- 舊 NVS `0=Bridge`、`2=HID` 保持原意；`1=XInput`、`3=Auto` 會安全遷移為 HID，桌面程式會明確提示。未知或讀取失敗的值會回到 Bridge。
- 選單只提供「寫入並啟用 Android USB HID 獨立模式」及切回 Bridge。長按 HOME+A 3 秒後放開切到 HID；長按 HOME+X 3 秒後放開切回 Bridge。HOME+Y 自動模式組合鍵已移除。
- 兩種模式都保留 CDC 救援；沒有手把時可用 CDC 指令切回 Bridge。CDC 無法使用時，可依板卡說明使用 BOOT／RESET 進入 ESP32-S3 ROM Download Mode。

### USB 識別與功能限制

- `303A:4001` 與 `303A:4005` 是 Espressif/TinyUSB 的共享類別預設值，不是專屬核發的 PID，也不代表 USB 認證或 Espressif 背書。桌面程式會再驗證韌體身分與功能，不會只憑 PID 判定 S2P 裝置。
- Android HID 僅提供輸入報告，**不支援也不承諾手機遊戲震動**；Windows 鍵盤／滑鼠、音訊震動及依程序切換等功能不會在 ESP32 獨立模式執行。

### 驗證狀態

- 自動化測試 **676 項通過、零略過**；ESP-IDF 5.5.4 編譯、隨附映像比對、實際 ELF／BIN USB 描述符檢查、Python 編譯及 Ruff 檢查均通過。韌體依賴鎖定為 esp_tinyusb 1.7.6~2 與 tinyusb 0.19.0~3，包內映像 SHA-256 收錄於 `app/esp32s3/firmware/SHA256SUMS.txt`。
- 解壓後兩個根目錄 EXE 已在含空白及中文的路徑啟動，均顯示正確主視窗；這項結果只代表桌面啟動驗證。
- 本版尚未進行實機測試。Windows／Android 列舉與輸入、實體 ViGEmBus／震動／音訊震動、拔插與睡眠、閒置斷線及重連、舊 NVS 遷移、設定檔重啟、控制器組合鍵、CDC 救援與 ROM 救援均待測。自動化與編譯結果不代表硬體已通過。

詳細模式說明與實機檢查表請見[USB HID 標準化驗證文件](docs/usb-hid-standardization.md)。

