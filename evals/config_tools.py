from __future__ import annotations

import re
from pathlib import Path


def update_model_constant(config_path: Path, name: str, value: str) -> None:
    """
    Update a top-level constant assignment like:
      TEXT_MODEL = "..."
      VISION_MODEL = "..."
    """
    text = config_path.read_text("utf-8")
    pat = re.compile(rf"^(?P<k>{re.escape(name)})\s*=\s*\"[^\"]*\"\s*$", re.MULTILINE)
    if not pat.search(text):
        raise ValueError(f"Could not find {name} assignment in {config_path}")
    text2 = pat.sub(f'{name} = "{value}"', text, count=1)
    config_path.write_text(text2, "utf-8")

