"""Start / stop / inspect the decision service from Python.

Shared by the offline acceptance probe and the benchmark, so both exercise the
same lifecycle code the shell script uses: an independent process, a loopback
port, a health gate before any request is sent, and the option to attach to an
already-running service instead of spawning one.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ServiceStartError(RuntimeError):
    """The service did not become healthy."""


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def http_json(url: str, *, timeout: float = 5.0, payload: Optional[dict[str, Any]] = None) -> Any:
    """Loopback JSON call that bypasses any system proxy."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with _NO_PROXY.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


@dataclass
class LocalDecisionService:
    """A running decision service, spawned or external."""

    base_url: str
    process: Optional[subprocess.Popen] = None
    log_path: Optional[Path] = None
    started_at: float = field(default_factory=time.time)
    spawned: bool = False

    # -- construction -----------------------------------------------------

    @classmethod
    def attach(cls, endpoint: str) -> "LocalDecisionService":
        url = endpoint.rstrip("/")
        return cls(base_url=url)

    @classmethod
    def spawn(
        cls,
        *,
        model_path: str,
        manifest: Optional[str] = None,
        dtype: str = "bfloat16",
        device: str = "auto",
        port: Optional[int] = None,
        mode: Optional[str] = None,
        rotations: Optional[int] = None,
        temperature: Optional[float] = None,
        python: Optional[str] = None,
        project_root: Optional[str] = None,
        log_dir: Optional[str] = None,
        extra_args: tuple[str, ...] = (),
    ) -> "LocalDecisionService":
        root = Path(project_root or Path(__file__).resolve().parents[2])
        interpreter = python or sys.executable
        chosen_port = int(port or free_port())
        logs = Path(log_dir) if log_dir else root / "test_output" / "decision_service"
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs / f"service_{chosen_port}.log"

        command = [
            interpreter,
            "-m",
            "decision.service",
            "--model-path",
            str(model_path),
            "--port",
            str(chosen_port),
            "--dtype",
            dtype,
            "--device",
            device,
        ]
        if manifest:
            command += ["--manifest", str(manifest)]
        if mode:
            command += ["--mode", mode]
        if rotations is not None:
            command += ["--rotations", str(rotations)]
        if temperature is not None:
            command += ["--temperature", str(temperature)]
        command += list(extra_args)

        env = dict(os.environ)
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONPATH", str(root))
        # Keep CUDA caching allocator behaviour predictable across runs.
        env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

        handle = log_path.open("w", encoding="utf-8", errors="replace")
        process = subprocess.Popen(
            command,
            cwd=str(root),
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=env,
        )
        return cls(
            base_url=f"http://127.0.0.1:{chosen_port}",
            process=process,
            log_path=log_path,
            spawned=True,
        )

    # -- lifecycle --------------------------------------------------------

    def wait_healthy(self, timeout: float = 180.0, interval: float = 0.5) -> dict[str, Any]:
        deadline = time.time() + timeout
        last_error = ""
        while time.time() < deadline:
            if self.process is not None and self.process.poll() is not None:
                raise ServiceStartError(
                    f"decision service exited with code {self.process.returncode}: "
                    f"{self.tail_log()}"
                )
            try:
                health = http_json(f"{self.base_url}/health", timeout=3.0)
                if str(health.get("status")) == "ok":
                    return health
                last_error = f"status={health.get('status')}"
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_error = str(exc)
            time.sleep(interval)
        raise ServiceStartError(
            f"decision service not healthy after {timeout:.0f}s ({last_error})"
            + (f": {self.tail_log()}" if self.log_path else "")
        )

    def health(self, timeout: float = 5.0) -> dict[str, Any]:
        return http_json(f"{self.base_url}/health", timeout=timeout)

    def stats(self, timeout: float = 5.0) -> dict[str, Any]:
        return http_json(f"{self.base_url}/v1/stats", timeout=timeout)

    def stop(self, timeout: float = 15.0) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5.0)
        self.process = None

    def tail_log(self, lines: int = 12) -> str:
        if not self.log_path or not self.log_path.is_file():
            return ""
        try:
            content = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return " | ".join(content[-lines:])

    def __enter__(self) -> "LocalDecisionService":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()
