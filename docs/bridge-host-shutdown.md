# ESP32 一般橋接關閉／耗電調查與修正

## 基準與結論

2026-10-15（Asia/Taipei）以 `git ls-remote`、`git fetch origin main --no-tags`
核對遠端 main：`c36d1eb7d9c1072c146f1dfc88b1a6e1485a6986`。
桌面版本確為 **v0.7.12**，韌體確為 **S2P-FW 1.0.5**。
修正提交為 `0b3731ed65e64a02de902c53edc779f4c04830b4`，來源分支為
`codex/bridge-host-disconnect`；依後續指示整合至 main 並推送 origin。
橋接修正最初保留桌面 v0.7.12；2026-10-16 依後續指示將桌面版本更新為
**v0.7.13**，並依使用者後續指示準備正式發佈；既有 v0.7.12 Release 附件保留。
韌體更新為 **1.0.6**，原始碼、偵測版本、映像及 SHA-256 同步更新。

**已確認的是連線管理缺口與可重現的軟體競爭，不是實測的電池耗盡。**
使用者於 2026-10-16 回報自行刷寫後初步功能正常；目前沒有實機 BLE trace、
休眠電流或電池續航測量，不能宣稱已證實特定手把的耗電量，
也不能以離線測試通過代替刷寫後的硬體驗證。

## 原始呼叫鏈與證據

1. GUI 的退出流程呼叫 `ConfigGUI.stop_main_process()`，對獨立 console／process group
   的 `main.py` 子程序送 `CTRL_BREAK_EVENT`。`wait(timeout=2)` 到期後以
   `taskkill /PID <child> /T /F` 強制終止。
2. `main.py` 的 `SIGBREAK` handler 只設 `stop_requested`；主迴圈離開後才執行
   `finally → cleanup()`。原順序為音訊、輸入 dispatcher／XInput、telemetry，最後才是實體 transport。
   音訊 join 最多 1 秒、dispatcher 最多 1+1 秒、XInput rumble join 最多 1 秒，
   加上 bridge 自己最多 2.5 秒，已超過 GUI 的 2 秒；鎖等待／GC／排程可能更久。
3. `ESP32Bridge.close()` 先封鎖震動 producer、排序最後的零震動，再送
   `auto off`、`ble disconnect`。`send()` 成功只表示 serial write 沒有拋錯，
   不代表韌體接受，更不代表 BLE 已解除。
4. 原 close 最多等待 0.75 秒的 `_disconnect_event`。這是單一桌面選定通道事件，
   不是全部實體連線的完成通知；原 reader 甚至會讓其他通道的 `disconnected`
   清掉目前通道。初始化失敗／transport failure 也可能設同一個 event。
5. 韌體 `handle_control_command()` 的 `auto` 分支為空操作。`ble disconnect`
   呼叫 `do_disc_all()`，透過 mask 逐一發出非同步 GAP disconnect。
   `cdc_host_ready()` 原本只控制 CDC 傳輸／丟棄輸出；CDC/DTR 消失不會解除 BLE。
   因此 ESP32 繼續供電時，即使 Windows 子程序被殺掉，BLE link 仍可存在。
6. 原 `DISCONNECT_EVT` 無條件清除 `s_disc_in_flight`，無關控制器的遠端斷線
   也能推進佇列。CDC 與 BTC callback 都能發下一個 GAP disconnect，另有
   「保留要求後、實際提交前」跨核心競爭。舊 mask 也沒有通道世代，可能誤斷重用的 slot。
7. `do_disc_all()` 原本允許掃描恢復。**一般橋接沒有韌體自主重連**，所以掃描本身
   不足以證明重連；但仍活著的桌面 reader、延遲 scan_result／conn、非同步 open
   會與退出交錯。不能把 `auto off` 當成防止這些情況的保護。
8. Windows GUI 與 connector 原本是獨立程序。只殺 GUI 時，connector 可繼續執行，
   即使只新增韌體心跳，也不會逾時；需要 GUI 程序生命週期通知。

