# S2P-XInput-Lite v0.7.3

## English

v0.7.3 is a desktop reliability release. The bundled ESP32 firmware remains
S2P-FW 1.0.1.

- Serializes connector restart requests so repeated clicks cannot launch two
  connector processes.
- Prevents duplicate calibration and firmware-detection workflows.
- Keeps the settings window open while esptool is actively flashing firmware
  and cancels pending detection cleanly during normal shutdown.
- Registers the connector shutdown handler before transport startup so cleanup
  still runs when the application closes during device discovery.
- Starts the connector console hidden without removing its graceful
  CTRL+BREAK shutdown support.
- Records launcher source version and SHA-256 hashes during the build. Release
  packaging now rejects missing, stale, or mismatched launcher artifacts.

## 繁體中文

v0.7.3 是桌面端可靠性修正版；隨附的 ESP32 韌體版本維持 S2P-FW 1.0.1。

- 將連接程式重新啟動請求序列化，快速重複點擊不再產生兩個連接程序。
- 防止校正及韌體連接埠偵測流程重複啟動。
- esptool 真正刷寫期間會阻止誤關設定視窗；正常關閉時會取消尚未完成的偵測。
- 在 transport 啟動前註冊關閉訊號，裝置搜尋期間關閉也能執行完整清理。
- 隱藏連接程式的 console，同時保留 CTRL+BREAK 正常關閉能力。
- 建置 launcher 時記錄來源版本及 SHA-256；封裝會拒絕缺少、過期或雜湊不符的 launcher。
