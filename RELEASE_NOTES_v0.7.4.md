# S2P-XInput-Lite v0.7.4

## English

v0.7.4 improves desktop window behavior and keeps connection status visible.
The bundled ESP32 firmware remains S2P-FW 1.0.1.

- Keeps the connector in its own visible black command window so pairing,
  connection, input, and transport diagnostics remain readable while playing.
- Minimizing Settings moves only the main window to the Windows notification
  area. The connector command window remains visible and the connection keeps
  running.
- Restores Settings by clicking the notification icon or choosing **Show
  Settings** from its context menu; **Exit** follows the normal confirmation
  and cleanup flow.
- Uses the project's original controller artwork for the notification icon and
  embeds the same multi-size Windows icon in newly built main launchers.
- Prevents duplicate Settings and Gamepad Tester processes. Reopening either
  application restores and foregrounds the existing window instead of creating
  a second process that could compete for controller access.
- Serializes repeated connector, calibration, firmware-detection, and tester
  launch requests to prevent overlapping operations.
- Falls back safely to normal taskbar minimization if the notification icon
  cannot be created, and restores Settings automatically if the tray backend
  becomes unavailable while hidden.
- Expands the automated regression suite to 526 tests.

## 繁體中文

v0.7.4 改善桌面視窗操作，並讓連線狀態持續可見；隨附的 ESP32 韌體版本維持
S2P-FW 1.0.1。

- 連線程序會保留獨立的黑色 CMD 視窗，遊玩時仍可查看配對、連線、輸入及
  傳輸診斷狀態。
- 縮小設定介面時，只有主視窗會移到 Windows 右下角通知區；黑色 CMD 仍會
  顯示，控制器連線也會繼續運作。
- 按一下通知區圖示或在右鍵選單選擇「顯示設定」即可還原主視窗；「結束程式」
  仍會執行原本的確認及完整清理流程。
- 通知區會使用本專案原創的控制器圖示，之後重建的主啟動器也會嵌入相同的
  Windows 多尺寸圖示。
- 設定介面與手把測試程式皆採全域單例；重複啟動只會還原並帶出既有視窗，
  不會再建立可能搶占控制器的第二份程序。
- 重複點擊連線、校正、韌體偵測及手把測試時會序列化處理，避免操作重疊。
- 通知區圖示若無法建立，主視窗會安全保留一般工作列縮小行為；隱藏期間若
  通知區後端失效，設定介面會自動還原，避免視窗遺失。
- 自動化回歸測試擴充至 526 項。
