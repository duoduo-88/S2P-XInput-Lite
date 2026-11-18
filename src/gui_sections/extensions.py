"""Extensions (擴充工具) settings window: a list of independent tools.

The window only edits the global [extensions] sections and sends connector
commands.  The connector owns the pipe server and reports per-tool state in
controller_status.json, which this window reads twice per second while open.
"""

import json
import subprocess
import threading
import time
import webbrowser
from dataclasses import replace
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from command_queue import enqueue_controller_command
from config_utils import CONFIG_PATH, atomic_write_config, config_file_lock, load_config
from extension_bridge import (
    MAX_TOOLS,
    STATE_CONNECTED,
    STATE_DISABLED,
    STATE_DISCONNECTED,
    STATE_ERROR,
    STATE_WAITING,
    ExtensionSettings,
    ToolSettings,
    load_extension_settings,
    new_tool_id,
    normalized_path,
    settings_problems,
    store_extension_settings,
)
from extension_updates import (
    CHECK_FAILED,
    NO_INFO,
    UP_TO_DATE,
    UPDATE_AVAILABLE,
    check_tool_updates,
)
from runtime_status import STATUS_PATH
from version import VERSION


STATUS_MAX_AGE_SECONDS = 3.0

REASON_TEXT = {
    "tool_closed": "工具已關閉連線",
    "user_disconnected": "已手動中斷",
    "unauthorized": "未授權的程式，請確認清單中的工具",
    "unsupported_protocol": "工具的通訊協定版本不相容",
    "handshake_timeout": "工具未在時限內完成連線",
    "bad_message": "工具送出無效資料",
    "message_too_large": "工具送出的資料過大",
    "pipe_in_use": "通訊管道已被其他程式占用",
    "pipe_failed": "通訊管道發生錯誤",
    "tool_not_found": "找不到工具 EXE",
    "launch_failed": "無法啟動工具",
    "permission_denied": "工具要求的權限未開啟",
    "input_timeout": "輸入逾時，已清除工具輸入",
    "conflict": "搖桿控制衝突",
    "settings_changed": "設定已變更，等待工具重新連線",
}

STATE_COLORS = {
    STATE_DISABLED: "#777777",
    STATE_WAITING: "#D97A00",
    STATE_CONNECTED: "#138A36",
    STATE_DISCONNECTED: "#777777",
    STATE_ERROR: "#C62828",
}
OK_COLOR = "#138A36"
UPDATE_COLOR = "#1565C0"
UPDATE_TEXT = {
    UPDATE_AVAILABLE: "有更新：v{latest}",
    UP_TO_DATE: "已是最新版本",
    NO_INFO: "未提供更新資訊",
    CHECK_FAILED: "更新檢查失敗",
}
STICK_TEXT = {"left": "左搖桿", "right": "右搖桿"}


def read_connector_status(path=None, now=None):
    """Return the connector's status dict, or None when it is not running."""
    path = STATUS_PATH if path is None else path
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        age = (time.time() if now is None else now) - float(data.get("updated_at", 0.0))
    except (OSError, ValueError, TypeError):
        return None
    if age > STATUS_MAX_AGE_SECONDS or data.get("state") == "stopped":
        return None
    return data


def _runtime(connector_status):
    status = (connector_status or {}).get("extensions")
    return status if isinstance(status, dict) else {}


def tool_name(tool, connector_status=None):
    entry = (_runtime(connector_status).get("tools") or {}).get(tool.tool_id) or {}
    return str(entry.get("name") or "").strip() or Path(tool.path).stem


def tool_status_text(tr, settings, tool, connector_status):
    """Map one list entry plus connector status to (text, state)."""
    if not settings.enabled or not tool.enabled:
        return tr("未啟用"), STATE_DISABLED
    if tool not in settings.active_tools:
        return tr("未載入"), STATE_ERROR
    if connector_status is None:
        return tr("等待連線（連接程式未啟動）"), STATE_WAITING
    runtime = _runtime(connector_status)
    if runtime.get("state") == STATE_ERROR:
        reason = REASON_TEXT.get(str(runtime.get("code") or ""), "")
        text = tr("連線失敗：{reason}").format(reason=tr(reason)) if reason else tr("連線失敗")
        return text, STATE_ERROR
    entry = (runtime.get("tools") or {}).get(tool.tool_id) or {}
    state = entry.get("state", STATE_WAITING)
    code = str(entry.get("code") or "")
    reason = REASON_TEXT.get(code, "")
    if state == STATE_CONNECTED:
        text = tr("已連接")
        if reason:
            text += f" · {tr(reason)}"
        return text, STATE_CONNECTED
    if state == STATE_DISCONNECTED:
        if reason:
            return tr("已中斷（{reason}）").format(reason=tr(reason)), STATE_DISCONNECTED
        return tr("已中斷"), STATE_DISCONNECTED
    if state == STATE_ERROR:
        if reason:
            return tr("連線失敗：{reason}").format(reason=tr(reason)), STATE_ERROR
        return tr("連線失敗"), STATE_ERROR
    if reason:
        return f"{tr('等待連線')} · {tr(reason)}", STATE_WAITING
    return tr("等待連線"), STATE_WAITING


