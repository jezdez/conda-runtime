# AGENTS.md - conda-runtime coding guidelines

## Project boundaries

- This repository owns the standalone `conda` runtime and the
  `conda-runtime-updater` plugin used inside its managed prefix.
- Use released conda-ship builders and templates. Do not install conda-ship in
  the generated runtime.
- Do not add behavior to conda or conda-self for runtime delivery.
- Keep the updater package in this repository. Do not create a separate updater
  repository.
- The Homebrew formula is named `conda`. The Python distribution is named
  `conda-runtime`.

## Release artifacts

- Published executable and conda package filenames are immutable.
- Never overwrite an existing release asset or conda package filename.
- Upload update packages to the `jezdez` owner and `main` channel on
  Anaconda.org.
- Publish Anaconda.org update packages only after the corresponding executable
  release is complete.

## Python

- Use `from __future__ import annotations` in Python modules.
- Keep `conda-runtime-updater` pure Python and dependent on conda, not
  conda-ship.
