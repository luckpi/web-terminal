import asyncio
import base64
import json
import os
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from tornado.testing import AsyncTestCase, gen_test
from tornado.websocket import websocket_connect


BROWSER = shutil.which("microsoft-edge") or shutil.which("chromium") or shutil.which("google-chrome")
ROOT = Path(__file__).resolve().parent


@unittest.skipUnless(BROWSER and shutil.which("tmux"), "Chromium browser and tmux are required")
class BrowserTests(AsyncTestCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory(prefix="web-terminal-browser-")
        self.path = Path(self.directory.name)
        self.processes = []
        self.debug = None
        self.sequence = 0
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.env = dict(os.environ, PORT=str(self.port), HOST="127.0.0.1", TOKEN="",
                        TMUX_SOCKET=str(self.path / "tmux"),
                        TMUX_NATIVE_SOCKET=str(self.path / "native"))
        self.tmux = self.spawn([
            shutil.which("tmux"), "-D", "-S", self.env["TMUX_SOCKET"],
            "-f", str(ROOT / ".devin/tmux.conf"),
        ], env={k: v for k, v in self.env.items() if k not in ("TOKEN", "TMUX", "TMUX_PANE")})
        for _ in range(100):
            if Path(self.env["TMUX_SOCKET"]).exists():
                break
            time.sleep(0.02)
        self.start_server()
        self.browser = self.spawn([
            BROWSER, "--headless", "--disable-gpu", "--disable-extensions", "--no-first-run",
            "--no-default-browser-check", "--no-proxy-server",
            "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            "--user-data-dir=" + str(self.path / "profile"), "about:blank",
        ])

    def spawn(self, command, **kwargs):
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
        )
        self.processes.append(process)
        return process

    def start_server(self):
        self.server = self.spawn([shutil.which("python3"), str(ROOT / "server.py")], env=self.env)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/backends", timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.03)
        self.fail("test server failed to start")

    def tearDown(self):
        if self.debug:
            self.debug.close()
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self.directory.cleanup()
        super().tearDown()

    async def call(self, method, params=None):
        self.sequence += 1
        await self.debug.write_message(json.dumps({
            "id": self.sequence, "method": method, "params": params or {},
        }))
        while True:
            response = json.loads(await self.debug.read_message())
            if response.get("id") == self.sequence:
                self.assertNotIn("error", response)
                return response["result"]

    async def evaluate(self, expression):
        response = await self.call("Runtime.evaluate", {
            "expression": expression, "awaitPromise": True, "returnByValue": True,
        })
        self.assertNotIn("exceptionDetails", response)
        return response.get("result", {}).get("value")

    async def wait_for(self, expression):
        for _ in range(150):
            if await self.evaluate(expression):
                return
            await asyncio.sleep(0.05)
        self.fail("browser condition timed out: " + expression)

    @gen_test(timeout=60)
    async def test_historical_queries_are_not_answered_again_on_reconnect(self):
        portfile = self.path / "profile/DevToolsActivePort"
        for _ in range(200):
            if portfile.exists():
                break
            await asyncio.sleep(0.05)
        port = portfile.read_text().splitlines()[0]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as response:
            pages = json.load(response)
        target = next(page for page in pages if page["type"] == "page")
        self.debug = await websocket_connect(target["webSocketDebuggerUrl"])
        await self.call("Page.navigate", {"url": f"http://127.0.0.1:{self.port}/"})
        await self.wait_for("typeof tmuxAvailable !== 'undefined' && tmuxAvailable")
        await self.evaluate("newTerminal('pty')")
        await self.wait_for("tabs.length === 1 && tabs[0].ws.readyState === 1")
        await self.evaluate(r"""
            window.responses = [];
            window.watchResponses = () => {
              const ws = tabs[0].ws;
              const send = ws.send.bind(ws);
              ws.send = data => {
                const msg = JSON.parse(data);
                if (msg.type === 'input' && /^\x1b\[[?>][\d;]*c$/.test(msg.data)) {
                  window.responses.push(msg.data);
                }
                send(data);
              };
            };
            watchResponses();
        """)
        source = (
            "import os,select,termios,tty,time;"
            "saved=termios.tcgetattr(0);tty.setraw(0);"
            "os.write(1,b'\\x1b[c\\x1b[>c');"
            "data=b'';deadline=time.monotonic()+3;"
            "\nwhile data.count(b'c')<2 and time.monotonic()<deadline:"
            "\n if select.select([0],[],[],0.1)[0]: data+=os.read(0,1024)"
            "\ntermios.tcsetattr(0,termios.TCSANOW,saved);"
            "os.write(1,b'\\r\\nQUERY_'+b'DONE\\r\\n')"
        )
        command = "python3 -c " + shlex.quote(source) + "\n"
        await self.evaluate(
            "sendTerminalInput(tabs[0], " + json.dumps(command) + ")"
        )
        await self.wait_for("responses.length === 2")
        await self.wait_for(
            "Array.from({length:tabs[0].term.buffer.active.length},(_,i)=>"
            "tabs[0].term.buffer.active.getLine(i)?.translateToString(true)).join('\\n').includes('QUERY_DONE')"
        )
        for _ in range(3):
            await self.evaluate("responses = []; reconnectTab(tabs[0]); watchResponses()")
            await self.wait_for("tabs[0].ws.readyState === 1 && !tabs[0].initialSync && !tabs[0].receivingReplay")
            await self.evaluate("new Promise(resolve => tabs[0].term.write('', resolve))")
            self.assertEqual(await self.evaluate("responses"), [])
        await self.evaluate("sendTerminalInput(tabs[0], " + json.dumps(command) + ")")
        await self.wait_for("responses.length === 2")
        query_id = await self.evaluate("tabs[0].id")
        await self.evaluate("newTerminal('pty')")
        await self.wait_for("tabs.length === 2 && tabs[1].ws.readyState === 1")
        await self.call("Page.enable")
        await self.call("Page.addScriptToEvaluateOnNewDocument", {"source": r"""
            window.queryTestDocument = Math.random();
            window.responses = [];
            const send = WebSocket.prototype.send;
            WebSocket.prototype.send = function(data) {
              const msg = JSON.parse(data);
              if (msg.type === 'input' && /^\x1b\[[?>][\d;]*c$/.test(msg.data)) {
                responses.push(msg.data);
              }
              return send.call(this, data);
            };
        """})
        await self.call("Page.reload", {"ignoreCache": True})
        await self.wait_for(
            "typeof queryTestDocument === 'number' && typeof tabs !== 'undefined' && "
            "tabs.length === 2 && activeTabId !== " + json.dumps(query_id)
        )
        self.assertTrue(await self.evaluate(
            "tabs.find(tab => tab.id === " + json.dumps(query_id) + ").term === null"
        ))
        await self.evaluate("switchTab(" + json.dumps(query_id) + ")")
        await self.wait_for(
            "tabs.find(tab=>tab.id===activeTabId).ws.readyState === 1 && "
            "!tabs.find(tab=>tab.id===activeTabId).initialSync"
        )
        await self.evaluate("new Promise(resolve => tabs.find(tab=>tab.id===activeTabId).term.write('', resolve))")
        self.assertEqual(await self.evaluate("responses"), [])
        await self.evaluate("window.queryTestDocument = null")
        await self.call("Page.reload", {"ignoreCache": True})
        await self.wait_for(
            "typeof queryTestDocument === 'number' && typeof tabs !== 'undefined' && tabs.length === 2 && "
            "tabs.find(tab=>tab.id===activeTabId)?.ws?.readyState === 1 && "
            "!tabs.find(tab=>tab.id===activeTabId).initialSync"
        )
        await self.evaluate("new Promise(resolve => tabs.find(tab=>tab.id===activeTabId).term.write('', resolve))")
        self.assertEqual(await self.evaluate("responses"), [])

    @gen_test(timeout=90)
    async def test_sidebar_groups_and_tmux_survive_a_real_web_server_restart(self):
        portfile = self.path / "profile/DevToolsActivePort"
        for _ in range(200):
            if portfile.exists():
                break
            await asyncio.sleep(0.05)
        port = portfile.read_text().splitlines()[0]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as response:
            pages = json.load(response)
        target = next(page for page in pages if page["type"] == "page")
        self.debug = await websocket_connect(target["webSocketDebuggerUrl"])
        await self.call("Page.navigate", {"url": f"http://127.0.0.1:{self.port}/"})
        await self.wait_for("typeof tmuxAvailable !== 'undefined' && tmuxAvailable")
        await self.evaluate("newTerminal('pty')")
        await self.wait_for("tabs.length === 1 && tabs[0].ws.readyState === 1")
        await self.evaluate("newTerminal('tmux')")
        await self.wait_for("tabs.length === 2 && tabs[1].ws.readyState === 1")
        sid = await self.evaluate("tabs[1].id")
        await self.evaluate("setGroup('pty')")
        self.assertEqual(await self.evaluate("visibleTabs().map(tab => tab.backend)"), ["pty"])
        self.assertEqual(await self.evaluate("tabs[1].ws.readyState"), 1)
        await self.evaluate("setGroup('tmux')")
        self.assertEqual(await self.evaluate("visibleTabs().map(tab => tab.backend)"), ["tmux"])
        await self.evaluate("setGroup('all')")
        self.assertEqual(await self.evaluate("visibleTabs().length"), 2)
        self.assertTrue(await self.evaluate(
            "terminalArea.getBoundingClientRect().left >= document.getElementById('sidebar').getBoundingClientRect().right"
        ))
        await self.evaluate(
            "tabs[1].ws.send(JSON.stringify({type:'input',data:"
            + json.dumps("export WT_RESTART=kept; printf 'BEFORE:%s\\n' \"$WT_RESTART\"\n") + "}))"
        )
        await self.wait_for(
            "Array.from({length:tabs[1].term.buffer.active.length},(_,i)=>"
            "tabs[1].term.buffer.active.getLine(i)?.translateToString(true)).join('\\n').includes('BEFORE:kept')"
        )
        screenshot = os.environ.get("WEB_TERMINAL_SCREENSHOT")
        if screenshot:
            result = await self.call("Page.captureScreenshot")
            Path(screenshot).write_bytes(base64.b64decode(result["data"]))
        await self.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        self.assertEqual(await self.evaluate("getComputedStyle(document.getElementById('sidebar')).display"), "none")
        await self.evaluate("toggleSidebar()")
        self.assertEqual(await self.evaluate("getComputedStyle(document.getElementById('sidebar')).display"), "flex")
        await self.evaluate("switchTab(tabs[1].id)")
        self.assertEqual(await self.evaluate("getComputedStyle(document.getElementById('sidebar')).display"), "none")
        await self.call("Emulation.clearDeviceMetricsOverride")
        await self.evaluate("window.confirm = () => true; setGroup('pty'); closeAllTabs()")
        self.assertEqual(await self.evaluate("tabs.map(tab=>tab.backend)"), ["tmux"])
        await self.evaluate("newTerminal('pty'); setGroup('tmux')")
        await self.wait_for("tabs.length === 2 && tabs[1].ws.readyState === 1")
        self.server.terminate()
        self.server.wait(timeout=5)
        self.assertIsNone(self.tmux.poll())
        self.start_server()
        await self.call("Page.reload", {"ignoreCache": True})
        await self.wait_for("typeof tabs !== 'undefined' && tabs.length === 1 && tabs[0].ws?.readyState === 1")
        self.assertEqual(await self.evaluate("tabs[0].id"), sid)
        self.assertEqual(await self.evaluate("tabs[0].backend"), "tmux")
        await self.evaluate(
            "tabs[0].ws.send(JSON.stringify({type:'input',data:"
            + json.dumps("printf 'AFTER:%s\\n' \"$WT_RESTART\"\n") + "}))"
        )
        await self.wait_for(
            "Array.from({length:tabs[0].term.buffer.active.length},(_,i)=>"
            "tabs[0].term.buffer.active.getLine(i)?.translateToString(true)).join('\\n').includes('AFTER:kept')"
        )
        await self.evaluate("window.confirm = () => true; closeTab(tabs[0].id)")
        self.assertEqual(await self.evaluate("tabs.length"), 0)
        self.native = self.spawn([
            shutil.which("tmux"), "-D", "-S", self.env["TMUX_NATIVE_SOCKET"],
            "-f", str(ROOT / ".devin/tmux.conf"),
        ], env={k: v for k, v in self.env.items() if k not in ("TOKEN", "TMUX", "TMUX_PANE")})
        for _ in range(100):
            if Path(self.env["TMUX_NATIVE_SOCKET"]).exists():
                break
            await asyncio.sleep(0.02)
        command = [shutil.which("tmux"), "-N", "-S", self.env["TMUX_NATIVE_SOCKET"]]
        created = await asyncio.to_thread(
            subprocess.run, command + ["new-session", "-d", "-s", "native-work", "/bin/bash"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(created.returncode, 0)
        await self.evaluate("refreshSessions()")
        await self.wait_for("tabs.length === 1 && tabs[0].ws?.readyState === 1")
        self.assertEqual(await self.evaluate("tabs[0].managed"), False)
        self.assertEqual(await self.evaluate("tabs[0].titleEl.textContent"), "native-work")
        self.assertEqual(await self.evaluate("document.getElementById('terminal-title').textContent"), "native-work")
        self.spawn([
            BROWSER, "--headless", "--disable-gpu", "--disable-extensions", "--no-first-run",
            "--no-default-browser-check", "--no-proxy-server",
            "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            "--user-data-dir=" + str(self.path / "mobile-profile"), "about:blank",
        ])
        mobile_portfile = self.path / "mobile-profile/DevToolsActivePort"
        for _ in range(200):
            if mobile_portfile.exists():
                break
            await asyncio.sleep(0.05)
        mobile_port = mobile_portfile.read_text().splitlines()[0]
        with urllib.request.urlopen(f"http://127.0.0.1:{mobile_port}/json/list") as response:
            mobile_pages = json.load(response)
        mobile_target = next(page for page in mobile_pages if page["type"] == "page")
        mobile_debug = await websocket_connect(mobile_target["webSocketDebuggerUrl"])
        desktop_debug = self.debug
        try:
            self.debug = mobile_debug
            await self.call("Emulation.setDeviceMetricsOverride", {
                "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
            })
            await self.call("Page.navigate", {"url": f"http://127.0.0.1:{self.port}/"})
            await self.wait_for("typeof tabs !== 'undefined' && tabs.length === 1 && tabs[0].ws?.readyState === 1")
            self.assertEqual(await self.evaluate("localStorage.getItem('webterm-titles')"), None)
            self.debug = desktop_debug
            await self.evaluate("renameTab(tabs[0], 'desktop-shared')")
            self.debug = mobile_debug
            await self.wait_for("document.getElementById('terminal-title').textContent === 'desktop-shared'")
            await self.evaluate("renameTab(tabs[0], 'mobile-shared')")
            self.debug = desktop_debug
            await self.wait_for("document.getElementById('terminal-title').textContent === 'mobile-shared'")
        finally:
            self.debug = desktop_debug
            mobile_debug.close()
        await self.evaluate(
            "startInlineRename(tabs[0].titleEl, tabs[0]); tabs[0].titleEl.textContent = 'shared-work';"
            "tabs[0].titleEl.dispatchEvent(new FocusEvent('blur'))"
        )
        await self.wait_for("document.getElementById('terminal-title').textContent === 'shared-work'")
        await self.evaluate("localStorage.setItem('webterm-titles', JSON.stringify({[tabs[0].id]:'stale-local-alias'}))")
        await self.call("Page.reload", {"ignoreCache": True})
        await self.wait_for(
            "typeof tabs !== 'undefined' && tabs.length === 1 && tabs[0].ws?.readyState === 1"
        )
        self.assertEqual(await self.evaluate("document.getElementById('terminal-title').textContent"), "shared-work")
        renamed = await asyncio.to_thread(
            subprocess.run, command + ["rename-session", "-t", "shared-work", "renamed-work"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(renamed.returncode, 0)
        await self.evaluate("refreshSessions()")
        self.assertEqual(await self.evaluate("tabs.length"), 1)
        self.assertEqual(await self.evaluate("document.getElementById('terminal-title').textContent"), "renamed-work")
        await self.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        self.assertNotEqual(await self.evaluate(
            "getComputedStyle(document.getElementById('terminal-title')).display"
        ), "none")
        self.assertGreater(await self.evaluate("document.getElementById('terminal-title').clientWidth"), 0)
        await self.call("Emulation.clearDeviceMetricsOverride")
        if screenshot:
            result = await self.call("Page.captureScreenshot")
            Path(screenshot).write_bytes(base64.b64decode(result["data"]))
        await self.evaluate(
            "window.confirm = () => {throw new Error('external close must not be destructive')}; closeTab(tabs[0].id)"
        )
        self.assertEqual(await self.evaluate("tabs.length"), 0)
        remaining = await asyncio.to_thread(
            subprocess.run, command + ["list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(remaining.stdout.strip(), "renamed-work")


if __name__ == "__main__":
    unittest.main()
