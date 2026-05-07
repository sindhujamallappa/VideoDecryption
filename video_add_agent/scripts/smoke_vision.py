"""Probe multiple multimodal payload shapes against the UiPath LLM Gateway.

Iterates several candidate image-block shapes to find one the gateway
accepts. Prints the response and the winning shape.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from langchain_core.messages import HumanMessage, SystemMessage
from PIL import Image, ImageDraw

from video_add_agent.utils.llm import build_llm

logging.basicConfig(level=logging.WARNING)


def _tiny_jpeg() -> bytes:
    img = Image.new("RGB", (64, 64), color="white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 8, 56, 56], fill="red", outline="black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _shapes(image_bytes: bytes) -> list[tuple[str, dict]]:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:image/jpeg;base64,{b64}"
    return [
        ("anthropic_native_image_type", {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        }),
        ("media_type_with_source_obj", {
            "type": "image/jpeg",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        }),
        ("media_type_flat_data", {
            "type": "image/jpeg",
            "data": b64,
        }),
        ("media_type_with_image_url", {
            "type": "image/jpeg",
            "image_url": {"url": data_url},
        }),
        ("media_type_with_url", {
            "type": "image/jpeg",
            "url": data_url,
        }),
        ("media_type_with_image_field", {
            "type": "image/jpeg",
            "image": b64,
        }),
        ("media_type_source_just_data", {
            "type": "image/jpeg",
            "source": {"data": b64},
        }),
    ]


async def main() -> int:
    image_bytes = _tiny_jpeg()
    print(f"image: {len(image_bytes)} bytes\n")

    llm = build_llm(model_name="anthropic.claude-opus-4-7")
    for name, block in _shapes(image_bytes):
        content = [
            {"type": "text", "text": "What colour is the rectangle? One word."},
            block,
        ]
        try:
            result = await llm.ainvoke([
                SystemMessage(content="Test."),
                HumanMessage(content=content),
            ])
            print(f"[OK]   {name}: {str(result.content).strip()[:120]}")
            print(f"\nWINNER: {name}")
            return 0
        except Exception as exc:
            msg = str(exc)
            short = msg.split("Message': ")[1][:160] if "Message': " in msg else msg[:160]
            print(f"[FAIL] {name}: {short}")
    print("\nAll shapes failed.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
