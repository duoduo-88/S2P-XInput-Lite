# USB HID 標準化與獨立模式簡化設計

狀態：使用者已於 2026-10-20 指示開始；桌面版本確認為 v0.7.14。

## 目的與邊界

ESP32-S3 僅保留 CDC Bridge 與 CDC＋標準 HID Gamepad，採用 Espressif/TinyUSB
對應類別的共享預設識別碼，移除實體 USB XInput 與 Auto 探測模式。
Windows 桌面經 ViGEmBus 產生 XInput 的路徑、映射、震動與音訊震動必須保留。
維持單一韌體 BIN 與現有 profile runtime；不進行無關的大規模重構。

本次交付為程式、測試、重新編譯的韌體、可驗證的本機發行包及 Draft PR。
不合併、不部署、不建立正式 GitHub Release，不自動對實體裝置刷入韌體。

## 已核對的依賴與官方規則

`dependencies.lock` 鎖定 ESP-IDF 5.5.4、esp_tinyusb 1.7.6~2、tinyusb 0.19.0~3。
該版本 `usb_descriptors.c` 的 PID 計算為
`0x4000 | CDC<<0 | MSC<<1 | HID<<2 | MIDI<<3 | AUDIO<<4 | VENDOR<<5`。
CDC=1、HID=1、其餘為零時結果為 0x4005；僅 CDC 時為 0x4001。

官方允許使用 Espressif VID 與 TinyUSB 標準類別預設 PID；這不代表專屬 PID、
USB 認證、商標許可或 Espressif 背書。

- https://docs.espressif.com/projects/esp-iot-solution/en/latest/usb/usb_overview/usb_vid_pid.html
- https://github.com/espressif/usb-pids/blob/main/README.md

## 影響範圍

| 區域 | 檔案／責任 |
| --- | --- |
| USB 與映射 runtime | `main/standalone_xinput.c/.h`：描述符、報告、模式、組合鍵、遷移；保留共用演算法 |
| 韌體整合 | `main/main.c`：啟動、能力／狀態回覆、模式命令、重啟、Bridge host lease |
| Profile 儲存 | `main/standalone_profile_store.c/.h`：保留 A/B、CRC、校正與 runtime 載入 |
| 桌面操作 | `src/gui_sections/footer.py`、`src/config_gui.py`、`src/standalone_profile.py`：選項、傳輸、重連、遷移提示 |
| 裝置身分 | `src/esp32_detection.py`、`src/gamepad_devices.py`、`src/gamepad_test_window.py`、`src/firmware_flash_guard.py` |
| 語言與說明 | `src/localization.py`、中英文 README、USER_GUIDE、韌體及 scripts README、新版 Release Notes |
| 版本／發行 | `src/version.py`、韌體及桌面版本契約、`scripts/build_firmware.ps1`、package／verify 腳本、正式 bundle 的 BIN 與 SHA-256 |
| 回歸 | 模式、身分、刷入、profile、GUI、翻譯、release、桌面輸出／映射／震動既有及新增測試 |

韌體路徑均相對於 `esp32s3/source/esp32s3_usb_bridge_bluedroid/`。

## USB 配置

| 模式 | VID:PID | 介面 | 產品名稱 |
| --- | --- | --- | --- |
| Bridge | 303A:4001 | CDC 控制 0、CDC 資料 1 | S2P ESP32 Bridge |
| HID | 303A:4005 | CDC 控制 0、CDC 資料 1、HID 2 | S2P Mobile Gamepad |

維持 CDC IAD 與裝置層 Misc/Common/IAD 類別配置。CDC notification 0x81、
CDC OUT/IN 0x02/0x82、HID interrupt IN 0x83；保留現有 HID 13-byte input report。
保留目前 Android 按鍵 usages、hat null 值、四軸、獨立類比扳機語義。
不新增 HID 震動 output report，不承諾 Android 震動。

編譯期保留 CDC/HID 支援，由啟動模式選擇實際描述符與對應 PID；不可直接用全域
CFG_TUD_HID=1 計算 Bridge PID。移除 vendor-specific XUSB 介面、XUSB20、
Microsoft OS Auto 探測、專屬 OUT endpoint／driver、RTC Auto 切換旗標與重列舉分支。
允許保留共用內部按鍵／軸資料結構及既有檔名，避免因名稱而誤刪 HID 使用的處理。
裝置序號改用晶片穩定且唯一的身分；Bridge/HID 可關聯至同一實體裝置。

## NVS 遷移與模式命令

保留既有儲存數值語義：0=Bridge、2=HID；1=XInput 與 3=Auto 僅作舊資料解碼。
開機先安全正規化，0→0、2→2、1/3→2；未知值／讀取錯誤以 Bridge 啟動。
涵蓋舊 `standalone`、`usb_hid` 布林鍵；未設定時預設 Bridge。

