# Release process

Publish a release only after acceptance testing of single-flight,
multiple-separate, and merged workflows.

## Prepare

1. Update `src/elios_colorizer/__init__.py` and `pyproject.toml` to the release
   version.
2. Move the applicable entries from **Unreleased** in `CHANGELOG.md` into a
   dated release section.
3. Confirm the README compatibility and limitation statements.
4. Commit the release changes and ensure the working tree is clean.

## Validate and package

Run the release script from a PowerShell prompt:

```powershell
./scripts/setup.ps1
./scripts/release.ps1 -Version 1.3.0
```

The script verifies version consistency and a clean Git tree, runs the complete
test suite, builds the portable application, runs its packaged synthetic
self-test, creates the Windows ZIP, and writes its SHA-256 checksum.

Test the generated ZIP on a second Windows computer before publishing it.

## Publish

1. Create the signed or annotated version tag at the verified commit.
2. Create a GitHub release from that tag and use the matching changelog section
   as the release notes.
3. Upload the Windows ZIP and `.sha256` file from `dist/`.
4. Mark it as the latest release after verifying the uploaded downloads.
5. Confirm that the README download link resolves to the new release.

Do not upload source flight data, self-test output, caches, local camera
candidates, or the unpackaged `dist/EliosColorizer` directory.
