# Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io/), certificate by
[SignPath Foundation](https://signpath.org/).

## Who signs

Only the maintainer of this repository, Joselma Jemk (GitHub: `Joselma-Jemk`), approves signing requests.
Every maintainer with access to the signing project must use multi-factor authentication on GitHub and on SignPath.

## What gets signed

Only binaries built by the `release` workflow of this repository, from a tagged commit (`v*`), on GitHub-hosted runners:

- `wifimanager.exe` (the program and the blocking service),
- `wifimanager-<version>-windows-setup.exe` (the installer).

Nothing else is signed: no code from other projects, no third-party binaries, no build produced on a personal computer.

## Source and dependencies

WifiManager is open source under the MIT license (see [LICENSE](../LICENSE)). The whole source is public in this
repository. Runtime dependencies are open source too (PySide6 / Qt, segno).

## Verification

Before a release is published, the workflow:

1. runs the test suite (`pytest`),
2. starts the built program and checks that it stays alive,
3. installs the signed installer silently, checks its files and the permissions of its folder, then uninstalls it and
   checks the cleanup.

Each release lists the SHA-256 hash of every file in `SHA256SUMS`.

## Reporting problems

A signed file that behaves unexpectedly, or a suspicion of a compromised build, should be reported privately as
described in [SECURITY.md](../SECURITY.md).

## Privacy

The signing request contains only the build artifact and the repository identity. No personal data is sent.
