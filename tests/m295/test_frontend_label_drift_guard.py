"""#295 — cross-layer drift guard: the JS display dicts must mirror the
backend canons.

The frontend (backend/app/web/app.js) carries five flat display dictionaries
(TASK_TYPE_RU, TASK_STATUS_RU, TASK_SOURCE_RU, JOB_TITLES_RU, OBJECT_TYPE_RU)
that mirror backend value domains. This test reads app.js as TEXT (no JS
runtime) and asserts the dicts stay in lockstep with the backend canons.

Two kinds of check:
- BACKEND-DERIVED (drift-detecting both ways): TASK_TYPE_RU keys+values from
  the registry import + labels.TASK_TYPE; JOB_TITLES_RU and OBJECT_TYPE_RU
  keys+values imported from _OCCUPATION_TITLES_RU and labels.OBJECT_TYPE. A
  new registry action, a renamed role title, or a changed label noun turns
  red.
- HAND-MAINTAINED CANON (catches JS-side drift only): TASK_STATUS_RU and
  TASK_SOURCE_RU are pinned to the writer sites named in the per-test
  comments. A NEW backend status/source does NOT auto-turn-red — it requires
  updating the pinned set (the comments name every writer, so the update path
  is obvious).

Every value in all five dicts must be a non-empty string. The test also fails
if app.js is missing (the RED spot-check relies on this).
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from app.actions.registry import ACTION_REGISTRY  # noqa: E402
from app.events.labels import OBJECT_TYPE, TASK_TYPE  # noqa: E402
from app.social.roles import _OCCUPATION_TITLES_RU  # noqa: E402

APP_JS = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "app", "web", "app.js",
)


def _read_app_js():
    """Read app.js as text; fail clearly if the file is missing (the RED
    spot-check stashes app.js and expects this guard to fail)."""
    if not os.path.exists(APP_JS):
        raise AssertionError(
            f"app.js not found at {APP_JS} — the #295 drift guard cannot "
            "verify the JS display dicts without the frontend file."
        )
    with open(APP_JS, encoding="utf-8") as fh:
        return fh.read()


def _parse_js_dict(js_text, name):
    """Extract a flat `const NAME = { key: "value", ... };` dict from app.js.

    Keys are unquoted identifiers or double-quoted strings; values are
    double-quoted strings. The dict is flat (no nesting) and terminated by
    the first `};` after the opening `{`. Returns {key: value}. Raises if the
    const is not found or no entries parse.
    """
    m = re.search(r"const\s+" + re.escape(name) + r"\s*=\s*\{", js_text)
    if m is None:
        raise AssertionError(f"const {name} not found in app.js")
    start = m.end()
    end = js_text.index("};", start)
    block = js_text[start:end]
    pairs = {}
    for km in re.finditer(
        r'(?:"([^"]+)"|([A-Za-z_]\w*))\s*:\s*"([^"]*)"', block
    ):
        key = km.group(1) if km.group(1) is not None else km.group(2)
        pairs[key] = km.group(3)
    if not pairs:
        raise AssertionError(f"const {name} parsed zero entries in app.js")
    return pairs


def _all_values_nonempty_str(d, name):
    for key, value in d.items():
        assert isinstance(value, str) and value.strip(), (
            f"{name}[{key!r}] is not a non-empty string: {value!r}"
        )


# ---------- 1. TASK_TYPE_RU mirrors the registry + labels.TASK_TYPE ----------

def test_task_type_ru_keys_match_registry_plus_lifecycle():
    js = _read_app_js()
    d = _parse_js_dict(js, "TASK_TYPE_RU")
    registry_names = {a.name for a in ACTION_REGISTRY}
    expected_keys = registry_names | {"TRAVEL_EXTERNAL", "CONSTRUCT"}
    assert set(d) == expected_keys, (
        f"TASK_TYPE_RU keys drifted from the registry.\n"
        f"only in JS: {sorted(set(d) - expected_keys)}\n"
        f"only in registry: {sorted(expected_keys - set(d))}"
    )


def test_task_type_ru_values_match_labels():
    js = _read_app_js()
    d = _parse_js_dict(js, "TASK_TYPE_RU")
    for key, value in d.items():
        assert key in TASK_TYPE, f"TASK_TYPE_RU key {key!r} not in labels.TASK_TYPE"
        assert value == TASK_TYPE[key], (
            f"TASK_TYPE_RU[{key!r}] = {value!r} != labels.TASK_TYPE[{key!r}] = "
            f"{TASK_TYPE[key]!r} — the JS noun must match the event phrase."
        )


# ---------- 2. TASK_STATUS_RU keys == the written statuses ----------

def test_task_status_ru_keys_match_written_statuses():
    js = _read_app_js()
    d = _parse_js_dict(js, "TASK_STATUS_RU")
    # HAND-MAINTAINED canon (not backend-derived): the statuses written to
    # CharacterTask, by writer. A NEW backend status requires adding it here.
    #   lifecycle.py            -> planned / active / completed / failed
    #   ai/worker.py:144        -> STARTED
    #   api/app.py:978 + :2425  -> cancelled (pause + cancel_task endpoint)
    #   characters/health.py:40 -> cancelled (death cancels open tasks)
    expected = {"planned", "active", "completed", "failed", "STARTED",
                "cancelled"}
    assert set(d) == expected, (
        f"TASK_STATUS_RU keys drifted from the written statuses.\n"
        f"only in JS: {sorted(set(d) - expected)}\n"
        f"only in writers: {sorted(expected - set(d))}"
    )


# ---------- 3. TASK_SOURCE_RU keys ⊇ the written sources ----------

def test_task_source_ru_keys_cover_written_sources():
    js = _read_app_js()
    d = _parse_js_dict(js, "TASK_SOURCE_RU")
    # HAND-MAINTAINED canon (not backend-derived): the sources written to
    # CharacterTask, by writer (superset allowed — the `user` alias is
    # documented in app.js). A NEW backend source requires adding it here.
    #   lifecycle.py:573-734     -> need / goal
    #   api/app.py               -> player
    #   external/npc_trips.py:86 -> utility
    #   ai/worker.py:145         -> ai
    required = {"player", "utility", "ai", "need", "goal"}
    missing = required - set(d)
    assert not missing, (
        f"TASK_SOURCE_RU is missing written sources: {sorted(missing)}"
    )


# ---------- 4. JOB_TITLES_RU mirrors _OCCUPATION_TITLES_RU ----------

def test_job_titles_ru_mirrors_roles():
    js = _read_app_js()
    d = _parse_js_dict(js, "JOB_TITLES_RU")
    assert d == dict(_OCCUPATION_TITLES_RU), (
        f"JOB_TITLES_RU drifted from _OCCUPATION_TITLES_RU.\n"
        f"JS: {d}\n"
        f"roles: {dict(_OCCUPATION_TITLES_RU)}"
    )


# ---------- 5. OBJECT_TYPE_RU mirrors labels.OBJECT_TYPE ----------

def test_object_type_ru_mirrors_labels():
    js = _read_app_js()
    d = _parse_js_dict(js, "OBJECT_TYPE_RU")
    value_drift = [
        (k, d[k], OBJECT_TYPE[k])
        for k in set(d) & set(OBJECT_TYPE) if d[k] != OBJECT_TYPE[k]
    ]
    assert d == dict(OBJECT_TYPE), (
        f"OBJECT_TYPE_RU drifted from labels.OBJECT_TYPE.\n"
        f"only in JS: {sorted(set(d) - set(OBJECT_TYPE))}\n"
        f"only in labels: {sorted(set(OBJECT_TYPE) - set(d))}\n"
        f"value drift: {value_drift}"
    )


# ---------- 6. every value in all five dicts is a non-empty string ----------

def test_all_dict_values_are_nonempty_strings():
    js = _read_app_js()
    for name in ("TASK_TYPE_RU", "TASK_STATUS_RU", "TASK_SOURCE_RU",
                 "JOB_TITLES_RU", "OBJECT_TYPE_RU"):
        d = _parse_js_dict(js, name)
        _all_values_nonempty_str(d, name)
