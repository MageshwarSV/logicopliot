"""reasoning_safe_chat_params/is_reasoning_model (app/core/llm.py) - confirmed live against
the real OpenAI API: gpt-5-mini 400s on temperature=0 ("Unsupported value: 'temperature'
does not support 0 with this model. Only the default (1) value is supported.") and
separately on max_tokens ("Unsupported parameter: 'max_tokens' is not supported with this
model. Use 'max_completion_tokens' instead."). Any call site whose model can be swapped to a
reasoning-family model at runtime must translate these two parameters first."""
from app.core.llm import is_reasoning_model, reasoning_safe_chat_params


def test_gpt4o_mini_keeps_temperature_and_max_tokens_unchanged():
    assert reasoning_safe_chat_params("gpt-4o-mini", temperature=0, max_tokens=400) == {
        "temperature": 0, "max_tokens": 400,
    }


def test_gpt5_mini_drops_temperature_renames_and_enlarges_max_tokens():
    """A reasoning model spends part of its completion-token budget on hidden reasoning
    before the visible answer - confirmed live (128 of 142 tokens on a trivial reply). The
    translated budget must be enlarged, not passed through 1:1, or a real multi-field
    extraction call risks silent truncation."""
    result = reasoning_safe_chat_params("gpt-5-mini", temperature=0, max_tokens=400)
    assert result == {"max_completion_tokens": 2400}
    assert result["max_completion_tokens"] > 400


def test_o_series_models_are_also_treated_as_reasoning_family():
    for model in ("o1", "o1-mini", "o3", "o3-mini", "o4-mini"):
        result = reasoning_safe_chat_params(model, temperature=0, max_tokens=200)
        assert result == {"max_completion_tokens": 1200}


def test_a_future_gpt5_point_release_is_still_recognized_by_prefix():
    assert is_reasoning_model("gpt-5.5")
    assert is_reasoning_model("gpt-5.2-pro")
    assert not is_reasoning_model("gpt-4.1")
    assert not is_reasoning_model("gpt-4o")
