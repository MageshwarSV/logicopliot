""""If this alert comes, press X" steps, and alerts a real job meets with no step for them.

An ERP alert that never showed up while a script was recorded used to be answered OK by
default on a real job, with nothing left behind for anybody to change. Now:
  - a `dialog` step marked is_optional is a rule for the whole run: an alert whose wording
    matches gets that step's button, wherever and whenever it appears; no alert, no effect;
  - an alert no step covers still gets the default, and is then written into the script as such
    a step (auto_added) so a Super Admin can choose its button from then on.
"""
from types import SimpleNamespace

from app.api.v1.jobs import learn_unscripted_alerts
from app.core.browser import (
    _dialog_answer,
    alert_text_matches,
    is_alert_rule,
    matching_dialog_rule,
    set_dialog_rules,
)


class _Ctx:  # weak-referenceable stand-in for a Playwright BrowserContext
    pass


def _page(ctx):
    return SimpleNamespace(context=ctx)


CREDIT = "Credit Limit for NOKIA SOLUTIONS AND NETWORKS INDIA has exhausted. Do you want to continue?"


# ------------------------------------------------------------------ matching the wording

def test_same_alert_for_another_customer_still_matches():
    other = "Credit Limit for FOXCONN HON HAI has exhausted. Do you want to continue?"
    assert alert_text_matches(CREDIT, other)


def test_a_very_long_customer_name_still_matches():
    other = ("Credit Limit for HON HAI PRECISION INDUSTRY COMPANY LIMITED TAIWAN BRANCH OFFICE "
             "CHENNAI UNIT 2 has exhausted. Do you want to continue?")
    assert alert_text_matches(CREDIT, other)


def test_a_different_alert_with_the_same_ending_does_not_match():
    assert not alert_text_matches(CREDIT, "Duplicate invoice found. Do you want to continue?")


def test_a_trimmed_phrase_matches_every_variant():
    assert alert_text_matches("has exhausted", CREDIT)


def test_unrelated_alert_does_not_match():
    assert not alert_text_matches(CREDIT, "Are you sure you want to close the form ?")


def test_empty_wording_is_a_catch_all():
    assert alert_text_matches("", "anything at all")


# ------------------------------------------------------------------ the rule at run time

def _rule_step(text, value="accept"):
    return {"action": "dialog", "description": text, "value": value, "is_optional": True}


def test_only_optional_dialog_steps_are_rules():
    assert is_alert_rule(_rule_step("x"))
    assert not is_alert_rule({"action": "dialog", "description": "x"})
    assert not is_alert_rule({"action": "click", "is_optional": True})


def test_a_rule_answers_its_alert_with_the_chosen_button():
    ctx = _Ctx()
    set_dialog_rules(ctx, [{"action": "click"}, _rule_step("has exhausted", "dismiss")])
    assert matching_dialog_rule(_page(ctx), CREDIT)["step_no"] == 2
    assert _dialog_answer(_page(ctx), CREDIT) == (False, "")


def test_a_rule_beats_the_destructive_wording_safety_net_when_chosen():
    """The person said OK for exactly this alert - trusted as given."""
    ctx = _Ctx()
    set_dialog_rules(ctx, [_rule_step("close the form", "accept")])
    assert _dialog_answer(_page(ctx), "Are you sure you want to close the form ?") == (True, "")


def test_an_alert_no_rule_covers_keeps_todays_default():
    ctx = _Ctx()
    set_dialog_rules(ctx, [_rule_step("has exhausted", "dismiss")])
    assert matching_dialog_rule(_page(ctx), "Saved successfully") is None
    assert _dialog_answer(_page(ctx), "Saved successfully") == (True, "")
    assert _dialog_answer(_page(ctx), "Are you sure you want to close the form ?") == (False, "")


def test_a_specific_rule_beats_a_catch_all():
    ctx = _Ctx()
    set_dialog_rules(ctx, [_rule_step("", "accept"), _rule_step("has exhausted", "dismiss")])
    assert _dialog_answer(_page(ctx), CREDIT) == (False, "")


# ------------------------------------------------------------------ writing it into the script

def _script(steps, checkpoint=None):
    return SimpleNamespace(id="s1", steps=steps, checkpoint_index=checkpoint)


def test_an_unscripted_alert_is_added_right_after_the_step_it_fired_during(db_session, monkeypatch):
    import sqlalchemy.orm.attributes as attrs
    monkeypatch.setattr(attrs, "flag_modified", lambda *a, **k: None)
    script = _script([{"action": "click"}, {"action": "click"}, {"action": "click"}])
    added = learn_unscripted_alerts(db_session, script, [
        {"type": "confirm", "message": CREDIT, "answer": "accept", "during_step": 1},
    ])
    assert added == 1
    new = script.steps[2]
    assert new["action"] == "dialog" and new["is_optional"] and new["auto_added"]
    assert new["description"] == CREDIT and new["value"] == "accept"
    assert new["dialog_type"] == "confirm"


def test_an_alert_already_covered_is_never_added_twice(db_session, monkeypatch):
    import sqlalchemy.orm.attributes as attrs
    monkeypatch.setattr(attrs, "flag_modified", lambda *a, **k: None)
    script = _script([{"action": "click"}, _rule_step("has exhausted", "dismiss")])
    added = learn_unscripted_alerts(db_session, script, [
        {"type": "confirm", "message": CREDIT, "answer": "accept", "during_step": 0},
    ])
    assert added == 0
    assert len(script.steps) == 2


def test_the_checkpoint_stays_on_the_same_step(db_session, monkeypatch):
    import sqlalchemy.orm.attributes as attrs
    monkeypatch.setattr(attrs, "flag_modified", lambda *a, **k: None)
    script = _script([{"action": "click"}] * 5, checkpoint=3)
    learn_unscripted_alerts(db_session, script, [
        {"type": "alert", "message": "Session from another portal was closed", "answer": "accept",
         "during_step": 0},
    ])
    assert script.checkpoint_index == 4


def test_several_alerts_land_in_their_own_places(db_session, monkeypatch):
    import sqlalchemy.orm.attributes as attrs
    monkeypatch.setattr(attrs, "flag_modified", lambda *a, **k: None)
    script = _script([{"action": "click", "n": n} for n in range(4)])
    learn_unscripted_alerts(db_session, script, [
        {"type": "alert", "message": "Login alert from another portal", "answer": "accept", "during_step": 0},
        {"type": "confirm", "message": CREDIT, "answer": "accept", "during_step": 2},
    ])
    kinds = [st.get("n", st.get("description", "")[:5]) for st in script.steps]
    assert kinds == [0, "Login", 1, 2, "Credi", 3]
