# S2P-XInput-Lite v0.7.2

## English

v0.7.2 is a desktop maintenance release. The bundled ESP32 firmware remains
S2P-FW 1.0.1.

### Fixed

- Closing the main window while it is minimized now restores the owner before
  showing confirmation dialogs, preventing prompts from appearing at the
  desktop origin or behind other windows.
- Canceling a close confirmation returns the main window to its previous
  minimized state.
- Profile names can now be changed by capitalization alone on Windows.
- Runtime status snapshots no longer expose nested mutable internal state.

### Release hardening

- Release verification now rejects unsafe or duplicate manifest paths and
  requires `SHA256SUMS.txt` to cover the complete package payload exactly.
- The packaged import smoke test no longer creates bytecode cache files.

## 繁體中文

v0.7.2 是桌面端維護版本；隨附的 ESP32 韌體版本維持 S2P-FW 1.0.1。

### 修正

- 主視窗最小化時執行關閉，現在會先還原擁有者視窗再顯示確認對話框，
  避免對話框出現在桌面左上角或其他視窗後方。
- 取消關閉後，主視窗會回到原本的最小化狀態。
- Windows 現在可以只變更方案名稱的英文大小寫。
- 執行狀態快照不再暴露可變的內部巢狀資料。

### 發佈流程強化

- 發佈驗證現在會拒絕不安全或重複的 manifest 路徑，並要求
  `SHA256SUMS.txt` 完整且精確涵蓋套件中的所有檔案。
- 套件匯入冒煙測試不再產生 Python bytecode 快取檔案。
