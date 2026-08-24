# conda-runtime

`conda-runtime` builds and publishes a standalone `conda` executable with an
embedded managed prefix.

The runtime is built with
[conda-ship](https://github.com/jezdez/conda-ship). The generated prefix
contains `conda`, `conda-self`, and `conda-runtime-updater`. It does not contain
conda-ship.

This repository owns the runtime package set, lockfiles, update integration,
native builds, and distribution packaging. The project is under active
development and has not published a stable release.

## Direct installation

On glibc-based x86-64 or ARM64 Linux and on Intel or Apple silicon macOS,
download and run the installer from the latest immutable release:

```sh
curl -fsSLO https://github.com/jezdez/conda-runtime/releases/latest/download/install.sh
sh install.sh
```

On Windows, use PowerShell without requiring curl:

```powershell
Invoke-WebRequest https://github.com/jezdez/conda-runtime/releases/latest/download/install.ps1 -OutFile install.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

The execution-policy override applies only to that PowerShell process. It does
not change the user or machine policy.

Both installers verify the executable against the release's `SHA256SUMS` and
install the outer `conda` executable under `~/.local/bin` by default. They
refuse to replace an existing executable. Update a directly installed runtime
with `conda self update`. To recover a damaged executable, move it aside and
use the installer from that same runtime release.

The installer bootstraps a separate managed prefix in the platform's user-data
directory:

- Linux: `$XDG_DATA_HOME/conda/runtime`, or
  `~/.local/share/conda/runtime` when `XDG_DATA_HOME` is unset
- macOS: `~/Library/Application Support/conda/runtime`
- Windows: `$env:LOCALAPPDATA\conda\runtime`

Set `CONDA_SHIP_PREFIX` persistently to use a different managed-prefix path.
The installer directory controls only the outer executable. If
`CONDA_SHIP_PREFIX` is set for bootstrap but omitted later, the runtime selects
the default prefix and bootstraps it separately.

The embedded prefix can bootstrap without network access after the executable
has been downloaded. Runtime updates use the configured conda channel. Offline
updates can use only update metadata and packages already present in the
runtime update cache.

> [!IMPORTANT]
> Windows runtimes through `26.7.1.post1` cannot apply the native executable
> format introduced by conda-ship 0.9.0. Starting with `26.7.1.post2`, Windows
> uses the separate `conda-runtime-v2` update package so legacy installations do
> not discover an incompatible executable. Preserve and move aside the existing
> executable and managed prefix, then use the `26.7.1.post2` installer with a
> fresh prefix. Do not reuse the old direct-install metadata. That fresh
> installation can receive later native updates from `conda-runtime-v2`.

## Software bill of materials

Runtime releases starting with `26.7.1.post1` include a platform-specific
CycloneDX 1.7 software bill of materials with the `.cdx.json` suffix. It lists
the exact conda packages resolved into that platform's executable and records
their versions, package identifiers, hashes, download locations, licenses when
available, and known dependency relationships.

The SBOM lets users inspect what the runtime contains without unpacking the
executable. Developers and redistributors can use its package identifiers and
hashes to connect the runtime to vulnerability, license, and provenance tools.
Each SBOM is covered by the release's `SHA256SUMS` file and has its own GitHub
artifact attestation.

The inventory is deliberately marked incomplete. It covers the resolved conda
package graph, not the host operating system, Rust crates used to build the
outer executable, or other vendored or statically linked code outside conda
package records.

Release instructions for the internal plugin are in
[`docs/releasing-the-updater.md`](docs/releasing-the-updater.md).

Release instructions for the standalone executable and its native update
packages are in
[`docs/releasing-the-runtime.md`](docs/releasing-the-runtime.md).
