from tkinter import ttk

def build_mapping_buttons_section(gui, right_frame):
    """Build the mapping notebook and primary button-mapping tab."""
    # =========================
    # 右側：映射設定頁籤
    # =========================
    mapping_notebook = ttk.Notebook(
        right_frame
    )

    mapping_notebook.grid(
        row=0,
        column=0,
        sticky="n"
    )

    # =========================
    # 頁籤 1：按鍵映射
    # =========================
    mapping_frame = ttk.Frame(
        mapping_notebook,
        padding=(10, 4)
    )

    mapping_notebook.add(
        mapping_frame,
        text=" 按鍵映射 "
    )

    mapping_frame.columnconfigure(
        1,
        weight=1
    )

    ttk.Label(mapping_frame, text="Switch 2 Pro").grid(
        row=0, column=0, sticky="e", padx=(0, 8), pady=(0, 4)
    )
    ttk.Label(mapping_frame, text="XInput / Xbox").grid(
        row=0, column=1, sticky="w", pady=(0, 4)
    )

    row = 1
    for switch_name, variable in gui.button_vars.items():
        gui._track_custom_mapping_variable(
            variable
        )
        ttk.Label(
            mapping_frame,
            text=f"{switch_name}  →",
            width=10,
            anchor="e",
        ).grid(
            row=row,
            column=0,
            sticky="e",
            padx=(0, 8),
            pady=4,
        )

        combo = ttk.Combobox(
            mapping_frame,
            textvariable=variable,
            values=gui.button_options,
            state="readonly",
            width=24
        )

        def on_mapping_selected(
            event,
            current_variable=variable
        ):
            if (
                current_variable.get()
                == "CUSTOM_KEYBOARD"
            ):
                gui.open_keyboard_capture(
                    current_variable,
                    mouse_mode="buttons",
                )

        combo.bind(
            "<<ComboboxSelected>>",
            on_mapping_selected
        )
        
        combo.grid(
            row=row,
            column=1,
            sticky="ew",
            pady=4
        )

        row += 1

    for mapping_row in range(1, row):
        mapping_frame.rowconfigure(
            mapping_row, weight=1, uniform="button_mapping_row"
        )

    gui.create_help(
        mapping_frame,
        "選擇 CUSTOM_KEYBOARD 後，按下要映射的輸入。\n\n"
        "支援鍵盤單鍵、Ctrl／Shift／Alt／Win 複合鍵，"
        "以及滑鼠左鍵、右鍵與中鍵。\n"
        "例如：F12、Ctrl + S、Win + D。"
    ).place(
        relx=1.0,
        x=-55,
        y=3,
        anchor="ne",
    )

    ttk.Button(
        mapping_frame,
        text="還原",
        width=5,
        command=gui.reset_button_mapping
    ).place(
        relx=1.0,
        x=-2,
        y=0,
        anchor="ne"
    )

    return mapping_notebook
