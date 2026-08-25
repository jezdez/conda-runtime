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

The manual run builds all five native executables, their platform-specific
SBOMs, and update packages. It checks the complete distribution and runs the
two-layer update proof on Linux, macOS, and Windows. It does not create a GitHub
release or upload to Anaconda.org. It does not access release credentials or
publish attestations.

The two-layer proof uses a temporary `file://` channel, so its executables are
stamped for that channel. Conda-ship rejects an executable from a different
update source by design. The proof therefore creates local-channel generations
from the committed locks instead of claiming to exercise the final
Anaconda.org-stamped bytes. The native build jobs separately verify that each
update package contains its finalized release executable byte for byte.

On Linux and macOS, generation one is built with released conda-ship 0.8.0 and
generation two is built with released conda-ship 0.9.0. Those jobs prove that
the published legacy-format readers can apply the new native format through the
existing `conda-runtime` package. On Windows, both generations are built with
conda-ship 0.9.0 and use `conda-runtime`. That job proves native updates after a
fresh 0.9.0-format installation. It does not demonstrate an in-place update
from a published 0.8.0-format Windows executable.

Do not create the tag unless that candidate run passes. Tag the same commit the
candidate used. The tag workflow repeats the build and proof before it can
publish anything.

## Create the release

Create an unprefixed tag that exactly matches `runtime-version`, such as
`26.7.1.post3`.

The workflow uses the conda-ship action and release assets from exactly 0.9.0.
It builds one executable for each of these five targets:

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
with the finalized executable. Each macOS build must also pass strict native
signature validation before packaging.

All five platforms build from the canonical `runtime` project and publish
native update packages under `conda-runtime`.

Conda-ship also creates a CycloneDX 1.7 SBOM for every executable. Each SBOM
describes the resolved conda packages, package hashes and locations, available
license data, and known dependency relationships. The release verifier checks
the runtime version, target platform, executable name, nonempty package graph,
and explicit incomplete-coverage marker. The tag build separately attests each
executable, SBOM, and native update package.

The SBOM does not claim to inventory the host operating system, Rust crates in
the outer executable, or other vendored or statically linked code outside the
resolved conda package records.

The GitHub installers record direct ownership after installing the canonical
executable. Future Homebrew and Python packages can distribute the same
executable bytes. Their delivery integrations must record external ownership
and the corresponding upgrade instruction.

## Windows alpha installations

Existing Windows alpha installations must be replaced with a fresh
`26.7.1.post3` installation. Move aside the existing executable and managed
prefix, then run the new installer with a new prefix. Do not reuse the old
direct-install metadata.

## Publication order

The workflow passes the five executables, five SBOMs, two installer scripts,
and their attested `SHA256SUMS` to `gh release create`. GitHub CLI creates a
draft, uploads every asset, and publishes the release before immutability takes
effect. An upload failure removes the unfinished draft. A separate restartable
job verifies the release attestation and every local asset. The workflow
refuses to replace an existing release. Immutable releases must be enabled for
this repository.

Only after the GitHub release is public does the `anaconda` environment upload
the five `conda-runtime` native packages to the configured owner and `main`
channel. Configure that environment with `ANACONDA_OWNER=jezdez` and an
`ANACONDA_API_KEY` token that can write through the API and manage conda
repositories.

Package artifacts retain their `linux-64`, `linux-aarch64`, `osx-64`,
`osx-arm64`, and `win-64` directories while they move between jobs. Their
basenames are identical, so the parent directories remain part of the
validated publication identity.

The upload does not use `--force`. If an executable or package is wrong, make a
new runtime version rather than replacing a published file. A rerun skips an
existing package only when its identity, size, and SHA-256 match, then waits
until every package is visible through the expected Anaconda.org package API
and channel repodata.
