# v0.8.3

**[Download the latest Windows release](https://github.com/duoduo-88/S2P-XInput-Lite/releases/latest)** · **[Support the project on Ko-fi](https://ko-fi.com/duoduo88)**

## English

### Quick start

1. Download the release ZIP and extract **all** of it to a new folder.
2. If ViGEmBus is not installed, run `app\driver\Install-ViGEmBus.bat`.
3. For wired USB, optionally install [HidHide](https://github.com/nefarius/HidHide/releases/latest)
   so games see only the virtual controller.
4. Connect the controller before opening Steam or another controller tool.
   For native BLE, turn Bluetooth on and hold the controller's **SYNC** button
   after starting the program. **Do not pair it in Windows Bluetooth settings.**
5. Run **S2P-XInput-Lite.exe**, choose a profile, and select **Save/Apply**.

To upgrade, extract the new version to a new folder and use **Import Settings**
to bring over your profiles, layers, and calibration. The
[User Guide](manual/USER_GUIDE.md) covers every setting step by step.

v0.8.3 bundles the same ESP32-S3 firmware as v0.8.2, S2P-FW `1.0.9`. If you
already flashed it with v0.8.2, nothing needs to be flashed again.

### What's new

- **Voice Cut** and a new **Movie** profile for audio rumble: while speech is recognised, rumble from the bands the voice occupies is lowered, so film dialogue no longer rumbles constantly. Explosions, gunshots, punches, and blade hits still come through over dialogue. The Movie profile turns it on and raises the H-Mid, High, and Ultra bands so gunshots and metal hits are clearly felt. Sung vocals count as speech, so other profiles leave it off.
- **Motor Emulation** under **Final Output** makes game rumble behave like a classic motor: light rumble moves to a lower, grainier frequency and strong rumble to a higher, smoother one. It is on in every bundled profile (Racing `1.00`, Action `0.75`, others `0.60`) and is greyed out when **Source** is Audio.
- Audio rumble adds **Detail**, which lifts quiet detail without amplifying background sound near the gate, and **Freq**, which moves the HD Rumble frequencies with the tone of the sound.
- Audio rumble now fades out when a player pauses instead of stopping on its last level. 5.1 and 7.1 outputs no longer feel weaker than stereo, and loud bass leaks much less into the high bands. **Mix** keeps game rumble at full strength up to `0.50`. Several bundled rumble profiles are retuned to match.
- Rumble Test patterns are redesigned so each one feels like its name, such as Heartbeat, Double, Machine Gun, and Engine. **Rate** now sets how often a pattern restarts instead of stretching it. **Repeat** loops a pattern, and the new **Overlap** decides whether a newly selected pattern plays on top of the others or replaces them. Duplicate patterns were removed, and Countdown is now Start Countdown.
- **Flash FW** is now a menu. **Flash bundled firmware** works as before, and **Flash custom firmware…** flashes an ESP32-S3 image you built yourself. The file is checked before any device is touched, and checked again right before flashing. The User Guide has a new section on building your own firmware, and the release package now includes the firmware source.
- The wait for the ESP32-S3 flash port can be cancelled. Cancelling it, or letting it time out, brings back the connection the flash stopped.
- **Sensor Calibration** without a connected controller now offers to start the connection, then continues by itself once the controller connects.
- Newly opened windows take the keyboard focus while S2P is the active application, so typing and Enter no longer go to the window behind them.
- Aim Test results compare each value with its own best among rounds with the same settings. The User Guide explains how screen resolution and aspect ratio affect the Aim Test.
- Gamepad Tester's Raw HID source now shows the buttons, D-pad, and triggers of XInput controllers, not only their sticks.
- The ESP32 menu item **Write and enable Android USB HID standalone** is shortened to **Write and enable standalone**. The startup banner uses the same font on every PC, English gyro and Final Output labels are no longer cut off, and a few untranslated texts in English mode are fixed.

## 繁體中文

**[下載最新 Windows 版本](https://github.com/duoduo-88/S2P-XInput-Lite/releases/latest)** · **[在 Ko-fi 贊助本專案](https://ko-fi.com/duoduo88)**

### 快速開始

1. 下載發佈 ZIP，**完整**解壓縮到新的資料夾。
2. 若尚未安裝 ViGEmBus，執行 `app\driver\Install-ViGEmBus.bat`。
3. USB 有線模式可另外安裝 [HidHide](https://github.com/nefarius/HidHide/releases/latest)，讓遊戲只看到虛擬手把。
4. 先連接手把，再開啟 Steam 或其他手把工具。原生 BLE 請開啟藍牙，啟動程式後按住手把的 **SYNC** 鍵；
   **不要在 Windows 藍牙設定中手動配對。**
5. 執行 **S2P-XInput-Lite.exe**，選擇方案後按 **儲存/套用**。

更新時請將新版解壓縮到新資料夾，再用 **匯入設定** 帶入舊版的方案、映射層與校正資料。
[使用手冊](manual/USER_GUIDE_zh-TW.md) 附截圖逐步說明每項設定。

v0.8.3 隨附的 ESP32-S3 韌體與 v0.8.2 相同，都是 S2P-FW `1.0.9`。已用 v0.8.2 刷過韌體的話不必重刷。

### 更新內容

- 音訊震動新增「人聲抑制」與「Movie」方案：辨識到講話時，會壓低人聲所在頻段的震動，電影對白不再一直震動；爆炸、槍聲、拳腳與刀劍等撞擊疊在對白上時照常震動。Movie 方案會開啟人聲抑制，並提高 H-Mid、High 與 Ultra 頻段，讓槍聲與金屬撞擊更明顯。歌曲的人聲也會被當成講話，所以其他方案預設關閉。
- 「最終輸出」新增「馬達模擬」，讓遊戲震動像傳統馬達：輕震動的頻率往下、更有顆粒感，強震動的頻率往上、維持滑順。所有內建方案都已開啟（Racing `1.00`、Action `0.75`，其他為 `0.60`）；Source 為純音訊時會停用。
- 音訊震動新增「細節增強」，可放大細小的聲音，噪音閘附近的背景聲不會被放大；以及「頻率跟隨」，讓 HD Rumble 頻率隨聲音的音色變化。
- 播放器暫停時，音訊震動會自然淡出，不再停在最後的強度。5.1 與 7.1 聲道輸出不再比立體聲弱，強烈低音也大幅減少滲入高頻段。「混合」比例在 `0.50` 以下時，遊戲震動維持完整強度。多個內建震動方案也已配合重新調整。
- 震動測試的模板重新設計，每個模板都符合名稱的手感，例如心跳、雙擊、機槍與引擎。「播放頻率」改為決定模板多久重新開始一次，不再拉長效果本身。「重複播放」讓模板循環播放，新增的「重疊播放」決定新點選的模板要疊加在播放中的模板上，還是取代它們。重複的模板已移除，「節拍」改名為「腳步」，「倒數」改名為「起跑倒數」。
- 「刷入韌體」改為選單：「刷入內建韌體」與原本相同，「刷入自訂韌體…」可刷入自己編譯的 ESP32-S3 韌體。接觸裝置前會先檢查檔案，刷寫前也會再確認一次。使用手冊新增自行編譯韌體的說明，發佈套件也附上韌體原始碼。
- 等待 ESP32-S3 刷機連接埠時可以取消。取消或等待逾時後，會恢復刷機前停止的連線。
- 手把未連線時執行「感測器校正」，會詢問是否自動啟動連線，手把連線後自動繼續校正。
- S2P 是目前使用中的程式時，新開啟的視窗會取得鍵盤焦點，輸入文字與按 Enter 不會再跑到後面的視窗。
- 瞄準測試的每項成績改為和相同設定回合中的各自最佳值比較。使用手冊說明了螢幕解析度與長寬比對瞄準測試的影響。
- 手把測試的 Raw HID 來源現在也會顯示 XInput 手把的按鍵、十字鍵與扳機，不再只有搖桿。
- ESP32 選單的「寫入並啟用 Android USB HID 獨立模式」縮短為「寫入並啟用獨立模式」。啟動畫面在每台電腦都使用相同字型，英文版的陀螺儀與「最終輸出」欄位名稱不再被截斷，也修正了英文模式下幾處未翻譯的文字。
