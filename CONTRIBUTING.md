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

---

## 🚀 Pull Request Workflow

1. Create a descriptive branch: `git checkout -b feat/my-new-tool`
2. Ensure all tests pass locally: `pytest`
3. Commit with conventional commit format (`feat:`, `fix:`, `docs:`, `test:`)
4. Submit your pull request to `main`!
