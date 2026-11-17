#!/usr/bin/env python3
"""Probe this workstation and emit ``config/runtime.local.yaml``.

Why this exists
---------------
``config/runtime.yaml`` is the production contract for the lab machine: it
names one fixed dialogue model on :8000, one fixed Agent model on :8001, and
``strict_preflight: true``.  Copying that file to another machine makes the
application refuse to start, because none of those endpoints or weights exist
there.

This script inspects the machine instead of assuming it, then writes a
*separate* file (``config/runtime.local.yaml``, git-ignored) that the local
launcher points at through ``VOICECHAT_RUNTIME_CONFIG``.  The production
contract stays untouched, so an experiment run on the lab machine keeps
byte-identical settings.

What it decides
---------------
1. dialogue endpoint -- the first reachable OpenAI-compatible ``/v1`` server
   among the candidates below.  A real model server always wins; the offline
   stub (:8010) only matches when the launcher has already started it.
2. dialogue model   -- the model id the endpoint actually serves.  The name
   from ``config/runtime.yaml`` is preferred when that server really has it.
3. Agent endpoint   -- a second reachable server, else disabled (the app then
   uses its deterministic keyword routing, which is a supported path).
4. strict_preflight -- true only when a real model server answered; the
   offline stub keeps it false so a stub crash cannot lock the GUI out.

Only the Python standard library is used: this runs *before* the virtual
environment has any dependencies installed.

Usage:
    python scripts/launch/autoconfigure.py            # write the config
    python scripts/launch/autoconfigure.py --check     # report only
    python scripts/launch/autoconfigure.py --json      # machine readable
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = APP_ROOT / "config" / "runtime.yaml"
LOCAL_CONFIG = APP_ROOT / "config" / "runtime.local.yaml"

STUB_MODEL_ID = "voicechat-offline-stub"
STUB_PORT = 8010

# Ordered by preference.  A server that actually holds weights must beat the
# stub, otherwise the stub would silently shadow a real deployment.
CANDIDATE_HOSTS = ("127.0.0.1",)
CANDIDATE_PORTS = (
    (8000, "llama.cpp / vLLM dialogue"),
    (8001, "llama.cpp / vLLM agent"),
    (8080, "vLLM"),
    (11434, "Ollama"),
    (STUB_PORT, "offline stub"),
)


# --------------------------------------------------------------------- probing
def _opener() -> urllib.request.OpenerDirector:
    """URL opener that never routes loopback through a system proxy."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe_models(host: str, port: int, timeout: float = 1.5) -> list[str] | None:
    """Return served model ids, or None when nothing answers on that port."""
    base = f"http://{host}:{port}/v1/models"
    try:
        with _opener().open(base, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, socket.timeout):
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    ids: list[str] = []
    for item in data or []:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
    return ids


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def wsl_hosts() -> list[str]:
    """Addresses of a running WSL2 distro, when this machine has one.

    On the lab machine the model servers run inside WSL2.  WSL's localhost
    forwarding usually makes them reachable as 127.0.0.1, but on some hosts
    that forwarding is broken and only the NAT address answers.  Probing both
    is cheaper than debugging the difference per machine.
    """
    import shutil
    import subprocess

    if not shutil.which("wsl"):
        return []
    try:
        completed = subprocess.run(
            ["wsl", "hostname", "-I"],
            capture_output=True, timeout=6, text=True,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    hosts: list[str] = []
    for token in completed.stdout.split():
        candidate = token.strip()
        if candidate and candidate not in hosts and ":" not in candidate:
            hosts.append(candidate)
    return hosts


# ------------------------------------------------------- model-name selection
def preferred_names() -> list[str]:
    """Model ids named by the production contract, most specific first.

    Only ``dialogue`` / ``agent`` sections count: the ``decision`` block also
    has a ``model`` key, but it is a decision checkpoint and never the
    dialogue model (and it is usually ``null``).
    """
    names: list[str] = []
    try:
        text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    except OSError:
        return names
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[:1].isspace():
            section = stripped.split(":", 1)[0]
            continue
        if section not in ("dialogue", "agent"):
            continue
        if not stripped.startswith("model:"):
            continue
        value = stripped.split(":", 1)[1].split("#", 1)[0].strip().strip("'\"")
        if value and value.lower() not in ("null", "none", "~") and value not in names:
            names.append(value)
    return names


# `embed` servers share the port namespace in some setups; they are never the
# dialogue model, so they are filtered out when anything else is served.
_NON_DIALOGUE_HINTS = ("embed", "rerank", "bge-", "gte-")


def pick_model(served: list[str], want: str | None = None) -> str | None:
    if not served:
        return None
    if want:
        for name in served:
            if name == want:
                return name
        for name in served:
            if want.lower() in name.lower():
                return name
    candidates = [
        name for name in served
        if not any(hint in name.lower() for hint in _NON_DIALOGUE_HINTS)
    ] or list(served)
    # Qwen is what the prompting in config.py is written against.
    for name in candidates:
        if "qwen" in name.lower():
            return name
    return candidates[0]


# ------------------------------------------------------------ model discovery
def _candidate_model_roots() -> list[Path]:
    """Places a machine may keep speech weights.

    Besides the two documented layouts (``<repo>/models`` and ``<parent>/models``)
    a workstation often collects them under a project folder elsewhere on disk
    (``D:\\<project>\\models``).  Those are discovered dynamically instead of
    hard-coding one machine's path: for every fixed drive, look at its immediate
    children and keep the ones that contain a ``models`` directory.
    """
    roots: list[Path] = []
    for fixed in (APP_ROOT / "models", APP_ROOT.parent / "models",
                  Path(os.environ.get("VOICECHAT_MODELS_DIR", "") or ".")):
        if fixed.is_dir() and fixed not in roots:
            roots.append(fixed)
    for letter in "CDEFGH":
        drive = Path(f"{letter}:/")
        try:
            if not drive.exists():
                continue
            with os.scandir(drive) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    nested = Path(entry.path) / "models"
                    if nested.is_dir() and nested not in roots:
                        roots.append(nested)
        except OSError:
            continue
    return roots


def find_model_dir(patterns: tuple[str, ...], markers: tuple[str, ...],
                   max_depth: int = 3) -> Path | None:
    """Depth-bounded search for a weight directory carrying one of `markers`."""
    for root in _candidate_model_roots():
        base_depth = len(root.resolve().parts)
        try:
            for current, dirs, files in os.walk(root, followlinks=False):
                depth = len(Path(current).parts) - base_depth
                if depth >= max_depth:
                    dirs[:] = []
                    continue
                lowered = {name.lower() for name in files}
                if any(marker.lower() in lowered for marker in markers):
                    if any(pat.lower() in Path(current).name.lower() for pat in patterns):
                        return Path(current)
        except OSError:
            continue
    return None


def detect_voice_assets() -> dict[str, object]:
    funasr = find_model_dir(("fun-asr", "funasr"), ("model.pt", "model.py"))
    voxcpm = find_model_dir(("voxcpm",), ("config.json",))
    prompt = None
    for candidate in (APP_ROOT / "data" / "s1.flac", APP_ROOT / "data" / "s1.wav",
                      APP_ROOT / "data" / "s1.mp3"):
        if candidate.exists():
            prompt = candidate
            break
    if prompt is None:
        found_prompt = find_model_dir(("voice_prompt",), ("s1.mp3", "s1.wav", "s1.flac"))
        if found_prompt:
            prompt = found_prompt / ("s1.mp3" if (found_prompt / "s1.mp3").exists() else "s1.wav")
    return {
        "funasr": str(funasr) if funasr else None,
        "voxcpm": str(voxcpm) if voxcpm else None,
        "voice_prompt": str(prompt) if prompt else None,
    }


# ------------------------------------------------------------------- decision
def discover() -> dict[str, object]:
    """Probe every candidate endpoint and settle on one dialogue source."""
    want = preferred_names()
    hosts = list(CANDIDATE_HOSTS)
    for extra in wsl_hosts():
        if extra not in hosts:
            hosts.append(extra)

    # Dialogue before agent, so a machine running both picks :8000 first and a
    # machine running only :8001 still gets a usable (agent-sized) model.
    reachable: list[dict[str, object]] = []
    seen_models: set[str] = set()
    for port, label in CANDIDATE_PORTS:
        for host in hosts:
            served = probe_models(host, port)
            if not served:
                continue
            # The same server may answer on both 127.0.0.1 and the WSL NAT
            # address; keep one entry, preferring the loopback form.
            fingerprint = f"{port}:{','.join(sorted(served))}"
            if fingerprint in seen_models:
                continue
            seen_models.add(fingerprint)
            reachable.append({
                "host": host,
                "port": port,
                "label": label,
                "base_url": f"http://{host}:{port}/v1",
                "models": served,
                "is_stub": port == STUB_PORT,
            })

    real = [item for item in reachable if not item["is_stub"]]
    chosen = real[0] if real else (reachable[0] if reachable else None)

    dialogue_model = None
    agent = None
    if chosen:
        dialogue_model = pick_model(chosen["models"], want[0] if want else None)
        # A second real server (not the dialogue one, not the stub) can host the
        # Agent; otherwise the app falls back to keyword routing.
        for item in real:
            if item is chosen:
                continue
            agent_model = pick_model(item["models"])
            if agent_model:
                agent = {"base_url": item["base_url"], "model": agent_model}
                break

    return {
        "endpoints": reachable,
        "chosen": chosen,
        "dialogue_model": dialogue_model,
        "preferred_models": want,
        "agent": agent,
        "voice": detect_voice_assets(),
        "stub_running": any(item["is_stub"] for item in reachable),
        "stub_port_free": not port_in_use("127.0.0.1", STUB_PORT),
    }


# --------------------------------------------------------------------- render
def render_yaml(state: dict[str, object]) -> str:
    chosen = state["chosen"]
    stub_mode = bool(chosen and chosen.get("is_stub"))
    base_url = chosen["base_url"] if chosen else "http://127.0.0.1:8010/v1"
    model = state["dialogue_model"] or STUB_MODEL_ID
    agent = state["agent"]

    lines = [
        "# ============================================================================",
        "# AUTO-GENERATED by scripts/launch/autoconfigure.py -- do not edit by hand.",
        "# ============================================================================",
        "# This file is written for THIS machine. The production contract lives in",
        "# config/runtime.yaml and is never modified by the local launcher.",
        "# Regenerate with:  .venv\\Scripts\\python.exe scripts\\launch\\autoconfigure.py",
        "# ============================================================================",
        "",
        "dialogue:",
        "  provider: openai_compatible",
    ]
    if chosen and not stub_mode:
        engine = "llamacpp" if chosen["port"] in (8000, 8001) else "vllm"
        lines.append(f"  engine: {engine}")
    lines += [
        f"  base_url: {base_url}",
        f"  model: {model}",
        "  api_key: EMPTY",
        "  timeout: 120",
        "  max_tokens: 1024",
        "  temperature: 0.35",
        "  top_p: 0.8",
        "  top_k: null",
        "  presence_penalty: null",
        "  enable_thinking: false",
        "  request_mode: chat",
        "  system_role_mode: native",
        "  system_prompt_override: null",
        "",
        "agent:",
    ]
    if agent:
        lines += [
            "  enabled: true",
            "  provider: openai_compatible",
            f"  base_url: {agent['base_url']}",
            f"  model: {agent['model']}",
            "  api_key: EMPTY",
            "  timeout: 10",
            "  temperature: 0.1",
        ]
    else:
        lines += [
            "  # No second model server found: the app uses its deterministic",
            "  # keyword routing, which is a supported (documented) fallback path.",
            "  enabled: false",
            "  provider: openai_compatible",
            "  base_url: http://127.0.0.1:8001/v1",
            "  model: disabled",
            "  api_key: EMPTY",
            "  timeout: 10",
            "  temperature: 0.1",
        ]

    voice = state["voice"]

    def _yaml_path(value: str | None) -> str:
        """Single-quote Windows paths: YAML double quotes would eat backslashes."""
        if not value or value == "auto":
            return "auto"
        return "'" + str(value).replace("'", "''") + "'"

    funasr_path = _yaml_path(voice["funasr"])
    voxcpm_path = _yaml_path(voice["voxcpm"])
    prompt_path = _yaml_path(voice["voice_prompt"])
    lines += [
        "",
        "asr:",
        "  provider: funasr",
        f"  model_path: {funasr_path}",
        "",
        "tts:",
        "  provider: voxcpm",
        f"  model_path: {voxcpm_path}",
        f"  voice_prompt_path: {prompt_path}",
        "",
        "runtime:",
        # Strict only when a real weight server answered. The stub must never be
        # able to lock the GUI out of starting.
        f"  strict_preflight: {'false' if stub_mode or not chosen else 'true'}",
        "  immutable_contract: true",
        "  manage_model_servers: false",
        "  enable_streaming_tts: true",
        "",
        "decision:",
        "  mode: legacy",
        "  backend: legacy_qwen",
        "  transport: server",
        "  timeout: 5.0",
        "  abstain_threshold: 0.55",
        "  escalation_mode: legacy",
        "  shadow_backend: null",
        "  endpoint: null",
        "  model: null",
        "  model_path: null",
        "  model_dtype: bfloat16",
        "  batch: true",
        "  use_grammar: true",
        "  allow_remote_endpoint: false",
        "  allow_model_download: false",
        "  journal_path: null",
        "  clinical_review:",
        "    enabled: false",
        "",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------- main
def summarize(state: dict[str, object]) -> str:
    out = []
    chosen = state["chosen"]
    voice = state["voice"]
    if chosen:
        kind = "offline stub (rehearsal only)" if chosen.get("is_stub") else chosen["label"]
        out.append(f"  对话服务 : {chosen['base_url']}  [{kind}]")
        out.append(f"  对话模型 : {state['dialogue_model']}")
    else:
        out.append("  对话服务 : 无（未探测到任何 /v1 服务）")
    agent = state.get("agent")
    if agent:
        out.append(f"  Agent    : {agent['base_url']}  {agent['model']}")
    else:
        out.append("  Agent    : 未配置（使用关键词兜底路由）")
    out.append(f"  FunASR   : {voice['funasr'] or '未找到（STT 关闭）'}")
    out.append(f"  VoxCPM2  : {voice['voxcpm'] or '未找到（TTS 关闭）'}")
    out.append(f"  音色提示 : {voice['voice_prompt'] or '未找到'}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report only, write nothing")
    parser.add_argument("--json", action="store_true", help="print the probe result as JSON")
    parser.add_argument("--quiet", action="store_true", help="suppress the human summary")
    args = parser.parse_args(argv)

    state = discover()

    # ``--json`` is the one-shot mode used by the application at startup: write
    # the config *and* report what was found, in a single probe.
    if args.json:
        if not args.check:
            LOCAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            LOCAL_CONFIG.write_text(render_yaml(state), encoding="utf-8")
        print(json.dumps(state, ensure_ascii=False))
        return 0

    if not args.quiet:
        print("本机运行环境探测：")
        print(summarize(state))

    if args.check:
        return 0

    LOCAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_CONFIG.write_text(render_yaml(state), encoding="utf-8")
    if not args.quiet:
        print(f"\n已写入 {LOCAL_CONFIG.relative_to(APP_ROOT)}")
        chosen = state["chosen"]
        if chosen and chosen.get("is_stub"):
            print("注意：当前指向离线桩服务，仅用于界面/流程联调，不可用于真实访谈。")
        elif not chosen:
            print("注意：未发现模型服务。启动器会拉起离线桩，或改用真实端点。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
