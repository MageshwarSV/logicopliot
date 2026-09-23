"""The customer's bulk reference sheet (material code -> CTH). Upload/status used to parse a
real master (9.8 MB / 22,000 rows, 25-30+ seconds) INLINE in the request handler - observed
live to get the worker answering that request killed outright by its process supervisor for
going quiet too long, taking down other traffic on the same worker with it. Parsing now always
happens off-thread (start_background_parse); the request only writes the file and returns
immediately, and GET reports processing=true until a background parse (this one, or a self-
healing one kicked off by the GET itself) has written something to read.

Endpoint tests patch out start_background_parse (real threads have no place in a test run);
the parsing logic itself is exercised directly against a small real workbook."""
import io
import time
from unittest.mock import patch

import openpyxl

from app.core.material_master import (
    cached_mapping, clear_cache, is_building, load_master, master_path,
    start_background_parse,
)
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant, name="Sea Import Test"):
    group = TemplateGroup(tenant_id=tenant.id, name=name, status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _small_workbook_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Material", "Material Description", "Comm./imp. code no."])
    ws.append(["839599A", "AREC PA PLATE A", "85177990"])
    ws.append(["094624A", "WIDGET B", "85340000"])
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def teardown_module(module):  # noqa: ANN001 - pytest hook
    clear_cache()


# ---- endpoint: upload never parses inline ------------------------------------------------------

def test_upload_writes_the_file_and_starts_a_background_parse_without_blocking(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-mm-upload@example.com")
    login(client, admin.email)

    try:
        with patch("app.core.material_master.start_background_parse") as mock_start:
            resp = client.post(
                f"/api/v1/template-groups/{group.id}/material-master",
                files={"file": ("master.xlsx", _small_workbook_bytes(),
                                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The response never reports a real key count - that would mean it waited on the parse.
        assert body == {"ok": True, "attached": True, "processing": True,
                        "file_name": "master.xlsx", "materials": 0}
        assert master_path(group.id).exists()
        mock_start.assert_called_once_with(group.id)
    finally:
        clear_cache(group.id)
        master_path(group.id).unlink(missing_ok=True)
        master_path(group.id).with_suffix(".name").unlink(missing_ok=True)


def test_upload_rejects_a_non_excel_file(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-mm-reject@example.com")
    login(client, admin.email)

    resp = client.post(
        f"/api/v1/template-groups/{group.id}/material-master",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 422


# ---- endpoint: status never parses inline either ------------------------------------------------

def test_status_reports_not_attached_when_nothing_was_ever_uploaded(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-mm-none@example.com")
    login(client, admin.email)

    resp = client.get(f"/api/v1/template-groups/{group.id}/material-master")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"attached": False, "materials": 0}


def test_status_reports_processing_while_the_building_marker_is_present(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-mm-building@example.com")
    login(client, admin.email)

    path = master_path(group.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_small_workbook_bytes())
    path.with_suffix(".building").write_text("", encoding="utf-8")
    try:
        with patch("app.core.material_master.start_background_parse") as mock_start:
            resp = client.get(f"/api/v1/template-groups/{group.id}/material-master")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["attached"] is True
        assert body["processing"] is True
        assert body["materials"] == 0
        # Already building - a second parse must not be kicked off on top of it.
        mock_start.assert_not_called()
    finally:
        path.with_suffix(".building").unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        clear_cache(group.id)


def test_status_self_heals_by_starting_a_parse_when_nothing_is_cached_and_none_is_running(client, db_session):
    """A file sitting on disk with no cache and no .building marker - e.g. a parse that never
    got the chance to start (process restarted mid-upload) - must not be served as a permanent
    dead end. The status route notices and starts one itself."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-mm-selfheal@example.com")
    login(client, admin.email)

    path = master_path(group.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_small_workbook_bytes())
    try:
        with patch("app.core.material_master.start_background_parse") as mock_start:
            resp = client.get(f"/api/v1/template-groups/{group.id}/material-master")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["attached"] is True
        assert body["processing"] is True
        mock_start.assert_called_once_with(group.id)
    finally:
        path.unlink(missing_ok=True)
        clear_cache(group.id)


def test_status_serves_the_real_mapping_once_a_parse_has_completed(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    admin = make_user(db_session, role="super_admin", email="sa-mm-done@example.com")
    login(client, admin.email)

    path = master_path(group.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_small_workbook_bytes())
    try:
        # Parse it directly (synchronously, in-test) rather than via a real background thread -
        # this exercises the exact function start_background_parse's thread would call.
        mapping = load_master(group.id)
        assert mapping  # sanity: the fixture workbook itself parses to something

        resp = client.get(f"/api/v1/template-groups/{group.id}/material-master")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["attached"] is True
        assert "processing" not in body or body["processing"] is not True
        assert body["materials"] == len(mapping)
        assert body["sample"]
    finally:
        path.unlink(missing_ok=True)
        clear_cache(group.id)


# ---- logic: the parse itself, and the background-thread wiring ---------------------------------

def test_load_master_indexes_both_material_code_and_description(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, name="Logic Test 1")
    path = master_path(group.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_small_workbook_bytes())
    try:
        mapping = load_master(group.id)
        assert mapping["839599A"] == "85177990"
        assert mapping["AREC PA PLATE A"] == "85177990"
        assert mapping["094624A"] == "85340000"
    finally:
        path.unlink(missing_ok=True)
        clear_cache(group.id)


def test_cached_mapping_is_none_until_something_has_actually_been_parsed(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, name="Logic Test 2")
    path = master_path(group.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_small_workbook_bytes())
    try:
        assert cached_mapping(group.id) is None  # uploaded, but never parsed - not a false hit
        load_master(group.id)
        assert cached_mapping(group.id) is not None  # now cached (memory + disk)
    finally:
        path.unlink(missing_ok=True)
        clear_cache(group.id)


def test_start_background_parse_populates_the_cache_and_clears_its_own_marker(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, name="Logic Test 3")
    path = master_path(group.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_small_workbook_bytes())
    try:
        start_background_parse(group.id)
        assert is_building(group.id)  # marker up immediately, before the thread finishes
        for _ in range(50):
            if not is_building(group.id):
                break
            time.sleep(0.1)
        assert not is_building(group.id), "background parse did not finish in time"
        assert cached_mapping(group.id) is not None
    finally:
        path.unlink(missing_ok=True)
        clear_cache(group.id)


def test_start_background_parse_is_a_no_op_while_one_is_already_running(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, name="Logic Test 4")
    path = master_path(group.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_small_workbook_bytes())
    marker = path.with_suffix(".building")
    marker.write_text("", encoding="utf-8")  # simulate one already in flight
    try:
        with patch("threading.Thread") as mock_thread:
            start_background_parse(group.id)
        mock_thread.assert_not_called()
    finally:
        marker.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        clear_cache(group.id)


def test_clear_cache_removes_a_stale_building_marker(db_session):
    """A marker left behind by a parse that never reached its own finally (the whole process
    died mid-parse) would otherwise convince every future upload a parse is already running,
    forever. Replacing the file (upload -> clear_cache) must not inherit that."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant, name="Logic Test 5")
    path = master_path(group.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_small_workbook_bytes())
    path.with_suffix(".building").write_text("", encoding="utf-8")
    try:
        assert is_building(group.id)
        clear_cache(group.id)
        assert not is_building(group.id)
    finally:
        path.with_suffix(".building").unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        clear_cache(group.id)