電池持續耗電的合理解釋是「BLE 未解除且持續初始化／串流」，但手把是否能自行休眠、
在各韌體版本下多久休眠及實際電流，仍屬待量測項目。

## 為何既有閒置斷線無法兜底

- 桌面 `IdleActivityTracker` 由仍在執行的 main 主迴圈檢查，可被使用者設為關閉。
  程序被終止後它不會再送命令；有活動時也不會到期。
- 韌體 `standalone_xinput_idle_disconnect_due()` 使用獨立輸出 runtime 的活動狀態，
  一般橋接不會經過 standalone input 接收流程來建立該追蹤。
- 主機是否存在與使用者是否操作是不同條件。本次保留原閒置行為，另外建立 bridge host lease。

## 修正內容

| 檔案 | 主要變更 |
|---|---|
| `src/config_gui.py` | 正常退出寬限增加到 12 秒；仍保留逾時／訊號失敗的 taskkill；啟動時傳遞 GUI PID |
| `src/main.py` | 只在 ESP32 transport 提早關閉 BLE；其 close 先封鎖 output，再送最後零震動；Windows BLE／USB wired 保留原順序；bridge 監看 GUI 結束；可取消建立工作階段的等待 |
| `src/runtime_cleanup.py` | 使用 Windows SYNCHRONIZE process handle 與無限阻塞等待接收 GUI 結束通知，無輪詢；只由 bridge 啟用 |
| `src/esp32_bridge.py` | 隨機非零 session ID、建立工作階段握手、既有 status worker 的低頻 ping、等待全部連線釋放的專用事件；退出時忽略延遲 connected／scan_result，其他通道斷線不清除目前通道 |
| 韌體 `bridge_host_watchdog.h` | 可離線執行的純 C lease 狀態機，3 秒 CDC loss grace、8 秒 heartbeat timeout，時間差可跨 tick wrap |
| 韌體 `main.c` | 嚴格 bridge gate、停止掃描／阻擋重連、逐一斷線；匹配 BDA+conn_id 的完成事件、generation-scoped mask；bridge 只由 CDC 發出後續 GAP 操作；保留尚未完成 open 的 slot，避免把 timeout 當成實體取消 |
| 版本／映像／文件／測試 | CMake、APP_FIRMWARE_VERSION、desktop detection 改為 1.0.6；更新 application binary、基準 hash 和既有 CI 的版本檢查常數。未新增或執行 Actions |

### 明確區分三個狀態

1. `host end <session>` 寫入成功：**程式已送出要求**。
2. `host_session`、`state=stopping`：**韌體接受要求**，不是完成。
3. 同一 session 的 `host_released`：**所有使用中的 channel slot、斷線佇列及
   非同步掃描停止皆已完成**。只有此事件能確認新韌體的正常退出。

bridge close 最多 6 秒；期限內沒有第三種確認會回報失敗，仍關閉 CDC，
由韌體繼續執行保護。同步 GAP 拒絕會重排；工作階段釋放時若缺少斷線完成事件，
每 2 秒重試，不能因此把 live slot 當成斷線。

`host begin` 成功後才啟用 watchdog。單純開機、等待配對、只查狀態皆不會啟用。
ping 只接受目前 session；釋放後的舊 begin／ping 不會重啟它。新 session 可重新掃描及連線。
異常終止後立即重開，握手最多等 14 秒，容納舊 lease 及多手把釋放；GUI 退出可立即取消。
舊韌體仍使用 scan off／ble disconnect 的相容路徑，但沒有新增的 crash 保護。
**必須使用本次更新的桌面來源與 1.0.6 韌體才能取得完整保護。**

### 模式隔離

啟用條件同時要求 `s_output_mode == STANDALONE_OUTPUT_BRIDGE` 及
`s_active_output_mode == STANDALONE_OUTPUT_BRIDGE`。host begin/ping/end 與
watchdog pump 均在此邊界內；原生 Windows BLE 與直接 USB 完全不經此協定。
獨立 XInput、USB HID、Auto 即使未開 S2P、DTR=false，也不會被新 watchdog 斷線。
獨立模式原本的 idle disconnect、按鍵喚醒、自動掃描／配對、USB output 及 Auto probe 流程保留。
共用斷線佇列的事件匹配／世代修正適用所有模式，但不加入獨立模式 host lease。
模式寫入仍只設定下一次開機；切換 personality 仍需 restart。

