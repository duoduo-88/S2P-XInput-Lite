# S2P-XInput-Lite v0.7.10

## English

**Runtime reliability and calibration diagnostics**

- Console and redirected startup-log output now use a bounded asynchronous listener. Input, transport, and rumble callbacks enqueue diagnostics instead of waiting for console or file I/O; status diagnostics report listener health and dropped-output counts.
- Added reusable Legacy / Shadow / V2 validation infrastructure for future high-risk runtime algorithm work. Shadow candidates collect bounded error statistics and cannot control production output.
- Magnetometer ellipsoid calibration now reports 3D direction coverage as `octant_count` / `octant_mask`, plus a robust raw-sensor `reference_magnitude_lsb`. Existing fit, outlier rejection, quality gates, and 9-axis runtime correction remain unchanged.
- The v0.7.9 hybrid low-latency input routing is retained: button/ZL/ZR edges use the bounded FIFO, while sticks and IMU remain latest-only.

This release does not claim an average latency reduction.

## 繁體中文

**Runtime reliability and calibration diagnostics（執行期可靠性與校正診斷）**

- Console 與重新導向的 startup log 改用有界的非同步 listener。輸入、傳輸與震動 callback 只會排入診斷訊息，不會等待 console 或檔案 I/O；status 診斷會顯示 listener 狀態與丟棄訊息數。
- 新增可重用的 Legacy / Shadow / V2 驗證基礎設施，供未來高風險 runtime 演算法使用。Shadow candidate 只收集有界的誤差統計，絕不控制正式輸出。
- 磁力計橢球校正新增 `octant_count` / `octant_mask` 三維方向覆蓋，以及穩健的原始感測器 `reference_magnitude_lsb`。既有擬合、離群值排除、品質 gate 與 9-axis runtime 修正均維持不變。
- 保留 v0.7.9 的 Hybrid Low-Latency Input Routing：Button／ZL／ZR edge 持續使用 bounded FIFO；Stick 與 IMU 持續採 latest-only。

本版未宣稱降低平均輸入延遲。
