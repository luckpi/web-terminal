#!/usr/bin/env python3
"""Simple functional tests for the persistent web terminal."""

import asyncio
import json
import os
import re

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.websocket import websocket_connect

HOST = "ws://127.0.0.1:8765"
TOKEN = os.environ.get("TOKEN", "")


def ws_url(path):
    if not TOKEN:
        return f"{HOST}{path}"
    sep = "&" if "?" in path else "?"
    return f"{HOST}{path}{sep}token={TOKEN}"


async def collect_output(ws, idle_timeout=0.6, max_wait=5.0):
    """Read messages until no message arrives for idle_timeout seconds."""
    chunks = []
    start = asyncio.get_event_loop().time()
    while True:
        remaining = max_wait - (asyncio.get_event_loop().time() - start)
        if remaining <= 0:
            break
        try:
            msg = await asyncio.wait_for(ws.read_message(), timeout=idle_timeout)
        except asyncio.TimeoutError:
            break
        if msg is None:
            break
        chunks.append(msg if isinstance(msg, bytes) else msg.encode("utf-8"))
    return b"".join(chunks)


async def test_basic_io():
    print("--- test_basic_io ---")
    ws = await websocket_connect(ws_url("/ws?session=test_basic"))
    await asyncio.sleep(0.3)
    banner = await collect_output(ws, idle_timeout=0.5)
    print("banner:", repr(banner[:200]))

    await ws.write_message(json.dumps({"type": "input", "data": "echo hello_terminal\n"}))
    out = await collect_output(ws, idle_timeout=0.5)
    print("output:", repr(out[:300]))
    assert b"hello_terminal" in out

    await ws.write_message(json.dumps({"type": "close"}))
    await asyncio.sleep(0.3)
    ws.close()
    print("OK\n")


async def test_persistence():
    print("--- test_persistence ---")
    session = "test_persist"

    ws1 = await websocket_connect(ws_url(f"/ws?session={session}"))
    await asyncio.sleep(0.3)
    await collect_output(ws1, idle_timeout=0.5)

    await ws1.write_message(
        json.dumps(
            {
                "type": "input",
                "data": "sleep 60 &\n",
            }
        )
    )
    await collect_output(ws1, idle_timeout=0.8)

    # Disconnect (this is what happens when the browser tab is closed).
    ws1.close()
    await asyncio.sleep(0.5)

    # Reconnect to the same session.
    ws2 = await websocket_connect(ws_url(f"/ws?session={session}"))
    await asyncio.sleep(0.3)
    replay = await collect_output(ws2, idle_timeout=0.8)
    print("reconnect replay length:", len(replay))
    assert len(replay) > 0

    # The shell should still be alive and the sleep process should exist.
    await ws2.write_message(
        json.dumps({"type": "input", "data": "ps aux | grep '[s]leep 60'\n"})
    )
    out = await collect_output(ws2, idle_timeout=0.8)
    print("ps output:", repr(out[:500]))
    assert b"sleep 60" in out, "background process did not survive disconnect"
    await ws2.write_message(json.dumps({"type": "close"}))
    await asyncio.sleep(0.3)
    ws2.close()
    print("OK\n")


async def test_close():
    print("--- test_close ---")
    session = "test_close"

    ws = await websocket_connect(ws_url(f"/ws?session={session}"))
    await asyncio.sleep(0.3)
    await collect_output(ws, idle_timeout=0.5)

    await ws.write_message(json.dumps({"type": "input", "data": "echo before_close\n"}))
    await collect_output(ws, idle_timeout=0.5)

    # Send the explicit close message that the "Close Terminal" button sends.
    await ws.write_message(json.dumps({"type": "close"}))
    await asyncio.sleep(0.6)

    # Reconnecting to the same session must start a brand-new PTY.
    ws2 = await websocket_connect(ws_url(f"/ws?session={session}"))
    await asyncio.sleep(0.5)
    await ws2.write_message(json.dumps({"type": "input", "data": "echo after_close\n"}))
    out = await collect_output(ws2, idle_timeout=0.8)
    print("new terminal output:", repr(out[:500]))
    assert b"after_close" in out

    await ws2.write_message(json.dumps({"type": "close"}))
    await asyncio.sleep(0.3)
    ws2.close()
    print("OK\n")


async def test_resize():
    print("--- test_resize ---")
    ws = await websocket_connect(ws_url("/ws?session=test_resize"))
    await asyncio.sleep(0.3)
    await collect_output(ws, idle_timeout=0.5)

    await ws.write_message(json.dumps({"type": "resize", "rows": 33, "cols": 111}))
    await asyncio.sleep(0.3)
    await ws.write_message(json.dumps({"type": "input", "data": "stty size\n"}))
    out = await collect_output(ws, idle_timeout=0.8)
    print("stty size:", repr(out[:200]))
    assert b"33 111" in out

    # Out-of-range resize values are ignored.
    await ws.write_message(json.dumps({"type": "resize", "rows": 99999, "cols": -1}))
    await asyncio.sleep(0.3)
    await ws.write_message(json.dumps({"type": "input", "data": "stty size\n"}))
    out = await collect_output(ws, idle_timeout=0.8)
    assert b"33 111" in out

    await ws.write_message(json.dumps({"type": "close"}))
    await asyncio.sleep(0.3)
    ws.close()
    print("OK\n")


async def test_http_api():
    print("--- test_http_api ---")
    client = AsyncHTTPClient()

    # Open a session so it shows up in the API listing.
    ws = await websocket_connect(ws_url("/ws?session=test_api"))
    await asyncio.sleep(0.3)
    await collect_output(ws, idle_timeout=0.5)

    headers = {"X-Token": TOKEN} if TOKEN else {}
    resp = await client.fetch("http://127.0.0.1:8765/api/sessions", headers=headers)
    sessions = json.loads(resp.body.decode())
    ids = [s["id"] for s in sessions]
    print("sessions:", ids)
    assert "test_api" in ids

    # DELETE removes the session.
    req = HTTPRequest(
        f"http://127.0.0.1:8765/api/sessions/test_api",
        method="DELETE", headers=headers,
    )
    resp = await client.fetch(req)
    assert resp.code == 204
    ws.close()
    print("OK\n")


async def test_auth_rejection():
    """Only meaningful when the server runs with TOKEN set."""
    print("--- test_auth_rejection ---")
    if not TOKEN:
        print("TOKEN unset, skipping\n")
        return
    client = AsyncHTTPClient()

    # Bad token -> 403 on API and static files; no token on / redirects to /login.
    resp = await client.fetch(
        "http://127.0.0.1:8765/api/sessions?token=wrong", raise_error=False)
    assert resp.code == 403, resp.code
    resp = await client.fetch(
        "http://127.0.0.1:8765/static/xterm.css?token=wrong", raise_error=False)
    assert resp.code == 403, resp.code
    resp = await client.fetch(
        "http://127.0.0.1:8765/", raise_error=False, follow_redirects=False)
    assert resp.code == 302, resp.code

    # WS handshake with bad token is rejected (error message then close).
    try:
        ws = await websocket_connect("ws://127.0.0.1:8765/ws?session=x&token=wrong")
    except Exception:
        ws = None
    else:
        msg = await ws.read_message()
        assert msg is None or b"token" in (
            msg if isinstance(msg, bytes) else msg.encode()), msg
        ws.close()
    print("OK\n")


async def main():
    await test_basic_io()
    await test_persistence()
    await test_close()
    await test_resize()
    await test_http_api()
    await test_auth_rejection()
    print("All tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
