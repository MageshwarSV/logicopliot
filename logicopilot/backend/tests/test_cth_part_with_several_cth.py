"""A part number the CTH dump lists with several CTHs (Allison Transmission: 29547630 is
"HOUSING-TRANSMISSION ..." 87089300 and "DURABLE CONTAINERS ..." 39231090): the line's own
description decides, among THAT part's rows only - never first-row-wins."""
from unittest.mock import patch

import openpyxl

from app.core import llm
from app.core import material_master as mm


def _master(tmp_path, rows):
    p = tmp_path / "dump.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Material", "Material Description", "Comm./imp. code no."])
    for r in rows:
        ws.append(list(r))
    wb.save(p)
    with patch.object(mm, "master_path", lambda gid: p), \
            patch.object(mm, "_write_disk_cache", lambda *a, **k: None), \
            patch.object(mm, "_read_disk_cache", lambda *a, **k: None):
        mm._CACHE.pop("t", None)
        return mm.load_master("t")


ROWS = [
    ("29547630", "HOUSING-TRANSMISSION, MAI (TRANSMISSION PARTS)", "87089300"),
    ("29547630", "DURABLE CONTAINERS- FOR RE-EXPORT PLASTIC CRATES", "39231090"),
    ("29549820", "PLUG-PIPE (TRANSMISSION PARTS)", "87089300"),
]


def test_single_cth_part_unchanged(tmp_path):
    m = _master(tmp_path, ROWS)
    assert mm.lookup_cth("29549820", m, description="anything") == "87089300"


def test_description_picks_among_the_parts_rows(tmp_path):
    m = _master(tmp_path, ROWS)
    assert mm.lookup_cth("29547630", m, description="DURABLE CONTAINERS- FOR RE-EXPORT PLASTIC CRATES") == "39231090"
    assert mm.lookup_cth("29547630", m, description="HOUSING-TRANSMISSION, MAI (TRANSMISSION PARTS)") == "87089300"


def test_unknown_description_is_left_for_the_ai_pick(tmp_path):
    m = _master(tmp_path, ROWS)
    assert mm.lookup_cth("29547630", m, description="SOMETHING ELSE") == ""
    assert len(mm.part_candidates("29547630", m)) == 2
    assert mm.part_candidates("29549820", m) == []


def test_ai_pick_returns_the_chosen_rows_cth():
    cands = [("HOUSING", "87089300"), ("CRATES", "39231090")]

    class R:
        choices = [type("C", (), {"message": type("M", (), {"content": '{"row": 2}'})()})()]

    with patch.object(llm, "create_chat_completion_with_retry", lambda **k: R()), \
            patch.object(llm.get_settings(), "openai_api_key", "x"):
        assert llm.pick_cth_for_description("29547630", "plastic crate", cands) == "39231090"


def test_ai_pick_none_or_bad_row_is_blank():
    cands = [("HOUSING", "87089300")]

    class R:
        choices = [type("C", (), {"message": type("M", (), {"content": '{"row": 0}'})()})()]

    with patch.object(llm, "create_chat_completion_with_retry", lambda **k: R()), \
            patch.object(llm.get_settings(), "openai_api_key", "x"):
        assert llm.pick_cth_for_description("29547630", "plastic crate", cands) == ""
