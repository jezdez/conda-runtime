#!/usr/bin/env python3
"""Check Anaconda.org release credentials without changing repository state."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

API_URL = "https://api.anaconda.org"


def authentication(token: str) -> dict[str, object]:
    request = urllib.request.Request(
        f"{API_URL}/authentication",
        headers={
            "Accept": "application/json",
            "Authorization": f"token {token}",
            "User-Agent": "conda-runtime-release",
            "x-binstar-api-version": "1.12.2",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            value = json.load(response)
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"Anaconda.org rejected ANACONDA_API_KEY with HTTP {error.code}."
        ) from None
    if not isinstance(value, dict):
        raise SystemExit("Anaconda.org returned invalid authentication metadata.")
    return value


def main() -> None:
    token = os.environ.get("ANACONDA_API_KEY")
    owner = os.environ.get("ANACONDA_OWNER")
    if not token:
        raise SystemExit("ANACONDA_API_KEY is not set.")
    if owner != "jezdez":
        raise SystemExit("ANACONDA_OWNER must be jezdez.")

    value = authentication(token)
    raw_scopes = value.get("scopes")
    if not isinstance(raw_scopes, list) or not all(
        isinstance(scope, str) for scope in raw_scopes
    ):
        raise SystemExit("Anaconda.org returned invalid token scopes.")
    scopes = set(raw_scopes)
    if not scopes.intersection({"all", "api", "api:write"}):
        raise SystemExit("ANACONDA_API_KEY cannot write through the Anaconda.org API.")
    if not scopes.intersection({"all", "repos", "conda"}):
        raise SystemExit("ANACONDA_API_KEY cannot manage conda repositories.")

    print(f"Anaconda.org release credentials are valid for {owner}.")


if __name__ == "__main__":
    main()
