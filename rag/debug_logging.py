"""
Debug logging helpers for tracing LLM inputs and outputs.

The logs intentionally include full prompts and model output for local
development. Disable with KNONOTE_LOG_LLM_IO=0 when logs should stay compact.
"""

from __future__ import annotations

import json
import os
from typing import Any


_FALSE_VALUES = {"0", "false", "no", "off"}


def llm_io_logging_enabled() -> bool:
    return os.getenv("KNONOTE_LOG_LLM_IO", "1").strip().lower() not in _FALSE_VALUES


def to_log_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)
