"""VLM figure/table captioning.

When VLM_ENABLED=True, image blocks extracted from PDF pages are sent to a
multimodal LLM (e.g. deepseek-vl2, qwen-vl-plus, gpt-4o) for description.
The returned caption is stored as a TextBlock(block_type="figure") so it
flows through the normal chunking + retrieval pipeline.

All errors are non-fatal: on failure the image is silently skipped.
"""
import base64
from loguru import logger

from app.core.config import settings


def vlm_describe_image(img_bytes: bytes) -> str:
    """Send an image to the VLM and return a descriptive caption.

    Parameters
    ----------
    img_bytes : bytes
        Raw image bytes (PNG / JPEG, as returned by PyMuPDF).

    Returns
    -------
    str
        Caption text, or empty string if VLM is disabled / call fails.
    """
    if not settings.VLM_ENABLED:
        return ""
    if not settings.VLM_MODEL:
        return ""
    if len(img_bytes) < settings.VLM_MIN_IMAGE_BYTES:
        return ""  # skip tiny icons / decorations

    try:
        from app.services.llm_client import get_llm_client
        client = get_llm_client()

        b64 = base64.b64encode(img_bytes).decode("ascii")
        # Detect image type heuristically from magic bytes
        mime = _detect_mime(img_bytes)

        resp = client.chat.completions.create(
            model=settings.VLM_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{b64}",
                                "detail": "high",
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "You are an expert in energetic materials and propellant science. "
                                "Describe this figure or table from a scientific paper in 2-4 sentences. "
                                "Include: what type of figure/table it is, what quantities or entities "
                                "are shown, any key numerical values visible, and the main conclusion "
                                "that can be drawn. Be concise and factual."
                            ),
                        },
                    ],
                }
            ],
            max_tokens=300,
            timeout=30,
        )
        caption = (resp.choices[0].message.content or "").strip()
        return caption
    except Exception as e:
        logger.debug(f"VLM captioning failed (non-fatal): {e}")
        return ""


def _detect_mime(data: bytes) -> str:
    """Guess MIME type from leading magic bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    # Default to PNG for unknown (PyMuPDF usually outputs PNG)
    return "image/png"
