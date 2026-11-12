# Validation and next steps

Snapshot prepared November 12, 2026.

## Current state

Prototype for Apple-platform testing. Running generated UI tests needs macOS and Xcode. Linear flows intentionally do not replace arbitrary handwritten UI automation.

## Validation

PASS: 39 Python tests, including sample-project discovery. Ruby xcodeproj was installed into an isolated tool directory for discovery. Generated device UI tests were not run.

Publication checks: the exact Git tree and its new history are scanned locally with Gitleaks before the repository becomes public. Reviewed scanner exceptions are restricted to synthetic fixture secrets, an invalid test token, a browser preference name and the published NIST AES test vector. Private histories are not included.

## Next action

Follow the README quick start, run the documented focused tests, then use isolated inputs for a manual walkthrough. Real hardware, release signing, background behavior and live-server compatibility are outside this publication check.
