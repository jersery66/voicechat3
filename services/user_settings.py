"""User-editable settings that survive restarts, and can be edited by hand.

Everything the operator is likely to need on a specific machine lives here —
which microphone to use, which speaker to play through, how large the interface
should be — because those are exactly the values that differ from machine to
machine and that a technician should be able to fix without a code change.

The file is plain JSON at ``config/user_settings.json`` and is deliberately
forgiving: a hand-edited typo falls back to the default for that key with a
logged warning instead of refusing to start, and keys this module does not know
about are preserved on save so a future option is not silently dropped.

Precedence for the microphone is: ``VOICECHAT_INPUT_DEVICE`` (testing override)
> ``audio.input_device`` here > automatic selection in ``stt_service``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Optional

APP_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_ENV = "VOICECHAT_USER_SETTINGS"

DEFAULTS: dict[str, Any] = {
    "_readme": [
        "本文件可直接用记事本编辑，保存后重启程序生效。",
        "audio.input_device: \"auto\" 表示自动挑选麦克风；也可以填设备序号，或设备名里的一段文字（如 \"USB\"）。",
        "audio.output_device: 同上，用于播放语音（\"default\" 表示系统默认扬声器）。",
        "ui.scale: 界面缩放，0.75–1.6。屏幕较小或系统缩放较大时可用 0.85。",
        "ui.start_maximized: 启动时是否最大化。",
        "删除本文件即可恢复全部默认值。",
    ],
    "version": 1,
    "ui": {
        "scale": 1.0,
        "start_maximized": True,
        "start_width": 1280,
        "start_height": 800,
    },
    "audio": {
        "input_device": "auto",
        "output_device": "default",
    },
    "asr": {
        "auto_stop_on_silence": True,
    },
}

_SCALE_RANGE = (0.75, 1.6)


def settings_path() -> Path:
    override = os.environ.get(SETTINGS_ENV, "").strip()
    if override:
        return Path(override)
    return APP_ROOT / "config" / "user_settings.json"


def _merge(base: dict, override: dict) -> dict:
    """Deep merge that keeps unknown keys from ``override``."""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _coerce(settings: dict) -> tuple[dict, list[str]]:
    """Clamp values that a hand edit could get wrong. Returns (settings, warnings)."""
    warnings: list[str] = []
    ui = settings.setdefault("ui", {})
    try:
        scale = float(ui.get("scale", 1.0))
    except (TypeError, ValueError):
        warnings.append(f"ui.scale 不是数字，已用 1.0（原值 {ui.get('scale')!r}）")
        scale = 1.0
    low, high = _SCALE_RANGE
    if not low <= scale <= high:
        clamped = min(max(scale, low), high)
        warnings.append(f"ui.scale={scale} 超出 {low}–{high}，已改为 {clamped}")
        scale = clamped
    ui["scale"] = round(scale, 2)

    for key, fallback in (("start_width", 1280), ("start_height", 800)):
        try:
            ui[key] = int(ui.get(key, fallback))
        except (TypeError, ValueError):
            warnings.append(f"ui.{key} 不是整数，已用 {fallback}")
            ui[key] = fallback
    ui["start_maximized"] = bool(ui.get("start_maximized", True))
    ui["start_width"] = max(800, min(ui["start_width"], 4096))
    ui["start_height"] = max(560, min(ui["start_height"], 2160))

    audio = settings.setdefault("audio", {})
    for key, fallback in (("input_device", "auto"), ("output_device", "default")):
        value = audio.get(key, fallback)
        if not isinstance(value, (str, int)):
            warnings.append(f"audio.{key} 应为字符串或序号，已用 {fallback}")
            value = fallback
        audio[key] = value

    asr = settings.setdefault("asr", {})
    asr["auto_stop_on_silence"] = bool(asr.get("auto_stop_on_silence", True))
    return settings, warnings


class UserSettings:
    """Load / edit / save one settings file."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else settings_path()
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.warnings: list[str] = []
        self.loaded_from_file = False

    # ------------------------------------------------------------------ io
    def load(self) -> "UserSettings":
        self.warnings = []
        if not self.path.is_file():
            # Write the template once so the file exists to be hand-edited.
            self.save()
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            self.warnings.append(f"配置文件无法解析，已使用默认值：{exc}")
            self.data = copy.deepcopy(DEFAULTS)
            return self
        if not isinstance(raw, dict):
            self.warnings.append("配置文件内容不是对象，已使用默认值")
            self.data = copy.deepcopy(DEFAULTS)
            return self
        self.data = _merge(DEFAULTS, raw)
        self.data, coerce_warnings = _coerce(self.data)
        self.warnings.extend(coerce_warnings)
        self.loaded_from_file = True
        return self

    def save(self) -> bool:
        """Atomic write: a crash mid-save must not leave a truncated file."""
        self.data, coerce_warnings = _coerce(self.data)
        self.warnings.extend(coerce_warnings)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            self.warnings.append(f"配置保存失败：{exc}")
            return False

    # --------------------------------------------------------------- access
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def update(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            self.set(key, value)

    def reset(self) -> None:
        self.data = copy.deepcopy(DEFAULTS)

    # ------------------------------------------------------------ raw text
    def raw_text(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2)

    def replace_raw(self, text: str) -> tuple[bool, str]:
        """Apply hand-edited JSON. Returns (ok, message)."""
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            return False, f"JSON 语法错误：{exc}"
        if not isinstance(parsed, dict):
            return False, "顶层必须是一个 JSON 对象"
        merged = _merge(DEFAULTS, parsed)
        merged, warnings = _coerce(merged)
        self.data = merged
        return True, ("已应用" + (f"（{len(warnings)} 处已修正）" if warnings else ""))

    # ------------------------------------------------------------- helpers
    @property
    def scale(self) -> float:
        return float(self.get("ui.scale", 1.0))

    @property
    def input_device(self) -> str:
        value = self.get("audio.input_device", "auto")
        return str(value).strip() if value is not None else "auto"

    @property
    def output_device(self) -> str:
        value = self.get("audio.output_device", "default")
        return str(value).strip() if value is not None else "default"


def load_settings() -> UserSettings:
    return UserSettings().load()
