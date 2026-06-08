"""Tests for write_sidecars — dry-run vs apply, and on-disk idempotency.

write_sidecars is where the bridge actually touches the filesystem, so these
tests assert on real files under tmp_path and on the printed report.
"""
from __future__ import annotations

import yaml


def read_sidecar(script_path: str) -> dict:
    from pathlib import Path

    return yaml.safe_load(Path(script_path + ".scriptvault.yaml").read_text())


def test_dry_run_writes_nothing_to_disk(bridge_module, make_entry, script, capsys):
    path = script()
    bridge_module.write_sidecars([make_entry(path, risk="high", owner="tom")], apply=False)

    from pathlib import Path

    assert not Path(path + ".scriptvault.yaml").exists()
    out = capsys.readouterr().out
    assert "would write" in out
    assert "DRY RUN" in out


def test_apply_writes_sidecar_to_disk(bridge_module, make_entry, script, capsys):
    path = script()
    bridge_module.write_sidecars([make_entry(path, risk="high", owner="tom")], apply=True)

    data = read_sidecar(path)
    assert data["tags"] == ["risk:high", "owner:tom"]
    out = capsys.readouterr().out
    assert "wrote" in out
    assert "DRY RUN" not in out


def test_apply_then_rerun_is_unchanged(bridge_module, make_entry, script, capsys):
    """Second apply with identical input writes nothing and reports up-to-date."""
    path = script()
    e = make_entry(path, risk="high", owner="tom")

    bridge_module.write_sidecars([e], apply=True)
    capsys.readouterr()  # drain first run's output

    before = read_sidecar(path)
    bridge_module.write_sidecars([e], apply=True)
    after = read_sidecar(path)

    assert before == after
    out = capsys.readouterr().out
    assert "1 already up-to-date" in out
    assert "wrote 0 sidecar" in out


def test_apply_does_not_clobber_user_content(bridge_module, make_entry, script):
    """An existing sidecar's user tags/desc survive an apply."""
    from pathlib import Path

    path = script()
    sidecar = Path(path + ".scriptvault.yaml")
    sidecar.write_text(
        yaml.safe_dump({"tags": ["favorite"], "desc": "Hand written"})
    )

    bridge_module.write_sidecars([make_entry(path, risk="high", owner="tom")], apply=True)

    data = read_sidecar(path)
    assert "favorite" in data["tags"]
    assert "risk:high" in data["tags"]
    assert data["desc"] == "Hand written  [RISK: HIGH]"


def test_re_apply_after_user_edit_only_touches_managed_fields(
    bridge_module, make_entry, script
):
    path = script()
    bridge_module.write_sidecars([make_entry(path, risk="low", owner="tom")], apply=True)

    # User adds a tag and a desc out of band.
    from pathlib import Path

    sidecar = Path(path + ".scriptvault.yaml")
    data = yaml.safe_load(sidecar.read_text())
    data["tags"].append("favorite")
    data["desc"] = "User note"
    sidecar.write_text(yaml.safe_dump(data))

    # Re-run with elevated risk.
    bridge_module.write_sidecars([make_entry(path, risk="critical", owner="tom")], apply=True)

    after = read_sidecar(path)
    assert "favorite" in after["tags"]
    assert "risk:critical" in after["tags"]
    assert "risk:low" not in after["tags"]
    assert after["desc"] == "User note  [RISK: CRITICAL]"


def test_entries_without_path_are_skipped(bridge_module, make_entry, capsys):
    bridge_module.write_sidecars([{"risk": "high"}], apply=True)
    out = capsys.readouterr().out
    assert "wrote 0 sidecar" in out


def test_sidecar_files_are_not_themselves_bridged(bridge_module, make_entry, capsys):
    # A path that is already a sidecar must be ignored, not double-wrapped.
    bridge_module.write_sidecars(
        [make_entry("/some/x.sh.scriptvault.yaml", risk="high")], apply=True
    )
    out = capsys.readouterr().out
    assert "wrote 0 sidecar" in out


def test_malformed_sidecar_is_skipped_not_overwritten(bridge_module, make_entry, script, capsys):
    from pathlib import Path

    path = script()
    sidecar = Path(path + ".scriptvault.yaml")
    sidecar.write_text("this: is: not: valid: yaml: [")  # broken

    original = sidecar.read_text()
    bridge_module.write_sidecars([make_entry(path, risk="high", owner="tom")], apply=True)

    assert sidecar.read_text() == original  # untouched
    out = capsys.readouterr().out
    assert "1 skipped" in out
