# AGENTS Instructions

This repository provides Home Assistant integration for Foxtron DALI gateways. Follow these guidelines when contributing:

## Linting and Formatting
- Use `pre-commit run --files <changed file>` to format and lint only the files you modify.
- The configured hooks run `ruff` for linting and formatting and `mypy` for type checking. Fix any reported issues.

## Testing
- Execute the test suite with `pytest` before submitting changes.

## Coding Style
- Write Python using type hints and keep functions small and well documented.
- Prefer `ruff`'s default formatting; do not run other formatters.

## Documentation
- Update `README.md` or `custom_components/foxtron_dali/docs` when you change behaviour or add features.

## Commit Messages
- Use short, descriptive commit messages in the imperative mood (e.g., `Add light discovery test`).

