"""Rasterize original stick reports away from the Tk presentation thread."""

from __future__ import annotations

import ctypes
import math
import struct
import threading
from dataclasses import dataclass
from functools import lru_cache
from itertools import chain


MAX_PENDING_SAMPLES = 65_536
_PACK_BATCH_SIZE = 256
_SAMPLE_BYTES = struct.calcsize("=dddQ")


@lru_cache(maxsize=128)
def _sample_packer(count):
    return struct.Struct("=" + "dddQ" * count)


class TrailStyle(ctypes.Structure):
    _fields_ = [
        ("percent", ctypes.c_int),
        ("length", ctypes.c_double),
        ("center", ctypes.c_double),
        ("radius", ctypes.c_double),
        ("zoom", ctypes.c_double),
        ("pan_x", ctypes.c_double),
        ("pan_y", ctypes.c_double),
    ]


class _TrailUpdate(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int), ("top", ctypes.c_int),
        ("right", ctypes.c_int), ("bottom", ctypes.c_int),
        ("next_expiry", ctypes.c_double),
    ]


class NativeTrailRaster:
    """A private C++ raster and expiry heap owned by one worker thread."""

    def __init__(self, library, width, height, color):
        self._create = library.s2p_trail_create
        self._create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint32]
        self._create.restype = ctypes.c_void_p
        self._destroy = library.s2p_trail_destroy
        self._destroy.argtypes = [ctypes.c_void_p]
        self._destroy.restype = None
        self._pixels = library.s2p_trail_pixels
        self._pixels.argtypes = [ctypes.c_void_p]
        self._pixels.restype = ctypes.c_void_p
        self._update = library.s2p_trail_update
        self._update.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.POINTER(TrailStyle), ctypes.c_double, ctypes.c_int,
            ctypes.POINTER(_TrailUpdate),
        ]
        self._update.restype = ctypes.c_int
        self._handle = self._create(width, height, color)
        if not self._handle:
            raise OSError("Could not create native trajectory raster")
        self._stride = width * ctypes.sizeof(ctypes.c_uint32)
        self._sample_buffer = None
        self._sample_capacity = 0

    def _pack_samples(self, samples):
        count = len(samples)
        if not count:
            return None
        if count > self._sample_capacity:
            self._sample_capacity = max(count, _PACK_BATCH_SIZE, self._sample_capacity * 2)
            self._sample_buffer = ctypes.create_string_buffer(self._sample_capacity * _SAMPLE_BYTES)
        for start in range(0, count, _PACK_BATCH_SIZE):
            batch = samples[start:start + _PACK_BATCH_SIZE]
            _sample_packer(len(batch)).pack_into(
                self._sample_buffer, start * _SAMPLE_BYTES, *chain.from_iterable(batch),
            )
        return self._sample_buffer

    def update(self, samples, style, now, reset):
        data = self._pack_samples(samples)
        result = _TrailUpdate()
        if not self._update(self._handle, data, len(samples), ctypes.byref(style), now, reset, ctypes.byref(result)):
            raise OSError("Could not rasterize trajectory reports")
        bounds = (
            (result.left, result.top, result.right, result.bottom)
            if result.left < result.right and result.top < result.bottom else None
        )
        return bounds, result.next_expiry

    def read_rows(self, bounds):
        _, top, _, bottom = bounds
        return ctypes.string_at(self._pixels(self._handle) + top * self._stride, (bottom - top) * self._stride)

    def close(self):
        if self._handle:
            self._destroy(self._handle)
            self._handle = None


@dataclass(frozen=True)
class TrailFrame:
    bounds: tuple[int, int, int, int]
    rows: bytes


@dataclass
class _TrailJob:
    generation: int
    style: TrailStyle
    now: float
    reset: bool
    samples: list


class TrailRasterWorker:
    """Coalesce display requests while processing every retained input report."""

    @classmethod
    def open(cls, width, height, colors):
        from gamepad_render_native import load_render_library

        library = load_render_library()
        if library is None or not hasattr(library, "s2p_trail_update"):
            return None
        return cls(width, height, colors, raster_factory=lambda w, h, color: NativeTrailRaster(library, w, h, color))

    def __init__(self, width, height, colors, *, raster_factory):
        self._width = width
        self._height = height
        self._colors = colors
        self._raster_factory = raster_factory
        self._condition = threading.Condition()
        self._pending = {}
        self._requests = {}
        self._frames = {}
        self._next_expiry = {}
        self._serial = 0
        self._closed = False
        self._failure = None
        self._thread = None

    def submit(self, side, key, style, samples, now, *, full_trail=()):
        with self._condition:
            if self._failure is not None:
                raise RuntimeError("Trajectory worker failed") from self._failure
            if self._closed:
                return
            request = self._requests.get(side)
            reset = request is None or request[0] != key
            if reset:
                self._serial += 1
                self._requests[side] = key, self._serial
                self._frames.pop(side, None)
                self._pending.pop(side, None)
                self._next_expiry.pop(side, None)
            pending = self._pending.get(side)
            if pending is not None:
                pending.now = now
                if len(pending.samples) + len(samples) > MAX_PENDING_SAMPLES:
                    # Rebuild from the authoritative history if presentation
                    # falls behind instead of dropping unseen valid samples.
                    pending.samples = list(full_trail)
                    pending.reset = True
                else:
                    pending.samples.extend(samples)
                return
            if not reset and not samples and now < self._next_expiry.get(side, math.inf):
                return
            generation = self._requests[side][1]
            self._pending[side] = _TrailJob(generation, style, now, reset, list(samples))
            if self._thread is None:
                thread = threading.Thread(target=self._run, name="GamepadTrailRaster", daemon=True)
                thread.start()
                self._thread = thread
            self._condition.notify()

    def take(self, side):
        with self._condition:
            return self._frames.pop(side, None)

    def discard(self, side=None):
        with self._condition:
            for channel in tuple(self._requests) if side is None else (side,):
                self._requests.pop(channel, None)
                self._pending.pop(channel, None)
                self._frames.pop(channel, None)
                self._next_expiry.pop(channel, None)

    def close(self):
        with self._condition:
            self._closed = True
            self._pending.clear()
            self._requests.clear()
            self._frames.clear()
            self._condition.notify()
        if self._thread is not None:
            self._thread.join()

    def _run(self):
        rasters = {}
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending)
                    if self._closed:
                        return
                    side = next(iter(self._pending))
                    job = self._pending.pop(side)
                    self._next_expiry.pop(side, None)
                raster = rasters.get(side)
                if raster is None:
                    raster = self._raster_factory(self._width, self._height, self._colors[side])
                    rasters[side] = raster
                bounds, next_expiry = raster.update(job.samples, job.style, job.now, job.reset)
                with self._condition:
                    request = self._requests.get(side)
                    if request is None or request[1] != job.generation:
                        continue
                    self._next_expiry[side] = next_expiry
                    if bounds is not None:
                        previous = self._frames.get(side)
                        if previous is not None:
                            a, b, c, d = previous.bounds
                            x, y, right, bottom = bounds
                            bounds = min(a, x), min(b, y), max(c, right), max(d, bottom)
                        self._frames[side] = TrailFrame(bounds, raster.read_rows(bounds))
        except Exception as error:
            with self._condition:
                self._failure = error
                self._frames.clear()
                self._pending.clear()
        finally:
            for raster in rasters.values():
                raster.close()
