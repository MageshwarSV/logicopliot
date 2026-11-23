"""Option E pieces: the image input for computed-field calls (FOLD_AI_FIELDS)."""
from app.core import llm


def test_text_only_without_images():
    assert llm._user_content("question", None) == "question"


def test_images_are_attached_and_capped(tmp_path):
    imgs = []
    for i in range(12):
        p = tmp_path / f"page_{i}.png"
        p.write_bytes(b"\x89PNG fake")
        imgs.append(p)
    parts = llm._user_content("question", imgs)
    assert parts[0]["type"] == "text" and parts[0]["text"].startswith("question")
    assert sum(1 for x in parts if x["type"] == "image_url") == llm.MAX_FIELD_IMAGES
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_missing_image_is_skipped(tmp_path):
    parts = llm._user_content("q", [tmp_path / "gone.png"])
    assert len(parts) == 1


def test_ai_field_reads_images_with_the_vision_model(tmp_path):
    p = tmp_path / "page_1.png"
    p.write_bytes(b"\x89PNG fake")
    kw = llm._ai_field_model([p], max_tokens=300)
    assert kw["model"] == llm.get_settings().vision_engine_model
    assert "temperature" not in kw or llm.is_reasoning_model(kw["model"]) is False


def test_ai_field_with_text_keeps_the_text_model():
    kw = llm._ai_field_model(None, max_tokens=300)
    assert kw == {"model": llm.get_settings().openai_model, "temperature": 0, "max_tokens": 300}


def test_page_images_only_when_there_is_no_text():
    from app.api.v1.jobs import _custom_field_page_images

    assert _custom_field_page_images({}, [], "=== Invoice ===\nINVOICE 123") is None
    assert _custom_field_page_images({}, [], "=== Invoice ===\n") is None  # no files -> nothing
