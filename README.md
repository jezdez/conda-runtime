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

Release instructions for the internal plugin are in
[`docs/releasing-the-updater.md`](docs/releasing-the-updater.md).
