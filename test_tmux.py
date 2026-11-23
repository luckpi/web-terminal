import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tornado.testing import AsyncHTTPTestCase, gen_test
from tornado.websocket import websocket_connect

import server
from tmux_backend import TmuxBackend, TmuxError


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
class TmuxTests(AsyncHTTPTestCase):
    def setUp(self):
        self.native_process = None
        self.directory = tempfile.TemporaryDirectory(prefix="web-terminal-tmux-test-")
        self.backend = TmuxBackend(str(Path(self.directory.name) / "socket"))
        self.process = subprocess.Popen(
            [self.backend.executable, "-D", "-S", self.backend.socket_path, "-f",
             str(Path(__file__).parent / ".devin/tmux.conf")],
            env={k: v for k, v in os.environ.items() if k not in ("TOKEN", "TMUX", "TMUX_PANE")},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for _ in range(100):
            if self.backend.available:
                break
            time.sleep(0.02)
        self.assertTrue(self.backend.available)
        self.backend_patch = patch.object(server, "tmux_backend", self.backend)
        self.backend_patch.start()
        self.native_backend = TmuxBackend(
            str(Path(self.directory.name) / "native"), source="default", managed=False,
        )
        self.native_patch = patch.object(server, "native_tmux_backend", self.native_backend)
        self.native_patch.start()
        self.state_patches = [
            patch.object(server, "_session_lock", asyncio.Lock()),
            patch.object(server, "sessions", {}),
            patch.object(server, "cleanup_futures", set()),
        ]
        for state_patch in self.state_patches:
            state_patch.start()
        super().setUp()

    def get_app(self):
        return server.make_app()

    def tearDown(self):
        for session in list(server.sessions.values()):
            session.close()
        async def finish_cleanup():
            if server.cleanup_futures:
                await asyncio.gather(*list(server.cleanup_futures))
        self.io_loop.run_sync(finish_cleanup, timeout=10)
        super().tearDown()
        for state_patch in reversed(self.state_patches):
            state_patch.stop()
        self.backend_patch.stop()
        self.native_patch.stop()
        self.process.terminate()
        self.process.communicate(timeout=5)
        if self.native_process:
            self.native_process.terminate()
            self.native_process.communicate(timeout=5)
        self.directory.cleanup()

    def start_native(self):
        self.native_process = subprocess.Popen(
            [self.native_backend.executable, "-D", "-S", self.native_backend.socket_path,
             "-f", str(Path(__file__).parent / ".devin/tmux.conf")],
            env={k: v for k, v in os.environ.items() if k not in ("TOKEN", "TMUX", "TMUX_PANE")},
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        for _ in range(100):
            if self.native_backend.available:
                break
            time.sleep(0.02)
        result = self.native_backend.run("new-session", "-d", "-s", "server_box", "-c", "/tmp", "/bin/bash")
        self.assertEqual(result.returncode, 0)

    async def connect(self, sid, backend="tmux"):
        return await websocket_connect(self.get_url(
            f"/ws?session={sid}&backend={backend}&cols=80&rows=24"
        ).replace("http:", "ws:", 1))

    async def output_until(self, ws, marker):
        output = b""
        async def read():
            nonlocal output
            while marker not in output:
                message = await ws.read_message()
                if message is None:
                    raise AssertionError("socket closed before expected output")
                if isinstance(message, bytes):
                    output += message
        await asyncio.wait_for(read(), timeout=8)
        return output

    @gen_test
    async def test_tmux_survives_wrapper_shutdown_and_is_rediscovered(self):
        ws = await self.connect("persist")
        await ws.write_message(json.dumps({
            "type": "input", "data": "export WT_PERSIST=survived; printf 'READY\\n'\n",
        }))
        await self.output_until(ws, b"READY")
        await asyncio.sleep(0.15)
        server.sessions["persist"].close()
        ws.close()
        listing = await server.session_listing()
        self.assertEqual([(item["id"], item["backend"]) for item in listing], [("persist", "tmux")])
        ws = await self.connect("persist")
        await ws.write_message(json.dumps({
            "type": "input", "data": "printf 'AFTER:%s\\n' \"$WT_PERSIST\"\n",
        }))
        await self.output_until(ws, b"AFTER:survived")
        response = await self.http_client.fetch(
            self.get_url("/api/sessions/persist?backend=tmux"), method="DELETE",
        )
        self.assertEqual(response.code, 204)
        self.assertEqual(self.backend.list_sessions(), [])
        ws.close()

    @gen_test
    async def test_detached_tmux_can_be_deleted_without_a_web_wrapper(self):
        await asyncio.to_thread(self.backend.create, "detached", "/bin/bash", "/tmp", 24, 80, "truecolor")
        response = await self.http_client.fetch(self.get_url("/api/sessions"))
        self.assertEqual(json.loads(response.body)[0]["backend"], "tmux")
        wrong_backend = await self.http_client.fetch(
            self.get_url("/api/sessions/detached?backend=pty"), method="DELETE", raise_error=False,
        )
        self.assertEqual(wrong_backend.code, 404)
        self.assertEqual(len(self.backend.list_sessions()), 1)
        response = await self.http_client.fetch(
            self.get_url("/api/sessions/detached?backend=tmux"), method="DELETE",
        )
        self.assertEqual(response.code, 204)
        self.assertEqual(self.backend.list_sessions(), [])

    @gen_test
    async def test_backend_collision_does_not_attach_or_replace_the_session(self):
        ws = await self.connect("shared", "pty")
        other = await self.connect("shared")
        message = json.loads(await other.read_message())
        self.assertEqual(message["type"], "error")
        self.assertEqual(server.sessions["shared"].backend, "pty")
        response = await self.http_client.fetch(
            self.get_url("/api/sessions/shared?backend=tmux"), method="DELETE", raise_error=False,
        )
        self.assertEqual(response.code, 404)
        self.assertIn("shared", server.sessions)
        ws.close()
        other.close()

    @gen_test
    async def test_detached_tmux_counts_towards_the_session_limit(self):
        await asyncio.to_thread(self.backend.create, "limited", "/bin/bash", "/tmp", 24, 80, "")
        with patch.object(server, "MAX_SESSIONS", 1):
            ws = await self.connect("extra", "pty")
            message = json.loads(await ws.read_message())
            self.assertEqual(message["message"], "max sessions reached")
            ws.close()
            existing = await self.connect("limited")
            await asyncio.wait_for(existing.read_message(), 2)
            self.assertEqual(server.sessions["limited"].backend, "tmux")
            existing.close()

    @gen_test
    async def test_backend_availability_and_authentication(self):
        response = await self.http_client.fetch(self.get_url("/api/backends"))
        self.assertEqual(json.loads(response.body), {"pty": True, "tmux": True})
        with patch.object(server, "TOKEN", "test-password"):
            response = await self.http_client.fetch(self.get_url("/api/backends"), raise_error=False)
            self.assertEqual(response.code, 403)

    @gen_test
    async def test_unavailable_tmux_does_not_autostart_a_server(self):
        with patch.object(self.backend, "socket_path", str(Path(self.directory.name) / "missing")):
            ws = await self.connect("unavailable")
            message = json.loads(await ws.read_message())
            self.assertEqual(message["type"], "error")
            self.assertIn("unavailable", message["message"])
            self.assertNotIn("unavailable", server.sessions)
            self.assertFalse(Path(self.backend.socket_path).exists())
            ws.close()

    def test_commands_never_autostart_and_use_exact_session_targets(self):
        command = self.backend.attach_command("safe-name")
        self.assertIn("-N", command)
        self.assertEqual(command[-1], "=wt_safe-name")
        with self.assertRaises(ValueError):
            self.backend.attach_command("bad; kill-server")
        with self.assertRaises(TmuxError):
            with patch.object(self.backend, "executable", None):
                self.backend.command("list-sessions")

    @gen_test
    async def test_native_sessions_are_named_attachable_and_close_only_detaches(self):
        await asyncio.to_thread(self.start_native)
        listing = await server.session_listing()
        self.assertEqual(len(listing), 1)
        item = listing[0]
        self.assertEqual(item["name"], "server_box")
        self.assertFalse(item["managed"])
        self.assertEqual(item["source"], "default")
        sid = item["id"]
        ws = await self.connect(sid)
        await ws.write_message(json.dumps({
            "type": "input", "data": "export WT_NATIVE=kept; printf 'BEFORE:%s\\n' \"$WT_NATIVE\"\n",
        }))
        await self.output_until(ws, b"BEFORE:kept")
        response = await self.http_client.fetch(
            self.get_url("/api/sessions/" + sid + "?backend=tmux"), method="DELETE",
        )
        self.assertEqual(response.code, 204)
        self.assertEqual(len(self.native_backend.list_sessions()), 1)
        self.assertNotIn(sid, server.sessions)
        ws.close()
        ws = await self.connect(sid)
        await ws.write_message(json.dumps({
            "type": "input", "data": "printf 'AFTER:%s\\n' \"$WT_NATIVE\"\n",
        }))
        await self.output_until(ws, b"AFTER:kept")
        ws.close()

    @gen_test
    async def test_native_rename_keeps_stable_identity_and_exact_attachment(self):
        await asyncio.to_thread(self.start_native)
        before = (await server.session_listing())[0]
        self.native_backend.run("rename-session", "-t", before["tmux_id"], "开发终端")
        after = (await server.session_listing())[0]
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["name"], "开发终端")
        ws = await self.connect(after["id"])
        await ws.write_message(json.dumps({"type": "input", "data": "printf 'NATIVE_READY\\n'\n"}))
        await self.output_until(ws, b"NATIVE_READY")
        self.assertEqual(server.sessions[after["id"]].name, "开发终端")
        ws.close()

    @gen_test
    async def test_managed_rename_preserves_web_identity_and_remains_deletable(self):
        await asyncio.to_thread(self.backend.create, "rename", "/bin/bash", "/tmp", 24, 80, "")
        item = self.backend.list_sessions()[0]
        self.backend.run("rename-session", "-t", item["tmux_id"], "my-claude")
        renamed = (await server.session_listing())[0]
        self.assertEqual(renamed["id"], "rename")
        self.assertEqual(renamed["name"], "my-claude")
        self.assertTrue(renamed["managed"])
        response = await self.http_client.fetch(
            self.get_url("/api/sessions/rename?backend=tmux"), method="DELETE",
        )
        self.assertEqual(response.code, 204)
        self.assertEqual(self.backend.list_sessions(), [])

    @gen_test
    async def test_same_socket_is_not_discovered_twice(self):
        await asyncio.to_thread(self.backend.create, "once", "/bin/bash", "/tmp", 24, 80, "")
        with patch.object(self.native_backend, "socket_path", self.backend.socket_path):
            listing = await server.session_listing()
        self.assertEqual([item["id"] for item in listing], ["once"])

    async def patch_name(self, sid, backend, name):
        return await self.http_client.fetch(
            self.get_url(f"/api/sessions/{sid}?backend={backend}"), method="PATCH",
            headers={"Content-Type": "application/json"}, body=json.dumps({"name": name}),
            raise_error=False,
        )

    async def renamed_event(self, ws, name):
        async def read():
            while True:
                message = await ws.read_message()
                if isinstance(message, str):
                    data = json.loads(message)
                    if data.get("type") == "session_info" and data.get("name") == name:
                        return data
        return await asyncio.wait_for(read(), timeout=3)

    @gen_test
    async def test_native_rename_is_shared_and_broadcast_to_two_clients(self):
        await asyncio.to_thread(self.start_native)
        item = (await server.session_listing())[0]
        first = await self.connect(item["id"])
        second = await self.connect(item["id"])
        await self.renamed_event(second, "server_box")
        response = await self.patch_name(item["id"], "tmux", "共享终端")
        self.assertEqual(response.code, 200)
        for ws in (first, second):
            event = await self.renamed_event(ws, "共享终端")
            self.assertEqual(event["id"], item["id"])
        listing = (await server.session_listing())[0]
        self.assertEqual(listing["id"], item["id"])
        self.assertEqual(listing["name"], "共享终端")
        self.assertEqual(self.native_backend.list_sessions()[0]["name"], "共享终端")
        first.close()
        second.close()

    @gen_test
    async def test_legacy_managed_rename_preserves_identity_and_survives_wrapper_restart(self):
        self.backend.run("new-session", "-d", "-s", "wt_legacy", "/bin/bash")
        response = await self.patch_name("legacy", "tmux", "claude-work")
        self.assertEqual(response.code, 200)
        item = self.backend.list_sessions()[0]
        self.assertEqual(item["id"], "legacy")
        self.assertEqual(item["name"], "claude-work")
        self.assertTrue(item["managed"])
        ws = await self.connect("legacy")
        await self.renamed_event(ws, "claude-work")
        server.sessions["legacy"].close()
        self.assertEqual((await server.session_listing())[0]["name"], "claude-work")
        ws.close()

    @gen_test
    async def test_pty_shared_name_validation_and_authentication(self):
        ws = await self.connect("named-pty", "pty")
        await self.renamed_event(ws, "")
        response = await self.patch_name("named-pty", "pty", "PC和手机")
        self.assertEqual(response.code, 200)
        await self.renamed_event(ws, "PC和手机")
        self.assertEqual((await server.session_listing())[0]["name"], "PC和手机")
        for invalid in ("", " \n ", "x" * 129, "bad\nname", None):
            response = await self.patch_name("named-pty", "pty", invalid)
            self.assertEqual(response.code, 400)
        self.assertEqual(server.sessions["named-pty"].name, "PC和手机")
        response = await self.patch_name("named-pty", "tmux", "wrong")
        self.assertEqual(response.code, 404)
        with patch.object(server, "TOKEN", "test-password"):
            response = await self.patch_name("named-pty", "pty", "unauthorized")
            self.assertEqual(response.code, 403)
        ws.close()

    @gen_test
    async def test_invalid_protocol_messages_do_not_break_a_connection(self):
        ws = await self.connect("protocol", "pty")
        await self.renamed_event(ws, "")
        for message in (
            "[]", "null", '{"type":"input","data":{}}', '{"type":"input","data":42}',
            '{"type":"resize","rows":true,"cols":80}', '{"type":"resize","rows":2.5,"cols":80}',
        ):
            await ws.write_message(message)
            while True:
                response = await asyncio.wait_for(ws.read_message(), 3)
                if isinstance(response, str) and json.loads(response).get("type") == "request_error":
                    break
        await ws.write_message(json.dumps({"type": "ping"}))
        while True:
            message = await ws.read_message()
            if isinstance(message, str) and json.loads(message).get("type") == "pong":
                break
        ws.close()

    @gen_test(timeout=10)
    async def test_large_output_flows_without_losing_following_input(self):
        ws = await self.connect("output-budget", "pty")
        await self.renamed_event(ws, "")
        await ws.write_message(json.dumps({
            "type": "input",
            "data": "python3 -c \"import os; os.write(1, b'x' * 2097152); print('OUTPUT_FINISHED')\"\n",
        }))
        data = await self.output_until(ws, b"OUTPUT_FINISHED\r\n")
        self.assertGreater(len(data), 2097152)
        session = server.sessions["output-budget"]
        self.assertLessEqual(session._output_pending_bytes, server.MAX_PTY_PENDING_BYTES)
        await ws.write_message(json.dumps({"type": "input", "data": "printf 'AFTER_OUTPUT_OK\\n'\n"}))
        await self.output_until(ws, b"AFTER_OUTPUT_OK\r\n")
        ws.close()


if __name__ == "__main__":
    unittest.main()
