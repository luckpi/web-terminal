#!/usr/bin/env python3
"""Persistent web terminal server with multi-session support.

Each session runs a real PTY (a shell process) on the backend. Closing the
browser tab only closes the WebSocket; the PTY stays alive until the user
closes the corresponding terminal tab (which sends a `close` message).
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import queue
import re
import signal
import termios
import threading
import time
from collections import deque

import tornado.httpserver
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
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "32"))
# Maximum bytes of outbound data buffered per WebSocket client. A client
# that stops reading (hung connection, dead browser) would otherwise let
# terminal output pile up in memory.
MAX_WS_PENDING_BYTES = int(os.environ.get("MAX_WS_PENDING", "8388608"))
# Set to 1 when running behind a trusted reverse proxy so remote_ip honours
# X-Real-Ip/X-Forwarded-For. Must only be enabled behind a trusted proxy,
# otherwise clients can spoof their IP.
XHEADERS = os.environ.get("XHEADERS", "") not in ("", "0", "false", "no")

# Cookie-based authentication. /login verifies the password (TOKEN) and sets
# this cookie. The cookie value is a per-boot random HMAC keyed by the
# password, so a leaked cookie stops working when the server restarts (all
# terminal sessions die on restart anyway) and it carries no usable secret.
# The cookie is HttpOnly and SameSite=Lax, which also prevents cross-site
# WebSocket handshakes from carrying it.
AUTH_COOKIE = "webterm_auth"
COOKIE_SECRET = (
    hmac.new(TOKEN.encode("utf-8"), os.urandom(32), hashlib.sha256).hexdigest()
    if TOKEN else ""
)
COOKIE_MAX_AGE = 30 * 86400

AUTH_FAIL_TTL = 3600  # reset a client's failure count after this quiet period
AUTH_FAIL_MAX_IPS = 1024  # bound the size of the failure bookkeeping table

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9._+-]{1,64}$")

# remote_ip -> (failed_attempts, last_attempt); used to throttle brute force.
_auth_failures = {}

# session_id -> Session
sessions = {}

_TOKEN_QS_RE = re.compile(r"([?&]token=)[^&]*")


def _log_request(handler):
    """Access log with the ?token= credential redacted.

    Tornado's default log_function logs the full request URI, which would
    write the access token to the server log whenever a script uses
    ?token=. Redact it here (same format as tornado.log.access_log).
    """
    status = handler.get_status()
    if status < 400:
        log_method = logging.info
    elif status < 500:
        log_method = logging.warning
    else:
        log_method = logging.error
    uri = _TOKEN_QS_RE.sub(r"\1[redacted]", handler.request.uri)
    summary = "%s %s (%s)" % (handler.request.method, uri, handler.request.remote_ip)
    log_method("%d %s %.2fms", status, summary,
               1000.0 * handler.request.request_time())


def make_session_id():
    return base64.urlsafe_b64encode(os.urandom(12)).decode("ascii").rstrip("=")


def _hmac_eq(a, b):
    try:
        return hmac.compare_digest(a, b)
    except TypeError:
        return False


def _check_token(handler):
    """Validate request auth when TOKEN is configured.

    Accepts the auth cookie set by /login, a ?token= query argument, or the
    X-Token header.
    """
    if not TOKEN:
        return True
    cookie = handler.get_cookie(AUTH_COOKIE)
    if cookie and _hmac_eq(cookie, COOKIE_SECRET):
        return True
    token = handler.get_argument("token", default=None)
    if not token:
        token = handler.request.headers.get("X-Token")
    return _hmac_eq(token, TOKEN) if token else False


def _presented_credential(handler):
    """True when the request offered an explicit credential worth throttling."""
    return bool(
        handler.get_argument("token", default=None)
        or handler.request.headers.get("X-Token")
    )


def _clear_auth_failures(handler):
    _auth_failures.pop(handler.request.remote_ip, None)


async def _throttle_auth_failure(handler):
    """Slow down repeated authentication failures from the same client.

    Entries expire after AUTH_FAIL_TTL seconds of quiet so the table
    neither grows forever nor penalises a client indefinitely for a
    handful of old typos.
    """
    ip = handler.request.remote_ip
    now = time.time()
    if len(_auth_failures) > AUTH_FAIL_MAX_IPS:
        for stale_ip, (_, ts) in list(_auth_failures.items()):
            if now - ts > AUTH_FAIL_TTL:
                _auth_failures.pop(stale_ip, None)
    count, last = _auth_failures.get(ip, (0, 0))
    if now - last > AUTH_FAIL_TTL:
        count = 0
    count += 1
    _auth_failures[ip] = (count, now)
    if count > 3:
        await asyncio.sleep(min(2 ** (count - 3), 30))


def _set_auth_cookie(handler):
    """Issue the auth cookie; Secure only when the request arrived over HTTPS
    (e.g. behind a TLS-terminating proxy, which requires XHEADERS=1)."""
    handler.set_cookie(
        AUTH_COOKIE, COOKIE_SECRET, httponly=True, samesite="Lax",
        max_age=COOKIE_MAX_AGE, secure=(handler.request.protocol == "https"),
    )


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
        # Never hand the access token to child shells; it would be visible
        # via `env` and /proc/<pid>/environ.
        env.pop("TOKEN", None)
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

        # Writes go through a dedicated thread with a FIFO queue: ptyprocess
        # writes with blocking os.write(), and a child that stops reading its
        # input would otherwise stall the whole IOLoop for every session.
        # The queue is bounded — a real PTY input buffer is ~4KB anyway, so
        # dropping input under sustained backpressure matches tty semantics
        # while keeping memory bounded.
        self._write_queue = queue.Queue(maxsize=1024)
        self._writer_thread = threading.Thread(target=self._writer, daemon=True)
        self._writer_thread.start()

        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()

    def _reader(self):
        """Background thread that reads PTY output and forwards it.

        This thread only blocks on read().  All mutation of the replay
        buffer and the client set happens on the IOLoop thread via
        add_callback, so no locking is needed.
        """
        try:
            while not self.closed:
                try:
                    data = self.process.read(65536)
                except EOFError:
                    break
                if not data:
                    break

                self.io_loop.add_callback(self._on_pty_data, data)
        except Exception:
            # A closed master fd during session teardown is expected.
            if not self.closed:
                logging.exception("[%s] Reader error", self.id)
        finally:
            try:
                status = self.process.isalive()
                exit_code = self.process.exitstatus if not status else None
            except Exception:
                status, exit_code = None, None
            logging.info("[%s] PTY reader ended; isalive=%s exitstatus=%s", self.id, status, exit_code)
            try:
                self.io_loop.add_callback(self.close)
            except Exception:
                pass

    def _writer(self):
        """Background thread draining the write queue into the PTY.

        Blocking writes are fine here because this thread owns the master
        fd write side. Exits on the None sentinel from close() or on a
        write error (the PTY is gone either way).
        """
        while True:
            data = self._write_queue.get()
            if data is None:
                return
            try:
                self.process.write(data)
            except Exception:
                if not self.closed:
                    logging.exception("[%s] Write error", self.id)
                return

    def _on_pty_data(self, data):
        """Buffer a chunk of PTY output and broadcast it. IOLoop thread only."""
        if self.closed:
            return
        self._append_output(data)
        for client in list(self.clients):
            client.safe_write(data)

    def _append_output(self, data):
        self.buffer.append(data)
        self.buffer_bytes += len(data)
        while self.buffer_bytes > MAX_BUFFER_BYTES and len(self.buffer) > 1:
            removed = self.buffer.popleft()
            self.buffer_bytes -= len(removed)

    def add_client(self, client):
        self.clients.add(client)
        # The buffer is only mutated on the IOLoop thread, so joining it here
        # is safe; output produced later is broadcast in queue order and can
        # never overtake this replay.
        replay = b"".join(self.buffer)
        if replay:
            client.safe_write(replay)

    def remove_client(self, client):
        self.clients.discard(client)

    def write(self, data):
        if self.closed or not self.process or not self.process.isalive():
            return
        if isinstance(data, str):
            data = data.encode("utf-8")
        # Enqueue only; the writer thread performs the (blocking) os.write
        # so input can never stall the IOLoop.
        try:
            self._write_queue.put_nowait(data)
        except queue.Full:
            logging.warning("[%s] Input queue full, dropping input", self.id)

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

        # Drain pending input and stop the writer thread; nothing that
        # arrives after close should be written anyway.
        try:
            with self._write_queue.mutex:
                self._write_queue.queue.clear()
            self._write_queue.put(None)
        except Exception:
            pass

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pending_bytes = 0
        self._dropped = False

    async def open(self):
        if not _check_token(self):
            await _throttle_auth_failure(self)
            try:
                await self.write_message(json.dumps({"type": "error", "message": "invalid or missing token"}), binary=False)
            except Exception:
                pass
            self.close()
            return

        sid = self.get_argument("session", default=None)
        if not sid:
            sid = make_session_id()
        elif not SESSION_ID_RE.match(sid):
            try:
                await self.write_message(json.dumps({"type": "error", "message": "invalid session id"}), binary=False)
            except Exception:
                pass
            self.close()
            return

        if sid not in sessions:
            if MAX_SESSIONS and len(sessions) >= MAX_SESSIONS:
                try:
                    await self.write_message(json.dumps({"type": "error", "message": "max sessions reached"}), binary=False)
                except Exception:
                    pass
                self.close()
                return

            term = self.get_argument("term", default=TERM)
            if not ENV_VALUE_RE.match(term or ""):
                term = TERM
            colorterm = self.get_argument("colorterm", default=COLORTERM)
            if colorterm and not ENV_VALUE_RE.match(colorterm):
                colorterm = COLORTERM
            try:
                rows = int(self.get_argument("rows", default=TERMINAL_ROWS))
            except (ValueError, TypeError):
                rows = TERMINAL_ROWS
            try:
                cols = int(self.get_argument("cols", default=TERMINAL_COLS))
            except (ValueError, TypeError):
                cols = TERMINAL_COLS
            if not 1 <= rows <= 500:
                rows = TERMINAL_ROWS
            if not 1 <= cols <= 1000:
                cols = TERMINAL_COLS
            try:
                sessions[sid] = Session(sid, IOLoop.current(), shell=SHELL, cwd=CWD, term=term, colorterm=colorterm, rows=rows, cols=cols)
            except Exception:
                sessions.pop(sid, None)
                logging.exception("[%s] Failed to spawn session", sid)
                try:
                    await self.write_message(json.dumps({"type": "error", "message": "failed to spawn session"}), binary=False)
                except Exception:
                    pass
                self.close()
                return

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
            self._send(json.dumps({"type": "pong"}), binary=False)

    def on_close(self):
        if self.session:
            logging.info("[%s] Client disconnected", self.session_id)
            self.session.remove_client(self)
            self.session = None

    def _send(self, data, binary=True):
        """Write a message, swallowing both sync and async socket errors.

        Tracks buffered-but-unwritten bytes and drops the client once it
        exceeds MAX_WS_PENDING_BYTES: a peer that stops reading would
        otherwise let PTY output pile up in memory indefinitely.
        """
        if self._dropped:
            return
        size = len(data) if isinstance(data, (bytes, bytearray, memoryview)) else len(data.encode("utf-8"))
        if MAX_WS_PENDING_BYTES and self._pending_bytes + size > MAX_WS_PENDING_BYTES:
            self._dropped = True
            logging.warning("[%s] Client %d bytes behind, dropping connection",
                            getattr(self, "session_id", "?"), self._pending_bytes + size)
            self.close()
            return
        self._pending_bytes += size
        try:
            future = self.write_message(data, binary=binary)
        except (tornado.websocket.WebSocketClosedError, StreamClosedError):
            self._pending_bytes -= size
            return
        except Exception:
            self._pending_bytes -= size
            logging.exception("[%s] Write error", getattr(self, "session_id", "?"))
            return
        if future is not None:
            future.add_done_callback(lambda f, s=size: self._send_done(f, s))
        else:
            self._pending_bytes -= size

    def _send_done(self, future, size=0):
        self._pending_bytes -= size
        if self._pending_bytes < 0:
            self._pending_bytes = 0
        # Retrieve async write failures so asyncio does not report them as
        # "Task exception was never retrieved" when a peer vanishes mid-write.
        try:
            exc = future.exception()
        except asyncio.CancelledError:
            return
        if exc is None or isinstance(exc, (tornado.websocket.WebSocketClosedError, StreamClosedError)):
            return
        logging.error("[%s] Async write error: %r", getattr(self, "session_id", "?"), exc)

    def safe_write(self, data):
        # Client going away mid-write is fine; cleanup happens in on_close.
        self._send(data, binary=True)

    def close_after_notify(self):
        """Notify the client that this session is closing, then close the socket."""
        self._send(json.dumps({"type": "session_closed"}), binary=False)
        try:
            self.close()
        except (tornado.websocket.WebSocketClosedError, StreamClosedError):
            pass
        except Exception:
            logging.exception("[%s] close_after_notify close error", getattr(self, "session_id", "?"))


class MainHandler(tornado.web.RequestHandler):
    async def get(self):
        self.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.set_header("Pragma", "no-cache")
        self.set_header("Expires", "0")

        if not _check_token(self):
            if _presented_credential(self):
                await _throttle_auth_failure(self)
            self.redirect("/login")
            return

        # Upgrade a valid ?token= URL to the auth cookie so the frontend can
        # strip the token from the address bar and keep it out of history.
        if TOKEN and self.get_argument("token", default=None):
            _set_auth_cookie(self)

        self.set_header("Content-Type", "text/html")
        html = _index_html()
        if html is None:
            self.set_status(404)
            self.finish("index.html not found")
            return
        self.write(html)


_index_html_cache = None


def _index_html():
    """index.html contents, cached after the first successful read."""
    global _index_html_cache
    if _index_html_cache is None:
        html_path = os.path.join(os.path.dirname(__file__), "index.html")
        try:
            with open(html_path, "rb") as f:
                _index_html_cache = f.read()
        except FileNotFoundError:
            return None
    return _index_html_cache


class ApiSessionsHandler(tornado.web.RequestHandler):
    """Return the list of currently active sessions."""

    async def get(self):
        if not _check_token(self):
            if _presented_credential(self):
                await _throttle_auth_failure(self)
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

    async def delete(self, session_id):
        if not _check_token(self):
            if _presented_credential(self):
                await _throttle_auth_failure(self)
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


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Web Terminal - Login</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center;
         justify-content: center; background: #1e1e1e; color: #ddd;
         font-family: system-ui, -apple-system, sans-serif; }
  form { background: #2d2d2d; padding: 32px; border-radius: 8px;
         border: 1px solid #444; display: flex; flex-direction: column;
         gap: 14px; min-width: 300px; max-width: 90vw; }
  h2 { margin: 0; font-size: 18px; font-weight: 600; }
  input { background: #1e1e1e; color: #ddd; border: 1px solid #555;
          border-radius: 4px; padding: 10px; font-size: 14px; }
  input:focus { outline: none; border-color: #1177bb; }
  button { background: #0e639c; color: #fff; border: none; border-radius: 4px;
           padding: 10px; font-size: 14px; cursor: pointer; }
  button:hover { background: #1177bb; }
  .error { color: #f48771; font-size: 12px; min-height: 14px; }
</style>
</head>
<body>
<form method="post" action="/login">
  <h2>Web Terminal</h2>
  <input type="password" name="password" placeholder="Password / 密码"
         autofocus autocomplete="current-password">
  <div class="error">%(error)s</div>
  <button type="submit">登录 / Login</button>
</form>
</body>
</html>"""


