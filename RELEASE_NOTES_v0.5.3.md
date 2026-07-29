# S2P-XInput-Lite v0.5.3

Release date: 2026-07-23

## English

### Changes since v0.5.2

- HidHide missing-installation reminders can now be dismissed permanently instead of appearing again on every launch.
- HidHide installed-but-not-configured reminders also remember a declined setup choice.
- The HidHide status at the bottom of the settings window is now clickable: **Missing** opens the official download page, while **Off/Setup** reopens the setup confirmation.
- Installing HidHide, completing setup, uninstalling it, or restoring System Default clears the corresponding stale reminder preference when appropriate.
- Consolidated automated tests, live hardware probes, their launcher, and test documentation under `tests/` in the development repository.
- Added repository ignore rules for local settings, generated packages, caches, and Python bytecode.
- Added regression tests for both HidHide reminder preferences and the manual status action. The release passes all 130 automated tests.

### Updating from v0.5.2

1. Extract this release to a new folder; do not overwrite a running installation.
2. To retain personal settings, copy the old `src/config.ini` into the new release after extraction.
3. Run `S2P-XInput-Lite.exe`. New preference keys are added automatically without overwriting existing settings.

### Compact package contents

This user package contains only the portable runtime, application sources and profiles required at run time, ViGEmBus installer scripts, the application icon, and the current ESP32-S3 flashing tool and firmware images. Development tests, ESP32 source/build trees, rollback firmware, caches, and personal configuration are excluded.

## 繁體中文

### 自 v0.5.2 起的更新

- HidHide 未安裝提示現在可永久略過，不會每次啟動都再次詢問。
- HidHide 已安裝但尚未設定時，選擇暫不設定也會被記住。
- 設定視窗下方的 HidHide 狀態現在可點擊：**缺少**會開啟官方下載頁，**關閉／設定**則會重新叫出設定確認。
- 安裝 HidHide、完成設定、解除安裝或還原系統預設時，會在適當情況自動清除已失效的提示偏好。
- 開發版的自動測試、實體硬體探測工具、測試啟動器與說明已統一整理至 `tests/`。
- 新增 Repo 忽略規則，排除個人設定、產生的發佈包、快取與 Python 位元組碼。
- 新增 HidHide 兩種提示偏好及狀態點擊入口的回歸測試；本版通過全部 130 項自動測試。

### 從 v0.5.2 更新

1. 將本版解壓縮至新資料夾，不要直接覆蓋正在執行的舊版。
2. 若要保留個人設定，解壓縮後將舊版 `src/config.ini` 複製到新版。
3. 執行 `S2P-XInput-Lite.exe`；缺少的新偏好欄位會自動補齊，不會覆蓋既有設定。

### 精簡包內容

此使用者發佈包只保留可攜式執行環境、執行所需程式碼與方案、ViGEmBus 安裝腳本、程式圖示，以及目前使用的 ESP32-S3 燒錄工具和韌體。開發測試、ESP32 原始碼／編譯目錄、回退韌體、快取及個人設定均不包含在內。
