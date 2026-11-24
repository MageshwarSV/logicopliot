"""Onboarding Step 4: category configuration is checked before it is stored."""
import pytest

from app.core.erp_field_config import ConfigError, clean_config, is_configured


def test_fixed_value_with_per_importer_overrides():
    out = clean_config("fixed", {"value": " N ", "per_importer": [
        {"importer": "TEREX INDIA", "value": "Y"}, {"importer": "", "value": "x"}], "hidden": 1})
    assert out == {"value": "N", "per_importer": [{"importer": "TEREX INDIA", "value": "Y"}], "hidden": True}
    assert is_configured("fixed", out, None)
    assert not is_configured("fixed", {"value": "", "per_importer": []}, None)


def test_fixed_same_importer_twice_is_refused():
    with pytest.raises(ConfigError):
        clean_config("fixed", {"value": "N", "per_importer": [
            {"importer": "Terex", "value": "Y"}, {"importer": "TEREX", "value": "N"}]})


def test_operator_dropdown_needs_choices_and_a_valid_default():
    out = clean_config("operator", {"input_type": "dropdown", "choices": ["A", "P", "A", " N "], "default": "A"})
    assert out == {"input_type": "dropdown", "choices": ["A", "P", "N"], "default": "A"}
    with pytest.raises(ConfigError):
        clean_config("operator", {"input_type": "dropdown", "choices": []})
    with pytest.raises(ConfigError):
        clean_config("operator", {"input_type": "dropdown", "choices": ["A"], "default": "Z"})
    with pytest.raises(ConfigError):
        clean_config("operator", {"input_type": "colour"})


def test_operator_text_drops_choices():
    assert clean_config("operator", {"input_type": "date", "choices": ["x"]}) == {"input_type": "date", "choices": [], "default": ""}


def test_other_categories_kept_and_none_allowed():
    assert clean_config("ai", {"prompt": "x"}) == {"prompt": "x"}
    assert clean_config("fixed", None) is None
