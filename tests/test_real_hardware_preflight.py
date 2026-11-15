"""Deterministic evidence-state tests for the real hardware preflight.

The preflight used to require a vLLM executable unconditionally, so a
llama.cpp deployment could never pass. These tests pin the replacement
contract: the validation items follow the engine declared in the runtime
configuration, an undeclared engine is an error rather than a silent default,
and service health is a real check when it is requested.
"""

from __future__ import annotations

import json

from deployment import runtime_config as rc
from deployment.runtime_config import load_runtime_config
from scripts import real_hardware_preflight as preflight


# ---------------------------------------------------------------------------
# Evidence metadata
# ---------------------------------------------------------------------------

def test_measured_preflight_success_is_measured_and_pass():
    metadata = preflight._evidence_metadata(
        {
            "windows_gpu": {"status": "PASS"},
            "wsl_gpu": {"status": "PASS"},
            "torch_cuda": {"status": "PASS"},
            "inference_engine": {"status": "PASS"},
            "windows_wsl_gpu_consistency": {"status": "PASS"},
        },
        overall_status="PASS",
    )

    assert metadata == {
        "execution": "RAN",
        "evidence_type": "MEASURED",
        "status": "PASS",
        "hardware_validation": "PASS",
    }


def test_measured_preflight_failure_stays_measured_and_fail():
    metadata = preflight._evidence_metadata(
        {
            "windows_gpu": {"status": "PASS"},
            "wsl_gpu": {"status": "PASS"},
            "torch_cuda": {"status": "FAIL", "error": "CUDA unavailable"},
            "inference_engine": {"status": "PASS"},
            "windows_wsl_gpu_consistency": {"status": "PASS"},
        },
        overall_status="FAIL",
    )

    assert metadata["execution"] == "RAN"
    assert metadata["evidence_type"] == "MEASURED"
    assert metadata["status"] == "FAIL"
    assert metadata["hardware_validation"] == "FAIL"


def test_unavailable_preflight_is_not_run_evidence_not_measured_failure():
    metadata = preflight._evidence_metadata(
        {
            "windows_gpu": {"status": "NOT AVAILABLE"},
            "wsl_gpu": {"status": "NOT AVAILABLE"},
            "torch_cuda": {"status": "NOT RUN"},
            "inference_engine": {"status": "NOT RUN"},
            "windows_wsl_gpu_consistency": {"status": "NOT RUN"},
        },
        overall_status="FAIL",
    )

    assert metadata["execution"] == "RAN"
    assert metadata["evidence_type"] == "NOT AVAILABLE"
    assert metadata["status"] == "FAIL"
    assert metadata["hardware_validation"] == "NOT RUN"


# ---------------------------------------------------------------------------
# Engine-driven validation items
# ---------------------------------------------------------------------------

def _runtime_yaml(*, dialogue_engine: str | None = None, agent_engine: str | None = None,
                  dialogue_provider: str = "openai_compatible",
                  agent_enabled: bool = True) -> str:
    def line(key: str, value: str | None) -> str:
        return f"  {key}: {value}\n" if value else ""

    return (
        "dialogue:\n"
        f"  provider: {dialogue_provider}\n"
        + line("engine", dialogue_engine)
        + "  base_url: http://127.0.0.1:8000/v1\n"
        "  model: dialogue-model\n"
        "agent:\n"
        f"  enabled: {'true' if agent_enabled else 'false'}\n"
        "  provider: openai_compatible\n"
        + line("engine", agent_engine)
        + ("  base_url: http://127.0.0.1:8001/v1\n" "  model: agent-model\n" if agent_enabled else "")
        + "asr:\n"
        "  provider: funasr\n"
        "tts:\n"
        "  provider: voxcpm\n"
        "runtime:\n"
        "  immutable_contract: true\n"
    )


def _config(tmp_path, **kwargs):
    path = tmp_path / "runtime.yaml"
    path.write_text(_runtime_yaml(**kwargs), encoding="utf-8")
    return load_runtime_config(path)


