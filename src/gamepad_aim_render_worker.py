"""Latest-scene rendering with thread-owned Direct2D resources."""

from dataclasses import dataclass
from threading import Condition, Event, Thread
from time import perf_counter


@dataclass(frozen=True, slots=True)
class AimRenderFrame:
    size: tuple[int, int]
    room_pose: tuple[int, int, float, float] | None
    balls: tuple
    reticle: tuple | None = None
    fragments: tuple = ()
    overlays: tuple = ()
    labels: tuple = ()
    ball_outlines: bool = True


class AimRenderWorker:
    """Take immutable scenes from Tk without accessing its interpreter."""

    def __init__(self, factory, hwnd, size, *, project_room=None):
        self._condition = Condition()
        self._pending = None
        self._completed = []
        self._closed = False
        self._error = None
        self.stopped = Event()
        Thread(
            target=self._run, args=(factory, hwnd, size, project_room),
            name="AimRender", daemon=True,
        ).start()

    def submit(self, frame):
        with self._condition:
            if self._closed:
                return False
            self._pending = frame
            self._condition.notify()
            return True

    def drain(self):
        with self._condition:
            completed = tuple(self._completed)
            self._completed.clear()
            return completed, self._error

    def discard_pending(self):
        with self._condition:
            self._pending = None

    def close(self):
        # DXGI may send a synchronous window message from the rendering
        # thread. Tk must stay free to dispatch it, including during shutdown.
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()

    def _fail(self, error):
        with self._condition:
            if self._error is None:
                self._error = str(error) or type(error).__name__
            self._closed = True
            self._pending = None

    def _run(self, factory, hwnd, size, project_room):
        renderer = None
        try:
            if self._closed:
                return
            renderer = factory(hwnd, *size)
            pose, lines = None, ()
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._pending is not None)
                    if self._closed:
                        return
                    frame, self._pending = self._pending, None
                if frame.room_pose != pose:
                    pose = frame.room_pose
                    if pose is None:
                        lines = ()
                    else:
                        if project_room is None:
                            from gamepad_aim_projection import project_room
                        lines = project_room(*pose)
                if self._closed:
                    return
                renderer.draw(
                    *frame.size, lines=lines, balls=frame.balls, reticle=frame.reticle,
                    fragments=frame.fragments, overlays=frame.overlays, labels=frame.labels,
                    ball_outlines=frame.ball_outlines,
                )
                with self._condition:
                    self._completed.append(perf_counter())
        except Exception as error:
            self._fail(error)
        finally:
            try:
                if renderer is not None:
                    renderer.close()
            except Exception as error:
                self._fail(error)
            finally:
                self.stopped.set()
