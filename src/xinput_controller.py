import configparser
import threading
import time
import ctypes
import vgamepad as vg

from switch2_input import SWITCH_BUTTONS


XB_BUTTONS = {
    "UP": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
    "DOWN": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
    "LEFT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
    "RIGHT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    "START": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
    "BACK": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
    "L_STK": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    "R_STK": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
    "LB": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    "RB": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    "GUIDE": vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
    "A": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    "B": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    "X": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    "Y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
}

# Windows 鍵盤輸入
user32 = ctypes.windll.user32

KEYEVENTF_KEYUP = 0x0002

VK_KEYS = {
    # 修飾鍵
    "CTRL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "WIN": 0x5B,

    # 常用控制鍵
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "SPACE": 0x20,
    "TAB": 0x09,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,

    # 方向鍵
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,

    # 功能鍵
    **{
        f"F{i}": 0x6F + i
        for i in range(1, 13)
    },

    # 數字
    **{
        str(i): 0x30 + i
        for i in range(10)
    },

    # 英文字母
    **{
        chr(code): code
        for code in range(
            ord("A"),
            ord("Z") + 1
        )
    },
}

def parse_keyboard_combo(combo_text):
    """
    將例如 CTRL+SHIFT+S
    轉換成虛擬鍵碼列表。
    """
    keys = []

    for key_name in combo_text.split("+"):
        key_name = key_name.strip().upper()

        if not key_name:
            continue

        vk_code = VK_KEYS.get(key_name)

        if vk_code is not None:
            keys.append(vk_code)

    return keys


def press_keyboard_combo(combo_text):
    """按下鍵盤組合鍵。"""

    for vk_code in parse_keyboard_combo(
        combo_text
    ):
        user32.keybd_event(
            vk_code,
            0,
            0,
            0
        )


def release_keyboard_combo(combo_text):
    """放開鍵盤組合鍵，使用相反順序避免修飾鍵卡住。"""

    keys = parse_keyboard_combo(
        combo_text
    )

    for vk_code in reversed(keys):
        user32.keybd_event(
            vk_code,
            0,
            KEYEVENTF_KEYUP,
            0
        )

def apply_calibration_to_axis(raw_value, center, max_abs, min_abs):
    """Tommy current axis calibration: normalize each side of center independently."""
    signed_value = raw_value - center
    if signed_value > 0:
        return min(signed_value / max(1, max_abs), 1.0)
    if signed_value < 0:
        return -min(-signed_value / max(1, min_abs), 1.0)
    return 0.0

def apply_stick_curve(value, curve_points):
    """
    套用 5 點 XY 分段線性搖桿曲線。

    curve_points 格式：
    [
        (x0, y0),
        (x1, y1),
        (x2, y2),
        (x3, y3),
        (x4, y4),
    ]

    正負方向使用相同曲線，
    最後恢復原本的正負方向。
    """

    # 保留原始方向
    sign = (
        -1.0
        if value < 0.0
        else 1.0
    )

    # 曲線處理 0.0 ～ 1.0 的輸入大小
    magnitude = max(
        0.0,
        min(
            1.0,
            abs(value)
        )
    )

    # 第一個控制點
    first_x, first_y = curve_points[0]

    # =========================
    # 第一點之前：
    # 從固定原點 (0, 0)
    # 線性延伸到第一個控制點
    # =========================
    if magnitude <= first_x:
        if first_x > 1e-9:
            position = (
                magnitude
                / first_x
            )

            output = (
                first_y
                * position
            )

        else:
            # 第一點本身就在 X = 0
            output = first_y

        output = max(
            0.0,
            min(
                1.0,
                output
            )
        )

        return (
            sign
            * output
        )

    # 尋找目前輸入所在的曲線區段
    for index in range(
        len(curve_points) - 1
    ):
        x1, y1 = curve_points[index]

        x2, y2 = curve_points[
            index + 1
        ]

        if (
            x1
            <= magnitude
            <= x2
        ):
            # 防止 X 重疊造成除以 0
            if abs(
                x2 - x1
            ) < 1e-9:
                output = y2

            else:
                position = (
                    (magnitude - x1)
                    / (x2 - x1)
                )

                output = (
                    y1
                    + (
                        y2 - y1
                    )
                    * position
                )

            output = max(
                0.0,
                min(
                    1.0,
                    output
                )
            )

            return (
                sign
                * output
            )

    # =========================
    # 第五點之後：
    # 從第五個控制點
    # 線性延伸到固定終點 (1, 1)
    # =========================
    last_x, last_y = curve_points[-1]

    if last_x < 1.0 - 1e-9:
        position = (
            (magnitude - last_x)
            / (1.0 - last_x)
        )

        output = (
            last_y
            + (
                1.0 - last_y
            )
            * position
        )

    else:
        # 第五點本身就在 X = 1
        output = last_y

    output = max(
        0.0,
        min(
            1.0,
            output
        )
    )

    return (
        sign
        * output
    )

