# Agent Notes — Web Terminal

This is a self-contained Python/Tornado web terminal.

## Commands

- Start server: `python3 server.py`
- Run server tests: `python3 test_client.py` (requires a running server).
- Run browser regressions: install `requirements-dev.txt` and Playwright Chromium, then `python3 test_browser.py`; set `CHROMIUM_PATH` to use an existing Chromium. This suite starts its own isolated server.
- Run frontend redraw regression tests: `node --test test_frontend.js` (Node.js 18+; no npm dependencies).
- Run backend unit tests: `python3 -m unittest test_server -v` (no running server required).
- Run tmux integration tests: `python3 -m unittest test_tmux -v` (uses an isolated temporary tmux socket).
- Run tmux/browser integration tests: `python3 -m unittest test_browser_tmux -v` (requires Edge/Chromium and tmux; starts isolated servers and verifies groups, shared naming, replay replies and restart persistence).
- Server URL: `http://127.0.0.1:8765/`
- WebSocket endpoint: `ws://127.0.0.1:8765/ws?session=<id>`
- Authentication: `TOKEN` env is the login password. Browsers authenticate via `GET/POST /login` which sets an `HttpOnly`+`SameSite=Lax` cookie (30 days, invalidated on server restart because the cookie secret is random per boot); `?token=`/`X-Token` remain as fallbacks for scripts (prefer `X-Token` — `?token=` is redacted in logs but still avoidable). `GET /logout` clears the cookie. All endpoints incl. `/static/*` require auth when TOKEN is set. Failed attempts are throttled per-IP on every endpoint (exponential backoff after 3 failures, counts expire after 1h of quiet).
- When running tests with auth enabled: `TOKEN=<password> python3 test_client.py`.
- Session management API: `GET /api/sessions`, `DELETE /api/sessions/<id>`, `PATCH /api/sessions/<id>?backend=pty|tmux` with JSON `{"name":"..."}` for shared renaming. Names are 1–128 characters without control characters; tmux additionally forbids `.` and `:`.
- WebSocket `resize` accepts `redraw: true`: if the PTY size is unchanged, explicitly send `SIGWINCH` to its foreground process group. Reconnect/tab restore uses this to recover TUI content missing from the bounded raw-output replay; browser-only refresh cannot reconstruct discarded content.
- History replay is bracketed by `replay_begin` / `replay_end` WebSocket control messages. The frontend queues parser-context callbacks alongside xterm writes (not just WebSocket receipt flags) to suppress device/status/window-query responses from historical output. Live queries still receive responses, and queued output from an old connection cannot send replies on a replacement socket. Re-answering historical DA queries reproduced the visible `1;2c` / `0;276;0c` artifacts; `test_browser_tmux.py` verifies live DA replies, reconnect, reload and lazy tab restore.

## Systemd user service

A user service is installed for automatic startup:

- Service file: `~/.config/systemd/user/web-terminal.service`
- Check status: `systemctl --user status web-terminal`
- Start: `systemctl --user start web-terminal`
- Stop: `systemctl --user stop web-terminal`
- Restart: `systemctl --user restart web-terminal`
- Enable on boot: `systemctl --user enable web-terminal` (already enabled)
- Linger is enabled (`loginctl enable-linger gumy`) so the service starts at boot even before login.
- Independent tmux service: `web-terminal-tmux.service`, enabled at boot, linked from `.devin/systemd/web-terminal-tmux.service`; configuration is `.devin/tmux.conf`. It is not part of the web-terminal service cgroup and must not be stopped/restarted just to update the web server.
- Default dedicated socket: `$XDG_RUNTIME_DIR/web-terminal-tmux/default` (fallback `/run/user/<uid>/web-terminal-tmux/default`); override with `TMUX_SOCKET`. tmux is optional for PTY mode. Web requests use `tmux -N` so they never auto-start a tmux server inside the web service.
- Existing sessions from the user's default tmux socket are also discovered. Set `TMUX_NATIVE_SOCKET` to use another external socket instead. These imported sessions are attach-only: web close/delete detaches the web wrapper and never kills the external session. Integration tests always override this socket to avoid touching real user sessions.
- Installation: `systemctl --user link "$PWD/.devin/systemd/web-terminal-tmux.service" && systemctl --user enable --now web-terminal-tmux.service`. The unit's config path currently targets this checkout under `%h/Desktop/web-terminal`.

## Dependencies

Already installed in this environment:

- `tornado 6.5.4`
- `ptyprocess 0.7.0`
- `tmux 3.6` (required only for the tmux backend)

No additional packages are required.

## Architecture

