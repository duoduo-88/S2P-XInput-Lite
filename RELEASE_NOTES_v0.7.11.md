# S2P-XInput-Lite v0.7.11 Release Notes

## English

### Import Settings

- The main-window **Manage Profiles** action is now **Import Settings**.
- Select an older or same-version S2P-XInput-Lite installation root to migrate
  application settings, persistent controller calibration/identity data, user
  Profiles, and managed Mapping Layers.
- The current schema and defaults remain authoritative: missing new keys use
  current defaults, and **System Default** is never imported or overwritten.
- A preview lists the detected data. Profile and Layer name/ID conflicts need
  an explicit overwrite-all or skip-all choice.
- The import is transactional: invalid or incompatible input cannot partially
  overwrite `config.ini`, user Profiles, or managed Layer files.

### Transient-aware Audio Reactive Haptics

- Six-band routing is retained.
- Positive spectral changes are tracked with fast and slow feature envelopes.
- Bass and high-frequency transients receive targeted LF/HF emphasis; sustained
  audio returns to the established base routing.
- GAME rumble and transport pacing are unchanged.

## 繁體中文

### 匯入設定

- 主視窗的 **管理方案** 已改為 **匯入設定**。
- 選取舊版或同版本 S2P-XInput-Lite 程式根目錄，可遷移應用程式設定、手把
  持久化校正／識別資料、使用者方案與 managed Mapping Layer。
- 目前版本的 schema 與預設值仍具權威性：新版缺少的 key 使用目前預設，
  **System Default** 永遠不會被匯入或覆蓋。
- Preview 會列出找到的資料；方案及 Layer 名稱／ID 衝突必須明確選擇全部覆蓋
  或全部略過。
- 匯入採交易式處理；無效或不相容資料不會部分覆蓋 `config.ini`、使用者方案或
  managed Layer 檔案。

### 瞬態感知 Audio Reactive Haptics

- 保留六頻段路由。
- 以快／慢特徵包絡追蹤正向頻譜變化。
- Bass 與高頻瞬態會分別加強 LF/HF；持續音訊回到既有基礎路由。
- GAME rumble 與傳輸節流維持不變。
