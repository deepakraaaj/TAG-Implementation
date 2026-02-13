# Contributing to TAG Backend

Thank you for contributing! To maintain a "perfect" codebase, please follow these guidelines.

## 🛠️ Development Setup

1.  **Clone & Install**:
    ```bash
    git clone https://github.com/deepakraaaj/TAG-Implementation.git
    pip install -r requirements.txt
    ```
2.  **Environment**: Use the `.vscode` settings provided. Ensure you have the **Python** and **Pylint** extensions installed.

## 📐 Standards

- **Formatting**: We use `black`. It is configured to run on save.
- **Linting**: We use `pylint`. Fix all warnings before submitting a PR.
- **Testing**: Use `make test` to run local tests. All new features must include unit tests.

## 🌿 Branch Strategy

- `main`: Production-ready, stable code.
- `feature/*`: New features or enhancements.
- `fix/*`: Bug fixes.

## 🚢 Pull Request Process

1.  Create a branch from `main`.
2.  Implement your changes and tests.
3.  Verify with `make test`.
4.  Submit a PR with a clear description of changes.
5.  CI (GitHub Actions) must pass before merge.
