# S2P-XInput-Lite v0.5.0

v0.5.0 is compared with **v0.4.1**, the previous public release. v0.4.2 was not published, so its completed work is included here.

## English

### What's new since v0.4.1

- Added complete game-profile management for stick curves, deadzones, mappings, gyro, rumble, and audio haptics. Profiles can be selected, saved, duplicated, renamed, and deleted without reconnecting.
- Added tuned General, Audio, FPS-COMP, FPS-IMM, Racing, Action, and Rhythm profiles, plus a protected read-only **System Default** baseline.
- Added global mapping layers. Layers can activate while a button chord is held or toggled, have an explicit priority order, and override buttons and stick directions without changing the selected profile.
- Added per-layer stick swap, stick-to-mouse, and linear XInput trigger/stick-direction modes, with configurable deadzones and speed.
- Added mapping-layer import/export and persistent enabled/order state.
- Improved settings safety: unsaved-change prompts, external profile-change detection, atomic writes, validation, and rollback if a profile or layer operation fails.
- Improved the settings layout, 720p scrolling, individual-setting reset, bilingual text coverage, and immediate UI refresh after profile changes.
- Profile switching and saving now reconfigure gameplay settings live. Restart is only needed for connection method, device, or serial changes.
- Preserved the v0.4.1 low-latency USB, ESP32-S3, native BLE, gyro, and latest-only rumble paths while expanding automated regression coverage.

### Updating from v0.4.1

1. Extract v0.5.0 to a new folder instead of overwriting the old installation.
2. To retain personal settings, copy `src/config.ini` from v0.4.1 before starting v0.5.0.
3. Copy any custom files under `src/profiles` if required. Mapping layers are new in v0.5.0 and are stored under `src/layers`.
4. Existing v0.4.1 ESP32-S3 users do not need to flash again when already using the bundled `0.12.4` low-latency firmware.
5. Keep `src/profiles/System Default.ini` in the release folder. Missing settings are migrated without overwriting existing values.

### Notes

- Windows 10/11 and one Switch 2 Pro Controller are supported.
- ViGEmBus is required. HidHide remains optional and is only needed when wired games must not see the physical controller.
- Mapping layers are global; switching profiles does not replace or delete them.
- XInput cannot expose raw motion sensors, so gyro is mapped to a stick or mouse.

---

## 繁體中文

### 自 v0.4.1 起的更新

- 新增完整遊戲設定檔管理，涵蓋搖桿曲線、死區、按鍵與方向映射、陀螺儀、震動及音訊觸覺；可直接選取、儲存、另存、重新命名與刪除，不必重新連線。
- 新增調校完成的 General、Audio、FPS-COMP、FPS-IMM、Racing、Action、Rhythm 設定檔，以及受保護且唯讀的 **System Default** 基準。
- 新增全域 Mapping Layers。可用按鍵組合「按住」或「切換」啟用，依排列順序決定優先權，並暫時覆寫按鍵與搖桿方向而不修改目前設定檔。
- Mapping Layer 支援左右搖桿交換、搖桿控制滑鼠、線性 XInput 扳機／搖桿方向輸出，以及個別死區與速度設定。
- 新增 Mapping Layer 匯入／匯出，並保存啟用狀態與排列順序。
- 改善設定資料安全：未儲存變更提示、外部設定檔變更偵測、原子寫入、內容驗證，以及設定檔或 Layer 操作失敗時回復原狀。
- 改善設定視窗配置、720p 捲動、單項設定重設、中英文文字完整度，以及切換設定檔後立即更新介面。
- 儲存或切換設定檔會即時套用遊戲相關設定；只有變更連線方式、裝置或序列埠時才需要重新啟動連線。
- 保留 v0.4.1 的低延遲 USB、ESP32-S3、Windows 原生 BLE、陀螺儀與 latest-only 震動路徑，並擴充自動化回歸測試。

### 從 v0.4.1 更新

1. 將 v0.5.0 解壓縮到新資料夾，不要直接覆蓋舊版。
2. 若要保留個人設定，請在第一次啟動 v0.5.0 前複製舊版的 `src/config.ini`。
3. 視需要複製 `src/profiles` 內的自訂設定檔。Mapping Layers 是 v0.5.0 新功能，儲存在 `src/layers`。
4. 若 v0.4.1 的 ESP32-S3 已使用隨附的 `0.12.4` 低延遲韌體，無須再次燒錄。
5. 發佈包內必須保留 `src/profiles/System Default.ini`；程式會補齊舊設定缺少的欄位，不覆寫既有值。

### 注意事項

- 僅支援 Windows 10／11 與單一 Switch 2 Pro Controller。
- 必須安裝 ViGEmBus。HidHide 仍為選用，只有在 USB 有線模式下需要避免遊戲同時偵測實體控制器時才需安裝。
- Mapping Layers 為全域設定；切換遊戲設定檔不會取代或刪除 Layer。
- XInput 無法輸出原始動作感測資料，因此陀螺儀會映射到搖桿或滑鼠。
