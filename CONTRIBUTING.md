# Contributing to [Project Name]

Thank you for your interest in contributing! We welcome issues, bug fixes, documentation improvements, and feature requests.

## How to Contribute

### 1. Report Bugs or Suggest Features
* Check the existing GitHub Issues to see if your topic is already being discussed.
* If not, open a new Issue with a clear title and description.

### 2. Local Development Setup
We use `uv` for environment management, dependency resolution, and toolchains.

1. Fork and clone the repository:
   ```bash
   git clone https://github.com
   cd YOUR_REPO_NAME
   ```

2. Install development dependencies and set up the environment:
   ```bash
   uv sync
   ```

### 3. Code Quality Standards
Before submitting your changes, ensure your code passes our linting and static analysis checks:

* **Format and Lint Check:**
  ```bash
  uv run ruff check .
  ```
* **Type Check:**
  ```bash
  uv run mypy .
  ```

### 4. Submit a Pull Request
1. Create a descriptive branch name (`git checkout -b feature/cool-new-thing`).
2. Make your changes and commit them with clear commit messages.
3. Push to your fork (`git push origin feature/cool-new-thing`).
4. Open a Pull Request against our default branch.
5. Ensure your PR description clearly states what problem is being solved.

## Code of Conduct
Please note that this project is released with a Contributor Code of Conduct. By participating in this project, you agree to abide by its terms.