def update_status_text(tr, result):
    """Short update note for the status column ("" when not checked)."""
    if result is None:
        return ""
    if result == "checking":
        return tr("檢查更新中…")
    return tr(UPDATE_TEXT.get(result.state, UPDATE_TEXT[CHECK_FAILED])).format(latest=result.latest)


def update_summary(tr, settings, results):
    """Message after a check, and the pages to offer for tools with updates."""
    lines, pages = [], []
    counts = {NO_INFO: 0, CHECK_FAILED: 0}
    for tool in settings.tools:
        result = results.get(tool.tool_id)
        if result is None:
            continue
        if result.state == UPDATE_AVAILABLE:
            lines.append(tr("{name}：v{current} → v{latest}").format(
                name=Path(tool.path).stem, current=result.current, latest=result.latest))
            if result.url and result.url not in pages:
                pages.append(result.url)
        elif result.state in counts:
            counts[result.state] += 1
    notes = []
    if counts[NO_INFO]:
        notes.append(tr("{count} 個工具未提供更新資訊。").format(count=counts[NO_INFO]))
    if counts[CHECK_FAILED]:
        notes.append(tr("{count} 個工具無法檢查更新。").format(count=counts[CHECK_FAILED]))
    if lines:
        text = tr("以下工具有新版本：") + "\n\n" + "\n".join(lines)
    elif any(result.state == UP_TO_DATE for result in results.values()):
        text = tr("所有可檢查的工具都已是最新版本。")
    else:
        text = ""
    text = "\n\n".join(part for part in (text, "\n".join(notes)) if part)
    return text or tr("無法檢查更新。"), pages


def conflict_messages(tr, settings, connector_status):
    """Configuration problems plus live stick conflicts, as display lines."""
    lines = []
    by_id = {tool.tool_id: tool for tool in settings.tools}
    for code, tool_id in settings_problems(settings):
        name = tool_name(by_id[tool_id], connector_status)
        if code == "duplicate_tool":
            lines.append(tr("「{name}」重複加入清單").format(name=name))
        elif code == "too_many_tools":
            lines.append(
                tr("最多同時啟用 {count} 個工具，「{name}」不會載入").format(
                    count=MAX_TOOLS, name=name,
                )
            )
        elif code == "no_permission":
            lines.append(tr("「{name}」沒有任何權限，無法連線").format(name=name))
    if settings.enabled and connector_status is not None:
        tools = _runtime(connector_status).get("tools") or {}
        for tool in settings.active_tools:
            entry = tools.get(tool.tool_id) or {}
            if entry.get("state") == STATE_ERROR and entry.get("code") == "conflict":
                lines.append(
                    tr("「{name}」與「{other}」同時要求控制{stick}").format(
                        name=tool_name(tool, connector_status),
                        other=entry.get("with_tool") or "?",
                        stick=tr(STICK_TEXT.get(entry.get("stick"), "搖桿")),
                    )
                )
    return lines


def save_extension_settings(settings, config_path=None):
    """Persist the global Extensions settings without touching profiles."""
    config_path = CONFIG_PATH if config_path is None else config_path
    with config_file_lock():
        latest = load_config(config_path)
        store_extension_settings(latest, settings)
        atomic_write_config(latest, config_path)


# Built-in tools ship with S2P and open through the GUI's own launcher; they do
# not use the Extensions pipe, so they work even when Extensions is disabled.
BUILTIN_GAMEPAD_TESTER = "builtin.gamepad_tester"
BUILTIN_TOOLS = ((BUILTIN_GAMEPAD_TESTER, "手把測試", "open_gamepad_test_window"),)

