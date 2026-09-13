Lockfiles must be consistent with package metadata. After any change to `pixi.toml`, run `pixi lock`.

Everything runs in a pixi environment. Any command (like `pytest`) must be prefixed with `pixi run` (e.g. `pixi run pytest`).

Code formatting must align with our standards. Run `pixi run lint` before `git commit`s to ensure this.

Commit messages and PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/), with an optional scope (e.g. `feat(search): filter dependency lists with /`, `fix: keep the active section tab visible`). PRs land as squash merges, so the PR title becomes the commit subject on `main` -- write it as the commit message you want in the history.

When using things from py-rattler and pulling data, always properly type them, try to avoid using `Any`.

Don't write tests with `monkeypatch`. Instead, write real-data tests using snapshots.

Feedback while working on a feature must be quick. A snapshot test runs the whole app, so the full suite takes minutes: while editing one feature, select only its tests with `-k`, which any test task forwards to pytest (`pixi run test -k search`, `pixi run snapshot-update -k search`). Run the full `pixi run test` before `git commit`s, and the full `pixi run snapshot-update` after deleting or renaming a test -- a filtered update never deletes the snapshots of the tests it did not select.
