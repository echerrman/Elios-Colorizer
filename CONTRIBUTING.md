# Contributing to Elios Colorizer

Thank you for helping improve Elios Colorizer. Contributions should preserve
the application's local-first operation, point-cloud geometry, diagnostic
traceability, and conservative handling of uncertain synchronization or camera
calibration.

## Before making a change

- Search existing issues and pull requests first.
- Open an issue before a large feature, file-format change, or colorization
  algorithm change so the approach can be discussed.
- Report security concerns privately according to [SECURITY.md](SECURITY.md).
- Never commit flight footage, customer data, facility names, credentials,
  generated point clouds, local paths, caches, or proprietary Inspector files.
- Submit only data and code you have permission to share.

## Development setup

Elios Colorizer targets 64-bit Windows and Python 3.12.

```powershell
./scripts/setup.ps1
& ./.venv/Scripts/python.exe -m pytest
```

Build the portable application with:

```powershell
./scripts/build.ps1
```

After building, run the deterministic packaged smoke test into a new temporary
directory:

```powershell
./dist/EliosColorizer/EliosColorizer.exe --self-test ./selftest-output
```

Delete generated build and test output before committing.

## Change guidelines

- Keep user-facing workflows simple and explain failures in actionable terms.
- Preserve source point coordinates and attributes unless a documented workflow
  explicitly requires a derived merged cloud.
- Keep unobserved points and mark them with `Colorized=0`.
- Do not silently infer missing timing, pose, or calibration data.
- Add focused tests for behavioral changes and regressions.
- Update documentation when inputs, output fields, compatibility, or limitations
  change.
- Treat camera profiles as validated only when their provenance supports that
  claim.

## Pull requests

Keep each pull request focused. Explain the concrete problem, resulting
behavior, validation performed, and any limitations. Complete the pull-request
template and make sure the test suite passes before requesting review.

By submitting a contribution, you agree that it is licensed under the repository's
[MIT License](LICENSE).