NOTES_TITLE = "擴充工具注意事項："
NOTES = (
    "擴充工具是另外安裝的獨立程式，只能依你允許的權限提交輸入或讀取控制器狀態。",
    "請只安裝信任來源的工具。擴充工具由其開發者提供，使用後的操作與結果由使用者自行負責。",
    "部分遊戲或線上服務可能禁止外部輸入或自動化操作，使用前請先確認相關規範。",
    "S2P 仍是唯一的虛擬手把輸出；同一支搖桿只能由一個工具控制，實體搖桿優先。",
    "工具關閉、逾時或當機時會立即清除其輸入；停用擴充工具即恢復原本操作。",
)


def _load_settings():
    try:
        return load_extension_settings(load_config(CONFIG_PATH, persist=False))
    except Exception:
        return ExtensionSettings()


def tray_items(gui, settings=None):
    """``(key, label)`` pairs for the tray menu: built-ins, then enabled tools."""
    settings = _load_settings() if settings is None else settings
    items = [(key, gui.tr(label)) for key, label, _method in BUILTIN_TOOLS]
    if settings.enabled:
        items.extend(
            (tool.tool_id, tool_name(tool, read_connector_status()))
            for tool in settings.active_tools
        )
    return items


def open_extension_tool(gui, tool_id, settings=None):
    """Open a built-in or listed tool; tools connect to S2P by themselves."""
    for key, _label, method in BUILTIN_TOOLS:
        if tool_id == key:
            getattr(gui, method)()
            return True
    settings = _load_settings() if settings is None else settings
    tool = settings.tool(tool_id)
    if tool is None:
        return False
    if read_connector_status() is not None:
        # The connector starts it with a one-time token (needed by .py tools).
        try:
            enqueue_controller_command(f"extensions_connect:{tool.tool_id}")
            return True
        except OSError:
            pass
    path = Path(tool.path)
    if path.suffix.lower() != ".exe" or not path.is_file():
        messagebox.showwarning(
            gui.tr("擴充工具"),
            gui.tr("連接程式尚未啟動。請先在主視窗按「重啟連線」。"),
            parent=gui.root,
        )
        return False
    try:
        subprocess.Popen([str(path)], cwd=str(path.parent), close_fds=True)
    except OSError as exc:
        messagebox.showerror(gui.tr("擴充工具"), str(exc), parent=gui.root)
        return False
    return True