def apply_raw_deadzone(x, y, deadzone):
    magnitude = (x * x + y * y) ** 0.5

    # 只消除中心飄移
    if magnitude < deadzone:
        return 0.0, 0.0

    # 超過死區後保持原始校正數值
    # 不重新縮放、不做外圈圓形化
    return x, y

def apply_outer_deadzone(x, y, outer_deadzone):
    """
    外圍死區／外圍飽和。

    例如 outer_deadzone = 0.03：
    當搖桿徑向輸入達到 97% 時，
    沿著目前方向直接輸出到 100%。

    0% ～ 97% 的輸入保持原樣，不重新縮放。
    """
    if outer_deadzone <= 0.0:
        return x, y

    magnitude = (x * x + y * y) ** 0.5

    if magnitude == 0.0:
        return x, y

    threshold = 1.0 - outer_deadzone

    if magnitude >= threshold:
        scale = 1.0 / magnitude
        return x * scale, y * scale

    return x, y


class XInputController:
    def __init__(self, config, calibration):
        self.pad = vg.VX360Gamepad()
        self.config = config
        self.cal = calibration
        self.deadzone = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "sticks",
                    "deadzone",
                    fallback=0.03
                )
            )
        )
        
        self.outer_deadzone = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "sticks",
                    "outer_deadzone",
                    fallback=0.03
                )
            )
        )        
        
        self.mapping = dict(config.items("buttons"))

        # 左右搖桿的 5 點 XY 分段線性曲線
        #
        # 每個控制點包含：
        # x = 原始搖桿輸入大小
        # y = 套用曲線後的輸出大小
        #
        # 例如：
        # (0.30, 0.50)
        # 表示原始輸入 30% 時，輸出為 50%
        self.stick_curves = {
            "left": [
                (
                    max(
                        0.0,
                        min(
                            1.0,
                            config.getfloat(
                                "stick_curve_left",
                                f"point_{i}_x",
                                fallback=i * 0.25
                            )
                        )
                    ),
                    max(
                        0.0,
                        min(
                            1.0,
                            config.getfloat(
                                "stick_curve_left",
                                f"point_{i}_y",
                                fallback=config.getfloat(
                                    "stick_curve_left",
                                    f"point_{i}",
                                    fallback=i * 0.25
                                )
                            )
                        )
                    ),
                )
                for i in range(5)
            ],

            "right": [
                (
                    max(
                        0.0,
                        min(
                            1.0,
                            config.getfloat(
                                "stick_curve_right",
                                f"point_{i}_x",
                                fallback=i * 0.25
                            )
                        )
                    ),
                    max(
                        0.0,
                        min(
                            1.0,
                            config.getfloat(
                                "stick_curve_right",
                                f"point_{i}_y",
                                fallback=config.getfloat(
                                    "stick_curve_right",
                                    f"point_{i}",
                                    fallback=i * 0.25
                                )
                            )
                        )
                    ),
                )
                for i in range(5)
            ],
        }

        # 記錄目前由 Switch 按鍵保持按下的鍵盤組合
        self._active_keyboard_buttons = set()

        # =========================
        # 搖桿方向映射設定
        # =========================

        # 觸發門檻
        self.stick_direction_trigger = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "stick_direction",
                    "trigger_threshold",
                    fallback=0.60
                )
            )
        )

        # 放開門檻
        self.stick_direction_release = max(
            0.0,
            min(
                self.stick_direction_trigger,
                config.getfloat(
                    "stick_direction",
                    "release_threshold",
                    fallback=0.50
                )
            )
        )

        # 8 個可能方向
        direction_names = (
            "UP",
            "UP_RIGHT",
            "RIGHT",
            "DOWN_RIGHT",
            "DOWN",
            "DOWN_LEFT",
            "LEFT",
            "UP_LEFT",
        )

        # 左右搖桿的模式與方向映射
        self.stick_direction_config = {}

        for side in (
            "LEFT",
            "RIGHT"
        ):
            section_name = (
                "stick_direction_"
                + side.lower()
            )

            mode = config.get(
                section_name,
                "mode",
                fallback="4WAY"
            ).strip().upper()

            # 防止 config.ini 出現未知值
            if mode not in (
                "4WAY",
                "8WAY"
            ):
                mode = "4WAY"

            mappings = {}

            for direction in direction_names:
                mappings[
                    direction
                ] = config.get(
                    section_name,
                    direction.lower(),
                    fallback="NONE"
                ).strip().upper()

            self.stick_direction_config[
                side
            ] = {
                "mode": mode,
                "mappings": mappings,
            }

        # =========================
        # 搖桿方向目前狀態
        # =========================
        #
        # None：
        # 目前沒有任何方向被觸發。
        #
        # 之後只有方向真正改變時，
        # 才執行按下 / 放開。

        self._active_stick_directions = {
            "LEFT": None,
            "RIGHT": None,
        }

        # 記錄由搖桿方向映射保持按下的
        # 鍵盤組合。
        self._active_stick_keyboard = {
            "LEFT": None,
            "RIGHT": None,
        }

        # =========================
        # 鍵盤輸出來源追蹤
        # =========================
        #
        # 格式：
        # {
        #     "W": {
        #         "BUTTON:A",
        #         "STICK:LEFT",
        #     }
        # }
        #
        # 同一個鍵盤組合可以同時由
        # 多個來源保持。
        #
        # 只有最後一個來源放開時，
        # 才真正送出 KeyUp。

        self._keyboard_combo_sources = {}


        # Rumble settings
        self.rumble_enabled = config.getboolean(
            "rumble", "enabled", fallback=True
        )
        self.lf_strength = max(
            0.0, min(1.0, config.getfloat("rumble", "lf_strength", fallback=1.0))
        )
        self.hf_strength = max(
            0.0, min(1.0, config.getfloat("rumble", "hf_strength", fallback=1.0))
        )
        
        self.lf_curve = max(
            0.1,
            min(
                5.0,
                config.getfloat(
                    "rumble",
                    "lf_curve",
                    fallback=1.0
                )
            )
        )

        self.hf_curve = max(
            0.1,
            min(
                5.0,
                config.getfloat(
                    "rumble",
                    "hf_curve",
                    fallback=1.0
                )
            )
        )       
        
        self.lf_to_hf_compensation = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "rumble",
                    "lf_to_hf_compensation",
                    fallback=0.0,
                ),
            ),
        )
 
        self.hf_to_lf_compensation = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "rumble",
                    "hf_to_lf_compensation",
                    fallback=0.0,
                ),
            ),
        ) 
                
        self.lf_frequency = max(
            1, min(511, config.getint("rumble", "lf_frequency", fallback=225))
        )
        self.hf_frequency = max(
            1, min(511, config.getint("rumble", "hf_frequency", fallback=350))
        )
        self.max_amplitude = max(
            0, min(1023, config.getint("rumble", "max_amplitude", fallback=800))
        )

        self._rumble_sender = None
        self._rumble_lock = threading.Lock()
        self._rumble_generation = 0

        self.pad.register_notification(
            callback_function=self._vibration_callback
        )

    def _acquire_keyboard_combo(
        self,
        combo_text,
        source
    ):
        """
        讓指定來源開始保持鍵盤組合。

        第一個來源取得時才真正 KeyDown。
        """

        combo_text = (
            combo_text
            .strip()
            .upper()
        )

        if not combo_text:
            return

        sources = (
            self._keyboard_combo_sources
            .setdefault(
                combo_text,
                set()
            )
        )

        # 這個來源已經持有，
        # 不重複 KeyDown。
        if source in sources:
            return

        # 第一個來源：
        # 真正按下鍵盤組合
        if not sources:
            press_keyboard_combo(
                combo_text
            )

        sources.add(
            source
        )


    def _release_keyboard_combo_source(
        self,
        combo_text,
        source
    ):
        """
        讓指定來源停止保持鍵盤組合。

        只有最後一個來源放開時，
        才真正 KeyUp。
        """

        combo_text = (
            combo_text
            .strip()
            .upper()
        )

        if not combo_text:
            return

        sources = (
            self._keyboard_combo_sources
            .get(
                combo_text
            )
        )

        if not sources:
            return

        sources.discard(
            source
        )

        # 還有其他來源保持：
        # 不放開鍵盤
        if sources:
            return

        # 最後一個來源已放開
        release_keyboard_combo(
            combo_text
        )

        self._keyboard_combo_sources.pop(
            combo_text,
            None
        )


    def release_all_keyboard_buttons(self):
        """
        強制釋放所有目前由程式保持的鍵盤組合。

        用於：
        - 控制器斷線
        - 程式停止
        - 重新啟動連接程式

        這裡直接清空所有來源，
        確保不會留下卡住的鍵盤按鍵。
        """

        # =========================
        # 釋放所有目前保持的鍵盤組合
        # =========================
        for combo_text in list(
            self._keyboard_combo_sources.keys()
        ):
            release_keyboard_combo(
                combo_text
            )

        # 清空統一來源追蹤
        self._keyboard_combo_sources.clear()

        # 清空普通 Switch 按鍵狀態
        self._active_keyboard_buttons.clear()

        # 清空左右搖桿鍵盤與方向狀態
        for side in (
            "LEFT",
            "RIGHT"
        ):
            self._active_stick_keyboard[
                side
            ] = None

            self._active_stick_directions[
                side
            ] = None

    def set_rumble_sender(self, sender):
        """sender(lf_freq, lf_amp, hf_freq, hf_amp)"""
        self._rumble_sender = sender

    def _vibration_callback(
        self,
        client,
        target,
        large_motor,
        small_motor,
        led_number,
        user_data,
    ):
        del client, target, led_number, user_data

        if not self.rumble_enabled or self._rumble_sender is None:
            return

        # 將 XInput 震動輸入轉換為 0.0 ～ 1.0
        lf_input = max(
            0.0,
            min(1.0, int(large_motor) / 255.0)
        )

        hf_input = max(
            0.0,
            min(1.0, int(small_motor) / 255.0)
        )

        # 套用 LF / HF 獨立震動曲線
        lf_output = lf_input ** self.lf_curve
        hf_output = hf_input ** self.hf_curve

        # 套用最大振幅與強度
        # 曲線處理後、強度調整前的原始振幅
        raw_lf_amp = int(
            self.max_amplitude
            * lf_output
        )

        raw_hf_amp = int(
            self.max_amplitude
            * hf_output
        )

        # 各通道自身的強度調整
        lf_amp = int(
            raw_lf_amp
            * self.lf_strength
        )

        hf_amp = int(
            raw_hf_amp
            * self.hf_strength
        )

        # LF → HF 補償使用強度調整前的 LF 振幅
        hf_amp += int(
            raw_lf_amp
            * self.lf_to_hf_compensation
        )

        # HF → LF 補償使用強度調整前的 HF 振幅
        lf_amp += int(
            raw_hf_amp
            * self.hf_to_lf_compensation
        )

        # 最終限制
        lf_amp = max(
            0,
            min(
                self.max_amplitude,
                lf_amp
            )
        )

        hf_amp = max(
            0,
            min(
                self.max_amplitude,
                hf_amp
            )
        )

        # # 只有震動振幅大於 0 時才顯示
        # if lf_amp > 0 or hf_amp > 0:
            # print(
                # f"RAW LF: {raw_lf_amp:4d}  "
                # f"RAW HF: {raw_hf_amp:4d}  |  "
                # f"OUT LF: {lf_amp:4d}  "
                # f"OUT HF: {hf_amp:4d}"
            # )

        with self._rumble_lock:
            self._rumble_generation += 1
            generation = self._rumble_generation

        # XInput notifications are change events. Like original 0.0.1, keep sending
        # the current frame every 15 ms until a newer vibration event replaces it.
        def rumble_worker():
            for _ in range(500):
                with self._rumble_lock:
                    if generation != self._rumble_generation:
                        return

                self._rumble_sender(
                    self.lf_frequency,
                    lf_amp,
                    self.hf_frequency,
                    hf_amp,
                )

                if lf_amp == 0 and hf_amp == 0:
                    return

                time.sleep(0.015)

        threading.Thread(
            target=rumble_worker,
            daemon=True,
            name="XInputRumble",
        ).start()

    def _axis_pair(self, raw_xy, side):
        cal = self.cal[side]
        cx, cy = cal["center"]
        max_x, max_y = cal["max"]
        min_x, min_y = cal["min"]

        # Same current processing path used by Tommy:
        # per-axis calibration -> clamp -> 3% radial deadzone.
        x = max(-1.0, min(1.0, apply_calibration_to_axis(
            raw_xy[0], cx, max_x, min_x
        )))
        y = max(-1.0, min(1.0, apply_calibration_to_axis(
            raw_xy[1], cy, max_y, min_y
        )))

        # =========================
        # 死區最高優先級
        # =========================
        #
        # 死區判定永遠使用：
        # 「校正後、套用曲線前」的原始搖桿位置。
        #
        # 因此 XY 曲線無法：
        # 1. 突破中心死區
        # 2. 延後或取消外圍死區

        raw_x = x
        raw_y = y

        raw_magnitude = (
            raw_x * raw_x
            + raw_y * raw_y
        ) ** 0.5

        # =========================
        # 最高優先級 1：中心死區
        # =========================
        if raw_magnitude < self.deadzone:
            return 0.0, 0.0

        # =========================
        # 依照搖桿徑向距離套用曲線
        # =========================
        #
        # 曲線只改變「推動距離」，
        # 不改變原始搖桿方向。
        #
        # 這樣斜方向不會因為 X、Y
        # 分別被放大而超出單位圓。

        curve_points = self.stick_curves[
            side
        ]

        # =========================
        # 檢查是否為預設 1:1 線性曲線
        # =========================
        #
        # 如果曲線完全是預設值，
        # 就完全旁路曲線處理。
        #
        # 這樣可以 100% 保留原本的
        # raw_x / raw_y，不額外圓形化。

        default_curve = (
            (0.00, 0.00),
            (0.25, 0.25),
            (0.50, 0.50),
            (0.75, 0.75),
            (1.00, 1.00),
        )

        is_default_curve = all(
            abs(
                point_x - default_x
            ) < 1e-6
            and
            abs(
                point_y - default_y
            ) < 1e-6
            for (
                point_x,
                point_y
            ), (
                default_x,
                default_y
            ) in zip(
                curve_points,
                default_curve
            )
        )

        # =========================
        # 預設曲線：完全旁路
        # =========================
        if is_default_curve:
            x = raw_x
            y = raw_y

        # =========================
        # 自定義曲線：徑向比例縮放
        # =========================
        else:
            # 使用校正後、曲線前的徑向距離
            # 作為曲線的輸入值。
            #
            # 如果原始斜方向本來超過 1.0，
            # 曲線輸入最多只取 1.0，
            # 避免破壞原本 raw 的外圍形狀。
            input_magnitude = min(
                1.0,
                raw_magnitude
            )

            # 對徑向距離套用自定義曲線
            output_magnitude = apply_stick_curve(
                input_magnitude,
                curve_points
            )

            # 曲線輸出限制在 0.0 ～ 1.0
            output_magnitude = max(
                0.0,
                min(
                    1.0,
                    output_magnitude
                )
            )

            # =========================
            # 按相同比例縮放 raw X / Y
            # =========================
            #
            # 不使用 max(|X|, |Y|)，
            # 因此不會額外產生方形／十字形。
            #
            # 不重新建立單位圓座標，
            # 只用相同比例縮放原始 X / Y。

            if input_magnitude > 0.0:
                scale = (
                    output_magnitude
                    / input_magnitude
                )

                x = raw_x * scale
                y = raw_y * scale

            else:
                x = 0.0
                y = 0.0

        # =========================
        # 最高優先級 2：外圍死區
        # =========================
        #
        # 外圍死區使用曲線前的
        # raw_magnitude 判定。
        #
        # 與自定義曲線使用相同的
        # 徑向座標系，避免十字軸附近
        # 因不同判定方式產生跳變。

        if self.outer_deadzone > 0.0:
            outer_threshold = (
                1.0
                - self.outer_deadzone
            )

            if (
                raw_magnitude
                >= outer_threshold
            ):
                # 使用原始 raw 方向，
                # 強制徑向長度到 1.0。
                if raw_magnitude > 0.0:
                    x = (
                        raw_x
                        / raw_magnitude
                    )

                    y = (
                        raw_y
                        / raw_magnitude
                    )

        return x, y

    def _get_stick_direction(
        self,
        x,
        y,
        side
    ):
        """
        根據搖桿 XY 判斷目前方向。

        支援：
        - 4WAY：上 / 下 / 左 / 右
        - 8WAY：包含四個斜方向

        使用觸發 / 放開雙門檻，
        避免搖桿在門檻附近抖動。
        """

        magnitude = (
            x * x
            + y * y
        ) ** 0.5

        current_direction = (
            self._active_stick_directions[
                side
            ]
        )

        # =========================
        # 遲滯判定
        # =========================

        # 目前沒有方向：
        # 必須達到「觸發門檻」才開始判定
        if current_direction is None:
            if (
                magnitude
                < self.stick_direction_trigger
            ):
                return None

        # 目前已有方向：
        # 只有低於「放開門檻」才完全放開
        else:
            if (
                magnitude
                <= self.stick_direction_release
            ):
                return None

        mode = (
            self.stick_direction_config[
                side
            ][
                "mode"
            ]
        )

        # =========================
        # 4 向模式
        # =========================
        #
        # 比較 |X| 與 |Y|，
        # 哪一軸較大就使用哪一軸。
        #
        # 斜推時仍然只會得到一個方向。

        if mode == "4WAY":
            if abs(x) >= abs(y):
                if x >= 0.0:
                    return "RIGHT"

                return "LEFT"

            else:
                if y >= 0.0:
                    return "UP"

                return "DOWN"

        # =========================
        # 8 向模式
        # =========================
        #
        # 使用角度將完整 360 度
        # 分成 8 個各 45 度的區域。

        import math

        angle = math.degrees(
            math.atan2(
                y,
                x
            )
        )

        # atan2 範圍為 -180 ～ 180
        # 轉成 0 ～ 360
        if angle < 0.0:
            angle += 360.0

        if (
            angle >= 337.5
            or angle < 22.5
        ):
            return "RIGHT"

        if angle < 67.5:
            return "UP_RIGHT"

        if angle < 112.5:
            return "UP"

        if angle < 157.5:
            return "UP_LEFT"

        if angle < 202.5:
            return "LEFT"

        if angle < 247.5:
            return "DOWN_LEFT"

        if angle < 292.5:
            return "DOWN"

        return "DOWN_RIGHT"

    def _is_stick_direction_mapping_enabled(
        self,
        side
    ):
        """
        判斷指定搖桿目前是否啟用方向映射。

        只檢查目前模式實際使用的方向：
        - 4WAY：上下左右
        - 8WAY：全部 8 個方向

        只要其中任何一個方向不是 NONE，
        就視為這支搖桿已切換成方向映射模式。
        """

        config_data = (
            self.stick_direction_config[
                side
            ]
        )

        mode = config_data[
            "mode"
        ]

        mappings = config_data[
            "mappings"
        ]

        if mode == "4WAY":
            active_directions = (
                "UP",
                "RIGHT",
                "DOWN",
                "LEFT",
            )

        else:
            active_directions = (
                "UP",
                "UP_RIGHT",
                "RIGHT",
                "DOWN_RIGHT",
                "DOWN",
                "DOWN_LEFT",
                "LEFT",
                "UP_LEFT",
            )

        for direction in active_directions:
            target = mappings.get(
                direction,
                "NONE"
            )

            target = (
                target
                .strip()
                .upper()
            )

            if target not in (
                "",
                "NONE"
            ):
                return True

        return False


    def update(self, state):
        # Reset XInput buttons/triggers, then rebuild current state.
        self.pad.reset()

        # =========================
        # Switch 按鍵 → Xbox 搖桿方向
        # =========================
        #
        # 這些值稍後會和正常的類比搖桿輸出整合。

        button_lx = 0.0
        button_ly = 0.0

        button_rx = 0.0
        button_ry = 0.0


        for switch_name, target in self.mapping.items():
            switch_name = switch_name.upper()
            target = target.upper().strip()

            mask = SWITCH_BUTTONS.get(
                switch_name
            )

            if mask is None:
                continue

            is_pressed = bool(
                state.buttons & mask
            )

            # =========================
            # 自定義鍵盤映射
            # =========================
            if target.startswith(
                "KEYBOARD:"
            ):
                combo_text = target[
                    len("KEYBOARD:"):
                ].strip()

                # 每個 Switch 實體按鍵
                # 都有自己的唯一來源名稱
                source = (
                    "BUTTON:"
                    + switch_name
                )

                # Switch 按鍵剛按下
                if (
                    is_pressed
                    and switch_name
                    not in self._active_keyboard_buttons
                ):
                    self._acquire_keyboard_combo(
                        combo_text,
                        source
                    )

                    self._active_keyboard_buttons.add(
                        switch_name
                    )

                # Switch 按鍵剛放開
                elif (
                    not is_pressed
                    and switch_name
                    in self._active_keyboard_buttons
                ):
                    self._release_keyboard_combo_source(
                        combo_text,
                        source
                    )

                    self._active_keyboard_buttons.discard(
                        switch_name
                    )

                continue

            # =========================
            # 一般 XInput 映射
            # =========================
            if target in ("", "NONE"):
                continue

            if not is_pressed:
                continue

            # =========================
            # Xbox 左搖桿方向
            # =========================
            if target == "L_STICK_UP":
                button_ly = 1.0
                continue

            elif target == "L_STICK_DOWN":
                button_ly = -1.0
                continue

            elif target == "L_STICK_LEFT":
                button_lx = -1.0
                continue

            elif target == "L_STICK_RIGHT":
                button_lx = 1.0
                continue

            # =========================
            # Xbox 右搖桿方向
            # =========================
            elif target == "R_STICK_UP":
                button_ry = 1.0
                continue

            elif target == "R_STICK_DOWN":
                button_ry = -1.0
                continue

            elif target == "R_STICK_LEFT":
                button_rx = -1.0
                continue

            elif target == "R_STICK_RIGHT":
                button_rx = 1.0
                continue


            if target == "LT":
                self.pad.left_trigger(
                    value=255
                )

            elif target == "RT":
                self.pad.right_trigger(
                    value=255
                )

            elif target in XB_BUTTONS:
                self.pad.press_button(
                    button=XB_BUTTONS[target]
                )

        # =========================
        # 搖桿
        # =========================
        lx, ly = self._axis_pair(
            state.left_stick,
            "left"
        )

        rx, ry = self._axis_pair(
            state.right_stick,
            "right"
        )

        # 判斷左右搖桿是否啟用方向映射
        left_mapping_enabled = (
            self._is_stick_direction_mapping_enabled(
                "LEFT"
            )
        )

        right_mapping_enabled = (
            self._is_stick_direction_mapping_enabled(
                "RIGHT"
            )
        )

        # =========================
        # 搖桿方向映射
        # =========================

        if left_mapping_enabled:
            left_direction = (
                self._get_stick_direction(
                    lx,
                    ly,
                    "LEFT"
                )
            )

            self._active_stick_directions[
                "LEFT"
            ] = left_direction

        else:
            left_direction = None

            self._active_stick_directions[
                "LEFT"
            ] = None

        if right_mapping_enabled:
            right_direction = (
                self._get_stick_direction(
                    rx,
                    ry,
                    "RIGHT"
                )
            )

            self._active_stick_directions[
                "RIGHT"
            ] = right_direction

        else:
            right_direction = None

            self._active_stick_directions[
                "RIGHT"
            ] = None

        # =========================
        # 方向 → Xbox / 鍵盤映射
        # =========================

        for side, direction in (
            ("LEFT", left_direction),
            ("RIGHT", right_direction),
        ):
            # 取得這支搖桿上一幀
            # 正在保持的鍵盤組合
            previous_keyboard = (
                self._active_stick_keyboard[
                    side
                ]
            )

            # 預設目前沒有鍵盤輸出
            current_keyboard = None

            # =========================
            # 取得目前方向的映射
            # =========================
            if direction is not None:
                target = (
                    self.stick_direction_config[
                        side
                    ][
                        "mappings"
                    ].get(
                        direction,
                        "NONE"
                    )
                    .strip()
                    .upper()
                )

            else:
                target = "NONE"

            # =========================
            # 鍵盤映射
            # =========================
            if target.startswith(
                "KEYBOARD:"
            ):
                current_keyboard = target[
                    len("KEYBOARD:"):
                ].strip()

            # =========================
            # 如果鍵盤組合改變：
            # 先放開舊的
            # =========================
            # 每支搖桿都有自己的唯一來源
            source = (
                "STICK:"
                + side
            )

            # =========================
            # 鍵盤組合改變：
            # 先釋放舊映射的來源
            # =========================
            if (
                previous_keyboard
                and previous_keyboard
                != current_keyboard
            ):
                self._release_keyboard_combo_source(
                    previous_keyboard,
                    source
                )

            # =========================
            # 再取得新映射
            # =========================
            if (
                current_keyboard
                and current_keyboard
                != previous_keyboard
            ):
                self._acquire_keyboard_combo(
                    current_keyboard,
                    source
                )

            # 保存目前鍵盤狀態
            self._active_stick_keyboard[
                side
            ] = current_keyboard

            # =========================
            # Xbox 映射
            # =========================

            # NONE 或鍵盤映射
            # 不需要再處理 Xbox 輸出
            if (
                target in (
                    "",
                    "NONE"
                )
                or target.startswith(
                    "KEYBOARD:"
                )
            ):
                continue

            # LT
            if target == "LT":
                self.pad.left_trigger(
                    value=255
                )

            # RT
            elif target == "RT":
                self.pad.right_trigger(
                    value=255
                )

            # 一般 Xbox 按鍵
            elif target in XB_BUTTONS:
                self.pad.press_button(
                    button=XB_BUTTONS[
                        target
                    ]
                )


        # =========================
        # 輸出類比搖桿
        # =========================
        #
        # 如果該搖桿啟用了方向映射：
        # 原本的 XInput 類比搖桿輸出歸零。
        #
        # 如果沒有啟用方向映射：
        # 保持原本的類比搖桿輸出。

        # =========================
        # 最終左搖桿輸出
        # =========================

        left_button_stick_active = (
            button_lx != 0.0
            or button_ly != 0.0
        )

        if left_button_stick_active:
            # Switch 按鍵模擬 Xbox 左搖桿
            final_lx = button_lx
            final_ly = button_ly

        elif left_mapping_enabled:
            # 實體左搖桿已作為方向映射使用
            final_lx = 0.0
            final_ly = 0.0

        else:
            # 正常實體左搖桿輸出
            final_lx = lx
            final_ly = ly

        # =========================
        # 最終右搖桿輸出
        # =========================

        right_button_stick_active = (
            button_rx != 0.0
            or button_ry != 0.0
        )

        if right_button_stick_active:
            # Switch 按鍵模擬 Xbox 右搖桿
            final_rx = button_rx
            final_ry = button_ry

        elif right_mapping_enabled:
            # 實體右搖桿已作為方向映射使用
            final_rx = 0.0
            final_ry = 0.0

        else:
            # 正常實體右搖桿輸出
            final_rx = rx
            final_ry = ry

        # =========================
        # 寫入 Xbox 虛擬搖桿
        # =========================

        self.pad.left_joystick_float(
            x_value_float=final_lx,
            y_value_float=final_ly
        )

        self.pad.right_joystick_float(
            x_value_float=final_rx,
            y_value_float=final_ry
        )

        self.pad.update()
