"""Traditional Chinese / English text used by the settings GUI."""

EN_TEXT = {
    "？": "?",
    "輸入錯誤": "Input Error",
    "輸入曲線控制點": "Enter Curve Point",
    "控制點": "Control Point",
    "X 座標": "X Coordinate",
    "Y 座標": "Y Coordinate",
    "允許範圍": "Allowed range",
    "X 與 Y 必須是有效數字。": "X and Y must be valid numbers.",
    "Y 必須介於 0.000 ～ 1.000。": "Y must be between 0.000 and 1.000.",
    "X 決定輸入位置，並須與相鄰控制點保持順序。\nY 決定該位置的輸出強度。輸入精度為 0.001。":
        "X sets the input position and must remain ordered between adjacent points.\nY sets the output strength at that position. Input precision is 0.001.",
    "映射未完成": "Mapping Incomplete",
    "映射層載入失敗": "Mapping Layer Load Failed",
    "映射層檔案包含無法使用的設定。介面仍可開啟，但修正檔案前將禁止儲存／套用。":
        "A mapping-layer file contains an unusable setting. The UI will remain available, but Save/Apply is disabled until the file is corrected.",
    "映射層檔案包含無法使用的設定。介面會保留目前內容，修正檔案後將自動重新載入。":
        "A mapping-layer file contains an unusable setting. The current UI state was preserved and the file will reload automatically after it is corrected.",
    "映射層檔案仍包含無法使用的設定，修正檔案前不能儲存／套用。":
        "A mapping-layer file still contains an unusable setting. Save/Apply is disabled until the file is corrected.",
    "仍有 CUSTOM_KEYBOARD 尚未完成輸入錄製，請重新選擇並完成錄製，或改回其他映射。":
        "One or more CUSTOM_KEYBOARD mappings have not been recorded. Select them again and finish recording, or choose another mapping.",
    "發現無法連接到執行端輸出的映射設定，本次儲存已取消。":
        "A mapping target cannot reach a runtime output. Saving was cancelled.",
    "不支援的映射層檔案版本。":
        "This mapping-layer file version is not supported.",
    "映射層檔案缺少必要欄位。":
        "The mapping-layer file is missing required fields.",
    "映射層必要欄位的資料類型不正確。":
        "One or more required mapping-layer fields have an invalid data type.",
    "即時套用設定失敗": "Live Settings Reload Failed",
    "已回復先前設定；請檢查設定內容或重新連線。":
        "The previous settings were restored. Check the settings or reconnect.",
    "執行中的連線未能套用新設定，目前暫時沿用先前設定。\n新設定已儲存至檔案，重新連線後將會生效。":
        "The live connection could not apply the new settings and is temporarily using the previous settings.\nThe new settings were saved and will take effect after reconnecting.",
    "MOUSE_WHEEL_LINEAR": "Linear Wheel",
    "XINPUT_LT_LINEAR": "Linear LT",
    "XINPUT_RT_LINEAR": "Linear RT",
    "輸入方向": "Input Direction",
    "滾輪速度": "Wheel Speed",
    "線性滑鼠滾輪": "Linear Mouse Wheel",
    "線性 Xbox LT": "Linear Xbox LT",
    "線性 Xbox RT": "Linear Xbox RT",
    "映射層": "Mapping Layers",
    "只在「混合」模式生效。\n\n0.00 = 只保留遊戲原生震動\n0.35 = 建議起始值（遊戲 65%、音訊 35%）\n1.00 = 只保留音訊震動":
        "Only applies in Mix mode.\n\n0.00 = game rumble only\n0.35 = recommended starting point (65% game, 35% audio)\n1.00 = audio rumble only",
    "控制音訊轉換成震動前的總靈敏度。\n\n0.60 = 建議起始值":
        "Controls overall sensitivity before audio is converted to rumble.\n\n0.60 = recommended starting point",
    "低於此音量的背景聲不會產生震動。\n\n0.015 = 建議起始值":
        "Background audio below this level will not produce rumble.\n\n0.015 = recommended starting point",
    "無法取得本機藍牙 MAC：": "Could not obtain the local Bluetooth MAC: ",
    "搖桿設定": "Stick Settings",
    " 左搖桿 ": " Left ",
    " 右搖桿 ": " Right ",
    "輸出與防抖": "Output & Stabilizer",
    "防抖": "Stab.",
    "曲線範圍": "Curve Range",
    "壓縮": "Scl.",
    "中心死區": "CTR DZ",
    "外圍死區": "OUT DZ",
    "平滑模式": "Curve Mode",
    "線性": "Lin.",
    "平滑": "Smo.",
    "形狀": "Shape",
    "圓": "R",
    "方": "S",
    "放大": "Zoom",
    "返回": "Back",
    "震動設定": "Rumble",
    "低頻震動強度": "LF Strength",
    "高頻震動強度": "HF Strength",
    "低頻輸出曲線": "LF Curve",
    "高頻輸出曲線": "HF Curve",
    "低頻補償": "HF → LF Mix",
    "高頻補償": "LF → HF Mix",
    "LF 頻率": "LF Frequency",
    "HF 頻率": "HF Frequency",
    "最大振幅": "Max Amp",
    " 按鍵映射 ": " Buttons ",
    " 搖桿方向映射 ": " Stick Map ",
    "按鍵映射": "Button Mapping",
    "搖桿方向映射": "Stick-Direction Mapping",
    "左搖桿": "Left Stick",
    "右搖桿": "Right Stick",
    " 映射層 ": " Layers ",
    "＋ 新增": "+ Add",
    "匯入": "Import",
    "管理映射層": "Layer Mgr.",
    "尚未建立映射層。\n\n新增後可用單鍵或複合鍵，以「按住／切換」方式套用另一組映射。\n映射內容由所有方案共用；啟用狀態與排列順序會隨方案保存。\n完成編輯後按「儲存/套用」，才會寫入方案並套用至連線。":
        "No mapping layers yet.\n\nAdd one to apply alternate mappings with a single button or chord using Hold/Toggle.\nMapping contents are shared by all profiles; enabled state and order are saved per profile.\nChoose Save/Apply to save the profile and apply it to the connection.",
    "同一時間只會套用一個映射層。\n\n複合鍵數量較多者優先；按鍵數相同時，清單較上方者優先。\n「按住」需持續按住全部啟用鍵，並會暫時覆蓋「切換」層；放開後返回原切換層。\n「切換」會在啟用組合每次按下時開啟或關閉；未勾選或未設定啟用鍵的層不會觸發。":
        "Only one mapping layer is applied at a time.\n\nA chord with more buttons has priority; with equal button counts, the layer higher in the list wins.\nHold requires all activation buttons to remain held and temporarily overrides a Toggle layer; releasing it returns to the previous Toggle layer.\nToggle turns on or off on each new press of its chord; unchecked layers and layers without activation buttons never trigger.",
    "未設定": "Not Set",
    "改名": "Rename",
    "編輯": "Edit",
    "刪除": "Delete",
    "新增映射層": "Add Mapping Layer",
    "無法新增映射層": "Could Not Add Mapping Layer",
    "無法重新命名映射層": "Could Not Rename Mapping Layer",
    "同名映射層已存在，請使用其他名稱。":
        "A mapping layer with this name already exists. Choose another name.",
    "映射層名稱不可空白。": "The mapping-layer name cannot be blank.",
    "映射層名稱不可超過 64 個字元。":
        "The mapping-layer name cannot exceed 64 characters.",
    "映射層名稱不可使用句點或空白結尾。":
        "The mapping-layer name cannot end with a period or space.",
    "映射層名稱不可包含 < > : \" / \\ | ? *。":
        "The mapping-layer name cannot contain < > : \" / \\ | ? *.",
    "這個名稱是 Windows 保留名稱，請改用其他名稱。":
        "This is a reserved Windows name. Choose another name.",
    "選擇映射層檔案": "Select Mapping Layer Files",
    "映射層檔案": "Mapping Layer Files",
    "映射層匯入成功": "Mapping Layers Imported",
    "已匯入映射層：": "Imported mapping layers:",
    "映射層匯入失敗": "Mapping Layer Import Failed",
    "無法匯入映射層檔案：": "Could not import the mapping-layer file:",
    "選擇的檔案不包含可匯入的映射層設定。":
        "The selected file does not contain importable mapping-layer settings.",
    "無法讀取映射層設定檔。": "Could not read the mapping-layer file.",
    "映射層設定格式不正確。": "The mapping-layer format is invalid.",
    "無法開啟映射層資料夾": "Could Not Open Layer Folder",
    "無法開啟存放映射層的資料夾：":
        "Could not open the mapping-layer folder:",
    "映射層資料夾已變更": "Mapping Layer Folder Changed",
    "映射層資料夾已在外部變更，但目前有尚未儲存的映射層調整。\n\n是否重新載入資料夾內容並放棄目前調整？":
        "The mapping-layer folder changed externally, but there are unsaved layer edits.\n\nReload the folder and discard the current edits?",
    "映射層名稱：": "Layer name:",
    "重新命名映射層": "Rename Mapping Layer",
    "新名稱：": "New name:",
    "刪除映射層": "Delete Mapping Layer",
    "確定刪除映射層：": "Delete mapping layer: ",
    "啟用按鍵": "Activation Buttons",
    "可選多個實體按鍵；必須全部按下才會啟用。":
        "Select one or more physical buttons; all must be held to activate.",
    "請至少選擇一個按鍵。": "Select at least one button.",
    "編輯映射層": "Edit Mapping Layer",
    "滑鼠速度": "Mouse Speed",
    "複製主要映射": "Copy Main Mapping",
    "以目前主要映射覆蓋此層的全部內容？":
        "Replace all mappings in this layer with the current main mapping?",
    "儲存": "Save",
    "音訊震動": "Audio Haptics",
    " 進階震動 ": " Advanced Rumble ",
    "還原進階震動預設": "Reset Defaults",
    "六頻段調整": "6-Band EQ",
    "LF／HF 重心": "LF/HF Balance",
    "最終輸出": "Final Output",
    "音訊釋放": "Rel.",
    "低": "Low",
    "低中": "L-Mid",
    "中": "Mid",
    "中高": "H-Mid",
    "高": "High",
    "頻率": "Frequency",
    "餘震強度": "Tail",
    "衰減 ms": "Decay ms",
    "輸出來源": "Source",
    "遊戲": "Game",
    "音訊": "Audio",
    "混合": "Mix",
    "音訊反應": "Audio Response",
    "強度": "Lvl",
    "低頻": "LF",
    "高頻": "HF",
    "噪閘": "Gate",
    "分頻": "Xover",
    "啟動": "Atk",
    "釋放": "Rel",
    "混合比例": "Mix Ratio",
    "音訊強度": "Audio Strength",
    "低頻反應": "LF Response",
    "高頻反應": "HF Response",
    "噪音閘": "Noise Gate",
    "分頻 Hz": "Crossover Hz",
    "啟動 ms": "Attack ms",
    "釋放 ms": "Release ms",
    "WASAPI 系統輸出擷取：可用": "WASAPI system-output capture: ready",
    "WASAPI 系統輸出擷取：缺少 PyAudioWPatch":
        "WASAPI system-output capture: PyAudioWPatch missing",
    "● WASAPI 系統輸出擷取：可用":
        "● WASAPI system-output capture: ready",
    "● WASAPI 系統輸出擷取：缺少 PyAudioWPatch":
        "● WASAPI system-output capture: PyAudioWPatch missing",
    "● WASAPI：可用": "● WASAPI: Ready",
    "● WASAPI：缺少": "● WASAPI: Missing",
    "模式": "Mode",
    "觸發門檻": "Trigger",
    "放開門檻": "Release",
    "方向死區": "Dir. Gap",
    "觸發": "Trig",
    "放開": "Rel",
    "映射為右搖桿": "Map to R Stick",
    "映射為左搖桿": "Map to L Stick",
    "映射為滑鼠": "Mouse",
    "游標速度": "Cursor Speed",
    "紅色扇形：方向死區，延伸至目前輸出形狀外緣；藍圈：觸發門檻；虛線圈：放開門檻。\n分界內維持原方向；滑鼠模式只調整游標速度。":
        "Red sectors: direction gap extending to the current output-shape edge; blue circle: trigger; dashed circle: release.\nBoundary gaps hold the last direction; Mouse mode adjusts cursor speed only.",
    " 陀螺儀映射 ": " Gyro Map ",
    "陀螺儀感度曲線": "Gyro Sensitivity Curve",
    "感度曲線": "Sens. Curve",
    "還原陀螺儀預設": "Reset Gyro Defaults",
    "還原曲線": "Reset Curve",
    "動態": "Dynamic",
    "後段加速": "Late Boost",
    "前段加速": "Early Boost",
    "曲線強度": "Curve Strength",
    "基礎輸出": "Base Output",
    "曲線輸出": "Curve Output",
    "啟動方式": "Activation",
    "6 軸": "6-Axis",
    "9 軸": "9-Axis",
    "按鍵": "Button",
    "按住": "Hold",
    "切換": "Toggle",
    "選擇啟動按鍵": "Select Activation Buttons",
    "選擇額外按鍵": "Select Extra Buttons",
    "Switch 按鍵配置": "Switch Button Layout",
    "觸發條件": "Trigger Rule",
    "任一按鍵": "Any Button",
    "全部同時按下": "All Together",
    "清除": "Clear",
    "取消": "Cancel",
    "確定": "OK",
    "輸入參數": "Enter Parameter",
    "目前數值": "Current Value",
    "設定範圍": "Range",
    "拉桿可選值": "Slider Values",
    "步進": "Step",
    "說明": "Help",
    "請輸入有效數字。": "Enter a valid number.",
    "輸出目標": "Target",
    "滑鼠": "Mouse",
    "控制模式": "Control Mode",
    "回中（瞄準）": "Aim (Center)",
    "傾斜（方向盤）": "Wheel (Tilt)",
    "僅水平": "Horizontal",
    "水平＋垂直": "H + V",
    "重設中立": "Recenter",
    "自訂按鍵": "Shortcut",
    "無法重設中立": "Could Not Reset Neutral",
    "中立角度已重設": "Neutral Reset",
    "反轉 X": "Invert X",
    "反轉 Y": "Invert Y",
    "傾斜軸補償": "Tilt Axis Comp.",
    "依手把傾斜角度修正瞄準軸向，減少左右與上下串軸。\n開啟時會依手把姿勢修正軸向；關閉後使用固定 X／Z 軸。": (
        "Corrects aiming axes for the controller tilt to reduce horizontal/"
        "vertical axis mixing.\nWhen enabled, axes follow controller orientation; disable it to use fixed X/Z axes."
    ),    "手把傾斜時，自動修正左右與上下的方向，減少斜拿手把造成的串軸。\n關閉後會固定使用感測器的 X／Z 軸。":
        "Automatically corrects horizontal and vertical directions as the controller tilts, reducing axis mixing when held at an angle.\nWhen disabled, the sensor's fixed X/Z axes are used.",
    "陀螺儀反應": "Gyro Response",
    "搖桿感度": "Stick Sens",
    "滑鼠感度": "Mouse Sens",
    "最大傾角": "Max Tilt",
    "X 比例": "X Ratio",
    "Y 比例": "Y Ratio",
    "死區": "DZ",
    "反死區": "Anti DZ",
    "傾斜死區": "Tilt DZ",
    "平滑 ms": "Smooth ms",
    "傾斜平滑": "Tilt Smooth",
    "穩定控制": "Stability",
    "加速抑制": "Accel Sup.",
    "自適死區": "Adaptive DZ",
    "防晃 ms": "Freeze ms",
    "額外按鍵": "Extra Key",
    "校正陀螺儀": "Calibrate Gyro",
    "校正中...": "Calibrating...",
    "感測器校正": "Sensor Cal",
    "靜止校正中...": "Still Cal...",
    "立體翻轉": "3D Rotate",
    "無法校正感測器": "Sensor Calibration Unavailable",
    "感測器校正（1/2）": "Sensor Calibration (1/2)",
    "感測器校正（2/2）": "Sensor Calibration (2/2)",
    "感測器校正（1/3）": "Sensor Calibration (1/3)",
    "感測器校正（2/3）": "Sensor Calibration (2/3)",
    "感測器校正（3/3）": "Sensor Calibration (3/3)",
    "加速度": "Accel",
    "感測器校正完成": "Sensor Calibration Complete",
    "感測器校正失敗": "Sensor Calibration Failed",
    "無法校正陀螺儀": "Gyro Calibration Unavailable",
    "陀螺儀校正完成": "Gyro Calibration Complete",
    "陀螺儀校正失敗": "Gyro Calibration Failed",
    "還原預設": "Defaults",
    "還原": "Reset",
    "校正搖桿": "Calibrate",
    "刷入相容韌體": "Flash FW",
    "儲存設定": "Save",
    "重新啟動連接程式": "Restart",
    "重啟連線": "Restart",
    "目前方案": "Profile",
    "切換並重新連線": "Apply & Reconnect",
    "切換方案": "Switch Profile",
    "儲存/套用": "Save/Apply",
    "設定已成功儲存至 config.ini。\n請由方案的「儲存/套用」完成套用。":
        "Settings were saved to config.ini.\nUse the profile Save/Apply action to apply them.",
    "可調設定已恢復為預設值。\n\n方案清單與已儲存方案均已保留。\n請按「儲存/套用」，才會寫入方案並套用至連線。\nHidHide 可重新設定。":
        "Adjustable settings were restored to defaults.\n\nThe profile list and saved profiles were preserved.\nChoose Save/Apply to save the profile and apply it to the connection.\nHidHide can be configured again.",
    "另存新方案": "Save New",
    "匯入方案": "Import Profile",
    "管理方案": "Profile Mgr.",
    "無法開啟方案資料夾": "Could Not Open Profile Folder",
    "無法開啟存放方案的資料夾：": "Could not open the profile folder:",
    "刪除方案": "Delete Profile",
    "重新命名方案": "Rename Profile",
    "重新命名": "Rename",
    "請輸入新的方案名稱：": "Enter a new profile name:",
    "同名方案已存在，請使用其他名稱。":
        "A profile with this name already exists. Choose another name.",
    "這個名稱是舊版遷移保留名稱，請改用其他名稱。":
        "This name is reserved for legacy migration. Choose another name.",
    "無法重新命名方案": "Could Not Rename Profile",
    "還原此參數預設": "Reset This Setting",
    "還原上次儲存": "Restore Saved",
    "還原系統預設": "Restore Default",
    "目前方案有尚未儲存的變更。\n\n是否先儲存至目前方案？\n\n是：儲存後繼續\n否：放棄變更並繼續\n取消：返回設定畫面":
        "The current profile has unsaved changes.\n\nSave them to the current profile first?\n\nYes: save and continue\nNo: discard and continue\nCancel: return to settings",
    "方案切換失敗": "Profile Switch Failed",
    "無法載入所選方案：": "Could not load the selected profile:",
    "方案切換成功": "Profile Switched",
    "已切換至方案：": "Switched to profile: ",
    "目前方案已由外部修改": "Active Profile Changed Externally",
    "目前方案檔案已由其他程式修改。\n\n是：重新載入外部版本並套用\n否：以目前畫面覆蓋外部版本\n取消：返回設定畫面":
        "The active profile file was changed by another program.\n\nYes: reload and apply the external version\nNo: overwrite it with the current UI values\nCancel: return to settings",
    "外部方案已重新載入": "External Profile Reloaded",
    "已重新載入並套用外部方案：":
        "Reloaded and applied the external profile: ",
    "外部方案載入失敗": "Could Not Reload External Profile",
    "方案檔案在儲存前再次變更，請重新操作。":
        "The profile file changed again before saving. Please try again.",
    "方案檔案在儲存期間再次變更，已取消儲存。":
        "The profile file changed while saving, so the save was canceled.",
    "系統預設不可修改": "System Default Is Read-only",
    "系統預設是唯讀方案；請使用「另存新方案」。":
        "System Default is read-only. Use Save New Profile instead.",
    "系統預設是唯讀方案，不能刪除。":
        "System Default is read-only and cannot be deleted.",
    "系統預設是唯讀方案，不能重新命名。":
        "System Default is read-only and cannot be renamed.",
    "系統預設有尚未儲存的調整。\n\n是否另存為新方案？\n\n是：另存後繼續\n否：放棄調整並繼續\n取消：返回設定畫面":
        "System Default has unsaved adjustments.\n\nSave them as a new profile?\n\nYes: save as new and continue\nNo: discard and continue\nCancel: return to settings",
    "方案已儲存": "Profile Saved",
    "完整設定已儲存至方案：": "The full configuration was saved to profile: ",
    "完整設定已儲存並套用至方案：":
        "The full configuration was saved and applied to profile: ",
    "完整設定已儲存，正在套用至連線：":
        "The full configuration was saved and is being applied to the connection: ",
    "完整設定已儲存，將於下次連線時生效：":
        "The full configuration was saved and will take effect on the next connection: ",
    "方案儲存失敗": "Profile Save Failed",
    "無法儲存目前方案：": "Could not save the current profile:",
    "請輸入新方案名稱：": "Enter a name for the new profile:",
    "覆蓋方案": "Replace Profile",
    "同名方案已存在，確定要覆蓋嗎？":
        "A profile with this name already exists. Replace it?",
    "方案已建立": "Profile Created",
    "已建立並切換至方案：": "Created and switched to profile: ",
    "已建立、套用並切換至方案：": "Created, applied, and switched to profile: ",
    "已建立並切換方案，正在套用：":
        "Created and switched profile; applying it now: ",
    "已建立並切換方案，將於下次連線時生效：":
        "Created and switched profile; it will take effect on the next connection: ",
    "System Default 是保留名稱，不能覆寫。":
        "System Default is a reserved name and cannot be overwritten.",
    "無法建立方案": "Could Not Create Profile",
    "選擇方案檔": "Select Profile File",
    "方案檔案": "Profile Files",
    "所有檔案": "All Files",
    "請輸入匯入後的方案名稱：": "Enter a name for the imported profile:",
    "方案匯入成功": "Profile Imported",
    "已匯入方案：": "Imported profile: ",
    "方案匯入失敗": "Profile Import Failed",
    "無法匯入方案檔：": "Could not import the profile file:",
    "選擇的檔案不包含可匯入的方案設定。":
        "The selected file does not contain importable profile settings.",
    "系統預設是保留名稱，請使用其他方案名稱。":
        "System Default is a reserved name. Choose another profile name.",
    "無法覆蓋目前方案": "Could Not Replace Active Profile",
    "目前使用中的方案不能由匯入功能覆蓋；請使用其他方案名稱。":
        "The active profile cannot be replaced by Import Profile. Choose another name.",
    "同名方案已存在，確定要以匯入檔覆蓋嗎？":
        "A profile with this name already exists. Replace it with the imported file?",
    "無法刪除方案": "Could Not Delete Profile",
    "目前正在使用這個方案；請先切換至其他方案。":
        "This profile is currently active. Switch to another profile first.",
    "至少必須保留一個方案。": "At least one profile must be kept.",
    "確定要刪除這個方案嗎？": "Delete this profile?",
    "自定義鍵盤映射": "Custom Key Map",
    "自定義輸入映射": "Custom Input Map",
    "選擇 CUSTOM_KEYBOARD 後，按下要映射的輸入。\n\n支援鍵盤單鍵、Ctrl／Shift／Alt／Win 複合鍵，以及滑鼠左鍵、右鍵與中鍵。\n例如：F12、Ctrl + S、Win + D。":
        "Choose CUSTOM_KEYBOARD, then press the input to map.\n\nSupports single keys; Ctrl/Shift/Alt/Win combinations; and left, right, or middle mouse buttons.\nExamples: F12, Ctrl + S, Win + D.",
    "等待按鍵...": "Press a key...",
    "請按下要映射的鍵盤按鍵或複合按鍵。\n\n例如：F12、Ctrl + S、Ctrl + Shift + S":
        "Press a key or key combination to map.\n\nExamples: F12, Ctrl + S, Ctrl + Shift + S",
    "請按下要映射的鍵盤按鍵、複合按鍵或滑鼠按鍵。\n\n可使用滑鼠左鍵、右鍵及中鍵（滾輪按下）。":
        "Press a key, key combination, or mouse button.\n\nLeft, right, and middle (wheel click) are supported.",
    "請按下要映射的鍵盤按鍵或複合按鍵，\n也可以向上或向下滾動滑鼠滾輪。":
        "Press a key or key combination,\nor scroll the mouse wheel up or down.",
    "錯誤": "Error",
    "設定錯誤": "Invalid Settings",
    "儲存完成": "Saved",
    "儲存失敗": "Save Failed",
    "還原完成": "Defaults Restored",
    "確定要將所有可調設定恢復為預設值嗎？\n\n搖桿校正資料不會被修改。\n方案清單與已儲存方案不會被修改。\n映射層內容會保留，但所有映射層將取消啟用。\nHidHide 隱藏設定也會取消。":
        "Restore all adjustable settings to their defaults?\n\nStick calibration data will not be changed.\nThe profile list and saved profiles will not be changed.\nMapping Layer contents will be kept, but all layers will be disabled.\nThis app's HidHide entries will also be removed.",
    "校正已啟動": "Calibration Started",
    "缺少 ViGEmBus": "ViGEmBus Missing",
    "安裝 ViGEmBus": "Install ViGEmBus",
    "ViGEmBus 安裝中": "ViGEmBus Installation",
    "ViGEmBus 安裝完成": "ViGEmBus Installed",
    "ViGEmBus 安裝未完成": "ViGEmBus Installation Incomplete",
    "找不到安裝程式": "Installer Not Found",
    "無法啟動安裝程式": "Could Not Start Installer",
    "ViGEmBus 安裝程式已在執行，請先在安裝視窗完成操作。":
        "The ViGEmBus installer is already running. Complete it in the installer window.",
    "未偵測到 ViGEmBus 驅動程式。\n\nXbox／XInput 輸出需要此驅動。\n\n安裝程式會從官方來源下載 ViGEmBus，需要網路連線及系統管理員權限。\n\n是否立即執行隨附的安裝程式？":
        "ViGEmBus was not detected.\n\nThe Xbox/XInput output requires this driver.\n\nThe installer downloads ViGEmBus from the official source and requires internet access and administrator permission.\n\nRun the bundled installer now?",
    "找不到 Install-ViGEmBus.bat。\n\n請確認發佈包內容完整。":
        "Install-ViGEmBus.bat was not found.\n\nMake sure the release package is complete.",
    "無法啟動 ViGEmBus 安裝程式：": "Could not start the ViGEmBus installer:",
    "已偵測到 ViGEmBus，現在將啟動手把連接程式。":
        "ViGEmBus was detected. The controller connector will now start.",
    "ViGEmBus 已安裝，但 Windows 必須重新啟動後才能使用。":
        "ViGEmBus was installed, but Windows must be restarted before it can be used.",
    "仍未偵測到 ViGEmBus。安裝可能已取消、失敗，或需要重新啟動 Windows。":
        "ViGEmBus is still unavailable. Installation may have been canceled, failed, or Windows may need to restart.",
    "安裝程式結束代碼：": "Installer exit code: ",
    "缺少刷機檔案": "Firmware Files Missing",
    "刷機失敗": "Flash Failed",
    "進入刷機模式": "Enter Flash Mode",
    "偵測到刷機連接埠": "Flash Port Found",
    "未偵測到刷機連接埠": "Flash Port Not Found",
    "Pin 失敗": "Pin Failed",
    "尚未儲存": "Unsaved Changes",
    "尚未儲存設定": "Unsaved Settings",
    "正在啟動": "Starting",
    "正在搜尋手把": "Searching for controller",
    "已連線": "Connected",
    "已斷線，正在重新搜尋": "Disconnected; searching again",
    "連接程式未啟動": "Connector not running",
    "狀態未知": "Unknown state",
    "● ViGEmBus 驅動程式：已安裝": "● ViGEmBus driver: installed",
    "● 未偵測到 ViGEmBus 驅動程式，目前無法建立 Xbox 虛擬控制器。":
        "● ViGEmBus driver not found; Xbox virtual controller unavailable.",
    "● ViGEm：正常": "● ViGEm: Ready",
    "● ViGEm：缺少": "● ViGEm: Missing",
    "● HidHide：正常": "● HidHide: Ready",
    "● HidHide：關閉": "● HidHide: Off",
    "● HidHide：設定": "● HidHide: Setup",
    "● HidHide：待用": "● HidHide: Idle",
    "● HidHide：缺少": "● HidHide: Missing",
    "● HidHide：錯誤": "● HidHide: Error",
    "HidHide 狀態錯誤": "HidHide Status Error",
    "尚未安裝 HidHide": "HidHide Not Installed",
    "未偵測到 HidHide。USB 有線模式仍可使用，但遊戲可能同時收到實體手把與虛擬 Xbox 手把。\n\n是否前往 HidHide 官方下載頁面？":
        "HidHide was not detected. Wired USB mode can still be used, but games may receive both the physical controller and virtual Xbox controller.\n\nOpen the official HidHide download page?",
    "未偵測到 HidHide。USB 有線模式仍可使用，但遊戲可能同時收到實體手把與虛擬 Xbox 手把。\n\n是否前往 HidHide 官方下載頁面？\n\n選擇「否」後不再自動提醒；需要時可點擊視窗下方的「HidHide：缺少」。":
        "HidHide was not detected. Wired USB mode can still be used, but games may receive both the physical controller and virtual Xbox controller.\n\nOpen the official HidHide download page?\n\nChoose No to stop automatic reminders. You can open the download page later by clicking HidHide: Missing at the bottom of the window.",
    "無法開啟下載頁面": "Could Not Open Download Page",
    "請手動開啟以下網址：": "Open this URL manually:",
    "設定 HidHide": "Set Up HidHide",
    "HidHide 設定完成": "HidHide Setup Complete",
    "HidHide 設定失敗": "HidHide Setup Failed",
    "HidHide 還原失敗": "HidHide Reset Failed",
    "可調設定已還原，但無法取消 HidHide 隱藏。請先關閉 HidHide Configuration Client 後再試。":
        "Adjustable settings were reset, but HidHide could not be disabled. Close HidHide Configuration Client and try again.",
    "無法讀取 HidHide 設定。請先關閉 HidHide Configuration Client，再重新啟動連接程式。":
        "Could not read HidHide settings. Close HidHide Configuration Client, then restart the connector.",
    "偵測到 USB 有線手把。為避免遊戲同時收到實體 HID 與虛擬 Xbox 手把，程式可以將實體手把加入 HidHide 隱藏清單，並允許攜帶版 Python 繼續讀取。":
        "A wired USB controller was detected. To prevent games from receiving both the physical HID and virtual Xbox controller, the app can hide the physical device while allowing the portable Python runtime to read it.",
    "HidHide 全域隱藏目前關閉；繼續將會開啟它。":
        "Global HidHide cloaking is off; continuing will enable it.",
    "HidHide 清單內另有其他隱藏裝置；開啟後也會套用到它們。":
        "Other devices are already in the HidHide list; enabling cloaking will affect them too.",
    "是否立即完成 HidHide 設定？": "Set up HidHide now?",
    "選擇「否」後不再自動提醒；需要時可點擊視窗下方的 HidHide 狀態。":
        "Choose No to stop automatic reminders. Click the HidHide status at the bottom of the window to set it up later.",
    "實體 USB 手把已加入隱藏清單。若已開啟 Steam 或遊戲，請完全關閉後重新開啟；若仍看到原始手把，請重新插拔 USB。":
        "The physical USB controller was added to the hidden list. Restart Steam or the game if it is already open; reconnect USB if the original controller is still visible.",
    "無法完成 HidHide 設定。請確認 HidHide Configuration Client 已關閉。":
        "Could not complete HidHide setup. Make sure HidHide Configuration Client is closed.",
    "● 手把：未啟動": "● Pad: Off",
    "● 手把連線：連接程式未啟動": "● Controller: connector not running",
    "● 手把連線：": "● Controller: ",
    " · 電量 ": " · Battery ",
    "（充電中）": " (charging)",
    "找不到 calibration.py。": "calibration.py was not found.",
    "找不到 main.py。": "main.py was not found.",
    "搖桿校正程式已經在執行。": "Stick calibration is already running.",
    "可調設定已恢復為預設值。\n\n請按「儲存設定」套用變更。":
        "Adjustable settings were restored.\n\nSelect Save to apply them.",
    "可調設定已恢復為預設值。\n\n請按「儲存設定」套用變更。\n下次啟動連線時可重新設定 HidHide。":
        "Adjustable settings were restored.\n\nSelect Save to apply them.\nHidHide can be configured again the next time the connector starts.",
    "確定要將所有可調設定恢復為預設值嗎？\n\n搖桿校正資料不會被修改。":
        "Restore all adjustable settings?\n\nStick calibration data will be kept.",
    "確定要將所有可調設定恢復為預設值嗎？\n\n搖桿校正資料不會被修改。\nHidHide 對本程式的隱藏設定也會取消。":
        "Restore all adjustable settings?\n\nStick calibration data will be kept.\nThis application's HidHide configuration will also be removed.",
    "目前有尚未儲存的設定。\n\n確定要放棄變更並關閉嗎？":
        "There are unsaved settings.\n\nDiscard them and close?",
    "設定已成功儲存至 config.ini。\n重新啟動主程式後生效。":
        "Settings were saved to config.ini.\nThey take effect after restarting the connector.",
    "輸入的數值格式不正確。\n\n請確認所有數值欄位只包含有效的數字。":
        "Invalid number format.\n\nMake sure every numeric field contains a valid number.",
    "輸入的整數格式不正確。\n\n請確認 LF／HF 頻率與最大振幅只包含有效的整數。":
        "Invalid integer format.\n\nLF/HF frequencies and max amplitude must be integers.",
    "線性：控制點之間使用直線。\n\n平滑：使用單調三次插值，平滑通過所有控制點，並避免反向與超調。":
        "Linear: straight segments between control points.\n\nSmooth: monotonic cubic interpolation through every point, without reversal or overshoot.",
    "輸出形狀會在圓形與方形之間分成 10 段。\n\n灰色圓：圓形基準；虛線方框：方形極限；藍線：目前設定的最大輸出範圍。\n\n對角方向每軸約為：\n0 = 0.707　2 = 0.766　5 = 0.854\n8 = 0.941　10 = 1.000\n\n數值越高，對角方向可輸出的範圍越大。\n\n拉桿後方百分比是預估圓周誤差；實際結果會受校正與取樣影響。":
        "Output shape is divided into 10 steps between round and square.\n\nGray circle: round reference; dashed box: square limit; blue line: the current maximum output range.\n\nApprox. diagonal output per axis:\n0 = 0.707   2 = 0.766   5 = 0.854\n8 = 0.941   10 = 1.000\n\nHigher values allow more diagonal output.\n\nThe percentage after the slider is an estimated circularity error; calibration and sampling affect actual results.",
    "防抖\n\n根據位移曲線目前區段的放大倍率，自動增加防抖強度。\n\n設定範圍：0.0 ～ 3.0\n\n0.0：關閉防抖補償。\n1.0：標準補償，曲線放大幾倍，就按相同倍率增加防抖。\n2.0：加強補償。\n3.0：更強的補償。\n\n僅在曲線斜率大於 1:1 的區域生效。\n平滑使用實際時間計算，不會因 BLE 或 ESP32 更新頻率不同而改變手感。\n數值越高，輸出越穩定，但也可能產生較明顯的平滑感。":
        "Stabilizer\n\nAutomatically increases stabilization where the response curve amplifies motion.\n\nRange: 0.0–3.0\n\n0.0: off\n1.0: standard slope-based compensation\n2.0: stronger\n3.0: strongest\n\nOnly applies where curve slope exceeds 1:1. Time-based smoothing keeps the feel consistent across BLE/ESP32 update rates. Higher values are steadier but feel smoother.",
    "用來消除左搖桿放開後的輕微飄移。\n\n設定範圍：0.00 ～ 0.99\n中心死區與外圍死區總和必須小於 1.00。\n0.00 = 無死區\n0.03 = 在 3% 範圍內忽略輸入\n\n數值越大，搖桿中心附近越不敏感。\n\n勾選「壓縮」後，曲線的 0% 起點會移到中心死區邊界，並將完整曲線重新分布到剩餘行程。\n\n曲線圖左側的灰色區域代表中心死區範圍。":
        "Removes slight left-stick drift after release.\n\nRange: 0.00–0.99\nCenter and outer deadzones must total less than 1.00.\n0.00 = no deadzone\n0.03 = ignore input within 3%\n\nHigher values reduce center sensitivity.\n\nScale moves the curve's 0% point to the deadzone edge and redistributes the full curve over the remaining travel.\n\nThe left gray area shows the center deadzone.",
    "用來消除右搖桿放開後的輕微飄移。\n\n設定範圍：0.00 ～ 0.99\n中心死區與外圍死區總和必須小於 1.00。\n0.00 = 無死區\n0.03 = 在 3% 範圍內忽略輸入\n\n數值越大，搖桿中心附近越不敏感。\n\n勾選「壓縮」後，曲線的 0% 起點會移到中心死區邊界，並將完整曲線重新分布到剩餘行程。\n\n曲線圖左側的灰色區域代表中心死區範圍。":
        "Removes slight right-stick drift after release.\n\nRange: 0.00–0.99\nCenter and outer deadzones must total less than 1.00.\n0.00 = no deadzone\n0.03 = ignore input within 3%\n\nHigher values reduce center sensitivity.\n\nScale moves the curve's 0% point to the deadzone edge and redistributes the full curve over the remaining travel.\n\nThe left gray area shows the center deadzone.",
    "設定左搖桿接近外圈時，提前輸出最大值。\n\n設定範圍：0.00 ～ 0.99\n中心死區與外圍死區總和必須小於 1.00。\n0.03 = 到達 97% 時輸出 100%\n\n勾選「壓縮」後，曲線的 100% 終點會移到外圍死區邊界，並將完整曲線重新分布到剩餘行程。\n\n曲線圖右側的灰色區域代表外圍死區範圍。":
        "Reaches maximum left-stick output before the physical edge.\n\nRange: 0.00–0.99\nCenter and outer deadzones must total less than 1.00.\n0.03 = 100% output at 97% travel\n\nScale moves the curve's 100% point to the outer-deadzone edge and redistributes the curve.\n\nThe right gray area shows the outer deadzone.",
    "設定右搖桿接近外圈時，提前輸出最大值。\n\n設定範圍：0.00 ～ 0.99\n中心死區與外圍死區總和必須小於 1.00。\n0.03 = 到達 97% 時輸出 100%\n\n勾選「壓縮」後，曲線的 100% 終點會移到外圍死區邊界，並將完整曲線重新分布到剩餘行程。\n\n曲線圖右側的灰色區域代表外圍死區範圍。":
        "Reaches maximum right-stick output before the physical edge.\n\nRange: 0.00–0.99\nCenter and outer deadzones must total less than 1.00.\n0.03 = 100% output at 97% travel\n\nScale moves the curve's 100% point to the outer-deadzone edge and redistributes the curve.\n\nThe right gray area shows the outer deadzone.",
    "調整沉重震動的力道，例如爆炸、撞擊、引擎與地面震動。\n\n設定範圍：0.00 ～ 1.00\n0.00 = 關閉低頻震動\n1.00 = 使用完整低頻強度\n建議：一般用途 0.80 ～ 0.90；強調衝擊 0.90 ～ 0.95。":
        "Adjusts heavy rumble such as explosions, impacts, engines, and ground vibration.\n\nRange: 0.00–1.00\n0.00 = disable LF rumble\n1.00 = full LF strength\nSuggested: 0.80–0.90 for general use; 0.90–0.95 for stronger impacts.",
    "調整較細、較俐落的震動力道，例如槍械、碰撞邊緣、路面與提示回饋。\n\n設定範圍：0.00 ～ 1.00\n0.00 = 關閉高頻震動\n1.00 = 使用完整高頻強度\n建議：一般用途 0.35 ～ 0.50；需要銳利細節時最高約 0.55。":
        "Adjusts finer, sharper rumble such as weapon feedback, impact edges, road texture, and alerts.\n\nRange: 0.00–1.00\n0.00 = disable HF rumble\n1.00 = full HF strength\nSuggested: 0.35–0.50 for general use; up to about 0.55 for sharper detail.",
    "以指數曲線決定低頻的小震動要保留多少；數值變化不是線性比例。\n\n設定範圍：0.10 ～ 5.00\n1.00 = 按遊戲原本比例輸出\n小於 1.00 = 小震動更明顯\n大於 1.00 = 小震動更弱，強震動仍保留\n建議：0.90 ～ 1.10；超出此區間時變化會很明顯。":
        "Uses an exponent curve to control weak LF rumble; changes are not linear.\n\nRange: 0.10–5.00\n1.00 = preserve the game's original proportion\nBelow 1.00 = weak rumble becomes more noticeable\nAbove 1.00 = weak rumble is reduced while strong rumble remains\nSuggested: 0.90–1.10; changes become pronounced outside this range.",
    "以指數曲線決定高頻的小震動要保留多少；數值變化不是線性比例。\n\n設定範圍：0.10 ～ 5.00\n1.00 = 按遊戲原本比例輸出\n小於 1.00 = 細小回饋更明顯\n大於 1.00 = 減少持續的細碎震動\n建議：一般用途 1.10 ～ 1.20；需要更直接的細節時可降至 0.95 ～ 1.05。":
        "Uses an exponent curve to control weak HF rumble; changes are not linear.\n\nRange: 0.10–5.00\n1.00 = preserve the game's original proportion\nBelow 1.00 = fine feedback becomes more noticeable\nAbove 1.00 = reduces continuous fine buzzing\nSuggested: 1.10–1.20 for general use; use 0.95–1.05 for more immediate detail.",
    "把一部分高頻訊號補到低頻，讓原本偏細的震動多一點重量。\n\n設定範圍：0.00 ～ 1.00\n0.00 = 不補充\n0.50 = 將高頻原始訊號的 50% 補到低頻\n建議：0.00 ～ 0.05；過高容易使兩種震動失去區別。":
        "Adds part of the HF signal to LF so fine rumble gains more weight.\n\nRange: 0.00–1.00\n0.00 = no added signal\n0.50 = add 50% of the raw HF signal to LF\nSuggested: 0.00–0.05; higher values blur the distinction between both components.",
    "把一部分低頻訊號補到高頻，讓沉重震動多一點清楚的邊緣感。\n\n設定範圍：0.00 ～ 1.00\n0.00 = 不補充\n0.50 = 將低頻原始訊號的 50% 補到高頻\n建議：一般用途維持 0.00；只有需要額外邊緣感時才提高到 0.01 ～ 0.03。":
        "Adds part of the LF signal to HF so heavy rumble has a clearer edge.\n\nRange: 0.00–1.00\n0.00 = no added signal\n0.50 = add 50% of the raw LF signal to HF\nSuggested: keep 0.00 for general use; use 0.01–0.03 only when extra edge definition is needed.",
    "設定低頻震動成分的頻率；此 9-bit 數值可近似以 Hz 理解，例如 215 約代表 215 Hz。\n\n設定範圍：0 ～ 511\n建議：一般用途 205 ～ 220；較柔和可用 190 ～ 205。\n實際觸感仍會受控制器韌體、致動器與外殼共振影響；在部分控制器上，低於約 180 的差異可能不明顯。":
        "Sets the LF rumble component frequency. This 9-bit value can be interpreted approximately in Hz; for example, 215 is about 215 Hz.\n\nRange: 0–511\nSuggested: 205–220 for general use; 190–205 for a softer feel.\nActual feel also depends on controller firmware, the actuator, and enclosure resonance; on some controllers, differences below about 180 may be difficult to feel.",
    "設定高頻震動成分的頻率；此 9-bit 數值可近似以 Hz 理解，例如 330 約代表 330 Hz。\n\n設定範圍：0 ～ 511\n建議：一般用途 315 ～ 325；較銳利可用 325 ～ 335。\n實際觸感仍會受控制器韌體、致動器與外殼共振影響；高於約 350 時較容易出現尖銳感、異音或不自然共振。":
        "Sets the HF rumble component frequency. This 9-bit value can be interpreted approximately in Hz; for example, 330 is about 330 Hz.\n\nRange: 0–511\nSuggested: 315–325 for general use; 325–335 for a sharper feel.\nActual feel also depends on controller firmware, the actuator, and enclosure resonance; above about 350, sharpness, audible noise, or unnatural resonance becomes more likely.",
    "限制低頻與高頻最多能震多強，相當於整體震動的安全上限。\n\n設定範圍：0 ～ 1023\n目前內建方案以 800 為上限；若偶爾出現撞擊聲，可降至 650 ～ 750。\n數值越高，最強震動越大；接近 1023 時可能增加異音、共振與硬體負擔。":
        "Limits how strong LF and HF rumble can become, acting as the overall output ceiling.\n\nRange: 0–1023\nBundled profiles currently use 800; reduce it to 650–750 if occasional impact noise occurs.\nHigher values allow stronger peaks; values near 1023 may increase noise, resonance, and hardware load.",
    "即將刷入相容的 ESP32-S3 韌體。\n\n程式將自動偵測 ESP32-S3 的刷機連接埠。\n\n是否繼續？":
        "Compatible ESP32-S3 firmware will be flashed.\n\nThe flash port will be detected automatically.\n\nContinue?",
    "請讓 ESP32-S3 進入刷機模式：\n\n1. 連接 ESP32-S3 的 OTG 接口\n2. 按住 BOOT 按鈕\n3. 按一下 RESET / EN 按鈕\n4. 放開 RESET / EN\n5. 再放開 BOOT\n\n完成後不需要做其他操作。\n程式會自動偵測刷機連接埠。":
        "Put the ESP32-S3 into flash mode:\n\n1. Connect its OTG port\n2. Hold BOOT\n3. Press RESET / EN\n4. Release RESET / EN\n5. Release BOOT\n\nThe flash port will then be detected automatically.",
    "30 秒內未偵測到新的 COM Port。\n\n請確認 ESP32-S3 已正確進入刷機模式，然後重新嘗試。":
        "No new COM port was found within 30 seconds.\n\nConfirm that the ESP32-S3 is in flash mode and try again.",
    "韌體刷寫中": "Firmware flashing",
    "ESP32-S3 韌體仍在刷寫中，請等待完成。":
        "ESP32-S3 firmware is still being flashed. Please wait for it to finish.",
    "韌體刷寫完成": "Firmware flashing completed",
    "ESP32-S3 韌體已刷寫完成。\n\n請按一下 RESET / EN 按鈕，或拔除後重新插入 ESP32-S3，再重新開啟本軟體。":
        "ESP32-S3 firmware was flashed successfully.\n\nPress RESET / EN, or unplug and reconnect the ESP32-S3, then restart this application.",
    "韌體刷寫失敗": "Firmware flashing failed",
    "esptool 刷寫失敗，結束代碼：":
        "esptool flashing failed. Exit code: ",
    "偵測到尚未儲存的設定變更。\n\n是否先儲存設定，再重新啟動連接程式？\n\n是：儲存後重新啟動\n否：不儲存，直接重新啟動\n取消：返回設定畫面":
        "Unsaved changes were detected.\n\nSave before restarting the connector?\n\nYes: save and restart\nNo: restart without saving\nCancel: return to settings",
    "未偵測到 ViGEmBus 驅動程式。\n\n目前無法啟動 Xbox 虛擬控制器。":
        "ViGEmBus was not found.\n\nThe Xbox virtual controller cannot start.",
    "找不到 config.ini。\n請將 config_gui.py 放在主程式相同資料夾。":
        "config.ini was not found.\nPlace config_gui.py in the application folder.",
    "左搖桿中心死區必須介於 0.00 ～ 1.00": "Left center DZ must be 0.00–1.00.",
    "左搖桿外圍死區必須介於 0.00 ～ 1.00": "Left outer DZ must be 0.00–1.00.",
    "右搖桿中心死區必須介於 0.00 ～ 1.00": "Right center DZ must be 0.00–1.00.",
    "右搖桿外圍死區必須介於 0.00 ～ 1.00": "Right outer DZ must be 0.00–1.00.",
    "左搖桿中心死區與外圍死區總和必須小於 1.00":
        "Left center and outer deadzones must total less than 1.00.",
    "右搖桿中心死區與外圍死區總和必須小於 1.00":
        "Right center and outer deadzones must total less than 1.00.",
    "LF 強度必須介於 0.00 ～ 1.00。": "LF strength must be 0.00–1.00.",
    "HF 強度必須介於 0.00 ～ 1.00。": "HF strength must be 0.00–1.00.",
    "LF 曲線必須介於 0.10 ～ 5.00。": "LF curve must be 0.10–5.00.",
    "HF 曲線必須介於 0.10 ～ 5.00。": "HF curve must be 0.10–5.00.",
    "LF → HF 補償必須介於 0.00 ～ 1.00。": "LF → HF mix must be 0.00–1.00.",
    "HF → LF 補償必須介於 0.00 ～ 1.00。": "HF → LF mix must be 0.00–1.00.",
    "LF 頻率必須介於 0 ～ 511。": "LF frequency must be 0–511.",
    "HF 頻率必須介於 0 ～ 511。": "HF frequency must be 0–511.",
    "最大振幅必須介於 0 ～ 1023。": "Max amplitude must be 0–1023.",
    "音訊震動模式無效。": "Invalid audio-haptics mode.",
    "游標速度必須介於 100 ～ 3000。": "Cursor speed must be 100–3000.",
    "左搖桿觸發門檻必須介於 10% ～ 100%。":
        "Left stick trigger must be 10–100%.",
    "右搖桿觸發門檻必須介於 10% ～ 100%。":
        "Right stick trigger must be 10–100%.",
    "左搖桿放開門檻必須介於 0% ～ 97%。":
        "Left stick release must be 0–97%.",
    "右搖桿放開門檻必須介於 0% ～ 97%。":
        "Right stick release must be 0–97%.",
    "左搖桿放開門檻必須至少比觸發門檻低 3%。":
        "Left stick release must be at least 3% below trigger.",
    "右搖桿放開門檻必須至少比觸發門檻低 3%。":
        "Right stick release must be at least 3% below trigger.",
    "左搖桿方向死區必須介於 0° ～ 20°。":
        "Left stick direction gap must be 0°–20°.",
    "右搖桿方向死區必須介於 0° ～ 20°。":
        "Right stick direction gap must be 0°–20°.",
    "左搖桿游標速度必須介於 100 ～ 3000。":
        "Left stick cursor speed must be 100–3000.",
    "右搖桿游標速度必須介於 100 ～ 3000。":
        "Right stick cursor speed must be 100–3000.",
    "陀螺儀啟動方式無效。": "Invalid gyro activation mode.",
    "陀螺儀啟動條件無效。": "Invalid gyro activation rule.",
    "至少選擇一個啟動按鍵。": "Select at least one activation button.",
    "陀螺儀啟動按鍵無效。": "Invalid gyro activation button.",
    "陀螺儀輸出目標無效。": "Invalid gyro output target.",
    "陀螺儀控制模式無效。": "Invalid gyro control mode.",
    "陀螺儀感度曲線無效。": "Invalid gyro sensitivity curve.",
    "陀螺儀曲線強度必須介於 0 ～ 10。": "Gyro curve strength must be 0–10.",
    "線性：維持原始輸出。\n\n後段加速：壓低慢速區、提高快速區，適合精細修正後快速轉向。\n\n前段加速：提高慢速區、壓縮後段，適合覺得中心反應不足時使用。\n\n僅作用於回中模式的搖桿輸出。":
        "Linear: preserves the original output.\n\nLate Boost: lowers slow output and raises fast output for precise correction followed by quick turns.\n\nEarly Boost: raises slow output and compresses the later range when center response feels weak.\n\nApplies only to Center-mode stick output.",
    "控制前段／後段曲線偏離線性的程度。\n\n0：等同線性。\n5：中等效果。\n10：最大效果。\n\n不會改變 0% 與 100% 終點；線性模式下此設定不生效。":
        "Controls how far Early/Late Boost bends away from linear.\n\n0: linear-equivalent.\n5: medium effect.\n10: maximum effect.\n\nThe 0% and 100% endpoints remain unchanged; this setting has no effect in Linear mode.",
    "傾斜模式僅支援左右搖桿輸出。": "Tilt mode supports stick output only.",
    "傾斜軸向無效。": "Invalid tilt axis.",
    "重設中立按鍵無效。": "Invalid recenter button.",
    "陀螺儀防晃按鍵無效。": "Invalid gyro freeze button.",
    "傾斜死區必須小於最大傾斜角。": "Tilt DZ must be below Max Tilt.",
    "最大傾斜角 必須介於 10 ～ 60。": "Max Tilt must be 10–60.",
    "傾斜死區 必須介於 0 ～ 5。": "Tilt DZ must be 0–5.",
    "傾斜平滑 ms 必須介於 0 ～ 150。": "Tilt smoothing must be 0–150 ms.",
    "陀螺儀搖桿感度 必須介於 0.1 ～ 10。": "Gyro stick sensitivity must be 0.1–10.",
    "陀螺儀滑鼠感度 必須介於 0.5 ～ 30。": "Gyro mouse sensitivity must be 0.5–30.",
    "陀螺儀 X 比例 必須介於 0.5 ～ 2。": "Gyro X ratio must be 0.5–2.0.",
    "陀螺儀 Y 比例 必須介於 0.5 ～ 2。": "Gyro Y ratio must be 0.5–2.0.",
    "陀螺儀死區 必須介於 0 ～ 5。": "Gyro DZ must be 0–5.",
    "陀螺儀平滑 ms 必須介於 0 ～ 100。": "Gyro smoothing must be 0–100 ms.",
    "加速度抑制 必須介於 0 ～ 100。": "Accel suppression must be 0–100.",
    "自適應死區 必須介於 0 ～ 100。": "Adaptive DZ must be 0–100.",
    "按鍵防晃 ms 必須介於 0 ～ 120。": "Button freeze must be 0–120 ms.",
    "按住：只有保持指定手把按鍵時啟用陀螺儀。\n\n切換：按一下啟用，再按一下關閉。\n\n啟動鍵原本的 Xbox／鍵盤映射仍會保留。":
        "Hold: gyro is active only while the selected controller button is held.\n\nToggle: press once to enable and again to disable.\n\nThe button's original Xbox/keyboard mapping is preserved.",
    "關閉：不產生任何陀螺儀映射輸出。\n\n按住：只有保持指定手把按鍵時啟用陀螺儀。\n\n切換：按一下啟用，再按一下關閉。\n\n啟動鍵原本的 Xbox／鍵盤映射仍會保留。":
        "Off: no gyro-mapping output is produced.\n\nHold: gyro is active only while the selected controller button is held.\n\nToggle: press once to enable and again to disable.\n\nThe button's original Xbox/keyboard mapping is preserved.",
    "關閉：不產生任何陀螺儀映射輸出。\n\n按住：選定按鍵符合任一／全部條件時啟用。\n\n切換：條件由未成立變成成立時切換一次。\n\n可選多顆按鍵；原本的 Xbox／鍵盤映射仍會保留。":
        "Off: no gyro output.\n\nHold: active while the selected Any/All rule is satisfied.\n\nToggle: switches once when the rule changes from false to true.\n\nMultiple buttons are supported and their original mappings remain active.",    "回中：依陀螺儀角速度輸出，停止轉動便自動回中，適合瞄準。\n\n傾斜：以手把相對於啟用瞬間的傾斜角度控制搖桿，適合方向盤；僅支援左右搖桿輸出。\n\n自訂重設按鍵會專門用於重設中立，不再送出原本的遊戲映射。":
        "Center maps angular speed and recenters when rotation stops.\n\nTilt maps angle relative to activation and supports stick output only.\n\nThe custom recenter button is intercepted and no longer sends its game mapping.",
    "回中模式：陀螺儀角速度轉成搖桿偏移的倍率。\n僅用於回中模式。":
        "Center-mode multiplier from angular speed to stick deflection.",
    "傾斜到此角度時輸出滿值。\n數值越小，較小的傾斜就能達到滿輸出。":
        "Tilt angle that reaches full output.\nLower values reach full output with less tilt.",
    "每轉動一度對應的游標像素倍率。\n數值越高，游標移動越快。":
        "Cursor-pixel multiplier per degree of rotation.\nHigher values move the cursor faster.",
    "水平陀螺儀感度相對倍率。1.00 = 不改變。":
        "Relative horizontal gyro sensitivity. 1.00 = unchanged.",
    "垂直陀螺儀感度相對倍率。1.00 = 不改變。":
        "Relative vertical gyro sensitivity. 1.00 = unchanged.",
    "忽略小於此角速度的感測器漂移。單位：度／秒。\n自適應功能會在明確轉動時縮小死區。\n數值越高，需要更明顯的轉動才會開始輸出。":
        "Ignores angular-speed drift. Adaptive mode reduces DZ during intentional motion.\nHigher values require more intentional motion before output begins.",
    "補償遊戲內建搖桿死區；只作用於回中搖桿輸出，不改變實體搖桿。\n0 = 不補償；數值越高，越容易跨過遊戲內建的搖桿死區。":
        "Compensates for the game's built-in stick deadzone. Only affects Center stick gyro output and does not alter the physical stick.\n0 = no compensation; higher values cross the game's built-in deadzone more easily.",
    "忽略中立角度附近的小幅傾斜。單位：度。\n數值越高，中立附近越不容易誤觸。":
        "Ignores small tilt near neutral, in degrees.\nHigher values reduce accidental movement near neutral.",
    "回中模式的速度自適應平滑：慢速使用設定值，快速轉動會降至約 5 ms。\n0 = 關閉；數值越高，慢速瞄準越穩但反應越柔和。":
        "Speed-adaptive Center smoothing: uses the set value for slow aim and drops to about 5 ms during fast turns.\n0 = off; higher values steady slow aim but soften response.",
    "傾斜模式的時間制低通平滑。0 = 關閉。\n數值越高，傾斜輸出越平穩但反應越慢。":
        "Time-based Tilt smoothing. 0 = off.\nHigher values are steadier but respond more slowly.",
    "以實際時間計算的低通平滑。0 = 關閉。\n數值越高，輸出越平穩但延遲感越明顯。":
        "Time-based low-pass smoothing. 0 = off.\nHigher values are steadier but add more delay.",
    "九軸或傾斜軸補償快速轉動、震動時，降低加速度計對姿態的拉動。\n0 = 關閉；數值越高，快速移動時越不容易被加速度拉偏。":
        "Reduces accelerometer pull during fast motion or vibration in 9-axis and Tilt Axis Compensation modes.\n0 = off; higher values reduce accelerometer pull during fast motion.",
    "回中模式靜止時保留死區，明確轉動時自動縮小。\n0 = 固定死區；數值越高，明確轉動時死區縮小得越多。":
        "Keeps Center DZ at rest and reduces it during intentional motion.\n0 = fixed DZ; higher values reduce the deadzone more during intentional motion.",
    "設定的額外按鍵按下或放開時，短暫停止陀螺儀輸出。\n0 = 關閉；數值越高，額外按鍵動作後暫停輸出的時間越長。":
        "Briefly freezes gyro output when a configured extra button is pressed or released.\n0 = off; higher values freeze gyro longer after an extra-button change.",
    "選擇射擊鍵等容易造成手把晃動的按鍵。\n只暫停陀螺儀，不會取消該按鍵原本的遊戲映射。":
        "Select a fire key or another button that tends to shake the controller.\nOnly gyro is frozen; the original game mapping remains active.",
    "可選多顆容易造成手把晃動的按鍵。\n任一按鍵按下或放開都會短暫停止陀螺儀，原映射仍保留。":
        "Select multiple buttons that tend to shake the controller.\nPressing or releasing any selected button briefly freezes gyro while preserving its original mapping.",    "只有搖桿模式選擇「映射為滑鼠」時生效。\n\n代表搖桿推到最外圈時每秒移動的游標像素數。\n設定範圍：100 ～ 3000；數值越高，游標移動越快。":
        "Used only when a stick mode is Mouse.\n\nPixels per second at full stick deflection.\nRange: 100–3000; higher values move the cursor faster.",
    "請先啟動連接程式並確認手把已連線。":
        "Start the connector and make sure the controller is connected first.",
    "連接程式沒有回覆重設指令，請確認程式仍在執行。":
        "The connector did not acknowledge the reset command. Confirm it is still running.",
    "已重設；若傾斜映射尚未啟用，將在下次啟用時取得中立角度。":
        "Reset accepted. If Tilt is inactive, neutral will be captured on the next activation.",
    "連接程式目前未使用傾斜模式。請先儲存設定並重新啟動連接程式。":
        "The connector is not running Tilt mode. Save settings and restart the connector first.",
    "請將手把平放在穩固且不會晃動的平面上。\n\n按下「確定」後請勿碰觸手把、按鍵或桌面，直到顯示校正完成。穩定採樣約需 1 秒；若偵測到移動，計時會重新開始。":
        "Place the controller flat on a firm, stable surface.\n\nAfter selecting OK, do not touch the controller, its buttons, or the desk until calibration completes. Stable sampling takes about one second; movement restarts the timer.",
    "請將手把平放在穩固且不會晃動的平面上。\n\n按下「確定」後請勿碰觸手把、按鍵或桌面，直到顯示校正完成。穩定採樣約需 5 秒；若偵測到移動，計時會重新開始。":
        "Place the controller flat on a firm, stable surface.\n\nAfter selecting OK, do not touch the controller, its buttons, or the desk until calibration completes. Stable sampling takes about five seconds; movement restarts the timer.",
    "三軸零點偏移已校正，並依目前這支控制器分開儲存。":
        "The three-axis zero bias was calibrated and saved for this controller.",
    "校正逾時。請確認手把保持連線並放在穩固平面後重試。":
        "Calibration timed out. Keep the controller connected on a stable surface and try again.",
    "校正期間手把已斷線，請重新連線後再試。":
        "The controller disconnected during calibration. Reconnect and try again.",
    "校正完成，但無法安全寫入 config.ini。":
        "Calibration completed, but config.ini could not be written safely.",
    "三軸數值變動過大。請勿碰觸手把或桌面後重試。":
        "The three-axis readings varied too much. Do not touch the controller or desk, then try again.",
    "持續偵測到移動，無法取得一秒穩定資料。":
        "Movement continued, so one second of stable data could not be collected.",
    "陀螺儀校正未完成。": "Gyro calibration did not complete.",
    "陀螺儀零偏已完成並儲存。\n\n請先遠離喇叭、磁鐵、大型金屬與通電設備。按下「確定」後，拿起手把並持續做立體 8 字翻轉：左右、前後及上下三個方向都要轉到。\n請至少讓 CMD 的磁力 3D 覆蓋達到 18/26。\n\n約需 8～30 秒。若取消，已完成的陀螺儀校正仍會保留，原有磁力計資料不會被修改。":
        "Gyro bias is complete and saved.\n\nMove away from speakers, magnets, large metal objects, and powered equipment. Select OK, then pick up the controller and continuously tumble it in a 3D figure-eight, covering left/right, forward/back, and up/down orientations.\nReach at least 18/26 Mag 3D coverage in CMD.\n\nThis takes about 8–30 seconds. If canceled, the gyro calibration remains saved and existing magnetometer data is unchanged.",
    "陀螺儀零偏已儲存；磁力計校正已略過，原資料保持不變。":
        "Gyro bias was saved. Magnetometer calibration was skipped and its existing data is unchanged.",
    "磁力計校正逾時。原有磁力計資料未被修改，請遠離磁性物體後重試。":
        "Magnetometer calibration timed out. Existing data was not changed. Move away from magnetic objects and try again.",
    "陀螺儀零偏與磁力計三軸偏移／縮放已依目前這支手把分開儲存，並已立即重設姿態融合；不需要重新連線。":
        "Gyro bias and three-axis magnetometer offset/scale were saved for this controller. Sensor fusion was reset immediately; reconnecting is not required.",
    "校正期間手把已斷線；原有磁力計資料未被修改。":
        "The controller disconnected during calibration. Existing magnetometer data was not changed.",
    "磁力方向覆蓋不足（至少 18/26）。請讓手把各面都朝向不同方向，完整做立體 8 字翻轉後重試；原有資料未被修改。":
        "Magnetic orientation coverage was insufficient (minimum 18/26). Point every face of the controller in different directions while making a full 3D figure-eight, then try again. Existing data was not changed.",
    "偵測到磁場形狀嚴重失真。請遠離喇叭、磁鐵、金屬桌架或通電設備後重試；原有資料未被修改。":
        "The magnetic field was severely distorted. Move away from speakers, magnets, metal desk frames, or powered equipment and try again. Existing data was not changed.",
    "磁力計校正未完成。": "Magnetometer calibration did not complete.",
    "靜止陀螺儀校正未完成。": "Stationary gyro calibration did not complete.",
    "陀螺儀與加速度計校正已完成並儲存。\n\n請先遠離喇叭、磁鐵、大型金屬與通電設備。按下「確定」後，拿起手把並持續做立體 8 字翻轉：左右、前後及上下三個方向都要轉到。\n請至少讓 CMD 的磁力 3D 覆蓋達到 18/26。\n\n約需 12～30 秒。若取消，已完成的其他校正仍會保留，原有磁力計資料不會被修改。":
        "Gyro and accelerometer calibration are complete and saved.\n\nMove away from speakers, magnets, large metal objects, and powered equipment. Select OK, then continuously tumble the controller in a 3D figure-eight through left/right, forward/back, and up/down orientations.\nReach at least 18/26 Mag 3D coverage in CMD.\n\nThis takes about 12–30 seconds. If canceled, completed calibrations remain saved and existing magnetometer data is unchanged.",
    "陀螺儀與加速度計校正已儲存；磁力計校正已略過，原資料保持不變。":
        "Gyro and accelerometer calibration were saved. Magnetometer calibration was skipped and existing data is unchanged.",
    "樣本無法形成有效的三維磁場橢球。請完整翻轉所有方向後重試。":
        "The samples did not form a valid 3D magnetic ellipsoid. Cover every orientation and try again.",
    "磁場變化不穩定或離群值過多，請更換位置後重試。":
        "The magnetic field was unstable or contained too many outliers. Move to another location and try again.",
    "磁力計橢球擬合殘差過大，請更換位置並完整翻轉後重試。":
        "The magnetic ellipsoid fit residual was too high. Move to another location, cover all orientations, and try again.",
    "加速度計姿勢採樣逾時。請保持手把完全靜止後重試。":
        "Accelerometer pose sampling timed out. Keep the controller completely still and try again.",
    "校正期間手把已斷線。": "The controller disconnected during calibration.",
    "加速度計校正完成，但無法安全寫入 config.ini。":
        "Accelerometer calibration completed, but config.ini could not be written safely.",
    "採樣期間手把晃動過大，請固定後重試。":
        "The controller moved too much during sampling. Stabilize it and try again.",
    "持續偵測到移動，無法取得穩定加速度資料。":
        "Movement continued, so stable accelerometer data could not be collected.",
    "加速度計校正未完成。": "Accelerometer calibration did not complete.",
    "接下來校正加速度計，約需 18～40 秒。\n\n請非常緩慢地讓手把朝向所有方向，並每隔一小段角度停住約半秒。不要快速甩動；程式只會收集低角速度、接近 1g 且方向不重複的樣本。\n\n務必包含正面、背面、左右側、USB 端及握把端，並加入一些斜向姿勢；至少讓 CMD 的加速 3D 覆蓋達到 14/26。若取消，會保留原有資料並直接進入磁力計校正。":
        "Accelerometer calibration takes about 18–40 seconds.\n\nVery slowly orient the controller in every direction, pausing for about half a second after each small angle. Do not swing it quickly; only low-angular-speed, near-1g, non-duplicate directions are collected.\n\nInclude the front, back, both sides, USB edge, grip edge, and several diagonal poses; reach at least 14/26 Accel 3D coverage in CMD. If canceled, existing data is retained and magnetometer calibration starts next.",
    "加速度計多姿態採樣逾時，請更慢地涵蓋所有方向後重試。":
        "Multi-pose accelerometer sampling timed out. Move more slowly through every orientation and try again.",
    "加速度計方向覆蓋不足（至少 14/26），請包含六個主要方向及更多斜向姿勢。":
        "Accelerometer coverage was insufficient (minimum 14/26). Include all six principal directions and more diagonal poses.",
    "加速度資料失真過大，請避免快速移動並重新校正。":
        "Accelerometer distortion was excessive. Avoid fast motion and calibrate again.",
    "重力橢球殘差過大，請放慢動作並在更多方向短暫停住。":
        "Gravity ellipsoid residual was too high. Move more slowly and pause briefly in more orientations.",
    "有效重力樣本不足或離群值過多，請放慢動作後重試。":
        "There were too few valid gravity samples or too many outliers. Move more slowly and try again.",
    "混合比例 必須介於 0 ～ 1。": "Mix ratio must be 0–1.",
    "音訊強度 必須介於 0 ～ 1。": "Audio strength must be 0–1.",
    "低頻反應 必須介於 0 ～ 2。": "LF response must be 0–2.",
    "高頻反應 必須介於 0 ～ 2。": "HF response must be 0–2.",
    "噪音閘 必須介於 0 ～ 0.25。": "Noise gate must be 0–0.25.",
    "分頻 Hz 必須介於 40 ～ 1000。": "Crossover must be 40–1000 Hz.",
    "啟動 ms 必須介於 1 ～ 500。": "Attack must be 1–500 ms.",
    "釋放 ms 必須介於 5 ～ 2000。": "Release must be 5–2000 ms.",
    "遊戲：只輸出遊戲原生震動。\n\n音訊：只將 Windows 預設輸出裝置的聲音轉成震動。\n\n混合：以柔和飽和方式合併遊戲與音訊震動，避免直接相加造成削波。":
        "Game: use only native game rumble.\n\nAudio: convert the Windows default output into rumble.\n\nMix: softly combine game and audio rumble without hard clipping.",
    "遊戲 ←→ 音訊的混合比例。0.00 = 全部遊戲，1.00 = 全部音訊。":
        "Game-to-audio mix ratio. 0.00 = game only; 1.00 = audio only.",
    "只在「混合」模式生效。\n\n0.00 = 只保留遊戲原生震動\n數值越高，音訊震動所占比例越多\n1.00 = 只保留音訊震動\n\n這個項目控制兩種來源的比例；音訊本身的靈敏度請用「音訊強度」調整。":
        "Used only in Mix mode.\n\n0.00 = native game rumble only\nHigher values give audio rumble a larger share\n1.00 = audio rumble only\n\nThis controls source balance. Use Audio Strength to adjust audio sensitivity.",
    "只在「混合」模式生效。\n\n0.00 = 只保留遊戲原生震動\n數值越高，音訊震動所占比例越多\n1.00 = 只保留音訊震動":
        "Used only in Mix mode.\n\n0.00 = native game rumble only\nHigher values give audio rumble a larger share\n1.00 = audio rumble only",
    "音訊轉震動的總強度。設定範圍：0.00 ～ 1.00。":
        "Overall audio-to-rumble strength. Range: 0.00–1.00.",
    "控制音訊轉換成震動前的總靈敏度。\n\n降低：整體音訊震動較弱\n提高：較小的聲音也會產生明顯震動\n聲音一出現就太強：降低此值\n只有很大的聲音才有反應：提高此值\n\n此項與混合比例不同；即使混合比例不變，也能單獨控制音訊震動的強弱。":
        "Controls overall audio-to-rumble sensitivity.\n\nLower: weaker overall audio rumble\nHigher: smaller sounds produce more rumble\nToo strong as soon as sound begins: lower it\nOnly loud sounds respond: raise it\n\nUnlike Mix Ratio, this independently adjusts the audio path's strength.",
    "控制音訊轉換成震動前的總靈敏度。\n\n降低：整體音訊震動較弱\n提高：較小的聲音也會產生明顯震動":
        "Controls overall audio-to-rumble sensitivity.\n\nLower: weaker overall audio rumble\nHigher: smaller sounds produce more rumble",
    "低頻音訊轉 LF 震動的倍率。設定範圍：0.00 ～ 2.00。":
        "Low-frequency audio to LF-rumble gain. Range: 0.00–2.00.",
    "調整低音、爆炸與引擎聲轉成 LF 震動的倍率。\n\n降低：減少此頻段的震動\n提高：加強此頻段的震動\n低音震動太重：降低此值\n爆炸感不足：提高此值":
        "Adjusts bass, explosions, and engine sounds converted to LF rumble.\n\nLower: reduce rumble from this band\nHigher: increase rumble from this band\nBass feels too heavy: lower it\nExplosions feel weak: raise it",
    "高頻音訊轉 HF 震動的倍率。設定範圍：0.00 ～ 2.00。":
        "High-frequency audio to HF-rumble gain. Range: 0.00–2.00.",
    "調整槍聲、碰撞、金屬聲及較尖銳聲音轉成 HF 震動的倍率。\n\n1.00 = 不額外放大或縮小\n持續出現細碎震動：降低此值\n短促細節不足：提高此值":
        "Adjusts gunshots, impacts, metallic sounds, and sharper audio converted to HF rumble.\n\n1.00 = no extra boost or reduction\nContinuous fine buzzing: lower it\nShort details feel weak: raise it",
    "低於此 RMS 強度的背景聲不產生震動。設定範圍：0.000 ～ 0.250。":
        "Background audio below this RMS level produces no rumble. Range: 0.000–0.250.",
    "低於此音量的背景聲不會產生震動。\n\n提高：過濾更多背景聲\n降低：較小的聲音也能觸發震動\n安靜時仍持續細震：提高此值\n較小的音效沒有反應：降低此值":
        "Background audio below this level produces no rumble.\n\nHigher: filter more background sound\nLower: smaller sounds can trigger rumble\nFine buzzing during quiet parts: raise it\nSmall effects do not respond: lower it",
    "低於此音量的背景聲不會產生震動。\n\n提高：過濾更多背景聲\n降低：較小的聲音也能觸發震動":
        "Background audio below this level produces no rumble.\n\nHigher: filter more background sound\nLower: smaller sounds can trigger rumble",
    "低頻與高頻震動的分界頻率。設定範圍：40 ～ 1000 Hz。":
        "LF/HF rumble crossover frequency. Range: 40–1000 Hz.",
    "決定哪些聲音分配到 LF 或 HF 震動。\n\n提高：更多聲音分配到 LF\n降低：更多聲音分配到 HF\n提高：更多聲音分配到 LF\n降低：更多聲音分配到 HF":
        "Determines which audio is routed to LF or HF rumble.\n\nHigher: route more audio to LF\nLower: route more audio to HF\nHigher: more audio goes to LF\nLower: more audio goes to HF",
    "震動追上音訊的速度。設定範圍：1 ～ 500 ms。":
        "How quickly rumble follows rising audio. Range: 1–500 ms.",
    "聲音出現後，震動升起所需的反應時間。\n\n降低：反應更快、更銳利\n提高：較平順，但可能弱化短促音效\n建議：一般用途 3 ～ 6 ms；節奏用途 1 ～ 3 ms。\n拉桿在常用低延遲區較密，之後逐步加大間距。":
        "Time for rumble to rise after sound begins.\n\nLower: faster and sharper\nHigher: smoother, but may weaken short effects\nRecommended: 3–6 ms for general use; 1–3 ms for rhythm use.\nThe slider is denser in the commonly used low-latency range, then increases in wider steps.",
    "聲音停止後震動衰減的速度。設定範圍：5 ～ 2000 ms。":
        "How quickly rumble fades after audio stops. Range: 5–2000 ms.",
    "只平滑音訊分析結果的下降速度。\n\n降低：停止更乾脆\n提高：下降更平順，但會拖得較久。\n建議：節奏用途 35 ～ 75 ms；一般用途 100 ～ 150 ms。\n拉桿採非線性刻度，例如 5 → 10 → 20 → 35 ms。":
        "Smooths only the falling audio-analysis envelope.\n\nLower: stops more cleanly\nHigher: falls more smoothly but lingers longer.\nRecommended: 35–75 ms for rhythm use; 100–150 ms for general use.\nThe slider uses a nonlinear scale, for example 5 → 10 → 20 → 35 ms.",
    "只調整音訊轉震動前的六個頻段。\n\n第五、六段分別控制 2000 ～ 4000 Hz 與 4000 ～ 8000 Hz，可用來降低人聲摩擦感或高頻提示音。\n1.00 = 原始增益；不影響純遊戲震動。":
        "Adjusts six bands before audio is converted to rumble.\n\nBands five and six control 2000–4000 Hz and 4000–8000 Hz, useful for reducing vocal friction or high-frequency notification sounds.\n1.00 = neutral gain; game-only rumble is unaffected.",

    "六個控制點的實際頻率範圍：\n\n低：20 ～ 120 Hz\n低中：120 ～ 300 Hz\n中：300 ～ 700 Hz\n中高：700 ～ 2000 Hz\n高：2000 ～ 4000 Hz\n極高：4000 ～ 8000 Hz\n\n拖曳控制點調整增益；雙擊控制點可還原該頻段預設。":
        "Frequency ranges for the six control points:\n\nLow: 20–120 Hz\nLow-mid: 120–300 Hz\nMid: 300–700 Hz\nHigh-mid: 700–2000 Hz\nHigh: 2000–4000 Hz\nUltra-high: 4000–8000 Hz\n\nDrag a point to adjust gain; double-click it to restore that band's default.",
    "六頻段：調整不同聲音範圍轉成震動的強弱。\nLF／HF 重心：調整中間頻段偏向低頻或高頻輸出。\n將游標移到這段文字，可查看完整對照與操作說明。":
        "6-band EQ adjusts how strongly different audio ranges become rumble.\nLF/HF Balance routes the middle bands more toward low- or high-frequency output.\nHover over this text for the full mapping and controls.",
    "六頻段與 LF／HF 重心完整說明\n\n圖表標籤與頻率對照：\nLow：20 ～ 120 Hz（低沉衝擊與重低音）\nL-Mid：120 ～ 300 Hz（低頻厚度）\nMid：300 ～ 700 Hz（中低頻細節）\nH-Mid：700 ～ 2000 Hz（中高頻細節）\nHigh：2000 ～ 4000 Hz（高頻摩擦與人聲邊緣）\nUltra：4000 ～ 8000 Hz（尖銳提示音與極高頻細節）\n\n拖曳控制點調整增益；雙擊控制點還原該頻段預設。\n1.00 代表原始增益。\n\nLF／HF 重心只改變中間頻段的輸出分配，不改變六段增益：\n-1.00 偏向 LF；0.00 平衡；+1.00 偏向 HF。\n建議先在 -0.15 ～ +0.15 內調整。\n\n本區只影響音訊與混合模式，不影響純遊戲震動。":
        "Complete 6-band and LF/HF balance guide\n\nChart labels and frequency ranges:\nLow: 20–120 Hz (deep impacts and sub-bass)\nL-Mid: 120–300 Hz (low-frequency body)\nMid: 300–700 Hz (low-mid detail)\nH-Mid: 700–2000 Hz (high-mid detail)\nHigh: 2000–4000 Hz (friction and vocal edges)\nUltra: 4000–8000 Hz (sharp alerts and ultra-high detail)\n\nDrag a point to adjust gain; double-click it to reset that band. 1.00 is neutral gain.\n\nLF/HF Balance changes only the routing of middle bands, not the six gains:\n-1.00 favors LF; 0.00 is balanced; +1.00 favors HF.\nStart within -0.15 to +0.15.\n\nThis section affects only Audio and Mix modes, not game-only rumble.",
    "調整中間音訊頻段分配到 LF 或 HF 震動的重心。\n\n-1.00：中間頻段較偏向 LF\n0.00：平衡分配\n+1.00：中間頻段較偏向 HF\n\n建議先在 -0.15 ～ +0.15 內調整。此設定不改變六個頻段的增益。":
        "Adjusts whether the middle audio bands are routed more toward LF or HF rumble.\n\n-1.00: middle bands favor LF\n0.00: balanced routing\n+1.00: middle bands favor HF\n\nStart within -0.15 to +0.15. This does not change the gain of the six bands.",
    "餘震是震動訊號減弱或停止時，暫時保留一部分剛才的震動，再依「衰減 ms」逐漸降到 0，讓停止感較柔和。\n\n0.00 = 關閉，立即跟隨原始訊號\n0.50 = 保留約一半的下降落差\n1.00 = 完整保留後再逐漸衰減\n\n套用於遊戲與音訊混合後的最終輸出；不會產生新的重複震動。":
        "Tail temporarily retains part of the previous rumble when the signal falls or stops, then fades it to zero according to Decay ms for a softer stop.\n\n0.00 = off; follow the source immediately\n0.50 = retain about half of the drop\n1.00 = retain the full drop, then fade\n\nApplied after game and audio rumble are mixed; it does not create repeated vibration pulses.",
    "控制餘震逐漸消失的時間。\n\n只在餘震強度大於 0 時生效；數值越高，震動消失得越慢。\n建議：100 ～ 225 ms；拉桿在常用區較密，之後逐步加大間距。":
        "Controls how long the final tail takes to fade.\n\nActive only when Tail is above 0; higher values fade more slowly.\nRecommended: 100–225 ms. The slider is denser in the common range, then increases in wider steps.",
    "方向邊界兩側不觸發按鍵的角度範圍。\n\n設定範圍：0 ～ 20°；建議 3 ～ 8°。\n提高可減少方向交界處誤觸，但會增加沒有方向輸出的區域。":
        "Angular range on both sides of a direction boundary where no key is triggered.\n\nRange: 0–20°; recommended: 3–8°.\nHigher values reduce accidental activation near boundaries but enlarge the area with no directional output.",
    "搖桿超過此行程比例後，才會觸發方向按鍵。\n\n設定範圍：0.10 ～ 1.00；建議 0.55 ～ 0.70。\n觸發值必須至少比放開值高 0.03。":
        "The direction key is triggered only after the stick exceeds this travel ratio.\n\nRange: 0.10–1.00; recommended: 0.55–0.70.\nTrigger must be at least 0.03 higher than Release.",
    "方向按鍵觸發後，搖桿回到此行程比例以下時放開。\n\n設定範圍：0.00 ～ 0.97；建議 0.45 ～ 0.60。\n必須至少比觸發值低 0.03，避免在邊界反覆切換。":
        "After a direction key is triggered, it is released when the stick falls below this travel ratio.\n\nRange: 0.00–0.97; recommended: 0.45–0.60.\nIt must be at least 0.03 below Trigger to prevent rapid toggling at the boundary.",
    "控制搖桿映射為滑鼠或滾輪時的移動速度。\n\n設定範圍：100 ～ 3000，步進 50。\n數值越高，游標或滾動速度越快。":
        "Controls movement speed when the stick is mapped to a mouse or wheel.\n\nRange: 100–3000, step 50.\nHigher values move the pointer or scroll faster.",
    "忽略搖桿中心附近的微小輸入，避免游標自行漂移。\n\n設定範圍：0.00 ～ 0.30，步進 0.01；建議 0.03 ～ 0.08。\n數值越高，開始移動所需的搖桿行程越大。":
        "Ignores small inputs near the stick center to prevent pointer drift.\n\nRange: 0.00–0.30, step 0.01; recommended: 0.03–0.08.\nHigher values require more stick travel before movement begins.",
    "聲音減弱後，震動完全消退所需的時間。\n\n降低：停止更乾脆\n提高：震動更平滑，但會拖尾":
        "Time for rumble to fade after sound falls.\n\nLower: stops more cleanly\nHigher: smoother, but lingers",
    "設定需先儲存，再重新啟動連接程式才會生效；擷取的是 Windows 預設輸出裝置的所有聲音。\n若安靜時仍細震，先提高噪音閘；整體太強則降低音訊強度。":
        "Save and restart the connector to apply changes. Capture includes all audio from the Windows default output device.\nIf quiet parts still buzz, raise Noise Gate. If everything is too strong, lower Audio Strength.",
    "\n\n即將開始刷入韌體。": "\n\nFirmware flashing will now begin.",
    "已偵測到刷機連接埠：": "Flash port found: ",
    "找不到以下檔案：\n\n": "The following files were not found:\n\n",
    "無法啟動校正程序：\n": "Could not start calibration:\n",
    "無法啟動連接程式：\n": "Could not start connector:\n",
    "無法啟動韌體刷入程序：\n": "Could not start firmware flasher:\n",
    "無法安全寫入 config.ini。\n\n錯誤資訊：": "Could not safely write config.ini.\n\nDetails: ",
}


