import tkinter as tk
from tkinter import ttk


AUDIO_ATTACK_VALUES = (
    1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 35, 50, 75, 100,
    150, 200, 300, 500,
)
AUDIO_RELEASE_VALUES = (
    5, 10, 20, 35, 45, 50, 75, 100, 110, 120, 150, 175,
    200, 225, 300, 500, 750, 1000, 1500, 2000,
)
AUDIO_TAIL_DECAY_VALUES = (
    50, 75, 100, 125, 150, 175, 200, 225, 300, 400, 500,
    750, 1000, 1500, 2000,
)


def build_audio_haptics_section(gui, mapping_notebook):
    """Build the advanced audio-haptics tab."""
    # =========================
    # 頁籤 4：進階震動
    # =========================
    audio_haptics_frame = ttk.Frame(mapping_notebook, padding=(12, 8))
    mapping_notebook.add(audio_haptics_frame, text=" 進階震動 ")
    audio_haptics_frame.columnconfigure(0, weight=1)

    mode_frame = ttk.LabelFrame(
        audio_haptics_frame, text="輸出來源", padding=(10, 7)
    )
    mode_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    mode_frame.columnconfigure(0, weight=1)
    mode_content = ttk.Frame(mode_frame)
    gui.audio_haptics_mode_content = mode_content
    mode_content.grid(row=0, column=0, sticky="ns")
    for column, (label_text, value) in enumerate((
        ("遊戲", "GAME"), ("音訊", "AUDIO"), ("混合", "MIX")
    )):
        ttk.Radiobutton(
            mode_content,
            text=label_text,
            value=value,
            variable=gui.audio_haptics_mode_var,
        ).grid(row=0, column=column, padx=12, sticky="w")
    gui.create_help(
        mode_content,
        "遊戲：只輸出遊戲原生震動。\n\n"
        "音訊：只將 Windows 預設輸出裝置的聲音轉成震動。\n\n"
        "混合：以柔和飽和方式合併遊戲與音訊震動，避免直接相加造成削波。"
    ).grid(row=0, column=3, padx=(8, 0))

    def add_audio_scale_row(
        parent, row_index, label_text, variable,
        minimum, maximum, step, number_format, help_text,
        allowed_values=None,
        length=190,
        label_width=7,
        show_help=True,
    ):
        label_widget = ttk.Label(
            parent, text=label_text, width=label_width, anchor="e"
        )
        label_widget.grid(row=row_index, column=0, sticky="e", pady=2)

        allowed_values = tuple(allowed_values or ())

        def snap_audio_value(value):
            if allowed_values:
                index = max(
                    0,
                    min(len(allowed_values) - 1, int(round(float(value)))),
                )
                numeric = allowed_values[index]
            else:
                numeric = max(minimum, min(maximum, float(value)))
                numeric = round(numeric / step) * step
            variable.set(format(numeric, number_format))

        if allowed_values:
            try:
                initial = float(variable.get())
            except (ValueError, TypeError, tk.TclError):
                initial = float(allowed_values[0])
            scale_variable = tk.DoubleVar(
                value=min(
                    range(len(allowed_values)),
                    key=lambda index: abs(allowed_values[index] - initial),
                )
            )
            scale_minimum = 0
            scale_maximum = len(allowed_values) - 1
        else:
            scale_variable = variable
            scale_minimum = minimum
            scale_maximum = maximum

        scale = ttk.Scale(
            parent,
            from_=scale_minimum,
            to=scale_maximum,
            variable=scale_variable,
            command=snap_audio_value,
            orient="horizontal",
            length=length,
        )
        # Discrete time sliders store an index in the Scale while ``variable``
        # stores the real millisecond value.  Keep the real setting attached so
        # the shared right-click reset menu can resolve it correctly.
        scale._s2p_parameter_variable = variable
        scale.grid(row=row_index, column=1, padx=(8, 8), pady=2)
        value_label = ttk.Label(
            parent, width=6, anchor="w",
            text=format(float(variable.get()), number_format),
        )
        value_label.grid(row=row_index, column=2, sticky="w", pady=2)

        def update_value_label(*_args):
            try:
                value_label.configure(
                    text=format(float(variable.get()), number_format)
                )
            except (ValueError, tk.TclError):
                pass

        variable.trace_add("write", update_value_label)
        if allowed_values:
            def sync_discrete_scale(*_args):
                try:
                    current = float(variable.get())
                    index = min(
                        range(len(allowed_values)),
                        key=lambda item: abs(allowed_values[item] - current),
                    )
                    if int(round(scale_variable.get())) != index:
                        scale_variable.set(index)
                except (ValueError, TypeError, tk.TclError):
                    pass

            variable.trace_add("write", sync_discrete_scale)
            scale._discrete_value_variable = scale_variable
        help_widget = None
        if show_help:
            help_widget = gui.create_help(parent, help_text)
            help_widget.grid(row=row_index, column=3, sticky="w", pady=2)
        gui.bind_slider_value_editor(
            value_label,
            variable,
            label_text,
            minimum,
            maximum,
            help_text,
            step=None if allowed_values else step,
            number_format=number_format,
            allowed_values=allowed_values or None,
        )
        widgets = [label_widget, scale, value_label]
        if help_widget is not None:
            widgets.append(help_widget)
        return widgets

    response_frame = ttk.LabelFrame(
        audio_haptics_frame, text="音訊反應", padding=(10, 6)
    )
    gui.audio_haptics_response_frame = response_frame
    response_frame.grid(row=1, column=0, sticky="ew", pady=(0, 6))
    response_frame.columnconfigure(0, weight=1)
    response_content = ttk.Frame(response_frame)
    gui.audio_haptics_response_content = response_content
    response_content.grid(row=0, column=0, sticky="ns")
    audio_rows = (
        ("混合", gui.audio_haptics_mix_ratio_var, 0.0, 1.0, 0.05, ".2f",
         "只在「混合」模式生效。\n\n"
         "0.00 = 只保留遊戲原生震動\n"
         "數值越高，音訊震動所占比例越多\n"
         "1.00 = 只保留音訊震動"),
        ("強度", gui.audio_haptics_strength_var, 0.0, 1.0, 0.05, ".2f",
         "控制音訊轉換成震動前的總靈敏度。\n\n"
         "降低：整體音訊震動較弱\n"
         "提高：較小的聲音也會產生明顯震動"),
        ("噪閘", gui.audio_haptics_noise_gate_var, 0.0, 0.25, 0.005, ".3f",
         "低於此音量的背景聲不會產生震動。\n\n"
         "提高：過濾更多背景聲\n"
         "降低：較小的聲音也能觸發震動"),
        ("啟動", gui.audio_haptics_attack_var, 1.0, 500.0, 1.0, ".0f",
         "聲音出現後，震動升起所需的反應時間。\n\n"
         "降低：反應更快、更銳利\n"
         "提高：較平順，但可能弱化短促音效\n"
         "建議：一般用途 3 ～ 6 ms；節奏用途 1 ～ 3 ms。\n"
         "拉桿在常用低延遲區較密，之後逐步加大間距。",
         AUDIO_ATTACK_VALUES),
        ("音訊釋放", gui.audio_haptics_release_var, 5.0, 2000.0, 5.0, ".0f",
         "只平滑音訊分析結果的下降速度。\n\n"
         "降低：停止更乾脆\n提高：下降更平順，但會拖得較久。\n"
         "建議：節奏用途 35 ～ 75 ms；一般用途 100 ～ 150 ms。\n"
         "拉桿採非線性刻度，例如 5 → 10 → 20 → 35 ms。",
         AUDIO_RELEASE_VALUES),
    )
    gui.audio_mix_ratio_widgets = []
    gui.audio_haptics_response_widgets = []
    for row_index, row_data in enumerate(audio_rows):
        row_widgets = add_audio_scale_row(
            response_content, row_index, *row_data
        )
        gui.audio_haptics_response_widgets.extend(row_widgets)
        if row_index == 0:
            gui.audio_mix_ratio_widgets = row_widgets

    eq_frame = ttk.LabelFrame(
        audio_haptics_frame, text="六頻段調整", padding=(10, 6)
    )
    gui.audio_haptics_eq_frame = eq_frame
    eq_frame.grid(row=2, column=0, sticky="ew", pady=(0, 6))
    eq_frame.columnconfigure(0, weight=1)
    eq_content = ttk.Frame(eq_frame)
    eq_content.grid(row=0, column=0, sticky="ns")
    eq_content.columnconfigure(0, weight=1)
    eq_canvas = tk.Canvas(
        eq_content, width=320, height=165, background="#FFFFFF",
        highlightthickness=1, highlightbackground="#A0A0A0",
    )
    gui.audio_haptics_eq_canvas = eq_canvas
    eq_canvas.grid(row=0, column=0)
    band_labels = ("Low", "L-Mid", "Mid", "H-Mid", "High", "Ultra")
    chart_left, chart_top = 32, 14
    chart_right, chart_bottom = 306, 128
    dragged_band = {"index": None}

    def audio_eq_enabled():
        return gui.audio_haptics_mode_var.get().strip().upper() in (
            "AUDIO", "MIX"
        )

    def band_x(index):
        return chart_left + (
            (chart_right - chart_left) * index / (len(band_labels) - 1)
        )

    def draw_audio_eq(*_args):
        enabled = audio_eq_enabled()
        eq_canvas.configure(
            background="#FFFFFF" if enabled else "#F2F2F2",
            cursor="hand2" if enabled else "",
        )
        eq_canvas.delete("all")
        grid_color = "#E2E2E2" if enabled else "#D8D8D8"
        curve_color = "#1976D2" if enabled else "#9E9E9E"
        text_color = "#555555" if enabled else "#909090"
        for gain in (0.0, 0.5, 1.0, 1.5, 2.0):
            y = chart_bottom - (
                (chart_bottom - chart_top) * gain / 2.0
            )
            eq_canvas.create_line(
                chart_left, y, chart_right, y, fill=grid_color
            )
            eq_canvas.create_text(
                4, y, text=f"{gain:g}", anchor="w",
                fill=text_color, font=("Segoe UI", 8),
            )
        points = []
        gains = []
        for index, variable in enumerate(
            gui.audio_haptics_band_gain_vars
        ):
            try:
                gain = max(0.0, min(2.0, float(variable.get())))
            except (ValueError, tk.TclError):
                gain = 1.0
            gains.append(gain)
            x = band_x(index)
            y = chart_bottom - (
                (chart_bottom - chart_top) * gain / 2.0
            )
            points.extend((x, y))
            eq_canvas.create_line(
                x, chart_top, x, chart_bottom, fill=grid_color
            )
        eq_canvas.create_line(
            *points, fill=curve_color, width=3, smooth=True
        )
        for index, gain in enumerate(gains):
            x = band_x(index)
            y = chart_bottom - (
                (chart_bottom - chart_top) * gain / 2.0
            )
            eq_canvas.create_oval(
                x - 6, y - 6, x + 6, y + 6,
                fill=curve_color, outline="#FFFFFF", width=2,
            )
            value_y = y + 15 if y < chart_top + 20 else y - 14
            eq_canvas.create_text(
                x, value_y, text=f"{gain:.2f}",
                fill=text_color, font=("Segoe UI", 8),
            )
            eq_canvas.create_text(
                x, 148, text=band_labels[index],
                fill=text_color, font=("Segoe UI", 8),
            )

    def update_dragged_band(event):
        index = dragged_band["index"]
        if index is None or not audio_eq_enabled():
            return
        y = max(chart_top, min(chart_bottom, event.y))
        gain = 2.0 * (
            chart_bottom - y
        ) / (chart_bottom - chart_top)
        gain = round(gain / 0.05) * 0.05
        gui.audio_haptics_band_gain_vars[index].set(
            f"{max(0.0, min(2.0, gain)):.2f}"
        )

    def begin_audio_eq_drag(event):
        if not audio_eq_enabled():
            return
        index = min(
            range(len(band_labels)),
            key=lambda item: abs(event.x - band_x(item)),
        )
        if abs(event.x - band_x(index)) <= 28:
            dragged_band["index"] = index
            update_dragged_band(event)

    def end_audio_eq_drag(_event):
        dragged_band["index"] = None

    def reset_audio_eq_band(event):
        if not audio_eq_enabled():
            return
        nearest_index = None
        nearest_distance = None
        for index, variable in enumerate(
            gui.audio_haptics_band_gain_vars
        ):
            try:
                gain = max(0.0, min(2.0, float(variable.get())))
            except (ValueError, tk.TclError):
                gain = 1.0
            point_y = chart_bottom - (
                (chart_bottom - chart_top) * gain / 2.0
            )
            x_distance = abs(event.x - band_x(index))
            y_distance = abs(event.y - point_y)
            distance = x_distance + y_distance
            if (
                x_distance <= 14
                and y_distance <= 14
                and (
                    nearest_distance is None
                    or distance < nearest_distance
                )
            ):
                nearest_index = index
                nearest_distance = distance
        if nearest_index is not None:
            dragged_band["index"] = None
            defaults = gui.audio_haptics_defaults_for_mode()
            gui.audio_haptics_band_gain_vars[nearest_index].set(
                defaults["band_gains"][nearest_index]
            )

    eq_canvas.bind("<Button-1>", begin_audio_eq_drag)
    eq_canvas.bind("<Double-Button-1>", reset_audio_eq_band)
    eq_canvas.bind("<B1-Motion>", update_dragged_band)
    eq_canvas.bind("<ButtonRelease-1>", end_audio_eq_drag)
    for variable in gui.audio_haptics_band_gain_vars:
        variable.trace_add("write", draw_audio_eq)

    routing_content = ttk.Frame(eq_content)
    routing_content.grid(row=1, column=0, pady=(5, 0))
    routing_widgets = add_audio_scale_row(
        routing_content,
        0,
        "LF／HF 重心",
        gui.audio_haptics_lf_hf_balance_var,
        -1.0,
        1.0,
        0.05,
        ".2f",
        "調整中間音訊頻段分配到 LF 或 HF 震動的重心。\n\n"
        "-1.00：中間頻段較偏向 LF\n"
        "0.00：平衡分配\n"
        "+1.00：中間頻段較偏向 HF\n\n"
        "建議先在 -0.15 ～ +0.15 內調整。此設定不改變六個頻段的增益。",
        length=170,
        label_width=11,
        show_help=False,
    )
    gui.audio_haptics_response_widgets.extend(routing_widgets)

    eq_description = ttk.Label(
        eq_frame,
        text=(
            "六頻段：調整不同聲音範圍轉成震動的強弱。\n"
            "LF／HF 重心：調整中間頻段偏向低頻或高頻輸出。\n"
            "將游標移到這段文字，可查看完整對照與操作說明。"
        ),
        justify="left",
        wraplength=310,
        cursor="question_arrow",
    )
    eq_description.grid(
        row=1, column=0, sticky="ew", pady=(5, 0)
    )

    eq_region_help = (
        "六頻段與 LF／HF 重心完整說明\n\n"
        "圖表標籤與頻率對照：\n"
        "Low：20 ～ 120 Hz（低沉衝擊與重低音）\n"
        "L-Mid：120 ～ 300 Hz（低頻厚度）\n"
        "Mid：300 ～ 700 Hz（中低頻細節）\n"
        "H-Mid：700 ～ 2000 Hz（中高頻細節）\n"
        "High：2000 ～ 4000 Hz（高頻摩擦與人聲邊緣）\n"
        "Ultra：4000 ～ 8000 Hz（尖銳提示音與極高頻細節）\n\n"
        "拖曳控制點調整增益；雙擊控制點還原該頻段預設。\n"
        "1.00 代表原始增益。\n\n"
        "LF／HF 重心只改變中間頻段的輸出分配，不改變六段增益：\n"
        "-1.00 偏向 LF；0.00 平衡；+1.00 偏向 HF。\n"
        "建議先在 -0.15 ～ +0.15 內調整。\n\n"
        "本區只影響音訊與混合模式，不影響純遊戲震動。"
    )
    gui.bind_help_tooltip(eq_description, eq_region_help)

    gui.audio_haptics_response_widgets.append(eq_description)
    draw_audio_eq()

    final_frame = ttk.LabelFrame(
        audio_haptics_frame, text="最終輸出", padding=(10, 6)
    )
    final_frame.grid(row=3, column=0, sticky="ew", pady=(0, 6))
    final_frame.columnconfigure(0, weight=1)
    final_content = ttk.Frame(final_frame)
    final_content.grid(row=0, column=0, sticky="ns")
    add_audio_scale_row(
        final_content, 0, "餘震強度",
        gui.audio_haptics_final_tail_strength_var,
        0.0, 1.0, 0.05, ".2f",
        "餘震是震動訊號減弱或停止時，暫時保留一部分剛才的震動，"
        "再依「衰減 ms」逐漸降到 0，讓停止感較柔和。\n\n"
        "0.00 = 關閉，立即跟隨原始訊號\n"
        "0.50 = 保留約一半的下降落差\n"
        "1.00 = 完整保留後再逐漸衰減\n\n"
        "套用於遊戲與音訊混合後的最終輸出；不會產生新的重複震動。",
    )
    add_audio_scale_row(
        final_content, 1, "衰減 ms",
        gui.audio_haptics_final_tail_decay_var,
        50.0, 2000.0, 25.0, ".0f",
        "控制餘震逐漸消失的時間。\n\n"
        "只在餘震強度大於 0 時生效；數值越高，震動消失得越慢。\n"
        "建議：100 ～ 225 ms；拉桿在常用區較密，之後逐步加大間距。",
        AUDIO_TAIL_DECAY_VALUES,
    )

    footer = ttk.Frame(audio_haptics_frame)
    footer.grid(row=4, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    gui.audio_haptics_reset_button = ttk.Button(
        footer,
        text="還原進階震動預設",
        command=gui.reset_audio_haptics,
    )
    gui.audio_haptics_reset_button.grid(row=0, column=1, sticky="e")

    gui._audio_haptics_last_mode = (
        gui.audio_haptics_mode_var.get().strip().upper()
    )

    def update_audio_response_state(*_args):
        mode = gui.audio_haptics_mode_var.get().strip().upper()

        # Profile files own the complete [audio_haptics] section.  During
        # a profile reload every Tk variable is restored in a batch; the
        # intermediate mode write must therefore not be treated as a user
        # action or allowed to resize the notebook before the remaining
        # saved values have been copied.
        if getattr(gui, "_loading_profile_values", False):
            gui._audio_haptics_last_mode = mode
            return

        # Changing GAME / AUDIO / MIX only changes which controls are
        # active.  It must never inject preset values.  Presets are applied
        # only by the explicit "reset advanced haptics" action, so custom
        # values remain intact and can be stored in each profile.
        gui._audio_haptics_last_mode = mode
        enabled = mode in ("AUDIO", "MIX")
        response_frame.state(["!disabled"] if enabled else ["disabled"])
        eq_frame.state(["!disabled"] if enabled else ["disabled"])
        for widget in gui.audio_haptics_response_widgets:
            try:
                widget.state(["!disabled"] if enabled else ["disabled"])
            except (AttributeError, tk.TclError):
                try:
                    widget.configure(
                        state="normal" if enabled else "disabled"
                    )
                except tk.TclError:
                    pass
        visible = mode == "MIX"
        for widget in gui.audio_mix_ratio_widgets:
            if visible:
                widget.grid()
            else:
                widget.grid_remove()
        draw_audio_eq()

    # Profile reload calls this once after all saved values are restored.
    gui._update_audio_response_state = update_audio_response_state
    gui.audio_haptics_mode_var.trace_add(
        "write", update_audio_response_state
    )
    update_audio_response_state()
