# S2P-XInput-Lite

S2P-XInput-Lite 可在 Windows 上將 Switch 2 Pro Controller 轉換成 Xbox 360 相容的 XInput 控制器，支援 USB 有線、ESP32-S3 USB 橋接與 Windows 原生 BLE。

目前版本：**v0.4.1**  
[版本更新說明](RELEASE_NOTES_v0.4.1.md)

> 本專案為非官方社群作品，與 Nintendo、Microsoft、Espressif Systems 無關。

## 主要功能

- 自動優先偵測 USB 有線手把，其次使用 ESP32-S3 或原生 BLE，並即時顯示六軸／九軸感測狀態
- 透過 ViGEmBus 輸出虛擬 Xbox 360 控制器
- 低延遲輸入分派：保留每次按鍵邊緣，同時只處理最新的搖桿與動作資料
- 按鍵、鍵盤、滑鼠及搖桿方向映射
- 每支控制器獨立的搖桿與動作感測器校正
- 搖桿曲線、死區、平滑、防抖及輸出形狀調整
- USB、ESP32 與原生 BLE 使用一致的 XInput 至 HD Rumble 2 轉換，並支援 latest-only 排程、停止優先與音訊反應震動
- 陀螺儀映射至 Xbox 搖桿或滑鼠
- 繁體中文／英文介面
- 即時顯示連線、電量、ESP32、ViGEmBus、WASAPI 與 HidHide 狀態

各項設定的詳細說明可直接查看程式內的 `?` 提示。

## 系統需求

- Windows 10 或 11
- Switch 2 Pro Controller
- ViGEmBus 1.22.0
- USB 有線選用：[最新版 HidHide](https://github.com/nefarius/HidHide/releases/latest)，用來向遊戲隱藏實體 HID
- USB-C 傳輸線、藍牙或相容的 ESP32-S3 橋接器

發佈版已包含攜帶版 Python 與必要依賴，不必另外安裝 Python。

## 安裝與使用

1. 尚未安裝 ViGEmBus 時，執行 `driver\Install-ViGEmBus.bat`。
2. USB 有線若希望遊戲只看到虛擬 Xbox 手把，請另外安裝 HidHide；ESP32 與原生 BLE 不需要 HidHide。
3. 請先接上手把再開啟 Steam 或其他手把工具。使用原生 BLE 時，**不要先透過 Windows 藍牙設定配對手把**；只需開啟 Windows 藍牙，再由本程式建立連線。使用 ESP32 時，請接上已刷入相容韌體的裝置。
4. 執行 `S2P-XInput-Lite.exe`。
5. 在 GUI 調整並儲存設定。
6. 啟動連線程式，再喚醒手把。

程式依序使用 USB 有線、ESP32、Windows 原生 BLE。遊戲期間請保持連線程式執行。

未安裝 HidHide 時，程式會提供前往官方下載頁面的選項；略過不會阻止連線。偵測到 USB 手把且已安裝但尚未設定 HidHide 時，程式會先詢問，再將攜帶版 `runtime\python.exe` 與目前選中的 Nintendo 實體 HID 加入設定。接受後可能會開啟 HidHide 全域隱藏；其他程式原有的 HidHide 清單會保留。按下 **還原預設** 會移除本程式與目前手把的 HidHide 設定，但保留無關項目；若手把仍在隱藏中，請先接上 USB 再還原。

原生 BLE 第一次配對時，請先啟動連線程式，再按住手把的 **SYNC**；已配對過的手把通常按任意鍵即可喚醒。

設定及每支手把的校正資料會儲存在 `src/config.ini`。檔案不存在時會由 `src/default_config.ini` 自動建立；舊設定缺少新版欄位時只會補齊，不會覆蓋原值。發佈包必須保留 `default_config.ini`，乾淨發佈時可以不附帶 `config.ini`。

## 已驗證低延遲路徑

- USB 有線輸入：250 Hz、固定 4 ms 報告間隔；震動並行壓測時 queue／丟棄皆為零。
- ESP32 輸入：約 132 Hz；低延遲韌體將正常到達間隔 p50／p95 從 7.986／8.059 ms 降至 7.500／7.595 ms。
- Windows 原生 BLE：66.7 Hz／15 ms，符合 WinRT `throughput_optimized` 可要求的最低間隔。
- 三種傳輸的震動皆採 latest-only。ESP32 與 USB 有線優先節拍約 8 ms；原生 BLE 為 15 ms；USB 音訊／Mix 普通更新為 25 ms。

可重複執行的硬體測試與 BAT 啟動檔位於 `tests/`。

## 使用提醒

- 搖桿或陀螺儀校正請從 GUI 啟動，並依照畫面提示操作。
- 修改映射後請儲存設定，並重新啟動連線程式再測試。
- 將 LF 與 HF 震動強度都設為零即可關閉震動。
- HD Rumble 2 預設命令值為 LF `225`、HF `481`；三種連線的連線與 Pin 提示皆使用相同的兩段震動。
- 刷完 ESP32 韌體後，請重新插拔或重啟 ESP32-S3。
- 狀態列會在磁力計持續有效時顯示九軸；只有陀螺儀與加速度計有效時顯示六軸。
- HidHide 設定失敗時，請先關閉 HidHide Configuration Client 再重試；修改隱藏設定後請重開 Steam／遊戲，若仍看到原始手把則重新插拔 USB。

## 目前限制

- 僅支援 Windows
- 一次連接一支手把
- 不支援 Joy-Con 配對與 PS5 控制器模擬
- XInput 不支援原始動作感測器，因此陀螺儀只能映射至搖桿或滑鼠
- ESP32 連線需使用相容的橋接韌體
- 發佈包已包含隨附 `0.12.4` 協定／`cdc_bridge_2_lowlatency` build 的完整 ESP32-S3 原始碼，位於 `esp32s3/source/esp32s3_usb_bridge_bluedroid`
- HidHide 未隨發佈包附帶，ESP32 與 BLE 也不需要它；未安裝時 USB 輸入仍可使用，但遊戲可能同時看到實體 HID 與虛擬 XInput。
- 若 USB 有線狀態顯示「基本模式」，請完全退出 Steam 或其他手把工具，重新插拔手把後先啟動本程式
- USB 有線會固定要求手把完整感測報告；六軸／九軸文字顯示的是實際收到的資料，不是可選的輪詢模式

## 上游專案與授權

本專案部分程式碼源自或參考 TommyWabg 的 [Switch2Connect](https://github.com/TommyWabg/Switch2Connect)，並已針對本專案修改及重整。

S2P-XInput-Lite 採用 [GNU General Public License v3.0](LICENSE)。第三方元件與來源說明請參閱 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
