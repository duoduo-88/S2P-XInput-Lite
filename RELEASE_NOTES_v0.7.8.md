# S2P-XInput-Lite v0.7.8

## English

v0.7.8 adds an experimental automatic standalone USB mode for ESP32-S3 and a controller-only recovery path when host detection is not suitable.

### ESP32 standalone host selection

- Added **Auto-detect Standalone Mode (Experimental)**. The ESP32 first enumerates with a dedicated HID probe identity. Android remains on standard USB HID; when Windows requests the Microsoft OS descriptor, the ESP32 performs one software restart and re-enumerates as XInput-compatible USB.
- Auto detection does not write flash during normal probing. A one-boot RTC marker selects XInput after the Windows probe and is cleared on the next boot.
- The fixed **PC XInput Standalone Mode** and **Mobile USB HID Mode** remain available and are recommended when predictable enumeration is more important than automatic selection.

### Controller recovery chords

- Hold **HOME+X** for 3 seconds, then release, to save and restart in PC XInput mode.
- Hold **HOME+A** for 3 seconds, then release, to save and restart in Mobile USB HID mode.
- Hold **HOME+Y** for 5 seconds, then release, to save Auto mode and probe the host again.
- Chord buttons are withheld from USB output while recognition is in progress.

### Compatibility note

Automatic host selection is experimental. Windows descriptor caching, USB hubs, Android USB stacks, and game-specific HID support differ between systems, so automatic selection cannot be guaranteed on every host. On some Windows hosts, the USB HID probe may remain selected instead of switching to XInput. Unplug and reconnect the ESP32, or hold **HOME+X** for 3 seconds and release to save and restart in fixed PC XInput mode. The recovery chords allow mode changes without using the ESP32 board button or reopening the Windows application.

### Versions

- Desktop application: `v0.7.8`
- Bundled ESP32 firmware: `S2P-FW 1.0.4`

---

## 繁體中文

v0.7.8 為 ESP32-S3 新增實驗性的獨立模式主機自動辨識，以及在辨識結果不適合時可完全透過手把操作的救援切換。

### ESP32 獨立模式主機辨識

- 新增 **自動辨識獨立模式（實驗性）**。ESP32 會先以專屬 HID 探測身分列舉；Android 保持標準 USB HID，Windows 發出 Microsoft OS 描述器要求後，ESP32 會軟體重啟一次並改以 XInput 相容 USB 重新列舉。
- 正常 Auto 探測不會寫入 Flash；Windows 探測結果只使用一次性的 RTC 標記，下一次開機就會清除。
- 固定的 **PC XInput 獨立模式** 與 **手機 USB HID 模式** 仍然保留；若重視穩定、可預期的列舉結果，建議使用固定模式。

### 手把救援組合鍵

- 長按 **HOME+X** 3 秒後放開：保存並重啟為 PC XInput 模式。
- 長按 **HOME+A** 3 秒後放開：保存並重啟為手機 USB HID 模式。
- 長按 **HOME+Y** 5 秒後放開：保存 Auto 模式並重新探測主機。
- 辨識組合鍵期間，相關按鍵不會輸出到 USB 主機。

### 相容性說明

自動主機辨識屬實驗功能。Windows 描述器快取、USB Hub、Android USB 實作及遊戲對 HID 的支援都可能不同，因此無法保證每台主機都能自動判斷。部分 Windows 主機可能停留在 USB HID 探測身分，沒有切換為 XInput；請重新插拔 ESP32，或長按 **HOME+X** 3 秒後放開，保存並重啟為固定 PC XInput 模式。若結果不理想，可直接使用手把組合鍵切換，不需要 ESP32 板載按鈕，也不必重新開啟 Windows 程式。

### 版本

- 桌面程式：`v0.7.8`
- 隨附 ESP32 韌體：`S2P-FW 1.0.4`
