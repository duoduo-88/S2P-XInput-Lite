"""Paint a retained BGRA trail in the native Windows Tk Canvas display list."""

import _tkinter
import atexit
import ctypes as ct
import platform
from threading import current_thread, main_thread
from tkinter import Canvas, TclError

from gamepad_trail_photo import _load_photo_api


_PTR, _INT, _DOUBLE = ct.c_void_p, ct.c_int, ct.c_double
_SYSTEM32 = 0x00000800
_ITEM_NAME = b"s2p_trail_bitmap"
_SOURCE_ALPHA = 0x01FF0000  # AC_SRC_OVER, constant alpha 255, AC_SRC_ALPHA.
_NATIVE = None


# The records below follow the Tk 8.6.12 x64 ABI in generic/tk.h and win/tkWinInt.h.
class _TkItem(ct.Structure):
    _fields_ = [
        ("id", _INT), ("next", _PTR), ("inline_tags", _PTR * 3), ("tags", _PTR),
        ("tag_space", _INT), ("tag_count", _INT), ("type", _PTR),
        ("left", _INT), ("top", _INT), ("right", _INT), ("bottom", _INT),
        ("previous", _PTR), ("state", _INT), ("reserved", _PTR), ("redraw_flags", _INT),
    ]


class _TkItemType(ct.Structure):
    _fields_ = [
        ("name", ct.c_char_p), ("size", _INT), ("create", _PTR), ("config_specs", _PTR),
        ("configure", _PTR), ("coords", _PTR), ("delete", _PTR), ("display", _PTR),
        ("always_redraw", _INT), ("point", _PTR), ("area", _PTR), ("postscript", _PTR),
        ("scale", _PTR), ("translate", _PTR), ("index", _PTR), ("cursor", _PTR),
        ("selection", _PTR), ("insert", _PTR), ("delete_chars", _PTR), ("next", _PTR),
        ("reserved1", _PTR), ("reserved2", _INT), ("reserved3", _PTR), ("reserved4", _PTR),
    ]


class _TkConfigSpec(ct.Structure):
    _fields_ = [
        ("type", _INT), ("option", _PTR), ("name", _PTR), ("class_name", _PTR),
        ("default", _PTR), ("offset", _INT), ("flags", _INT), ("custom", _PTR),
    ]


class _TkDCState(ct.Structure):
    _fields_ = [("palette", _PTR), ("background_mode", _INT)]


class _BitmapHeader(ct.Structure):
    _fields_ = [
        ("size", ct.c_uint32), ("width", ct.c_int32), ("height", ct.c_int32),
        ("planes", ct.c_uint16), ("bit_count", ct.c_uint16), ("compression", ct.c_uint32),
        ("image_size", ct.c_uint32), ("x_pixels_per_meter", ct.c_int32),
        ("y_pixels_per_meter", ct.c_int32), ("colors_used", ct.c_uint32),
        ("important_colors", ct.c_uint32),
    ]


def _bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