class RecordingRunner:
    """Stand-in for ``_run`` that records commands and returns a fixed outcome."""

    def __init__(self, *, status: str = "PASS", stdout: str = "version: 0.5.0-dev",
                 stderr: str = "", returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self._status = status
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode

    def __call__(self, command, timeout_seconds):
        self.calls.append(list(command))
        return {
            "command": list(command),
            "returncode": self._returncode,
            "stdout": self._stdout,
            "stderr": self._stderr,
            "status": self._status,
        }

    def scripts(self) -> list[str]:
        return [call[-1] for call in self.calls]


def _engine_check(config, runner, *, executables=None, distro=None):
    return preflight._engine_check(
        config,
        distro=distro,
        executables=executables
        or {
            rc.ENGINE_LLAMA_CPP: "llama-server",
            rc.ENGINE_VLLM: "~/.venvs/voicechat-vllm/bin/vllm",
            rc.ENGINE_OLLAMA: "ollama",
        },
        runner=runner,
        timeout_seconds=5.0,
    )


def test_llama_cpp_deployment_checks_llama_server_not_vllm(tmp_path):
    runner = RecordingRunner()
    result = _engine_check(_config(tmp_path, dialogue_engine="llamacpp", agent_engine="llamacpp"), runner)

    assert result["status"] == "PASS"
    assert result["declared"] == {"dialogue": "llamacpp", "agent": "llamacpp"}
    scripts = runner.scripts()
    assert len(scripts) == 1, "the same engine must be probed once, not once per role"
    assert "llama-server" in scripts[0]
    assert "vllm" not in scripts[0]
    assert result["engines"]["llamacpp"]["version"] == "version: 0.5.0-dev"


def test_vllm_deployment_checks_the_vllm_executable(tmp_path):
    runner = RecordingRunner(stdout="0.9.2")
    result = _engine_check(_config(tmp_path, dialogue_engine="vllm", agent_engine="vllm"), runner)

    assert result["status"] == "PASS"
    script = runner.scripts()[0]
    assert "voicechat-vllm/bin/vllm" in script
    assert "llama-server" not in script
    assert result["engines"]["vllm"]["version"] == "0.9.2"


def test_home_relative_executable_path_stays_expandable():
    """A quoted tilde never expands, so the default vLLM path must survive."""
    home_relative = preflight._shell_executable("~/.venvs/voicechat-vllm/bin/vllm")
    assert home_relative.startswith("$HOME/")
    assert "'~" not in home_relative and '"~' not in home_relative
    assert preflight._shell_executable("llama-server") == "llama-server"
    assert preflight._shell_executable("/opt/llama.cpp/llama-server") == "/opt/llama.cpp/llama-server"


def test_both_engines_are_verified_when_roles_differ(tmp_path):
    runner = RecordingRunner()
    result = _engine_check(
        _config(tmp_path, dialogue_engine="llamacpp", agent_engine="vllm"), runner
    )

    assert result["status"] == "PASS"
    assert set(result["engines"]) == {"llamacpp", "vllm"}
    joined = " ".join(runner.scripts())
    assert "llama-server" in joined and "vllm" in joined


def test_undeclared_engine_fails_instead_of_defaulting_to_vllm(tmp_path):
    runner = RecordingRunner()
    result = _engine_check(_config(tmp_path, agent_enabled=False), runner)

    assert result["status"] == "FAIL"
    assert result["undeclared"] == ["dialogue"]
    assert "engine" in result["error"]
    assert runner.calls == [], "nothing may be probed when the engine is unknown"


def test_disabled_agent_is_not_required_to_declare_an_engine(tmp_path):
    runner = RecordingRunner()
    result = _engine_check(
        _config(tmp_path, dialogue_engine="llamacpp", agent_enabled=False), runner
    )

    assert result["status"] == "PASS"
    assert result["declared"] == {"dialogue": "llamacpp"}


def test_missing_binary_fails_with_the_engine_named(tmp_path):
    runner = RecordingRunner(
        status="FAIL", stdout="", stderr="bash: llama-server: command not found", returncode=127
    )
    result = _engine_check(
        _config(tmp_path, dialogue_engine="llamacpp", agent_engine="llamacpp"), runner
    )

    assert result["status"] == "FAIL"
    assert result["engines"]["llamacpp"]["status"] == "FAIL"
    assert result["engines"]["llamacpp"]["returncode"] == 127
    assert "llama.cpp" in result["errors"][0]
    assert "command not found" in result["errors"][0]


def test_missing_runtime_configuration_fails_the_engine_check():
    result = _engine_check(None, RecordingRunner())

    assert result["status"] == "FAIL"
    assert "runtime configuration" in result["error"]


# ---------------------------------------------------------------------------
# Service health
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False


def _opener(payload=None, *, error: Exception | None = None, by_url: dict | None = None):
    def open_(url, timeout=None):
        if by_url is not None:
            entry = by_url[url]
            if isinstance(entry, Exception):
                raise entry
            return FakeResponse(entry)
        if error is not None:
            raise error
        return FakeResponse(payload)

    return open_


def test_service_health_is_opt_in_and_never_silently_passes(tmp_path):
    result = preflight._service_health(
        _config(tmp_path, dialogue_engine="llamacpp"),
        expect_services=False,
        timeout_seconds=5.0,
        opener=_opener({"data": [{"id": "dialogue-model"}]}),
    )

    assert result["status"] == "NOT RUN"
    assert "expect-services" in result["reason"]


def test_service_health_passes_with_exact_model_identity(tmp_path):
    result = preflight._service_health(
        _config(tmp_path, dialogue_engine="llamacpp"),
        expect_services=True,
        timeout_seconds=5.0,
        opener=_opener(
            by_url={
                "http://127.0.0.1:8000/v1/models": {"data": [{"id": "dialogue-model"}]},
                "http://127.0.0.1:8001/v1/models": {"data": [{"id": "agent-model"}]},
            }
        ),
    )

    assert result["status"] == "PASS"
    assert result["services"]["dialogue"]["status"] == "PASS"
    assert result["services"]["agent"]["status"] == "PASS"


def test_service_health_fails_when_the_endpoint_does_not_answer(tmp_path):
    result = preflight._service_health(
        _config(tmp_path, dialogue_engine="llamacpp"),
        expect_services=True,
        timeout_seconds=5.0,
        opener=_opener(error=OSError("connection refused")),
    )

    assert result["status"] == "FAIL"
    assert result["services"]["dialogue"]["error_code"] == "ENDPOINT_UNAVAILABLE"
    assert "connection refused" in result["services"]["dialogue"]["error"]


def test_service_health_fails_on_model_identity_mismatch(tmp_path):
    result = preflight._service_health(
        _config(tmp_path, dialogue_engine="llamacpp"),
        expect_services=True,
        timeout_seconds=5.0,
        opener=_opener(
            by_url={
                "http://127.0.0.1:8000/v1/models": {"data": [{"id": "some-other-model"}]},
                "http://127.0.0.1:8001/v1/models": {"data": [{"id": "agent-model"}]},
            }
        ),
    )

    assert result["status"] == "FAIL"
    assert result["services"]["dialogue"]["error_code"] == "MODEL_IDENTITY_MISMATCH"
    assert result["services"]["agent"]["status"] == "PASS"


def test_service_health_does_not_require_a_disabled_agent(tmp_path):
    result = preflight._service_health(
        _config(tmp_path, dialogue_engine="llamacpp", agent_enabled=False),
        expect_services=True,
        timeout_seconds=5.0,
        opener=_opener({"data": [{"id": "dialogue-model"}]}),
    )

    assert result["status"] == "PASS"
    assert set(result["services"]) == {"dialogue"}


# ---------------------------------------------------------------------------
# Acceptance gate
# ---------------------------------------------------------------------------

def test_required_checks_follow_the_declared_engine():
    llama_required = preflight._required_checks(torch_required=False, expect_services=False)
    vllm_required = preflight._required_checks(torch_required=True, expect_services=False)

    assert "inference_engine" in llama_required
    assert "torch_cuda" not in llama_required
    assert "torch_cuda" in vllm_required
    assert "service_health" not in llama_required
    assert "service_health" in preflight._required_checks(
        torch_required=False, expect_services=True
    )


def test_overall_status_requires_every_required_check():
    checks = {"inference_engine": {"status": "PASS"}, "service_health": {"status": "NOT RUN"}}

    assert preflight._overall_status(checks, ["inference_engine"]) == "PASS"
    assert preflight._overall_status(checks, ["inference_engine", "service_health"]) == "FAIL"
    assert preflight._overall_status(checks, ["missing_check"]) == "FAIL"


def test_smoke_resolves_the_version_probe_from_the_declared_engine(tmp_path):
    from scripts import real_hardware_smoke as smoke

    config = tmp_path / "runtime.yaml"
    config.write_text(_runtime_yaml(dialogue_engine="llamacpp"), encoding="utf-8")

    assert smoke.engine_executable_from_config(str(config)) == "llama-server"


def test_smoke_declines_to_guess_when_no_engine_is_declared(tmp_path):
    from scripts import real_hardware_smoke as smoke

    config = tmp_path / "runtime.yaml"
    config.write_text(_runtime_yaml(), encoding="utf-8")

    assert smoke.engine_executable_from_config(str(config)) is None


def test_configuration_error_is_reported_rather_than_raised():
    assert preflight._deployment_check("/nonexistent/runtime.yaml")[0]["status"] == "FAIL"