def open_extensions_window(gui):
    existing = getattr(gui, "_extensions_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                gui._bring_child_to_front(existing)
                return existing
        except tk.TclError:
            pass

    tr = gui.tr
    if not hasattr(gui, "_extension_update_results"):
        gui._extension_update_results = {}  # kept while S2P runs
    model = {
        "settings": _load_settings(),
        "updates": gui._extension_update_results,
        "checking": set(),   # tool ids being checked
        "finished": [],      # results handed over by the check thread
    }

    window = tk.Toplevel(gui.root)
    window.withdraw()
    window.transient(gui.root)
    window.title(tr("擴充工具"))
    window.resizable(False, False)
    gui._extensions_window = window

    def text_widget(factory, parent, source, **options):
        widget = factory(parent, text=tr(source), **options)
        # apply_language() retranslates from this stable Chinese key.
        widget._s2p_zh_text = source
        return widget

    frame = ttk.Frame(window, padding=12)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)

    enabled_var = tk.BooleanVar(value=model["settings"].enabled)
    window._s2p_enabled_checkbox = text_widget(
        ttk.Checkbutton, frame, "啟用擴充工具", variable=enabled_var,
        command=lambda: update(enabled=bool(enabled_var.get())),
    )
    window._s2p_enabled_checkbox.grid(row=0, column=0, sticky="w")

    # -- tool list -------------------------------------------------------------
    body_font = tkfont.nametofont("TkDefaultFont")
    columns = (
        ("enabled", "啟用", ("✓",), "center"),
        ("name", "工具", ("S2P-Joystick-Macro",), "w"),
        ("permissions", "權限", (tr("輸入") + " / " + tr("讀取"), tr("內建")), "w"),
        ("status", "狀態", (
            tr("等待連線（連接程式未啟動）"),
            tr("已連接") + " · " + tr(UPDATE_TEXT[UPDATE_AVAILABLE]).format(latest="0.10.10"),
        ), "w"),
    )
    listing = ttk.Frame(frame)
    listing.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
    listing.columnconfigure(0, weight=1)  # the list spans the full row width
    tree = ttk.Treeview(
        listing, columns=tuple(key for key, *_ in columns), show="headings",
        height=MAX_TOOLS + len(BUILTIN_TOOLS) + 1, selectmode="browse",
    )
    for key, heading, samples, anchor in columns:
        label = tr(heading)
        width = max(map(body_font.measure, (label, *samples))) + 18
        tree.heading(key, text=label, anchor=anchor)
        tree.column(key, width=width, anchor=anchor, stretch=key in ("name", "status"))
    for state, color in STATE_COLORS.items():
        tree.tag_configure(state, foreground=color)
    tree.tag_configure(UPDATE_AVAILABLE, foreground=UPDATE_COLOR)
    tree.tag_configure("builtin", foreground="#333333")
    tree.grid(row=0, column=0, sticky="nsew")
    window._s2p_tree = tree

    list_buttons = ttk.Frame(frame)
    list_buttons.grid(row=2, column=0, sticky="ew", pady=(6, 0))
    add_button = text_widget(ttk.Button, list_buttons, "新增工具...", command=lambda: add_tool())
    add_button.pack(side="left")
    remove_button = text_widget(ttk.Button, list_buttons, "移除", command=lambda: remove_tool())
    remove_button.pack(side="left", padx=(6, 0))
    toggle_button = text_widget(
        ttk.Button, list_buttons, "啟用／停用", command=lambda: toggle_tool(),
    )
    toggle_button.pack(side="left", padx=(6, 0))
    open_button = text_widget(ttk.Button, list_buttons, "開啟", command=lambda: open_selected())
    open_button.pack(side="right")
    update_button = text_widget(
        ttk.Button, list_buttons, "檢查更新", command=lambda: check_updates(),
    )
    update_button.pack(side="right", padx=(0, 6))
    window._s2p_buttons = {
        "add": add_button, "remove": remove_button, "toggle": toggle_button,
        "open": open_button, "updates": update_button,
    }

    # -- permissions of the selected tool ----------------------------------------
    options = ttk.Frame(frame)
    options.grid(row=3, column=0, sticky="w", pady=(8, 0))
    text_widget(ttk.Label, options, "權限").pack(side="left", padx=(0, 8))
    allow_input_var = tk.BooleanVar(value=False)
    allow_read_var = tk.BooleanVar(value=False)
    option_boxes = []
    for source, variable, key in (
        ("允許提交輸入", allow_input_var, "allow_input"),
        ("允許讀取控制器狀態", allow_read_var, "allow_read"),
    ):
        box = text_widget(
            ttk.Checkbutton, options, source, variable=variable,
            command=lambda key=key, variable=variable: update_selected(**{key: bool(variable.get())}),
        )
        box.pack(side="left", padx=(0, 12))
        option_boxes.append(box)
    window._s2p_options = dict(zip(("allow_input", "allow_read"), option_boxes))

    # -- conflict check ------------------------------------------------------
    check_row = ttk.Frame(frame)
    check_row.grid(row=4, column=0, sticky="w", pady=(10, 0))
    text_widget(ttk.Label, check_row, "衝突檢查").pack(side="left", anchor="n", padx=(0, 8))
    conflict_label = tk.Label(check_row, text="", anchor="w", justify="left")
    conflict_label.pack(side="left")
    window._s2p_conflict_label = conflict_label

    # -- notes -----------------------------------------------------------------
    notes = ttk.Frame(frame)
    notes.grid(row=5, column=0, sticky="w", pady=(10, 0))
    # Wrap at the longest Chinese line so Chinese keeps one line per note and
    # the longer English text wraps to the same width.
    wrap = max(body_font.measure(f"{index}. {note}") for index, note in enumerate(NOTES, 1)) + 4
    text_widget(ttk.Label, notes, NOTES_TITLE, foreground="#555555").grid(
        row=0, column=0, columnspan=2, sticky="w")
    for index, note in enumerate(NOTES, start=1):
        ttk.Label(notes, text=f"{index}.", foreground="#555555").grid(
            row=index, column=0, sticky="nw", padx=(0, 4))
        text_widget(
            ttk.Label, notes, note, foreground="#555555", justify="left",
            wraplength=wrap - body_font.measure("0. "),
        ).grid(row=index, column=1, sticky="w")

    controls = ttk.Frame(frame)
    controls.grid(row=6, column=0, sticky="ew", pady=(12, 0))
    text_widget(ttk.Button, controls, "關閉", command=window.destroy).pack(side="right")

    # -- behaviour -------------------------------------------------------------
    def selected_id():
        selection = tree.selection()
        return selection[0] if selection else None

    def selected_tool():
        tool_id = selected_id()
        return model["settings"].tool(tool_id) if tool_id else None

    def persist(settings):
        try:
            save_extension_settings(settings)
        except OSError as exc:
            messagebox.showerror(
                tr("儲存失敗"),
                tr("無法安全寫入 config.ini。\n\n錯誤資訊：{error}").format(error=exc),
                parent=window,
            )
            return False
        model["settings"] = settings
        if read_connector_status() is not None:
            # Changed tools are dropped and reconnect with the new permissions.
            try:
                enqueue_controller_command("extensions_apply")
            except OSError:
                pass
        refresh_tray = getattr(gui, "refresh_extension_tray_items", None)
        if refresh_tray is not None:
            refresh_tray()
        refresh(schedule=False)
        return True

    def update(**changes):
        current = model["settings"]
        persist(ExtensionSettings(
            enabled=changes.get("enabled", current.enabled), tools=current.tools,
        ))

    def replace_tool(new_tool):
        current = model["settings"]
        tools = tuple(new_tool if tool.tool_id == new_tool.tool_id else tool for tool in current.tools)
        return persist(ExtensionSettings(enabled=current.enabled, tools=tools))

    def enabled_count(excluding=None):
        return sum(
            1 for tool in model["settings"].tools
            if tool.enabled and tool.tool_id != excluding
        )

    def warn_limit():
        messagebox.showwarning(
            tr("擴充工具"),
            tr("最多可同時啟用 {count} 個工具。請先停用其他工具。").format(count=MAX_TOOLS),
            parent=window,
        )

    def add_tool():
        selected = filedialog.askopenfilename(
            parent=window,
            title=tr("選擇擴充工具"),
            filetypes=(
                (tr("執行檔"), "*.exe"),
                (tr("Python 腳本（開發用）"), "*.py"),
                (tr("所有檔案"), "*.*"),
            ),
        )
        if not selected:
            return
        path = str(Path(selected))
        current = model["settings"]
        for tool in current.tools:
            if normalized_path(tool.path) == normalized_path(path):
                messagebox.showwarning(
                    tr("擴充工具"),
                    tr("「{name}」已在清單中。").format(name=Path(path).stem),
                    parent=window,
                )
                return
        tool = ToolSettings(
            tool_id=new_tool_id(), path=path, enabled=enabled_count() < MAX_TOOLS,
        )
        if persist(ExtensionSettings(enabled=current.enabled, tools=current.tools + (tool,))):
            tree.selection_set(tool.tool_id)
            if not tool.enabled:
                warn_limit()

    def remove_tool():
        tool = selected_tool()
        if tool is None:
            return
        current = model["settings"]
        persist(ExtensionSettings(
            enabled=current.enabled,
            tools=tuple(item for item in current.tools if item.tool_id != tool.tool_id),
        ))

    def toggle_tool():
        tool = selected_tool()
        if tool is None:
            return
        if not tool.enabled and enabled_count(excluding=tool.tool_id) >= MAX_TOOLS:
            warn_limit()
            return
        replace_tool(replace(tool, enabled=not tool.enabled))

    def update_selected(**changes):
        tool = selected_tool()
        if tool is None:
            return
        updated = replace(tool, **changes)
        if not (updated.allow_input or updated.allow_read):
            messagebox.showwarning(
                tr("擴充工具"),
                tr("工具至少需要一項權限才能連線。"),
                parent=window,
            )
            refresh_options()
            return
        replace_tool(updated)

    def can_open(tool_id):
        if tool_id is None:
            return False
        if any(tool_id == key for key, *_ in BUILTIN_TOOLS):
            return True
        tool = model["settings"].tool(tool_id)
        return bool(model["settings"].enabled and tool is not None and tool in model["settings"].active_tools)

    def open_selected():
        tool_id = selected_id()
        if can_open(tool_id):
            open_extension_tool(gui, tool_id, model["settings"])

    def check_updates():
        if model["checking"]:
            return
        tools = {tool.tool_id: tool.path for tool in model["settings"].tools}
        if not tools:
            messagebox.showinfo(
                tr("檢查更新"), tr("清單中沒有擴充工具。"), parent=window,
            )
            return
        model["checking"] = set(tools)
        update_button.state(["disabled"])

        def run():
            try:
                results = check_tool_updates(tools, user_agent=f"S2P-XInput-Lite/{VERSION}")
            except Exception:  # never leave the button disabled
                results = {}
            # Kept even if the window was closed meanwhile.
            gui._extension_update_results.update(results)
            model["finished"].append(results)

        threading.Thread(target=run, name="S2P-ExtensionUpdates", daemon=True).start()

    def finish_update_check():
        if not model["finished"]:
            return
        results = model["finished"].pop()
        model["checking"] = set()
        update_button.state(["!disabled"])
        refresh(schedule=False)
        text, pages = update_summary(tr, model["settings"], results)
        if pages:
            if messagebox.askyesno(
                tr("檢查更新"), text + "\n\n" + tr("要開啟下載頁面嗎？"), parent=window,
            ):
                for page in pages:
                    try:
                        webbrowser.open(page, new=2)
                    except (OSError, webbrowser.Error):
                        pass
        else:
            messagebox.showinfo(tr("檢查更新"), text, parent=window)

    def refresh_options():
        """Reflect the selected tool in the permission boxes and buttons."""
        tool = selected_tool()
        for box, value, variable in (
            (option_boxes[0], tool.allow_input if tool else False, allow_input_var),
            (option_boxes[1], tool.allow_read if tool else False, allow_read_var),
        ):
            variable.set(value)
            box.state(["!disabled"] if tool else ["disabled"])
        for key in ("remove", "toggle"):
            window._s2p_buttons[key].state(["!disabled"] if tool else ["disabled"])
        window._s2p_buttons["open"].state(["!disabled"] if can_open(selected_id()) else ["disabled"])

    def refresh(schedule=True):
        try:
            if not window.winfo_exists():
                return
        except tk.TclError:
            return
        settings = model["settings"]
        connector = read_connector_status()
        rows = []
        for key, label, _method in BUILTIN_TOOLS:
            rows.append((key, ("✓", tr(label), tr("內建"), tr("內建工具，隨 S2P 提供")), STATE_CONNECTED))
        for tool in settings.tools:
            text, state = tool_status_text(tr, settings, tool, connector)
            result = model["updates"].get(tool.tool_id)
            if result is not None and result.path != tool.path:
                result = None  # the tool was moved or replaced since the check
            if tool.tool_id in model["checking"]:
                result = "checking"
            note = update_status_text(tr, result)
            if note:
                text = f"{text} · {note}"
            if result not in (None, "checking") and result.state == UPDATE_AVAILABLE:
                state = UPDATE_AVAILABLE
            permissions = " / ".join(
                tr(label) for label, allowed in (("輸入", tool.allow_input), ("讀取", tool.allow_read))
                if allowed
            ) or "—"
            values = ("✓" if tool.enabled else "—", tool_name(tool, connector), permissions, text)
            rows.append((tool.tool_id, values, state))
        wanted = [row[0] for row in rows]
        for item in tree.get_children():
            if item not in wanted:
                tree.delete(item)
        # Rows are updated in place so the selection is never disturbed.
        for index, (iid, values, state) in enumerate(rows):
            tags = ("builtin",) if iid.startswith("builtin.") else (state,)
            if tree.exists(iid):
                tree.item(iid, values=values, tags=tags)
                if tree.index(iid) != index:
                    tree.move(iid, "", index)
            else:
                tree.insert("", index, iid=iid, values=values, tags=tags)
        refresh_options()
        lines = conflict_messages(tr, settings, connector)
        if lines:
            conflict_label.configure(text="\n".join(lines), fg=STATE_COLORS[STATE_ERROR])
        else:
            conflict_label.configure(text=tr("未發現衝突"), fg=OK_COLOR)
        if model["finished"]:
            window.after_idle(finish_update_check)
        if schedule:
            window.after(500, refresh)

    tree.bind("<<TreeviewSelect>>", lambda _event: refresh_options())
    tree.bind("<Double-1>", lambda _event: open_selected())

    def on_destroy(event):
        if event.widget is window:
            gui._extensions_window = None

    window.bind("<Destroy>", on_destroy)
    window._s2p_model = model
    refresh()
    gui._center_child_window(window)
    gui._bring_child_to_front(window)
    return window