class _NativeCanvas:
    def __init__(self, tk):
        gdi = ct.WinDLL("gdi32", winmode=_SYSTEM32)
        image = ct.WinDLL("msimg32", winmode=_SYSTEM32)
        self.create_dc = _bind(gdi, "CreateCompatibleDC", _PTR, _PTR)
        self.delete_dc = _bind(gdi, "DeleteDC", _INT, _PTR)
        self.create_bitmap = _bind(
            gdi, "CreateDIBSection", _PTR, _PTR, ct.POINTER(_BitmapHeader),
            ct.c_uint, ct.POINTER(_PTR), _PTR, ct.c_uint32,
        )
        self.select_object = _bind(gdi, "SelectObject", _PTR, _PTR, _PTR)
        self.delete_object = _bind(gdi, "DeleteObject", _INT, _PTR)
        self.flush = _bind(gdi, "GdiFlush", _INT)
        self.blend = _bind(
            image, "AlphaBlend", _INT, _PTR, _INT, _INT, _INT, _INT,
            _PTR, _INT, _INT, _INT, _INT, ct.c_uint32,
        )
        self.get_dc = _bind(tk, "TkWinGetDrawableDC", _PTR, _PTR, _PTR, ct.POINTER(_TkDCState))
        self.release_dc = _bind(tk, "TkWinReleaseDrawableDC", None, _PTR, _PTR, ct.POINTER(_TkDCState))
        self.drawable_coords = _bind(
            tk, "Tk_CanvasDrawableCoords", None, _PTR, _DOUBLE, _DOUBLE,
            ct.POINTER(ct.c_short), ct.POINTER(ct.c_short),
        )
        self.invalidate = _bind(tk, "Tk_CanvasEventuallyRedraw", None, _PTR, _INT, _INT, _INT, _INT)
        create_type = _bind(tk, "Tk_CreateItemType", None, ct.POINTER(_TkItemType))
        self.live = {}
        self.pending = None
        self._config = _TkConfigSpec(21)  # TK_CONFIG_END, an empty option table.
        self._type = _TkItemType(_ITEM_NAME, ct.sizeof(_TkItem), config_specs=ct.addressof(self._config))
        self._callbacks = {
            "create": ct.CFUNCTYPE(_INT, _PTR, _PTR, _PTR, _INT, _PTR)(self._create),
            "configure": ct.CFUNCTYPE(_INT, _PTR, _PTR, _PTR, _INT, _PTR, _INT)(
                lambda interp, canvas, item, argc, args, flags: int(bool(argc))
            ),
            "delete": ct.CFUNCTYPE(None, _PTR, _PTR, _PTR)(self._delete),
            "display": ct.CFUNCTYPE(None, _PTR, _PTR, _PTR, _PTR, _INT, _INT, _INT, _INT)(self._display),
            "point": ct.CFUNCTYPE(_DOUBLE, _PTR, _PTR, _PTR)(lambda *_: 1e10),
            "area": ct.CFUNCTYPE(_INT, _PTR, _PTR, _PTR)(lambda *_: -1),
            "scale": ct.CFUNCTYPE(None, _PTR, _PTR, _DOUBLE, _DOUBLE, _DOUBLE, _DOUBLE)(lambda *_: None),
            "translate": ct.CFUNCTYPE(None, _PTR, _PTR, _DOUBLE, _DOUBLE)(lambda *_: None),
        }
        for name, callback in self._callbacks.items():
            setattr(self._type, name, ct.cast(callback, _PTR))
        # Tk retains the record and callbacks for the process lifetime. Remove
        # live items before Python can tear down their callback trampolines.
        atexit.register(self._close_live)
        create_type(ct.byref(self._type))

    def _create(self, interp, canvas, item, argc, _args):
        surface = self.pending
        if surface is None or surface._interp != interp or argc != 2:
            return 1  # TCL_ERROR
        try:
            record = _TkItem.from_address(item)
            record.left = record.top = 0
            record.right, record.bottom = surface._width, surface._height
            surface._canvas_ptr, surface._item_ptr = canvas, item
            surface.item = record.id
            self.live[item] = surface
            return 0  # TCL_OK
        except BaseException:
            surface.failed = True
            return 1

    def _delete(self, _canvas, item, _display):
        surface = self.live.pop(item, None)
        if surface is not None:
            try:
                surface._release()
            except BaseException:
                surface.failed = True

    def _display(self, canvas, item, display, drawable, x, y, width, height):
        surface = self.live.get(item)
        if surface is not None and not surface.failed and drawable:
            try:
                surface._draw(canvas, display, drawable, x, y, width, height)
            except BaseException:
                # A callback must return to Tk even when GDI fails. The plot
                # will see this flag and switch to its PhotoImage backend.
                surface.failed = True

    def _close_live(self):
        for surface in tuple(self.live.values()):
            surface.close()


def _load_native(canvas):
    global _NATIVE
    if current_thread() is not main_thread():
        raise RuntimeError("Trail canvas requires the Tk main thread")
    if (
        not isinstance(canvas, Canvas)
        or not isinstance(canvas.tk, _tkinter.TkappType)
        or ct.sizeof(_PTR) != 8
        or platform.machine().lower() not in ("amd64", "x86_64")
    ):
        raise RuntimeError("Trail canvas requires Windows x64 Tk 8.6.12")
    tk = _load_photo_api()
    # eval checks Tk's owning thread before we use its native interpreter.
    if tk is None or canvas.tk.eval("package provide Tk") != "8.6.12":
        raise RuntimeError("Trail canvas requires Windows x64 Tk 8.6.12")
    if _NATIVE is None:
        try:
            _NATIVE = _NativeCanvas(tk)
        except AttributeError as error:
            raise OSError("Native Tk canvas entry points are unavailable") from error
    return _NATIVE


