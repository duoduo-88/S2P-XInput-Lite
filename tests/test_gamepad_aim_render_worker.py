import threading
import time
import unittest
from types import SimpleNamespace

from gamepad_aim_render_worker import AimRenderFrame, AimRenderWorker


class AimRenderWorkerTests(unittest.TestCase):
    def make_worker(self, factory, project_room=None):
        worker = AimRenderWorker(factory, 1, (640, 360), project_room=project_room)

        def stop():
            worker.close()
            self.assertTrue(worker.stopped.wait(2))

        self.addCleanup(stop)
        return worker

    @staticmethod
    def frame(number, room_pose=None):
        return AimRenderFrame((640, 360), room_pose, ((number, 0, 1, "#ffffff"),))

    def test_keeps_only_latest_pending_scene_and_counts_only_completed_draws(self):
        entered = threading.Event()
        release = threading.Event()
        drawn = threading.Event()
        calls = []
        threads = []
        projected = []
        lines = ((0, 0, 100, 100, "#ffffff"),)
        pose = (640, 360, 0.1, 0.2)

        def project_room(*pose_args):
            projected.append(pose_args)
            return lines

        def factory(*_args):
            threads.append(threading.get_ident())

            def draw(_width, _height, **parts):
                threads.append(threading.get_ident())
                calls.append(parts)
                if len(calls) == 1:
                    entered.set()
                    if not release.wait(2):
                        raise RuntimeError("test did not unblock the renderer")
                else:
                    drawn.set()

            return SimpleNamespace(draw=draw, close=lambda: threads.append(threading.get_ident()))

        worker = self.make_worker(factory, project_room)
        self.addCleanup(release.set)
        self.assertTrue(worker.submit(self.frame(1, pose)))
        self.assertTrue(entered.wait(2))
        self.assertTrue(worker.submit(self.frame(2, pose)))
        self.assertTrue(worker.submit(AimRenderFrame(
            (640, 360), pose, ((3, 0, 1, "#ffffff"),), ball_outlines=False,
        )))
        self.assertEqual(worker.drain(), ((), None))
        released_at = time.perf_counter()
        release.set()
        self.assertTrue(drawn.wait(2))
        worker.close()
        self.assertTrue(worker.stopped.wait(2))

        completed, error = worker.drain()
        self.assertIsNone(error)
        self.assertEqual(len(completed), 2)
        self.assertLessEqual(released_at, completed[0])
        self.assertLessEqual(completed[0], completed[1])
        self.assertEqual([parts["balls"][0][0] for parts in calls], [1, 3])
        self.assertEqual([parts["ball_outlines"] for parts in calls], [True, False])
        self.assertEqual(projected, [pose])
        self.assertIs(calls[0]["lines"], calls[1]["lines"])
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())

    def test_close_discards_pending_scene_without_waiting_for_inflight_draw(self):
        entered = threading.Event()
        release = threading.Event()
        drawn = []
        closed = threading.Event()

        def draw(_width, _height, **parts):
            drawn.append(parts["balls"][0][0])
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test did not unblock the renderer")

        worker = self.make_worker(lambda *_: SimpleNamespace(draw=draw, close=closed.set))
        self.addCleanup(release.set)
        worker.submit(self.frame(1))
        self.assertTrue(entered.wait(2))
        worker.submit(self.frame(2))
        worker.close()
        self.assertFalse(worker.stopped.is_set())
        self.assertFalse(worker.submit(self.frame(3)))
        release.set()
        self.assertTrue(worker.stopped.wait(2))
        self.assertEqual(drawn, [1])
        self.assertTrue(closed.is_set())

    def test_reset_invalidates_a_scene_already_taken_by_the_worker(self):
        for stage in ("projection", "draw"):
            with self.subTest(stage=stage):
                entered, release, fresh, finish = (threading.Event() for _ in range(4))
                drawn = []

                def wait_at_reset():
                    entered.set()
                    if not release.wait(2):
                        raise RuntimeError("test did not unblock the old scene")

                def project_room(*_pose):
                    if stage == "projection":
                        wait_at_reset()
                    return ()

                def draw(_width, _height, **parts):
                    number = parts["balls"][0][0]
                    drawn.append(number)
                    if number == 1 and stage == "draw":
                        wait_at_reset()
                    if number == 2:
                        fresh.set()
                        if not finish.wait(2):
                            raise RuntimeError("test did not unblock the new scene")

                worker = self.make_worker(
                    lambda *_: SimpleNamespace(draw=draw, close=lambda: None),
                    project_room,
                )
                try:
                    worker.submit(self.frame(1, (640, 360, 0.1, 0.2)))
                    self.assertTrue(entered.wait(2))
                    worker.discard_pending()
                    worker.submit(self.frame(2))
                    release.set()
                    self.assertTrue(fresh.wait(2))
                    self.assertEqual(drawn, [2] if stage == "projection" else [1, 2])
                    self.assertEqual(worker.drain(), ((), None))
                finally:
                    release.set()
                    finish.set()
                    worker.close()
                    self.assertTrue(worker.stopped.wait(2))

    def test_init_and_draw_failures_are_observable_and_release_the_renderer(self):
        for initialization in (True, False):
            with self.subTest(initialization=initialization):
                released = threading.Event()

                def fail(*_args, **_parts):
                    raise OSError("GPU unavailable")

                factory = fail if initialization else (
                    lambda *_: SimpleNamespace(draw=fail, close=released.set)
                )
                worker = self.make_worker(factory)
                worker.submit(self.frame(1))
                self.assertTrue(worker.stopped.wait(2))
                self.assertEqual(worker.drain(), ((), "GPU unavailable"))
                self.assertFalse(worker.submit(self.frame(2)))
                self.assertEqual(released.is_set(), not initialization)
