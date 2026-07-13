import configparser
import ctypes
import subprocess
import signal
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import serial.tools.list_ports
import winreg
from version import APP_TITLE

# 修正 Windows 高 DPI 縮放
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

CONFIG_PATH = Path(__file__).with_name(
    "config.ini"
)

COMMAND_PATH = Path(__file__).with_name(
    "controller_command.txt"
)

PYTHON_EXE = Path(sys.executable)

if PYTHON_EXE.name.lower() == "pythonw.exe":
    PYTHON_EXE = PYTHON_EXE.with_name(
        "python.exe"
    )

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ESPTOOL_PATH = (
    PROJECT_ROOT
    / "esp32s3"
    / "tools"
    / "esptool.exe"
)

FIRMWARE_DIR = (
    PROJECT_ROOT
    / "esp32s3"
    / "firmware"
)

BOOTLOADER_PATH = (
    FIRMWARE_DIR
    / "bootloader.bin"
)

PARTITION_PATH = (
    FIRMWARE_DIR
    / "partition-table.bin"
)

APP_FIRMWARE_PATH = (
    FIRMWARE_DIR
    / "esp32s3_bluedroid_bridge.bin"
)

class ToolTip:
    """滑鼠移到元件上時顯示說明。"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None

        self.widget.bind("<Enter>", self.show)
        self.widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.window is not None:
            return

        self.window = tk.Toplevel(
            self.widget
        )

        self.window.wm_overrideredirect(
            True
        )

        label = tk.Label(
            self.window,
            text=self.text,
            justify="left",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=6,
            wraplength=320,
        )

        label.pack()

        # 先計算泡泡視窗的實際大小
        self.window.update_idletasks()

        tooltip_width = (
            self.window.winfo_reqwidth()
        )

        tooltip_height = (
            self.window.winfo_reqheight()
        )

        # 預設顯示在問號右下方
        x = (
            self.widget.winfo_rootx()
            + 25
        )

        y = (
            self.widget.winfo_rooty()
            + 25
        )

        # 取得螢幕大小
        screen_width = (
            self.widget.winfo_screenwidth()
        )

        screen_height = (
            self.widget.winfo_screenheight()
        )

        # 右側超出螢幕時，
        # 改到問號左側
        if (
            x + tooltip_width
            > screen_width
        ):
            x = (
                self.widget.winfo_rootx()
                - tooltip_width
                - 5
            )

        # 下方超出螢幕時，
        # 改到問號上方
        if (
            y + tooltip_height
            > screen_height
        ):
            y = (
                self.widget.winfo_rooty()
                - tooltip_height
                - 5
            )

        # 最後保證不會超出螢幕左側或上側
        x = max(
            0,
            x
        )

        y = max(
            0,
            y
        )

        self.window.wm_geometry(
            f"+{x}+{y}"
        )

    def hide(self, event=None):
        if self.window is not None:
            self.window.destroy()
            self.window = None

def is_vigembus_installed():
    """檢查 Windows 是否已安裝 ViGEmBus。"""

    registry_paths = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\ViGEmBus"
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Nefarius Software Solutions e.U.\ViGEm Bus Driver"
        ),
    ]

    for root, path in registry_paths:
        try:
            with winreg.OpenKey(
                root,
                path,
                0,
                winreg.KEY_READ
            ):
                return True

        except OSError:
            continue

    return False


class StickCurveEditor(ttk.Frame):
    """5 點分段線性搖桿曲線編輯器。"""

    def __init__(
        self,
        parent,
        curve_vars,
        deadzone_var,
        outer_deadzone_var,
        label_color="#EA0000",
        width=280,
        height=220,
        zoom_command=None
    ):
    
        self.deadzone_var = deadzone_var
        self.outer_deadzone_var = outer_deadzone_var    
        super().__init__(parent)

        self.curve_vars = curve_vars
        self.label_color = label_color
        self.canvas_width = width
        self.canvas_height = height
        self.zoom_command = zoom_command
        self.zoomed = False

        # 圖表四周留白，避免點貼在邊界
        self.margin_left = 48
        self.margin_right = 20
        self.margin_top = 10
        self.margin_bottom = 25

        self.point_radius = 6
        self.dragging_index = None

        self.canvas = tk.Canvas(
            self,
            width=width,
            height=height,
            highlightthickness=1,
            highlightbackground="gray"
        )

        self.canvas.pack()

        self.canvas.bind(
            "<Button-1>",
            self.on_mouse_down
        )

        self.canvas.bind(
            "<Double-Button-1>",
            self.on_mouse_double_click
        )

        self.canvas.bind(
            "<B1-Motion>",
            self.on_mouse_drag
        )

        self.canvas.bind(
            "<ButtonRelease-1>",
            self.on_mouse_up
        )

        self.draw()


    def toggle_zoom(self):
        """切換整個搖桿設定區域的放大 / 還原模式。"""

        if self.zoom_command is not None:
            self.zoom_command()


    def set_zoomed(self, zoomed):
        """同步曲線編輯器的放大狀態。"""

        self.zoomed = zoomed


    def set_canvas_size(self, width, height):
        """調整曲線 Canvas 尺寸並重新繪製。"""

        self.canvas_width = width
        self.canvas_height = height

        self.canvas.configure(
            width=width,
            height=height
        )

        self.draw()


    def value_to_canvas(self, x_value, y_value):
        """將 0.0～1.0 的曲線數值轉換成 Canvas 座標。"""

        graph_width = (
            self.canvas_width
            - self.margin_left
            - self.margin_right
        )

        graph_height = (
            self.canvas_height
            - self.margin_top
            - self.margin_bottom
        )

        x = (
            self.margin_left
            + x_value * graph_width
        )

        y = (
            self.margin_top
            + (1.0 - y_value) * graph_height
        )

        return x, y


    def canvas_to_value(self, y):
        """將滑鼠 Y 座標轉換成 0.0～1.0 的輸出值。"""

        graph_height = (
            self.canvas_height
            - self.margin_top
            - self.margin_bottom
        )

        value = 1.0 - (
            (y - self.margin_top)
            / graph_height
        )

        return max(
            0.0,
            min(1.0, value)
        )

    def canvas_to_x_value(self, x):
        """將滑鼠 X 座標轉換成 0.0～1.0 的輸入值。"""

        graph_width = (
            self.canvas_width
            - self.margin_left
            - self.margin_right
        )

        value = (
            (x - self.margin_left)
            / graph_width
        )

        return max(
            0.0,
            min(1.0, value)
        )


    def draw(self):
        """重新繪製格線、曲線與控制點。"""

        self.canvas.delete("all")

        x_values = [
            0.00,
            0.25,
            0.50,
            0.75,
            1.00
        ]

        # =========================
        # 顯示中心死區與外圍死區
        # =========================

        try:
            deadzone = max(
                0.0,
                min(
                    1.0,
                    float(
                        self.deadzone_var.get()
                    )
                )
            )

            outer_deadzone = max(
                0.0,
                min(
                    1.0,
                    float(
                        self.outer_deadzone_var.get()
                    )
                )
            )

        except (ValueError, TypeError):
            deadzone = 0.0
            outer_deadzone = 0.0

        graph_left = self.margin_left
        graph_right = (
            self.canvas_width
            - self.margin_right
        )
        graph_top = self.margin_top
        graph_bottom = (
            self.canvas_height
            - self.margin_bottom
        )

        # 中心死區結束位置
        deadzone_x, _ = self.value_to_canvas(
            deadzone,
            0.0
        )

        # 外圍死區開始位置
        outer_start = max(
            0.0,
            1.0 - outer_deadzone
        )

        outer_x, _ = self.value_to_canvas(
            outer_start,
            0.0
        )

        # 左側：中心死區
        if deadzone > 0.0:
            self.canvas.create_rectangle(
                graph_left,
                graph_top,
                deadzone_x,
                graph_bottom,
                fill="#d9d9d9",
                outline=""
            )

        # 右側：外圍死區
        if outer_deadzone > 0.0:
            self.canvas.create_rectangle(
                outer_x,
                graph_top,
                graph_right,
                graph_bottom,
                fill="#d9d9d9",
                outline=""
            )

        # 畫 25% 間隔的格線
        for value in x_values:
            x, _ = self.value_to_canvas(
                value,
                0.0
            )

            _, y = self.value_to_canvas(
                0.0,
                value
            )

            self.canvas.create_line(
                x,
                self.margin_top,
                x,
                self.canvas_height
                - self.margin_bottom,
                fill="#d0d0d0"
            )

            self.canvas.create_line(
                self.margin_left,
                y,
                self.canvas_width
                - self.margin_right,
                y,
                fill="#d0d0d0"
            )


        # X 軸標示
        for index, value in enumerate(x_values):
            x, _ = self.value_to_canvas(
                value,
                0.0
            )

            self.canvas.create_text(
                x,
                self.canvas_height - 15,
                text=f"{int(value * 100)}%"
            )

        # Y 軸標示
        for value in x_values:
            _, y = self.value_to_canvas(
                0.0,
                value
            )

            self.canvas.create_text(
                self.margin_left - 12,
                y,
                text=f"{int(value * 100)}%",
                anchor="e"
            )

        # 取得目前 5 個控制點
        points = []

        for index in range(5):
            x_value = self.curve_vars[
                index
            ]["x"].get()

            y_value = self.curve_vars[
                index
            ]["y"].get()

            x, y = self.value_to_canvas(
                x_value,
                y_value
            )

            points.append(
                (
                    x,
                    y,
                    x_value,
                    y_value
                )
            )

        # =========================
        # 繪製實際完整曲線
        # =========================
        #
        # 固定起點 (0, 0)
        #     ↓
        # 5 個可調整控制點
        #     ↓
        # 固定終點 (1, 1)
        #
        # 固定起點與終點只用來畫線，
        # 不會變成可拖曳控制點。

        start_x, start_y = self.value_to_canvas(
            0.0,
            0.0
        )

        end_x, end_y = self.value_to_canvas(
            1.0,
            1.0
        )

        # 固定起點 → 第一個控制點
        first_x, first_y, _, _ = points[0]

        self.canvas.create_line(
            start_x,
            start_y,
            first_x,
            first_y,
            width=2
        )

        # 連接 5 個可調整控制點
        for index in range(4):
            x1, y1, _, _ = points[index]
            x2, y2, _, _ = points[
                index + 1
            ]

            self.canvas.create_line(
                x1,
                y1,
                x2,
                y2,
                width=2
            )

        # 第五個控制點 → 固定終點
        last_x, last_y, _, _ = points[4]

        self.canvas.create_line(
            last_x,
            last_y,
            end_x,
            end_y,
            width=2
        )

        # 畫 5 個控制點與目前 XY 座標
        for index, (
            x,
            y,
            x_value,
            y_value
        ) in enumerate(points):
            radius = self.point_radius

            self.canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill="white",
                outline="black",
                width=2,
                tags=(
                    "curve_point",
                    f"point_{index}"
                )
            )

            # 在控制點上方顯示目前座標
            label_y = y - 16

            # 避免最上方的文字超出 Canvas
            if label_y < 10:
                label_y = y + 18

            self.canvas.create_text(
                x,
                label_y,
                text=(
                    f"({x_value:.2f}, "
                    f"{y_value:.2f})"
                ),
                fill=self.label_color,
                font=("", 8, "bold")
            )


    def find_nearest_point(self, mouse_x, mouse_y):
        """尋找滑鼠附近的控制點。"""

        nearest_index = None
        nearest_distance = 15

        for index in range(5):
            x_value = self.curve_vars[
                index
            ]["x"].get()

            y_value = self.curve_vars[
                index
            ]["y"].get()

            x, y = self.value_to_canvas(
                x_value,
                y_value
            )

            distance = (
                (mouse_x - x) ** 2
                + (mouse_y - y) ** 2
            ) ** 0.5

            if distance < nearest_distance:
                nearest_distance = distance
                nearest_index = index

        return nearest_index


    def on_mouse_down(self, event):
        self.dragging_index = (
            self.find_nearest_point(
                event.x,
                event.y
            )
        )

    def on_mouse_double_click(
        self,
        event
    ):
        """雙擊單一控制點，恢復該點的預設位置。"""

        index = self.find_nearest_point(
            event.x,
            event.y
        )

        if index is None:
            return

        default_value = (
            index * 0.25
        )

        self.curve_vars[
            index
        ][
            "x"
        ].set(
            default_value
        )

        self.curve_vars[
            index
        ][
            "y"
        ].set(
            default_value
        )

        self.dragging_index = None

        self.draw()


    def on_mouse_drag(self, event):
        if self.dragging_index is None:
            return

        index = self.dragging_index

        # 將滑鼠位置轉換成 0.0 ～ 1.0
        x_value = self.canvas_to_x_value(
            event.x
        )

        y_value = self.canvas_to_value(
            event.y
        )

        # 控制點之間保留最小距離，
        # 避免互相穿過或重疊
        minimum_gap = 0.01

        # 根據控制點位置決定可移動的 X 範圍
        if index == 0:
            min_x = 0.0
            max_x = (
                self.curve_vars[1]["x"].get()
                - minimum_gap
            )

        elif index == 4:
            min_x = (
                self.curve_vars[3]["x"].get()
                + minimum_gap
            )
            max_x = 1.0

        else:
            min_x = (
                self.curve_vars[
                    index - 1
                ]["x"].get()
                + minimum_gap
            )

            max_x = (
                self.curve_vars[
                    index + 1
                ]["x"].get()
                - minimum_gap
            )

        x_value = max(
            min_x,
            min(
                max_x,
                x_value
            )
        )

        # 更新目前控制點的 X、Y
        self.curve_vars[
            index
        ]["x"].set(
            round(x_value, 3)
        )

        self.curve_vars[
            index
        ]["y"].set(
            round(y_value, 3)
        )

        self.draw()


    def on_mouse_up(self, event):
        self.dragging_index = None


class ConfigGUI:
    def __init__(self, root):
        self.root = root
        
        self.vigembus_installed = (
            is_vigembus_installed()
        )        
        
         # 記錄由 GUI 啟動的子程序
        self.main_process = None
        self.calibration_process = None

        # 按下視窗右上角 X 時，統一關閉所有程序
        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.on_close
        )              
        self.root.title(
            f"{APP_TITLE} Setting UI"
        )
        self.root.resizable(False, False)

        # =========================
        # 搖桿設定放大模式尺寸
        # =========================
        #
        # 這幾個數值可以直接獨立修改：
        #
        # ZOOM_CANVAS_WIDTH / HEIGHT
        #     放大後「整個 Canvas」的寬高。
        #
        # ZOOM_MARGIN_*
        #     放大後「裡面座標圖」距離 Canvas 邊框的距離。
        #
        # 例如想讓座標圖更靠近左邊：
        #     ZOOM_MARGIN_LEFT = 55
        #
        # 想讓座標圖離右邊更遠：
        #     ZOOM_MARGIN_RIGHT = 60
        #
        self.ZOOM_CANVAS_WIDTH = 840
        self.ZOOM_CANVAS_HEIGHT = 660

        self.ZOOM_MARGIN_LEFT = 50
        self.ZOOM_MARGIN_RIGHT = 30
        self.ZOOM_MARGIN_TOP = 20
        self.ZOOM_MARGIN_BOTTOM = 35

        # 原始曲線尺寸與邊距
        self.NORMAL_CANVAS_WIDTH = 280
        self.NORMAL_CANVAS_HEIGHT = 220

        self.NORMAL_MARGIN_LEFT = 48
        self.NORMAL_MARGIN_RIGHT = 20
        self.NORMAL_MARGIN_TOP = 10
        self.NORMAL_MARGIN_BOTTOM = 25

        # 搖桿設定區域放大狀態
        self.stick_settings_zoomed = False
        self.normal_geometry = None

        self.config = configparser.ConfigParser()

        if not CONFIG_PATH.exists():
            messagebox.showerror(
                "錯誤",
                "找不到 config.ini。\n"
                "請將 config_gui.py 放在主程式相同資料夾。"
            )
            self.root.destroy()
            return

        self.config.read(
            CONFIG_PATH,
            encoding="utf-8"
        )

        self.create_variables()
        self.create_widgets()

    def get_serial_ports(self):
        """取得目前所有可用的 COM Port。"""

        return {
            port.device
            for port in serial.tools.list_ports.comports()
        }

    def flash_firmware(self):
        """自動偵測 ESP32-S3 刷機連接埠並刷入相容韌體。"""

        required_files = [
            ESPTOOL_PATH,
            BOOTLOADER_PATH,
            PARTITION_PATH,
            APP_FIRMWARE_PATH,
        ]

        missing_files = [
            path.name
            for path in required_files
            if not path.exists()
        ]

        if missing_files:
            messagebox.showerror(
                "缺少刷機檔案",
                "找不到以下檔案：\n\n"
                + "\n".join(missing_files)
            )
            return

        confirmed = messagebox.askyesno(
            "刷入相容韌體",
            "即將刷入相容的 ESP32-S3 韌體。\n\n"
            "程式將自動偵測 ESP32-S3 的刷機連接埠。\n\n"
            "是否繼續？"
        )

        if not confirmed:
            return

        # 關閉主連接程式，釋放正常模式的 COM Port
        self.stop_main_process()

        # 記錄目前存在的 COM Port
        self.ports_before_flash = self.get_serial_ports()

        messagebox.showinfo(
            "進入刷機模式",
            "請讓 ESP32-S3 進入刷機模式：\n\n"
            "1. 連接EPSP32-S3的OTG接口\n"
            "2. 按住 BOOT 按鈕\n"
            "3. 按一下 RESET / EN 按鈕\n"
            "4. 放開 RESET / EN\n"
            "5. 再放開 BOOT\n\n"
            "完成後不需要做其他操作。\n"
            "程式會自動偵測刷機連接埠。"
        )

        self.flash_detect_attempts = 0
        self.detect_flash_port()

    def detect_flash_port(self):
        """等待 ESP32-S3 刷機模式的新 COM Port。"""

        current_ports = self.get_serial_ports()

        new_ports = (
            current_ports
            - self.ports_before_flash
        )

        if new_ports:
            flash_port = sorted(new_ports)[0]

            messagebox.showinfo(
                "偵測到刷機連接埠",
                f"已偵測到刷機連接埠：{flash_port}\n\n"
                "即將開始刷入韌體。"
            )

            self.start_firmware_flash(
                flash_port
            )
            return

        self.flash_detect_attempts += 1

        if self.flash_detect_attempts >= 60:
            messagebox.showerror(
                "未偵測到刷機連接埠",
                "30 秒內未偵測到新的 COM Port。\n\n"
                "請確認 ESP32-S3 已正確進入刷機模式，"
                "然後重新嘗試。"
            )
            return

        self.root.after(
            500,
            self.detect_flash_port
        )

    def start_firmware_flash(self, port):
        try:
            command = [
                str(ESPTOOL_PATH),

                "--chip",
                "esp32s3",

                "--port",
                port,

                "--baud",
                "921600",

                "write_flash",

                "--flash_mode",
                "dio",

                "--flash_freq",
                "80m",

                "--flash_size",
                "16MB",

                "0x0",
                str(BOOTLOADER_PATH),

                "0x8000",
                str(PARTITION_PATH),

                "0x10000",
                str(APP_FIRMWARE_PATH),
            ]

            command_text = subprocess.list2cmdline(command)

            subprocess.Popen(
                [
                    "cmd.exe",
                    "/k",
                    command_text
                ],
                cwd=str(PROJECT_ROOT),
                creationflags=getattr(
                    subprocess,
                    "CREATE_NEW_CONSOLE",
                    0
                )
            )

        except Exception as exc:
            messagebox.showerror(
                "刷機失敗",
                f"無法啟動韌體刷入程序：\n{exc}"
            )


    def create_variables(self):
        # Sticks
        self.deadzone_var = tk.StringVar(
            value=self.config.get(
                "sticks",
                "deadzone",
                fallback="0.03"
            )
        )
        self.outer_deadzone_var = tk.StringVar(
            value=self.config.get(
                "sticks",
                "outer_deadzone",
                fallback="0.03"
            )
        )        
 
        # 左右搖桿的 5 點 XY 曲線
        self.left_curve_vars = [
            {
                "x": tk.DoubleVar(
                    value=self.config.getfloat(
                        "stick_curve_left",
                        f"point_{i}_x",
                        fallback=i * 0.25
                    )
                ),
                "y": tk.DoubleVar(
                    value=self.config.getfloat(
                        "stick_curve_left",
                        f"point_{i}_y",
                        fallback=self.config.getfloat(
                            "stick_curve_left",
                            f"point_{i}",
                            fallback=i * 0.25
                        )
                    )
                ),
            }
            for i in range(5)
        ]

        self.right_curve_vars = [
            {
                "x": tk.DoubleVar(
                    value=self.config.getfloat(
                        "stick_curve_right",
                        f"point_{i}_x",
                        fallback=i * 0.25
                    )
                ),
                "y": tk.DoubleVar(
                    value=self.config.getfloat(
                        "stick_curve_right",
                        f"point_{i}_y",
                        fallback=self.config.getfloat(
                            "stick_curve_right",
                            f"point_{i}",
                            fallback=i * 0.25
                        )
                    )
                ),
            }
            for i in range(5)
        ] 

        # =========================
        # 左右搖桿防抖
        # =========================

        self.left_stick_smoothing_var = tk.DoubleVar(
            value=self.config.getfloat(
                "stick_curve_left",
                "smoothing",
                fallback=0.0
            )
        )

        self.right_stick_smoothing_var = tk.DoubleVar(
            value=self.config.getfloat(
                "stick_curve_right",
                "smoothing",
                fallback=0.0
            )
        )


        # Rumble
        self.rumble_enabled_var = tk.BooleanVar(
            value=self.config.getboolean(
                "rumble",
                "enabled",
                fallback=True
            )
        )

        self.lf_strength_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "lf_strength",
                fallback="1.00"
            )
        )

        self.hf_strength_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "hf_strength",
                fallback="1.00"
            )
        )

        self.lf_curve_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "lf_curve",
                fallback="1.00"
            )
        )

        self.hf_curve_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "hf_curve",
                fallback="1.00"
            )
        )

        self.lf_to_hf_compensation_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "lf_to_hf_compensation",
                fallback="0.00"
            )
        )

        self.hf_to_lf_compensation_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "hf_to_lf_compensation",
                fallback="0.00"
            )
        )

        self.lf_frequency_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "lf_frequency",
                fallback="225"
            )
        )

        self.hf_frequency_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "hf_frequency",
                fallback="350"
            )
        )

        self.max_amplitude_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "max_amplitude",
                fallback="800"
            )
        )

        # =========================
        # 按鍵映射可選目標
        # =========================
        self.button_options = [
            "NONE",

            # Xbox 按鍵
            "A", "B", "X", "Y",
            "LB", "RB",
            "LT", "RT",
            "START", "BACK", "GUIDE",
            "L_STK", "R_STK",
            "UP", "DOWN", "LEFT", "RIGHT",

            # Xbox 左搖桿方向
            "L_STICK_UP",
            "L_STICK_DOWN",
            "L_STICK_LEFT",
            "L_STICK_RIGHT",

            # Xbox 右搖桿方向
            "R_STICK_UP",
            "R_STICK_DOWN",
            "R_STICK_LEFT",
            "R_STICK_RIGHT",

            # 自定義鍵盤
            "CUSTOM_KEYBOARD",
        ]

        # =========================
        # 搖桿方向映射
        # =========================

        # 左右搖桿各自選擇 4 向或 8 向
        self.stick_direction_mode_vars = {
            "LEFT": tk.StringVar(
                value=self.config.get(
                    "stick_direction_left",
                    "mode",
                    fallback="4WAY"
                ).upper()
            ),

            "RIGHT": tk.StringVar(
                value=self.config.get(
                    "stick_direction_right",
                    "mode",
                    fallback="4WAY"
                ).upper()
            ),
        }

        # 搖桿方向可以映射到：
        # Xbox 按鍵 / D-Pad / 鍵盤
        self.stick_direction_options = [
            "NONE",
            "A", "B", "X", "Y",
            "LB", "RB",
            "LT", "RT",
            "START", "BACK", "GUIDE",
            "L_STK", "R_STK",
            "UP", "DOWN", "LEFT", "RIGHT",
            "CUSTOM_KEYBOARD",
        ]

        # 8 個可能方向
        direction_names = [
            "UP",
            "UP_RIGHT",
            "RIGHT",
            "DOWN_RIGHT",
            "DOWN",
            "DOWN_LEFT",
            "LEFT",
            "UP_LEFT",
        ]

        # 左右搖桿各自保存方向映射
        self.stick_direction_vars = {}

        for side in (
            "LEFT",
            "RIGHT"
        ):
            section_name = (
                "stick_direction_"
                + side.lower()
            )

            self.stick_direction_vars[
                side
            ] = {}

            for direction in direction_names:
                value = self.config.get(
                    section_name,
                    direction.lower(),
                    fallback="NONE"
                ).upper()

                self.stick_direction_vars[
                    side
                ][
                    direction
                ] = tk.StringVar(
                    value=value
                )

        # 方向映射觸發門檻
        self.stick_direction_trigger_var = (
            tk.StringVar(
                value=self.config.get(
                    "stick_direction",
                    "trigger_threshold",
                    fallback="0.60"
                )
            )
        )

        # 方向映射放開門檻
        self.stick_direction_release_var = (
            tk.StringVar(
                value=self.config.get(
                    "stick_direction",
                    "release_threshold",
                    fallback="0.50"
                )
            )
        )

        self.button_vars = {}
        if self.config.has_section("buttons"):
            for name, value in self.config.items("buttons"):
                self.button_vars[name.upper()] = tk.StringVar(
                    value=value.upper()
                )

    def create_widgets(self):
        main = ttk.Frame(self.root, padding=20)
        main.grid(row=0, column=0, sticky="nsew")
        self.main_frame = main

        # 左右兩欄
        content_frame = ttk.Frame(main)
        self.content_frame = content_frame
        content_frame.grid(
            row=1,
            column=0,
            columnspan=2
        )

        left_frame = ttk.Frame(
            content_frame,
            padding=(0, 0, 6, 0)
        )
        left_frame.grid(row=0, column=0, sticky="n")
        self.left_frame = left_frame

        right_frame = ttk.Frame(
            content_frame,
            padding=(6, 0, 0, 0)
        )
        right_frame.grid(row=0, column=1, sticky="n")
        self.right_frame = right_frame

        # =========================
        # 左側：搖桿
        # =========================
        stick_frame = ttk.LabelFrame(
            left_frame,
            text="搖桿設定",
            padding=12
        )
        stick_frame.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 12)
        )
        self.stick_frame = stick_frame

        # =========================
        # 搖桿線性曲線
        # =========================
        curve_notebook = ttk.Notebook(
            stick_frame
        )
        self.curve_notebook = curve_notebook

        curve_notebook.grid(
            row=0,
            column=0,
            columnspan=3,
            pady=(0, 12)
        )

        # 左搖桿頁籤
        left_curve_tab = ttk.Frame(
            curve_notebook,
            padding=8
        )

        curve_notebook.add(
            left_curve_tab,
            text="左搖桿"
        )

        self.left_curve_editor = StickCurveEditor(
            left_curve_tab,
            self.left_curve_vars,
            self.deadzone_var,
            self.outer_deadzone_var,
            label_color="#EA0000",
            zoom_command=self.toggle_stick_settings_zoom
        )

        self.left_curve_editor.pack()

        left_smoothing_frame = ttk.Frame(
            left_curve_tab
        )
        self.left_smoothing_frame = left_smoothing_frame

        left_smoothing_frame.pack(
            fill="x",
            pady=(8, 0)
        )

        ttk.Label(
            left_smoothing_frame,
            text="防抖"
        ).pack(
            side="left"
        )

        def snap_left_smoothing(value):
            value = round(
                float(value),
                1
            )

            self.left_stick_smoothing_var.set(
                value
            )

        ttk.Scale(
            left_smoothing_frame,
            from_=0,
            to=3.0,
            variable=self.left_stick_smoothing_var,
            command=snap_left_smoothing,
            orient="horizontal",
            length=150
        ).pack(
            side="left",
            padx=(10, 0)
        )


        left_smoothing_value_label = ttk.Label(
            left_smoothing_frame,
            width=5
        )

        left_smoothing_value_label.pack(
            side="left"
        )

        def update_left_smoothing_label(
            *args
        ):
            value = round(
                self.left_stick_smoothing_var.get(),
                1
            )

            left_smoothing_value_label.config(
                text=f"{value:.1f}"
            )

        self.left_stick_smoothing_var.trace_add(
            "write",
            update_left_smoothing_label
        )

        update_left_smoothing_label()

        self.create_help(
            left_smoothing_frame,
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
                "數值越高，輸出越穩定，"
                "但也可能產生較明顯的平滑感。"
            )
        ).pack(
            side="left",
            padx=(4, 0)
        )

        self.left_curve_zoom_button = ttk.Button(
            left_smoothing_frame,
            text="放大",
            width=5,
            command=self.toggle_stick_settings_zoom
        )

        self.left_curve_zoom_button.pack(
            side="left",
            padx=(8, 0)
        )

        # 右搖桿頁籤
        right_curve_tab = ttk.Frame(
            curve_notebook,
            padding=8
        )

        curve_notebook.add(
            right_curve_tab,
            text="右搖桿"
        )

        self.right_curve_editor = StickCurveEditor(
            right_curve_tab,
            self.right_curve_vars,
            self.deadzone_var,
            self.outer_deadzone_var,
            label_color="#0066CC",
            zoom_command=self.toggle_stick_settings_zoom
        )

        self.right_curve_editor.pack()

        right_smoothing_frame = ttk.Frame(
            right_curve_tab
        )
        self.right_smoothing_frame = right_smoothing_frame

        right_smoothing_frame.pack(
            fill="x",
            pady=(8, 0)
        )

        ttk.Label(
            right_smoothing_frame,
            text="防抖"
        ).pack(
            side="left"
        )

        def snap_right_smoothing(value):
            value = round(
                float(value),
                1
            )

            self.right_stick_smoothing_var.set(
                value
            )

        ttk.Scale(
            right_smoothing_frame,
            from_=0,
            to=3.0,
            variable=self.right_stick_smoothing_var,
            command=snap_right_smoothing,
            orient="horizontal",
            length=150
        ).pack(
            side="left",
            padx=(10, 0)
        )


        right_smoothing_value_label = ttk.Label(
            right_smoothing_frame,
            width=5
        )

        right_smoothing_value_label.pack(
            side="left"
        )

        def update_right_smoothing_label(
            *args
        ):
            value = round(
                self.right_stick_smoothing_var.get(),
                1
            )

            right_smoothing_value_label.config(
                text=f"{value:.1f}"
            )

        self.right_stick_smoothing_var.trace_add(
            "write",
            update_right_smoothing_label
        )

        update_right_smoothing_label()

        self.create_help(
            right_smoothing_frame,
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
                "數值越高，輸出越穩定，"
                "但也可能產生較明顯的平滑感。"
            )
        ).pack(
            side="left",
            padx=(4, 0)
        )
        
        self.right_curve_zoom_button = ttk.Button(
            right_smoothing_frame,
            text="放大",
            width=5,
            command=self.toggle_stick_settings_zoom
        )

        self.right_curve_zoom_button.pack(
            side="left",
            padx=(8, 0)
        )

        # =========================
        # 死區數值變更時即時重畫曲線圖
        # =========================

        def redraw_deadzone_preview(*args):
            self.left_curve_editor.draw()
            self.right_curve_editor.draw()

        self.deadzone_var.trace_add(
            "write",
            redraw_deadzone_preview
        )

        self.outer_deadzone_var.trace_add(
            "write",
            redraw_deadzone_preview
        )        
        

        self.center_deadzone_widgets = self.add_entry(
            stick_frame,
            row=1,
            label="中心死區",
            variable=self.deadzone_var,
            help_text=(
                "用來消除搖桿放開後的輕微飄移。\n\n"
                "設定範圍：0.00 ～ 1.00\n"
                "0.00 = 無死區\n"
                "0.03 = 建議預設值\n\n"
                "數值越大，搖桿中心附近越不敏感。\n\n"
                "曲線圖左側的灰色區域代表中心死區範圍。\n"
                "搖桿進入此範圍時，中心死區具有最高優先級，\n"
                "實際輸出會直接設為 0，不受曲線設定影響。"
            )
        )

        self.outer_deadzone_widgets = self.add_entry(
            stick_frame,
            row=2,
            label="外圍死區",
            variable=self.outer_deadzone_var,
            help_text=(
                "因搖桿内部的橡膠環存有公差，建議設定0.03。\n\n"
                "設定搖桿接近外圈時，提前輸出最大值。\n\n"
                "設定範圍：0.00 ～ 1.00\n"
                "0.00 = 必須到達 100% 才輸出最大值\n"
                "0.03 = 到達 97% 時直接輸出 100%\n"
                "0.05 = 到達 95% 時直接輸出 100%\n\n"
                "曲線圖右側的灰色區域代表外圍死區範圍。\n"
                "搖桿進入此範圍時，外圍死區具有最高優先級，\n"
                "實際輸出會直接設為最大值，不受曲線設定影響。\n\n"
                "此設定只會截斷最外圍區域，\n"
                "不會重新映射或壓縮整段搖桿行程。"
            )
        )

        # =========================
        # 左側：震動
        # =========================
        rumble_frame = ttk.LabelFrame(
            left_frame,
            text="震動設定",
            padding=12
        )
        rumble_frame.grid(
            row=1,
            column=0,
            sticky="ew"
        )
        self.rumble_frame = rumble_frame

        enabled_check = ttk.Checkbutton(
            rumble_frame,
            text="啟用震動",
            variable=self.rumble_enabled_var
        )
        enabled_check.grid(
            row=0,
            column=0,
            columnspan=2,
            sticky="w",
            pady=4
        )

        help_label = self.create_help(
            rumble_frame,
            "控制是否啟用 Xbox XInput 震動轉換。\n\n"
            "關閉後，遊戲震動不會傳送到控制器。"
        )
        help_label.grid(row=0, column=2, padx=(8, 0))

        self.add_entry(
            rumble_frame, 1, "低頻震動強度", self.lf_strength_var,
            "低頻震動通道的強度倍率。\n\n"
            "設定範圍：0.00 ～ 1.00\n"
            "0.00 = 關閉\n1.00 = 完整強度"
        )
        self.add_entry(
            rumble_frame, 2, "高頻震動強度", self.hf_strength_var,
            "高頻震動通道的強度倍率。\n\n"
            "設定範圍：0.00 ～ 1.00\n"
            "0.00 = 關閉\n1.00 = 完整強度"
        )
  
        self.add_entry(
            rumble_frame, 3, "低頻輸出曲線", self.lf_curve_var,
            "低頻震動的響應曲線。\n\n"
            "設定範圍：0.10 ～ 5.00\n"
            "1.00 = 線性，保持原始比例\n"
            "小於 1.00 = 增強較弱的震動\n"
            "大於 1.00 = 壓低較弱的震動"
        )

        self.add_entry(
            rumble_frame, 4, "高頻輸出曲線", self.hf_curve_var,
            "高頻震動的響應曲線。\n\n"
            "設定範圍：0.10 ～ 5.00\n"
            "1.00 = 線性，保持原始比例\n"
            "小於 1.00 = 增強較弱的震動\n"
            "大於 1.00 = 壓低較弱的震動"
        )  
 
        self.add_entry(
            rumble_frame, 5, "低頻補償", self.hf_to_lf_compensation_var,
            "將一部分 HF 最終振幅加入 LF 通道。\n\n"
            "設定範圍：0.00 ～ 1.00\n"
            "0.00 = 不補償\n0.50 = 加入 HF 振幅的 50%"
        )


        self.add_entry(
            rumble_frame, 6, "高頻補償", self.lf_to_hf_compensation_var,
            "將一部分 LF 最終振幅加入 HF 通道。\n\n"
            "設定範圍：0.00 ～ 1.00\n"
            "0.00 = 不補償\n0.50 = 加入 LF 振幅的 50%"
        )

        self.add_entry(
            rumble_frame, 7, "LF 頻率命令值", self.lf_frequency_var,
            "LF 的 HD Rumble 2 頻率命令值。\n\n"
            "設定範圍：200 ～ 225\n預設：225\n\n"
            "注意：此數值不是直接的實際 Hz。"
        )
        self.add_entry(
            rumble_frame, 8, "HF 頻率命令值", self.hf_frequency_var,
            "HF 的 HD Rumble 2 頻率命令值。\n\n"
            "設定範圍：建議300 ～ 350\n預設：350\n\n"
            "注意：此數值不是直接的實際 Hz。"
        )
        self.add_entry(
            rumble_frame, 9, "最大振幅", self.max_amplitude_var,
            "限制 LF 與 HF 的最大振幅輸出。\n\n"
            "設定範圍：0 ～ 1023\n"
            "輸入接近極限數值有可能損壞控制器馬達。\n\n"
            "本專案建議預設：800"
        )


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
            padding=(10, 6)
        )

        mapping_notebook.add(
            mapping_frame,
            text="按鍵映射"
        )

        mapping_frame.columnconfigure(
            1,
            weight=1
        )

        ttk.Label(mapping_frame, text="Switch 2 Pro").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 8)
        )
        ttk.Label(mapping_frame, text="XInput / Xbox").grid(
            row=0, column=1, sticky="w", pady=(0, 8)
        )

        row = 1
        for switch_name, variable in self.button_vars.items():
            button_name_frame = ttk.Frame(
                mapping_frame,
                width=80
            )

            button_name_frame.grid(
                row=row,
                column=0,
                sticky="w",
                padx=(0, 12),
                pady=6
            )

            ttk.Label(
                button_name_frame,
                text=switch_name,
                width=6,
                anchor="w"
            ).pack(
                side="left"
            )

            ttk.Label(
                button_name_frame,
                text="→"
            ).pack(
                side="left"
            )

            combo = ttk.Combobox(
                mapping_frame,
                textvariable=variable,
                values=self.button_options,
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
                    self.open_keyboard_capture(
                        current_variable
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

        # =========================
        # 頁籤 2：搖桿方向映射
        # =========================
        stick_direction_frame = ttk.Frame(
            mapping_notebook,
            padding=(10, 6)
        )

        mapping_notebook.add(
            stick_direction_frame,
            text="搖桿方向映射"
        )

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
        self.stick_diagonal_widgets = {
            "LEFT": [],
            "RIGHT": [],
        }
        # 保存左右搖桿的模式刷新函式
        # 讓「還原預設」也能主動刷新介面
        self.stick_direction_mode_updaters = {}

        def create_stick_direction_panel(
            parent,
            side
        ):

            # 左右搖桿的第三個模式不同
            if side == "LEFT":
                mode_values = [
                    "4WAY",
                    "8WAY",
                    "映射為右搖桿"
                ]
            else:
                mode_values = [
                    "4WAY",
                    "8WAY",
                    "映射為左搖桿"
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
                    pady=27
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

                combo = ttk.Combobox(
                    cell,
                    textvariable=(
                        self.stick_direction_vars[
                            side
                        ][
                            direction
                        ]
                    ),
                    values=(
                        self.stick_direction_options
                    ),
                    state="readonly",
                    width=12
                )

                combo.pack()
                
                direction_combos.append(
                    combo
                )

                def on_direction_selected(
                    event,
                    current_variable=(
                        self.stick_direction_vars[
                            side
                        ][
                            direction
                        ]
                    )
                ):
                    if (
                        current_variable.get()
                        == "CUSTOM_KEYBOARD"
                    ):
                        self.open_keyboard_capture(
                            current_variable
                        )

                combo.bind(
                    "<<ComboboxSelected>>",
                    on_direction_selected
                )

                if diagonal:
                    self.stick_diagonal_widgets[
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
            mode_combo = ttk.Combobox(
                mode_combo_border,
                textvariable=(
                    self.stick_direction_mode_vars[
                        side
                    ]
                ),
                values=mode_values,
                state="readonly",
                width=8
            )

            mode_combo.pack()

            mode_combo.pack()
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

            # =========================
            # 4 向 / 8 向顯示切換
            # =========================
            def update_direction_mode(
                *args
            ):
                mode = (
                    self.stick_direction_mode_vars[
                        side
                    ].get()
                )

                is_stick_mapping = (
                    mode
                    in (
                        "映射為右搖桿",
                        "映射為左搖桿"
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
                    self.stick_diagonal_widgets[
                        side
                    ]
                ):
                    if (
                        mode == "8WAY"
                        or is_stick_mapping
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

            # 保存這支搖桿的模式刷新函式
            self.stick_direction_mode_updaters[
                side
            ] = update_direction_mode

            mode_combo.bind(
                "<<ComboboxSelected>>",
                update_direction_mode
            )

            # 初始化顯示狀態
            update_direction_mode()

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

        # =========================
        # 觸發 / 放開門檻
        # =========================
        threshold_frame = ttk.Frame(
            stick_direction_frame
        )

        threshold_frame.grid(
            row=2,
            column=0,
            columnspan=2,
            pady=(6, 0)
        )

        ttk.Label(
            threshold_frame,
            text="觸發門檻"
        ).grid(
            row=0,
            column=0,
            padx=(0, 6)
        )

        ttk.Entry(
            threshold_frame,
            textvariable=(
                self.stick_direction_trigger_var
            ),
            width=8
        ).grid(
            row=0,
            column=1,
            padx=(0, 16)
        )

        ttk.Label(
            threshold_frame,
            text="放開門檻"
        ).grid(
            row=0,
            column=2,
            padx=(0, 6)
        )

        ttk.Entry(
            threshold_frame,
            textvariable=(
                self.stick_direction_release_var
            ),
            width=8
        ).grid(
            row=0,
            column=3,
            padx=(0, 6)
        )

        self.create_help(
            threshold_frame,
            (
                "搖桿方向映射\n\n"
                "4WAY：\n"
                "將搖桿判定為上、下、左、右四個方向。\n"
                "斜向推動時，只會觸發最接近的單一方向。\n\n"

                "8WAY：\n"
                "除了上、下、左、右之外，"
                "也可獨立映射四個斜方向。\n\n"

                "映射為另一邊搖桿：\n"
                "將整支實體搖桿的類比輸入，"
                "改為輸出到另一邊的 Xbox 搖桿。\n"
                "左右兩邊同時互相映射時，"
                "會交換左右搖桿。\n\n"

                "觸發門檻：\n"
                "搖桿推動超過此數值後，"
                "才會觸發方向映射。\n\n"

                "放開門檻：\n"
                "方向已觸發後，只有搖桿退回此數值以下，"
                "才會放開目前方向。\n"
                "放開門檻應低於觸發門檻，"
                "可避免在臨界位置反覆觸發與放開。"
            )
        ).grid(
            row=0,
            column=4
        )

        # =========================
        # 狀態列
        # =========================
        status_frame = ttk.Frame(
            right_frame
        )

        status_frame.grid(
            row=1,
            column=0,
            pady=(14, 0)
        )

        # ViGEmBus 狀態
        self.driver_status_label = tk.Label(
            status_frame,
            text="",
            font=("", 10, "bold")
        )

        self.driver_status_label.pack(
            side="left",
            padx=(0, 8)
        )

        self.update_driver_status()

        # =========================
        # 底部功能按鈕
        # =========================
        action_frame = ttk.Frame(main)
        self.action_frame = action_frame
        action_frame.grid(
            row=3,
            column=0,
            columnspan=2,
            pady=(8, 0)
        )

        ttk.Button(
            action_frame,
            text="還原預設",
            command=self.reset_to_defaults
        ).pack(
            side="left",
            ipadx=12,
            ipady=4
        )

        ttk.Button(
            action_frame,
            text="校正搖桿",
            command=self.run_calibration
        ).pack(
            side="left",
            padx=(10, 0),
            ipadx=12,
            ipady=4
        )

        ttk.Button(
            action_frame,
            text="刷入相容韌體",
            command=self.flash_firmware
        ).pack(
            side="left",
            padx=(10, 0),
            ipadx=12,
            ipady=4
        )

        ttk.Button(
            action_frame,
            text="儲存設定",
            command=self.save_config
        ).pack(
            side="left",
            padx=(10, 0),
            ipadx=15,
            ipady=4
        )

        ttk.Button(
            action_frame,
            text="重新啟動連接程式",
            command=self.restart_main
        ).pack(
            side="left",
            padx=(10, 0),
            ipadx=12,
            ipady=4
        )

        ttk.Button(
            action_frame,
            text="Pin",
            command=self.pin_controller
        ).pack(
            side="left",
            padx=(10, 0),
            ipadx=12,
            ipady=4
        )

    def set_zoom_controls_centered(
        self,
        centered
    ):
        """只在放大模式將防抖與死區控制列真正置於水平中央。"""

        if centered:
            # 防抖列本身就是一個 Frame。
            # 取消 fill="x"，讓 Frame 只保持內容所需寬度，
            # 再以 anchor="center" 放在頁籤中央。
            for frame in (
                self.left_smoothing_frame,
                self.right_smoothing_frame
            ):
                frame.pack_forget()

                frame.pack(
                    pady=(8, 0),
                    anchor="center"
                )

            # 死區原本直接放在 stick_frame 的 3 個欄位中，
            # 改成使用左右兩個等權重空白欄：
            #
            # [彈性空白] [文字] [輸入框] [?] [彈性空白]
            #
            # 這樣三個元件的「整體」會位於真正的水平中央。
            self.stick_frame.columnconfigure(
                0,
                weight=1
            )
            self.stick_frame.columnconfigure(
                1,
                weight=0
            )
            self.stick_frame.columnconfigure(
                2,
                weight=0
            )
            self.stick_frame.columnconfigure(
                3,
                weight=0
            )
            self.stick_frame.columnconfigure(
                4,
                weight=1
            )

            for group in (
                self.center_deadzone_widgets,
                self.outer_deadzone_widgets
            ):
                row = group["row"]

                group["label"].grid_configure(
                    row=row,
                    column=1,
                    sticky=""
                )

                group["entry"].grid_configure(
                    row=row,
                    column=2,
                    padx=(15, 0)
                )

                group["help"].grid_configure(
                    row=row,
                    column=3,
                    padx=(8, 0)
                )

        else:
            # 恢復防抖原本的滿寬排列。
            for frame in (
                self.left_smoothing_frame,
                self.right_smoothing_frame
            ):
                frame.pack_forget()

                frame.pack(
                    fill="x",
                    pady=(8, 0)
                )

            # 恢復死區原本 0 / 1 / 2 欄排列。
            for column in range(5):
                self.stick_frame.columnconfigure(
                    column,
                    weight=0
                )

            for group in (
                self.center_deadzone_widgets,
                self.outer_deadzone_widgets
            ):
                row = group["row"]

                group["label"].grid_configure(
                    row=row,
                    column=0,
                    sticky="w"
                )

                group["entry"].grid_configure(
                    row=row,
                    column=1,
                    padx=(15, 0)
                )

                group["help"].grid_configure(
                    row=row,
                    column=2,
                    padx=(8, 0)
                )


    def toggle_stick_settings_zoom(self):
        """放大 / 還原整個搖桿設定區域。"""

        if not self.stick_settings_zoomed:
            self.root.update_idletasks()
            self.normal_geometry = self.root.geometry()

            self.stick_settings_zoomed = True

            # 只保留搖桿設定區域。
            self.right_frame.grid_remove()
            self.rumble_frame.grid_remove()
            self.action_frame.grid_remove()

            # 放大模式約為原始曲線的 3 倍。
            canvas_width = self.ZOOM_CANVAS_WIDTH
            canvas_height = self.ZOOM_CANVAS_HEIGHT

            # 放大模式的座標圖邊距可獨立修改。
            margin_left = self.ZOOM_MARGIN_LEFT
            margin_right = self.ZOOM_MARGIN_RIGHT
            margin_top = self.ZOOM_MARGIN_TOP
            margin_bottom = self.ZOOM_MARGIN_BOTTOM

            # 讓整個搖桿設定區塊在放大視窗中水平置中。
            self.main_frame.columnconfigure(
                0,
                weight=1
            )

            self.content_frame.grid(
                sticky="n"
            )

            self.stick_frame.grid(
                sticky=""
            )

            # 曲線 Notebook 與下面的死區列水平置中。
            self.stick_frame.columnconfigure(
                0,
                weight=1
            )

            self.stick_frame.columnconfigure(
                1,
                weight=0
            )

            self.stick_frame.columnconfigure(
                2,
                weight=0
            )

            # 視窗依內容自動調整，不再放到螢幕 82%。
            self.root.resizable(
                True,
                True
            )

            # 只有放大模式：
            # 防抖整排與兩排死區控制項水平置中。
            self.curve_notebook.grid_configure(
                column=0,
                columnspan=5
            )

            self.set_zoom_controls_centered(
                True
            )

        else:
            self.stick_settings_zoomed = False

            self.right_frame.grid()
            self.rumble_frame.grid()
            self.action_frame.grid()

            canvas_width = self.NORMAL_CANVAS_WIDTH
            canvas_height = self.NORMAL_CANVAS_HEIGHT

            margin_left = self.NORMAL_MARGIN_LEFT
            margin_right = self.NORMAL_MARGIN_RIGHT
            margin_top = self.NORMAL_MARGIN_TOP
            margin_bottom = self.NORMAL_MARGIN_BOTTOM

            self.main_frame.columnconfigure(
                0,
                weight=0
            )

            self.content_frame.grid(
                sticky=""
            )

            self.stick_frame.grid(
                sticky="ew"
            )

            self.stick_frame.columnconfigure(
                0,
                weight=0
            )

            # 恢復原本版型。
            self.set_zoom_controls_centered(
                False
            )

            self.curve_notebook.grid_configure(
                column=0,
                columnspan=3
            )

            if self.normal_geometry:
                self.root.geometry(
                    self.normal_geometry
                )

            self.root.resizable(
                False,
                False
            )

        # 左右曲線使用相同的尺寸與座標圖邊距。
        for editor in (
            self.left_curve_editor,
            self.right_curve_editor
        ):
            editor.set_zoomed(
                self.stick_settings_zoomed
            )

            editor.margin_left = margin_left
            editor.margin_right = margin_right
            editor.margin_top = margin_top
            editor.margin_bottom = margin_bottom

            editor.set_canvas_size(
                canvas_width,
                canvas_height
            )

        # 左右頁籤中的按鈕同步顯示「放大 / 返回」。
        zoom_text = (
            "返回"
            if self.stick_settings_zoomed
            else "放大"
        )

        self.left_curve_zoom_button.configure(
            text=zoom_text
        )

        self.right_curve_zoom_button.configure(
            text=zoom_text
        )

        self.root.update_idletasks()

        # 放大時讓 Tk 依新內容重新計算視窗大小。
        if self.stick_settings_zoomed:
            required_width = (
                self.main_frame.winfo_reqwidth()
            )

            required_height = (
                self.main_frame.winfo_reqheight()
            )

            self.root.geometry(
                f"{required_width}x{required_height}"
            )


    def update_driver_status(self):
        if self.vigembus_installed:
            self.driver_status_label.config(
                text="● ViGEmBus 驅動程式：已安裝",
                fg="green"
            )
        else:
            self.driver_status_label.config(
                text=(
                    "● 未偵測到 ViGEmBus 驅動程式，"
                    "目前無法建立 Xbox 虛擬控制器。"
                ),
                fg="red"
            )        

    def pin_controller(self):
        try:
            COMMAND_PATH.write_text(
                "pin",
                encoding="utf-8"
            )

        except Exception as exc:
            messagebox.showerror(
                "Pin 失敗",
                f"無法呼叫手把：\n{exc}"
            )

    def add_entry(
        self,
        parent,
        row,
        label,
        variable,
        help_text
    ):
        label_widget = ttk.Label(
            parent,
            text=label
        )
        label_widget.grid(
            row=row,
            column=0,
            sticky="w",
            pady=5
        )

        entry = ttk.Entry(
            parent,
            textvariable=variable,
            width=18
        )
        entry.grid(
            row=row,
            column=1,
            padx=(15, 0),
            pady=5
        )

        help_label = self.create_help(
            parent,
            help_text
        )
        help_label.grid(
            row=row,
            column=2,
            padx=(8, 0),
            pady=5
        )

        return {
            "label": label_widget,
            "entry": entry,
            "help": help_label,
            "row": row,
        }


    def open_keyboard_capture(self, variable):
        """開啟視窗，錄製單一鍵盤按鍵或複合按鍵。"""

        capture_window = tk.Toplevel(
            self.root
        )

        capture_window.title(
            "自定義鍵盤映射"
        )

        capture_window.resizable(
            False,
            False
        )

        capture_window.transient(
            self.root
        )

        capture_window.grab_set()

        ttk.Label(
            capture_window,
            text=(
                "請按下要映射的鍵盤按鍵或複合按鍵。\n\n"
                "例如：F12、Ctrl + S、Ctrl + Shift + S"
            ),
            justify="center"
        ).pack(
            padx=25,
            pady=(20, 12)
        )

        result_var = tk.StringVar(
            value="等待按鍵..."
        )

        result_label = ttk.Label(
            capture_window,
            textvariable=result_var,
            font=("", 12, "bold")
        )

        result_label.pack(
            padx=20,
            pady=10
        )

        pressed_keys = set()

        # Tkinter 按鍵名稱轉換成我們底層使用的名稱
        key_name_map = {
            "CONTROL_L": "CTRL",
            "CONTROL_R": "CTRL",
            "SHIFT_L": "SHIFT",
            "SHIFT_R": "SHIFT",
            "ALT_L": "ALT",
            "ALT_R": "ALT",
            "ESCAPE": "ESC",
            "RETURN": "ENTER",
            "PRIOR": "PAGEUP",
            "NEXT": "PAGEDOWN",
        }

        modifier_order = [
            "CTRL",
            "SHIFT",
            "ALT",
            "WIN",
        ]

        def normalize_key(event):
            key = event.keysym.upper()

            return key_name_map.get(
                key,
                key
            )

        def update_display():
            ordered_keys = []

            # 修飾鍵固定排序
            for key in modifier_order:
                if key in pressed_keys:
                    ordered_keys.append(
                        key
                    )

            # 其他按鍵放後面
            for key in pressed_keys:
                if key not in modifier_order:
                    ordered_keys.append(
                        key
                    )

            if ordered_keys:
                result_var.set(
                    " + ".join(
                        ordered_keys
                    )
                )

            return ordered_keys

        def on_key_press(event):
            key = normalize_key(
                event
            )

            pressed_keys.add(
                key
            )

            update_display()

            # 阻止按鍵操作到其他 GUI 元件
            return "break"

        def on_key_release(event):
            ordered_keys = (
                update_display()
            )

            # 至少要有一個按鍵
            if ordered_keys:
                variable.set(
                    "KEYBOARD:"
                    + "+".join(
                        ordered_keys
                    )
                )

                capture_window.destroy()

            return "break"

        capture_window.bind(
            "<KeyPress>",
            on_key_press
        )

        capture_window.bind(
            "<KeyRelease>",
            on_key_release
        )

        # 等待 Tkinter 計算完成視窗大小
        capture_window.update_idletasks()

        # 將錄製視窗置中於主 GUI
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_width = self.root.winfo_width()
        parent_height = self.root.winfo_height()

        window_width = capture_window.winfo_reqwidth()
        window_height = capture_window.winfo_reqheight()

        x = (
            parent_x
            + (parent_width - window_width) // 2
        )

        y = (
            parent_y
            + (parent_height - window_height) // 2
        )

        capture_window.geometry(
            f"+{x}+{y}"
        )

        capture_window.focus_force()

    def create_help(self, parent, text):
        label = tk.Label(
            parent,
            text="?",
            width=2,
            relief="solid",
            borderwidth=1,
            cursor="question_arrow"
        )

        ToolTip(
            label,
            text
        )

        return label

    def reset_to_defaults(self):
        """將可調設定恢復為預設值，不修改搖桿校正資料。"""

        confirmed = messagebox.askyesno(
            "還原預設",
            "確定要將所有可調設定恢復為預設值嗎？\n\n"
            "搖桿校正資料不會被修改。"
        )

        if not confirmed:
            return

        # 搖桿設定
        self.deadzone_var.set("0.03")
        self.outer_deadzone_var.set("0.03")

        # 左右搖桿 XY 曲線恢復為 1:1 線性
        default_curve = [
            0.00,
            0.25,
            0.50,
            0.75,
            1.00
        ]

        for i, value in enumerate(
            default_curve
        ):
            # 左搖桿
            self.left_curve_vars[i][
                "x"
            ].set(
                value
            )

            self.left_curve_vars[i][
                "y"
            ].set(
                value
            )

            # 右搖桿
            self.right_curve_vars[i][
                "x"
            ].set(
                value
            )

            self.right_curve_vars[i][
                "y"
            ].set(
                value
            )

        # 立即重畫曲線
        self.left_curve_editor.draw()
        self.right_curve_editor.draw()

        # 左右搖桿防抖恢復為 0
        self.left_stick_smoothing_var.set(
            0
        )

        self.right_stick_smoothing_var.set(
            0
        )
        # 震動設定
        self.rumble_enabled_var.set(True)
        self.lf_strength_var.set("1.00")
        self.hf_strength_var.set("1.00")
        self.lf_curve_var.set("1.00")
        self.hf_curve_var.set("1.00")
        self.lf_to_hf_compensation_var.set("0.00")
        self.hf_to_lf_compensation_var.set("0.00")
        self.lf_frequency_var.set("225")
        self.hf_frequency_var.set("350")
        self.max_amplitude_var.set("800")        

        # 按鍵映射恢復預設
        default_buttons = {
            "Y": "X",
            "X": "Y",
            "B": "A",
            "A": "B",
            "R": "RB",
            "ZR": "RT",
            "L": "LB",
            "ZL": "LT",
            "MINUS": "BACK",
            "PLUS": "START",
            "L_STK": "L_STK",
            "R_STK": "R_STK",
            "HOME": "GUIDE",
            "CAPT": "NONE",
            "C": "NONE",
            "UP": "UP",
            "DOWN": "DOWN",
            "LEFT": "LEFT",
            "RIGHT": "RIGHT",
            "GR": "NONE",
            "GL": "NONE",
        }

        for button_name, default_value in default_buttons.items():
            if button_name in self.button_vars:
                self.button_vars[button_name].set(
                    default_value
                )

        # =========================
        # 搖桿方向映射恢復預設
        # =========================

        # 共用觸發 / 放開門檻
        self.stick_direction_trigger_var.set(
            "0.60"
        )

        self.stick_direction_release_var.set(
            "0.50"
        )

        # 左右搖桿
        for side in (
            "LEFT",
            "RIGHT"
        ):
            # 模式恢復為 4WAY
            self.stick_direction_mode_vars[
                side
            ].set(
                "4WAY"
            )

            # 所有方向映射恢復為 NONE
            for variable in (
                self.stick_direction_vars[
                    side
                ].values()
            ):
                variable.set(
                    "NONE"
                )

            # 立即刷新 4WAY / 8WAY 顯示狀態
            updater = (
                self.stick_direction_mode_updaters.get(
                    side
                )
            )

            if updater is not None:
                updater()


        messagebox.showinfo(
            "還原完成",
            "可調設定已恢復為預設值。\n\n"
            "請按「儲存設定」套用變更。"
        )


    def run_calibration(self):
        calibration_path = Path(__file__).with_name("calibration.py")

        if not calibration_path.exists():
            messagebox.showerror(
                "錯誤",
                "找不到 calibration.py。"
            )
            return

        try:
            self.calibration_process = subprocess.Popen(
                [
                    "cmd.exe",
                    "/c",
                    str(PYTHON_EXE),
                    str(calibration_path)
                ],
                cwd=str(calibration_path.parent),
                creationflags=getattr(
                    subprocess,
                    "CREATE_NEW_CONSOLE",
                    0
                )
            )
        except Exception as exc:
            messagebox.showerror(
                "錯誤",
                f"無法啟動校正程序：\n{exc}"
            )

    def stop_main_process(self):
        """先正常關閉主連接程式，失敗時再強制終止。"""

        if (
            self.main_process is None
            or self.main_process.poll() is not None
        ):
            self.main_process = None
            return

        try:
            # 先發送 CTRL_BREAK_EVENT，
            # 讓 main.py 有機會執行 finally：
            # 1. 釋放所有鍵盤按鍵
            # 2. 關閉 ESP32 連接
            self.main_process.send_signal(
                signal.CTRL_BREAK_EVENT
            )

            # 最多等待 2 秒正常退出
            self.main_process.wait(
                timeout=2
            )

        except subprocess.TimeoutExpired:
            # 2 秒後仍未退出，才強制終止整個程序樹
            subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(self.main_process.pid),
                    "/T",
                    "/F"
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(
                    subprocess,
                    "CREATE_NO_WINDOW",
                    0
                )
            )

        except Exception:
            # 發送正常關閉訊號失敗時，
            # 使用原本的強制終止方式
            try:
                subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(self.main_process.pid),
                        "/T",
                        "/F"
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(
                        subprocess,
                        "CREATE_NO_WINDOW",
                        0
                    )
                )
            except Exception:
                pass

        self.main_process = None

    def start_main(self, main_path):
        try:
            self.main_process = subprocess.Popen(
                [
                    str(PYTHON_EXE),
                    str(main_path)
                ],
                cwd=str(main_path.parent),
                creationflags=(
                    getattr(
                        subprocess,
                        "CREATE_NEW_CONSOLE",
                        0
                    )
                    |
                    getattr(
                        subprocess,
                        "CREATE_NEW_PROCESS_GROUP",
                        0
                    )
                )
            )

        except Exception as exc:
            messagebox.showerror(
                "錯誤",
                f"無法啟動連接程式：\n{exc}"
            )

    def has_unsaved_changes(self):
        """檢查 GUI 目前設定是否與已載入的 config.ini 不同。"""

        # 搖桿設定
        try:
            current_deadzone = float(
                self.deadzone_var.get()
            )

            saved_deadzone = self.config.getfloat(
                "sticks",
                "deadzone",
                fallback=0.03
            )

            current_outer_deadzone = float(
                self.outer_deadzone_var.get()
            )

            saved_outer_deadzone = self.config.getfloat(
                "sticks",
                "outer_deadzone",
                fallback=0.03
            )

        except (
            ValueError,
            TypeError,
            configparser.Error
        ):
            return True

        if abs(
            current_deadzone
            - saved_deadzone
        ) > 1e-9:
            return True

        if abs(
            current_outer_deadzone
            - saved_outer_deadzone
        ) > 1e-9:
            return True

        # 左右搖桿 XY 曲線
        for i in range(5):
            # =========================
            # 左搖桿
            # =========================
            saved_left_x = self.config.getfloat(
                "stick_curve_left",
                f"point_{i}_x",
                fallback=i * 0.25
            )

            saved_left_y = self.config.getfloat(
                "stick_curve_left",
                f"point_{i}_y",
                fallback=self.config.getfloat(
                    "stick_curve_left",
                    f"point_{i}",
                    fallback=i * 0.25
                )
            )

            current_left_x = (
                self.left_curve_vars[i]["x"].get()
            )

            current_left_y = (
                self.left_curve_vars[i]["y"].get()
            )

            if abs(
                current_left_x - saved_left_x
            ) > 0.0005:
                return True

            if abs(
                current_left_y - saved_left_y
            ) > 0.0005:
                return True

            # =========================
            # 右搖桿
            # =========================
            saved_right_x = self.config.getfloat(
                "stick_curve_right",
                f"point_{i}_x",
                fallback=i * 0.25
            )

            saved_right_y = self.config.getfloat(
                "stick_curve_right",
                f"point_{i}_y",
                fallback=self.config.getfloat(
                    "stick_curve_right",
                    f"point_{i}",
                    fallback=i * 0.25
                )
            )

            current_right_x = (
                self.right_curve_vars[i]["x"].get()
            )

            current_right_y = (
                self.right_curve_vars[i]["y"].get()
            )

            if abs(
                current_right_x - saved_right_x
            ) > 0.0005:
                return True

            if abs(
                current_right_y - saved_right_y
            ) > 0.0005:
                return True

        # =========================
        # 左右搖桿防抖
        # =========================

        try:
            saved_left_smoothing = (
                self.config.getfloat(
                    "stick_curve_left",
                    "smoothing",
                    fallback=0.0
                )
            )

            current_left_smoothing = round(
                self.left_stick_smoothing_var.get(),
                1
            )

            saved_right_smoothing = (
                self.config.getfloat(
                    "stick_curve_right",
                    "smoothing",
                    fallback=0.0
                )
            )

            current_right_smoothing = round(
                self.right_stick_smoothing_var.get(),
                1
            )

        except (
            ValueError,
            TypeError,
            configparser.Error
        ):
            return True

        if abs(
            current_left_smoothing
            - saved_left_smoothing
        ) > 0.0001:
            return True

        if abs(
            current_right_smoothing
            - saved_right_smoothing
        ) > 0.0001:
            return True

        # =========================
        # 震動設定
        # =========================

        # 浮點數設定使用數值比較，
        # 避免 1、1.0、1.00 被誤判為不同。
        current_rumble_float_values = {
            "lf_strength": self.lf_strength_var.get(),
            "hf_strength": self.hf_strength_var.get(),
            "lf_curve": self.lf_curve_var.get(),
            "hf_curve": self.hf_curve_var.get(),
            "lf_to_hf_compensation": (
                self.lf_to_hf_compensation_var.get()
            ),
            "hf_to_lf_compensation": (
                self.hf_to_lf_compensation_var.get()
            ),
        }

        for key, current_value in (
            current_rumble_float_values.items()
        ):
            try:
                current_number = float(
                    current_value
                )

                saved_number = self.config.getfloat(
                    "rumble",
                    key
                )

            except (
                ValueError,
                TypeError,
                configparser.Error
            ):
                return True

            if abs(
                current_number
                - saved_number
            ) > 1e-9:
                print(
                    "未儲存震動設定：",
                    key,
                    "GUI =",
                    current_number,
                    "CONFIG =",
                    saved_number
                )
                return True


        # 整數設定使用整數比較
        current_rumble_int_values = {
            "lf_frequency": self.lf_frequency_var.get(),
            "hf_frequency": self.hf_frequency_var.get(),
            "max_amplitude": self.max_amplitude_var.get(),
        }

        for key, current_value in (
            current_rumble_int_values.items()
        ):
            try:
                current_number = int(
                    current_value
                )

                saved_number = self.config.getint(
                    "rumble",
                    key
                )

            except (
                ValueError,
                TypeError,
                configparser.Error
            ):
                return True

            if current_number != saved_number:
                print(
                    "未儲存震動設定：",
                    key,
                    "GUI =",
                    current_number,
                    "CONFIG =",
                    saved_number
                )
                return True

        # =========================
        # 搖桿方向映射
        # =========================

        # 觸發門檻
        saved_trigger_threshold = self.config.get(
            "stick_direction",
            "trigger_threshold",
            fallback="0.60"
        ).strip()

        current_trigger_threshold = (
            self.stick_direction_trigger_var
            .get()
            .strip()
        )

        if (
            current_trigger_threshold
            != saved_trigger_threshold
        ):
            print(
                "未儲存設定：trigger_threshold",
                "GUI =",
                repr(current_trigger_threshold),
                "CONFIG =",
                repr(saved_trigger_threshold)
            )
            return True

        # 放開門檻
        saved_release_threshold = self.config.get(
            "stick_direction",
            "release_threshold",
            fallback="0.50"
        ).strip()

        current_release_threshold = (
            self.stick_direction_release_var
            .get()
            .strip()
        )

        if (
            current_release_threshold
            != saved_release_threshold
        ):
            print(
                "未儲存設定：release_threshold",
                "GUI =",
                repr(current_release_threshold),
                "CONFIG =",
                repr(saved_release_threshold)
            )
            return True

        # 左右搖桿
        for side in (
            "LEFT",
            "RIGHT"
        ):
            section_name = (
                "stick_direction_"
                + side.lower()
            )

            # -------------------------
            # 4WAY / 8WAY 模式
            # -------------------------
            saved_mode = self.config.get(
                section_name,
                "mode",
                fallback="4WAY"
            ).strip().upper()

            current_mode = (
                self.stick_direction_mode_vars[
                    side
                ]
                .get()
                .strip()
                .upper()
            )

            if current_mode != saved_mode:
                print(
                    "未儲存設定：",
                    section_name,
                    "mode",
                    "GUI =",
                    repr(current_mode),
                    "CONFIG =",
                    repr(saved_mode)
                )
                return True

            # -------------------------
            # 8 個方向的映射
            # -------------------------
            for (
                direction,
                variable
            ) in self.stick_direction_vars[
                side
            ].items():
                saved_value = self.config.get(
                    section_name,
                    direction.lower(),
                    fallback="NONE"
                ).strip().upper()

                current_value = (
                    variable.get()
                    .strip()
                    .upper()
                )

                if current_value != saved_value:
                    print(
                        "未儲存設定：",
                        section_name,
                        direction,
                        "GUI =",
                        repr(current_value),
                        "CONFIG =",
                        repr(saved_value)
                    )
                    return True

        return False


    def restart_main(self):
        """正常關閉舊的主連接程式，再重新啟動。"""

        # 檢查是否有尚未儲存的設定變更
        if self.has_unsaved_changes():
            choice = messagebox.askyesnocancel(
                "尚未儲存設定",
                "偵測到尚未儲存的設定變更。\n\n"
                "是否先儲存設定，再重新啟動連接程式？\n\n"
                "是：儲存後重新啟動\n"
                "否：不儲存，直接重新啟動\n"
                "取消：返回設定畫面"
            )

            # 取消
            if choice is None:
                return

            # 是：先儲存
            if choice:
                if not self.save_config():
                    return


        # 檢查 ViGEmBus
        if not self.vigembus_installed:
            messagebox.showerror(
                "缺少 ViGEmBus",
                "未偵測到 ViGEmBus 驅動程式。\n\n"
                "目前無法啟動 Xbox 虛擬控制器。"
            )
            return

        main_path = Path(__file__).with_name(
            "main.py"
        )

        if not main_path.exists():
            messagebox.showerror(
                "錯誤",
                "找不到 main.py。"
            )
            return

        # 如果舊的連接程式仍在執行，
        # 先正常關閉，再重新啟動
        if (
            self.main_process is not None
            and self.main_process.poll() is None
        ):
            self.stop_main_process()

            # 等待 COM Port 完全釋放
            self.root.after(
                500,
                lambda: self.start_main(
                    main_path
                )
            )
            return

        # 沒有舊程序時直接啟動
        self.start_main(
            main_path
        )


    def on_close(self):
        # 先正常關閉主連接程式
        # 讓 main.py 執行 finally，
        # 釋放鍵盤按鍵並關閉 ESP32 連接
        self.stop_main_process()

        # 關閉校正程式及其所有子程序
        if (
            self.calibration_process is not None
            and self.calibration_process.poll() is None
        ):
            try:
                subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(self.calibration_process.pid),
                        "/T",
                        "/F"
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(
                        subprocess,
                        "CREATE_NO_WINDOW",
                        0
                    )
                )
            except Exception:
                pass

        self.root.destroy()

    def save_config(self):
        try:
            # 驗證數值
            deadzone = float(
                self.deadzone_var.get()
            )

            outer_deadzone = float(
                self.outer_deadzone_var.get()
            )

            lf_strength = float(
                self.lf_strength_var.get()
            )

            hf_strength = float(
                self.hf_strength_var.get()
            )

            lf_curve = float(
                self.lf_curve_var.get()
            )

            hf_curve = float(
                self.hf_curve_var.get()
            )

            lf_to_hf_compensation = float(
                self.lf_to_hf_compensation_var.get()
            )

            hf_to_lf_compensation = float(
                self.hf_to_lf_compensation_var.get()
            )

            lf_frequency = int(
                self.lf_frequency_var.get()
            )

            hf_frequency = int(
                self.hf_frequency_var.get()
            )

            max_amplitude = int(
                self.max_amplitude_var.get()
            )

            # 檢查範圍
            if not 0.0 <= deadzone <= 1.0:
                raise ValueError(
                    "中心死區必須介於 0.00 ～ 1.00。"
                )

            if not 0.0 <= outer_deadzone <= 1.0:
                raise ValueError(
                    "外圍死區必須介於 0.00 ～ 1.00。"
                )

            if not 0.0 <= lf_strength <= 1.0:
                raise ValueError(
                    "LF 強度必須介於 0.00 ～ 1.00。"
                )

            if not 0.0 <= hf_strength <= 1.0:
                raise ValueError(
                    "HF 強度必須介於 0.00 ～ 1.00。"
                )

            if not 0.1 <= lf_curve <= 5.0:
                raise ValueError(
                    "LF 曲線必須介於 0.10 ～ 5.00。"
                )

            if not 0.1 <= hf_curve <= 5.0:
                raise ValueError(
                    "HF 曲線必須介於 0.10 ～ 5.00。"
                )

            if not 0.0 <= lf_to_hf_compensation <= 1.0:
                raise ValueError(
                    "LF → HF 補償必須介於 0.00 ～ 1.00。"
                )

            if not 0.0 <= hf_to_lf_compensation <= 1.0:
                raise ValueError(
                    "HF → LF 補償必須介於 0.00 ～ 1.00。"
                )

            if not 1 <= lf_frequency <= 511:
                raise ValueError(
                    "LF 頻率命令值必須介於 1 ～ 511。"
                )

            if not 1 <= hf_frequency <= 511:
                raise ValueError(
                    "HF 頻率命令值必須介於 1 ～ 511。"
                )

            if not 0 <= max_amplitude <= 1023:
                raise ValueError(
                    "最大振幅必須介於 0 ～ 1023。"
                )

            self.config.set(
                "sticks",
                "outer_deadzone",
                f"{outer_deadzone:.2f}"
            )

            self.config.set(
                "sticks",
                "deadzone",
                f"{deadzone:.2f}"
            )

            # 儲存左右搖桿的 5 點線性曲線
            if not self.config.has_section(
                "stick_curve_left"
            ):
                self.config.add_section(
                    "stick_curve_left"
                )

            if not self.config.has_section(
                "stick_curve_right"
            ):
                self.config.add_section(
                    "stick_curve_right"
                )

            for i in range(5):
                # 左搖桿 XY 曲線
                self.config.set(
                    "stick_curve_left",
                    f"point_{i}_x",
                    f"{self.left_curve_vars[i]['x'].get():.3f}"
                )

                self.config.set(
                    "stick_curve_left",
                    f"point_{i}_y",
                    f"{self.left_curve_vars[i]['y'].get():.3f}"
                )

                # 右搖桿 XY 曲線
                self.config.set(
                    "stick_curve_right",
                    f"point_{i}_x",
                    f"{self.right_curve_vars[i]['x'].get():.3f}"
                )

                self.config.set(
                    "stick_curve_right",
                    f"point_{i}_y",
                    f"{self.right_curve_vars[i]['y'].get():.3f}"
                )

            # 儲存左右搖桿防抖
            self.config.set(
                "stick_curve_left",
                "smoothing",
                f"{self.left_stick_smoothing_var.get():.1f}"
            )

            self.config.set(
                "stick_curve_right",
                "smoothing",
                f"{self.right_stick_smoothing_var.get():.1f}"
            )

            self.config.set(
                "rumble",
                "enabled",
                "true"
                if self.rumble_enabled_var.get()
                else "false"
            )

            self.config.set(
                "rumble",
                "lf_strength",
                f"{lf_strength:.2f}"
            )

            self.config.set(
                "rumble",
                "hf_strength",
                f"{hf_strength:.2f}"
            )

            self.config.set(
                "rumble",
                "lf_curve",
                f"{lf_curve:.2f}"
            )

            self.config.set(
                "rumble",
                "hf_curve",
                f"{hf_curve:.2f}"
            )


            self.config.set(
                "rumble",
                "lf_to_hf_compensation",
                f"{lf_to_hf_compensation:.2f}"
            )

            self.config.set(
                "rumble",
                "hf_to_lf_compensation",
                f"{hf_to_lf_compensation:.2f}"
            )

            self.config.set(
                "rumble",
                "lf_frequency",
                str(lf_frequency)
            )

            self.config.set(
                "rumble",
                "hf_frequency",
                str(hf_frequency)
            )

            self.config.set(
                "rumble",
                "max_amplitude",
                str(max_amplitude)
            )

            # 寫入按鍵映射
            if not self.config.has_section("buttons"):
                self.config.add_section("buttons")

            for switch_name, variable in self.button_vars.items():
                self.config.set(
                    "buttons",
                    switch_name,
                    variable.get().strip().upper()
                )

            # =========================
            # 儲存搖桿方向映射設定
            # =========================

            # 共用門檻設定
            if not self.config.has_section(
                "stick_direction"
            ):
                self.config.add_section(
                    "stick_direction"
                )

            self.config.set(
                "stick_direction",
                "trigger_threshold",
                self.stick_direction_trigger_var.get()
            )

            self.config.set(
                "stick_direction",
                "release_threshold",
                self.stick_direction_release_var.get()
            )

            # 左右搖桿方向映射
            for side in (
                "LEFT",
                "RIGHT"
            ):
                section_name = (
                    "stick_direction_"
                    + side.lower()
                )

                if not self.config.has_section(
                    section_name
                ):
                    self.config.add_section(
                        section_name
                    )

                # 儲存 4WAY / 8WAY
                self.config.set(
                    section_name,
                    "mode",
                    self.stick_direction_mode_vars[
                        side
                    ].get()
                )

                # 儲存 8 個方向
                for (
                    direction,
                    variable
                ) in self.stick_direction_vars[
                    side
                ].items():
                    self.config.set(
                        section_name,
                        direction.lower(),
                        variable.get()
                    )

            with open(
                CONFIG_PATH,
                "w",
                encoding="utf-8"
            ) as config_file:
                self.config.write(
                    config_file
                )

            # 重新載入剛剛實際寫入的設定，
            # 確保 has_unsaved_changes() 使用最新內容。
            self.config.read(
                CONFIG_PATH,
                encoding="utf-8"
            )

            messagebox.showinfo(
                "儲存完成",
                "設定已成功儲存至 config.ini。\n"
                "重新啟動主程式後生效。"
            )

            return True
        
        except ValueError as exc:
            error_text = str(exc)

            # Python 內建的數字格式錯誤改成中文提示
            if (
                "could not convert string to float"
                in error_text
            ):
                error_text = (
                    "輸入的數值格式不正確。\n\n"
                    "請確認所有數值欄位只包含有效的數字。"
                )

            elif (
                "invalid literal for int()"
                in error_text
            ):
                error_text = (
                    "輸入的整數格式不正確。\n\n"
                    "請確認頻率命令值與最大振幅"
                    "只包含有效的整數。"
                )

            messagebox.showerror(
                "設定錯誤",
                error_text
            )

            return False

def main():
    root = tk.Tk()

    # Windows 高 DPI 顯示比例
    root.tk.call(
        "tk",
        "scaling",
        1.4
    )

    ConfigGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()