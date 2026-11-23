import asyncio
import json
import signal
import threading
import unittest
from unittest.mock import Mock, patch

from tornado.ioloop import IOLoop
from tornado.testing import AsyncTestCase, gen_test

import server
from server import Session


class ResizeTests(unittest.TestCase):
    def setUp(self):
        self.session = Session.__new__(Session)
        self.session.id = "test_redraw"
        self.session.closed = False
        self.session.process = Mock()
        self.session.process.fd = 42
        self.session.process.getwinsize.return_value = (24, 80)

    @patch("server.os.killpg")
    @patch("server.os.tcgetpgrp", return_value=1234)
    def test_explicit_redraw_signals_foreground_group_without_changing_size(self, foreground, killpg):
        self.session.resize(24, 80, redraw=True)
        self.session.process.setwinsize.assert_called_once_with(24, 80)
        foreground.assert_called_once_with(42)
        killpg.assert_called_once_with(1234, signal.SIGWINCH)

    @patch("server.os.killpg")
    def test_changed_dimensions_rely_on_kernel_resize_notification(self, killpg):
        self.session.resize(33, 111, redraw=True)
        self.session.process.setwinsize.assert_called_once_with(33, 111)
        killpg.assert_not_called()

    @patch("server.os.killpg")
    def test_ordinary_resize_does_not_force_an_additional_redraw(self, killpg):
        self.session.resize(24, 80)
        self.session.process.getwinsize.assert_not_called()
        killpg.assert_not_called()

    @patch("server.os.killpg")
    @patch("server.os.tcgetpgrp", return_value=0)
    def test_missing_foreground_group_never_signals_the_server_group(self, foreground, killpg):
        self.session.resize(24, 80, redraw=True)
        foreground.assert_called_once_with(42)
        killpg.assert_not_called()

    @patch("server.os.killpg")
    def test_closed_session_is_not_signalled(self, killpg):
        self.session.closed = True
        self.session.resize(24, 80, redraw=True)
        self.session.process.setwinsize.assert_not_called()
        killpg.assert_not_called()


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.loop = Mock()
        with patch("server.PtyProcess.spawn"), patch("server.os.set_blocking"), patch("server.threading.Thread.start"):
            self.session = Session("queue-test", self.loop)

    def test_invalid_input_never_reaches_the_writer(self):
        for value in (None, {}, [], 42, b"bytes"):
            with self.assertRaises(ValueError):
                self.session.write(value)
        self.assertTrue(self.session._write_queue.empty())

    def test_input_budget_is_bytes_not_message_count_and_reports_rejection(self):
        with patch.object(server, "MAX_INPUT_PENDING_BYTES", 6):
            self.assertTrue(self.session.write("中文"))
            self.assertFalse(self.session.write("x"))
            self.assertEqual(self.session._input_pending_bytes, 6)
            self.assertEqual(self.session._write_queue.qsize(), 1)
        self.session.process.isalive.assert_not_called()

    def test_oversized_message_is_rejected_before_enqueue(self):
        with patch.object(server, "MAX_INPUT_MESSAGE_BYTES", 4):
            with self.assertRaises(ValueError):
                self.session.write("中文")
        self.assertTrue(self.session._write_queue.empty())

    def test_output_reader_waits_for_callback_budget(self):
        self.session.process.fd = 42
        with patch.object(server, "MAX_PTY_PENDING_BYTES", 4), \
                patch("server.select.select", return_value=([42], [], [])), \
                patch("server.os.read", return_value=b"data") as read:
            worker = threading.Thread(target=self.session._reader)
            worker.start()
            try:
                for _ in range(100):
                    if self.loop.add_callback.called:
                        break
                    threading.Event().wait(0.005)
                self.assertEqual(read.call_count, 1)
                self.assertEqual(self.session._output_pending_bytes, 4)
            finally:
                with self.session._output_condition:
                    self.session.closed = True
                    self.session._output_condition.notify_all()
                worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.session.process.isalive.assert_not_called()

    def test_writer_handles_partial_writes_and_releases_byte_budget(self):
        self.session.process.fd = 42
        self.session.write("abcd")
        self.session._write_queue.put_nowait(None)
        with patch("server.select.select", return_value=([], [42], [])), \
                patch("server.os.write", side_effect=[2, 2]) as write:
            self.session._writer()
        self.assertEqual(bytes(write.call_args_list[0].args[1]), b"abcd")
        self.assertEqual(bytes(write.call_args_list[1].args[1]), b"cd")
        self.assertEqual(self.session._input_pending_bytes, 0)

    def test_output_callback_releases_budget_even_after_session_closed(self):
        self.session._output_pending_bytes = 4
        self.session.closed = True
        self.session._on_pty_data(b"data")
        self.assertEqual(self.session._output_pending_bytes, 0)
        self.assertEqual(self.session.buffer_bytes, 0)

    def test_writer_failure_requests_close_and_releases_input_budget(self):
        self.session.process.fd = 42
        self.session.write("abcd")
        with patch("server.select.select", return_value=([], [42], [])), \
                patch("server.os.write", side_effect=OSError("PTY disconnected")), \
                self.assertLogs(level="ERROR"):
            self.session._writer()
        self.assertEqual(self.session._input_pending_bytes, 0)
        self.loop.add_callback.assert_called_once_with(self.session.close)

    def test_replay_boundaries_surround_only_the_history(self):
        messages = []
        client = Mock()
        client._send.side_effect = lambda data, binary: messages.append(json.loads(data))
        client.safe_write.side_effect = messages.append
        self.session._append_output(b"\x1b[c")
        self.session.add_client(client)
        self.assertEqual(messages, [
            {"type": "replay_begin"}, b"\x1b[c", {"type": "replay_end"},
        ])


class LifecycleTests(AsyncTestCase):
    @gen_test
    async def test_close_is_nonblocking_idempotent_and_reaps_off_loop(self):
        entered = threading.Event()
        release = threading.Event()
        loop_thread = threading.get_ident()
        cleanup_threads = []

        def cleanup(force=True):
            cleanup_threads.append(threading.get_ident())
            entered.set()
            release.wait(timeout=3)

        with patch("server.PtyProcess.spawn") as spawn, patch("server.os.set_blocking"), \
                patch("server.threading.Thread.start"):
            session = Session("lifecycle-test", IOLoop.current())
        session._reader_thread = Mock()
        session._writer_thread = Mock()
        spawn.return_value.close.side_effect = cleanup
        try:
            future = session.close()
            self.assertIs(session.close(), future)
            await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
            self.assertFalse(future.done())
            await asyncio.sleep(0)
        finally:
            release.set()
            await asyncio.wait_for(future, 3)
        self.assertNotEqual(cleanup_threads, [loop_thread])
        spawn.return_value.close.assert_called_once_with(force=True)
        spawn.return_value.isalive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
