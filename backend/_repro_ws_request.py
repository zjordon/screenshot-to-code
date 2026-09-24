"""Reproduce the frontend /generate-code WebSocket request and surface errors."""

import asyncio
import base64
import json
from pathlib import Path

import websockets

IMG = Path(__file__).parent / ".." / "test-screenshots" / "01-shadcn-dashboard.png"


async def main() -> None:
    data_url = "data:image/png;base64," + base64.b64encode(IMG.read_bytes()).decode()
    params = {
        "generatedCodeConfig": "html_tailwind",
        "inputMode": "image",
        "generationType": "create",
        "isImageGenerationEnabled": False,
        "isAssetExtractionEnabled": False,
        "prompt": {"text": "Recreate this dashboard", "images": [data_url]},
        "history": [],
    }

    async with websockets.connect(
        "ws://127.0.0.1:7001/generate-code", max_size=50 * 1024 * 1024
    ) as ws:
        await ws.send(json.dumps(params))
        counts: dict[str, int] = {}
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=300)
            except asyncio.TimeoutError:
                print("TIMEOUT waiting for messages")
                break
            except websockets.exceptions.ConnectionClosed as exc:
                print(f"CONNECTION CLOSED code={exc.code} reason={exc.reason!r}")
                break
            msg = json.loads(raw)
            msg_type = msg.get("type", "?")
            counts[msg_type] = counts.get(msg_type, 0) + 1
            if msg_type in ("error", "variantError"):
                print(f"[{msg_type}] variant={msg.get('variantIndex')} "
                      f"value={str(msg.get('value'))[:500]}")
            elif msg_type in ("variantCount", "variantModels", "variantComplete"):
                print(f"[{msg_type}] {str(msg)[:200]}")
        print("message counts:", counts)


asyncio.run(main())
