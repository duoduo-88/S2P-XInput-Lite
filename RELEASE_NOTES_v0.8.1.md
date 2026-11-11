# v0.8.1 — Aim Test grid and mouse fixes

- The 3D room uses low-contrast filled squares to reduce shimmer as the
  camera turns. The floor and walls have distinct palettes and the top is open.
- 3D mouse aim uses the cursor's actual position after recentering. If Windows
  places the cursor away from the requested center, the offset no longer turns
  the camera while the mouse is still.

On a Radeon RX 7900 GRE, the fixed 3D replay averaged 177.0 Direct2D
submissions per second in a 1080p window on a 180 Hz display for both room styles.
At 4K offscreen with a 600 Hz scheduler target, the wireframe and filled room
averaged 593.4 and 593.9 submissions per second, respectively. The P99 interval
went from 2.98 ms to 3.08 ms.

## 繁體中文

- 3D 場景使用低對比的交錯填色方格，減輕視角轉動時的閃爍。地板與牆面各用不同色調，頂部維持開放。
- 3D 滑鼠瞄準改用游標回中後的實際落點。Windows 調整落點時，這個偏移不會在滑鼠靜止時轉動視角。

在 Radeon RX 7900 GRE 上以同一段 3D 回放測試，1080p 視窗於 180 Hz 螢幕上，線框版和填色版
平均都是每秒 177.0 次 Direct2D 繪圖提交。4K 離屏測試將排程目標設為 600 Hz，兩版平均分別為
每秒 593.4 和 593.9 次提交，P99 幀間隔從 2.98 ms 增至 3.08 ms。
