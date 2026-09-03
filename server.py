#!/usr/bin/env python3
"""Persistent web terminal server with multi-session support.

Each session runs a real PTY (a shell process) on the backend. Closing the
browser tab only closes the WebSocket; the PTY stays alive until the user
closes the corresponding terminal tab (which sends a `close` message).
"""

import base64
import json
import logging
import os
import signal
import termios
import threading
import time
from collections import deque

import tornado.web
import tornado.websocket
from tornado.ioloop import IOLoop
from tornado.iostream import StreamClosedError
from ptyprocess import PtyProcess

PORT = int(os.environ.get("PORT", "8765"))
HOST = os.environ.get("HOST", "127.0.0.1")
SHELL = os.environ.get("SHELL", "/bin/bash")
# Default working directory for new sessions.  Defaults to the user home
# directory. Override with the CWD environment variable.
CWD = os.environ.get("CWD") or os.path.expanduser("~")
# Terminal type advertised to child shells. xterm-256color enables 256-color
# and truecolor support in most terminal applications.
TERM = os.environ.get("WEB_TERMINAL_TERM", "xterm-256color")
COLORTERM = os.environ.get("WEB_TERMINAL_COLORTERM", "truecolor")
# Default PTY dimensions before the frontend resizes it.
TERMINAL_ROWS = int(os.environ.get("TERMINAL_ROWS", "24"))
TERMINAL_COLS = int(os.environ.get("TERMINAL_COLS", "80"))
MAX_BUFFER_BYTES = int(os.environ.get("MAX_BUFFER", "100000"))
# Optional access token. If set, every endpoint (HTTP and WebSocket) requires
# a matching token in the query string or in the X-Token header.
TOKEN = os.environ.get("TOKEN", "")
# Maximum number of active sessions. 0 means unlimited.
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "0"))

# session_id -> Session
sessions = {}


def make_session_id():
    return base64.urlsafe_b64encode(os.urandom(12)).decode("ascii").rstrip("=")


def _check_token(handler):
    """Validate the request token when TOKEN is configured."""
    if not TOKEN:
        return True
    token = handler.get_argument("token", default=None)
    if not token:
        token = handler.request.headers.get("X-Token")
    return token == TOKEN


def _preexec_setup_pty():
    """Configure the child PTY before exec.

    Keep terminal line editing echo (ECHO) on so the user sees what they
    type, but turn off ECHOCTL. ECHOCTL would otherwise echo terminal
    control sequences such as ESC as ^[ and can leak xterm.js responses
    (e.g., the primary device attributes reply ESC[?1;2c) onto the screen.
    """
    try:
        fd = 0
        attr = termios.tcgetattr(fd)
        # c_lflag index is 3 on Linux
        attr[3] &= ~(termios.ECHOCTL | termios.ECHOKE | termios.ECHOK)
        termios.tcsetattr(fd, termios.TCSANOW, attr)
    except Exception:
        pass


