# conda-runtime-updater

`conda-runtime-updater` is the transaction coordinator installed inside the
managed prefix of the standalone conda runtime.

It has no user-facing subcommand. For root-prefix conda updates, it coordinates
the stamped outer executable with conda's existing pre-solve and post-command
hooks. It depends on conda and does not depend on conda-ship.

The pre-solve hook runs only when the target prefix is conda's root prefix and
the requested specs include `conda`, or when the operation is a root `--all`
update. Environment updates do not affect the outer executable.

For a directly managed runtime, the plugin checks and stages the outer update,
lets conda complete the inner transaction, then applies the staged executable.
It holds the runtime update lock across both layers. An interactive command
asks for approval. JSON mode and `--yes` use conda's existing noninteractive
behavior. The hook pins the inner `conda` package to the conda version bundled
by the current or staged outer runtime while preserving unrelated configured
pins. A runtime-only `.postN` suffix does not change the inner conda version.
Matching root updates reject `--no-pin` because it would bypass that
coordination. Dry runs read the local runtime record and apply its current
conda pin without checking, staging, or locking an outer update.

For an externally managed runtime, an available outer update stops the inner
transaction and reports the update command for the detected package manager.
The plugin recognizes Homebrew, pipx, uv tools, and RECORD-bound Python wheel
installations from receipts those tools already write. Detection records the
final user-facing instruction in the runtime record. A downstream external
installer can record its own instruction. The package manager replaces the
executable before the conda update is retried.

The reported commands are:

- Homebrew: `brew update && brew upgrade conda`
- pipx: `pipx upgrade conda-runtime`
- global pipx: `pipx upgrade --global conda-runtime`
- uv tool: `uv tool upgrade conda-runtime`
- pip: the owning Python's `-m pip install --upgrade conda-runtime`
- uv pip: `uv pip install --python <owning-python> --upgrade conda-runtime`

The Python commands include an exact interpreter only when the wheel receipt
and environment layout prove which Python owns the executable. Otherwise the
message asks the user to update `conda-runtime` in its owning Python
environment.

Installed ownership remains in the runtime's `.<runtime>.json` record. Receipt
detection can change direct ownership to external ownership, but the absence of
a receipt never grants direct ownership. The plugin invokes the stamped
executable's version-one local helper actions and does not add a daemon,
service, receipt, or updater command. If the inner transaction fails, the old
executable remains usable. The next runtime invocation and update attempt
recover or discard the interrupted state.