遷移不主動重啟。寫入或 commit 失敗時仍以安全的記憶體模式提供 USB，下一次啟動
可再次遷移；只有新值可靠提交後才清除舊來源，不能形成重啟迴圈。
保存已移除模式的遷移來源，在能力／狀態回覆提供桌面可讀的遷移資訊。
桌面顯示「獨立 PC XInput／Auto 已取消，已改為 Android USB HID；桌面 XInput
請切回 Bridge」；提示讀取不能遺失或再次啟用舊模式。

新命令只接受 `bridge`、`standalone_hid`；舊 `standalone`／`standalone_auto`
回覆結構化 unsupported 錯誤，不能默默啟用別的模式。
能力宣告保留 Bridge、profile write/runtime、HID，停用 XInput／Auto 能力。
桌面對舊韌體仍可辨識、升級與切回 Bridge，但不再提供啟用已移除模式的入口。

## 救援與重連

HOME+A 長按 3 秒、放開後可切 HID；原 HOME+X 改為長按 3 秒、放開後切 Bridge。
刪除 HOME+Y Auto 切換。組合鍵直接讀原始輸入，在映射之前處理，不受 profile、
映射層或搖桿校正影響；pending 必須使用獨立旗標，不能再把 Bridge 當成無請求值。
同模式不重啟；切換持久化失敗不重啟並保留 CDC，成功後才執行受控重啟。
保留 CDC `mode bridge` 與 ROM Download Mode；未連接手把時仍可用這兩種救援。
說明文件需區分手把組合鍵、CDC 與實體 BOOT/RESET 的適用條件。

## 桌面識別與 UI

唯一啟用入口為「寫入並啟用 Android USB HID 獨立模式」，另保留切回 Bridge。
`config_gui.py` 的預設目標、確認、成功提示、工作序列化與校正選擇同步調整。
既有校正、曲線、死區、映射層、陀螺儀轉搖桿、idle disconnect、重連繼續沿用。

VID/PID 與產品資訊只用於候選篩選。接受為 S2P 前必須收到相符的韌體產品、協定、
profile/build 及相容能力回覆；共享 PID 無回覆時不可合成 S2P 身分。
HID 特殊解析需透過 USB parent/container 或穩定序號關聯到已驗證的 CDC 裝置，
不能將另一個 S2P 的存在當成驗證。無法關聯時保留一般 HID 行為。
Windows pyserial 未提供 USB product 欄位時，以必要的 S2P-FW 產品／協定／profile／
build／能力回覆加上與 USB port 相符的唯一序號驗證；有提供但不相符的 USB product
必須拒絕。舊版 Bridge 升級例外限制為已知舊版本、固定舊序號及完整韌體身分回覆，
不可用該固定序號關聯 HID 或恢復重連交易。Raw HID 仍要求實際 USB 產品字串。
舊 CAFE:4020/4021/4022 僅作升級候選，亦不可跳過身分驗證。
Profile commit 重連需驗證身分與預期裝置，再確認長度及 CRC。
ROM 刷入救援依既有 esptool 晶片識別及刷入保護處理，不能要求 ROM 回覆應用協定。

## 版本、發行與文件

目標版本：桌面 0.7.14、S2P-FW 1.0.7；保留 s2p_bridge 1.0.0 與既有 profile schema。
能力與模式縮減在新版 Release Notes 明確說明，歷史 Release Notes 不回溯改寫。
更新中英文說明、描述符／依賴規則、遷移、救援、手機震動限制及實機待測清單。
重新產生三個 bundle BIN、版本標記、launcher metadata 及發行 SHA-256；
保留 upstream 歷史映像，使用既有 package／verify 流程驗證本機發行包。

## 驗收與證據

新增能執行 production 邏輯的 NVS／模式測試，涵蓋所有舊模式、舊鍵、損壞值、
提交失敗及重複開機；驗證組合鍵與切換不會進入重啟迴圈。
新增描述符解析檢查，驗證 PID、長度、介面、IAD、端點、HID report layout，
並從此次實際編譯輸出取得描述符證據，不能只對文字常數做成功宣告。
測試非 S2P 的 303A:4005、無身分回覆、重連 COM 改變、多裝置及舊韌體升級候選。
執行 `runtime/python.exe tests/run_tests.py`、ESP-IDF 編譯及發行包驗證；
保留失敗／跳過項目與原因，不將 mock 的 Windows 輸出測試當成實體 ViGEm 驗證。

實機待測：Windows 枚舉／Bridge XInput／ViGEmBus／按鍵映射／震動／音訊震動；
Android 按鍵、雙搖桿、十字鍵、類比扳機及遊戲相容性；USB 拔插、睡眠、remote wake、
idle disconnect、BLE 重連、實際 NVS 升級、組合鍵與 ROM Download 救援。
未操作硬體前，以上一律標示待測。Draft PR 附功能差異、測試結果與限制。
