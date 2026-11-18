#!/usr/bin/env python3
"""Snowflake API and SDK drift monitor.

Prints the latest Snowflake SDK versions on PyPI (a failure to reach PyPI never
fails the run) and the number of tools the server registers. With
``--fail-on-new-unmapped`` it fetches the Snowflake REST specifications and
exits 1 when an operation or a specification file is not in the committed
snapshot. A fetch or parse failure exits 2 before any comparison. The exact
tool count stays in ``scripts/check_tool_contract.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import yaml

SDK_PACKAGES = [
    "snowflake-connector-python",
    "snowflake-core",
    "snowflake-snowpark-python",
]
TAGS = frozenset({"tool", "gap", "deprecated", "excluded"})
SPEC_REPO = "snowflakedb/snowflake-rest-api-specs"
PINNED_SHA = "990e25d97236a11826c9eed40e587c2b859e5680"
ROOT = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT = ROOT / "snowflake_rest_snapshot.tsv"
DEFAULT_EXTRA = ROOT / "snowflake_rest_extra.tsv"
METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

HttpGet = Callable[[str], tuple[int, str]]


class SnapshotError(Exception):
    """The local snapshot or extra list is not usable. Exit 2."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class FetchError(Exception):
    """A specification download or parse failed. Exit 2, before comparison."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def check_pypi_versions() -> dict[str, str]:
    """Fetch latest versions of Snowflake SDK packages using curl."""
    versions = {}
    for pkg in SDK_PACKAGES:
        try:
            url = f"https://pypi.org/pypi/{pkg}/json"
            res = subprocess.run(
                ["curl", "-sL", "--max-time", "5", url],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                latest = data.get("info", {}).get("version", "unknown")
                versions[pkg] = latest
            else:
                versions[pkg] = "Unavailable"
        except Exception as e:
            versions[pkg] = f"Error: {e}"
    return versions


def http_get(url: str) -> tuple[int, str]:
    """GET ``url`` with curl. A ``GITHUB_TOKEN`` is sent as a bearer header."""
    command = ["curl", "-sS", "-L", "--max-time", "30", "-w", "\n%{http_code}", url]
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        command[1:1] = ["-H", f"Authorization: Bearer {token}"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"curl exit {result.returncode}"
        raise FetchError(detail)
    body, _, status = result.stdout.rpartition("\n")
    try:
        code = int(status.strip())
    except ValueError as exc:
        raise FetchError("curl returned no HTTP status") from exc
    return code, body


def _split_row(line: str, *, width: int, where: str) -> list[str]:
    parts = line.split("\t")
    if len(parts) < width or any(not part.strip() for part in parts[:width]):
        raise SnapshotError(f"Empty field in {where}: {line!r}")
    return parts


def load_table(path: Path, *, width: int) -> tuple[str, list[str], list[list[str]]]:
    """Return the header text, blob lines, and data rows."""
    if not path.is_file():
        raise SnapshotError(f"Missing snapshot {path}")
    header: list[str] = []
    blobs: list[str] = []
    rows: list[list[str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        if raw.startswith("#"):
            header.append(raw)
            blob = raw[1:].strip().split()
            if len(blob) == 2 and blob[1].startswith("specifications/"):
                blobs.append(blob[1])
            continue
        parts = _split_row(raw, width=width, where=path.name)
        tag = parts[3]
        if tag not in TAGS:
            raise SnapshotError(f"Bad tag {tag!r} in {path.name}")
        key = (parts[0], parts[1], parts[2])
        if key in seen:
            raise SnapshotError(f"Duplicate line in {path.name}: {parts[0]} {parts[1]} {parts[2]}")
        seen.add(key)
        rows.append(parts)
    return "\n".join(header), blobs, rows


def registered_names() -> set[str]:
    from snowflake_mcp.config import SnowflakeConfig
    from snowflake_mcp.server import create_server

    server = create_server(config=SnowflakeConfig(account="dummy_acc", user="dummy_user"))
    tools = getattr(server, "_tool_manager", None)
    names = set(tools._tools) if tools else set(getattr(server, "_tools", {}))
    return {name for name in names if isinstance(name, str)}


def _check_targets(rows: list[list[str]], registry: set[str], where: str) -> list[str]:
    missing: list[str] = []
    for parts in rows:
        if parts[3] == "tool" and parts[4] not in registry:
            missing.append(f"{where}: {parts[4]}")
    return missing


def parse_operations(file_name: str, text: str) -> list[tuple[str, str, str]]:
    """``(file stem, METHOD, path)`` for every operation in one specification."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FetchError(f"YAML parse error in {file_name}: {exc}") from exc
    if not isinstance(document, dict):
        raise FetchError(f"YAML parse error in {file_name}: document is not a mapping")
    paths = document.get("paths", {})
    if not isinstance(paths, dict):
        raise FetchError(f"YAML parse error in {file_name}: paths is not a mapping")
    stem = Path(file_name).name.removesuffix(".yaml")
    found: list[tuple[str, str, str]] = []
    for url, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method.lower() not in METHODS or not isinstance(operation, dict):
                continue
            found.append((stem, method.upper(), str(url)))
    return found


