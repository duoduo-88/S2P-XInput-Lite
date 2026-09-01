# S2P-XInput-Lite v0.7.9

## English

v0.7.9 fixes intermittent trigger and button-state loss in ESP32 bridge and standalone modes while preserving the low-latency newest-state path for motion input.

### Hybrid low-latency input routing

- Button, ZL, and ZR transitions now use a bounded edge FIFO in both bridge and standalone modes, preserving press and release order even when gyro activation is held or toggled.
- Stick and IMU samples remain newest-state-only, so stale gyro data is not replayed and normal motion latency does not gain a report backlog.
- Standalone mode now applies USB pending-slot backpressure before consuming another edge, preventing a preserved trigger press from being replaced before USB submission.
- The GCN-compatible input path uses the same transition preservation, including analog trigger threshold changes.

### Diagnostics and validation

- Latency diagnostics distinguish intentional continuous-state coalescing from edge-FIFO drops and USB pending-state pressure.
- The bundled ESP32 firmware was rebuilt as `S2P-FW 1.0.5`.
- The full automated suite passes 561 tests; the firmware also passes a complete ESP-IDF 5.5.4 build and release-image verification.

### Versions

- Desktop application: `v0.7.9`
- Bundled ESP32 firmware: `S2P-FW 1.0.5`
- Bridge protocol: `s2p_bridge 1.0.0`

---

## 繁體中文

v0.7.9 修正 ESP32 橋接與獨立模式下偶發的扳機／按鍵狀態遺失，同時保留動作輸入使用最新狀態的低延遲設計。

### 混合式低延遲輸入路徑

- 橋接與獨立模式的按鍵、ZL、ZR 變化改由有界邊沿 FIFO 傳送；即使按住或切換陀螺儀啟用鍵，也會依序保留按下與放開。
- 搖桿與 IMU 樣本仍採 latest-only，不會重播過期陀螺儀資料，也不會讓一般動作輸入累積回報延遲。
- 獨立模式在取得下一筆邊沿前會檢查 USB pending slot，避免已保留的扳機按下狀態在送往 USB 前又被取代。
- GCN 相容輸入路徑也套用相同的狀態保留，包含類比扳機跨越門檻時的變化。

### 診斷與驗證

- 延遲診斷現在能區分刻意合併的連續狀態、邊沿 FIFO 丟棄，以及 USB pending 狀態壓力。
- 隨附 ESP32 韌體已重新建置為 `S2P-FW 1.0.5`。
- 完整自動測試共 561 項全部通過；韌體也完成 ESP-IDF 5.5.4 全量編譯與發佈映像驗證。

### 版本

- 桌面程式：`v0.7.9`
- 隨附 ESP32 韌體：`S2P-FW 1.0.5`
- 橋接協議：`s2p_bridge 1.0.0`
