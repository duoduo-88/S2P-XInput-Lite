# S2P-XInput-Lite

S2P-XInput-Lite 是一個輕量級的 Windows 控制器互通專案，透過 ESP32-S3 USB
CDC 橋接，將 Switch 2 Pro Controller 轉換為相容 Xbox 360 控制器的 XInput
虛擬控制器輸出。

本專案提供控制器輸入轉換、可自訂按鍵與搖桿方向映射、類比搖桿校正，以及將
XInput 震動轉換為 HD Rumble 2 的功能。

> S2P-XInput-Lite 是非官方社群專案，與 Nintendo、Microsoft、Espressif
> Systems 或任何其他第三方不存在官方隸屬、認可、贊助或合作關係。

## 專案定位

此版本專注於輕量化的 **ESP32-S3 橋接 → XInput 輸出** 工作流程。

S2P-XInput-Lite 使用 ESP32-S3 作為控制器通訊橋接裝置，並在 Windows
上輸出標準的 XInput 相容 Xbox 360 虛擬控制器。在此 XInput
架構基礎上，本專案加入自訂的控制與處理功能，包括按鍵映射、搖桿方向映射、類比搖桿校正，以及自訂震動轉換與調整。

由於此版本以 XInput
輸出為核心，因此虛擬控制器**不支援陀螺儀或加速度計等動作感測輸入**。

如果你需要更多控制器功能或更完整的實作，可以參考 TommyWabg 的上游專案
**Switch2Connect**：

https://github.com/TommyWabg/Switch2Connect

## 功能

-   透過 ESP32-S3 USB CDC 橋接 Switch 2 Pro Controller 輸入
-   自動偵測相容的 ESP32 序列裝置
-   透過 `vgamepad` / ViGEmBus 輸出 Xbox 360 XInput 虛擬控制器
-   按鍵與方向鍵映射
-   左右類比搖桿輸入
-   自訂類比搖桿校正
-   可設定左右搖桿方向映射
-   鍵盤按鍵映射
-   將 XInput 震動轉換為 HD Rumble 2
-   獨立 LF 與 HF 震動曲線
-   獨立 LF 與 HF 震動強度
-   LF → HF 與 HF → LF 震動補償
-   可設定震動頻率與最大振幅
-   圖形化設定介面
-   ViGEmBus 安裝狀態顯示
-   連線程式啟動與重新啟動控制
-   Pin 功能，可透過短暫震動提醒定位已連線的控制器
-   連線成功後設定 Player 1 LED
-   設定儲存於 `config.ini`

## 目前限制

-   一次僅支援一支控制器
-   僅支援 Windows
-   需要執行相容橋接韌體的 ESP32-S3
-   此 XInput 版本不支援陀螺儀與加速度計動作輸入
-   不支援 Joy-Con 配對
-   不支援 PS5 控制器模擬
-   不支援音訊觸覺

## 系統需求

-   Windows

### 硬體

-   Switch 2 Pro Controller
-   已安裝相容橋接韌體的 ESP32-S3 裝置
-   Windows PC

### 必要驅動程式

-   **ViGEmBus 1.22.0（`ViGEmBus_1.22.0_x64_x86_arm64`）**

ViGEmBus 是建立 Xbox 360 / XInput 虛擬控制器輸出所必需的驅動程式。

如果系統尚未安裝 ViGEmBus，請在使用 S2P-XInput-Lite 前先自行安裝
`ViGEmBus_1.22.0_x64_x86_arm64`。

> **注意：** ViGEmBus 是第三方系統驅動程式，並非 S2P-XInput-Lite
> 本身的一部分。安裝驅動程式時可能需要系統管理員權限；若安裝程式要求重新啟動
> Windows，請依提示操作。

S2P-XInput-Lite 的圖形化設定介面包含 ViGEmBus 安裝狀態檢查功能。

### 已打包發布版本

已打包的發布版本內含可攜式 Python 執行環境與所需的 Python 套件。

你**不需要**：

