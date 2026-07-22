import math
import tkinter as tk
from tkinter import ttk

def build_stick_mapping_section(gui, mapping_notebook):
    """Build the stick-direction mapping tab."""
    # =========================
    # 頁籤 2：搖桿方向映射
    # =========================
    stick_direction_frame = ttk.Frame(
        mapping_notebook,
        padding=(10, 4)
    )

    mapping_notebook.add(
        stick_direction_frame,
        text=" 搖桿方向映射 "
    )
    stick_direction_frame.columnconfigure(0, weight=1)

    # 左右兩個搖桿區塊
    left_direction_frame = ttk.LabelFrame(
        stick_direction_frame,
        text="左搖桿",
        padding=10
    )

    left_direction_frame.grid(
        row=0,
        column=0,
        pady=(0, 10),
        sticky="ew"
    )

    right_direction_frame = ttk.LabelFrame(
        stick_direction_frame,
        text="右搖桿",
        padding=10
    )

    right_direction_frame.grid(
        row=1,
        column=0,
        sticky="ew"
    )

    # 保存斜方向元件，
    # 之後切換 4 向 / 8 向時使用
    gui.stick_diagonal_widgets = {
        "LEFT": [],
        "RIGHT": [],
    }
    # 保存左右搖桿的模式刷新函式
    # 讓「還原預設」也能主動刷新介面
    gui.stick_direction_mode_updaters = {}
    gui.stick_mode_selectors = []

    def create_stick_direction_panel(
        parent,
        side
    ):

        # 左右搖桿的第三個模式不同
        if side == "LEFT":
            mode_values = [
                "4WAY",
                "8WAY",
                "映射為右搖桿",
                "映射為滑鼠",
                "MOUSE_WHEEL_LINEAR",
                "XINPUT_LT_LINEAR",
                "XINPUT_RT_LINEAR",
            ]
        else:
            mode_values = [
                "4WAY",
                "8WAY",
                "映射為左搖桿",
                "映射為滑鼠",
                "MOUSE_WHEEL_LINEAR",
                "XINPUT_LT_LINEAR",
                "XINPUT_RT_LINEAR",
            ]
        # =========================
        # 保存 8 個方向的下拉選單
        # =========================
        direction_combos = []

        # =========================
        # 方向下拉框建立函式
        # =========================
        def create_direction_combo(
            direction,
            row,
            column,
            symbol,
            diagonal=False
        ):
            cell = ttk.Frame(
                parent
            )

            cell.grid(
                row=row,
                column=column,
                padx=3,
                pady=8
            )

            ttk.Label(
                cell,
                text=symbol,
                anchor="center",
                font=(
                    "",
                    12,
                    "bold"
                )
            ).pack()

            direction_variable = (
                gui.stick_direction_vars[
                    side
                ][
                    direction
                ]
            )
            gui._track_custom_mapping_variable(
                direction_variable
            )

            combo = ttk.Combobox(
                cell,
                textvariable=direction_variable,
                values=(
                    gui.stick_direction_options
                ),
                state="readonly",
                width=9
            )

            combo.pack()
            
            direction_combos.append(
                combo
            )

            def on_direction_selected(
                event,
                current_variable=direction_variable
            ):
                if (
                    current_variable.get()
                    == "CUSTOM_KEYBOARD"
                ):
                    gui.open_keyboard_capture(
                        current_variable,
                        mouse_mode="wheel",
                    )

            combo.bind(
                "<<ComboboxSelected>>",
                on_direction_selected
            )

            if diagonal:
                gui.stick_diagonal_widgets[
                    side
                ].append(
                    cell
                )

            return cell

        # =========================
        # 3 × 3 方向配置
        # =========================

        # 左上
        create_direction_combo(
            "UP_LEFT",
            0,
            0,
            "↖",
            diagonal=True
        )

        # 上
        create_direction_combo(
            "UP",
            0,
            1,
            "↑"
        )

        # 右上
        create_direction_combo(
            "UP_RIGHT",
            0,
            2,
            "↗",
            diagonal=True
        )

        # 左
        create_direction_combo(
            "LEFT",
            1,
            0,
            "←"
        )

        # =========================
        # 中心模式選擇
        # =========================
        center_mode_frame = ttk.Frame(
            parent
        )

        center_mode_frame.grid(
            row=1,
            column=1
        )

        ttk.Label(
            center_mode_frame,
            text="模式"
        ).pack(
            pady=(0, 3)
        )

        # 黑色外框
        mode_combo_border = tk.Frame(
            center_mode_frame,
            bg="black",
            padx=5,
            pady=5
        )

        mode_combo_border.pack()

        # 原本的模式下拉選單
        mode_display_var = tk.StringVar(
            value=gui.stick_mode_label(
                gui.stick_direction_mode_vars[side].get()
            )
        )
        mode_combo = ttk.Combobox(
            mode_combo_border,
            textvariable=mode_display_var,
            values=tuple(gui.stick_mode_label(value) for value in mode_values),
            state="readonly",
            width=10
        )

        mode_combo.pack()
        gui.stick_mode_selectors.append(
            (mode_combo, mode_display_var, side, tuple(mode_values))
        )
        # 右
        create_direction_combo(
            "RIGHT",
            1,
            2,
            "→"
        )

        # 左下
        create_direction_combo(
            "DOWN_LEFT",
            2,
            0,
            "↙",
            diagonal=True
        )

        # 下
        create_direction_combo(
            "DOWN",
            2,
            1,
            "↓"
        )

        # 右下
        create_direction_combo(
            "DOWN_RIGHT",
            2,
            2,
            "↘",
            diagonal=True
        )

        # 模式專用設定區固定高度，切換模式時不改變頁面尺寸。
        for column in range(3):
            parent.columnconfigure(column, weight=1)
        settings_host = ttk.Frame(parent, height=96)
        settings_host.grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(2, 0)
        )
        settings_host.grid_propagate(False)

        direction_settings_frame = ttk.Frame(settings_host)
        mouse_settings_frame = ttk.Frame(settings_host)
        analog_settings_frame = ttk.Frame(settings_host)
        direction_settings_frame.columnconfigure(1, weight=1)
        mouse_settings_frame.columnconfigure(1, weight=1)

        preview = tk.Canvas(
            direction_settings_frame,
            width=88,
            height=88,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#a8a8a8",
        )
        preview.grid(row=0, column=0, padx=(2, 5))
        direction_controls = ttk.Frame(direction_settings_frame)
        direction_controls.grid(
            row=0, column=1, sticky="ew", padx=(0, 2)
        )
        direction_controls.columnconfigure(1, weight=1)

        def safe_float(variable, fallback):
            try:
                return float(variable.get())
            except (ValueError, TypeError, tk.TclError):
                return fallback

        value_labels = []

        def add_direction_slider(
            row, label_text, variable, minimum, maximum, command, formatter,
            step, number_format, help_text,
        ):
            ttk.Label(
                direction_controls,
                text=label_text,
                width=4,
                anchor="e",
            ).grid(row=row, column=0, padx=(0, 4), pady=2)
            ttk.Scale(
                direction_controls,
                from_=minimum,
                to=maximum,
                variable=variable,
                command=command,
                orient="horizontal",
                length=120,
            ).grid(
                row=row, column=1, padx=4, pady=2, sticky="ew"
            )
            value_label = ttk.Label(
                direction_controls, width=5, anchor="w"
            )
            value_label.grid(row=row, column=2, padx=(3, 0), pady=2)
            value_labels.append((value_label, variable, formatter))
            gui.bind_slider_value_editor(
                value_label,
                variable,
                label_text,
                minimum,
                maximum,
                help_text,
                step=step,
                number_format=number_format,
                setter=command,
            )

        def snap_trigger(value):
            numeric = max(0.10, min(1.0, round(float(value) * 100.0) / 100.0))
            release_var = gui.stick_direction_release_vars[side]
            release = safe_float(release_var, 0.50)
            if release > numeric - 0.03:
                release_var.set(f"{max(0.0, numeric - 0.03):.2f}")
            gui.stick_direction_trigger_vars[side].set(f"{numeric:.2f}")

        def snap_release(value):
            trigger = safe_float(
                gui.stick_direction_trigger_vars[side], 0.60
            )
            numeric = max(
                0.0,
                min(trigger - 0.03, round(float(value) * 100.0) / 100.0),
            )
            gui.stick_direction_release_vars[side].set(f"{numeric:.2f}")

        def snap_direction_deadzone(value):
            numeric = max(0.0, min(20.0, round(float(value))))
            gui.stick_direction_deadzone_vars[side].set(f"{numeric:.0f}")

        add_direction_slider(
            0,
            "死區",
            gui.stick_direction_deadzone_vars[side],
            0,
            20,
            snap_direction_deadzone,
            lambda value: f"{value:.0f}°",
            1,
            ".0f",
            "方向邊界兩側不觸發按鍵的角度範圍。\n\n"
            "設定範圍：0 ～ 20°；建議 3 ～ 8°。\n"
            "提高可減少方向交界處誤觸，但會增加沒有方向輸出的區域。",
        )
        add_direction_slider(
            1,
            "觸發",
            gui.stick_direction_trigger_vars[side],
            0.10,
            1.0,
            snap_trigger,
            lambda value: f"{value * 100:.0f}%",
            0.01,
            ".2f",
            "搖桿超過此行程比例後，才會觸發方向按鍵。\n\n"
            "設定範圍：0.10 ～ 1.00；建議 0.55 ～ 0.70。\n"
            "觸發值必須至少比放開值高 0.03。",
        )
        add_direction_slider(
            2,
            "放開",
            gui.stick_direction_release_vars[side],
            0.0,
            0.97,
            snap_release,
            lambda value: f"{value * 100:.0f}%",
            0.01,
            ".2f",
            "方向按鍵觸發後，搖桿回到此行程比例以下時放開。\n\n"
            "設定範圍：0.00 ～ 0.97；建議 0.45 ～ 0.60。\n"
            "必須至少比觸發值低 0.03，避免在邊界反覆切換。",
        )

        mouse_speed_label = ttk.Label(
            mouse_settings_frame,
            text="游標速度",
            width=8,
            anchor="e",
        )
        mouse_speed_label.grid(row=0, column=0, padx=(0, 4))

        def snap_mouse_speed(value):
            numeric = max(
                100.0,
                min(3000.0, round(float(value) / 50.0) * 50.0),
            )
            gui.stick_mouse_speed_vars[side].set(f"{numeric:.0f}")

        ttk.Scale(
            mouse_settings_frame,
            from_=100,
            to=3000,
            variable=gui.stick_mouse_speed_vars[side],
            command=snap_mouse_speed,
            orient="horizontal",
            length=120,
        ).grid(row=0, column=1, padx=4, sticky="ew")
        mouse_speed_value = ttk.Label(
            mouse_settings_frame, width=5, anchor="w"
        )
        mouse_speed_value.grid(row=0, column=2, padx=(3, 0))
        mouse_speed_help = (
            "控制搖桿映射為滑鼠或滾輪時的移動速度。\n\n"
            "設定範圍：100 ～ 3000，步進 50。\n"
            "數值越高，游標或滾動速度越快。"
        )
        gui.bind_slider_value_editor(
            mouse_speed_value,
            gui.stick_mouse_speed_vars[side],
            "游標速度",
            100,
            3000,
            mouse_speed_help,
            step=50,
            number_format=".0f",
            setter=snap_mouse_speed,
        )

        mouse_deadzone_label = ttk.Label(
            mouse_settings_frame,
            text=gui.tr("中心死區"),
            width=8,
            anchor="e",
        )
        mouse_deadzone_label.grid(row=1, column=0, padx=(0, 4), pady=(3, 0))

        def snap_mouse_deadzone(value):
            numeric = max(0.0, min(0.30, round(float(value) * 100.0) / 100.0))
            gui.stick_mouse_deadzone_vars[side].set(f"{numeric:.2f}")

        mouse_deadzone_scale = ttk.Scale(
            mouse_settings_frame,
            from_=0.0,
            to=0.30,
            variable=gui.stick_mouse_deadzone_vars[side],
            command=snap_mouse_deadzone,
            orient="horizontal",
            length=120,
        )
        mouse_deadzone_scale.grid(row=1, column=1, padx=4, pady=(3, 0), sticky="ew")
        mouse_deadzone_value = ttk.Label(
            mouse_settings_frame, width=5, anchor="w"
        )
        mouse_deadzone_value.grid(row=1, column=2, padx=(3, 0), pady=(3, 0))
        mouse_deadzone_help = (
            "忽略搖桿中心附近的微小輸入，避免游標自行漂移。\n\n"
            "設定範圍：0.00 ～ 0.30，步進 0.01；建議 0.03 ～ 0.08。\n"
            "數值越高，開始移動所需的搖桿行程越大。"
        )
        gui.bind_slider_value_editor(
            mouse_deadzone_value,
            gui.stick_mouse_deadzone_vars[side],
            "中心死區",
            0.0,
            0.30,
            mouse_deadzone_help,
            step=0.01,
            number_format=".2f",
            setter=snap_mouse_deadzone,
        )

        analog_settings_frame.columnconfigure(0, weight=1)
        analog_settings_frame.columnconfigure(3, weight=1)
        ttk.Label(
            analog_settings_frame,
            text=gui.tr("輸入方向"),
            width=8,
            anchor="e",
        ).grid(row=0, column=1, padx=(0, 8))
        analog_direction_display = tk.StringVar(
            value=gui.tr(gui.stick_analog_direction_vars[side].get())
        )
        analog_direction_combo = ttk.Combobox(
            analog_settings_frame,
            textvariable=analog_direction_display,
            values=tuple(gui.tr(value) for value in ("UP", "DOWN", "LEFT", "RIGHT")),
            state="readonly",
            width=12,
        )
        analog_direction_combo.grid(row=0, column=2, sticky="w")

        def select_analog_direction(_event=None):
            reverse = {
                gui.tr(value): value
                for value in ("UP", "DOWN", "LEFT", "RIGHT")
            }
            gui.stick_analog_direction_vars[side].set(
                reverse.get(
                    analog_direction_display.get(),
                    analog_direction_display.get(),
                )
            )

        analog_direction_combo.bind(
            "<<ComboboxSelected>>", select_analog_direction
        )

        def draw_direction_preview(*args):
            del args
            preview.delete("all")
            # Tk adds the highlight border outside the configured Canvas
            # size. Centre against the actual rendered widget instead of
            # the nominal 88 px content width, which otherwise makes every
            # ring look about one pixel too far left/up.
            border = float(preview.cget("highlightthickness")) + float(
                preview.cget("borderwidth")
            )
            fallback_width = float(preview.cget("width")) + border * 2.0
            fallback_height = float(preview.cget("height")) + border * 2.0
            width = max(float(preview.winfo_width()), fallback_width)
            height = max(float(preview.winfo_height()), fallback_height)
            center_x = width / 2.0
            center_y = height / 2.0
            radius = min(width, height) / 2.0 - 8.0
            mode = gui.stick_direction_mode_vars[side].get().strip()
            deadzone = max(
                0.0,
                min(
                    20.0,
                    safe_float(gui.stick_direction_deadzone_vars[side], 5.0),
                ),
            )
            boundary_step = 90.0 if mode == "4WAY" else 45.0
            first_boundary = 45.0 if mode == "4WAY" else 22.5
            boundary_count = 4 if mode == "4WAY" else 8
            shape_steps = max(
                0,
                min(10, int(round(gui.left_output_shape_var.get()
                                  if side == "LEFT"
                                  else gui.right_output_shape_var.get()))),
            )
            shape_blend = shape_steps / 10.0

            def output_edge_point(angle_degrees):
                angle = math.radians(angle_degrees)
                direction_x = math.cos(angle)
                direction_y = math.sin(angle)
                square_scale = 1.0 / max(
                    abs(direction_x), abs(direction_y), 1e-9
                )
                edge_scale = (
                    1.0 - shape_blend
                    + square_scale * shape_blend
                )
                return (
                    center_x + radius * direction_x * edge_scale,
                    center_y - radius * direction_y * edge_scale,
                )

            # 方向死區是角度區域，不會在單位圓處終止。將扇形畫到
            # 目前圓形／方形混合後的實際外緣，和執行時判定一致。
            if deadzone > 0.0:
                for index in range(boundary_count):
                    boundary = first_boundary + index * boundary_step
                    sector_points = [center_x, center_y]
                    for sample in range(13):
                        angle = (
                            boundary - deadzone
                            + deadzone * 2.0 * sample / 12.0
                        )
                        sector_points.extend(output_edge_point(angle))
                    preview.create_polygon(
                        *sector_points,
                        fill="#f3caca",
                        outline="",
                    )
            preview.create_line(
                center_x - radius, center_y,
                center_x + radius, center_y,
                fill="#b0b0b0"
            )
            preview.create_line(
                center_x, center_y - radius,
                center_x, center_y + radius,
                fill="#b0b0b0"
            )
            trigger = max(
                0.0,
                min(1.0, safe_float(gui.stick_direction_trigger_vars[side], 0.60)),
            )
            release = max(
                0.0,
                min(trigger, safe_float(gui.stick_direction_release_vars[side], 0.50)),
            )
            for value, color, dash in (
                (trigger, "#2878c8", ()),
                (release, "#666666", (3, 2)),
            ):
                ring = radius * value
                preview.create_oval(
                    center_x - ring,
                    center_y - ring,
                    center_x + ring,
                    center_y + ring,
                    outline=color,
                    width=2,
                    dash=dash,
                )
            boundary_points = []
            for sample in range(121):
                boundary_points.extend(
                    output_edge_point(360.0 * sample / 120.0)
                )
            preview.create_line(
                *boundary_points,
                fill="#333333",
                width=2,
            )
            preview.create_oval(
                center_x - 2, center_y - 2,
                center_x + 2, center_y + 2,
                fill="#333333", outline=""
            )

            for label, variable, formatter in value_labels:
                label.configure(
                    text=formatter(safe_float(variable, 0.0))
                )
            mouse_speed_value.configure(
                text=(
                    f"{safe_float(gui.stick_mouse_speed_vars[side], 900) / 50.0:.0f}/s"
                    if gui.stick_direction_mode_vars[side].get()
                    == "MOUSE_WHEEL_LINEAR"
                    else f"{safe_float(gui.stick_mouse_speed_vars[side], 900):.0f}"
                )
            )
            mouse_deadzone_value.configure(
                text=f"{safe_float(gui.stick_mouse_deadzone_vars[side], 0.05) * 100:.0f}%"
            )

        for variable in (
            gui.stick_direction_trigger_vars[side],
            gui.stick_direction_release_vars[side],
            gui.stick_direction_deadzone_vars[side],
            gui.stick_mouse_speed_vars[side],
            gui.stick_mouse_deadzone_vars[side],
            gui.left_output_shape_var
            if side == "LEFT"
            else gui.right_output_shape_var,
        ):
            variable.trace_add("write", draw_direction_preview)
        preview.bind(
            "<Configure>",
            lambda _event: draw_direction_preview(),
            add="+",
        )

        # =========================
        # 4 向 / 8 向顯示切換
        # =========================
        def update_direction_mode(
            *args
        ):
            mode = (
                gui.stick_direction_mode_vars[
                    side
                ].get()
            )
            mode_display_var.set(gui.stick_mode_label(mode))

            is_stick_mapping = (
                mode
                in (
                    "映射為右搖桿",
                    "映射為左搖桿",
                    "映射為滑鼠",
                    "MOUSE_WHEEL_LINEAR",
                    "XINPUT_LT_LINEAR",
                    "XINPUT_RT_LINEAR",
                )
            )

            # =========================
            # 對角方向顯示規則
            # =========================
            #
            # 4WAY：
            # 隱藏四個對角方向。
            #
            # 8WAY：
            # 顯示四個對角方向。
            #
            # 映射為另一邊搖桿：
            # 顯示完整 8 個方向圖示。

            for widget in (
                gui.stick_diagonal_widgets[
                    side
                ]
            ):
                if mode == "8WAY" or mode in (
                    "映射為右搖桿",
                    "映射為左搖桿",
                    "映射為滑鼠",
                ):
                    widget.grid()

                else:
                    widget.grid_remove()

            # =========================
            # 方向按鍵下拉選單狀態
            # =========================
            for combo in direction_combos:
                if is_stick_mapping:
                    # 保留原本位置與大小，
                    # 但變灰且不能操作。
                    combo.configure(
                        state="disabled"
                    )

                else:
                    # 恢復正常可選狀態。
                    combo.configure(
                        state="readonly"
                    )

            direction_settings_frame.place_forget()
            mouse_settings_frame.place_forget()
            analog_settings_frame.place_forget()
            if mode in ("4WAY", "8WAY"):
                direction_settings_frame.place(
                    x=0, rely=0.5, relwidth=1.0, anchor="w"
                )
            elif mode in ("映射為滑鼠", "MOUSE_WHEEL_LINEAR"):
                mouse_speed_label.configure(
                    text=gui.tr(
                        "滾輪速度" if mode == "MOUSE_WHEEL_LINEAR"
                        else "游標速度"
                    )
                )
                mouse_settings_frame.place(
                    x=0, rely=0.5, relwidth=1.0, anchor="w"
                )
                mouse_deadzone_label.grid()
                mouse_deadzone_scale.grid()
                mouse_deadzone_value.grid()
            elif mode in ("XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR"):
                analog_settings_frame.place(
                    x=0, rely=0.5, relwidth=1.0, anchor="w"
                )
            draw_direction_preview()

        # 保存這支搖桿的模式刷新函式
        gui.stick_direction_mode_updaters[
            side
        ] = update_direction_mode

        def select_direction_mode(event=None):
            del event
            displayed = mode_display_var.get()
            reverse_values = {
                gui.stick_mode_label(value): value for value in mode_values
            }
            gui.stick_direction_mode_vars[side].set(
                reverse_values.get(displayed, displayed)
            )
            update_direction_mode()

        mode_combo.bind(
            "<<ComboboxSelected>>",
            select_direction_mode
        )

        # 初始化顯示狀態
        update_direction_mode()

        # 疊放在區塊右上角，不參與 grid 尺寸計算。
        ttk.Button(
            parent,
            text="還原",
            width=5,
            command=(
                lambda current_side=side:
                gui.reset_stick_direction_mapping(current_side)
            )
        ).place(
            relx=1.0,
            x=-4,
            y=2,
            anchor="ne"
        )

    # 建立左搖桿方向映射
    create_stick_direction_panel(
        left_direction_frame,
        "LEFT"
    )

    # 建立右搖桿方向映射
    create_stick_direction_panel(
        right_direction_frame,
        "RIGHT"
    )

    ttk.Label(
        stick_direction_frame,
        text=(
            "紅色扇形：方向死區，延伸至目前輸出形狀外緣；"
            "藍圈：觸發門檻；虛線圈：放開門檻。\n"
            "分界內維持原方向；滑鼠模式只調整游標速度。"
        ),
        anchor="w",
        justify="left",
        wraplength=330,
    ).grid(
        row=2,
        column=0,
        pady=(5, 0),
        sticky="w",
    )
