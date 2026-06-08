"""End-to-end tests for main() / argument handling.

The real bulwark binary is not required: we monkeypatch find_bulwark and
run_bulwark_scan so the CLI logic (flag handling, dry-run vs apply routing,
sidecar self-filtering) is exercised in isolation.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def _patch_scan(bridge_module, monkeypatch, entries):
    monkeypatch.setattr(bridge_module, "find_bulwark", lambda explicit: "/fake/bulwark")
    monkeypatch.setattr(bridge_module, "run_bulwark_scan", lambda bulwark, paths: entries)


def test_report_mode_is_read_only(bridge_module, monkeypatch, script, capsys):
    path = script()
    _patch_scan(bridge_module, monkeypatch, [{"path": path, "risk": "high", "owner": "tom"}])

    rc = bridge_module.main([])

    assert rc == 0
    assert not Path(path + ".scriptvault.yaml").exists()
    assert "write-sidecars" in capsys.readouterr().out


def test_write_sidecars_dry_run_by_default(bridge_module, monkeypatch, script, capsys):
    path = script()
    _patch_scan(bridge_module, monkeypatch, [{"path": path, "risk": "high", "owner": "tom"}])

    rc = bridge_module.main(["--write-sidecars"])

    assert rc == 0
    assert not Path(path + ".scriptvault.yaml").exists()
    assert "DRY RUN" in capsys.readouterr().out


def test_write_sidecars_apply_writes_files(bridge_module, monkeypatch, script, capsys):
    path = script()
    _patch_scan(bridge_module, monkeypatch, [{"path": path, "risk": "high", "owner": "tom"}])

    rc = bridge_module.main(["--write-sidecars", "--apply"])

    assert rc == 0
    sidecar = Path(path + ".scriptvault.yaml")
    assert sidecar.exists()
    assert yaml.safe_load(sidecar.read_text())["tags"] == ["risk:high", "owner:tom"]


def test_apply_without_write_sidecars_warns_and_does_nothing(
    bridge_module, monkeypatch, script, capsys
):
    path = script()
    _patch_scan(bridge_module, monkeypatch, [{"path": path, "risk": "high", "owner": "tom"}])

    rc = bridge_module.main(["--apply"])

    assert rc == 0
    # No file written: --apply alone falls through to the read-only report.
    assert not Path(path + ".scriptvault.yaml").exists()
    err = capsys.readouterr().err
    assert "no effect without --write-sidecars" in err


def test_cli_filters_out_sidecar_entries(bridge_module, monkeypatch, capsys):
    # Even if bulwark returns a sidecar path, the CLI drops it before processing.
    entries = [
        {"path": "/some/x.sh.scriptvault.yaml", "risk": "high"},
    ]
    _patch_scan(bridge_module, monkeypatch, entries)

    rc = bridge_module.main(["--write-sidecars", "--apply"])

    assert rc == 0
    assert "wrote 0 sidecar" in capsys.readouterr().out
