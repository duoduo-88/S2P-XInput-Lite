import tkinter as tk
from tkinter import ttk

def build_main_layout(gui):
    """Build the scrollable root layout and return its main columns."""
    # =========================
    # 自適應低解析度視窗容器
    # =========================
    #
    # 正常解析度：
    #     內容完整顯示，不出現捲軸。
    #
    # 低解析度（例如 720p / 768p）：
    #     主內容區限制在螢幕可用高度內，
    #     超出的內容可以垂直捲動。
    #
    # 底部功能按鈕不放進捲動區，
    # 因此「儲存設定」等按鈕永遠保持可見。
    gui.root.rowconfigure(0, weight=1)
    gui.root.columnconfigure(0, weight=1)

    # Tk Canvas and the root toplevel are painted before the embedded ttk
    # frame hierarchy on taskbar restore.  Give both an explicit theme-matched
    # backing colour so Windows never exposes an uninitialised black surface.
    style = ttk.Style(gui.root)
    backing_color = style.lookup("TFrame", "background")
    if not backing_color:
        backing_color = gui.root.cget("background") or "#f0f0f0"
    gui.root.configure(background=backing_color)

    gui.scroll_host = ttk.Frame(
        gui.root
    )
    gui.scroll_host.grid(
        row=0,
        column=0,
        sticky="nsew"
    )

    gui.scroll_host.rowconfigure(
        0,
        weight=1
    )
    gui.scroll_host.columnconfigure(
        0,
        weight=1
    )

    gui.scroll_canvas = tk.Canvas(
        gui.scroll_host,
        highlightthickness=0,
        borderwidth=0,
        background=backing_color,
    )
    gui.scroll_canvas.grid(
        row=0,
        column=0,
        sticky="nsew"
    )

    gui.scrollbar = ttk.Scrollbar(
        gui.scroll_host,
        orient="vertical",
        command=gui.scroll_canvas.yview
    )

    gui.scroll_canvas.configure(
        yscrollcommand=gui.scrollbar.set
    )

    main = ttk.Frame(
        gui.scroll_canvas,
        padding=(20, 20, 20, 8)
    )
    gui.main_frame = main

    gui.main_canvas_window = (
        gui.scroll_canvas.create_window(
            (0, 0),
            window=main,
            anchor="nw"
        )
    )

    main.bind(
        "<Configure>",
        gui._on_main_frame_configure
    )

    gui.scroll_canvas.bind(
        "<Enter>",
        gui._bind_mousewheel
    )

    gui.scroll_canvas.bind(
        "<Leave>",
        gui._unbind_mousewheel
    )

    # 左右兩欄
    content_frame = ttk.Frame(main)
    gui.content_frame = content_frame
    content_frame.grid(
        row=1,
        column=0,
        columnspan=2
    )

    left_frame = ttk.Frame(
        content_frame,
        padding=(0, 0, 6, 0)
    )
    left_frame.grid(row=0, column=0, sticky="ns")
    left_frame.columnconfigure(0, weight=1)
    left_frame.rowconfigure(1, weight=1)
    gui.left_frame = left_frame

    right_frame = ttk.Frame(
        content_frame,
        padding=(6, 0, 0, 0)
    )
    right_frame.grid(row=0, column=1, sticky="n")
    gui.right_frame = right_frame

    return main, left_frame, right_frame
