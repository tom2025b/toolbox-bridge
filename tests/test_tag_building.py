"""Tests for the tag/badge/description building logic.

These cover the pure translation functions that turn a Bulwark entry into the
bridge-managed `risk:` / `owner:` tags and the optional risk badge, plus the
classifier that decides whether a tag is bridge-owned.
"""
from __future__ import annotations


def test_bridge_tags_from_risk_and_owner(bridge_module, make_entry):
    tags = bridge_module.bridge_tags_from(
        make_entry("/s.sh", risk="high", owner="tom")
    )
    assert tags == ["risk:high", "owner:tom"]


def test_bridge_tags_from_risk_only(bridge_module, make_entry):
    tags = bridge_module.bridge_tags_from(make_entry("/s.sh", risk="low"))
    assert tags == ["risk:low"]


def test_bridge_tags_from_owner_only(bridge_module, make_entry):
    tags = bridge_module.bridge_tags_from(make_entry("/s.sh", owner="root"))
    assert tags == ["owner:root"]


def test_bridge_tags_from_empty_entry(bridge_module, make_entry):
    assert bridge_module.bridge_tags_from(make_entry("/s.sh")) == []


def test_bridge_tags_ignores_empty_string_values(bridge_module):
    # Falsy risk/owner must not produce dangling "risk:"/"owner:" tags.
    assert bridge_module.bridge_tags_from({"risk": "", "owner": ""}) == []


def test_is_bridge_tag_recognizes_managed_prefixes(bridge_module):
    assert bridge_module.is_bridge_tag("risk:high")
    assert bridge_module.is_bridge_tag("owner:tom")


def test_is_bridge_tag_rejects_user_tags(bridge_module):
    assert not bridge_module.is_bridge_tag("favorite")
    assert not bridge_module.is_bridge_tag("category:network")
    assert not bridge_module.is_bridge_tag("riskier")  # not the risk: prefix


def test_desc_badge_only_for_elevated_risk(bridge_module, make_entry):
    assert bridge_module.desc_badge_from(make_entry("/s", risk="medium")) == "[RISK: MEDIUM]"
    assert bridge_module.desc_badge_from(make_entry("/s", risk="high")) == "[RISK: HIGH]"
    assert bridge_module.desc_badge_from(make_entry("/s", risk="critical")) == "[RISK: CRITICAL]"


def test_desc_badge_none_for_low_and_missing_risk(bridge_module, make_entry):
    assert bridge_module.desc_badge_from(make_entry("/s", risk="low")) is None
    assert bridge_module.desc_badge_from(make_entry("/s")) is None


def test_strip_our_badge_removes_trailing_badge(bridge_module):
    assert bridge_module.strip_our_badge("Backs up home  [RISK: HIGH]") == "Backs up home"


def test_strip_our_badge_leaves_clean_desc_untouched(bridge_module):
    assert bridge_module.strip_our_badge("Backs up home") == "Backs up home"


def test_strip_our_badge_only_strips_trailing(bridge_module):
    # A badge-looking token in the middle of the desc is left alone.
    desc = "Mentions [RISK: HIGH] in the middle of text"
    assert bridge_module.strip_our_badge(desc) == desc


def test_sidecar_path_for(bridge_module):
    assert (
        str(bridge_module.sidecar_path_for("/home/tom/x.sh"))
        == "/home/tom/x.sh.scriptvault.yaml"
    )
