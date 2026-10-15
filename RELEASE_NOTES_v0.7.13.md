# S2P-XInput-Lite v0.7.13 Release Notes

## English

Desktop version: **v0.7.13**. Bundled firmware: **S2P-FW 1.0.6**.

- Release ESP32 bridge BLE links during normal application exit and request
  connector shutdown if only the GUI crashes.
- Wait for firmware confirmation that all BLE links are released, separately
  from acknowledgement that the disconnect request was accepted.
- After a bridge host session is established, 3 seconds of continuous CDC loss
  or 8 seconds without matching heartbeats starts sequential BLE release.
- Restrict the host watchdog to configured and active bridge mode. Standalone
  XInput, USB HID and Auto retain their existing idle, wake and reconnect paths;
  native Windows BLE and direct wired USB keep their existing cleanup order.
- Protect multi-controller teardown against unrelated disconnect events,
  reused slots and late connection completions; allow a new connector to wait
  for a crashed session to expire before reconnecting.

The bridge fix passed 647 local tests, including ESP-IDF build and bundled-image
comparison. Clean-build images match byte for byte. A user reported successful
flashing and initial normal functionality on 2026-10-16. Comprehensive shutdown,
sleep-current, rumble and reconnect checks remain pending. Use the updated desktop
application and manually flashed 1.0.6 firmware together. See the
[investigation and hardware checklist](docs/bridge-host-shutdown.md).

### Updating

1. Extract the v0.7.13 ZIP to a new folder.
2. Use **Import Settings** to transfer your settings, profiles, mapping layers
   and saved calibration from the old installation.
3. For ESP32 bridge protection, use **Flash FW** to install S2P-FW 1.0.6 if
   your board is still running an older firmware. Boards already on 1.0.6 do
   not need another flash for this desktop version change.
4. The `.zip.sha256` asset verifies the ZIP; `SHA256SUMS.txt` inside the
   package lists hashes for its contents.

## 繁體中文

桌面版本：**v0.7.13**。隨附韌體：**S2P-FW 1.0.6**。

- 正常退出時解除 ESP32 橋接 BLE 連線；只有 GUI 崩潰時，也會通知連接子程序關閉。
- 區分韌體接受斷線要求與所有 BLE 連線實際解除，等待後者才確認關閉完成。
- 一般橋接主機工作階段建立後，CDC 連續消失 3 秒或對應心跳逾時 8 秒便開始逐一解除 BLE。
- 主機 watchdog 僅在設定與實際模式皆為一般橋接時啟用。獨立 XInput／USB HID／Auto
  保留原有閒置、喚醒與重連流程；Windows 原生 BLE 及直接 USB 有線維持原有清理順序。
- 修正多手把斷線時無關事件、通道重用與延遲連線完成的競爭；異常終止後重開可等待舊工作階段到期再連線。

橋接修正的 647 項本機測試已全部通過，包含 ESP-IDF 建置與隨附映像比對；乾淨重建
映像逐位元一致。使用者於 2026-10-16 回報成功刷寫且初步功能正常；完整實機斷線、
休眠電流、震動與重連仍待驗證。須搭配更新後的桌面程式及手動刷寫 1.0.6 韌體，
詳見[調查報告及實機檢查表](docs/bridge-host-shutdown.md)。

### 更新方式

1. 將 v0.7.13 ZIP 解壓縮到新資料夾。
2. 使用**匯入設定**移轉舊版的設定、方案、映射層與已保存的校正資料。
3. ESP32 一般橋接若仍使用舊韌體，請以 **Flash FW** 手動更新為 S2P-FW 1.0.6；
   已經刷入 1.0.6 的板子，不需要為這次桌面版本更新再次刷寫。
4. `.zip.sha256` 附件可核對 ZIP；包內 `SHA256SUMS.txt` 列出各檔案校驗碼。
