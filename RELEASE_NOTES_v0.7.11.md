# S2P-XInput-Lite v0.7.11 Release Notes

## English

### Import Settings

- In the main window, **Manage Profiles** is now called **Import Settings**.

![Import Settings button in the v0.7.11 main window](manual/assets/annotated/16-import-settings-button.png)

- Use **Import Settings** to bring settings over from another S2P-XInput-Lite
  installation. You can choose an older or same-version installation.
- It can bring over your app settings, saved controller calibration and
  identity, personal Profiles, and managed Mapping Layers.
- The current version stays in control of its settings: anything new that is
  missing from the imported settings uses its current default. The built-in
  **System Default** Profile is never imported or replaced.
- Before anything is imported, you can review what was found. If a Profile or
  Layer has the same name or ID as one you already have, choose to overwrite
  all conflicts or skip them all.
- If the selected data is invalid or incompatible, the import stops without
  partly changing `config.ini`, your Profiles, or managed Layer files.

### Audio-Reactive Haptics

- The existing six-band vibration settings are unchanged.
- The app now compares short-term and longer-term sound levels to catch sudden
  changes.
- A sudden bass or high-pitched sound gets a stronger response in the matching
  vibration range. When the sound settles, vibration returns to its usual mix.
- Game vibration signals and data-sending intervals are unchanged.

## 繁體中文

### 匯入設定

- 主視窗裡的 **管理方案** 按鈕，現在改名為 **匯入設定**。

![v0.7.11 主視窗中的匯入設定按鈕](manual/assets/annotated/16-import-settings-button.png)

- 想把另一個 S2P-XInput-Lite 資料夾裡的設定帶過來，就按 **匯入設定**。舊版和
  同版本都可以匯入。
- 可以帶過來的內容包括程式設定、手把校正與識別資料、自己的設定方案，以及
  程式管理的 Mapping Layer。
- 匯入後仍以目前版本的設定為準；舊設定裡沒有的新項目會使用目前的預設值。
  內建的 **System Default** 方案不會被匯入，也不會被取代。
- 匯入前會先列出找到的內容。如果設定方案或 Layer 和現有項目撞名或撞 ID，
  可以選擇全部覆蓋，或全部略過。
- 如果資料有問題或和目前版本不相容，匯入就會停止，不會只改到一半，也不會
  部分覆蓋 `config.ini`、你的設定方案或 Mapping Layer 檔案。

### 音訊震動反應

- 原本的六頻段震動設定維持不變。
- 程式會比較短時間和較長時間內的音量變化，抓出突然變強的聲音。
- 低音突然變強時會加強低頻震動；高音突然變強時會加強高頻震動。聲音穩定後，
  就回到原本的震動分配。
- 遊戲本身的震動訊號和資料傳送間隔都沒有改變。
