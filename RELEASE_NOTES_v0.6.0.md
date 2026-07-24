# S2P-XInput-Lite v0.6.0

Release date: 2026-07-24

## English

### Highlights

- Adds ESP32 standalone PC XInput-compatible and mobile USB HID output modes.
- Adds persistent SYNC pairing so controllers reconnect after wake or ESP32 reboot without pairing again.
- Adds atomic A/B standalone-profile storage with schema validation and fallback recovery.
- Moves input processing out of BLE callbacks and adds callback P95/P99 timing diagnostics.
- Adds BLE connection watchdogs, checked scan transitions, complete failure recovery, and generation-scoped input, ACK, and rumble queues.
- Makes desktop pairing and controller initialization wait for matching command and subcommand acknowledgements.
- Improves transport shutdown, compatibility blocking, per-controller calibration selection, and idle disconnect handling.
- Splits the ESP32 firmware into focused profile-store, callback-metrics, standalone runtime, and Fusion modules.
- Includes stable ESP32 firmware `0.14.0`; the bundled application and firmware pass 181 automated tests and a complete ESP-IDF 5.5.4 build.

### Updating

1. Extract this release to a new folder; do not overwrite a running installation.
2. To retain personal settings, copy the old `src/config.ini` into the new release after extraction.
3. Flash the bundled ESP32 firmware before using standalone output.
4. For first pairing, hold the controller **SYNC** button. Later wakeups reconnect automatically.

## 繁體中文

### 重點更新

- 新增 ESP32 PC XInput 相容獨立模式與手機 USB HID 模式。
- 新增持久 SYNC 配對；手把喚醒或 ESP32 重啟後不必重新配對。
- 新增具 schema 驗證、A/B 原子寫入與備援讀取的獨立模式設定檔儲存。
- 將輸入演算法移出 BLE callback，並新增 callback P95／P99 延遲診斷。
- 新增 BLE 建連 watchdog、掃描錯誤復原，以及依連線世代隔離的輸入、ACK 與震動佇列。
- 桌面配對與控制器初始化現在逐步等待 command 與 subcommand 均相符的 ACK。
- 改善關閉流程、相容性阻擋、每支控制器校正選擇與閒置斷線。
- ESP32 韌體拆分為設定檔儲存、callback 指標、獨立模式 runtime 與 Fusion 模組。
- 隨附正式 ESP32 韌體 `0.14.0`；本版通過 181 項自動測試及完整 ESP-IDF 5.5.4 建置。

### 更新方式

1. 將新版解壓縮到新資料夾，不要覆蓋正在執行的舊版。
2. 若要保留個人設定，解壓縮後將舊版 `src/config.ini` 複製到新版。
3. 使用獨立輸出前，先燒錄本版隨附的 ESP32 韌體。
4. 第一次配對時按住手把 **SYNC**；之後喚醒即可自動重連。
