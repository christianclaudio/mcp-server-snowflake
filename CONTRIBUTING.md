# Contributing to mcp-server-snowflake
 
Thank you for your interest in contributing to `mcp-server-snowflake`!

---

## 🛠️ Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/christianclaudio/mcp-server-snowflake.git
   cd mcp-server-snowflake
   ```

2. **Install from the lockfile:**
   ```bash
   uv sync --locked --extra dev
   ```

3. **Run tests & quality checks:**
   ```bash
   # Linting and formatting
   uv run ruff check .
   uv run ruff format --check .

   # Type checking
   uv run mypy src/

   # Test suite
   uv run pytest -q

   # Tool contract verification
   uv run python scripts/check_tool_contract.py
   ```

---

## 📐 Design Guidelines

- **Tool Signatures**: All tool functions must use type annotations and include clear docstrings.
- **Safety First**: Any tool modifying state must check `client.config.read_only`. Destructive tools require `confirm: bool = False`.
- **Secret Redaction**: Never log credentials, session tokens, or private keys.
- **Contract Coverage**: Whenever a tool is added or modified, update `scripts/check_tool_contract.py` and `tests/test_coverage_full.py`.
- **Tool Annotations**: Every tool carries all four `ToolAnnotations` hints (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) from one of the constants in `server.py` (`_READ_ONLY`, `_WRITE_SAFE`, `_DESTRUCTIVE`, `_IDEMPOTENT`). Add a new tool's local name to `_DESTRUCTIVE_NAMES`, `_IDEMPOTENT_NAMES`, or the read-only names and prefixes; an unlisted name is annotated as a non-destructive, non-idempotent write. `scripts/check_tool_contract.py` asserts the read-only, destructive, and idempotent counts, and that every tool sets those three hints.

---

## 🚀 Pull Request Workflow

1. Create a descriptive branch: `git checkout -b feat/my-new-tool`
2. Ensure all tests pass locally: `pytest`
3. Commit with conventional commit format (`feat:`, `fix:`, `docs:`, `test:`)
4. Submit your pull request to `main`!

---

## 🔀 Pull Requests & Versions

Merges are performed via **Squash Merge** with Conventional Commit titles (`feat:`, `fix:`, `docs:`, `chore:`). Do not edit version numbers or `CHANGELOG.md`: the git tag is the version, and GitHub Releases are the changelog. The squash commit message is the PR body, so every breaking PR (any `type!:` title, such as `feat!:` or `fix!:`) carries a `BREAKING CHANGE:` footer, with the migration steps, as the final paragraph of the PR body before CodeRabbit's generated summary. `scripts/release_notes.py` stops at the CodeRabbit marker line and ignores everything after it, so a footer inside that summary never reaches the release notes.
