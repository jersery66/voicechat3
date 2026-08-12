"""Configuration compatibility tests for current Ollama SDK responses."""

import config


def test_detect_ollama_model_reads_object_style_list_response(monkeypatch):
    class Model:
        model = "qwen2.5:7b"

    class Response:
        models = [Model()]

    class Client:
        def __init__(self, **kwargs):
            pass

        def list(self):
            return Response()

    monkeypatch.setattr("ollama.Client", Client)

    assert config._detect_ollama_model() == "qwen2.5:7b"
