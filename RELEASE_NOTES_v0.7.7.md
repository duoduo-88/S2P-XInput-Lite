# S2P-XInput-Lite v0.7.7

## English

v0.7.7 improves native Windows Bluetooth stability and standardizes the
built-in rumble defaults.

- Replaces the unsafe PyBluez local-adapter address lookup with the Windows
  Runtime Bluetooth adapter API, preventing the native access violation seen
  when ESP32 fallback starts.
- Sets the maximum rumble amplitude to 500 in every bundled profile and in the
  ESP32 standalone default while preserving existing profile content.
- Keeps the original ESP32 bridge connection schedule: 7.5 ms for one or two
  controllers and 15 ms when three or more controllers are ready.
- Detects an ESP32-S3 ROM flashing port by its USB identity even when that COM
  port was already present, avoiding repeated reset attempts and COM-number
  churn.
- Checks the connected ESP32 firmware version before flashing, shows the
  installed and bundled versions for confirmation, and resumes the connector
  automatically when flashing is cancelled.
- Starts the desktop connector automatically after a successful flash or after
  returning from ESP32 standalone mode to bridge mode, then searches for the
  controller again.
- Rebuilds the bundled ESP32-S3 bridge firmware with these defaults.
- Enables reproducible ESP-IDF firmware builds so release-image hashes can be
  verified reliably across clean rebuilds.
- Passes all 551 automated regression tests; one optional full ESP-IDF rebuild
  test remains opt-in.

## 繁體中文

v0.7.7 改善 Windows 原生藍牙的穩定性，並統一內建震動預設值。

- 改用 Windows Runtime 藍牙介面取得本機藍牙位址，取代不安全的 PyBluez
  查詢，避免拔除 ESP32 並啟動原生藍牙時發生原生存取違規而關閉程式。
- 所有內建方案與 ESP32 獨立模式的最大震動振幅統一為 500，同時保留既有
  方案的其他內容。
- 保留原本的 ESP32 橋接連線節奏：一至兩支手把使用 7.5 ms，三支以上手把
  就緒時使用 15 ms。
- 依 USB 身分直接辨識 ESP32-S3 ROM 刷機連接埠，即使按下刷寫按鈕前該 COM
  已經存在也能使用，避免重複重設及 COM 編號持續增加。
- 刷寫前先檢查已連接 ESP32 的韌體版本，顯示目前版本與內建版本供確認；取消
  刷寫時會自動恢復原本的連接程式。
- 成功刷寫，或從 ESP32 獨立模式切回橋接模式後，都會自動啟動桌面連接程式並
  重新搜尋手把。
- 依上述設定重新編譯隨附的 ESP32-S3 橋接韌體。
- 啟用 ESP-IDF 可重現韌體建置，讓正式映像可在乾淨重建後可靠核對雜湊。
- 通過全部 551 項自動化回歸測試；另有一項完整 ESP-IDF 重建測試維持選用。
