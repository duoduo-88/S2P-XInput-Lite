# S2P-XInput-Lite v0.7.12 Release Notes

## English

### Reliability and safety improvements

- Reject incomplete wired USB full input reports instead of padding missing button and stick bytes with zeros.
- Validate imported global settings before copying data to the current installation.
- Keep migration snapshot, writes, and rollback within one cross-process configuration lock.
- Serialize the audio capture callback with shutdown so delayed native reads cannot re-enable non-zero rumble after close.
- Verify actual ESP32-S3 flash capacity with the read-only `flash_id` command before writing firmware. The bundled firmware requires a 16 MB board; 8 MB or unknown capacities are rejected.
- Expand automated coverage for invalid imports, migration rollback, USB report length, audio close, and ESP32 flash preflight.

The desktop application is **v0.7.12**. Bundled firmware is still **S2P-FW 1.0.5**. The firmware binary has not been modified.

## 繁體中文

### 可靠性與安全性改善

- USB 收到不完整的完整輸入報告時直接捨棄，不再補零導致異常按鍵或搖桿狀態。
- 匯入設定前先驗證全域參數，避免將無效值寫入現有設定。
- 設定移轉的快照、寫入與失敗復原都維持在同一個跨程序設定鎖內。
- 音訊擷取 callback 與關閉作業同步，避免延遲讀取在關閉後再次觸發非零震動。
- ESP32-S3 刷機前先以唯讀 `flash_id` 偵測實際 Flash 容量。隨附韌體要求 16 MB；8 MB 或無法判別的板子禁止刷寫。
- 增加匯入失敗、交易復原、USB 報告長度、音訊關閉及刷機前檢查等回歸測試。

桌面程式版本為 **v0.7.12**。隨附韌體仍為 **S2P-FW 1.0.5**，韌體映像檔未修改。

> Flashing, disconnect and host-detection behaviour require hardware validation before publishing the release.
> 正式發佈前仍需在實際硬體上驗證刷寫、斷線與主機偵測行為。
