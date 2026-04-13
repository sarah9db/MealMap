import base64

from config import VISION_MODEL

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


def encode_image(uploaded_file) -> tuple[str, str]:
    """Return (base64_string, mime_type). Validates MIME type."""
    mime = uploaded_file.type if uploaded_file.type in ALLOWED_MIME_TYPES else "image/jpeg"
    uploaded_file.seek(0)
    b64 = base64.b64encode(uploaded_file.read()).decode("utf-8")
    return b64, mime


def analyze_ingredients(client, b64: str, mime: str) -> str:
    """Use vision model to list ingredients visible in an image."""
    response = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": "List every food ingredient you can see. Be specific."},
            ],
        }],
    )
    return response.choices[0].message.content
