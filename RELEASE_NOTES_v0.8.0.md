# v0.8.0 — Gamepad Tester Aim Test and Stick Trails

Gamepad Tester adds 2D and 3D aim drills. Stick trails use a native background
worker, and Settings adds a radar editor for directional deadzones.

## What's new

- Choose Tracking, Targets, or Timed Targets in either dimension. Both
  dimensions accept mouse or right-stick input.
- Run the drill in a separate window or fullscreen. Both 2D and 3D use
  a background Direct2D renderer when available and fall back to Tk. The
  draw rate follows the current monitor refresh rate.
- Adjust round lengths, target layout, reticle and target appearance,
  vibration, and sound. A saved seed lets you replay a layout.
- Review or clear completed rounds in the Aim Test tab. The tester compares
  each result with the previous best under matching conditions.
- Fullscreen restores the previous display mode when closed. Restarting the
  test clears stale render work, and renderer changes start a fresh FPS
  measurement. The first RT press after the countdown now counts when the
  trigger was released before the round.
- Stick trails retain each report's position and lifetime. A shared-memory
  sequence fix prevents the trail reader from replaying old samples while the
  writer publishes a new sequence.
- Edit the width and angle of individual gaps for `4WAY` or `8WAY` mappings.
  Profile and mapping layer fixes keep edits associated with the intended
  settings when files change or the list refreshes.
- Settings import preserves compatible profiles, layers and calibration. The
  bundled ESP32-S3 firmware is **S2P-FW 1.0.7**.

## Installation

Extract the full Windows ZIP to a new folder and open **S2P-XInput-Lite.exe**
or **GamepadTester.exe**. Keep **app** beside both EXEs. To upgrade, use
**Import Settings** and select the old folder containing its EXEs.
[User Guide](manual/USER_GUIDE.md)

## Validation

The 30-minute Windows test opened and closed the tester 30 times. All render
workers and Tk timers were released after each close. Five aim sessions were
interrupted, and some 10,000 Hz synthetic input intervals had gaps. After the
sequence fix, a one-minute rerun received 240,310 consecutive synthetic
samples during the measured intervals. Physical dual-monitor movement and
scoring across a physical controller reconnect remain to be verified.
[Performance and test details](docs/aim-performance.md)

## 繁體中文

手把測試程式新增 2D 與 3D 瞄準測試。搖桿軌跡交由原生背景執行緒繪製，設定頁也新增方向死區雷達編輯器。

### 更新重點

- 兩種模式都提供跟槍、點靶與限時點靶，並支援滑鼠與右搖桿。
- 可在獨立視窗或全螢幕執行測試。2D 和 3D 都優先使用背景 Direct2D 繪圖，無法使用時會切回 Tk。繪圖目標跟隨螢幕目前的更新率。
- 可調整回合時間、出靶配置、準心和靶的外觀、震動與音效。保留種子即可重現出靶配置。
- 可在瞄準測試分頁檢視或清除已完成的紀錄。每筆成績會與相同條件下的歷史最佳成績比較。
- 關閉全螢幕會還原原本的顯示模式。重開測試會清理舊畫面，切換繪圖方式會重新計量 FPS。倒數時 RT 已放開的情況下，第一下按壓會正常計入。
- 軌跡保留每筆取樣的位置與到期時間。修正共享記憶體序號更新期間可能重讀舊取樣的問題。
- `4WAY` 與 `8WAY` 模式可個別調整方向死區的角度與寬度。方案及映射層的編輯也會在清單重新整理或外部檔案變更時對應原本的設定。
- 「匯入設定」可遷移相容的方案、映射層與校正資料。隨附 ESP32-S3 韌體為 **S2P-FW 1.0.7**。

### 使用方式

完整解壓縮 Windows ZIP 到新資料夾，再開啟 **S2P-XInput-Lite.exe** 或 **GamepadTester.exe**。
請將 **app** 資料夾與兩個 EXE 一起保留。升級時可用「匯入設定」選取舊版含 EXE 的程式根目錄。
[繁體中文使用手冊](manual/USER_GUIDE_zh-TW.md)

### 驗證狀態

Windows 實機在 30 分鐘內開關測試視窗 30 次，每輪關窗後的繪圖執行緒與 Tk 計時器均已釋放。
其中 5 輪瞄準中斷，部分 10,000 Hz 合成輸入區間也有採樣缺口。修正序號後的一分鐘重測中，
量測區間共收到 240,310 筆連續的合成採樣。實體雙螢幕移動與手把拔插後同一局的計分仍待實機驗證。
[效能與測試紀錄](docs/aim-performance.md)
