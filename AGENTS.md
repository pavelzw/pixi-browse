## Package Management

This repository uses the Pixi package manager. The full documentation for Pixi can be found at https://pixi.sh/latest/llms.txt or with `pixi --help`.
If you change `pixi.toml`, please run `pixi lock` afterwards.

If you want to run any commands (like `pytest`), prepend them with `pixi run`.

## Code Standards

### Required Before Each Commit

- To ensure that our code formatting aligns with our standards, run `pixi run pre-commit-run` before committing any changes.
