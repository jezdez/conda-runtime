# Releasing the standalone runtime

The standalone runtime release is separate from the
`conda-runtime-updater` release.

## Prepare the runtime

Publish the updater package required by the runtime lock before preparing the
runtime release.

Set `runtime-version` in `runtime/conda.toml` to the bundled conda version.
Use `X.Y.Z.postN` for a runtime-only rebuild of the same conda version. Every
runtime version starts with update package build number `0`.

If package inputs change, regenerate and commit `runtime/conda.lock` or
`tests/e2e/gen1/conda.lock` as applicable. A runtime-only `.postN` rebuild with
the same package inputs leaves both locks unchanged. The release workflow does
not solve or change either lock.

## Rehearse the release

After the release changes merge, run the workflow manually on the exact commit
that would be tagged:

```text
gh workflow run release-runtime.yml --ref main
```

The manual run builds all five native executables and update packages, checks
the complete distribution, and runs the two-layer update proof on Linux,
macOS, and Windows. It does not create a GitHub release or upload to
Anaconda.org. It does not access release credentials or publish attestations.

The two-layer proof uses a temporary `file://` channel, so its executables are
stamped for that channel. Conda-ship rejects an executable from a different
update source by design. The proof therefore uses the same committed locks and
released conda-ship builder to create a local-channel generation instead of
claiming to exercise the final Anaconda.org-stamped bytes. The native build
jobs separately verify that each update package contains its finalized release
executable byte for byte.

Do not create the tag unless that candidate run passes. Tag the same commit the
candidate used. The tag workflow repeats the build and proof before it can
publish anything.

## Create the release

Create an unprefixed tag that exactly matches `runtime-version`, such as
`26.5.3` or `26.5.3.post1`.

The workflow uses the conda-ship action and release assets from exactly 0.6.4.
It builds these five directly managed variants:

| Conda subdirectory | Runner | Runtime target |
| --- | --- | --- |
| `linux-64` | `ubuntu-latest` | `x86_64-unknown-linux-gnu` |
| `linux-aarch64` | `ubuntu-24.04-arm` | `aarch64-unknown-linux-gnu` |
| `osx-64` | `macos-15-intel` | `x86_64-apple-darwin` |
| `osx-arm64` | `macos-15` | `aarch64-apple-darwin` |
| `win-64` | `windows-latest` | `x86_64-pc-windows-msvc` |

Each job bootstraps its executable once, then packages those exact executable
bytes with `cs package-update`. The package verifier checks the native package
identity, extracts the sole payload, and compares its size and SHA-256 digest
with the finalized executable. The tag build attests executables and native
update packages.

The GitHub release assets use direct ownership. Their installers refuse to
replace an existing executable because direct runtime updates are coordinated
through `conda self update`. Future Homebrew or PyPI artifacts must be built
separately with external ownership and their package-manager update
instruction.

## Publication order

The workflow passes the five executables, installer scripts, and their attested
`SHA256SUMS` to `gh release create`. GitHub CLI creates a draft, uploads every
asset, and publishes the release before immutability takes effect. An upload
failure removes the unfinished draft. A separate restartable job verifies the
release attestation and every local asset. The workflow refuses to replace an
existing release. Immutable releases must be enabled for this repository.

Only after the GitHub release is public does the `anaconda` environment upload
the five native packages to the configured owner and `main` channel. Configure
that environment with `ANACONDA_OWNER=jezdez` and an `ANACONDA_API_KEY` token
that can write through the API and manage conda repositories.

Package artifacts retain their `linux-64`, `linux-aarch64`, `osx-64`,
`osx-arm64`, and `win-64` directories while they move between jobs. Their
basenames are identical, so flattening them would lose release files.

The upload does not use `--force`. If an executable or package is wrong, make a
new runtime version rather than replacing a published file. A rerun skips an
existing package only when its identity, size, and SHA-256 match, then waits
until all five packages are visible through the Anaconda.org API and channel
repodata.
