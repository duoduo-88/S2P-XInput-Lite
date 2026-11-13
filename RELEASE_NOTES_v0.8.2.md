# v0.8.2

## English

- Gyro X and Y ratios now range from `0.10` to `2.00`. **Compensation Settings** accepts a separate multiplier for each axis. It scales gyro stick output after smoothing and the response curve.
- The 3D compensation preview reads its starting speeds from **Test Settings** and measures both axes against trial speeds. The adjacent calculator estimates compensation from in-game turn times. Tracking supports adjustable target speed and reverses at the ends of the configured arc.
- Center gyro uses a radial deadzone and offers button spike suppression. The default button stabilization mode is Freeze Output. The bundled FPS-COMP preset uses `10 ms` smoothing.
- Bundled S2P-FW `1.0.9` brings the radial deadzone, spike suppression, and independent gyro compensation to Android USB HID standalone mode. Use **Flash FW** in the main app, then write your profile to the ESP32.

## 繁體中文

- 陀螺儀 X／Y 比例放寬到 `0.10–2.00`。「補償設定」可分軸輸入補償倍率，在平滑和感度曲線之後調整陀螺儀搖桿輸出。
- 3D 補償預覽從「測試設定」讀取原始視角速度，一次比較水平和垂直兩軸。旁邊的「補償計算」可用遊戲錄影的轉向耗時試算倍率。跟槍可調整靶速，靶會在指定角度範圍內往返。
- 回中模式改用圓形死區，按鍵防晃可選「抑制突波」，預設為「暫停輸出」。內建 FPS-COMP 方案的平滑值調為 `10 ms`。
- 隨附 S2P-FW `1.0.9`，Android USB HID 獨立模式支援圓形死區、抑制突波及獨立的陀螺儀輸出補償。在主視窗刷入新韌體後，再將設定檔寫入 ESP32。