-   額外安裝 Python
-   執行 `pip install`
-   手動安裝 `pyserial` 或 `vgamepad`

## 安裝

1.  如果尚未安裝 ViGEmBus，請先安裝 **ViGEmBus
    1.22.0（`ViGEmBus_1.22.0_x64_x86_arm64`）**。
2.  將 ESP32-S3 連接至電腦。
3.  確認 ESP32-S3 已執行相容的橋接韌體。
4.  啟動 `S2P-XInput-Lite.exe`。
5.  使用圖形化設定介面完成控制器設定並啟動連線程式。

程式會自動搜尋相容的 ESP32 裝置，一般情況下不需要手動設定 COM Port。

## 基本使用方式

1.  開啟圖形化設定介面。
2.  依需求設定按鍵映射、搖桿方向映射與震動參數。
3.  儲存設定。
4.  從 GUI 啟動或重新啟動連線程式。
5.  按下控制器任意按鍵喚醒控制器，並等待連線完成。
6.  使用控制器期間，請保持連線程式視窗開啟。

控制器成功連線後，程式會設定 Player 1 LED，並播放短暫的震動提示。

## 設定

設定儲存於：

``` text
config.ini
```

建議使用圖形化設定介面修改設定。

### 按鍵映射

控制器按鍵可以映射至 Xbox / XInput 按鍵或鍵盤按鍵。

### 搖桿方向映射

左右搖桿方向可分別設定，包括上下左右與斜向方向。

### 搖桿校正

如果搖桿中心點或活動範圍不準確，可以使用內建的校正功能。

校正資料會儲存於 `config.ini`，並由主程式自動套用。

為獲得最佳結果，開始校正前請先關閉正在執行的控制器連線程式。

## 震動

本專案會將 Xbox XInput 震動轉換為 Switch 2 Pro Controller 的 HD Rumble 2
震動資料。

可設定的震動項目包括：

-   LF 頻率
-   HF 頻率
-   LF 強度
-   HF 強度
-   LF 響應曲線
-   HF 響應曲線
-   最大振幅
-   LF → HF 補償
-   HF → LF 補償

GUI 亦包含 **Pin** 功能，可向目前已連線的控制器發送短暫震動提示。

## ESP32 通訊

橋接程式透過 USB CDC 序列通訊與 ESP32-S3 連線，鮑率為：

``` text
2,000,000 baud
```

程式會透過橋接韌體的狀態回應，自動辨識相容的韌體。

## 上游專案與致謝

S2P-XInput-Lite 的部分程式碼基於或衍生自 TommyWabg 的
**Switch2Connect**。這些部分已針對本專案用途進行大量刪減、修改與重新整合。

本專案隨附的 `esp32s3.zip` 直接來自 Switch2Connect，S2P-XInput-Lite
未對該檔案進行修改。

特別感謝 TommyWabg 與 Switch2Connect 的貢獻者所提供的開源成果。

有關第三方軟體與上游來源的詳細資訊，請參閱 `THIRD_PARTY_NOTICES.md`。

## 授權

S2P-XInput-Lite 採用 **GNU General Public License v3.0（GPL-3.0）**
授權。

你可以依照 GPL-3.0 的條款使用、研究、修改與重新散布本專案。

本專案包含基於上游開源軟體衍生的程式碼，以及隨專案一同提供的第三方元件。第三方元件仍受其各自適用的著作權與授權條款約束。

完整授權條款請參閱專案根目錄中的 `LICENSE`。

## 商標聲明

S2P-XInput-Lite 是非官方的控制器互通專案，與
Nintendo、Microsoft、Espressif Systems
或任何其他第三方不存在官方隸屬、認可、贊助或合作關係。

Nintendo、Nintendo Switch、Switch 2
及相關名稱與商標均為其各自權利人的財產。Xbox、Microsoft
及其他相關名稱與商標亦為其各自權利人的財產。

本專案提及第三方產品與專案名稱，僅用於說明相容性、互通性、軟體來源及開源專案致謝。
