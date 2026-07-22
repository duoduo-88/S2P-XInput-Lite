import tkinter as tk
from tkinter import ttk

def build_gyro_section(gui, mapping_notebook):
    """Build the gyroscope mapping tab."""
    # =========================
    # 頁籤 4：陀螺儀映射
    # =========================
    gyro_frame = ttk.Frame(mapping_notebook, padding=(12, 10))
    mapping_notebook.add(gyro_frame, text=" 陀螺儀映射 ")
    gyro_frame.columnconfigure(0, weight=1)

    activation_frame = ttk.LabelFrame(
        gyro_frame, text="啟動方式", padding=(10, 8)
    )
    activation_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    activation_content = ttk.Frame(activation_frame)
    activation_content.pack(anchor="center")
    ttk.Label(activation_content, text="按鍵").grid(
        row=0, column=0, padx=(0, 5)
    )
    gui.gyro_activation_button_entry = ttk.Entry(
        activation_content,
        textvariable=gui.gyro_activation_buttons_summary_var,
        state="readonly",
        width=7,
        justify="center",
    )
    gui.gyro_activation_button_entry.grid(row=0, column=1)
    gui.gyro_activation_select_button = ttk.Button(
        activation_content,
        text="…",
        width=2,
        command=lambda: gui.open_gyro_button_selector("activation"),
    )
    gui.gyro_activation_select_button.grid(
        row=0, column=2, padx=(2, 7)
    )
    for column, (text, value) in enumerate((
        ("關閉", "OFF"), ("按住", "HOLD"), ("切換", "TOGGLE")
    ), start=3):
        ttk.Radiobutton(
            activation_content,
            text=text,
            value=value,
            variable=gui.gyro_activation_mode_var,
        ).grid(row=0, column=column, padx=2)
    gui.create_help(
        activation_content,
        "關閉：不產生任何陀螺儀映射輸出。\n\n"
        "按住：選定按鍵符合任一／全部條件時啟用。\n\n"
        "切換：條件由未成立變成成立時切換一次。\n\n"
        "可選多顆按鍵；原本的 Xbox／鍵盤映射仍會保留。"
    ).grid(row=0, column=6, padx=(2, 0))

    def update_activation_button_state(*_args):
        disabled = gui.gyro_activation_mode_var.get() == "OFF"
        gui.gyro_activation_button_entry.configure(
            state="disabled" if disabled else "readonly"
        )
        gui.gyro_activation_select_button.configure(
            state="disabled" if disabled else "normal"
        )
    gui.gyro_activation_mode_var.trace_add(
        "write", update_activation_button_state
    )
    update_activation_button_state()

    target_frame = ttk.LabelFrame(
        gyro_frame, text="輸出目標", padding=(10, 8)
    )
    target_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    target_content = ttk.Frame(target_frame)
    target_content.pack(anchor="center")
    for column, (text, value) in enumerate((
        ("左搖桿", "LEFT_STICK"),
        ("右搖桿", "RIGHT_STICK"),
        ("滑鼠", "MOUSE"),
    )):
        ttk.Radiobutton(
            target_content,
            text=text,
            value=value,
            variable=gui.gyro_target_var,
        ).grid(row=0, column=column, padx=10)

    invert_content = ttk.Frame(target_content)
    invert_content.grid(
        row=1, column=0, columnspan=3, pady=(6, 0)
    )
    ttk.Checkbutton(
        invert_content, text="反轉 X", variable=gui.gyro_invert_x_var
    ).pack(side="left", padx=8)
    ttk.Checkbutton(
        invert_content, text="反轉 Y", variable=gui.gyro_invert_y_var
    ).pack(side="left", padx=8)
    gui.gyro_player_space_button = ttk.Checkbutton(
        invert_content,
        text="傾斜軸補償",
        variable=gui.gyro_player_space_var,
    )
    gui.gyro_player_space_button.pack(side="left", padx=(8, 4))
    gui.gyro_player_space_help = gui.create_help(
        invert_content,
        "手把傾斜時，自動修正左右與上下的方向，減少斜拿手把造成的串軸。\n"
        "關閉後會固定使用感測器的 X／Z 軸。"
    )
    gui.gyro_player_space_help.pack(side="left")

    motion_mode_frame = ttk.LabelFrame(
        gyro_frame, text="控制模式", padding=(10, 8)
    )
    gui.gyro_motion_mode_frame = motion_mode_frame
    motion_mode_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
    motion_mode_content = ttk.Frame(motion_mode_frame)
    motion_mode_content.pack(anchor="center")
    gui.gyro_center_mode_button = ttk.Radiobutton(
        motion_mode_content,
        text="回中（瞄準）",
        value="CENTER",
        variable=gui.gyro_motion_mode_var,
    )
    gui.gyro_center_mode_button.pack(side="left", padx=10)
    gui.gyro_tilt_mode_button = ttk.Radiobutton(
        motion_mode_content,
        text="傾斜（方向盤）",
        value="TILT",
        variable=gui.gyro_motion_mode_var,
    )
    gui.gyro_tilt_mode_button.pack(side="left", padx=10)
    gui.gyro_motion_mode_help = gui.create_help(
        motion_mode_content,
        "回中：依陀螺儀角速度輸出，停止轉動便自動回中，適合瞄準。\n\n"
        "傾斜：以手把相對於啟用瞬間的傾斜角度控制搖桿，適合方向盤；"
        "僅支援左右搖桿輸出。\n\n"
        "自訂重設按鍵會專門用於重設中立，不再送出原本的遊戲映射。"
    )
    gui.gyro_motion_mode_help.pack(side="left", padx=(6, 0))

    tilt_axis_content = ttk.Frame(motion_mode_frame)
    tilt_axis_content.pack(anchor="center", pady=(6, 0))
    gui.gyro_tilt_axis_widgets = []
    for text, value in (
        ("僅水平", "HORIZONTAL"), ("水平＋垂直", "DUAL")
    ):
        widget = ttk.Radiobutton(
            tilt_axis_content,
            text=text,
            value=value,
            variable=gui.gyro_tilt_axis_var,
        )
        widget.pack(side="left", padx=8)
        gui.gyro_tilt_axis_widgets.append(widget)
    tilt_recenter_content = ttk.Frame(motion_mode_frame)
    tilt_recenter_content.pack(anchor="center", pady=(6, 0))
    gui.gyro_tilt_recenter_button = ttk.Button(
        tilt_recenter_content,
        text="重設中立",
        width=9,
        command=gui.reset_tilt_neutral,
    )
    gui.gyro_tilt_recenter_button.pack(side="left", padx=(0, 12))
    gui.gyro_tilt_recenter_label = ttk.Label(
        tilt_recenter_content, text="自訂按鍵"
    )
    gui.gyro_tilt_recenter_label.pack(side="left", padx=(0, 5))
    gui.gyro_tilt_recenter_combo = ttk.Combobox(
        tilt_recenter_content,
        textvariable=gui.gyro_tilt_recenter_button_var,
        values=gui.gyro_tilt_recenter_button_options,
        state="readonly",
        width=9,
    )
    gui.gyro_tilt_recenter_combo.pack(side="left")

    gyro_settings_frame = ttk.LabelFrame(
        gyro_frame, text="陀螺儀反應", padding=(10, 8)
    )
    gyro_settings_frame.grid(row=3, column=0, sticky="ew")
    gyro_settings_frame.columnconfigure(0, weight=1)
    gyro_settings_content = ttk.Frame(gyro_settings_frame)
    gyro_settings_content.grid(
        row=0, column=0, sticky="ns"
    )

    gyro_rows = (
        ("搖桿感度", gui.gyro_stick_sensitivity_var, 0.1, 10.0, 0.1, ".1f",
         "回中模式：陀螺儀角速度轉成搖桿偏移的倍率。\n"
         "僅用於回中模式。", "CENTER_STICK"),
        ("滑鼠感度", gui.gyro_mouse_sensitivity_var, 0.5, 30.0, 0.5, ".1f",
         "每轉動一度對應的游標像素倍率。\n數值越高，游標移動越快。", "MOUSE"),
        ("最大傾角", gui.gyro_tilt_max_angle_var, 10.0, 60.0, 1.0, ".0f",
         "傾斜到此角度時輸出滿值。\n數值越小，較小的傾斜就能達到滿輸出。", "TILT"),
        ("X 比例", gui.gyro_x_ratio_var, 0.5, 2.0, 0.05, ".2f",
         "水平陀螺儀感度相對倍率。1.00 = 不改變。", "ALL"),
        ("Y 比例", gui.gyro_y_ratio_var, 0.5, 2.0, 0.05, ".2f",
         "垂直陀螺儀感度相對倍率。1.00 = 不改變。", "Y_AXIS"),
        ("死區", gui.gyro_deadzone_var, 0.0, 5.0, 0.1, ".1f",
         "忽略小於此角速度的感測器漂移。單位：度／秒。\n"
         "自適應功能會在明確轉動時縮小死區。\n數值越高，需要更明顯的轉動才會開始輸出。", "CENTER"),
        ("反死區", gui.gyro_stick_anti_deadzone_var, 0.0, 30.0, 1.0, ".0f",
         "補償遊戲內建搖桿死區；只作用於回中搖桿輸出，不改變實體搖桿。\n"
         "0 = 不補償；數值越高，越容易跨過遊戲內建的搖桿死區。", "CENTER_STICK"),
        ("傾斜死區", gui.gyro_tilt_deadzone_var, 0.0, 5.0, 0.1, ".1f",
         "忽略中立角度附近的小幅傾斜。單位：度。\n數值越高，中立附近越不容易誤觸。", "TILT"),
        ("平滑 ms", gui.gyro_smoothing_var, 0.0, 100.0, 5.0, ".0f",
         "回中模式的速度自適應平滑：慢速使用設定值，快速轉動會降至約 5 ms。\n0 = 關閉；數值越高，慢速瞄準越穩但反應越柔和。", "CENTER"),
        ("傾斜平滑", gui.gyro_tilt_smoothing_var, 0.0, 150.0, 5.0, ".0f",
         "傾斜模式的時間制低通平滑。0 = 關閉。\n"
         "數值越高，傾斜輸出越平穩但反應越慢。", "TILT"),
    )
    gyro_scoped_rows = []
    for row_index, (
        label, variable, minimum, maximum, step, number_format,
        help_text, target_scope
    ) in enumerate(gyro_rows):
        label_widget = ttk.Label(
            gyro_settings_content, text=label, width=8, anchor="e"
        )
        label_widget.grid(row=row_index, column=0, sticky="e", pady=2)

        def snap_gyro_value(
            value,
            current_variable=variable,
            current_minimum=minimum,
            current_maximum=maximum,
            current_step=step,
            current_format=number_format,
        ):
            numeric = max(
                current_minimum, min(current_maximum, float(value))
            )
            numeric = round(numeric / current_step) * current_step
            current_variable.set(format(numeric, current_format))

        scale = ttk.Scale(
            gyro_settings_content,
            from_=minimum,
            to=maximum,
            variable=variable,
            command=snap_gyro_value,
            orient="horizontal",
            length=190,
        )
        scale.grid(row=row_index, column=1, padx=8, pady=2)
        value_label = ttk.Label(
            gyro_settings_content,
            width=6,
            anchor="w",
            text=format(float(variable.get()), number_format),
        )
        value_label.grid(row=row_index, column=2, sticky="w", pady=2)

        def update_gyro_value_label(
            *args,
            current_variable=variable,
            current_label=value_label,
            current_format=number_format,
        ):
            try:
                current_label.configure(
                    text=format(float(current_variable.get()), current_format)
                )
            except (ValueError, tk.TclError):
                pass

        variable.trace_add("write", update_gyro_value_label)
        help_widget = gui.create_help(gyro_settings_content, help_text)
        help_widget.grid(row=row_index, column=3, pady=2)
        gui.bind_slider_value_editor(
            value_label,
            variable,
            label,
            minimum,
            maximum,
            help_text,
            step=step,
            number_format=number_format,
        )
        gyro_scoped_rows.append((
            target_scope, (label_widget, scale, value_label, help_widget)
        ))

    gyro_action_row = ttk.Frame(gyro_settings_content)
    gyro_action_row.grid(
        row=len(gyro_rows), column=0, columnspan=4, pady=(10, 2)
    )
    gui.gyro_calibration_button = ttk.Button(
        gyro_action_row,
        text="感測器校正",
        command=gui.calibrate_gyro,
    )
    gui.gyro_calibration_button.pack(side="left", padx=(0, 6))
    gui.gyro_curve_button = ttk.Button(
        gyro_action_row,
        text="感度曲線",
        command=gui.open_gyro_curve_window,
    )
    gui.gyro_curve_button.pack(side="left", padx=(0, 6))
    stability_frame = ttk.LabelFrame(
        gyro_frame, text="穩定控制", padding=(10, 8)
    )
    stability_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
    stability_content = ttk.Frame(stability_frame)
    stability_content.pack(anchor="center")
    stability_rows = (
        (
            "加速抑制", gui.gyro_accel_suppression_var,
            0.0, 100.0, 5.0,
            "九軸或傾斜軸補償快速轉動、震動時，降低加速度計對姿態的拉動。\n"
            "0 = 關閉；數值越高，快速移動時越不容易被加速度拉偏。", "FUSION",
        ),
        (
            "自適死區", gui.gyro_adaptive_deadzone_var,
            0.0, 100.0, 5.0,
            "回中模式靜止時保留死區，明確轉動時自動縮小。\n"
            "0 = 固定死區；數值越高，明確轉動時死區縮小得越多。", "CENTER",
        ),
        (
            "防晃 ms", gui.gyro_button_freeze_var,
            0.0, 120.0, 5.0,
            "設定的額外按鍵按下或放開時，短暫停止陀螺儀輸出。\n"
            "0 = 關閉；數值越高，額外按鍵動作後暫停輸出的時間越長。", "ALL",
        ),
    )
    stability_scoped_rows = []
    for row_index, (
        label, variable, minimum, maximum, step, help_text, target_scope
    ) in enumerate(stability_rows):
        label_widget = ttk.Label(
            stability_content, text=label, width=8, anchor="e"
        )
        label_widget.grid(row=row_index, column=0, sticky="e", pady=3)

        def snap_stability_value(
            value,
            current_variable=variable,
            current_minimum=minimum,
            current_maximum=maximum,
            current_step=step,
        ):
            numeric = max(
                current_minimum, min(current_maximum, float(value))
            )
            numeric = round(numeric / current_step) * current_step
            current_variable.set(f"{numeric:.0f}")

        scale = ttk.Scale(
            stability_content,
            from_=minimum,
            to=maximum,
            variable=variable,
            command=snap_stability_value,
            orient="horizontal",
            length=190,
        )
        scale.grid(row=row_index, column=1, padx=8, pady=3)
        value_label = ttk.Label(
            stability_content, width=6, anchor="w",
            text=f"{float(variable.get()):.0f}",
        )
        value_label.grid(row=row_index, column=2, sticky="w", pady=3)

        def update_stability_value_label(
            *args,
            current_variable=variable,
            current_label=value_label,
        ):
            try:
                current_label.configure(
                    text=f"{float(current_variable.get()):.0f}"
                )
            except (ValueError, tk.TclError):
                pass

        variable.trace_add("write", update_stability_value_label)
        help_widget = gui.create_help(stability_content, help_text)
        help_widget.grid(row=row_index, column=3, pady=3)
        gui.bind_slider_value_editor(
            value_label,
            variable,
            label,
            minimum,
            maximum,
            help_text,
            step=step,
            number_format=".0f",
        )
        stability_scoped_rows.append((
            target_scope, (label_widget, scale, value_label, help_widget)
        ))

    stabilization_key_content = ttk.Frame(stability_content)
    stabilization_key_content.grid(
        row=len(stability_rows), column=0, columnspan=4, pady=(4, 0)
    )
    ttk.Label(stabilization_key_content, text="額外按鍵").pack(
        side="left", padx=(0, 5)
    )
    gui.gyro_stabilization_button_entry = ttk.Entry(
        stabilization_key_content,
        textvariable=gui.gyro_stabilization_buttons_summary_var,
        state="readonly",
        width=7,
        justify="center",
    )
    gui.gyro_stabilization_button_entry.pack(side="left")
    ttk.Button(
        stabilization_key_content,
        text="…",
        width=2,
        command=lambda: gui.open_gyro_button_selector("stabilization"),
    ).pack(side="left", padx=(2, 0))
    stabilization_key_help = gui.create_help(
        stabilization_key_content,
        "可選多顆容易造成手把晃動的按鍵。\n"
        "任一按鍵按下或放開都會短暫停止陀螺儀，原映射仍保留。"
    )
    stabilization_key_help.pack(side="left", padx=(6, 0))
    gui.gyro_reset_button = ttk.Button(
        stabilization_key_content,
        text="還原陀螺儀預設",
        command=gui.reset_gyro_mapping,
    )
    gui.gyro_reset_button.pack(side="left", padx=(6, 0))

    def update_gyro_target_state(*args):
        target = gui.gyro_target_var.get().strip().upper()
        is_mouse = target == "MOUSE"
        if is_mouse and gui.gyro_motion_mode_var.get() == "TILT":
            gui.gyro_motion_mode_var.set("CENTER")
        mode_state = ["disabled"] if is_mouse else ["!disabled"]
        gui.gyro_motion_mode_frame.state(mode_state)
        gui.gyro_center_mode_button.state(mode_state)
        gui.gyro_tilt_mode_button.state(mode_state)
        gui.gyro_motion_mode_help.configure(
            state="disabled" if is_mouse else "normal"
        )
        motion_mode = gui.gyro_motion_mode_var.get().strip().upper()
        curve_enabled = (
            gui.gyro_activation_mode_var.get() != "OFF"
            and motion_mode == "CENTER"
            and not is_mouse
        )
        gui.gyro_curve_button.state(
            ["!disabled"] if curve_enabled else ["disabled"]
        )
        player_space_enabled = motion_mode == "CENTER"
        gui.gyro_player_space_button.state(
            ["!disabled"] if player_space_enabled else ["disabled"]
        )
        gui.gyro_player_space_help.configure(
            state="normal" if player_space_enabled else "disabled"
        )
        tilt_enabled = motion_mode == "TILT" and not is_mouse
        for widget in gui.gyro_tilt_axis_widgets:
            widget.state(["!disabled"] if tilt_enabled else ["disabled"])
        gui.gyro_tilt_recenter_button.state(
            ["!disabled"] if tilt_enabled else ["disabled"]
        )
        shortcut_enabled = not is_mouse
        gui.gyro_tilt_recenter_label.state(
            ["!disabled"] if shortcut_enabled else ["disabled"]
        )
        gui.gyro_tilt_recenter_combo.state(
            ["!disabled", "readonly"]
            if shortcut_enabled else ["disabled"]
        )
        dual_axis = gui.gyro_tilt_axis_var.get() == "DUAL"
        for scope, widgets in gyro_scoped_rows:
            visible = (
                scope == "ALL"
                or (scope == "MOUSE" and is_mouse)
                or (scope == "CENTER" and motion_mode == "CENTER")
                or (
                    scope == "CENTER_STICK"
                    and motion_mode == "CENTER"
                    and not is_mouse
                )
                or (scope == "TILT" and tilt_enabled)
                or (
                    scope == "Y_AXIS"
                    and (motion_mode == "CENTER" or dual_axis)
                )
            )
            for widget in widgets:
                if visible:
                    widget.grid()
                else:
                    widget.grid_remove()
        for scope, widgets in stability_scoped_rows:
            visible = (
                scope == "ALL"
                or (scope == "CENTER" and motion_mode == "CENTER")
                or (scope == "TILT" and tilt_enabled)
                or (
                    scope == "FUSION"
                    and (
                        tilt_enabled
                        or (
                            motion_mode == "CENTER"
                            and gui.gyro_player_space_var.get()
                        )
                    )
                )
            )
            for widget in widgets:
                if visible:
                    widget.grid()
                else:
                    widget.grid_remove()

    gui.gyro_activation_mode_var.trace_add(
        "write", update_gyro_target_state
    )
    gui.gyro_target_var.trace_add("write", update_gyro_target_state)
    gui.gyro_motion_mode_var.trace_add("write", update_gyro_target_state)
    gui.gyro_tilt_axis_var.trace_add("write", update_gyro_target_state)
    gui.gyro_player_space_var.trace_add("write", update_gyro_target_state)
    update_gyro_target_state()
