# S2P-XInput-Lite

S2P-XInput-Lite 可在 Windows 上將 Switch 2 Pro Controller 轉換成 Xbox 360 相容的 XInput 控制器，支援 ESP32-S3 USB 橋接與 Windows 原生 BLE。

> 本專案為非官方社群作品，與 Nintendo、Microsoft、Espressif Systems 無關。

## 主要功能

- 自動偵測 ESP32-S3，找不到時改用原生 BLE
- 透過 ViGEmBus 輸出虛擬 Xbox 360 控制器
- 按鍵、鍵盤、滑鼠及搖桿方向映射
- 每支控制器獨立的搖桿與動作感測器校正
- 搖桿曲線、死區、平滑、防抖及輸出形狀調整
- XInput 震動轉換為 HD Rumble 2，並支援音訊反應震動
- 陀螺儀映射至 Xbox 搖桿或滑鼠
- 繁體中文／英文介面
- 即時顯示連線、電量、ESP32 與 ViGEmBus 狀態

各項設定的詳細說明可直接查看程式內的 `?` 提示。

## 系統需求

- Windows 10 或 11
- Switch 2 Pro Controller
- ViGEmBus 1.22.0
- 藍牙，或相容的 ESP32-S3 橋接器（選用）

發佈版已包含攜帶版 Python 與必要依賴，不必另外安裝 Python。

## 安裝與使用

1. 尚未安裝 ViGEmBus 時，執行 `Install-ViGEmBus.bat`。
2. 使用原生 BLE 時，先在 Windows 配對手把；使用 ESP32 時，接上已刷入相容韌體的裝置。
3. 執行 `S2P-XInput-Lite.exe`。
4. 在 GUI 調整並儲存設定。
5. 啟動連線程式，再喚醒手把。

程式會優先尋找 ESP32，找不到時自動改用原生 BLE。遊戲期間請保持連線程式執行。

設定及每支手把的校正資料會儲存在 `config.ini`。

## 使用提醒

- 搖桿或陀螺儀校正請從 GUI 啟動，並依照畫面提示操作。
- 修改映射後請儲存設定，並重新啟動連線程式再測試。
- 將 LF 與 HF 震動強度都設為零即可關閉震動。
- 刷完 ESP32 韌體後，請重新插拔或重啟 ESP32-S3。

## 目前限制

- 僅支援 Windows
- 一次連接一支手把
- 不支援 Joy-Con 配對與 PS5 控制器模擬
- XInput 不支援原始動作感測器，因此陀螺儀只能映射至搖桿或滑鼠
- ESP32 連線需使用相容的橋接韌體

## 上游專案與授權

本專案部分程式碼源自或參考 TommyWabg 的 [Switch2Connect](https://github.com/TommyWabg/Switch2Connect)，並已針對本專案修改及重整。

S2P-XInput-Lite 採用 [GNU General Public License v3.0](LICENSE)。第三方元件與來源說明請參閱 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
