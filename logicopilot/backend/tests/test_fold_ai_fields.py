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
