# 擴充工具（Extensions）IPC 協定 v1

[English](extensions-ipc.md)

S2P-XInput-Lite 透過本機 Windows Named Pipe 接收獨立擴充工具（S2P-Extensions，例如巨集、
多設備輸入）的控制器輸入狀態，與實體手把輸入合併後，從 S2P 原本唯一的虛擬 Xbox 控制器輸出。
工具不需要、也不可以直接操作 ViGEmBus。

```
實體手把 → S2P 輸入處理（曲線、陀螺儀、映射層）→ 擴充輸入合併層 → ViGEmBus → XInput
                                                       ↑
                     擴充工具 A、B…（最多 4 個）── Named Pipe（協定 v1）
```

## 架構與生命週期

| 元件 | 位置 | 說明 |
|---|---|---|
| `ExtensionManager` | `src/extension_bridge.py` | 連接程式內建立；依 `config.ini` 啟停伺服器、辨識工具、處理衝突、回報狀態、啟動工具。 |
| `ExtensionPipeServer` | 同上 | 同名 pipe 的多個 instance，每個啟用的工具一個，各自用 overlapped I/O。 |
| `ExtensionInputHub` | 同上 | 掛在 `XInputController` 上的唯一物件，依序合併每個已連線工具。 |
| `ExtensionInputMixer` | 同上 | 單一工具的輸入狀態；每份報告只取一次鎖。 |
| 合併點 | `XInputController.update()` | 報告寫完、`pad.update()` 之前。未啟用時只多一次 `is None` 判斷。 |
| 設定視窗 | `src/gui_sections/extensions.py` | 工具清單、權限、衝突檢查；只寫入全域設定並送出指令。 |

執行緒：每個啟用的工具一條 daemon 執行緒 `S2P-Extensions-IPC`（最多 4 條），只在擴充工具啟用時存在。
每條執行緒阻塞在 `WaitForMultipleObjects`（pipe I/O、共用停止事件、個別中斷事件），不輪詢；
停用或連接程式結束時設定停止事件並 join（實測 < 0.5 秒）。

連接程式指令（GUI 經 `controller_commands` 佇列送出）：

| 指令 | 動作 |
|---|---|
| `extensions_apply` | 重新讀取設定；工具數量改變時重建 pipe，其餘只中斷設定有變動的工具（狀態 `waiting`／`settings_changed`，工具自動重連後取得新權限）。 |
| `extensions_connect[:<tool id>]` | 「開啟」按鈕與系統匣選單使用：工具未連線時啟動它（`.py` 開發工具帶一次性權杖）。 |
| `extensions_disconnect[:<tool id>]` | 中斷並暫停該工具（或全部），立即清除其輸入；目前 UI 不使用，停用請用「啟用／停用」。 |

狀態每 0.5 秒隨 `controller_status.json` 的 `extensions` 欄位發佈：

```json
{"state": "connected", "code": "", "enabled": true, "protocol": 1, "max_tools": 4,
 "tools": {"<id>": {"state": "connected", "code": "", "name": "S2P-Joystick-Macro",
                    "capabilities": ["input"], "sticks": ["left"]}}}
```

連接程式視窗也會在工具連線、中斷、失敗、輸入逾時，或設定變更後等待重連時各印一行訊息。

設定（`config.ini`，全域，不隨方案切換）：

```ini
[extensions]
enabled = true

[extensions.tool.1a2b3c4d]
order = 0
path = C:\Tools\S2P-Joystick-Macro.exe
enabled = true
auto_launch = false
allow_input = true
allow_read = false
```

## 連線與安全

- Pipe 名稱：`\\.\pipe\S2P-XInput-Lite.Extensions`。由 S2P 啟動的工具會在環境變數
  `S2P_EXTENSION_PIPE` 取得名稱、在 `S2P_EXTENSION_TOKEN` 取得一次性權杖。
- 已啟用的工具會在連線開始時（或剛被啟用時）由 S2P 啟動，並帶環境變數 `S2P_EXTENSION_BACKGROUND=1`：
  工具應直接在背景執行（例如縮到系統匣），不要跳出視窗。已在執行的工具不會重複啟動。
