"""_resolve_target_value's lookup_key_label used to name exactly one OTHER job-level field to
key a lookup off (e.g. "Erp Entry Name" keyed on "Quotation Name" alone - see
test_target_value_field_ordering.py). CustomsHouseCode needs TWO keys together - the port of
entry AND the transport mode, since neither alone picks one customs house (Chennai Sea and
Chennai Air are different codes) - so lookup_key_label now accepts a comma-separated list,
and the raw value remembered for a later correction is "||"-joined back into the same ordered
keys. These tests cover the multi-key path directly; the existing single-key tests (no comma
anywhere) prove that path is completely unchanged.
"""
from unittest.mock import patch

from app.api.v1.jobs import _resolve_target_value
from app.core.reference_cache import remember_reference
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_job_and_field(db_session, lookup_key_label: str, fuzzy_match: bool = False):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-MULTIKEY", status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    cf = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="CustomsHouseCode",
        kind="hardcoded", hardcoded_value=None, is_target_value=True,
        lookup_key_label=lookup_key_label, fuzzy_match=fuzzy_match,
    )
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)
    return tenant, group, job, cf


def _add_job_level_value(db_session, tenant, job, label_name, value):
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name=label_name,
                        extracted_value=value)
    db_session.add(fv)
    db_session.commit()


def test_two_keys_together_resolve_the_right_row(db_session):
    tenant, group, job, cf = _make_job_and_field(db_session, "port_of_entry,TransportModeCode")
    _add_job_level_value(db_session, tenant, job, "port_of_entry", "CHENNAI")
    _add_job_level_value(db_session, tenant, job, "TransportModeCode", "S")
    remember_reference(db_session, custom_field_id=cf.id, match_values=["CHENNAI", "S"],
                       resolved_value="INMAA1")
    remember_reference(db_session, custom_field_id=cf.id, match_values=["CHENNAI", "A"],
                       resolved_value="INMAA4")
    db_session.commit()

    value, raw = _resolve_target_value(db_session, job.id, cf, None)
    assert value == "INMAA1"
    assert raw == "CHENNAI||S"


def test_the_same_port_with_a_different_mode_resolves_a_different_row(db_session):
    tenant, group, job, cf = _make_job_and_field(db_session, "port_of_entry,TransportModeCode")
    _add_job_level_value(db_session, tenant, job, "port_of_entry", "CHENNAI")
    _add_job_level_value(db_session, tenant, job, "TransportModeCode", "A")
    remember_reference(db_session, custom_field_id=cf.id, match_values=["CHENNAI", "S"],
                       resolved_value="INMAA1")
    remember_reference(db_session, custom_field_id=cf.id, match_values=["CHENNAI", "A"],
                       resolved_value="INMAA4")
    db_session.commit()

    value, raw = _resolve_target_value(db_session, job.id, cf, None)
    assert value == "INMAA4"
    assert raw == "CHENNAI||A"


def test_a_port_mode_pair_with_no_matching_row_resolves_to_none_but_still_keeps_the_raw_key(db_session):
    tenant, group, job, cf = _make_job_and_field(db_session, "port_of_entry,TransportModeCode")
    _add_job_level_value(db_session, tenant, job, "port_of_entry", "SHANGHAI")
    _add_job_level_value(db_session, tenant, job, "TransportModeCode", "S")

    value, raw = _resolve_target_value(db_session, job.id, cf, None)
    assert value is None
    assert raw == "SHANGHAI||S"


def test_missing_one_of_the_two_keys_resolves_to_nothing_at_all(db_session):
    tenant, group, job, cf = _make_job_and_field(db_session, "port_of_entry,TransportModeCode")
    _add_job_level_value(db_session, tenant, job, "port_of_entry", "CHENNAI")
    # TransportModeCode never extracted on this job at all.

    value, raw = _resolve_target_value(db_session, job.id, cf, None)
    assert value is None
    assert raw is None


def test_fuzzy_match_true_still_prefers_an_exact_match_with_no_ai_call(db_session):
    """Step 1 (exact) always runs first and, when it finds something, step 2 (AI-assisted) is
    never even reached - free and instant whenever the sheet's own spelling already agrees
    with what was extracted."""
    tenant, group, job, cf = _make_job_and_field(
        db_session, "port_of_entry,TransportModeCode", fuzzy_match=True)
    _add_job_level_value(db_session, tenant, job, "port_of_entry", "CHENNAI")
    _add_job_level_value(db_session, tenant, job, "TransportModeCode", "S")
    remember_reference(db_session, custom_field_id=cf.id, match_values=["CHENNAI", "S"],
                       resolved_value="INMAA1")
    db_session.commit()

    with patch("app.core.reference_cache.lookup_reference_ai_assisted") as mocked:
        value, raw = _resolve_target_value(db_session, job.id, cf, None)
    assert value == "INMAA1"
    assert raw == "CHENNAI||S"
    mocked.assert_not_called()


def test_fuzzy_match_true_falls_to_ai_assisted_only_when_the_exact_match_fails(db_session):
    tenant, group, job, cf = _make_job_and_field(
        db_session, "port_of_entry,TransportModeCode", fuzzy_match=True)
    _add_job_level_value(db_session, tenant, job, "port_of_entry", "SHANGHAI")
    _add_job_level_value(db_session, tenant, job, "TransportModeCode", "S")
    # No exact row for ("SHANGHAI", "S") - step 1 finds nothing, so step 2 must run.

    with patch("app.core.reference_cache.lookup_reference_ai_assisted", return_value="CNSHA") as mocked:
        value, raw = _resolve_target_value(db_session, job.id, cf, None)
    assert value == "CNSHA"
    assert raw == "SHANGHAI||S"
    mocked.assert_called_once()
    assert mocked.call_args.args[1:] == (cf.id, ["SHANGHAI", "S"])


def test_fuzzy_match_false_never_calls_ai_assisted_even_when_the_exact_match_fails(db_session):
    """Regression for the existing (fuzzy_match=False) behaviour - CustomsHouseCode's own
    port+mode lookup must stay a plain exact match with no AI step at all."""
    tenant, group, job, cf = _make_job_and_field(
        db_session, "port_of_entry,TransportModeCode", fuzzy_match=False)
    _add_job_level_value(db_session, tenant, job, "port_of_entry", "SHANGHAI")
    _add_job_level_value(db_session, tenant, job, "TransportModeCode", "S")

    with patch("app.core.reference_cache.lookup_reference_ai_assisted") as mocked:
        value, raw = _resolve_target_value(db_session, job.id, cf, None)
    assert value is None
    assert raw == "SHANGHAI||S"
    mocked.assert_not_called()


def test_a_single_key_label_still_works_exactly_as_before(db_session):
    tenant, group, job, cf = _make_job_and_field(db_session, "Quotation Name")
    _add_job_level_value(db_session, tenant, job, "Quotation Name", "KUEHNE + NAGEL PVT. LTD.")
    remember_reference(db_session, custom_field_id=cf.id,
                       match_values=["KUEHNE + NAGEL PVT. LTD."], resolved_value="KUEHNE+NAGEL PVT LTD")
    db_session.commit()

    value, raw = _resolve_target_value(db_session, job.id, cf, None)
    assert value == "KUEHNE+NAGEL PVT LTD"
    assert raw == "KUEHNE + NAGEL PVT. LTD."
    assert "||" not in raw
