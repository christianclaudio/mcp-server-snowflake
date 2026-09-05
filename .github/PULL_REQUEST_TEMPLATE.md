## What changed

<!-- One or two sentences. Focus on why, not just what. -->

## Checklist

- [ ] `mypy src/` is clean
- [ ] `ruff check .` and `ruff format --check .` are clean
- [ ] `pytest` passes with 100% mocked coverage
- [ ] `python scripts/check_tool_contract.py` exits 0 (asserts all 140 tools)
- [ ] If tools were added/removed: tool-count and annotation assertions updated
- [ ] If a tool writes or deletes: read-only gating & safety hints preserved
- [ ] If behavior changed: README / `COOKBOOK.md` updated
- [ ] `CHANGELOG.md` updated
- [ ] No credentials, passwords, or private keys committed

## Verification

<!-- Paste the actual command output you ran. -->