- 「開啟」按鈕與系統匣選單會再次啟動工具（不帶背景旗標）；工具應只保留一份執行個體，
  由第二次啟動通知原本那份顯示視窗。Windows 只允許前景程式或它剛啟動的程式把視窗移到最前面：
  第二份由 S2P 啟動所以可以，原本在執行的那份不行。因此第二份要先以原本那份的程序 ID 呼叫 `AllowSetForegroundWindow`
  （由原本那份公布，例如放在具名共享記憶體），再通知原本那份，然後結束；原本那份即使最小化或藏在系統匣，也要還原視窗、移到最前面並取得焦點。
  範例：S2P-Extensions 的 `macro/s2p_macro/single_instance.py`。
- 工具應定期送 `ping`（SDK 每秒一次）偵測 S2P 結束連線，並自動重新連線；權限變更就是靠這個生效。
- 內建的「手把測試」不使用 pipe，由 S2P GUI 直接開啟。
- `FILE_FLAG_FIRST_PIPE_INSTANCE`：其他程式無法搶先建立同名 pipe；名稱被占用時狀態為 `pipe_in_use`。
- `PIPE_REJECT_REMOTE_CLIENTS` 與 DACL `D:P(D;;GA;;;NU)(A;;GA;;;<目前使用者 SID>)`：只允許本機、
  同一位使用者連線。
- 辨識：客戶端行程的映像檔路徑必須等於清單中某個啟用工具的 EXE，或 hello 帶有 S2P 啟動該工具時發出的
  權杖（`.py` 開發用腳本走這條路徑）。其他程式會收到 `unauthorized`。
- 權限分開授予：`input`（提交輸入）預設允許，`read`（讀取控制器狀態）預設關閉，由使用者在清單中逐一開啟。
  工具只會得到「hello 宣告」與「使用者允許」兩者的交集。

## 衝突規則

| 情況 | 處理 |
|---|---|
| 同一個 EXE 重複加入清單 | 設定視窗拒絕加入；載入設定時只保留第一筆。 |
| 啟用超過 4 個工具 | 設定視窗拒絕啟用；第 5 個以後標示「未載入」。 |
| 工具沒有任何權限 | 設定視窗拒絕並在衝突檢查顯示。 |
| 兩個工具要求同一支搖桿 | 後連線的工具收到 `conflict`，畫面顯示「A 與 B 同時要求控制左搖桿」。 |
| 同一個工具重複連線 | 第二個連線收到 `already_connected`，不會搶走原本的連線。 |
| 按鍵、扳機 | 不衝突：按鍵 OR、扳機取最大值。 |

## 訊息格式

Pipe 為 message mode。每則訊息是一個 UTF-8 JSON 物件，建議以 `\n` 結尾；一則訊息內也可放多行
JSON。單則訊息上限 4096 bytes。

### 1. 握手（3 秒內必須送出）

```json
{"type": "hello", "protocol": 1, "name": "S2P-Joystick-Macro", "version": "0.1.0",
 "capabilities": ["input"], "sticks": ["left"], "token": "<選填>"}
```

| 欄位 | 說明 |
|---|---|
| `capabilities` | 要求的權限：`input`、`read`。省略時視為 `["input"]`。 |
| `sticks` | 要獨占控制的搖桿：`left`、`right`。不控制搖桿可省略。 |
| `token` | S2P 啟動工具時提供的權杖；自行啟動的 EXE 不需要。 |

成功：

```json
{"type": "welcome", "protocol": 1, "server": "S2P-XInput-Lite", "server_version": "0.8.2",
 "capabilities": ["input"], "sticks": ["left"], "input_timeout_ms": 1000, "host_pid": 12345}
```

`host_pid` 是 S2P 視窗的程序 ID。要跟著 S2P 一起關閉的工具，可等待該程序結束（例如 `OpenProcess(SYNCHRONIZE)`
加 `WaitForSingleObject`）。只重新啟動 S2P 連線時它不會改變；舊版 S2P 不會提供此欄位。

失敗時 S2P 回覆後中斷連線（最多等 200 ms 讓工具讀到回覆）：

```json
{"type": "error", "code": "conflict", "detail": "left stick is used by another tool"}
```

錯誤代碼：`unauthorized`、`unsupported_protocol`、`handshake_timeout`、`permission_denied`、
`conflict`、`already_connected`、`user_disconnected`、`bad_message`、`message_too_large`。

### 2. 提交輸入（需要 `input`；完整狀態，非增量）

