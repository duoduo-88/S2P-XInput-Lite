import tkinter as tk
from tkinter import ttk

def build_mapping_layers_section(gui, mapping_notebook):
    """Build the secondary mapping-layer tab."""
    # =========================
    # 頁籤 3：次級映射層
    # =========================
    mapping_layers_frame = ttk.Frame(mapping_notebook, padding=(8, 6))
    mapping_notebook.add(mapping_layers_frame, text=" 映射層 ")
    mapping_layers_frame.rowconfigure(0, weight=1)
    mapping_layers_frame.columnconfigure(0, weight=1)

    layer_canvas = tk.Canvas(
        mapping_layers_frame,
        width=300,
        height=438,
        highlightthickness=0,
        borderwidth=0,
    )
    layer_scrollbar = ttk.Scrollbar(
        mapping_layers_frame, orient="vertical", command=layer_canvas.yview
    )
    gui.mapping_layers_list_frame = ttk.Frame(layer_canvas)
    layer_window = layer_canvas.create_window(
        (0, 0), window=gui.mapping_layers_list_frame, anchor="nw"
    )
    layer_canvas.configure(yscrollcommand=layer_scrollbar.set)
    layer_canvas.grid(row=0, column=0, sticky="nsew")

    def refresh_layer_scrollregion(_event=None):
        bounds = layer_canvas.bbox("all")
        layer_canvas.configure(scrollregion=bounds or (0, 0, 0, 0))
        content_height = 0 if bounds is None else bounds[3] - bounds[1]
        if content_height > layer_canvas.winfo_height() + 1:
            layer_scrollbar.grid(row=0, column=1, sticky="ns")
        else:
            layer_scrollbar.grid_remove()
            layer_canvas.yview_moveto(0.0)

    def on_layer_mousewheel(event):
        bounds = layer_canvas.bbox("all")
        content_height = 0 if bounds is None else bounds[3] - bounds[1]
        if content_height <= layer_canvas.winfo_height() + 1:
            layer_canvas.yview_moveto(0.0)
            return "break"
        units = int(-event.delta / 120) if event.delta else 0
        if units:
            layer_canvas.yview_scroll(units, "units")
        return "break"

    gui.mapping_layers_canvas = layer_canvas
    gui._mapping_layers_mousewheel_handler = on_layer_mousewheel
    gui.mapping_layers_list_frame.bind(
        "<Configure>",
        refresh_layer_scrollregion,
    )
    layer_canvas.bind(
        "<Configure>",
        lambda event: (
            layer_canvas.itemconfigure(layer_window, width=event.width),
            layer_canvas.after_idle(refresh_layer_scrollregion),
        ),
    )
    layer_canvas.bind("<MouseWheel>", on_layer_mousewheel)
    gui.mapping_layers_list_frame.bind(
        "<MouseWheel>", on_layer_mousewheel
    )
    layer_action_frame = ttk.Frame(mapping_layers_frame)
    layer_action_frame.grid(
        row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 3)
    )
    for column in range(3):
        layer_action_frame.columnconfigure(
            column, weight=1, uniform="layer_footer_action"
        )
    ttk.Button(
        layer_action_frame,
        text="＋ 新增",
        command=gui.add_mapping_layer,
    ).grid(row=0, column=0, sticky="ew", padx=(0, 2))
    ttk.Button(
        layer_action_frame,
        text="匯入",
        command=gui.import_mapping_layer_files,
    ).grid(row=0, column=1, sticky="ew", padx=2)
    ttk.Button(
        layer_action_frame,
        text="管理映射層",
        command=gui.open_mapping_layer_folder,
    ).grid(row=0, column=2, sticky="ew", padx=(2, 0))
    gui.mapping_layers_help = gui.create_help(
        mapping_layers_frame,
        "同一時間只會套用一個映射層。\n\n"
        "複合鍵數量較多者優先；按鍵數相同時，清單較上方者優先。\n"
        "「按住」需持續按住全部啟用鍵，並會暫時覆蓋「切換」層；放開後返回原切換層。\n"
        "「切換」會在啟用組合每次按下時開啟或關閉；未勾選或未設定啟用鍵的層不會觸發。",
    )
    gui.mapping_layers_help.grid(
        row=1, column=1, pady=(6, 0)
    )
    gui.refresh_mapping_layer_rows()

