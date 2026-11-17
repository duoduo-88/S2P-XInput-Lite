# v0.8.2

## English

- Gyro X and Y ratios now range from `0.10` to `2.00`. **Compensation Settings** accepts a separate multiplier for each axis. It scales gyro stick output after smoothing and the response curve.
- The 3D compensation preview compares the default turn speed with your adjusted speed, then saves the suggested multiplier to S2P and applies it to the current connection. The adjacent calculator estimates compensation from in-game turn times. Tracking supports adjustable target speed and reverses at the ends of the configured arc.
- Center gyro uses a radial deadzone and offers button spike suppression. The default button stabilization mode is Freeze Output. Gyro stick anti-deadzone now defaults to `0`. The bundled FPS-COMP preset uses `10 ms` smoothing.
- Bundled S2P-FW `1.0.9` brings the radial deadzone, spike suppression, and independent gyro compensation to Android USB HID standalone mode. Use **Flash FW** in the main app, then write your profile to the ESP32.
- Gamepad Tester: **S2P Mobile Gamepad (Raw HID)** now shows every button and the D-pad, and works in the Aim Test.
- Gamepad Tester parameters, the Aim Test settings, and the compensation windows now work like the main app: drag a parameter's name to change it, and right-click for **Restore Saved** or **Restore Default**.
- Dialogs open centered on their window instead of the screen.
- If the ESP32 disconnects, the connector window stays open and shows why.
- Improved stability for connection, sensor calibration, and live settings apply.

## 繁體中文

- 陀螺儀 X／Y 比例放寬到 `0.10–2.00`。「補償設定」可分軸輸入補償倍率，在平滑和感度曲線之後調整陀螺儀搖桿輸出。
- 3D 補償預覽比較預設視角速度與調整後的速度，量測後會將建議倍率存入 S2P 設定，並套用到目前連線。旁邊的「補償計算」可用遊戲錄影的轉向耗時試算倍率。跟槍可調整靶速，靶會在指定角度範圍內往返。
- 回中模式改用圓形死區，按鍵防晃可選「抑制突波」，預設為「暫停輸出」。陀螺儀搖桿反死區預設改為 `0`。內建 FPS-COMP 方案的平滑值調為 `10 ms`。
- 隨附 S2P-FW `1.0.9`，Android USB HID 獨立模式支援圓形死區、抑制突波及獨立的陀螺儀輸出補償。在主視窗刷入新韌體後，再將設定檔寫入 ESP32。
- 手把測試：「S2P Mobile Gamepad（Raw HID）」現在會顯示所有按鍵與十字鍵，也能用於瞄準測試。
- 手把測試、瞄準測試設定與補償視窗的參數改成和主程式一樣：在參數名稱上拖曳可調整數值，按右鍵可選「還原上次儲存」或「還原系統預設」。
- 提示視窗改為顯示在所屬視窗中央，不再出現在螢幕中央。
- ESP32 中斷時，連接程式視窗會保留並顯示原因。
- 改善連線、感測器校正與即時套用設定的穩定性。