class Session:
    """Wraps a PTY process and the clients connected to it."""

    def __init__(self, session_id, io_loop, shell=SHELL, cwd=CWD, term=TERM, colorterm=COLORTERM, rows=TERMINAL_ROWS, cols=TERMINAL_COLS):
        self.id = session_id
        self.io_loop = io_loop
        self.clients = set()
        self.buffer = deque()       # recent raw output bytes
        self.buffer_bytes = 0
        self.closed = False
        self.created_at = time.time()

        # Validate shell and cwd; fall back to safe defaults if necessary.
        if not os.path.isfile(shell) or not os.access(shell, os.X_OK):
            logging.warning("[%s] Shell %s is not executable, falling back to /bin/bash", self.id, shell)
            shell = "/bin/bash"
        if not os.path.isdir(cwd):
            logging.warning("[%s] CWD %s is not a directory, falling back to %s", self.id, cwd, os.path.expanduser("~"))
            cwd = os.path.expanduser("~")

        # Start from a clean environment and only set COLORTERM when requested.
        # This prevents the parent process's COLORTERM from leaking through
        # when the frontend selects the empty "none" color mode.
        env = os.environ.copy()
        env.pop("COLORTERM", None)
        env["TERM"] = term
        if colorterm:
            env["COLORTERM"] = colorterm
        logging.info("[%s] Spawning shell: %s in %s (TERM=%s, COLORTERM=%s, %dx%d)", self.id, shell, cwd, term, colorterm, cols, rows)
        self.process = PtyProcess.spawn(
            [shell],
            dimensions=(rows, cols),
            echo=True,
            preexec_fn=_preexec_setup_pty,
            cwd=cwd,
            env=env,
        )

        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

    def _reader(self):
        """Background thread that reads PTY output and forwards it."""
        try:
            while not self.closed:
                try:
                    data = self.process.read(65536)
                except EOFError:
                    break
                if not data:
                    break

                self._append_output(data)
                if self.clients:
                    self._broadcast(data)
        except Exception:
            logging.exception("[%s] Reader error", self.id)
        finally:
            try:
                status = self.process.isalive()
                exit_code = self.process.exitstatus if not status else None
            except Exception:
                status, exit_code = None, None
            logging.info("[%s] PTY reader ended; isalive=%s exitstatus=%s", self.id, status, exit_code)
            self.io_loop.add_callback(self.close)

    def _append_output(self, data):
        self.buffer.append(data)
        self.buffer_bytes += len(data)
        while self.buffer_bytes > MAX_BUFFER_BYTES and len(self.buffer) > 1:
            removed = self.buffer.popleft()
            self.buffer_bytes -= len(removed)

    def _broadcast(self, data):
        for client in list(self.clients):
            self.io_loop.add_callback(client.safe_write, data)

    def add_client(self, client):
        self.clients.add(client)
        replay = b"".join(self.buffer)
        if replay:
            self.io_loop.add_callback(client.safe_write, replay)

    def remove_client(self, client):
        self.clients.discard(client)

    def write(self, data):
        if self.closed or not self.process or not self.process.isalive():
            return
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            self.process.write(data)
        except Exception:
            logging.exception("[%s] Write error", self.id)

    def resize(self, rows, cols):
        if self.closed or not self.process or not self.process.isalive():
            return
        try:
            self.process.setwinsize(rows, cols)
        except Exception:
            logging.exception("[%s] Resize error", self.id)

    def close(self):
        if self.closed:
            return
        self.closed = True

        # Notify all attached clients and close their sockets.
        for client in list(self.clients):
            self.io_loop.add_callback(client.close_after_notify)
        self.clients.clear()

        # Kill the underlying shell/PTY and close the master fd.
        try:
            if self.process and self.process.isalive():
                self.process.terminate(force=True)
        except Exception:
            logging.exception("[%s] Terminate error", self.id)
        try:
            if self.process:
                self.process.close()
        except Exception:
            logging.exception("[%s] Process close error", self.id)

        sessions.pop(self.id, None)
        logging.info("[%s] Session closed", self.id)


