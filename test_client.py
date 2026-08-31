#!/usr/bin/env python3
"""Simple functional tests for the persistent web terminal."""

import asyncio
import json
import re

from tornado.websocket import websocket_connect

HOST = "ws://127.0.0.1:8765"


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
    ws = await websocket_connect(f"{HOST}/ws?session=test_basic")
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

    ws1 = await websocket_connect(f"{HOST}/ws?session={session}")
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
    ws2 = await websocket_connect(f"{HOST}/ws?session={session}")
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

    ws = await websocket_connect(f"{HOST}/ws?session={session}")
    await asyncio.sleep(0.3)
    await collect_output(ws, idle_timeout=0.5)

    await ws.write_message(json.dumps({"type": "input", "data": "echo before_close\n"}))
    await collect_output(ws, idle_timeout=0.5)

    # Send the explicit close message that the "Close Terminal" button sends.
    await ws.write_message(json.dumps({"type": "close"}))
    await asyncio.sleep(0.6)

    # Reconnecting to the same session must start a brand-new PTY.
    ws2 = await websocket_connect(f"{HOST}/ws?session={session}")
    await asyncio.sleep(0.5)
    await ws2.write_message(json.dumps({"type": "input", "data": "echo after_close\n"}))
    out = await collect_output(ws2, idle_timeout=0.8)
    print("new terminal output:", repr(out[:500]))
    assert b"after_close" in out

    await ws2.write_message(json.dumps({"type": "close"}))
    await asyncio.sleep(0.3)
    ws2.close()
    print("OK\n")


async def main():
    await test_basic_io()
    await test_persistence()
    await test_close()
    print("All tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
