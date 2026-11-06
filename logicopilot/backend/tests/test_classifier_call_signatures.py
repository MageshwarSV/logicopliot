"""Every call into a classifier module must match the signature that is actually there.

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
EVERY public callable in these modules, with no list to keep up to date.

Classification is now split one independent module per shipping mode (classifier_sea_import,
classifier_air_import, classifier_sea_export, classifier_air_export - see
classifier_common.py's own docstring for why), with a real call site resolving which ONE of
them it hits only at runtime, via classifier_common.classifier_module(mode). A static check
cannot know which module a given `clf.assign_documents_detailed(...)` call will actually reach,
so every call site is checked against EVERY module's own copy of that function name - if any
one copy has drifted out of sync with its callers (a parameter renamed while tuning just one
mode, say), that is exactly the "identical copies silently diverged" failure this test exists
to catch.

Static, not dynamic: it reads the call sites with `ast` rather than executing them, so it
covers code paths that only run when a real email arrives with real attachments.
"""
import ast
import inspect
import pathlib

import app.core.classifier_air_export as classifier_air_export
import app.core.classifier_air_import as classifier_air_import
import app.core.classifier_common as classifier_common
import app.core.classifier_sea_export as classifier_sea_export
import app.core.classifier_sea_import as classifier_sea_import

MODULES = [
    classifier_common, classifier_sea_import, classifier_air_import,
    classifier_sea_export, classifier_air_export,
]
# parents[1], not [2]: these modules live at app/core/, so [1] is the `app` package.
# [2] is the backend directory, which walks tests/ and .venv/ too - slow, and it trips
# over files that are not application code at all.
APP_ROOT = pathlib.Path(classifier_common.__file__).resolve().parents[1]  # .../app
MODULE_PATHS = {pathlib.Path(m.__file__).resolve() for m in MODULES}
MODULES_BY_NAME = {m.__name__: m for m in MODULES}


def _public_callables(module) -> dict:
    return {
        name: obj
        for name, obj in vars(module).items()
        if inspect.isfunction(obj) and not name.startswith("_")
        # Only things DEFINED here - a re-exported import belongs to whatever module owns it.
        and obj.__module__ == module.__name__
    }


def _signatures_for(name: str):
    """Every module's own version of this function name, if it defines one - a call site
    resolved dynamically (clf.func(...)) might hit any of them at runtime."""
    sigs = []
    for m in MODULES:
        fn = _public_callables(m).get(name)
        if fn is not None:
            sigs.append((m.__name__, inspect.signature(fn)))
    return sigs


def _call_sites():
    """(file, line, function name, keyword args used, positional count) for every call to a
    classifier function from anywhere else in the app."""
    known_names: set[str] = set()
    for m in MODULES:
        known_names |= set(_public_callables(m))
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.resolve() in MODULE_PATHS:
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
            if name not in known_names:
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
    offenders = []
    for path, line, name, kwargs, _pos in _call_sites():
        for mod_name, sig in _signatures_for(name):
            if any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values()):
                continue  # **kwargs swallows anything
            unknown = kwargs - set(sig.parameters)
            if unknown:
                offenders.append(
                    f"{path}:{line} {name}() ({mod_name}) does not accept {sorted(unknown)}")
    assert offenders == [], "\n".join(offenders)


def test_no_call_passes_more_positional_arguments_than_the_function_takes():
    """The same mistake's other shape - an argument dropped from the middle of a signature
    rather than the end, which reorders everything after it."""
    offenders = []
    for path, line, name, _kwargs, positional in _call_sites():
        for mod_name, sig in _signatures_for(name):
            if any(p.kind is p.VAR_POSITIONAL for p in sig.parameters.values()):
                continue
            takes = sum(
                1 for p in sig.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            )
            if positional > takes:
                offenders.append(
                    f"{path}:{line} {name}() ({mod_name}) takes {takes} positional, "
                    f"called with {positional}"
                )
    assert offenders == [], "\n".join(offenders)


def test_every_required_parameter_is_supplied_by_every_caller():
    """A parameter ADDED to a classifier module without a default, while callers stay as they
    were - the mirror image of the outage, and just as invisible until it runs."""
    offenders = []
    for path, line, name, kwargs, positional in _call_sites():
        for mod_name, sig in _signatures_for(name):
            params = [
                p for p in sig.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
            ]
            for index, p in enumerate(params):
                if p.default is not inspect.Parameter.empty:
                    continue
                given = index < positional or p.name in kwargs
                if not given:
                    offenders.append(
                        f"{path}:{line} {name}() ({mod_name}) needs '{p.name}' and is not "
                        f"given it"
                    )
    assert offenders == [], "\n".join(offenders)


def test_the_module_actually_exposes_what_the_app_imports():
    """A function RENAMED or removed from one of the classifier modules. Import errors
    normally catch this at startup, but a deferred `from ... import x` inside a function body
    does not fail until that line runs."""
    missing = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if path.resolve() in MODULE_PATHS:
            continue
        try:
            # utf-8-sig for the same byte-order-mark reason as above.
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), str(path))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in MODULES_BY_NAME:
                mod = MODULES_BY_NAME[node.module]
                for alias in node.names:
                    if not hasattr(mod, alias.name):
                        missing.append(
                            f"{path.relative_to(APP_ROOT.parent)}:{node.lineno} "
                            f"imports '{alias.name}', which {node.module} does not define"
                        )
    assert missing == [], "\n".join(missing)
