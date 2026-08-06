"""Tests for ${VAR} template expansion (single-pass and fixpoint)."""

import pytest

from q8020_cfd_metautil.sweep import (
    _expand_templates,
    _expand_templates_fixpoint,
)


def test_single_pass_flat_expansion():
    variables = {"_ranks": 8, "name": "ghz"}
    result = _expand_templates("srun -n ${_ranks} ${name}.py", variables)
    assert result == "srun -n 8 ghz.py"


def test_unknown_token_left_verbatim():
    result = _expand_templates("path/${UNKNOWN}/x", {"other": 1})
    assert result == "path/${UNKNOWN}/x"


def test_fixpoint_resolves_nested_indirection():
    variables = {
        "_launch": "srun -N ${_nodes} -n ${_ranks}",
        "_nodes": 2,
        "_ranks": 16,
        "_script": "${_launch} python solver.py",
    }
    result = _expand_templates_fixpoint(variables, variables)
    assert result["_script"] == "srun -N 2 -n 16 python solver.py"


def test_fixpoint_single_level_matches_single_pass():
    variables = {"a": "x", "b": "${a}y"}
    assert _expand_templates_fixpoint(variables, variables) == \
        _expand_templates(variables, variables)


def test_fixpoint_unknown_token_left_verbatim():
    variables = {"_script": "python ${NOT_DEFINED}"}
    result = _expand_templates_fixpoint(variables, variables)
    assert result["_script"] == "python ${NOT_DEFINED}"


def test_fixpoint_cycle_raises():
    variables = {"a": "${b}", "b": "${a}"}
    with pytest.raises(ValueError, match="cycle"):
        _expand_templates_fixpoint(variables, variables)


def test_fixpoint_recurses_into_dicts_and_lists():
    variables = {"root": "/data", "sub": "${root}/runs"}
    obj = {
        "_env_exports": {"OUT": "${sub}/out"},
        "paths": ["${root}/a", "${sub}/b"],
    }
    result = _expand_templates_fixpoint(obj, variables)
    assert result["_env_exports"]["OUT"] == "/data/runs/out"
    assert result["paths"] == ["/data/a", "/data/runs/b"]


def test_fixpoint_non_string_values_untouched():
    variables = {"n": 4}
    obj = {"count": 4, "flag": True, "none": None}
    assert _expand_templates_fixpoint(obj, variables) == obj
