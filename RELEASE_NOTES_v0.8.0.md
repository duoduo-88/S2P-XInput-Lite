# v0.8.0 — Gamepad Tester Aim Test and Stick Trails

Gamepad Tester adds 2D and 3D aim drills for measuring how your Windows
controller or mouse output performs.

- Choose Tracking, Targets, or Timed Targets in either dimension. Both
  dimensions accept mouse or right-stick input.
- Run the drill in a separate window or fullscreen. Both 2D and 3D use
  a background Direct2D renderer when available and fall back to Tk. The
  draw rate follows the current monitor refresh rate.
- Adjust round lengths, target layout, reticle and target appearance,
  vibration, and sound. A saved seed lets you replay a layout.
- Review or clear completed rounds in the Aim Test tab. The tester compares
  each result with the previous best under matching conditions.
- Closing or interrupting a drill cleans up its input and feedback state.
  Fullscreen mode restores the original display mode when the test closes.
  Aim settings and history serialize local JSON access to protect writes
  during concurrent use.

Stick trails rasterize samples on a native background worker and use Windows
Canvas composition with a Tk image fallback.
The trail retains each received sample's position and lifetime. Overlapping
pixels share the same mark.

With synthetic input at 10,000 reports per second, a 180 Hz display, a
five-second trail, and 100% sample display, the previous renderer averaged
46 FPS per plot. The new renderer averaged 172 FPS on the left and 162 FPS on
the right. Each plot held about 50,000 samples. All 86,090 reports received
during the 8.6-second measurement had consecutive sequence numbers. This
benchmark covers the shared telemetry queue and Input Monitor rendering.
The background renderer measurements are in [Aim and stick trail performance](docs/aim-performance.md).

## 繁體中文

手把測試程式新增 2D 與 3D 瞄準測試，可評估 Windows 的手把或滑鼠輸出表現。

- 兩種模式都提供跟槍、點靶與限時點靶，並支援滑鼠與右搖桿。
- 可在獨立視窗或全螢幕執行測試。2D 和 3D 都優先使用背景 Direct2D 繪圖，無法使用時會切回 Tk。繪圖目標跟隨螢幕目前的更新率。
- 可調整回合時間、出靶配置、準心和靶的外觀、震動與音效。保留種子即可重現出靶配置。
- 可在瞄準測試分頁檢視或清除已完成的紀錄。每筆成績會與相同條件下的歷史最佳成績比較。
- 測試關閉或中斷時會清理輸入與回饋狀態。關閉全螢幕測試時會還原原本的顯示模式。瞄準設定與歷史紀錄會協調本機 JSON 檔案的讀寫，避免多個實例同時存取時互相干擾。

搖桿軌跡在原生背景執行緒處理取樣，使用 Windows Canvas 合成，必要時會切回 Tk 圖塊。每筆取樣保留原始落點和到期時間，同一像素上的點會合併顯示。

在 180 Hz 螢幕上，以每秒 10,000 筆合成資料、5 秒軌跡和 100% 顯示比例測試，舊版左右圖各約 46 FPS，新版左圖約 172 FPS、右圖約 162 FPS。每張圖各保留約 5 萬筆取樣，8.6 秒量測期間收到的 86,090 筆回報序號連續。這項測試涵蓋共享遙測佇列與輸入監看頁的繪圖。
背景繪圖的量測結果見 [瞄準與搖桿軌跡效能紀錄](docs/aim-performance.md)。
