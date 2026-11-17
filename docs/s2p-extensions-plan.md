# S2P-Extensions 規劃

S2P-Extensions 是 S2P-XInput-Lite 的獨立擴充工具專案（預定 Repository：`duoduo-88/S2P-Extensions`，
尚未建立）。原先規劃的 S2P-Macro 併入此專案，巨集只是其中一個工具。

## 分工

| S2P-XInput-Lite | S2P-Extensions |
|---|---|
| 手把連線與輸入處理 | 巨集錄製與回放工具 |
| 控制器參數與映射 | 多設備輸入合併工具 |
| 唯一的 XInput 虛擬控制器輸出 | 其他控制器擴充功能 |
| 擴充工具通訊介面（IPC Server） | 共用 IPC Client 與 SDK |
| 外部輸入合併、衝突檢查 | 擴充工具開發範例 |
| 擴充工具清單 UI | 各工具自己的 UI 與資料儲存 |

S2P 不依賴任何擴充工具即可啟動；未啟用擴充工具時行為與效能維持原狀。

## 目標目錄

```
S2P-Extensions/
├── sdk/            IPC Client 與共用協定（由 tests/extension_ipc_test_client.py 整理而來）
├── macro/          巨集工具
├── multi-device/   多設備輸入工具
├── examples/       最小化擴充範例
├── docs/           擴充工具開發文件（協定見本 repo docs/extensions-ipc_zh-TW.md）
└── README.md
```

尚未實作的功能不先建立空白模組。工具以 Python 開發，正式發佈打包成獨立 EXE。

## 現況（本 repo 已完成）

- IPC Server、多工具清單（最多 4 個同時啟用）、讀寫權限分離、搖桿獨占與衝突檢查、心跳逾時清除。
- 參考客戶端 `tests/extension_ipc_test_client.py`：只用標準函式庫，涵蓋 hello、state、心跳、read 請求。
  建立 S2P-Extensions 時可直接移到 `sdk/`。
- 協定文件 `docs/extensions-ipc_zh-TW.md`（英文版 `docs/extensions-ipc.md`）。

## 後續方向

### A. 巨集工具（macro/，S2P-Joystick-Macro v0.1.0 已完成）

錄製、回放、觸發時間記錄、操作編輯、巨集資料儲存、手把組合鍵觸發播放。錄製時以 `read` 權限的 `get_input` 取得 S2P
合併前的輸出（不會錄到自己回放的輸入），回放時以 `input` 權限送出狀態。不建立虛擬控制器，
不加入任何反作弊規避機制。

### B. 多設備輸入合併（multi-device/）

讓其他實體設備（第二支手把、鍵盤、其他 HID）透過 Extensions 提供額外輸入。按鍵與扳機可直接合併；
搖桿需在 hello 宣告 `sticks`，與其他工具重複時會被 S2P 拒絕並顯示衝突。這是選用工具，不強制安裝。

### C. 多支 Pro 2 獨立控制器（不屬於 Extensions）

每支手把對應獨立 XInput 控制器、獨立校正、方案與震動，需要 S2P 核心支援多工作階段，不能只靠
Extensions 完成。已有「單一 ESP32 多 BLE 連線時回報率約降到 67 Hz」的測試資訊，原因尚未驗證；
若開發此功能，應優先評估多 ESP32 或 Windows 原生多連線，不預設單 ESP32 多連線能維持低延遲。
本次未實作。

## 尚未完成

- S2P-Extensions Repository 本身（依指示未建立或修改任何遠端 Repository）。
- 實機驗證：實體手把＋真實 ViGEm 下的合併、震動與延遲量測（目前只有自動測試與模擬節拍量測）。
- 讀取權限目前只提供請求／回覆；若巨集錄製需要逐幀串流，可在協定 v2 加入訂閱機制。
