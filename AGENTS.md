# AGENTS.md - conda-runtime contributor and coding guidelines

## Project structure

- This repository owns the standalone `conda` runtime and the
  `conda-runtime-updater` plugin installed in its managed prefix.
- `updater/src/conda_runtime_updater/` contains the pure-Python conda plugin:
  - `plugin.py` owns hook registration, trigger policy, prompting, and the
    transaction session lifecycle.
  - `metadata.py` discovers and validates `.<runtime>.json`.
  - `helper.py` invokes and validates the stamped executable's version-one
    update helper.
  - `locking.py` owns cross-platform transaction locking.
- `updater/tests/` contains focused pytest coverage for the plugin package.
- `recipes/conda-runtime-updater/` builds the noarch updater package.
- `runtime/` owns the conda workspace manifest, committed lockfile, downstream
  condarc, and conda-ship build policy for the standalone executable.
- `.github/workflows/` owns updater CI, native runtime builds, end-to-end update
  tests, and publication.

## Project boundaries

- Use released conda-ship builders and templates. Do not build production
  runtimes from a conda-ship checkout.
- Do not install conda-ship in the generated runtime. It is a build-time tool.
- Keep `conda-runtime-updater` in this repository and dependent on supported
  Python and conda versions, not conda-ship.
- Do not add runtime delivery behavior to conda or conda-self.
- The updater has no user-facing conda subcommand. It coordinates an outer
  executable update around matching root-prefix conda transactions.
- Keep distribution policy here. Do not add conda-runtime defaults to generic
  conda-ship builder paths.
- The Homebrew formula is named `conda`. The Python distribution is named
  `conda-runtime`.

## Local development

- Use the Pixi workspace in `updater/pyproject.toml` for updater development.
- Run updater tests with
  `pixi run --manifest-path updater/pyproject.toml --locked test`.
- Run lint and formatting checks with
  `pixi run --manifest-path updater/pyproject.toml --locked lint` and
  `pixi run --manifest-path updater/pyproject.toml --locked format-check`.
- Build the noarch conda package with
  `pixi run --manifest-path updater/pyproject.toml --locked build-conda-package`.
- When interacting with GitHub, use `gh` and follow repository-native issue and
  pull request templates.

## Imports and dependencies

- Use relative imports for intra-package references. Absolute
  `conda_runtime_updater.*` imports belong in tests and entry points.
- Keep imports at module scope unless they are platform-specific or deferring a
  dependency has a measured conda startup benefit. The platform-specific lock
  imports in `locking.py` are intentional.
- Minimize the dependency graph. Prefer the standard library, conda APIs, and
  already-required packages over new dependencies.
- Pin minimum supported versions in Python package metadata. Runtime manifests
  may use exact pins because they define a reproducible distribution.

## Python style

- Use `from __future__ import annotations` in every Python module.
- Use modern annotations such as `str | None` and `list[str]`.
- Follow the Conda Style Guide for imports, typing, and docstrings.
- Use short Google-style docstrings where argument or return documentation is
  useful. Do not repeat annotation types in docstrings.
- Do not use section comments to group code. If a module needs section
  headings, split it by responsibility.
- Comments explain non-obvious intent or constraints. Do not narrate the code.

## Conda integration

- Use conda's public APIs and plugin hooks instead of reimplementing context,
  path, configuration, or transaction behavior.
- Register the updater through the `[project.entry-points.conda]` entry point
  and `conda_pre_solves` and `conda_post_commands` hooks.
- Coordinate only root-prefix solves that request `conda` or a root
  update-all operation. Never affect non-root environments.
- Discover delivery metadata only from the runtime's `.<runtime>.json` record.
  Do not add another receipt, service, daemon, or installer-detection layer.
- Ignore runtime records whose delegate is not `conda`.
- Conda plugin settings in `.condarc` belong under `plugins`. Protect the
  updater as `plugins.self_permanent_packages`.

## Output and transaction contracts

- `--json` is an output format, not a behavior switch. The updater must not
  print human-readable status to stdout when conda is producing JSON.
- Quiet mode must stay quiet. Route recovery diagnostics to stderr only when
  conda is not quiet.
- Dry-run operations must not check, download, stage, lock, or replace the
  outer executable.
- Hold the runtime update lock from staging through completion of the inner
  conda transaction and outer apply step.
- Preserve a usable old executable when the inner transaction or replacement
  fails. Recovery must be possible on the next invocation.
- Keep the stamped executable helper contract at version one unless a
  deliberate compatibility change introduces a new version.

## Testing

- Write plain pytest functions with descriptive names. Do not group tests in
  classes.
- Use pytest fixtures such as `tmp_path`, `monkeypatch`, and `capsys` and small
  real fakes. Do not use `unittest.mock` or another mock library.
- Parameterize repeated cases and give non-obvious cases readable IDs.
- Prefer tests that cross the real conda plugin boundary when practical.
  Unit tests should still isolate network access and user prefixes.
- Subprocess assertions must include compact stderr context when an exit code
  fails.
- Cover Linux, macOS, and Windows behavior. Windows tests must exercise the
  deferred executable replacement path.
- End-to-end tests must cover offline embedded bootstrap, JSON, quiet mode,
  dry-run, declined approval, inner failure, successful two-layer update,
  idempotence, interruption recovery, and external replacement reconciliation.

## Runtime manifest and lockfile

- After changing any `[tool.pixi.*]` dependencies, features, tasks, platforms,
  channels, or workspace settings in `updater/pyproject.toml`, run
  `pixi lock --manifest-path updater/pyproject.toml` and commit
  `updater/pixi.lock` in the same change.
- Never hand-edit `updater/pixi.lock`.
- The production runtime uses `runtime/conda.toml`, `runtime/conda.lock`, and
  `runtime/runtime.condarc`.
- Keep the production package set limited to Python, conda, conda-self, and
  conda-runtime-updater unless a reviewed runtime requirement adds another
  package.
- Do not add conda-ship, conda-spawn, conda-express plugins, or a downstream
  replacement solver to the managed prefix.
- Use strict channel priority and keep the `jezdez` channel ahead of
  conda-forge.
- After changing dependencies, features, channels, platforms, or environments
  in `runtime/conda.toml`, run `conda workspace lock` from `runtime/` and commit
  the resulting `runtime/conda.lock` in the same change.
- Never hand-edit `runtime/conda.lock`.
- Build production artifacts with the exact released conda-ship version pinned
  by the runtime workflow.

## Release artifacts

- Published executable, GitHub release, PyPI, and conda package files are
  immutable. Fix a bad release with a new version or build number.
- Never use `--force` when uploading conda packages.
- Publish updater and native update packages to the `jezdez` owner and `main`
  channel on Anaconda.org.
- Finalize and attest executable bytes before creating the matching
  `conda-runtime` transport package.
- Publish GitHub executable assets, checksums, and installer scripts before
  uploading matching update packages to Anaconda.org. The channel upload is
  last so installed runtimes cannot discover an incomplete release.
- Preserve platform subdirectories while collecting native transport packages.
  Their basenames can be identical across conda subdirectories.
- Homebrew and PyPI builds use external ownership and provider-specific update
  instructions. They must not overwrite or impersonate the directly managed
  executable variant.

## Pull requests and issues

- Keep titles and prose literal. Describe current behavior and concrete scope.
- Write one line per paragraph or bullet in GitHub issue and pull request
  bodies. Let GitHub wrap prose in the browser.
- Keep commit subjects concise. Commit bodies may be wrapped for terminal
  reading.
