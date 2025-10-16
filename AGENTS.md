<!-- OPENSPEC:START -->
# OpenSpec Instructions

These instructions are for AI assistants working in this project.

Always open `@/openspec/AGENTS.md` when the request:
- Mentions planning or proposals (words like proposal, spec, change, plan)
- Introduces new capabilities, breaking changes, architecture shifts, or big performance/security work
- Sounds ambiguous and you need the authoritative spec before coding

Use `@/openspec/AGENTS.md` to learn:
- How to create and apply change proposals
- Spec format and conventions
- Project structure and guidelines

Keep this managed block so 'openspec update' can refresh the instructions.

<!-- OPENSPEC:END -->

## Engineering Principles

- Favor clarity over cleverness. Write code that another engineer can understand quickly.
- Keep functions small and focused; extract helpers when logic branches out.
- Guard against data corruption by validating inputs and raising explicit exceptions.
- Preserve backward compatibility for the CLI and catalog schema unless a spec explicitly allows breaking changes.
- Default to immutability—avoid mutating shared dictionaries/lists in place unless necessary.

## Development Workflow

1. Review relevant specs in `openspec/` and confirm whether an OpenSpec change proposal is required.
2. Use **Test-Driven Development** when possible:
   - Write or update a failing test that captures the desired behavior.
   - Implement the minimal code change to make the test pass.
   - Refactor for readability while keeping tests green.
3. Keep commits scoped and descriptive. Reference the related change proposal when applicable.
4. Run formatting, linting, and tests before committing.

## Tooling

- **uv** is the preferred package/task runner.
  - Install dependencies with `uv pip install -e .[test]` if needed.
  - Run commands via `uv run <tool>` to ensure the project environment is used.
- **Ruff** handles linting and formatting.
  - Lint: `uv run ruff check .`
  - Format: `uv run ruff format .`
- **Pytest** executes the test suite.
  - Run targeted tests: `uv run pytest tests/test_catalog.py::<test_name>`
  - Run full suite: `uv run pytest`

Ensure new tooling configuration lives in `pyproject.toml` so CI and other developers inherit the settings automatically.

## Coding Guidelines

- Follow PEP 8 style conventions; use Ruff to enforce consistency.
- Prefer type hints on public functions and critical internal helpers.
- Keep imports grouped (stdlib, third-party, local) and remove unused imports.
- Use dataclasses or simple objects for structured data instead of raw dictionaries when it clarifies intent (e.g., catalog records).
- Document complex logic paths with short, focused docstrings or module-level comments.

## Testing Expectations

- All new behavior must include unit or integration test coverage.
- CLI-related tests should exercise real command invocations through `subprocess` with isolated temporary directories.
- Use `pytest.importorskip("slatedb")` for optional dependencies so the suite remains green without them.
- Maintain deterministic tests—avoid relying on wall-clock time or network resources.

## Documentation & Communication

- Update README or inline docs whenever user-facing behavior changes.
- Reflect new capabilities in OpenSpec specs/proposals before coding changes.
- Provide clear PR descriptions summarizing changes, risks, and verification steps.

Following these practices keeps the Python codebase consistent, maintainable, and ready for collaboration.➕