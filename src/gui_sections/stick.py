import tkinter as tk
from tkinter import ttk

def build_stick_section(
    gui,
    left_frame,
    editor_class,
    format_shape_circularity_error,
    draw_output_shape_preview,
):
    """Build the left/right stick curve and deadzone controls."""
    StickCurveEditor = editor_class
    # =========================
    # 左側：搖桿
    # =========================
    stick_frame = ttk.LabelFrame(
        left_frame,
        text="搖桿設定",
        padding=(8, 4)
    )
    stick_frame.grid(
        row=0,
        column=0,
        sticky="ew",
        pady=(0, 6)
    )
    gui.stick_frame = stick_frame

    # =========================
    # 搖桿線性曲線
    # =========================
    curve_notebook = ttk.Notebook(
        stick_frame
    )
    gui.curve_notebook = curve_notebook

    curve_notebook.grid(
        row=0,
        column=0,
        columnspan=3,
        pady=(0, 6)
    )

    # 使用 place 疊放在群組右上角，不參與 grid 欄寬計算，
    # 避免按鈕擠壓或拉伸曲線設定中的其他元件。
    ttk.Style(gui.root).configure(
        "CurveZoom.TButton",
        padding=(4, 0),
    )
    gui.curve_zoom_button = ttk.Button(
        stick_frame,
        text="放大",
        width=5,
        style="CurveZoom.TButton",
        command=gui.toggle_stick_settings_zoom
    )
    gui.curve_zoom_button.place(
        relx=1.0,
        x=-8,
        y=0,
        anchor="ne"
    )
    gui.curve_zoom_button.lift()

    # 左搖桿頁籤
    left_curve_tab = ttk.Frame(
        curve_notebook,
        padding=(6, 2)
    )

    curve_notebook.add(
        left_curve_tab,
        text=" 左搖桿 "
    )

    gui.left_curve_editor = StickCurveEditor(
        left_curve_tab,
        gui.left_curve_vars,
        gui.left_deadzone_var,
        gui.left_outer_deadzone_var,
        gui.left_deadzone_compress_var,
        gui.left_outer_deadzone_compress_var,
        gui.left_interpolation_var,
        label_color="#1976D2",
        width=gui.NORMAL_CANVAS_WIDTH,
        height=gui.NORMAL_CANVAS_HEIGHT,
        zoom_command=gui.toggle_stick_settings_zoom,
        ui_scale=gui.ui_scale,
        default_curve=gui.system_default_curve("left"),
    )

    gui.left_curve_editor.pack()

    def align_group_help_column(frame, column):
        """Pin every help button to a shared right edge in this group."""
        frame.columnconfigure(column, weight=1)
        for child in frame.winfo_children():
            try:
                if child.cget("text") == "?":
                    child.grid_configure(sticky="e")
            except (tk.TclError, AttributeError):
                pass

    left_smoothing_frame = ttk.Frame(
        left_curve_tab
    )
    gui.left_smoothing_frame = left_smoothing_frame

    # Fill the same tab width used by the curve-range group below.
    left_smoothing_frame.pack(
        pady=(4, 0),
        fill="x"
    )
    left_smoothing_frame.columnconfigure(0, weight=1)

    left_output_group = ttk.LabelFrame(
        left_smoothing_frame,
        text="輸出與防抖",
        padding=(6, 2)
    )
    gui.left_output_group = left_output_group
    left_output_group.grid(row=0, column=0, sticky="ew")
    left_output_group.columnconfigure(1, weight=1)

    ttk.Label(
        left_output_group,
        text="防抖"
    ).grid(row=1, column=0, sticky="w", padx=(2, 2), pady=2)

    def snap_left_smoothing(value):
        value = round(
            float(value),
            1
        )

        gui.left_stick_smoothing_var.set(
            value
        )

    ttk.Scale(
        left_output_group,
        from_=0,
        to=3.0,
        variable=gui.left_stick_smoothing_var,
        command=snap_left_smoothing,
        orient="horizontal",
        length=163
    ).grid(row=1, column=1, padx=3, pady=2, sticky="ew")


    left_smoothing_value_label = ttk.Label(
        left_output_group,
        width=5
    )

    left_smoothing_value_label.grid(
        row=1, column=2, sticky="w", pady=2
    )

    def update_left_smoothing_label(
        *args
    ):
        value = round(
            gui.left_stick_smoothing_var.get(),
            1
        )

        left_smoothing_value_label.config(
            text=f"{value:.1f}"
        )

    gui.left_stick_smoothing_var.trace_add(
        "write",
        update_left_smoothing_label
    )

    update_left_smoothing_label()

    # =========================
    # 左搖桿死區設定
    # =========================

    left_deadzone_frame = ttk.LabelFrame(
        left_curve_tab,
        text="曲線範圍",
        padding=(6, 2)
    )
    gui.left_deadzone_frame = left_deadzone_frame

    left_deadzone_frame.pack(
        pady=(4, 0),
        fill="x"
    )

    # 中心死區
    left_center_deadzone_label_frame = (
        ttk.Frame(
            left_deadzone_frame
        )
    )

    left_center_deadzone_label_frame.grid(
        row=0,
        column=0,
        sticky="e",
        pady=2
    )

    ttk.Checkbutton(
        left_center_deadzone_label_frame,
        text="壓縮",
        width=5,
        variable=(
            gui.left_deadzone_compress_var
        )
    ).pack(
        side="left",
        padx=(0, 4)
    )

    left_center_deadzone_label = ttk.Label(
        left_center_deadzone_label_frame,
        text="中心死區",
        width=7,
        anchor="w"
    )
    left_center_deadzone_label.pack(
        side="left"
    )
    gui.bind_numeric_scrubber(
        left_center_deadzone_label,
        gui.left_deadzone_var,
        0.0,
        0.99,
        step=0.01,
        number_format=".2f",
    )

    ttk.Entry(
        left_deadzone_frame,
        textvariable=gui.left_deadzone_var,
        width=8
    ).grid(
        row=0,
        column=1,
        padx=(15, 0),
        pady=2
    )

    gui.create_help(
        left_deadzone_frame,
        (
            "用來消除左搖桿放開後的輕微飄移。\n\n"
            "設定範圍：0.00 ～ 0.99\n"
            "中心死區與外圍死區總和必須小於 1.00。\n"
            "0.00 = 無死區\n"
            "0.03 = 在 3% 範圍內忽略輸入\n\n"
            "數值越大，搖桿中心附近越不敏感。\n\n"
            "勾選「壓縮」後，曲線的 0% 起點"
            "會移到中心死區邊界，"
            "並將完整曲線重新分布到剩餘行程。\n\n"
            "曲線圖左側的灰色區域代表"
            "中心死區範圍。"
        )
    ).grid(
        row=0,
        column=2,
        padx=(8, 0)
    )

    # 外圍死區
    left_outer_deadzone_label_frame = (
        ttk.Frame(
            left_deadzone_frame
        )
    )

    left_outer_deadzone_label_frame.grid(
        row=1,
        column=0,
        sticky="e",
        pady=2
    )

    ttk.Checkbutton(
        left_outer_deadzone_label_frame,
        text="壓縮",
        width=5,
        variable=(
            gui.left_outer_deadzone_compress_var
        )
    ).pack(
        side="left",
        padx=(0, 4)
    )

    left_outer_deadzone_label = ttk.Label(
        left_outer_deadzone_label_frame,
        text="外圍死區",
        width=7,
        anchor="w"
    )
    left_outer_deadzone_label.pack(
        side="left"
    )
    gui.bind_numeric_scrubber(
        left_outer_deadzone_label,
        gui.left_outer_deadzone_var,
        0.0,
        0.99,
        step=0.01,
        number_format=".2f",
    )

    ttk.Entry(
        left_deadzone_frame,
        textvariable=(
            gui.left_outer_deadzone_var
        ),
        width=8
    ).grid(
        row=1,
        column=1,
        padx=(15, 0),
        pady=2
    )

    gui.create_help(
        left_deadzone_frame,
        (
            "設定左搖桿接近外圈時，"
            "提前輸出最大值。\n\n"
            "設定範圍：0.00 ～ 0.99\n"
            "中心死區與外圍死區總和必須小於 1.00。\n"
            "0.03 = 到達 97% 時輸出 100%\n\n"
            "勾選「壓縮」後，曲線的 100% 終點"
            "會移到外圍死區邊界，"
            "並將完整曲線重新分布到剩餘行程。\n\n"
            "曲線圖右側的灰色區域代表"
            "外圍死區範圍。"
        )
    ).grid(
        row=1,
        column=2,
        padx=(8, 0)
    )

    ttk.Label(
        left_deadzone_frame, text="平滑模式"
    ).grid(row=2, column=0, sticky="w", padx=(2, 2), pady=2)
    left_interpolation_frame = ttk.Frame(left_deadzone_frame)
    left_interpolation_frame.grid(
        row=2, column=1, pady=2
    )
    ttk.Radiobutton(
        left_interpolation_frame,
        text="線性",
        variable=gui.left_interpolation_var,
        value="LINEAR",
    ).pack(side="left")
    ttk.Radiobutton(
        left_interpolation_frame,
        text="平滑",
        variable=gui.left_interpolation_var,
        value="SMOOTH",
    ).pack(side="left", padx=(8, 0))
    gui.create_help(
        left_deadzone_frame,
        "線性：控制點之間使用直線。\n\n"
        "平滑：使用單調三次插值，平滑通過所有控制點，"
        "並避免反向與超調。"
    ).grid(row=2, column=2, padx=(8, 0))

    ttk.Label(
        left_output_group,
        text="形狀"
    ).grid(row=0, column=0, sticky="w", padx=(2, 2), pady=2)

    def snap_left_shape(value):
        gui.left_output_shape_var.set(
            max(0, min(10, int(round(float(value)))))
        )

    ttk.Scale(
        left_output_group,
        from_=0,
        to=10,
        variable=gui.left_output_shape_var,
        command=snap_left_shape,
        orient="horizontal",
        length=163,
    ).grid(row=0, column=1, padx=3, pady=2, sticky="ew")
    gui.left_output_shape_value_label = ttk.Label(
        left_output_group,
        width=5,
        anchor="w",
    )
    gui.left_output_shape_value_label.grid(
        row=0, column=2, sticky="w", pady=2
    )

    def update_left_shape_value(*args):
        del args
        gui.left_output_shape_value_label.configure(
            text=format_shape_circularity_error(
                gui.left_output_shape_var.get()
            )
        )

    gui.left_output_shape_var.trace_add(
        "write", update_left_shape_value
    )
    update_left_shape_value()
    left_shape_help = gui.create_help(
        left_output_group,
        "輸出形狀會在圓形與方形之間分成 10 段。\n\n"
        "灰色圓：圓形基準；虛線方框：方形極限；"
        "藍線：目前設定的最大輸出範圍。\n\n"
        "對角方向每軸約為：\n"
        "0 = 0.707　2 = 0.766　5 = 0.854\n"
        "8 = 0.941　10 = 1.000\n\n"
        "數值越高，對角方向可輸出的範圍越大。\n\n"
        "拉桿後方百分比是預估圓周誤差；"
        "實際結果會受校正與取樣影響。",
        illustration=lambda canvas: draw_output_shape_preview(
            canvas, gui.left_output_shape_var.get()
        ),
    )
    left_shape_help.grid(
        row=0, column=3, padx=(2, 0), pady=2, sticky="e"
    )
    gui.bind_slider_value_editor(
        gui.left_output_shape_value_label,
        gui.left_output_shape_var,
        "形狀",
        0,
        10,
        left_shape_help.parameter_help_text,
        step=1,
        number_format=".0f",
    )

    left_smoothing_help = gui.create_help(
        left_output_group,
        (
            "防抖\n\n"
            "根據位移曲線目前區段的放大倍率，"
            "自動增加防抖強度。\n\n"
            "設定範圍：0.0 ～ 3.0\n\n"
            "0.0：關閉防抖補償。\n"
            "1.0：標準補償，曲線放大幾倍，"
            "就按相同倍率增加防抖。\n"
            "2.0：加強補償。\n"
            "3.0：更強的補償。\n\n"
            "僅在曲線斜率大於 1:1 的區域生效。\n"
            "平滑使用實際時間計算，不會因 BLE 或 ESP32 "
            "更新頻率不同而改變手感。\n"
            "數值越高，輸出越穩定，"
            "但也可能產生較明顯的平滑感。"
        )
    )
    left_smoothing_help.grid(
        row=1, column=3, padx=(2, 0), pady=2, sticky="e"
    )
    gui.bind_slider_value_editor(
        left_smoothing_value_label,
        gui.left_stick_smoothing_var,
        "防抖",
        0.0,
        3.0,
        left_smoothing_help.parameter_help_text,
        step=0.1,
        number_format=".1f",
    )

    align_group_help_column(left_deadzone_frame, 2)

    # 右搖桿頁籤
    right_curve_tab = ttk.Frame(
        curve_notebook,
        padding=(6, 2)
    )

    curve_notebook.add(
        right_curve_tab,
        text=" 右搖桿 "
    )

    gui.right_curve_editor = StickCurveEditor(
        right_curve_tab,
        gui.right_curve_vars,
        gui.right_deadzone_var,
        gui.right_outer_deadzone_var,
        gui.right_deadzone_compress_var,
        gui.right_outer_deadzone_compress_var,
        gui.right_interpolation_var,
        label_color="#D32F2F",
        width=gui.NORMAL_CANVAS_WIDTH,
        height=gui.NORMAL_CANVAS_HEIGHT,
        zoom_command=gui.toggle_stick_settings_zoom,
        ui_scale=gui.ui_scale,
        default_curve=gui.system_default_curve("right"),
    )

    gui.right_curve_editor.pack()

    right_smoothing_frame = ttk.Frame(
        right_curve_tab
    )
    gui.right_smoothing_frame = right_smoothing_frame

    right_smoothing_frame.pack(
        pady=(4, 0),
        fill="x"
    )
    right_smoothing_frame.columnconfigure(0, weight=1)

    right_output_group = ttk.LabelFrame(
        right_smoothing_frame,
        text="輸出與防抖",
        padding=(6, 2)
    )
    gui.right_output_group = right_output_group
    right_output_group.grid(row=0, column=0, sticky="ew")
    right_output_group.columnconfigure(1, weight=1)

    ttk.Label(
        right_output_group,
        text="防抖"
    ).grid(row=1, column=0, sticky="w", padx=(2, 2), pady=2)

    def snap_right_smoothing(value):
        value = round(
            float(value),
            1
        )

        gui.right_stick_smoothing_var.set(
            value
        )

    ttk.Scale(
        right_output_group,
        from_=0,
        to=3.0,
        variable=gui.right_stick_smoothing_var,
        command=snap_right_smoothing,
        orient="horizontal",
        length=163
    ).grid(row=1, column=1, padx=3, pady=2, sticky="ew")


    right_smoothing_value_label = ttk.Label(
        right_output_group,
        width=5
    )

    right_smoothing_value_label.grid(
        row=1, column=2, sticky="w", pady=2
    )

    def update_right_smoothing_label(
        *args
    ):
        value = round(
            gui.right_stick_smoothing_var.get(),
            1
        )

        right_smoothing_value_label.config(
            text=f"{value:.1f}"
        )

    gui.right_stick_smoothing_var.trace_add(
        "write",
        update_right_smoothing_label
    )

    update_right_smoothing_label()

    right_smoothing_help = gui.create_help(
        right_output_group,
        (
            "防抖\n\n"
            "根據位移曲線目前區段的放大倍率，"
            "自動增加防抖強度。\n\n"
            "設定範圍：0.0 ～ 3.0\n\n"
            "0.0：關閉防抖補償。\n"
            "1.0：標準補償，曲線放大幾倍，"
            "就按相同倍率增加防抖。\n"
            "2.0：加強補償。\n"
            "3.0：更強的補償。\n\n"
            "僅在曲線斜率大於 1:1 的區域生效。\n"
            "平滑使用實際時間計算，不會因 BLE 或 ESP32 "
            "更新頻率不同而改變手感。\n"
            "數值越高，輸出越穩定，"
            "但也可能產生較明顯的平滑感。"
        )
    )
    right_smoothing_help.grid(
        row=1, column=3, padx=(2, 0), pady=2, sticky="e"
    )
    gui.bind_slider_value_editor(
        right_smoothing_value_label,
        gui.right_stick_smoothing_var,
        "防抖",
        0.0,
        3.0,
        right_smoothing_help.parameter_help_text,
        step=0.1,
        number_format=".1f",
    )

    # =========================
    # 右搖桿死區設定
    # =========================

    right_deadzone_frame = ttk.LabelFrame(
        right_curve_tab,
        text="曲線範圍",
        padding=(6, 2)
    )
    gui.right_deadzone_frame = right_deadzone_frame

    right_deadzone_frame.pack(
        pady=(4, 0),
        fill="x"
    )

    # 中心死區
    right_center_deadzone_label_frame = (
        ttk.Frame(
            right_deadzone_frame
        )
    )

    right_center_deadzone_label_frame.grid(
        row=0,
        column=0,
        sticky="e",
        pady=2
    )

    ttk.Checkbutton(
        right_center_deadzone_label_frame,
        text="壓縮",
        width=5,
        variable=(
            gui.right_deadzone_compress_var
        )
    ).pack(
        side="left",
        padx=(0, 4)
    )

    right_center_deadzone_label = ttk.Label(
        right_center_deadzone_label_frame,
        text="中心死區",
        width=7,
        anchor="w"
    )
    right_center_deadzone_label.pack(
        side="left"
    )
    gui.bind_numeric_scrubber(
        right_center_deadzone_label,
        gui.right_deadzone_var,
        0.0,
        0.99,
        step=0.01,
        number_format=".2f",
    )

    ttk.Entry(
        right_deadzone_frame,
        textvariable=gui.right_deadzone_var,
        width=8
    ).grid(
        row=0,
        column=1,
        padx=(15, 0),
        pady=2
    )

    gui.create_help(
        right_deadzone_frame,
        (
            "用來消除右搖桿放開後的輕微飄移。\n\n"
            "設定範圍：0.00 ～ 0.99\n"
            "中心死區與外圍死區總和必須小於 1.00。\n"
            "0.00 = 無死區\n"
            "0.03 = 在 3% 範圍內忽略輸入\n\n"
            "數值越大，搖桿中心附近越不敏感。\n\n"
            "勾選「壓縮」後，曲線的 0% 起點"
            "會移到中心死區邊界，"
            "並將完整曲線重新分布到剩餘行程。\n\n"
            "曲線圖左側的灰色區域代表"
            "中心死區範圍。"
        )
    ).grid(
        row=0,
        column=2,
        padx=(8, 0)
    )

    # 外圍死區
    right_outer_deadzone_label_frame = (
        ttk.Frame(
            right_deadzone_frame
        )
    )

    right_outer_deadzone_label_frame.grid(
        row=1,
        column=0,
        sticky="e",
        pady=2
    )

    ttk.Checkbutton(
        right_outer_deadzone_label_frame,
        text="壓縮",
        width=5,
        variable=(
            gui.right_outer_deadzone_compress_var
        )
    ).pack(
        side="left",
        padx=(0, 4)
    )

    right_outer_deadzone_label = ttk.Label(
        right_outer_deadzone_label_frame,
        text="外圍死區",
        width=7,
        anchor="w"
    )
    right_outer_deadzone_label.pack(
        side="left"
    )
    gui.bind_numeric_scrubber(
        right_outer_deadzone_label,
        gui.right_outer_deadzone_var,
        0.0,
        0.99,
        step=0.01,
        number_format=".2f",
    )

    ttk.Entry(
        right_deadzone_frame,
        textvariable=(
            gui.right_outer_deadzone_var
        ),
        width=8
    ).grid(
        row=1,
        column=1,
        padx=(15, 0),
        pady=2
    )

    gui.create_help(
        right_deadzone_frame,
        (
            "設定右搖桿接近外圈時，"
            "提前輸出最大值。\n\n"
            "設定範圍：0.00 ～ 0.99\n"
            "中心死區與外圍死區總和必須小於 1.00。\n"
            "0.03 = 到達 97% 時輸出 100%\n\n"
            "勾選「壓縮」後，曲線的 100% 終點"
            "會移到外圍死區邊界，"
            "並將完整曲線重新分布到剩餘行程。\n\n"
            "曲線圖右側的灰色區域代表"
            "外圍死區範圍。"
        )
    ).grid(
        row=1,
        column=2,
        padx=(8, 0)
    )

    ttk.Label(
        right_deadzone_frame, text="平滑模式"
    ).grid(row=2, column=0, sticky="w", padx=(2, 2), pady=2)
    right_interpolation_frame = ttk.Frame(right_deadzone_frame)
    right_interpolation_frame.grid(
        row=2, column=1, pady=2
    )
    ttk.Radiobutton(
        right_interpolation_frame,
        text="線性",
        variable=gui.right_interpolation_var,
        value="LINEAR",
    ).pack(side="left")
    ttk.Radiobutton(
        right_interpolation_frame,
        text="平滑",
        variable=gui.right_interpolation_var,
        value="SMOOTH",
    ).pack(side="left", padx=(8, 0))
    gui.create_help(
        right_deadzone_frame,
        "線性：控制點之間使用直線。\n\n"
        "平滑：使用單調三次插值，平滑通過所有控制點，"
        "並避免反向與超調。"
    ).grid(row=2, column=2, padx=(8, 0))

    ttk.Label(
        right_output_group,
        text="形狀"
    ).grid(row=0, column=0, sticky="w", padx=(2, 2), pady=2)

    def snap_right_shape(value):
        gui.right_output_shape_var.set(
            max(0, min(10, int(round(float(value)))))
        )

    ttk.Scale(
        right_output_group,
        from_=0,
        to=10,
        variable=gui.right_output_shape_var,
        command=snap_right_shape,
        orient="horizontal",
        length=163,
    ).grid(row=0, column=1, padx=3, pady=2, sticky="ew")
    gui.right_output_shape_value_label = ttk.Label(
        right_output_group,
        width=5,
        anchor="w",
    )
    gui.right_output_shape_value_label.grid(
        row=0, column=2, sticky="w", pady=2
    )

    def update_right_shape_value(*args):
        del args
        gui.right_output_shape_value_label.configure(
            text=format_shape_circularity_error(
                gui.right_output_shape_var.get()
            )
        )

    gui.right_output_shape_var.trace_add(
        "write", update_right_shape_value
    )
    update_right_shape_value()
    right_shape_help = gui.create_help(
        right_output_group,
        "輸出形狀會在圓形與方形之間分成 10 段。\n\n"
        "灰色圓：圓形基準；虛線方框：方形極限；"
        "藍線：目前設定的最大輸出範圍。\n\n"
        "對角方向每軸約為：\n"
        "0 = 0.707　2 = 0.766　5 = 0.854\n"
        "8 = 0.941　10 = 1.000\n\n"
        "數值越高，對角方向可輸出的範圍越大。\n\n"
        "拉桿後方百分比是預估圓周誤差；"
        "實際結果會受校正與取樣影響。",
        illustration=lambda canvas: draw_output_shape_preview(
            canvas, gui.right_output_shape_var.get()
        ),
    )
    right_shape_help.grid(
        row=0, column=3, padx=(2, 0), pady=2, sticky="e"
    )
    gui.bind_slider_value_editor(
        gui.right_output_shape_value_label,
        gui.right_output_shape_var,
        "形狀",
        0,
        10,
        right_shape_help.parameter_help_text,
        step=1,
        number_format=".0f",
    )

    align_group_help_column(right_deadzone_frame, 2)

    # 初始畫面將「輸出與防抖」群組放在「曲線範圍」下方。
    # 重新 pack 會把整個控制列移到頁籤的最後。
    gui.set_zoom_controls_centered(False)

    # 兩個曲線編輯器共用同一組變數。曲線點被任一視窗修改後，
    # 以 after_idle 合併同一事件中的 X/Y 更新，再重畫大小兩個視窗。
    for side, curve_vars in (
        ("LEFT", gui.left_curve_vars),
        ("RIGHT", gui.right_curve_vars),
    ):
        for point_vars in curve_vars:
            for axis_var in point_vars.values():
                axis_var.trace_add(
                    "write",
                    lambda *_args, current_side=side: (
                        gui._schedule_stick_curve_redraw(current_side)
                    ),
                )

    # =========================
    # 左右搖桿死區即時重畫
    # =========================

    def redraw_left_deadzone_preview(
        *args
    ):
        gui._schedule_stick_curve_redraw("LEFT")

    gui.left_deadzone_var.trace_add(
        "write",
        redraw_left_deadzone_preview
    )

    gui.left_outer_deadzone_var.trace_add(
        "write",
        redraw_left_deadzone_preview
    )

    gui.left_deadzone_compress_var.trace_add(
        "write",
        redraw_left_deadzone_preview
    )

    gui.left_outer_deadzone_compress_var.trace_add(
        "write",
        redraw_left_deadzone_preview
    )
    gui.left_interpolation_var.trace_add(
        "write",
        redraw_left_deadzone_preview
    )

    def redraw_right_deadzone_preview(
        *args
    ):
        gui._schedule_stick_curve_redraw("RIGHT")

    gui.right_deadzone_var.trace_add(
        "write",
        redraw_right_deadzone_preview
    )

    gui.right_outer_deadzone_var.trace_add(
        "write",
        redraw_right_deadzone_preview
    )

    gui.right_deadzone_compress_var.trace_add(
        "write",
        redraw_right_deadzone_preview
    )

    gui.right_outer_deadzone_compress_var.trace_add(
        "write",
        redraw_right_deadzone_preview
    )
    gui.right_interpolation_var.trace_add(
        "write",
        redraw_right_deadzone_preview
    )
