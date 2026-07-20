# S2P-XInput-Lite

S2P-XInput-Lite 可在 Windows 將 Switch 2 Pro Controller 轉換為 Xbox 360 相容的 XInput 控制器，支援 USB 有線、ESP32-S3 USB 橋接器及 Windows 原生 BLE。

目前版本：**v0.5.0**  
[版本更新說明](RELEASE_NOTES_v0.5.0.md)

> 本專案是非官方社群作品，與 Nintendo、Microsoft 或 Espressif Systems 無關。

## 主要功能

- 自動依序使用 USB 有線、ESP32-S3 或原生 BLE，並即時顯示 6 軸／9 軸感測狀態
- 透過 ViGEmBus 輸出 Xbox 360 虛擬控制器
- 低延遲輸入處理：保留按鍵邊緣事件，同時使用最新的搖桿與動作感測報告
- 按鍵、鍵盤、滑鼠及搖桿方向映射
- 全域 Mapping Layers：支援按住／切換啟用、個別按鍵與搖桿覆寫、排序及匯入／匯出
- 每支控制器獨立的搖桿與動作感測校正
- 搖桿曲線、死區、平滑、穩定化與輸出形狀調整
- USB、ESP32 與原生 BLE 共用一致的 XInput 至 HD Rumble 2 轉換，包含 latest-only 節流、優先停止訊框及音訊震動
- 將陀螺儀映射至 Xbox 搖桿或滑鼠
- 完整遊戲設定檔，可一起切換搖桿、陀螺儀、震動、音訊觸覺與映射設定
- 繁體中文與英文介面
- 即時顯示連線、電量、ESP32、ViGEmBus、WASAPI 及 HidHide 狀態

應用程式內各設定旁的 `?` 按鈕提供詳細說明。

## 系統需求

- Windows 10 或 11
- Switch 2 Pro Controller
- ViGEmBus 1.22.0
- USB 有線模式可選用最新版 [HidHide](https://github.com/nefarius/HidHide/releases/latest)，避免遊戲偵測實體 HID
- USB-C 資料線、Bluetooth，或相容的 ESP32-S3 橋接器

發佈包已包含可攜式 Python 執行環境及所需套件，不必另外安裝 Python。

## 安裝與使用

1. 若尚未安裝 ViGEmBus，執行 `driver\Install-ViGEmBus.bat`。
2. USB 有線模式若希望遊戲只看到虛擬 Xbox 控制器，可另外安裝 HidHide；ESP32 與原生 BLE 不需要 HidHide。
3. 請先連接控制器，再開啟 Steam 或其他控制器工具。原生 BLE 模式請勿在 Windows 藍牙設定內手動配對；只需開啟藍牙，讓本程式建立連線。ESP32 模式則接上已燒錄相容韌體的裝置。
4. 執行 `S2P-XInput-Lite.exe`。
5. 選擇遊戲設定檔或調整設定，再使用 **儲存設定檔** 或 **另存新設定檔**。
6. 儲存或切換設定檔會在連線中立即套用遊戲設定。只有變更連線方式、裝置或序列埠設定時才需要 **重新啟動**。

程式會先檢查 USB 有線，再檢查 ESP32，最後使用 Windows 原生 BLE。遊玩期間請保持連線程式開啟。

第一次使用原生 BLE 配對時，啟動連線後按住控制器的 **SYNC** 鍵。已配對的控制器通常按任意鍵即可喚醒。

若未安裝 HidHide，程式會詢問是否開啟官方下載頁；略過不會阻止連線。USB 控制器已連接且 HidHide 尚未設定時，程式會先詢問，再將可攜式 `runtime\python.exe` 與選定的 Nintendo HID 加入 HidHide；其他應用程式既有的 HidHide 項目會保留。**恢復預設值** 只會移除本程式與選定控制器的項目，並保留無關項目。

## 設定檔與 Mapping Layers

設定及每支控制器的校正資料儲存在 `src/config.ini`。檔案不存在時，程式會以 `src/profiles/System Default.ini` 建立；舊設定缺少的欄位會自動補齊，不會覆寫既有值。每個發佈包都必須保留 `System Default.ini`，乾淨發佈包則可省略 `config.ini`。

首次啟動提供 General、Audio、FPS-COMP、FPS-IMM、Racing、Action 與 Rhythm 設定檔。選擇設定檔會更新介面，並在不中斷連線的情況下套用。自訂設定檔可儲存、重新命名或刪除；**System Default** 是受保護的唯讀基準，固定顯示於清單最下方。**恢復預設值** 不會刪除設定檔或校正資料。

Mapping Layers 是全域設定，可在按住按鍵組合時暫時覆寫目前設定檔，也可切換為持續啟用。Layer 可重新映射按鍵與搖桿方向、交換左右搖桿、用搖桿控制滑鼠，或輸出線性的 XInput 扳機／搖桿方向。優先權依編輯器中的排列順序決定；檔案儲存在 `src/layers`，並支援匯入與匯出。

## 已驗證的低延遲路徑

- USB 有線輸入：250 Hz、4 ms 報告間隔；在同時進行震動壓力測試時未發生排隊或丟棄。
- ESP32 輸入：約 132 Hz；低延遲韌體將正常主機到達時間 p50／p95 從 7.986／8.059 ms 降至 7.500／7.595 ms。
- Windows 原生 BLE：66.7 Hz／15 ms，符合 WinRT `throughput_optimized` 的最低間隔。
- 所有傳輸皆採用 latest-only 震動。ESP32 與 USB 有線的優先節流約 8 ms，原生 BLE 約 15 ms；USB 音訊／Mix 更新為 25 ms。

## 注意事項與限制

- 僅支援 Windows，且一次只支援一支控制器。
- 不支援 Joy-Con 配對或 PS5 控制器模擬。
- XInput 無法輸出原始動作感測資料，因此陀螺儀只能映射至搖桿或滑鼠。
- ESP32 連線需要相容的橋接韌體。發佈包在 `esp32s3/source/esp32s3_usb_bridge_bluedroid` 內含 `0.12.4` protocol／`cdc_bridge_2_lowlatency` 的完整來源。
- HidHide 不隨附，也不是 ESP32 或 BLE 的必要元件。未安裝時 USB 輸入仍可使用，但遊戲可能同時偵測實體 HID 與虛擬 XInput 控制器。
- 若 USB 狀態顯示 Basic 模式，請完整關閉 Steam 或其他控制器工具，重新連接控制器，再先啟動本程式。
- 搖桿或動作感測校正請依 GUI 顯示的步驟操作。
- 將 LF 與 HF 震動強度都設為零即可停用震動；預設 HD Rumble 2 指令為 LF `225`、HF `481`。
- ESP32 韌體燒錄完成後，必須重新連接或重新啟動 ESP32-S3。

## 上游專案與授權

本專案部分內容以 TommyWabg 的 [Switch2Connect](https://github.com/TommyWabg/Switch2Connect) 為基礎或由其衍生，並已為本專案修改與重整。

S2P-XInput-Lite 採用 [GNU General Public License v3.0](LICENSE)。第三方來源及授權資訊請參閱 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