class TerminalWSHandler(tornado.websocket.WebSocketHandler):
    session = None

    async def open(self):
        if not _check_token(self):
            try:
                await self.write_message(json.dumps({"type": "error", "message": "invalid or missing token"}), binary=False)
            except Exception:
                pass
            self.close()
            return

        sid = self.get_argument("session", default=None)
        if not sid:
            sid = make_session_id()

        if sid not in sessions:
            if MAX_SESSIONS and len(sessions) >= MAX_SESSIONS:
                try:
                    await self.write_message(json.dumps({"type": "error", "message": "max sessions reached"}), binary=False)
                except Exception:
                    pass
                self.close()
                return

            term = self.get_argument("term", default=TERM)
            colorterm = self.get_argument("colorterm", default=COLORTERM)
            try:
                rows = int(self.get_argument("rows", default=TERMINAL_ROWS))
            except (ValueError, TypeError):
                rows = TERMINAL_ROWS
            try:
                cols = int(self.get_argument("cols", default=TERMINAL_COLS))
            except (ValueError, TypeError):
                cols = TERMINAL_COLS
            sessions[sid] = Session(sid, IOLoop.current(), shell=SHELL, cwd=CWD, term=term, colorterm=colorterm, rows=rows, cols=cols)

        self.session_id = sid
        self.session = sessions[sid]
        self.session.add_client(self)
        logging.info("[%s] Client connected (total %d)", sid, len(self.session.clients))

    def on_message(self, message):
        if not self.session:
            return
        try:
            msg = json.loads(message)
        except Exception:
            logging.warning("[%s] Malformed WebSocket message: %r", self.session_id, message[:200])
            return

        mtype = msg.get("type")
        if mtype == "input":
            self.session.write(msg.get("data", ""))
        elif mtype == "resize":
            rows = msg.get("rows", TERMINAL_ROWS)
            cols = msg.get("cols", TERMINAL_COLS)
            try:
                rows = int(rows)
                cols = int(cols)
            except (ValueError, TypeError):
                return
            if rows <= 0 or cols <= 0 or rows > 500 or cols > 1000:
                return
            self.session.resize(rows, cols)
        elif mtype == "close":
            self.session.close()
            self.session = None
        elif mtype == "ping":
            try:
                self.write_message(json.dumps({"type": "pong"}), binary=False)
            except Exception:
                pass

    def on_close(self):
        if self.session:
            logging.info("[%s] Client disconnected", self.session_id)
            self.session.remove_client(self)
            self.session = None

    def safe_write(self, data):
        try:
            self.write_message(data, binary=True)
        except (tornado.websocket.WebSocketClosedError, StreamClosedError):
            # Client went away; session cleanup happens in on_close.
            pass
        except Exception:
            logging.exception("[%s] Unexpected safe_write error", self.session_id or "?")

    def close_after_notify(self):
        """Notify the client that this session is closing, then close the socket."""
        try:
            self.write_message(json.dumps({"type": "session_closed"}), binary=False)
        except (tornado.websocket.WebSocketClosedError, StreamClosedError):
            pass
        except Exception:
            logging.exception("[%s] close_after_notify write error", getattr(self, "session_id", "?"))
        try:
            self.close()
        except (tornado.websocket.WebSocketClosedError, StreamClosedError):
            pass
        except Exception:
            logging.exception("[%s] close_after_notify close error", getattr(self, "session_id", "?"))


class MainHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.set_header("Pragma", "no-cache")
        self.set_header("Expires", "0")

        if not _check_token(self):
            self.set_status(403)
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps({"error": "invalid or missing token"}))
            return

        self.set_header("Content-Type", "text/html")
        html_path = os.path.join(os.path.dirname(__file__), "index.html")
        try:
            with open(html_path, "rb") as f:
                self.write(f.read())
        except FileNotFoundError:
            self.set_status(404)
            self.finish("index.html not found")


class ApiSessionsHandler(tornado.web.RequestHandler):
    """Return the list of currently active sessions."""

    def get(self):
        if not _check_token(self):
            self.set_status(403)
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps({"error": "invalid or missing token"}))
            return

        self.set_header("Content-Type", "application/json")
        items = [
            {
                "id": sid,
                "created_at": s.created_at,
                "clients": len(s.clients),
            }
            for sid, s in sessions.items()
        ]
        items.sort(key=lambda x: x["created_at"], reverse=True)
        self.write(json.dumps(items))


class ApiSessionHandler(tornado.web.RequestHandler):
    """Close a specific session by ID."""

    def delete(self, session_id):
        if not _check_token(self):
            self.set_status(403)
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps({"error": "invalid or missing token"}))
            return

        session = sessions.get(session_id)
        if not session:
            self.set_status(404)
            self.finish(json.dumps({"error": "session not found"}))
            return
        session.close()
        self.set_status(204)
        self.finish()


def make_app():
    static_path = os.path.join(os.path.dirname(__file__), "static")
    return tornado.web.Application(
        [
            (r"/", MainHandler),
            (r"/ws", TerminalWSHandler),
            (r"/api/sessions", ApiSessionsHandler),
            (r"/api/sessions/([^/]+)", ApiSessionHandler),
            (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": static_path}),
        ],
        debug=False,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    app = make_app()
    app.listen(PORT, address=HOST)
    logging.info("Web terminal listening on http://%s:%s", HOST, PORT)

    def shutdown():
        logging.info("Shutting down...")
        for s in list(sessions.values()):
            s.close()
        IOLoop.current().stop()

    def _handle_signal(signum, frame):
        IOLoop.current().add_callback_from_signal(shutdown)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        IOLoop.current().start()
    finally:
        if sessions:
            logging.info("Cleaning up remaining sessions...")
            for s in list(sessions.values()):
                s.close()
