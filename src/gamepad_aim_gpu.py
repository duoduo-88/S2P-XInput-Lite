"""Windows hardware rendering for the aim viewport."""

import ctypes
import sys
from functools import lru_cache, partial
from uuid import UUID


_UINT = ctypes.c_uint32
_FLOAT = ctypes.c_float
_PTR = ctypes.c_void_p
_HRESULT = ctypes.c_int32
_SYSTEM32 = 0x00000800  # LOAD_LIBRARY_SEARCH_SYSTEM32


class _GUID(ctypes.Structure):
    _fields_ = [
        ("data1", _UINT),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


class _Size(ctypes.Structure):
    _fields_ = [("width", _UINT), ("height", _UINT)]


class _PixelFormat(ctypes.Structure):
    _fields_ = [("format", _UINT), ("alpha_mode", _UINT)]


class _TargetProperties(ctypes.Structure):
    _fields_ = [
        ("type", _UINT),
        ("pixel_format", _PixelFormat),
        ("dpi_x", _FLOAT),
        ("dpi_y", _FLOAT),
        ("usage", _UINT),
        ("min_level", _UINT),
    ]


class _HwndProperties(ctypes.Structure):
    _fields_ = [("hwnd", _PTR), ("size", _Size), ("present_options", _UINT)]


class _Color(ctypes.Structure):
    _fields_ = [("r", _FLOAT), ("g", _FLOAT), ("b", _FLOAT), ("a", _FLOAT)]


class _Point(ctypes.Structure):
    _fields_ = [("x", _FLOAT), ("y", _FLOAT)]


class _Rect(ctypes.Structure):
    _fields_ = [("left", _FLOAT), ("top", _FLOAT), ("right", _FLOAT), ("bottom", _FLOAT)]


class _Ellipse(ctypes.Structure):
    _fields_ = [("center", _Point), ("radius_x", _FLOAT), ("radius_y", _FLOAT)]


_D2D_FACTORY_ID = _GUID.from_buffer_copy(UUID("06152247-6f50-465a-9245-118bfd3b6007").bytes_le)
_DWRITE_FACTORY_ID = _GUID.from_buffer_copy(UUID("b859ee5a-d838-4b5b-a2e8-1adc7d93db48").bytes_le)
_BACKGROUND = _Color(16 / 255, 24 / 255, 37 / 255, 1.0)
_TEXT_ALIGNMENT = {"left": 0, "right": 1, "center": 2}
_D2D_TARGET_HARDWARE = 2
_D2D_PRESENT_IMMEDIATELY = 2
TARGET_OUTLINE_WIDTH = 1


@lru_cache(maxsize=32)
def target_outline_color(fill):
    red, green, blue = bytes.fromhex(fill[1:])
    brightness = 299 * red + 587 * green + 114 * blue
    return "#edf4fc" if brightness < 118_000 else "#172231"


def _check_hresult(result, operation):
    if result < 0:
        code = result & 0xFFFFFFFF
        raise OSError(None, f"{operation} failed (HRESULT 0x{code:08X})", None, result)


def _method(instance, slot, result, *arguments):
    vtable = ctypes.cast(instance, ctypes.POINTER(ctypes.POINTER(_PTR))).contents
    function = ctypes.WINFUNCTYPE(result, _PTR, *arguments)(vtable[slot])
    return partial(function, instance)


def _release(instance):
    if instance:
        _method(instance, 2, _UINT)()  # IUnknown::Release


class Direct2DAimRenderer:
    """Draw complete frames in physical client pixels on the HWND's owning thread."""

    def __init__(self, hwnd: int, width: int, height: int):
        if sys.platform != "win32":
            raise RuntimeError("Direct2D requires Windows")
        if not hwnd:
            raise OSError("Direct2D requires a window handle")
        self._validate_size(width, height)
        self._factory = _PTR()
        self._write_factory = _PTR()
        self._target = _PTR()
        self._brushes = {}
        self._formats = {}
        try:
            self._create_factories()
            self._create_target(hwnd, width, height)
        except BaseException:
            self.close()
            raise

    def _create_factories(self):
        self._d2d = ctypes.WinDLL("d2d1.dll", winmode=_SYSTEM32)
        self._dwrite = ctypes.WinDLL("dwrite.dll", winmode=_SYSTEM32)
        try:
            create_d2d = self._d2d.D2D1CreateFactory
            create_dwrite = self._dwrite.DWriteCreateFactory
        except AttributeError as error:
            raise RuntimeError("Direct2D and DirectWrite factories are required") from error
        create_d2d.argtypes = (_UINT, ctypes.POINTER(_GUID), _PTR, ctypes.POINTER(_PTR))
        create_d2d.restype = _HRESULT
        create_dwrite.argtypes = (_UINT, ctypes.POINTER(_GUID), ctypes.POINTER(_PTR))
        create_dwrite.restype = _HRESULT
        _check_hresult(
            create_d2d(0, ctypes.byref(_D2D_FACTORY_ID), None, ctypes.byref(self._factory)),
            "D2D1CreateFactory",
        )
        _check_hresult(
            create_dwrite(0, ctypes.byref(_DWRITE_FACTORY_ID), ctypes.byref(self._write_factory)),
            "DWriteCreateFactory",
        )
        # IDWriteFactory::CreateTextFormat
        self._create_format = _method(
            self._write_factory,
            15,
            _HRESULT,
            ctypes.c_wchar_p,
            _PTR,
            _UINT,
            _UINT,
            _UINT,
            _FLOAT,
            ctypes.c_wchar_p,
            ctypes.POINTER(_PTR),
        )

    def _create_target(self, hwnd, width, height):
        # Explicit HARDWARE avoids silently creating a software target. An immediate
        # present keeps frame cadence under the caller's control.
        pixel_format = _PixelFormat(87, 3)  # B8G8R8A8_UNORM, ALPHA_MODE_IGNORE
        properties = _TargetProperties(_D2D_TARGET_HARDWARE, pixel_format, 96.0, 96.0, 0, 0)
        hwnd_properties = _HwndProperties(hwnd, _Size(width, height), _D2D_PRESENT_IMMEDIATELY)
        create_target = _method(
            self._factory,
            14,
            _HRESULT,
            ctypes.POINTER(_TargetProperties),
            ctypes.POINTER(_HwndProperties),
            ctypes.POINTER(_PTR),
        )  # ID2D1Factory::CreateHwndRenderTarget
        _check_hresult(
            create_target(
                ctypes.byref(properties), ctypes.byref(hwnd_properties), ctypes.byref(self._target)
            ),
            "CreateHwndRenderTarget",
        )
        self._size = (width, height)

        # Slots from ID2D1RenderTarget and ID2D1HwndRenderTarget in d2d1.h.
        target = self._target
        self._create_brush = _method(
            target,
            8,
            _HRESULT,
            ctypes.POINTER(_Color),
            _PTR,
            ctypes.POINTER(_PTR),
        )
        self._draw_line = _method(target, 15, None, _Point, _Point, _PTR, _FLOAT, _PTR)
        self._fill_rect = _method(target, 17, None, ctypes.POINTER(_Rect), _PTR)
        self._fill_ellipse = _method(target, 21, None, ctypes.POINTER(_Ellipse), _PTR)
        self._draw_text = _method(
            target,
            27,
            None,
            ctypes.c_wchar_p,
            _UINT,
            _PTR,
            ctypes.POINTER(_Rect),
            _PTR,
            _UINT,
            _UINT,
        )
        _method(target, 34, None, _UINT)(2)  # SetTextAntialiasMode(GRAYSCALE)
        self._clear = _method(target, 47, None, ctypes.POINTER(_Color))
        self._begin_draw = _method(target, 48, None)
        self._end_draw = _method(target, 49, _HRESULT, _PTR, _PTR)
        self._resize = _method(target, 58, _HRESULT, ctypes.POINTER(_Size))

    @staticmethod
    def _validate_size(width, height):
        if not (0 < width <= 0xFFFFFFFF and 0 < height <= 0xFFFFFFFF):
            raise OSError("Direct2D requires a positive client size")

    def _brush(self, color, opacity=1.0):
        key = (color, opacity)
        brush = self._brushes.get(key)
        if brush is None:
            rgb = int(color[1:], 16)
            rgba = _Color((rgb >> 16) / 255, ((rgb >> 8) & 255) / 255, (rgb & 255) / 255, opacity)
            brush = _PTR()
            _check_hresult(
                self._create_brush(ctypes.byref(rgba), None, ctypes.byref(brush)),
                "CreateSolidColorBrush",
            )
            self._brushes[key] = brush
        return brush

    def _text_format(self, size, bold, align):
        key = (size, bold, align)
        text_format = self._formats.get(key)
        if text_format is None:
            alignment = _TEXT_ALIGNMENT[align]
            text_format = _PTR()
            weight = 700 if bold else 400
            _check_hresult(
                self._create_format(
                    "Segoe UI", None, weight, 0, 5, size, "zh-TW", ctypes.byref(text_format)
                ),
                "CreateTextFormat",
            )
            try:
                # IDWriteTextFormat methods, with one layout box per visible Tk line.
                _check_hresult(
                    _method(text_format, 3, _HRESULT, _UINT)(alignment), "SetTextAlignment"
                )
                _check_hresult(
                    _method(text_format, 4, _HRESULT, _UINT)(2), "SetParagraphAlignment"
                )  # CENTER
                _check_hresult(
                    _method(text_format, 5, _HRESULT, _UINT)(1), "SetWordWrapping"
                )  # NO_WRAP
            except BaseException:
                _release(text_format)
                raise
            self._formats[key] = text_format
        return text_format

    def draw(
        self,
        width,
        height,
        *,
        lines=(),
        balls=(),
        reticle=None,
        fragments=(),
        overlays=(),
        labels=(),
    ):
        """Present one frame, raising OSError on a native failure."""
        if not self._target:
            raise OSError("Direct2D renderer is closed")
        self._validate_size(width, height)
        if self._size != (width, height):
            _check_hresult(self._resize(ctypes.byref(_Size(width, height))), "Resize")
            self._size = (width, height)

        # Trim between frames so deferred commands always finish before release.
        for cache, limit in ((self._brushes, 64), (self._formats, 32)):
            while len(cache) > limit:
                _release(cache.pop(next(iter(cache))))

        self._begin_draw()
        try:
            self._clear(ctypes.byref(_BACKGROUND))
            for x0, y0, x1, y1, color in lines:
                # Center one-pixel strokes without snapping the projected endpoints.
                start = _Point(x0 + 0.5, y0 + 0.5)
                end = _Point(x1 + 0.5, y1 + 0.5)
                self._draw_line(start, end, self._brush(color), 1.0, None)
            for x, y, radius, color in balls:
                ellipse = _Ellipse(_Point(x, y), radius, radius)
                self._fill_ellipse(ctypes.byref(ellipse), self._brush(target_outline_color(color)))
                ellipse.radius_x = ellipse.radius_y = radius - min(TARGET_OUTLINE_WIDTH, radius / 2)
                self._fill_ellipse(ctypes.byref(ellipse), self._brush(color))
            if reticle is not None:
                x, y, span, color = reticle
                brush = self._brush(color)
                self._draw_line(_Point(x - span, y), _Point(x + span, y), brush, 2.0, None)
                self._draw_line(_Point(x, y - span), _Point(x, y + span), brush, 2.0, None)
            for x, y, radius, color in fragments:
                rect = _Rect(x - radius, y - radius, x + radius, y + radius)
                self._fill_rect(ctypes.byref(rect), self._brush(color))
            for left, top, right, bottom, color, opacity in overlays:
                rect = _Rect(left, top, right, bottom)
                self._fill_rect(ctypes.byref(rect), self._brush(color, opacity))
            for left, top, right, bottom, text, color, size, bold, align in labels:
                rect = _Rect(left, top, right, bottom)
                length = len(text.encode("utf-16-le")) // 2
                self._draw_text(
                    text,
                    length,
                    self._text_format(size, bold, align),
                    ctypes.byref(rect),
                    self._brush(color),
                    2,
                    0,
                )
        finally:
            result = self._end_draw(None, None)
        _check_hresult(result, "EndDraw")

    def close(self):
        """Release the renderer's native resources before the window is destroyed."""
        for cache in (self._brushes, self._formats):
            for resource in cache.values():
                _release(resource)
            cache.clear()
        for instance in (self._target, self._write_factory, self._factory):
            _release(instance)
            instance.value = None