def fetch_remote(get: HttpGet) -> tuple[str, list[str], list[tuple[str, str, str]]]:
    """Commit SHA, specification paths, and operations. Failures raise ``FetchError``."""
    commit_url = f"https://api.github.com/repos/{SPEC_REPO}/commits/main"
    code, body = get(commit_url)
    if code != 200:
        raise FetchError(f"git commit API HTTP {code}")
    try:
        commit = json.loads(body)
        sha = str(commit["sha"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FetchError(f"git commit API JSON: {exc}") from exc
    tree_url = f"https://api.github.com/repos/{SPEC_REPO}/git/trees/{sha}?recursive=1"
    code, body = get(tree_url)
    if code != 200:
        raise FetchError(f"git trees API HTTP {code}")
    try:
        tree = json.loads(body)
        entries = tree["tree"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FetchError(f"git trees API JSON: {exc}") from exc
    files = [
        item["path"]
        for item in entries
        if isinstance(item, dict)
        and str(item.get("path", "")).startswith("specifications/")
        and str(item.get("path", "")).endswith(".yaml")
    ]
    operations: list[tuple[str, str, str]] = []
    for path in files:
        raw_url = f"https://raw.githubusercontent.com/{SPEC_REPO}/{sha}/{path}"
        code, text = get(raw_url)
        if code != 200:
            raise FetchError(f"raw file HTTP {code} for {path}")
        operations.extend(parse_operations(path, text))
    return sha, files, operations


def _print_fetch_error(message: str) -> int:
    print(f"::error::Spec fetch failed: {message}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None, *, get: HttpGet | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Snowflake SDK and REST specification drift.")
    parser.add_argument("--fail-on-new-unmapped", action="store_true")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--extra", type=Path, default=DEFAULT_EXTRA)
    args = parser.parse_args(argv)

    print("=" * 60)
    print("🔍 SNOWFLAKE API & SDK DRIFT MONITOR")
    print("=" * 60)
    print("\n📦 Latest Snowflake SDK Releases on PyPI:")
    for pkg, ver in check_pypi_versions().items():
        print(f"  • {pkg}: {ver}")

    registry = registered_names()
    print(f"\n🛡️ Registered MCP tools: {len(registry)}")

    try:
        header, blobs, rows = load_table(args.snapshot, width=5)
        extra_header, _, extra_rows = load_table(args.extra, width=5)
    except SnapshotError as exc:
        print(exc.message, file=sys.stderr)
        return 2
    del header, extra_header

    missing = _check_targets(rows, registry, args.snapshot.name)
    missing.extend(_check_targets(extra_rows, registry, args.extra.name))
    counts: dict[str, int] = {tag: 0 for tag in sorted(TAGS)}
    for parts in rows:
        counts[parts[3]] += 1
    print(f"\nREST snapshot: {len(rows)} ops ({', '.join(f'{tag} {counts[tag]}' for tag in sorted(counts))})")
    print(f"Documented endpoints missing from the pinned specifications: {len(extra_rows)}")
    if missing:
        print("❌ Snapshot tool target is not a registered wire name:")
        for item in missing:
            print(f"  • {item}")
        return 1

    if not args.fail_on_new_unmapped:
        print("\n✅ Drift scan completed successfully. All contracts aligned.")
        return 0

    try:
        sha, files, operations = fetch_remote(get or http_get)
    except FetchError as exc:
        return _print_fetch_error(exc.message)

    if sha != PINNED_SHA:
        print(f"⚠️ Spec commit changed: snapshot {PINNED_SHA} remote {sha}")
    remote_files = set(files)
    local_files = set(blobs)
    new_files = sorted(remote_files - local_files)
    removed_files = sorted(local_files - remote_files)
    remote_keys = {(method, path) for _stem, method, path in operations}
    local_keys = {(parts[1], parts[2]) for parts in rows}
    new_ops = [op for op in operations if (op[1], op[2]) not in local_keys]
    gone = [parts for parts in rows if (parts[1], parts[2]) not in remote_keys]
    for parts in gone:
        print(f"⚠️ Snapshot line is no longer in the specifications: {parts[0]} {parts[1]} {parts[2]}")
    extra_paths = {(parts[1], parts[2]) for parts in extra_rows}
    for stem, method, path in operations:
        if (method, path) in extra_paths:
            print(f"⚠️ Documented extra endpoint is now in the specifications: {method} {path} ({stem})")
    if new_ops or new_files or removed_files:
        print(f"❌ New unmapped Snowflake REST ops: {len(new_ops)}")
        for stem, method, path in new_ops:
            print(f"  • {stem} {method} {path}")
        for path in new_files:
            print(f"  • new specification file {path}")
        for path in removed_files:
            print(f"  • removed specification file {path}")
        return 1

    print("\n✅ Drift scan completed successfully. All contracts aligned.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
