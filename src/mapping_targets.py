"""Shared validation for controller mapping target strings.

The GUI, layer importer, and runtime all use these rules so a value cannot look
valid in one path and silently become a no-op in another.
"""

XINPUT_BUTTON_TARGETS = frozenset((
    "A", "B", "X", "Y",
    "LB", "RB", "LT", "RT",
    "START", "BACK", "GUIDE",
    "L_STK", "R_STK",
    "UP", "DOWN", "LEFT", "RIGHT",
))

BUTTON_AXIS_TARGETS = frozenset((
    "L_STICK_UP", "L_STICK_DOWN", "L_STICK_LEFT", "L_STICK_RIGHT",
    "R_STICK_UP", "R_STICK_DOWN", "R_STICK_LEFT", "R_STICK_RIGHT",
))

MOUSE_BUTTON_TARGETS = frozenset((
    "MOUSE:LEFT", "MOUSE:RIGHT", "MOUSE:MIDDLE",
))

MOUSE_WHEEL_TARGETS = frozenset((
    "MOUSE:WHEEL_UP", "MOUSE:WHEEL_DOWN",
))


STICK_DIRECTION_KEYS = frozenset((
    "UP", "UP_RIGHT", "RIGHT", "DOWN_RIGHT",
    "DOWN", "DOWN_LEFT", "LEFT", "UP_LEFT",
))

STICK_SETTING_KEYS = frozenset((
    "MODE",
    "DIRECTION_DEADZONE",
    "DIRECTION_DEADZONE_CUSTOM",
    "TRIGGER_THRESHOLD",
    "RELEASE_THRESHOLD",
    "MOUSE_SPEED",
    "MOUSE_DEADZONE",
    "ANALOG_DIRECTION",
)) | STICK_DIRECTION_KEYS

STICK_ANALOG_DIRECTIONS = frozenset(("UP", "DOWN", "LEFT", "RIGHT"))

_STICK_COMMON_MODES = frozenset((
    "4WAY", "8WAY", "映射為滑鼠",
    "MOUSE_WHEEL_LINEAR", "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR",
))

STICK_DIRECTION_MODES_BY_SIDE = {
    "LEFT": _STICK_COMMON_MODES | frozenset(("映射為右搖桿",)),
    "RIGHT": _STICK_COMMON_MODES | frozenset(("映射為左搖桿",)),
}

KEYBOARD_KEY_NAMES = frozenset({
    "CTRL", "SHIFT", "ALT", "WIN",
    "ENTER", "ESC", "SPACE", "TAB", "BACKSPACE",
    "DELETE", "INSERT", "HOME", "END", "PAGEUP", "PAGEDOWN",
    "UP", "DOWN", "LEFT", "RIGHT",
    "VOLUME_MUTE", "VK_VOLUME_MUTE", "XF86AUDIOMUTE",
    "VOLUME_DOWN", "VK_VOLUME_DOWN", "XF86AUDIOLOWERVOLUME",
    "VOLUME_UP", "VK_VOLUME_UP", "XF86AUDIORAISEVOLUME",
    "MEDIA_NEXT", "MEDIA_NEXT_TRACK", "VK_MEDIA_NEXT_TRACK", "XF86AUDIONEXT",
    "MEDIA_PREV", "MEDIA_PREV_TRACK", "VK_MEDIA_PREV_TRACK", "XF86AUDIOPREV",
    "MEDIA_STOP", "VK_MEDIA_STOP", "XF86AUDIOSTOP",
    "MEDIA_PLAY_PAUSE", "VK_MEDIA_PLAY_PAUSE", "XF86AUDIOPLAY", "XF86AUDIOPAUSE",
    *(f"F{index}" for index in range(1, 13)),
    *(str(index) for index in range(10)),
    *(chr(code) for code in range(ord("A"), ord("Z") + 1)),
})

