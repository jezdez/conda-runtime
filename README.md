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

On Linux and macOS, download and run the installer from the latest immutable
release:

```sh
curl -fsSLO https://github.com/jezdez/conda-runtime/releases/latest/download/install.sh
sh install.sh
```

On Windows, use PowerShell without requiring curl:

```powershell
Invoke-WebRequest https://github.com/jezdez/conda-runtime/releases/latest/download/install.ps1 -OutFile install.ps1
.\install.ps1
```

Both installers verify the executable against the release's `SHA256SUMS` and
install `conda` under `~/.local/bin` by default. A directly installed runtime
updates its managed conda prefix and outer executable together through
`conda self update`.

Release instructions for the internal plugin are in
[`docs/releasing-the-updater.md`](docs/releasing-the-updater.md).

Release instructions for the standalone executable and its native update
packages are in
[`docs/releasing-the-runtime.md`](docs/releasing-the-runtime.md).