```json
{"type": "state", "buttons": ["A", "LB"], "lt": 0, "rt": 255, "lx": 0, "ly": 32767}
```

| 欄位 | 型別 | 範圍 | 未送出時 |
|---|---|---|---|
| `buttons` | 名稱陣列或 XUSB 位元遮罩整數 | `UP DOWN LEFT RIGHT START BACK L_STK R_STK LB RB GUIDE A B X Y` | 不按任何鍵 |
| `lt`、`rt` | 整數 | 0–255 | 不控制該扳機 |
| `lx`+`ly`、`rx`+`ry` | 整數（必須成對，且已在 hello 宣告該搖桿） | -32768–32767 | 不控制該搖桿 |

每則 `state` 取代該工具上一則。放開所有按鍵請送 `"buttons": []` 或 `release_all`。

**心跳**：工具有任何輸入時，至少每 `input_timeout_ms`（1000 ms）要送一則訊息（重送目前狀態或 `ping`）；
否則 S2P 清除該工具輸入、保持連線，狀態顯示「輸入逾時」，下一則訊息即恢復。沒有輸入的工具可以保持安靜。

### 3. 讀取（需要 `read`）

| 請求 | 回覆 |
|---|---|
| `{"type": "get_controller"}` | `{"type": "controller", "connected": true, "mode": "bluetooth", "battery_percent": 80}` |
| `{"type": "get_input"}` | `{"type": "input", "frame": 1234, "age_ms": 1.8, "buttons": ["A"], "lt": 0, "rt": 0, "lx": 0, "ly": 0, "rx": 0, "ry": 0}` |

`get_input` 回傳 S2P 在合併任何擴充工具「之前」的輸出（已套用曲線、陀螺儀與映射），所以工具不會讀回
自己或其他工具送出的輸入；`frame` 每份實體報告加 1。只有連線中且擁有 `read` 的工具存在時才會擷取。

### 4. 其他

| 訊息 | 動作 |
|---|---|
| `{"type": "release_all"}` | 立即清除該工具的全部輸入。 |
| `{"type": "ping", "seq": 1}` | 回覆 `{"type": "pong", "seq": 1}`；工具需讀取回覆。 |
| `{"type": "bye"}` | 正常結束；S2P 清除該工具輸入並回到等待連線。 |

未知 `type`、無效數值、未宣告的搖桿、超出權限的請求都會得到 `error` 並被中斷，同時清除該工具輸入。

## 合併規則

- **按鍵**：OR。實體或任一工具按住即輸出；全部放開才放開。實體報告每幀重建後才合併，
  所以工具放開或斷線不會誤放開實體仍按住的鍵。工具在兩份實體報告之間「按下又放開」時，
  該按鍵仍輸出一幀，不會遺失短按。
- **扳機**：取實體與所有工具中的最大值，連續變化、不會跳動。
- **搖桿**：每支搖桿最多一個工具（連線時檢查），並以實體優先＋遲滯合併：工具數值只在實體搖桿
  （含曲線與陀螺儀輸出後）靜止時採用；實體幅度 ≥ 12% 立即由玩家接手，降到 6% 以下並維持
  0.15 秒才交還工具。工具送出中立值（0, 0）不會蓋掉玩家正在推的搖桿。
- **斷線、逾時、崩潰**：立即清除該工具狀態，下一份實體報告即恢復；其他工具不受影響。

合併頻率跟隨實體手把的報告率；工具送出的狀態會在下一份實體報告（通常 4–8 ms 內）生效。
S2P 不會因工具而增加第二套輸出或額外的 `pad.update()`，也不等待工具回覆。

## 流量保護

連續訊息可立即處理；若持續超過約 2000 則／秒（每 50 ms 超過 100 則），該工具的執行緒會在時間窗剩餘時間
內暫停讀取並釋放 GIL，避免異常工具影響實體輸入執行緒。Pipe 緩衝區為 4 KB，堆積的舊訊息有上限，
暫停結束後會依序處理到最新狀態。

## 工具清單檔與更新

工具必須在 EXE 旁附上以 EXE 命名的清單檔：`S2P-Joystick-Macro.exe` → `S2P-Joystick-Macro.s2p.json`
（UTF-8 JSON，最大 64 KB）。擴充工具視窗的 **檢查更新** 會讀取它；沒有清單檔的工具會顯示「未提供更新資訊」。

