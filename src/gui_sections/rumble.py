from tkinter import ttk

def build_rumble_section(gui, left_frame):
    """Build the standard rumble controls."""
    # =========================
    # 左側：震動
    # =========================
    rumble_frame = ttk.LabelFrame(
        left_frame,
        text="震動設定",
        padding=6
    )
    rumble_frame.grid(
        row=1,
        column=0,
        sticky="nsew"
    )
    gui.rumble_frame = rumble_frame

    # 外框與搖桿設定同寬；實際欄位放在獨立容器中水平置中，
    # 因此不會因為外框拉寬而在內容右側留下不對稱空白。
    rumble_content = ttk.Frame(rumble_frame)
    rumble_content.grid(row=0, column=0, sticky="ns")
    rumble_frame.columnconfigure(0, weight=1)
    rumble_frame.rowconfigure(0, weight=1)
    for row_index in range(9):
        rumble_content.rowconfigure(
            row_index,
            weight=1,
            uniform="rumble_rows"
        )

    gui.add_entry(
        rumble_content, 0, "低頻震動強度", gui.lf_strength_var,
        "調整沉重震動的力道，例如爆炸、撞擊、引擎與地面震動。\n\n"
        "設定範圍：0.00 ～ 1.00\n"
        "0.00 = 關閉低頻震動\n1.00 = 使用完整低頻強度\n"
        "建議：一般用途 0.80 ～ 0.90；強調衝擊 0.90 ～ 0.95。",
        minimum=0.0, maximum=1.0, step=0.01,
    )
    gui.add_entry(
        rumble_content, 1, "高頻震動強度", gui.hf_strength_var,
        "調整較細、較俐落的震動力道，例如槍械、碰撞邊緣、路面與提示回饋。\n\n"
        "設定範圍：0.00 ～ 1.00\n"
        "0.00 = 關閉高頻震動\n1.00 = 使用完整高頻強度\n"
        "建議：一般用途 0.35 ～ 0.50；需要銳利細節時最高約 0.55。",
        minimum=0.0, maximum=1.0, step=0.01,
    )

    gui.add_entry(
        rumble_content, 2, "低頻輸出曲線", gui.lf_curve_var,
        "以指數曲線決定低頻的小震動要保留多少；數值變化不是線性比例。\n\n"
        "設定範圍：0.10 ～ 5.00\n"
        "1.00 = 按遊戲原本比例輸出\n"
        "小於 1.00 = 小震動更明顯\n"
        "大於 1.00 = 小震動更弱，強震動仍保留\n"
        "建議：0.90 ～ 1.10；超出此區間時變化會很明顯。",
        minimum=0.1, maximum=5.0, step=0.05,
    )

    gui.add_entry(
        rumble_content, 3, "高頻輸出曲線", gui.hf_curve_var,
        "以指數曲線決定高頻的小震動要保留多少；數值變化不是線性比例。\n\n"
        "設定範圍：0.10 ～ 5.00\n"
        "1.00 = 按遊戲原本比例輸出\n"
        "小於 1.00 = 細小回饋更明顯\n"
        "大於 1.00 = 減少持續的細碎震動\n"
        "建議：一般用途 1.10 ～ 1.20；需要更直接的細節時可降至 0.95 ～ 1.05。",
        minimum=0.1, maximum=5.0, step=0.05,
    )

    gui.add_entry(
        rumble_content, 4, "低頻補償", gui.hf_to_lf_compensation_var,
        "把一部分高頻訊號補到低頻，讓原本偏細的震動多一點重量。\n\n"
        "設定範圍：0.00 ～ 1.00\n"
        "0.00 = 不補充\n0.50 = 將高頻原始訊號的 50% 補到低頻\n"
        "建議：0.00 ～ 0.05；過高容易使兩種震動失去區別。",
        minimum=0.0, maximum=1.0, step=0.01,
    )

    gui.add_entry(
        rumble_content, 5, "高頻補償", gui.lf_to_hf_compensation_var,
        "把一部分低頻訊號補到高頻，讓沉重震動多一點清楚的邊緣感。\n\n"
        "設定範圍：0.00 ～ 1.00\n"
        "0.00 = 不補充\n0.50 = 將低頻原始訊號的 50% 補到高頻\n"
        "建議：一般用途維持 0.00；只有需要額外邊緣感時才提高到 0.01 ～ 0.03。",
        minimum=0.0, maximum=1.0, step=0.01,
    )

    gui.add_entry(
        rumble_content, 6, "LF 頻率", gui.lf_frequency_var,
        "設定低頻震動成分的頻率；此 9-bit 數值可近似以 Hz 理解，"
        "例如 215 約代表 215 Hz。\n\n"
        "設定範圍：0 ～ 511\n"
        "建議：一般用途 205 ～ 220；較柔和可用 190 ～ 205。\n"
        "實際觸感仍會受控制器韌體、致動器與外殼共振影響；"
        "在部分控制器上，低於約 180 的差異可能不明顯。",
        minimum=0, maximum=511, step=1, number_format=".0f",
    )
    gui.add_entry(
        rumble_content, 7, "HF 頻率", gui.hf_frequency_var,
        "設定高頻震動成分的頻率；此 9-bit 數值可近似以 Hz 理解，"
        "例如 330 約代表 330 Hz。\n\n"
        "設定範圍：0 ～ 511\n"
        "建議：一般用途 315 ～ 325；較銳利可用 325 ～ 335。\n"
        "實際觸感仍會受控制器韌體、致動器與外殼共振影響；"
        "高於約 350 時較容易出現尖銳感、異音或不自然共振。",
        minimum=0, maximum=511, step=1, number_format=".0f",
    )
    gui.add_entry(
        rumble_content, 8, "最大振幅", gui.max_amplitude_var,
        "限制低頻與高頻最多能震多強，相當於整體震動的安全上限。\n\n"
        "設定範圍：0 ～ 1023\n"
        "目前內建方案以 800 為上限；若偶爾出現撞擊聲，可降至 650 ～ 750。\n"
        "數值越高，最強震動越大；接近 1023 時可能增加異音、共振與硬體負擔。",
        minimum=0, maximum=1023, step=10, number_format=".0f",
    )
