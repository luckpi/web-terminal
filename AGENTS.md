# Agent Notes — Web Terminal

This is a self-contained Python/Tornado web terminal.

## Commands

- Start server: `python3 server.py`
- Run tests: `python3 test_client.py`
- Server URL: `http://127.0.0.1:8765/`
- WebSocket endpoint: `ws://127.0.0.1:8765/ws?session=<id>`
- Optional token authentication: set `TOKEN` env and append `?token=<TOKEN>` to all URLs (or use `X-Token` header).
- Session management API: `GET /api/sessions`, `DELETE /api/sessions/<id>`.

## Systemd user service

A user service is installed for automatic startup:

- Service file: `~/.config/systemd/user/web-terminal.service`
- Check status: `systemctl --user status web-terminal`
- Start: `systemctl --user start web-terminal`
- Stop: `systemctl --user stop web-terminal`
- Restart: `systemctl --user restart web-terminal`
- Enable on boot: `systemctl --user enable web-terminal` (already enabled)
- Linger is enabled (`loginctl enable-linger gumy`) so the service starts at boot even before login.

## Dependencies

Already installed in this environment:

- `tornado 6.5.4`
- `ptyprocess 0.7.0`

No additional packages are required.

## Architecture

- `server.py`: Tornado HTTP + WebSocket server; manages `Session` objects, each wrapping a `PtyProcess`.
- `index.html`: Browser terminal UI using xterm.js (loaded from local `static/`).
- `test_client.py`: Async functional tests using `tornado.websocket.websocket_connect`.

## Notes

- Each session is keyed by a `session` query parameter.
- Closing the WebSocket (browser tab close) does **not** terminate the PTY.
- Sending `{"type":"close"}` over the WebSocket terminates the PTY and removes the session.
- The server binds to `127.0.0.1:8765` by default; set `HOST`/`PORT` env vars to change.
- Child shells are spawned with `TERM=xterm-256color` and `COLORTERM=truecolor` by default. Override with `WEB_TERMINAL_TERM` and `WEB_TERMINAL_COLORTERM`.
- Default PTY dimensions are 80 columns x 24 rows (`TERMINAL_COLS` / `TERMINAL_ROWS`). The browser frontend resizes the PTY automatically when the container changes size.
- `MAX_SESSIONS` (default `0`, unlimited) can cap the number of concurrent sessions.
- `index.html` is served with `Cache-Control: no-store`.
- xterm.js, xterm-addon-fit, and xterm-addon-search are served from `static/` for offline use and SRI has been removed for local files.
- Shortcuts: `Ctrl+Shift+T` new tab, `Ctrl+Shift+W` close tab, `Ctrl+Shift+F` search, `F3`/`Shift+F3` next/previous.
- Double-click a tab title to rename it.
- Mobile devices get a compact `body.mobile` style and a bottom shortcut bar with common keys (Ctrl+C, Esc, arrows, Tab, pipe, tilde, etc.).
- Mobile and desktop settings are stored separately in `localStorage` based on `pointer: coarse` detection.