## 自動化與建置驗證

- 新增 20 項 unittest（包括兩個執行原始 C 的測試）；先在未修正程式上看到
  GUI 期限、確認事件及多通道問題失敗，再完成修正。
- 完整 `runtime/python.exe tests/run_tests.py`：最終啟用
  `S2P_RUN_IDF_BUILD=1 S2P_VERIFY_RELEASE_IMAGE=1`，**647 項全部通過，無跳過**。
  未設定這兩個環境變數的一般執行為 646 通過、1 個可選建置測試跳過。
- Python `compileall` 及 Ruff correctness 檢查通過。
- C 測試由 MSVC `/std:c11 /W4 /WX` 編譯執行，包含真正的 production header
  及從 `main.c` 擷取的函式；只替換時鐘、USB／ESP-IDF API。不是只檢查字串或模擬 Python 狀態機。
- Windows process wait 包含實際建立暫時 Python 程序、終止它並收到 handle 通知的測試。
- ESP-IDF 5.5.4 本機建置成功；無刷寫。映像比對與乾淨重建結果見下方最終驗證紀錄。

| 必要情境 | 自動化覆蓋 |
|---|---|
| GUI 正常退出、超時、CTRL_BREAK 失敗 | 呼叫真實 GUI stop method，檢查 grace／taskkill；main cleanup AST 實際執行驗證先後順序 |
| 只終止 GUI | mock 與真實 Windows process-handle 通知；主程式只在 bridge 註冊 |
| CDC 消失／DTR false、8 秒無 ping | C 狀態轉移、實際 watchdog pump；包含持續供電的狀態 |
| 短暫 CDC 中斷恢復 | 3 秒內恢復不斷線，之後能繼續同 session ping |
| 正常重新連線／崩潰後重開 | 新舊 session 隔離、握手時間預算、新 session 恢復掃描 |
| 多手把、無關／重複事件 | 真實 queue 函式執行；BDA+conn_id 匹配、序列化、slot 世代重用 |
| open 尚未完成／scan stop 延遲／GAP 拒絕 | 不提前回覆 host_released，重試後才繼續 |
| 獨立 XInput／HID／Auto、模式邊界 | 各 enum 與 configured/active 不同組合、無 CDC 時不碰 BLE、仍允許原掃描路徑 |
| idle disconnect／按鍵重連、零震動與低延遲 | 原有相關測試加 C 的同 session scan resume；未改 input hot path 或新增 task polling |

離線排程測試不等於真的跨核心壓力測試。Windows 驅動的 DTR 行為、RF／HCI 時序、
USB 枚舉、實際震動、手把睡眠與耗電都尚未實測。

## 完整實機驗證清單（初步功能由使用者回報正常，以下矩陣仍待執行）

刷寫前保存配對／設定與目前版本，使用現有 GUI 的刷寫工具由操作者手動更新 1.0.6。
確認實際 flash 容量符合專案要求；本次沒有自動刷寫。

1. **正常 GUI 關閉**：一般橋接配對並持續操作，先測一支、再測兩支及多支手把。
   退出 S2P，記錄「已送出」、「韌體接受」、「全部 BLE 解除」三個訊息；ESP32 保持供電。
   確認所有手把 BLE 消失、停止震動並能休眠。持續觀察並量測休眠電流，不以 LED 熄滅單獨判定。
2. **正常 restart**：反覆啟動／退出，按手把任意鍵喚醒，確認仍能使用既有配對重連、
   輸入、LED、初始化提示震動。同步開始連線時立即關閉，不能留下晚到的 BLE link。
3. **異常終止**：分別只強制結束 GUI、只結束 connector、結束整個程序樹。
   前者應喚起 connector cleanup；後兩者應由 CDC loss（連續 3 秒）或 heartbeat
   timeout（最後 ping 後 8 秒）**開始**釋放；再加順序斷線與 BLE stack 完成時間。
   關閉確認期限逾時必須如實顯示，不可把「要求送出」當成完成。