PHRASES = (
    ("主要映射", "Main Mapping"),
    ("左搖桿", "Left Stick"),
    ("右搖桿", "Right Stick"),
    ("控制器按鍵來源不可空白", "Controller button source cannot be blank"),
    ("不支援的控制器按鍵來源", "Unsupported controller button source"),
    ("搖桿設定欄位不可空白", "Stick setting field cannot be blank"),
    ("不支援的搖桿設定欄位", "Unsupported stick setting field"),
    ("搖桿模式不可空白", "Stick mode cannot be blank"),
    ("不支援或方向接反的搖桿模式", "Unsupported or side-reversed stick mode"),
    ("線性輸入方向不可空白", "Linear-input direction cannot be blank"),
    ("不支援的線性輸入方向", "Unsupported linear-input direction"),
    ("按鍵映射目標不可空白", "Button mapping target cannot be blank"),
    ("搖桿方向映射目標不可空白", "Stick-direction target cannot be blank"),
    ("不支援的按鍵映射目標", "Unsupported button mapping target"),
    ("不支援的搖桿方向映射目標", "Unsupported stick-direction mapping target"),
    ("CUSTOM_KEYBOARD 尚未完成輸入錄製", "CUSTOM_KEYBOARD recording is incomplete"),
    ("鍵盤按鍵不可空白", "Keyboard key cannot be blank"),
    ("鍵盤組合包含空白按鍵", "Keyboard combination contains a blank key"),
    ("不支援的鍵盤按鍵：", "Unsupported keyboard key: "),
    ("按鍵映射資料類型不正確", "Button mapping data has an invalid type"),
    ("搖桿設定資料類型不正確", "Stick settings have an invalid type"),
    (
        "映射層檔案包含重複 ID；請刪除重複檔案或重新匯入，本次儲存已取消：",
        "Mapping-layer files contain duplicate IDs. Delete the duplicate files or import them again. Saving was canceled:",
    ),
    (
        "映射層資料夾已存在同名的非 S2P JSON；請改用其他名稱：",
        "The mapping-layer folder already contains a non-S2P JSON with the same name. Choose another layer name:",
    ),
    (
        "映射層資料夾包含無法解析的 JSON；為避免資料遺失，本次儲存已取消：",
        "The mapping-layer folder contains invalid JSON. Saving was canceled to prevent data loss:",
    ),
    ("USB 供電", "USB powered"),
    ("基本模式", "Basic mode"),
    ("九軸", "9-axis"),
    ("六軸", "6-axis"),
    ("USB 有線", "Wired USB"),
    ("無法送出磁力計校正指令：", "Could not send the magnetometer calibration command: "),
    ("無法送出加速度計校正指令：", "Could not send the accelerometer calibration command: "),
    ("無法送出校正指令：", "Could not send the calibration command: "),
    ("持續偵測到移動，無法取得五秒穩定資料。", "Continuous movement prevented five seconds of stable data collection."),
    ("陀螺儀與加速度計校正已完成並儲存。", "Gyro and accelerometer calibration are complete and saved."),
    ("陀螺儀校正已完成；加速度計沿用原有資料。", "Gyro calibration is complete; existing accelerometer data was retained."),
    ("請先遠離喇叭、磁鐵、大型金屬與通電設備。", "Move away from speakers, magnets, large metal objects, and powered equipment."),
    ("按下「確定」後，拿起手把並持續做立體 8 字翻轉：左右、前後及上下三個方向都要轉到。", "Select OK, then continuously tumble the controller in a 3D figure-eight through left/right, forward/back, and up/down orientations."),
    ("約需 12～30 秒。若取消，已完成的其他校正仍會保留，原有磁力計資料不會被修改。", "This takes about 12–30 seconds. If canceled, completed calibrations remain saved and existing magnetometer data is unchanged."),
    ("陀螺儀、多姿態加速度計與磁力計完整橢球校正已依目前這支手把分開儲存，", "Gyro, multi-pose accelerometer, and full magnetic ellipsoid calibration were saved for this controller, "),
    ("陀螺儀與磁力計完整橢球校正已儲存；加速度計沿用原有資料，", "Gyro and full magnetic ellipsoid calibration were saved; existing accelerometer data was retained, "),
    ("並已立即重設姿態融合；不需要重新連線。", "and sensor fusion was reset immediately; reconnecting is not required."),
    ("陀螺儀搖桿感度", "Gyro stick sensitivity"),
    ("陀螺儀滑鼠感度", "Gyro mouse sensitivity"),
    ("陀螺儀 X 比例", "Gyro X ratio"),
    ("陀螺儀 Y 比例", "Gyro Y ratio"),
    ("陀螺儀死區", "Gyro deadzone"),
    ("陀螺儀反死區", "Gyro anti-deadzone"),
    ("陀螺儀平滑 ms", "Gyro smoothing ms"),
    ("最大傾斜角", "Maximum tilt angle"),
    ("傾斜平滑 ms", "Tilt smoothing ms"),
    ("加速度抑制", "Acceleration suppression"),
    ("自適應死區", "Adaptive deadzone"),
    ("按鍵防晃 ms", "Button freeze ms"),
    (" 必須介於 ", " must be between "),
    ("處理控制器指令失敗：", "Controller command processing failed: "),
    ("音訊震動無法啟動：缺少 PyAudioWPatch；遊戲震動仍可正常使用。", "Audio haptics unavailable: PyAudioWPatch is missing; game rumble still works."),
    ("音訊震動已啟動：", "Audio haptics started: "),
    ("音訊震動擷取失敗：", "Audio haptics capture failed: "),
    ("ESP32 狀態連續三次無回應，判定橋接已中斷。", "ESP32 status missed three times; bridge disconnected."),
    ("無法配對：控制器尚未連線。", "Pairing failed: controller is not connected."),
    ("無法配對：尚未取得 ESP32 BLE MAC。", "Pairing failed: ESP32 BLE MAC is unavailable."),
    ("正在將控制器配對到 ESP32：", "Pairing controller with ESP32: "),
    ("ESP32 配對資料已送出。", "ESP32 pairing data sent."),
    ("控制器功能初始化完成（含陀螺儀）。", "Controller features initialized, including gyro."),
    ("控制器功能命令初始化完成，陀螺儀資料串流已啟用。", "Controller feature commands initialized; the gyro data stream is enabled."),
    ("控制器連線與基本輸入已準備完成。", "Controller connection and basic input are ready."),
    ("已送出 Player 1 LED 設定。", "Player 1 LED setting sent."),
    ("控制器初始化命令未收到回應：", "Controller initialization command timed out: "),
    ("控制器準備完成回呼錯誤：", "Controller-ready callback error: "),
    ("ESP32 已中斷連線。", "ESP32 disconnected."),
    ("正在重新搜尋控制器...", "Searching for controller again..."),
    ("ESP32 狀態連續三次未包含控制器通道。", "ESP32 status omitted the controller channel three times."),
    ("控制器斷線回呼錯誤：", "Controller-disconnected callback error: "),
    ("控制器 reconnect_mac：", "Controller reconnect_mac: "),
    ("無法解析", "unavailable"),
    ("目前 ESP32 BLE MAC：", "Current ESP32 BLE MAC: "),
    ("尚未取得 ESP32 BLE MAC，暫時使用一般連線模式。", "ESP32 BLE MAC is unavailable; using normal connection mode."),
    ("偵測到 SYNC 配對連線，開始寫入 ESP32 配對資料...", "SYNC pairing connection detected; writing ESP32 pairing data..."),
    ("控制器功能初始化失敗，陀螺儀資料可能無法使用。", "Controller feature initialization failed; gyro data may be unavailable."),
    ("控制器連線回呼錯誤：", "Controller-connected callback error: "),
    ("ESP32 連接錯誤：", "ESP32 connection error: "),
    ("無法取得 WinRT BLE 裝置，略過連線間隔最佳化。", "Could not obtain the WinRT BLE device; connection-interval optimization skipped."),
    ("目前 Windows/Bleak 不支援連線間隔最佳化。", "Connection-interval optimization is unavailable on this Windows/Bleak setup."),
    ("BLE 通知訂閱失敗，正在重試 ", "BLE notification subscription failed; retrying "),
    ("藍牙停止回呼錯誤：", "Bluetooth stop callback error: "),
    ("正在搜尋 Switch 2 Pro Controller...", "Searching for Switch 2 Pro Controller..."),
    ("已配對的手把請按任意按鍵喚醒；第一次配對請按住 SYNC。", "Press any button to wake a paired controller; hold SYNC for first-time pairing."),
    ("找不到 Switch 2 Pro Controller。", "Switch 2 Pro Controller not found."),
    ("正在使用 Windows 原生藍牙連線...", "Connecting through Windows native Bluetooth..."),
    ("Switch 2 Pro Controller BLE 連線已建立，正在初始化。", "Switch 2 Pro Controller BLE connected; initializing."),
    ("設定 BLE 高吞吐量參數失敗：", "Could not configure BLE high-throughput parameters: "),
    ("原生藍牙連線成功，輸入已啟動。", "Native Bluetooth connected; input is active."),
    ("Windows 藍牙已關閉。", "Windows Bluetooth is turned off."),
    ("請開啟藍牙後再重新進入原生藍牙模式。", "Turn on Bluetooth, then start native Bluetooth mode again."),
    ("BLE 在初始化期間中斷，Windows 已取消待處理操作。", "BLE disconnected during initialization; Windows canceled the pending operation."),
    ("這是可自動恢復的暫時斷線。", "This is a temporary disconnect and recovery will be attempted automatically."),
    ("原生藍牙連線失敗：", "Native Bluetooth connection failed: "),
    ("原生藍牙主流程錯誤：", "Native Bluetooth main-loop error: "),
    ("本機藍牙 MAC：", "Local Bluetooth MAC: "),
    ("無法取得本機藍牙 MAC：", "Could not obtain the local Bluetooth MAC: "),
    ("找到 Switch 2 Pro Controller：", "Switch 2 Pro Controller found: "),
    ("辨識為已配對手把，將直接重新連線。", "Paired controller detected; reconnecting directly."),
    ("辨識為第一次配對模式。", "First-time pairing mode detected."),
    ("尚未偵測到可連線的手把，搜尋中...", "No connectable controller detected yet; searching..."),
    ("正在將 Switch 2 Pro Controller 配對到這台電腦...", "Pairing Switch 2 Pro Controller with this PC..."),
    ("配對資料已寫入手把；之後按任意按鍵即可喚醒重連。", "Pairing data was written to the controller; press any button later to wake and reconnect."),
    ("正在準備控制器...", "Preparing controller..."),
    ("初始化命令失敗：", "Initialization command failed: "),
    ("控制器已準備完成，可以開始使用。", "Controller ready."),
    ("提醒：連線期間請勿關閉此視窗。", "Keep this window open while connected."),
    ("若需要中斷連線，請關閉此視窗。", "Close this window to disconnect."),
    ("如需校正搖桿，請先關閉本程式，再按下「校正搖桿」按鈕。", "To calibrate sticks, close this program and click Stick Calibration."),
    ("接著依照校正程序的畫面提示操作即可。", "Then follow the calibration prompts."),
    ("Switch 2 Pro Controller 原生藍牙已中斷。", "Switch 2 Pro Controller native Bluetooth disconnected."),
    ("震動傳送失敗：", "Rumble transmission failed: "),
    ("未儲存設定：", "Unsaved setting: "),
    ("未儲存震動設定：", "Unsaved rumble setting: "),
    ("● 手把連線：", "● Controller: "),
    ("● 手把：", "● Pad: "),
    ("啟動中", "Starting"),
    ("搜尋中", "Searching"),
    ("重連中", "Reconnecting"),
    ("連接程式未啟動", "Connector not running"),
    ("未啟動", "Off"),
    ("正在搜尋手把", "Searching for controller"),
    ("已斷線，正在重新搜尋", "Disconnected; searching again"),
    ("正在啟動", "Starting"),
    ("已連線", "Connected"),
    ("狀態未知", "Unknown state"),
    (" · 電量 高", " · Battery High"),
    (" · 電量 中", " · Battery Medium"),
    (" · 電量 低", " · Battery Low"),
    (" · 電量高", " · Bat High"),
    (" · 電量中", " · Bat Med"),
    (" · 電量低", " · Bat Low"),
    (" · 供電", " · Powered"),
    (" · 基本", " · Basic"),
    ("（充電中）", " (charging)"),
    ("用來消除左搖桿放開後的輕微飄移。", "Removes slight left-stick drift after release."),
    ("用來消除右搖桿放開後的輕微飄移。", "Removes slight right-stick drift after release."),
    ("設定範圍：", "Range: "),
    ("常用數值", "common value"),
    ("數值越大，搖桿中心附近越不敏感。", "Higher values reduce sensitivity near center."),
    ("曲線圖左側的灰色區域代表中心死區範圍。", "The gray area on the left is the center deadzone."),
    ("曲線圖右側的灰色區域代表外圍死區範圍。", "The gray area on the right is the outer deadzone."),
    ("勾選「壓縮」後", "When Scale is enabled"),
    ("無死區", "no deadzone"),
    ("關閉", "off"),
    ("預設", "Default"),
    ("錯誤資訊：", "Details: "),
    ("無法啟動", "Could not start "),
    ("無法呼叫手把", "Could not call controller"),
    ("找不到", "Not found: "),
    ("品質結果：", "Quality results:"),
    ("陀螺儀噪音：", "Gyro noise: "),
    ("加速度重力誤差：", "Accelerometer gravity error: "),
    ("加速度重力 RMS：", "Accelerometer gravity RMS: "),
    ("磁力橢球殘差：", "Magnetic ellipsoid residual: "),
    ("磁力三維覆蓋：", "Magnetic 3D coverage: "),
    ("陀螺儀、多姿態加速度計與磁力計完整橢球校正已依目前這支手把分開儲存，並已立即重設姿態融合；不需要重新連線。", "Gyro, multi-pose accelerometer, and full magnetic ellipsoid calibration were saved for this controller. Sensor fusion was reset immediately; reconnecting is not required."),
    ("陀螺儀與磁力計完整橢球校正已儲存；加速度計沿用原有資料，並已立即重設姿態融合；不需要重新連線。", "Gyro and full magnetic ellipsoid calibration were saved. Existing accelerometer data was retained. Sensor fusion was reset immediately; reconnecting is not required."),
)


def translate_text(text, language):
    if language != "en" or not isinstance(text, str):
        return text
    translated = EN_TEXT.get(text)
    if translated is not None:
        return translated
    translated = text
    for source, target in PHRASES:
        translated = translated.replace(source, target)
    return translated
