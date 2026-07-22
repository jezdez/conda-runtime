# Release conda-runtime-updater

`conda-runtime-updater` is a noarch conda package published to the `jezdez/main`
channel on Anaconda.org. It is not installed from conda-ship and does not
depend on conda-ship.

Update the version in both of these files:

- `updater/pyproject.toml`
- `recipes/conda-runtime-updater/recipe.yaml`

Merge the release change, then create and push the matching tag:

```text
conda-runtime-updater-<version>
```

The release workflow uses the locked Pixi workspace in
`updater/pyproject.toml` to build and upload the noarch package. The upload is
equivalent to this command inside that environment:

```text
rattler-build upload anaconda \
  --owner "$ANACONDA_OWNER" \
  --channel main \
  PACKAGE_FILE
```

The `anaconda` GitHub environment must provide the `ANACONDA_API_KEY` and
`ANACONDA_OWNER` secrets. The workflow does not use `--force`. A published
filename is immutable, so a bad release requires a new version and build
number.

For an on-premises Anaconda server, run the same build and pass its server URL
to `rattler-build upload anaconda`. Other conda channel hosts can consume the
same noarch package without changing the updater.
