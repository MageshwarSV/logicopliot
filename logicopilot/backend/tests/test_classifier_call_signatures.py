"""Every call into app.core.classifier must match the signature that is actually there.

This exists because of a live outage. classifier.py was restored to an older version whose
`assign_documents_detailed` has no `tenant_id` parameter, while the email puller and Smart
Upload both pass one. Nothing caught it: the app imported fine, the whole classifier test
suite passed, and the failure only appeared once a real email arrived -

    TypeError: assign_documents_detailed() got an unexpected keyword argument 'tenant_id'

- at which point every harvested email crashed and jobs were created with no documents at
all, showing up as "Missing Documents" with empty slots.

The check that was done by hand before that change grepped three function names and found
three call sites. It missed the fourth function entirely, which is exactly the kind of thing
a person checking by eye misses and a machine does not. So it is done here instead, over
EVERY public callable in that module, with no list to keep up to date.

Static, not dynamic: it reads the call sites with `ast` rather than executing them, so it
covers code paths that only run when a real email arrives with real attachments.
"""
import ast
import inspect
import pathlib

import pytest

import app.core.classifier as classifier

# parents[1], not [2]: classifier.py lives at app/core/, so [1] is the `app` package.
# [2] is the backend directory, which walks tests/ and .venv/ too - slow, and it trips
# over files that are not application code at all.
APP_ROOT = pathlib.Path(classifier.__file__).resolve().parents[1]  # .../app
CLASSIFIER_PATH = pathlib.Path(classifier.__file__).resolve()


def _public_callables() -> dict:
    return {
        name: obj
        for name, obj in vars(classifier).items()
        if inspect.isfunction(obj) and not name.startswith("_")
        # Only things DEFINED here - a re-exported import belongs to whatever module owns it.
        and obj.__module__ == classifier.__name__
    }


def _call_sites():
    """(file, line, function name, keyword args used, positional count) for every call to a
    classifier function from anywhere else in the app."""
    known = _public_callables()
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.resolve() == CLASSIFIER_PATH:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
        except SyntaxError:  # pragma: no cover - a file that will not parse is its own alarm
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue
            if name not in known:
                continue
            yield (
                path.relative_to(APP_ROOT.parent),
                node.lineno,
                name,
                {kw.arg for kw in node.keywords if kw.arg},
                len(node.args),
            )


def test_no_call_passes_a_keyword_the_function_does_not_accept():
    """The exact outage: a caller passing tenant_id= to a function without it."""
    known = _public_callables()
    offenders = []
    for path, line, name, kwargs, _pos in _call_sites():
        sig = inspect.signature(known[name])
        if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
            continue  # **kwargs swallows anything
        unknown = kwargs - set(sig.parameters)
        if unknown:
            offenders.append(f"{path}:{line} {name}() does not accept {sorted(unknown)}")
    assert offenders == [], "\n".join(offenders)


def test_no_call_passes_more_positional_arguments_than_the_function_takes():
    """The same mistake's other shape - an argument dropped from the middle of a signature
    rather than the end, which reorders everything after it."""
    known = _public_callables()
    offenders = []
    for path, line, name, _kwargs, positional in _call_sites():
        sig = inspect.signature(known[name])
        if any(p.kind is p.VAR_POSITIONAL for p in sig.parameters.values()):
            continue
        takes = sum(
            1 for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
        if positional > takes:
            offenders.append(
                f"{path}:{line} {name}() takes {takes} positional, called with {positional}"
            )
    assert offenders == [], "\n".join(offenders)


def test_every_required_parameter_is_supplied_by_every_caller():
    """A parameter ADDED to the classifier without a default, while callers stay as they
    were - the mirror image of the outage, and just as invisible until it runs."""
    known = _public_callables()
    offenders = []
    for path, line, name, kwargs, positional in _call_sites():
        sig = inspect.signature(known[name])
        params = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        ]
        for index, p in enumerate(params):
            if p.default is not inspect.Parameter.empty:
                continue
            given = index < positional or p.name in kwargs
            if not given:
                offenders.append(f"{path}:{line} {name}() needs '{p.name}' and is not given it")
    assert offenders == [], "\n".join(offenders)


def test_the_module_actually_exposes_what_the_app_imports():
    """A function RENAMED or removed. Import errors normally catch this at startup, but a
    deferred `from ... import x` inside a function body does not fail until that line runs -
    and the classifier is imported that way in two places."""
    missing = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.resolve() == CLASSIFIER_PATH:
            continue
        try:
            # utf-8-sig for the same byte-order-mark reason as above.
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "app.core.classifier":
                for alias in node.names:
                    if not hasattr(classifier, alias.name):
                        missing.append(
                            f"{path.relative_to(APP_ROOT.parent)}:{node.lineno} "
                            f"imports '{alias.name}', which classifier.py does not define"
                        )
    assert missing == [], "\n".join(missing)
