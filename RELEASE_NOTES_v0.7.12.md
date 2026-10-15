# S2P-XInput-Lite v0.7.12 Release Notes

## English

### Reliability and safety improvements

- Reject incomplete wired USB full input reports instead of padding missing button and stick bytes with zeros.
- Validate imported global settings before copying data to the current installation.
- Keep migration snapshot, writes, and rollback within one cross-process configuration lock.
- Serialize the audio capture callback with shutdown so delayed native reads cannot re-enable non-zero rumble after close.
- Verify actual ESP32-S3 flash capacity with the read-only `flash_id` command before writing firmware. The bundled firmware requires a 16 MB board; 8 MB or unknown capacities are rejected.
- Expand automated coverage for invalid imports, migration rollback, USB report length, audio close, and ESP32 flash preflight.

The published **v0.7.12** release originally bundled **S2P-FW 1.0.5** without changing its firmware binary.

### Main branch update: v0.7.13

- Desktop **v0.7.13** includes **S2P-FW 1.0.6** and the matching bridge shutdown fix. See the [v0.7.13 notes](RELEASE_NOTES_v0.7.13.md). The existing v0.7.12 release archive remains unchanged.
- Graceful exit waits for firmware confirmation that all BLE links are released. A GUI-only crash also requests connector shutdown.
- Once a host session is established, 3 seconds of continuous CDC loss or 8 seconds without matching heartbeats starts BLE release. This protection applies only when both configured and active modes are bridge; standalone XInput, HID and Auto retain their existing idle/wake behavior.
- Added shutdown, host-loss and multi-controller race coverage. All 647 tests passed locally, including the optional ESP-IDF build and bundled-image comparison; clean-build images match byte for byte.
- The updated desktop source and manually flashed 1.0.6 firmware are both required. Hardware shutdown, sleep-current and reconnect validation remain pending. See the [investigation and hardware checklist](docs/bridge-host-shutdown.md).

## 繁體中文

### 可靠性與安全性改善

- USB 收到不完整的完整輸入報告時直接捨棄，不再補零導致異常按鍵或搖桿狀態。
- 匯入設定前先驗證全域參數，避免將無效值寫入現有設定。
- 設定移轉的快照、寫入與失敗復原都維持在同一個跨程序設定鎖內。
- 音訊擷取 callback 與關閉作業同步，避免延遲讀取在關閉後再次觸發非零震動。
- ESP32-S3 刷機前先以唯讀 `flash_id` 偵測實際 Flash 容量。隨附韌體要求 16 MB；8 MB 或無法判別的板子禁止刷寫。
- 增加匯入失敗、交易復原、USB 報告長度、音訊關閉及刷機前檢查等回歸測試。

已發佈的 **v0.7.12** 原本隨附 **S2P-FW 1.0.5**，該次發佈沒有修改韌體映像。

### main 更新：v0.7.13

- 桌面 **v0.7.13** 包含 **S2P-FW 1.0.6** 與配套橋接關閉修正，詳見 [v0.7.13 發佈說明](RELEASE_NOTES_v0.7.13.md)。既有 v0.7.12 發佈壓縮檔保留。
- 正常退出會等待韌體確認全部 BLE 連線解除；只有 GUI 崩潰時，也會通知連接子程序關閉。
- 主機工作階段建立後，CDC 連續消失 3 秒或對應心跳逾時 8 秒便開始釋放 BLE。只有設定與實際模式皆為一般橋接時啟用；獨立 XInput／HID／Auto 保留既有閒置斷線與喚醒行為。
- 新增關閉、主機遺失與多手把競爭測試。本機 647 項測試全部通過，包含可選 ESP-IDF 建置與隨附映像比對；乾淨重建映像逐位元一致。
- 須搭配更新後的桌面程式碼及手動刷寫 1.0.6 韌體。實機斷線、休眠電流與重連仍待驗證，詳見[調查報告及實機檢查表](docs/bridge-host-shutdown.md)。

> Flashing, disconnect and host-detection behaviour require hardware validation before publishing the release.
> 正式發佈前仍需在實際硬體上驗證刷寫、斷線與主機偵測行為。