```json
{"format": "s2p-extension", "name": "S2P-Joystick-Macro", "version": "0.1.0",
 "update_url": "https://github.com/duoduo-88/S2P-Extensions/releases/latest/download/s2p-extensions-updates.json",
 "homepage": "https://github.com/duoduo-88/S2P-Extensions"}
```

| 欄位 | 說明 |
|---|---|
| `format` | 固定為 `s2p-extension`。 |
| `name` | 工具名稱；多工具更新來源以它為索引。 |
| `version` | 已安裝的版本，格式 `MAJOR.MINOR.PATCH`，請與 `hello` 回報的版本一致。 |
| `update_url` | 更新來源的 HTTPS 網址。可省略；省略時顯示「未提供更新資訊」。 |
| `homepage` | 選用的 HTTPS 網頁；更新來源沒有下載頁時改開這個網址。 |

更新來源是一份 JSON 文件（最大 256 KB），可描述單一工具，或以 `name` 為索引描述多個工具：

```json
{"format": "s2p-extension-updates", "version": "0.1.1", "url": "https://example.com/releases/0.1.1"}
```

```json
{"format": "s2p-extension-updates",
 "tools": {"S2P-Joystick-Macro": {"version": "0.1.1",
                                  "url": "https://github.com/duoduo-88/S2P-Extensions/releases/tag/v0.1.1"}}}
```

- 只有在使用者按 **檢查更新** 時才會讀取更新來源；多個工具共用同一個 `update_url` 時只下載一次。
- 只使用 `https` 網址。S2P 不會下載或執行工具，只在狀態欄顯示「有更新：vX.Y.Z」，並詢問是否用瀏覽器開啟 `url`。
- 更新來源的 `version` 比清單檔新即視為有更新；更新來源無法連線、格式錯誤或沒有列出此工具時，顯示「更新檢查失敗」。
- 把更新來源放在 GitHub release 的附檔，`update_url` 指向 `/releases/latest/download/<檔名>`，就會自動跟著最新版 release。

## 撰寫獨立工具（最小範例）

只需標準函式庫；完整參考實作見 `tests/extension_ipc_test_client.py`（僅供開發，不隨發行版提供）。
正式的共用 IPC Client 見 [S2P-Extensions](https://github.com/duoduo-88/S2P-Extensions) 的 `sdk/s2p_ext`。

```python
import json, os, threading

pipe_name = os.environ.get("S2P_EXTENSION_PIPE", r"\\.\pipe\S2P-XInput-Lite.Extensions")
pipe = open(pipe_name, "r+b", buffering=0)          # 每次 write() 為一則訊息
lock = threading.Lock()

def send(message):
    with lock:
        pipe.write((json.dumps(message) + "\n").encode("utf-8"))

hello = {"type": "hello", "protocol": 1, "name": "S2P-Joystick-Macro", "version": "0.1.0",
         "capabilities": ["input"]}
if os.environ.get("S2P_EXTENSION_TOKEN"):
    hello["token"] = os.environ["S2P_EXTENSION_TOKEN"]
send(hello)
assert json.loads(pipe.read(4096))["type"] == "welcome"

send({"type": "state", "buttons": ["A"]})            # 按住 A（按住期間每 250 ms 重送一次）
send({"type": "state", "buttons": []})               # 放開
send({"type": "bye"})
pipe.close()
```

建議：

- 每個按鍵狀態至少維持 16 ms，確保遊戲以 60 FPS 讀取時一定看得到。
- 按住期間每 250 ms 重送一次目前狀態作為心跳。
- 連線失敗（`ERROR_PIPE_BUSY` 或 S2P 未執行）時每 0.5–1 秒重試即可。
- 只宣告真正需要的權限與搖桿，減少與其他工具衝突。
- 正式發佈請打包成獨立 EXE，讓使用者把該 EXE 加入「擴充工具」清單（辨識依據就是這個路徑）。

## 測試

```powershell
& ".\runtime\python.exe" ".\tests\run_tests.py"   # 包含 test_extension_bridge.py
```

手動驗證：在「擴充工具」清單加入 `tests\extension_ipc_test_client.py`，選取後按「開啟」，S2P 會以
開發模式啟動客戶端並執行 A、A+B、X、Y 與左搖桿示範；用現有的「手把測試」觀察合併後的 XInput 輸出。
