import sys
from contextlib import contextmanager
from types import SimpleNamespace

from pharmassist_api.scripts import haidef_smoke


class _FakeTensor:
    shape = (1, 3)


class _FakeInputs(dict):
    def __init__(self) -> None:
        super().__init__({"input_ids": _FakeTensor()})

    def to(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self


def test_run_conditional_passes_hf_token_to_processor(monkeypatch):
    calls: dict[str, dict] = {}

    class _FakeModel:
        device = "cpu"

        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs):  # noqa: ARG003
            calls["model_kwargs"] = kwargs
            return cls()

        def generate(self, **kwargs):  # noqa: ANN003
            return [[0, 1, 2, 3]]

    class _FakeProcessor:
        @classmethod
        def from_pretrained(cls, model_id: str, **kwargs):  # noqa: ARG003
            calls["processor_kwargs"] = kwargs
            return cls()

        def apply_chat_template(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return _FakeInputs()

        def decode(self, gen, skip_special_tokens=True):  # noqa: ANN001, ARG002
            return "{}"

    @contextmanager
    def _fake_inference_mode():
        yield

    fake_transformers = SimpleNamespace(
        AutoModelForImageTextToText=_FakeModel,
        AutoProcessor=_FakeProcessor,
    )
    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
        bfloat16="bf16",
        float16="f16",
        float32="f32",
        inference_mode=_fake_inference_mode,
    )

    monkeypatch.setenv("HF_TOKEN", "hf_test_token")
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    out = haidef_smoke._run_conditional(
        "google/medgemma-4b-it",
        "prompt",
        max_new_tokens=32,
        debug=False,
    )

    assert out == "{}"
    assert calls["model_kwargs"]["token"] == "hf_test_token"
    assert calls["processor_kwargs"]["token"] == "hf_test_token"
