# S2P-XInput-Lite v0.7.13 Release Notes

## English

Desktop source version: **v0.7.13**. Bundled firmware: **S2P-FW 1.0.6**.
This version update does not create a GitHub Release, tag, or packaged archive.

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
comparison. Clean-build images match byte for byte. Hardware shutdown, sleep
current, rumble and reconnect checks remain pending. Use the updated desktop
source and manually flashed 1.0.6 firmware together. See the
[investigation and hardware checklist](docs/bridge-host-shutdown.md).

## 繁體中文

桌面程式碼版本：**v0.7.13**。隨附韌體：**S2P-FW 1.0.6**。
本次版本更新未建立 GitHub Release、版本標籤或發佈壓縮檔。

- 正常退出時解除 ESP32 橋接 BLE 連線；只有 GUI 崩潰時，也會通知連接子程序關閉。
- 區分韌體接受斷線要求與所有 BLE 連線實際解除，等待後者才確認關閉完成。
- 一般橋接主機工作階段建立後，CDC 連續消失 3 秒或對應心跳逾時 8 秒便開始逐一解除 BLE。
- 主機 watchdog 僅在設定與實際模式皆為一般橋接時啟用。獨立 XInput／USB HID／Auto
  保留原有閒置、喚醒與重連流程；Windows 原生 BLE 及直接 USB 有線維持原有清理順序。
- 修正多手把斷線時無關事件、通道重用與延遲連線完成的競爭；異常終止後重開可等待舊工作階段到期再連線。

橋接修正的 647 項本機測試已全部通過，包含 ESP-IDF 建置與隨附映像比對；乾淨重建
映像逐位元一致。實機斷線、休眠電流、震動與重連仍待驗證。須搭配更新後的桌面程式碼及
手動刷寫 1.0.6 韌體，詳見[調查報告及實機檢查表](docs/bridge-host-shutdown.md)。