class TrailCanvasSurface:
    """A fixed-size canvas item with a writable top-down premultiplied BGRA buffer."""

    def __init__(self, canvas, width, height):
        if not (0 < width <= 32767 and 0 < height <= 32767):
            raise ValueError("Invalid trail canvas dimensions")
        self._native = native = _load_native(canvas)
        if native.pending is not None:
            raise RuntimeError("Trail canvas item creation is already active")
        self._canvas = canvas
        self._width, self._height = width, height
        self._pitch = width * 4
        self._interp = canvas.tk.interpaddr()
        self._canvas_ptr = self._item_ptr = None
        self._dc = self._bitmap = self._previous_bitmap = None
        self._bits = _PTR()
        self.item = None
        self.failed = False
        # Keep Python-owned storage so a caller retaining the array after the
        # item closes never holds a dangling view of a freed GDI bitmap.
        self._pixels = (ct.c_uint32 * (width * height))()
        self._pixels_address = ct.addressof(self._pixels)
        try:
            self._dc = native.create_dc(None)
            if not self._dc:
                raise OSError("Could not create trail canvas device context")
            header = _BitmapHeader(ct.sizeof(_BitmapHeader), width, -height, 1, 32)
            self._bitmap = native.create_bitmap(self._dc, ct.byref(header), 0, ct.byref(self._bits), None, 0)
            if not self._bitmap or not self._bits:
                raise OSError("Could not allocate trail canvas bitmap")
            previous = native.select_object(self._dc, self._bitmap)
            if not previous or previous == _PTR(-1).value:
                raise OSError("Could not select trail canvas bitmap")
            self._previous_bitmap = previous
            native.pending = self
            try:
                canvas.tk.call(str(canvas), "create", _ITEM_NAME.decode("ascii"), 0, 0)
                if self._item_ptr is None or self.failed:
                    raise RuntimeError("Tk did not create the trail canvas item")
            finally:
                native.pending = None
        except BaseException:
            self.close()
            raise

    @property
    def pixels(self):
        return self._pixels

    def invalidate(self, left, top, right, bottom):
        if current_thread() is not main_thread():
            raise RuntimeError("Trail canvas requires the Tk main thread")
        left, top = max(0, left), max(0, top)
        right, bottom = min(self._width, right), min(self._height, bottom)
        if not self.failed and left < right and top < bottom:
            self._native.invalidate(self._canvas_ptr, left, top, right, bottom)

    def _draw(self, canvas, display, drawable, x, y, width, height):
        left, top = max(0, x), max(0, y)
        right, bottom = min(self._width, x + width), min(self._height, y + height)
        if left >= right or top >= bottom:
            return
        dx, dy = ct.c_short(), ct.c_short()
        native = self._native
        native.drawable_coords(canvas, left, top, ct.byref(dx), ct.byref(dy))
        state = _TkDCState()
        destination = native.get_dc(display, drawable, ct.byref(state))
        if not destination:
            raise OSError("Could not access Tk canvas device context")
        try:
            # The previous blend was flushed before we write into its DIB.
            offset = top * self._pitch
            ct.memmove(self._bits.value + offset, self._pixels_address + offset, (bottom - top) * self._pitch)
            width, height = right - left, bottom - top
            blended = native.blend(
                destination, dx.value, dy.value, width, height,
                self._dc, left, top, width, height, _SOURCE_ALPHA,
            )
            if not native.flush() or not blended:
                raise OSError("Could not blend the trail into Tk canvas")
        finally:
            native.release_dc(drawable, destination, ct.byref(state))

    def close(self):
        if current_thread() is not main_thread():
            raise RuntimeError("Trail canvas requires the Tk main thread")
        item_ptr = self._item_ptr
        try:
            if item_ptr is not None:
                self._canvas.delete(self.item)
        except TclError:
            pass  # The widget command may already have been destroyed.
        finally:
            self._native.live.pop(item_ptr, None)
            self._release()

    def _release(self):
        self.failed = True
        self._canvas_ptr = self._item_ptr = None
        self._canvas = None
        native = self._native
        if self._previous_bitmap:
            native.select_object(self._dc, self._previous_bitmap)
            self._previous_bitmap = None
        if self._bitmap:
            native.delete_object(self._bitmap)
            self._bitmap = None
        if self._dc:
            native.delete_dc(self._dc)
            self._dc = None
