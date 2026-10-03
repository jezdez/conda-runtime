# Runtime update notifications

`conda-runtime-updater` can show a notice after an interactive command when a
newer standalone runtime package is available for the current platform. The
stamped executable selects the package from its configured update channel.
This includes runtime-only post releases and higher build numbers.

For a directly installed runtime, the notice points to `conda self update`.
Externally managed installations show the recorded update instruction when
available, or guidance to use the package manager that installed the runtime.
A notice does not download or install update packages.

Notifications require an executable built with conda-ship 0.10.0 or newer,
which provides the `v1/probe` helper. Older executables can still use the
updater's existing transaction coordination, but do not produce these notices.

## Network and offline behavior

The plugin uses a one-day interval for online checks of each installation and
runtime version. Between online checks, it reads the Rust helper's cached
channel metadata. Explicit offline mode uses that cache for remote channels
and can read local file channels. An offline check does not postpone the next
online check.

The helper gives the network request and response body two seconds, then uses
cached metadata if the request fails. Python stops the helper after five
seconds if it has not returned. Notices based on cached metadata say so and
include its age when available. A missing or unusable cache produces no
notice. Helper errors and an unwritable notification cache do not fail the
conda command.

The plugin saves check and notification times in the user's cache directory
to suppress repeated notices for the same candidate for one day. If that
history cannot be saved, or commands run concurrently, checks and notices can
repeat. Package selection and repodata caching remain in the executable.

Cached metadata does not prove that the update package is cached. An offline
update still needs the package payload. The normal update command checks the
candidate again before staging it.

## Output and settings

Notices appear on stderr after `info`, `list`, `create`, `env update`,
`install`, `update`, and `remove`. All three standard streams must be attached
to a terminal. JSON, quiet, dry-run, and noninteractive commands produce no
notices or notification checks. Shell activation does not trigger a notice.

Disable runtime update notifications with:

```sh
conda config --set plugins.runtime_update_notifications false
```

The updater package installs `condarc.d/conda-runtime-updater.yaml` to disable
conda's generic outdated-conda warning. Upgrading the package also installs
this default in existing prefixes without rewriting their `.condarc` files.
User configuration and environment variables can override it. To restore the
generic warning:

```sh
conda config --set notify_outdated_conda true
```

The package's default also applies when runtime update notifications are
disabled, the updater is installed in an unmanaged prefix, or plugins are
disabled. The same prefix-relative configuration file is installed on Windows.
Removing the updater package removes the file.