class LoginHandler(tornado.web.RequestHandler):
    """Password login page; sets the auth cookie on success."""

    def get(self):
        if not TOKEN or _check_token(self):
            self.redirect("/")
            return
        self.set_header("Content-Type", "text/html")
        self.write(LOGIN_PAGE % {"error": ""})

    async def post(self):
        if not TOKEN:
            self.redirect("/")
            return
        password = self.get_body_argument("password", default="")
        if _hmac_eq(password, TOKEN):
            _clear_auth_failures(self)
            _set_auth_cookie(self)
            self.redirect("/")
            return
        await _throttle_auth_failure(self)
        self.set_status(403)
        self.set_header("Content-Type", "text/html")
        self.write(LOGIN_PAGE % {"error": "密码错误 / Wrong password"})


class LogoutHandler(tornado.web.RequestHandler):
    def get(self):
        self.clear_cookie(AUTH_COOKIE)
        self.redirect("/login")


class AuthStaticFileHandler(tornado.web.StaticFileHandler):
    """Static files are behind auth as well when TOKEN is configured."""

    async def get(self, path, include_body=True):
        if not _check_token(self):
            # Keep the same brute-force throttling as every other endpoint;
            # without it /static would be an unthrottled guessing oracle.
            if _presented_credential(self):
                await _throttle_auth_failure(self)
            self.set_status(403)
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps({"error": "invalid or missing token"}))
            return
        await super().get(path, include_body=include_body)


def make_app():
    static_path = os.path.join(os.path.dirname(__file__), "static")
    return tornado.web.Application(
        [
            (r"/", MainHandler),
            (r"/login", LoginHandler),
            (r"/logout", LogoutHandler),
            (r"/ws", TerminalWSHandler),
            (r"/api/sessions", ApiSessionsHandler),
            (r"/api/sessions/([^/]+)", ApiSessionHandler),
            (r"/static/(.*)", AuthStaticFileHandler, {"path": static_path}),
        ],
        debug=False,
        log_function=_log_request,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )
    app = make_app()
    server = tornado.httpserver.HTTPServer(app, xheaders=XHEADERS)
    server.listen(PORT, address=HOST)
    logging.info("Web terminal listening on http://%s:%s", HOST, PORT)
    if HOST not in ("127.0.0.1", "::1", "localhost") and not TOKEN:
        logging.warning(
            "Listening on %s with TOKEN unset — anyone who can reach this "
            "port gets a shell as this user. Set TOKEN or bind to loopback.", HOST)

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