# A gamepad combo presses several XInput buttons in the same report, for as
# long as the physical input is held. It has no delay, order, or repeat.
COMBO_PREFIX = "COMBO:"
COMBO_PENDING_TARGET = "CUSTOM_COMBO"
COMBO_MEMBER_ORDER = (
    "A", "B", "X", "Y",
    "LB", "RB", "LT", "RT",
    "BACK", "START", "GUIDE",
    "L_STK", "R_STK",
    "UP", "DOWN", "LEFT", "RIGHT",
)
COMBO_MIN_MEMBERS = 2
COMBO_MAX_MEMBERS = 4

BUTTON_FIXED_TARGETS = frozenset(("NONE",)) | XINPUT_BUTTON_TARGETS | BUTTON_AXIS_TARGETS
DIRECTION_FIXED_TARGETS = frozenset(("NONE",)) | XINPUT_BUTTON_TARGETS


def normalize_mapping_target(value):
    """Normalize a serialized target without hiding blank/null mistakes."""
    if value is None:
        return ""
    return str(value).strip().upper()


def normalize_mapping_source(value):
    if value is None:
        return ""
    return str(value).strip().upper()


def button_source_error(value, valid_sources):
    """Return an error reason when a physical controller source is unknown."""
    normalized = normalize_mapping_source(value)
    if not normalized:
        return "控制器按鍵來源不可空白"
    valid = {
        normalize_mapping_source(source)
        for source in valid_sources
    }
    if normalized not in valid:
        return "不支援的控制器按鍵來源"
    return ""


def stick_setting_key_error(value):
    normalized = normalize_mapping_source(value)
    if not normalized:
        return "搖桿設定欄位不可空白"
    if normalized not in STICK_SETTING_KEYS:
        return "不支援的搖桿設定欄位"
    return ""


def stick_direction_mode_error(value, side):
    normalized = normalize_mapping_target(value)
    normalized_side = normalize_mapping_source(side)
    if not normalized:
        return "搖桿模式不可空白"
    valid_modes = STICK_DIRECTION_MODES_BY_SIDE.get(normalized_side)
    if valid_modes is None:
        return "不支援的搖桿側別"
    if normalized not in valid_modes:
        return "不支援或方向接反的搖桿模式"
    return ""


def stick_analog_direction_error(value):
    normalized = normalize_mapping_target(value)
    if not normalized:
        return "線性輸入方向不可空白"
    if normalized not in STICK_ANALOG_DIRECTIONS:
        return "不支援的線性輸入方向"
    return ""


def keyboard_combo_error(value):
    """Return an error reason for KEYBOARD: targets, or an empty string."""
    normalized = normalize_mapping_target(value)
    if not normalized.startswith("KEYBOARD:"):
        return "不是鍵盤映射"
    combo = normalized[len("KEYBOARD:"):].strip()
    if not combo:
        return "鍵盤按鍵不可空白"
    tokens = [token.strip() for token in combo.split("+")]
    if any(not token for token in tokens):
        return "鍵盤組合包含空白按鍵"
    unsupported = [token for token in tokens if token not in KEYBOARD_KEY_NAMES]
    if unsupported:
        return "不支援的鍵盤按鍵：" + ", ".join(unsupported)
    return ""


def is_combo_target(value):
    return normalize_mapping_target(value).startswith(COMBO_PREFIX)


def _combo_tokens(value):
    combo = normalize_mapping_target(value)[len(COMBO_PREFIX):].strip()
    return [token.strip() for token in combo.split("+")] if combo else []


def combo_target_error(value):
    """Return an error reason for COMBO: targets, or an empty string."""
    if not is_combo_target(value):
        return "不是手把組合"
    tokens = _combo_tokens(value)
    if any(not token for token in tokens):
        return "手把組合包含空白按鍵"
    unsupported = [token for token in tokens if token not in COMBO_MEMBER_ORDER]
    if unsupported:
        return "不支援的手把組合按鍵：" + ", ".join(unsupported)
    if len(set(tokens)) != len(tokens):
        return "手把組合不可重複同一個按鍵"
    if not COMBO_MIN_MEMBERS <= len(tokens) <= COMBO_MAX_MEMBERS:
        return f"手把組合須包含 {COMBO_MIN_MEMBERS} 到 {COMBO_MAX_MEMBERS} 個按鍵"
    return ""


