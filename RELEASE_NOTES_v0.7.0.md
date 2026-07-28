# S2P-XInput-Lite v0.7.0

Release date: 2026-07-28

## English

### Highlights

- Adds a standalone **Gamepad Test** window for S2P-XInput-Lite output and
  third-party controllers.
- Displays live stick input/output, button mappings, trigger values, XInput
  slot information, and vibration tests.
- Allows direct selection of individual Raw HID collections and adds a
  report-rate measurement page with report count, P50/P95/P99 intervals,
  distribution statistics, and a timeline.
- Adds an ESP32 **Diagnostics** tab with 30/60/120-second tests, a concise
  pass/warn/fail verdict, and UTF-8 text report export.
- Adds a bilingual **About** tab with the project logo, GitHub and Ko-fi links,
  plus scrollable license and third-party-software pages.
- Adds configurable trail rendering and measured output-shape capture. Shape
  drawing follows the display refresh rate while active, then stops redrawing
  after the capture is complete and stable.
- Corrects the S2P mobile USB HID button, axis, trigger, and POV mapping used by
  the Windows tester.
- Runs the tester in a separate process, raises the existing tester instead of
  opening duplicates, and safely queues a reopen request while the old process
  is closing.
- Adds bilingual initialization feedback and keeps the settings and tester
  windows hidden until their final centered geometry is ready.
- Adds a startup/restore splash, improves dialog centering, and prevents
  partially rendered or translucent settings windows from flashing onscreen.
- Adds a close warning explaining that bridge mode disconnects when the
  settings application exits while standalone mode continues. The warning can
  be dismissed and is restored by **Restore Defaults**.
- Fixes stick direction-mode persistence so linear-trigger settings cannot
  reopen with the selector incorrectly showing 4-way mode.
- Reduces audio-haptics processing overhead by caching FFT analysis data and
  draining audio without DSP work while game rumble owns the output.
- Completes Traditional Chinese and English text coverage for the new
  interfaces, shortens constrained English tester controls, and improves
  tooltip line wrapping.
- Makes diagnostic shutdown interrupt an in-progress serial response wait and
  serializes start/stop transitions so the worker and COM port are released
  promptly.
- Bundles `S2P-FW 1.0.1` with standalone PC/mobile output, link and latency
  diagnostics, A/B profile storage, and the independent `s2p_bridge 1.0.0`
  protocol.

### Gamepad Test preview

![S2P-XInput-Lite Gamepad Test interface](https://raw.githubusercontent.com/duoduo-88/S2P-XInput-Lite/v0.7.0/image/test.gif)

### Updating

1. Extract this release to a new folder; do not overwrite a running
   installation.
2. To retain personal settings, copy the old `src/config.ini` into the new
   release after extraction.
3. ESP32-S3 standalone users should flash the bundled firmware before using
   standalone output.

## 繁體中文

### 重點更新

- 新增獨立的 **手把測試** 視窗，可測試 S2P-XInput-Lite 輸出及第三方手把。
- 即時顯示搖桿輸入／輸出、按鍵映射、扳機數值、XInput 插槽資訊及震動測試。
- 可直接選擇個別 Raw HID collection，新增回報率量測頁，顯示回報筆數、
  P50／P95／P99 間隔、分佈統計與時間軸。
- 新增 ESP32 **診斷** 頁，支援 30／60／120 秒測試、清楚的通過／警告／失敗
  判定，以及匯出 UTF-8 文字報告。
- 新增中英文 **關於** 頁：顯示專案 Logo、GitHub 與 Ko-fi 連結，右側提供
  可捲動的許可協議及第三方程式頁簽。
- 新增可調整的軌跡顯示與實測輸出形狀。量測時會配合螢幕更新率即時繪製；
  覆蓋完成並穩定後會停止重畫，避免持續消耗繪製效能。
- 修正 Windows 測試工具對 S2P 行動裝置 USB HID 的按鍵、軸、扳機與 POV
  映射。
- 手把測試改以獨立程序執行；重複按下按鈕時會叫回現有視窗，舊程序關閉中
  的再次點擊則會在退出後安全地重新開啟。
- 新增中英文初始化提示，主設定與手把測試視窗都會等到尺寸與置中位置確定後
  才顯示。
- 新增啟動／還原啟動畫面、改善對話框置中，並避免未完成或半透明的設定視窗
  短暫閃現。
- 新增關閉提示：橋接模式會在設定程式關閉後斷線，獨立模式則會繼續運作。
  可勾選不再提示，並可透過 **還原預設** 恢復提示。
- 修正搖桿方向模式保存問題，避免線性扳機設定在重新開啟程式後錯誤顯示為
  4-way。
- 快取 FFT 分析資料，且遊戲震動接管輸出時僅清空音訊緩衝、不執行 DSP，
  降低音訊震動處理負擔。
- 補齊新介面的繁體中文與英文文字、縮短受限寬度內的英文測試控制文字，
  並改善說明提示的自動換行。
- 診斷停止時會中斷正在等待的序列回應，並序列化啟動／停止流程，讓工作執行緒
  與 COM Port 能及時釋放。
- 隨附 `S2P-FW 1.0.1`，包含 PC／手機獨立輸出、連線與延遲診斷、A/B
  設定檔儲存，以及獨立的 `s2p_bridge 1.0.0` 協議。

### 手把測試預覽

![S2P-XInput-Lite 手把測試介面](https://raw.githubusercontent.com/duoduo-88/S2P-XInput-Lite/v0.7.0/image/test.gif)

### 更新方式

1. 請解壓縮到新資料夾，不要覆寫正在執行的舊版。
2. 如需保留個人設定，請在解壓後將舊版的 `src/config.ini` 複製到新版。
3. 使用 ESP32-S3 獨立模式前，請先刷入本版隨附的韌體。
