"""REST snapshot drift checks. No network: PyPI and the spec fetch are stubbed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts import check_snowflake_drift as drift

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "scripts" / "snowflake_rest_snapshot.tsv"
EXTRA = ROOT / "scripts" / "snowflake_rest_extra.tsv"
SPEC = """
openapi: 3.0.0
paths:
  /api/v2/accounts:
    get:
      operationId: listAccounts
"""
NEW_SPEC = """
openapi: 3.0.0
paths:
  /api/v2/accounts:
    get:
      operationId: listAccounts
  /api/v2/widgets:
    post:
      operationId: createWidget
"""


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _baseline(directory: Path) -> tuple[Path, Path]:
    snap = directory / "snap.tsv"
    extra = directory / "extra.tsv"
    _write(
        snap,
        "\n".join(
            [
                f"# snowflake-rest-api-specs {drift.PINNED_SHA}",
                "# abc specifications/account.yaml",
                "account\tGET\t/api/v2/accounts\tgap\tno tool yet",
                "",
            ]
        ),
    )
    _write(extra, "# none\n")
    return snap, extra


def _fetcher(spec: str, *, commit: int = 200, tree: int = 200, raw: int = 200, sha: str | None = None):
    def get(url: str) -> tuple[int, str]:
        if url.endswith("/commits/main"):
            return commit, json.dumps({"sha": sha or drift.PINNED_SHA})
        if "/git/trees/" in url:
            return tree, json.dumps({"tree": [{"path": "specifications/account.yaml"}]})
        if url.endswith("/specifications/account.yaml"):
            return raw, spec
        return 404, "missing"

    return get


@pytest.fixture
def quiet_pypi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drift, "check_pypi_versions", lambda: {})


def test_new_operation_exits_1(tmp_path: Path, quiet_pypi: None, capsys: pytest.CaptureFixture[str]) -> None:
    snap, extra = _baseline(tmp_path)
    code = drift.main(
        ["--fail-on-new-unmapped", "--snapshot", str(snap), "--extra", str(extra)],
        get=_fetcher(NEW_SPEC),
    )
    assert code == 1
    assert "❌ New unmapped Snowflake REST ops:" in capsys.readouterr().out


def test_baselined_operation_exits_0(tmp_path: Path, quiet_pypi: None) -> None:
    snap, extra = _baseline(tmp_path)
    code = drift.main(
        ["--fail-on-new-unmapped", "--snapshot", str(snap), "--extra", str(extra)],
        get=_fetcher(SPEC),
    )
    assert code == 0


def test_without_the_flag_exits_0(tmp_path: Path, quiet_pypi: None) -> None:
    snap, extra = _baseline(tmp_path)
    code = drift.main(["--snapshot", str(snap), "--extra", str(extra)])
    assert code == 0


def test_bad_tag_exits_2(tmp_path: Path, quiet_pypi: None) -> None:
    snap = tmp_path / "snap.tsv"
    extra = tmp_path / "extra.tsv"
    _write(snap, "account\tGET\t/api/v2/accounts\tnope\treason\n")
    _write(extra, "# none\n")
    code = drift.main(["--snapshot", str(snap), "--extra", str(extra)])
    assert code == 2


def test_unregistered_target_exits_1(tmp_path: Path, quiet_pypi: None, monkeypatch: pytest.MonkeyPatch) -> None:
    snap = tmp_path / "snap.tsv"
    extra = tmp_path / "extra.tsv"
    _write(snap, "account\tGET\t/api/v2/accounts\ttool\tnot_a_registered_tool\n")
    _write(extra, "# none\n")
    monkeypatch.setattr(drift, "registered_names", lambda: {"queries_query"})
    code = drift.main(["--snapshot", str(snap), "--extra", str(extra)])
    assert code == 1


def test_committed_snapshot_has_418_ops(quiet_pypi: None) -> None:
    _header, _blobs, rows = drift.load_table(SNAPSHOT, width=5)
    assert len(rows) == 418
    assert {parts[3] for parts in rows} <= drift.TAGS
    assert drift.main(["--snapshot", str(SNAPSHOT), "--extra", str(EXTRA)]) == 0


def test_trees_api_failure_exits_2(tmp_path: Path, quiet_pypi: None, capsys: pytest.CaptureFixture[str]) -> None:
    snap, extra = _baseline(tmp_path)
    code = drift.main(
        ["--fail-on-new-unmapped", "--snapshot", str(snap), "--extra", str(extra)],
        get=_fetcher(SPEC, tree=500),
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "::error::Spec fetch failed:" in captured.err
    assert "New unmapped" not in captured.out
    assert "New unmapped" not in captured.err


def test_raw_file_failure_exits_2(tmp_path: Path, quiet_pypi: None, capsys: pytest.CaptureFixture[str]) -> None:
    snap, extra = _baseline(tmp_path)
    code = drift.main(
        ["--fail-on-new-unmapped", "--snapshot", str(snap), "--extra", str(extra)],
        get=_fetcher(SPEC, raw=404),
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "::error::Spec fetch failed:" in captured.err
    assert "New unmapped" not in captured.out


def test_rate_limit_exits_2(tmp_path: Path, quiet_pypi: None, capsys: pytest.CaptureFixture[str]) -> None:
    snap, extra = _baseline(tmp_path)
    code = drift.main(
        ["--fail-on-new-unmapped", "--snapshot", str(snap), "--extra", str(extra)],
        get=_fetcher(SPEC, commit=403),
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "HTTP 403" in captured.err
    assert "New unmapped" not in captured.out
    assert "New unmapped" not in captured.err


def test_yaml_parse_error_exits_2(tmp_path: Path, quiet_pypi: None, capsys: pytest.CaptureFixture[str]) -> None:
    snap, extra = _baseline(tmp_path)
    code = drift.main(
        ["--fail-on-new-unmapped", "--snapshot", str(snap), "--extra", str(extra)],
        get=_fetcher("paths: ["),
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "YAML parse error" in captured.err
    assert "New unmapped" not in captured.out
