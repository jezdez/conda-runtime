# Releasing the standalone runtime

The standalone runtime release is separate from the
`conda-runtime-updater` release.

## Prepare the runtime

Publish the updater package required by the runtime lock before preparing the
runtime release.

Set `runtime-version` in `runtime/conda.toml` to the bundled conda version.
Use `X.Y.Z.postN` for a runtime-only rebuild of the same conda version. Every
runtime version starts with update package build number `0`.

Regenerate and commit `runtime/conda.lock`. The release workflow does not solve
or change the lock.

## Create the release

Create an unprefixed tag that exactly matches `runtime-version`, such as
`26.5.3` or `26.5.3.post1`.

The workflow uses the conda-ship action and release assets from exactly 0.6.2.
It builds these five directly managed variants:

| Conda subdirectory | Runner | Runtime target |
| --- | --- | --- |
| `linux-64` | `ubuntu-latest` | `x86_64-unknown-linux-gnu` |
| `linux-aarch64` | `ubuntu-24.04-arm` | `aarch64-unknown-linux-gnu` |
| `osx-64` | `macos-15-intel` | `x86_64-apple-darwin` |
| `osx-arm64` | `macos-15` | `aarch64-apple-darwin` |
| `win-64` | `windows-latest` | `x86_64-pc-windows-msvc` |

Each job bootstraps its executable once, then packages those exact executable
bytes with `cs package-update`. Executables and native update packages receive
GitHub artifact attestations.

## Publication order

The workflow uploads the five executables and their attested `SHA256SUMS` to a
draft GitHub release. It checks the draft asset names, bytes, checksums, tag,
and title before publishing the immutable release. It refuses to replace an
existing release.

Only after the GitHub release is public does the `anaconda` environment upload
the five native packages to the configured owner and `main` channel. Configure
that environment with the `ANACONDA_API_KEY` and `ANACONDA_OWNER` secrets.

Package artifacts retain their `linux-64`, `linux-aarch64`, `osx-64`,
`osx-arm64`, and `win-64` directories while they move between jobs. Their
basenames are identical, so flattening them would lose release files.

The upload does not use `--force`. If an executable or package is wrong, make a
new runtime version rather than replacing a published file.