def combo_members(value):
    """Return a valid combo's buttons in canonical order, or ``()``."""
    if combo_target_error(value):
        return ()
    members = set(_combo_tokens(value))
    return tuple(name for name in COMBO_MEMBER_ORDER if name in members)


def format_combo_target(members):
    """Serialize buttons as ``COMBO:A+B`` in canonical order."""
    chosen = {normalize_mapping_target(name) for name in members}
    return COMBO_PREFIX + "+".join(name for name in COMBO_MEMBER_ORDER if name in chosen)


def _custom_target_error(normalized):
    if normalized == "CUSTOM_KEYBOARD":
        return "CUSTOM_KEYBOARD 尚未完成輸入錄製"
    if normalized == COMBO_PENDING_TARGET:
        return "手把組合尚未完成選擇"
    if normalized.startswith("KEYBOARD:"):
        return keyboard_combo_error(normalized)
    if normalized.startswith(COMBO_PREFIX):
        return combo_target_error(normalized)
    return None


def button_target_error(value):
    normalized = normalize_mapping_target(value)
    if not normalized:
        return "按鍵映射目標不可空白"
    if normalized in BUTTON_FIXED_TARGETS or normalized in MOUSE_BUTTON_TARGETS:
        return ""
    custom_error = _custom_target_error(normalized)
    if custom_error is not None:
        return custom_error
    return "不支援的按鍵映射目標"


def direction_target_error(value):
    normalized = normalize_mapping_target(value)
    if not normalized:
        return "搖桿方向映射目標不可空白"
    if normalized in DIRECTION_FIXED_TARGETS or normalized in MOUSE_WHEEL_TARGETS:
        return ""
    custom_error = _custom_target_error(normalized)
    if custom_error is not None:
        return custom_error
    return "不支援的搖桿方向映射目標"


def validate_stick_setting_key(value):
    normalized = normalize_mapping_source(value)
    error = stick_setting_key_error(normalized)
    if error:
        raise ValueError(f"{normalized or '<空白>'}：{error}")
    return normalized


def validate_stick_direction_mode(value, side):
    normalized = normalize_mapping_target(value)
    error = stick_direction_mode_error(normalized, side)
    if error:
        raise ValueError(f"{normalized or '<空白>'}：{error}")
    return normalized


def validate_stick_analog_direction(value):
    normalized = normalize_mapping_target(value)
    error = stick_analog_direction_error(normalized)
    if error:
        raise ValueError(f"{normalized or '<空白>'}：{error}")
    return normalized


def validate_button_source(value, valid_sources):
    normalized = normalize_mapping_source(value)
    error = button_source_error(normalized, valid_sources)
    if error:
        raise ValueError(f"{normalized or '<空白>'}：{error}")
    return normalized


def validate_button_target(value):
    normalized = normalize_mapping_target(value)
    error = button_target_error(normalized)
    if error:
        raise ValueError(f"{normalized or '<空白>'}：{error}")
    return normalized


def validate_direction_target(value):
    normalized = normalize_mapping_target(value)
    error = direction_target_error(normalized)
    if error:
        raise ValueError(f"{normalized or '<空白>'}：{error}")
    return normalized


def validate_profile_mapping_targets(config, valid_sources):
    """Reject profile mappings that cannot be consumed by the runtime."""
    if config.has_section("buttons"):
        for source, target in config.items("buttons"):
            try:
                validate_button_source(source, valid_sources)
                validate_button_target(target)
            except ValueError as exc:
                raise ValueError(f"[buttons] {source}: {exc}") from exc

    for side in ("LEFT", "RIGHT"):
        section = f"stick_direction_{side.lower()}"
        if not config.has_section(section):
            continue
        for key, value in config.items(section):
            try:
                normalized = validate_stick_setting_key(key)
                if normalized in STICK_DIRECTION_KEYS:
                    validate_direction_target(value)
                elif normalized == "MODE":
                    validate_stick_direction_mode(value, side)
                elif normalized == "ANALOG_DIRECTION":
                    validate_stick_analog_direction(value)
            except ValueError as exc:
                raise ValueError(f"[{section}] {key}: {exc}") from exc
    return config
