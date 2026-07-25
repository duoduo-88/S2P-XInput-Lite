# S2P-XInput-Lite v0.6.1

Release date: 2026-07-26

## English

### Highlights

- Strengthens readiness and disconnect handling for wired USB, ESP32-S3, and
  native Windows BLE connections.
- Prevents stale input, acknowledgements, and rumble work from crossing a
  controller reconnect or connection generation.
- Makes shutdown wait for the final zero-rumble frame within bounded deadlines
  and retains live worker references when cleanup cannot complete safely.
- Fixes connection-feedback completion handling: calibration or ready callbacks
  now run only after a cue finishes successfully for the active connection.
- Adds mobile-device compatibility guidance for the ESP32-S3 standalone USB HID
  mode.

### Updating

1. Extract this release to a new folder; do not overwrite a running
   installation.
2. To retain personal settings, copy the old `src/config.ini` into the new
   release after extraction.
3. ESP32-S3 standalone users should flash the bundled firmware before using
   standalone output.

## 繁體中文

### 重點更新

- 強化有線 USB、ESP32-S3 與原生 Windows BLE 的 ready 狀態與斷線處理。
- 防止舊連線世代的輸入、ACK 與震動工作跨越重新連線後作用於新控制器。
- 關閉時會在受限期限內等待最後的零震動封包；若無法安全結束清理，會保留仍存活的 worker 參考。
- 修正連線提示震動的完成判定：校正或 ready callback 僅會在目前連線的提示成功播完後執行。
- 補充 ESP32-S3 USB HID 獨立模式的行動裝置相容性說明。

### 更新方式

1. 請解壓縮到新資料夾，不要覆寫正在執行的舊版。
2. 如需保留個人設定，請在解壓後將舊版的 `src/config.ini` 複製到新版。
3. 使用 ESP32-S3 獨立模式前，請先刷入本版隨附的韌體。
