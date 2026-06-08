"""Tests for merge_sidecar — the non-clobbering merge at the heart of the bridge.

The contract: bridge-managed tags (risk:/owner:) and the trailing risk badge are
the only things the bridge owns. User-authored tags and descriptions must survive
a merge untouched, and re-running with the same input must be a no-op.
"""
from __future__ import annotations


def test_merge_into_empty_sidecar(bridge_module, make_entry):
    merged = bridge_module.merge_sidecar({}, make_entry("/s", risk="high", owner="tom"))
    assert merged["tags"] == ["risk:high", "owner:tom"]


def test_merge_preserves_user_tags(bridge_module, make_entry):
    existing = {"tags": ["favorite", "category:net"]}
    merged = bridge_module.merge_sidecar(existing, make_entry("/s", risk="low", owner="tom"))
    # User tags kept and ordered first; bridge tags appended.
    assert merged["tags"] == ["favorite", "category:net", "risk:low", "owner:tom"]


def test_merge_replaces_stale_bridge_tags(bridge_module, make_entry):
    # A previous run wrote risk:low/owner:bob; new scan says high/tom.
    existing = {"tags": ["favorite", "risk:low", "owner:bob"]}
    merged = bridge_module.merge_sidecar(existing, make_entry("/s", risk="high", owner="tom"))
    assert merged["tags"] == ["favorite", "risk:high", "owner:tom"]


def test_merge_preserves_user_desc(bridge_module, make_entry):
    existing = {"desc": "My hand-written description"}
    merged = bridge_module.merge_sidecar(existing, make_entry("/s", risk="low"))
    # Low risk → no badge; user desc untouched.
    assert merged["desc"] == "My hand-written description"


def test_merge_appends_badge_to_user_desc(bridge_module, make_entry):
    existing = {"desc": "My script"}
    merged = bridge_module.merge_sidecar(existing, make_entry("/s", risk="high"))
    assert merged["desc"] == "My script  [RISK: HIGH]"


def test_merge_refreshes_stale_badge_not_double_appends(bridge_module, make_entry):
    # Previously high; now critical. Badge must be replaced, not stacked.
    existing = {"desc": "My script  [RISK: HIGH]"}
    merged = bridge_module.merge_sidecar(existing, make_entry("/s", risk="critical"))
    assert merged["desc"] == "My script  [RISK: CRITICAL]"
    assert merged["desc"].count("[RISK:") == 1


def test_merge_drops_badge_when_risk_downgraded(bridge_module, make_entry):
    existing = {"desc": "My script  [RISK: HIGH]"}
    merged = bridge_module.merge_sidecar(existing, make_entry("/s", risk="low"))
    assert merged["desc"] == "My script"


def test_merge_uses_bulwark_desc_when_sidecar_has_none(bridge_module, make_entry):
    merged = bridge_module.merge_sidecar(
        {}, make_entry("/s", risk="low", description="Does a thing")
    )
    assert merged["desc"] == "Does a thing"


def test_merge_flattens_and_trims_bulwark_desc(bridge_module, make_entry):
    merged = bridge_module.merge_sidecar(
        {}, make_entry("/s", risk="low", description="  ── Does\n  a   thing  ")
    )
    assert merged["desc"] == "Does a thing"


def test_merge_truncates_long_desc(bridge_module, make_entry):
    long = "x" * 500
    merged = bridge_module.merge_sidecar({}, make_entry("/s", risk="low", description=long))
    assert len(merged["desc"]) <= bridge_module.MAX_DESC_LEN


def test_merge_is_idempotent(bridge_module, make_entry):
    """Merging the same entry twice must reach a fixed point."""
    e = make_entry("/s", risk="high", owner="tom", description="Backs up home")
    once = bridge_module.merge_sidecar({}, e)
    twice = bridge_module.merge_sidecar(once, e)
    assert once == twice


def test_merge_does_not_mutate_input(bridge_module, make_entry):
    existing = {"tags": ["favorite"], "desc": "mine"}
    snapshot = {"tags": list(existing["tags"]), "desc": existing["desc"]}
    bridge_module.merge_sidecar(existing, make_entry("/s", risk="high", owner="tom"))
    assert existing == snapshot