4. **快速重開／主機凍結**：終止 connector 後 1 秒內重開；應等待舊 owner 釋放並自動繼續。
   另將 connector 暫停，保持 COM 開啟及 DTR=true，驗證無 ping 仍在 8 秒後開始斷線。
5. **短暫 CDC 中斷**：以測試程式在同一 session 切換 DTR=false 1–2 秒後恢復，
   在 8 秒內續送 ping；確認無誤斷。保持 DTR=false 超過 3 秒應斷線。
   USB 拔除測試必須另供電，避免將 ESP32 斷電誤當成 watchdog 成功。
6. **多手把競爭**：host end 過程中讓另一支手把自行斷線，並在掃描／第三支連線途中退出。
   確認所有 link 依序消失、沒有 HCI assert／133 flooding、沒有意外重新掃描或連線。
7. **獨立模式矩陣**：分別設 XInput、HID、Auto 並 restart；關閉 S2P、不開 COM、DTR=false，
   連續操作超過 8 秒（建議 60 秒），確認 BLE、USB 輸入及支援的震動仍正常。
   設非零 idle timeout，驗證原有閒置斷線與按鍵喚醒；Auto 在 Windows／Android 分別驗證。
   開啟再關閉診斷 CDC，不能因 host lease 觸發斷線。
8. **切換模式與延遲**：bridge→各 standalone→bridge，每次依原流程 restart，確認配對保留。
   以原 live input／rumble probes 對照 input rate、P95/P99、按鍵邊緣、零震動與重新連線。
   原生 Windows BLE 及直接 USB wired 也各執行既有連線／關閉／震動 probe。

## 最終驗證紀錄

- 最終完整 suite：啟用 `S2P_RUN_IDF_BUILD=1 S2P_VERIFY_RELEASE_IMAGE=1`，
  647 項全部通過、無失敗或跳過（16.681 秒）；該 IDF 測試另曾單獨執行並通過。
- ESP-IDF 5.5.4 正式建置與從 `sdkconfig.defaults` 生成設定的全新建置皆成功，
  最後重試修正後亦重新建置；三個映像均逐位元相同。
- application SHA-256：`f68c8f1df2001b51464757c80597ca49d6530f94661ee4978c2809d9e286013d`。
- bootloader SHA-256：`0674ee7d6721269bff482811b4441f6a85d6f590afde9f7f71f6e7db39c68e94`。
- partition table SHA-256：`7f00b6c042a89b15b0cac534f82ed988caf29278ff5700b0c511eb1b5bb7c820`。
- `compileall`、Ruff、`git diff --check` 通過。獨立程式審查提出的
  queue ownership、未完成 open 與快速重開問題已修正並加測試；最後覆查無新增重要問題。
- 本機測試記錄在忽略的 `build/bridge-all-tests.log`、`build/bridge-optional-idf-test.log`、
  `build/bridge-idf-final.log`；可用上述指令重跑。
- 本工具未自動刷寫 ESP32、未執行硬體 probe 或量測睡眠電流；使用者自行刷入並回報
  初步功能正常。該回報不等於完整模式矩陣、故障情境或長時間耗電量皆已驗證。
  本機原有未追蹤 `LAUNCHER_BUILD.json` 不納入提交。

## 整合紀錄

依使用者後續指示，將修正與 Markdown 更新合併至 main 並推送 origin；保留來源修正分支。
文件提交與合併提交加入 `[skip ci]`，以略過現有 push／pull_request 工作流程，
維持「不自動執行 GitHub Actions」限制。未修改 Actions 的觸發條件或手動啟動工作流程。
2026-10-16 使用者在自行刷寫並初步確認功能後要求發佈 v0.7.13，發佈採本機建置、
驗證 ZIP 與校驗檔後上傳 GitHub Release；不執行 GitHub Actions。完整硬體驗證
仍以本文件的待驗證清單為準。
