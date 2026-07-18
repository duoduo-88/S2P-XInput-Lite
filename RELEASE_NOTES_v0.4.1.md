# S2P-XInput-Lite v0.4.1

## English

### Changes since v0.4

- Improved input latency and stability for wired USB, ESP32, and native Windows Bluetooth.
- Reduced gyro and Tilt-mode delay for more immediate aiming.
- Improved game and audio rumble so only the newest state is sent, reducing delayed vibration and slow stops.
- Kept wired input stable during heavy rumble output.
- Added an updated low-latency ESP32-S3 firmware while retaining the previous firmware for rollback.
- Improved controller connection, reconnection, HidHide, and error-recovery behavior.

### How to use

1. Install ViGEmBus with `driver/Install-ViGEmBus.bat` if it is not already installed.
2. Connect the controller using one of these methods:
   - **Wired USB:** connect the controller with a USB-C data cable.
   - **ESP32:** connect a flashed ESP32-S3, then wake the controller. Hold **SYNC** when pairing it to the ESP32 for the first time.
   - **Native Bluetooth:** turn on Windows Bluetooth, start this application, then wake the controller. Do not pair it manually in Windows Settings; hold **SYNC** for first pairing.
3. Run `S2P-XInput-Lite.exe`.
4. Configure button mapping, sticks, gyro, and rumble in the settings window, then select **Save**.
5. Select **Start Connection** and wake the controller if necessary.
6. Keep the connection console open while playing. Closing it disconnects the controller and virtual XInput device.

The application automatically checks wired USB first, then ESP32, and finally native Bluetooth. Connect only the transport you want to use.

For stick or motion calibration, stop the active connection, open the appropriate calibration tool in the settings window, and follow the on-screen instructions.

### Updating

1. Extract this release to a new folder instead of overwriting a running installation.
2. To retain settings, back up the old `src/config.ini` and copy it into the new version.
3. If ViGEmBus is not installed, run `driver/Install-ViGEmBus.bat`.
4. ESP32 users should flash the new firmware under `esp32s3/firmware` to receive all improvements.
5. Hold the controller's **SYNC** button when pairing native Bluetooth for the first time or switching hosts.

### Notes

- Windows and one Switch 2 Pro Controller only.
- HidHide is optional for wired USB when games should see only the virtual Xbox controller.
- Close Steam, reWASD, and other controller tools before calibration, firmware testing, or first connection.
- XInput cannot expose raw motion sensors; gyro input is mapped to a stick or mouse.

---

## 繁體中文

### 相較 v0.4 的更新

- 改善 USB 有線、ESP32 與 Windows 藍牙的操作延遲與穩定性。
- 降低陀螺儀與 Tilt 模式的延遲，瞄準反應更直接。
- 改善遊戲與音訊震動，只傳送最新狀態，減少震動延後或停止不乾脆的情況。
- 有線手把在大量震動輸出時，仍能維持穩定輸入。
- 更新 ESP32-S3 低延遲韌體，並保留舊版韌體供需要時回復。
- 改善手把連線、重新連線、HidHide 與錯誤恢復流程。

### 使用方法

1. 尚未安裝 ViGEmBus 時，先執行 `driver/Install-ViGEmBus.bat`。
2. 選擇一種方式連接手把：
   - **USB 有線：**使用可傳輸資料的 USB-C 線連接手把。
   - **ESP32：**接上已燒錄韌體的 ESP32-S3，再喚醒手把；第一次配對 ESP32 時請按住 **SYNC**。
   - **Windows 原生藍牙：**開啟 Windows 藍牙並啟動本程式，再喚醒手把。不要先在 Windows 設定中手動配對；第一次配對請按住 **SYNC**。
3. 執行 `S2P-XInput-Lite.exe`。
4. 在設定視窗調整按鍵映射、搖桿、陀螺儀與震動，然後按下 **儲存設定**。
5. 按下 **啟動連線**，需要時再按手把按鍵喚醒。
6. 遊戲期間請保持連線主控台開啟；關閉視窗會中斷手把與虛擬 XInput 控制器。

程式會依序檢查 USB 有線、ESP32、Windows 原生藍牙。建議只接上目前要使用的連線方式。

需要校正搖桿或動作感測器時，請先停止目前連線，再從設定視窗開啟對應校正工具並依畫面指示操作。

### 更新方式

1. 解壓縮到新的資料夾，不要直接覆蓋正在使用中的程式。
2. 若要保留原本設定，先備份舊版的 `src/config.ini`，再複製到新版。
3. 尚未安裝 ViGEmBus 時，執行 `driver/Install-ViGEmBus.bat`。
4. ESP32 使用者請燒錄新版 `esp32s3/firmware` 韌體，才能取得完整改善。
5. 第一次使用原生藍牙或切換連線主機時，請按住手把的 **SYNC** 鍵重新配對。

### 注意事項

- 僅支援 Windows 與單支 Switch 2 Pro Controller。
- USB 有線若希望遊戲只看到虛擬 Xbox 手把，可另外安裝 HidHide。
- 請先關閉 Steam、reWASD 或其他會占用手把的程式，再進行校正、韌體測試或首次連線。
- XInput 不支援原始動作感測器；陀螺儀會映射成搖桿或滑鼠輸入。
