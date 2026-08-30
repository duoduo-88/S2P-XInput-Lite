import importlib.util
import tkinter as tk
from tkinter import ttk

def build_status_and_footer(gui, right_frame):
    """Build status rows and the fixed action footer."""
    # =========================
    # 狀態列
    # =========================
    status_frame = ttk.Frame(
        right_frame
    )

    status_frame.grid(
        row=1,
        column=0,
        sticky="w",
        pady=(3, 0)
    )

    driver_status_row = ttk.Frame(status_frame)
    driver_status_row.pack(anchor="w")

    # ViGEmBus 狀態
    gui.driver_status_label = tk.Label(
        driver_status_row,
        text=""
    )

    gui.driver_status_label.pack(
        side="left",
        anchor="w",
        padx=(0, 8)
    )

    wasapi_available = importlib.util.find_spec("pyaudiowpatch") is not None
    dependency_text = (
        "● WASAPI：可用"
        if wasapi_available
        else "● WASAPI：缺少"
    )
    gui.audio_haptics_status_label = tk.Label(
        driver_status_row,
        text=dependency_text,
        fg="#178A38" if wasapi_available else "#C62828",
    )
    gui.audio_haptics_status_label.pack(side="left", anchor="w")

    # HidHide 狀態放在驅動狀態列最右側。
    gui.hidhide_status_label = tk.Label(
        driver_status_row,
        text="",
    )
    gui.hidhide_status_label.pack(
        side="left",
        anchor="w",
        padx=(8, 0),
    )
    gui.hidhide_status_label.bind(
        "<Button-1>",
        lambda _event: gui.handle_hidhide_status_click(),
    )

    gui.update_driver_status()
    gui.update_hidhide_status()

    gui.controller_status_label = tk.Label(
        status_frame,
        text="● 手把：未啟動",
        fg="#777777",
    )
    gui.controller_status_label.pack(
        anchor="w",
        padx=(0, 8),
        pady=(1, 0),
    )
    gui.update_controller_status()

    # 兩排共用同一個 7 欄網格，因此左右邊界與按鈕寬度一致。
    # 整個 footer 固定在捲動區外，720p 時仍可隨時切換與儲存。
    footer_frame = ttk.Frame(
        gui.root,
        padding=(20, 0, 20, 12),
    )
    gui.profile_frame = footer_frame
    gui.action_frame = footer_frame
    footer_frame.grid(row=1, column=0, sticky="ew")
    for column in range(7):
        footer_frame.columnconfigure(
            column,
            weight=1,
            uniform="footer_column",
        )

    # Use the same seven equal columns as the action row below. The profile
    # selector uses two columns and each action uses one, so Save through
    # Gamepad Test match the Reconnect button width exactly.
    profile_row = ttk.Frame(footer_frame)
    profile_row.grid(
        row=0, column=0, columnspan=7, sticky="ew", pady=(0, 5)
    )
    for column in range(7):
        profile_row.columnconfigure(
            column, weight=1, uniform="profile_column"
        )

    profile_selector = ttk.Frame(profile_row)
    profile_selector.grid(
        row=0, column=0, columnspan=2, sticky="ew", padx=3
    )
    ttk.Label(profile_selector, text="目前方案").pack(side="left")
    gui.profile_combo = ttk.Combobox(
        profile_selector,
        textvariable=gui.profile_name_var,
        values=gui.profile_names,
        state="readonly",
        width=1,
        postcommand=gui.sync_profile_directory,
    )
    gui.profile_combo.pack(
        side="left", fill="x", expand=True, padx=(6, 0), ipady=3
    )
    gui.profile_combo.bind("<Button-3>", gui.show_profile_context_menu)
    gui.profile_combo.bind("<Shift-F10>", gui.show_profile_context_menu)
    gui.profile_combo.bind(
        "<<ComboboxSelected>>",
        gui.on_profile_selected,
    )
    gui.bind_profile_popdown_context_menu()

    ttk.Button(
        profile_row,
        text="儲存/套用",
        command=gui.save_current_profile,
    ).grid(row=0, column=2, sticky="nsew", padx=3, ipady=5)
    ttk.Button(
        profile_row,
        text="另存新方案",
        command=gui.save_profile_as,
    ).grid(row=0, column=3, sticky="nsew", padx=3, ipady=5)
    ttk.Button(
        profile_row,
        text="匯入方案",
        command=gui.import_profile_file,
    ).grid(row=0, column=4, sticky="nsew", padx=3, ipady=5)
    ttk.Button(
        profile_row,
        text="管理方案",
        command=gui.open_profile_folder,
    ).grid(row=0, column=5, sticky="nsew", padx=3, ipady=5)
    gui.gamepad_test_button = ttk.Button(
        profile_row,
        text=gui.tr("手把測試"),
        command=gui.open_gamepad_test_window,
    )
    gui.gamepad_test_button.grid(
        row=0, column=6, sticky="nsew", padx=3, ipady=5
    )

    language_pin_frame = ttk.Frame(footer_frame)
    language_pin_frame.grid(
        row=1, column=0, sticky="nsew", padx=3
    )
    language_pin_frame.columnconfigure(0, weight=1, uniform="language_pin")
    language_pin_frame.columnconfigure(1, weight=1, uniform="language_pin")
    gui.language_button = ttk.Button(
        language_pin_frame,
        text="En",
        command=gui.toggle_language,
    )
    gui.language_button.grid(
        row=0, column=0, sticky="nsew", padx=(0, 2), ipady=5
    )
    gui.pin_button = ttk.Button(
        language_pin_frame,
        text="Pin",
        command=gui.pin_controller,
    )
    gui.pin_button.grid(
        row=0, column=1, sticky="nsew", padx=(2, 0), ipady=5
    )

    ttk.Button(
        footer_frame,
        text="還原預設",
        command=gui.reset_to_defaults
    ).grid(row=1, column=1, sticky="nsew", padx=3, ipady=5)

    ttk.Button(
        footer_frame,
        text="校正搖桿",
        command=gui.run_calibration
    ).grid(row=1, column=2, sticky="nsew", padx=3, ipady=5)

    ttk.Button(
        footer_frame,
        text="刷入相容韌體",
        command=gui.flash_firmware
    ).grid(row=1, column=3, sticky="nsew", padx=3, ipady=5)

    gui.write_esp32_button = ttk.Button(
        footer_frame,
        text=f"{gui.tr('寫入 ESP32')} ▼",
    )
    gui.write_esp32_menu = tk.Menu(
        gui.write_esp32_button,
        tearoff=False,
    )
    def refresh_write_esp32_menu():
        menu = gui.write_esp32_menu
        menu.delete(0, "end")
        menu.add_command(
            label=gui.tr("寫入並啟用 PC XInput 獨立模式"),
            command=lambda: gui.write_current_profile_to_esp32(
                "standalone"
            ),
        )
        menu.add_command(
            label=gui.tr("寫入並啟用手機 USB HID 模式"),
            command=lambda: gui.write_current_profile_to_esp32(
                "standalone_hid"
            ),
        )
        menu.add_command(
            label=gui.tr("寫入並啟用自動辨識獨立模式（實驗性）"),
            command=lambda: gui.write_current_profile_to_esp32(
                "standalone_auto"
            ),
        )
        menu.add_command(
            label=gui.tr("僅寫入設定"),
            command=lambda: gui.write_current_profile_to_esp32(None),
        )
        menu.add_separator()
        menu.add_command(
            label=gui.tr("切回 ESP32 橋接模式"),
            command=gui.set_esp32_bridge_mode,
        )

    gui.refresh_write_esp32_menu = refresh_write_esp32_menu
    refresh_write_esp32_menu()

    def show_write_esp32_menu():
        refresh_write_esp32_menu()
        button = gui.write_esp32_button
        try:
            gui.write_esp32_menu.tk_popup(
                button.winfo_rootx(),
                button.winfo_rooty() + button.winfo_height(),
            )
        finally:
            gui.write_esp32_menu.grab_release()

    gui.write_esp32_button.configure(command=show_write_esp32_menu)
    gui.write_esp32_button.grid(
        row=1, column=4, sticky="nsew", padx=3, ipady=5
    )

    ttk.Button(
        footer_frame,
        text="重啟連線",
        command=gui.restart_main
    ).grid(
        row=1, column=5, sticky="nsew", padx=3, ipady=5
    )

    gui.idle_disconnect_button = ttk.Button(
        footer_frame,
        text="閒置自動斷線",
        command=gui.show_idle_disconnect_menu,
    )
    gui.idle_disconnect_button.grid(
        row=1, column=6, sticky="nsew", padx=3, ipady=5
    )

    # 所有元件建立完成後，
    # 再依照實際螢幕可用高度調整視窗。
    gui.request_adaptive_window_update()