- `server.py`: Tornado HTTP + WebSocket server; manages `Session` objects, each wrapping a `PtyProcess`. PTY input goes through a per-session writer thread so a child that stops reading cannot stall the IOLoop; each client is dropped if >`MAX_WS_PENDING` (8 MB) of unwritten output piles up.
- PTY master I/O uses nonblocking `os.read`/`os.write` with select-based reader/writer threads. Writes handle partial results. The reader never calls `isalive()` or reaps children; `Session.close()` stops the threads, schedules a single background `_reap()`, and returns an idempotent cleanup future. Shutdown waits for outstanding cleanup futures.
- Input limits: 64 KiB UTF-8 per message, 1 MiB queued/in-flight per session plus the existing message-count cap. Invalid or congested input returns a nonfatal `request_error`, never silently drops input. The browser chunks input on Unicode boundaries and refuses pastes over 262144 UTF-16 code units. PTY-to-IOLoop pending output is capped at 1 MiB; readers pause until callbacks release the budget, without dropping output.
- Async integration tests patch their session registry, lock, cleanup-future set and both tmux sockets, then await cleanup before closing the test loop. This prevents cross-loop locks and process teardown races. `test_server.py` covers queue limits, partial writes, output callback budgets and nonblocking/idempotent cleanup; `test_tmux.py` also exercises a 2 MiB output burst followed by input.
- `index.html`: Browser terminal UI shell using xterm.js (loaded from local `static/`).
- `static/app.js`: all frontend logic (tabs, settings, i18n, touch scrolling). xterm instances are created lazily per tab via `ensureTerm()` — `tab.term`/`fitAddon`/`searchAddon` are null until the tab is first activated.
- `test_client.py`: Async functional tests using `tornado.websocket.websocket_connect`.
- `tmux_backend.py`: bounded subprocess operations on dedicated and native tmux sockets. Managed sessions initially use `wt_<id>` and store their web identity in `@web_terminal_id` so native renames preserve identity. Imported identities include server PID and tmux session ID, and commands target immutable tmux IDs rather than display names. Calls run off the IOLoop. `GET /api/backends` reports backend availability for creation.
- Session objects and `GET /api/sessions` include `backend: "pty" | "tmux"`. Select it with `?backend=` on WebSocket connections and DELETE requests. A session ID cannot belong to both backends.
- Desktop tabs live in the left sidebar, filtered by `PTY / tmux / All`; the group is stored in `webterm-group`. On small screens, the Sessions button toggles an overlay. New-session buttons select a backend explicitly, while Alt+N follows the current group (All defaults to PTY).
- Closing a browser or stopping the web server preserves tmux sessions; explicit close/delete kills only managed tmux sessions. Managed tmux tab close and group close require confirmation; group close affects only visible sessions, and imported tmux sessions are detached instead of killed.
- The sidebar and toolbar display server-owned session names; PTY tabs fall back to terminal OSC titles when no shared name is set. Renaming a tmux session updates its actual tmux name (including explicit rename of imported sessions), preserves its identity, and survives web-server restart. PTY names live with the PTY session. Renames broadcast `session_info` with the target `id` to all connected clients so PC/mobile and lazy tabs synchronize. Old `webterm-titles` browser aliases are ignored; users can reapply them through shared rename.

## Notes

- Each session is keyed by a `session` query parameter.
- Closing the WebSocket (browser tab close) does **not** terminate the PTY.
- Sending `{"type":"close"}` over the WebSocket terminates the PTY and removes the session. The browser UI confirms termination, then uses HTTP DELETE and removes the tab only after success (or 404).
- A `session_ready` WebSocket control message indicates successful session attachment; binary frames carry terminal output. Always synchronize the current frontend dimensions after WebSocket open, even if fit() reports no change.
- The bottom status bar distinguishes connecting, connected, reconnecting, disconnected, ended and error states; ended tabs retain output and offer creation of a fresh terminal.
- A web-server restart still ends direct PTY sessions, but tmux sessions are enumerated from the independent server and can be reattached. This does not preserve sessions across a machine reboot or tmux-server restart.
- The server binds to `127.0.0.1:8765` by default; set `HOST`/`PORT` env vars to change.
- Child shells are spawned with `TERM=xterm-256color` and `COLORTERM=truecolor` by default. Override with `WEB_TERMINAL_TERM` and `WEB_TERMINAL_COLORTERM`.
- Default PTY dimensions are 80 columns x 24 rows (`TERMINAL_COLS` / `TERMINAL_ROWS`). The browser frontend resizes the PTY automatically when the container changes size.
- `MAX_SESSIONS` (default `32`, `0` = unlimited) caps the number of concurrent sessions.
- `XHEADERS=1` makes the server honor `X-Real-Ip`/`X-Forwarded-For` (behind a trusted reverse proxy only).
- `index.html` is served with `Cache-Control: no-store`.
- xterm.js, xterm-addon-fit, and xterm-addon-search are served from `static/` for offline use and SRI has been removed for local files.
- Shortcuts: `Alt+N` new tab, `Alt+W` close tab, `Ctrl+Shift+F` search, `F3`/`Shift+F3` next/previous. `Ctrl+Shift+T`/`Ctrl+Shift+W` are kept as a fallback but browsers reserve them (page never sees the event).
- Double-click a tab title to rename it.
- Mobile devices get a compact `body.mobile` style and a bottom shortcut bar with modifier keys (Ctrl/Alt/Shift can combine and arm once for the next input, including shortcut buttons) plus Esc, Tab, arrows, Home/End/PgUp/PgDn and hard-to-type symbols.
- Mobile and desktop settings are stored separately in `localStorage` based on `pointer: coarse` detection.
