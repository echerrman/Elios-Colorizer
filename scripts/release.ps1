param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$executable = Join-Path $projectRoot 'dist\EliosColorizer\EliosColorizer.exe'
$archive = Join-Path $projectRoot "dist\EliosColorizer-v$Version-windows-x64.zip"
$checksum = Join-Path $projectRoot "dist\EliosColorizer-v$Version-windows-x64.zip.sha256"
$selfTest = Join-Path $projectRoot ".release-selftest-$Version"

if (-not (Test-Path -LiteralPath $python)) {
    throw 'Run scripts\setup.ps1 before preparing a release.'
}

$sourceVersion = & $python -c 'from elios_colorizer import __version__; print(__version__)'
if ($LASTEXITCODE -ne 0 -or $sourceVersion.Trim() -ne $Version) {
    throw "Application version '$sourceVersion' does not match requested release '$Version'."
}

$manifest = Get-Content -LiteralPath 'pyproject.toml' -Raw
if ($manifest -notmatch "(?m)^version = `"$([regex]::Escape($Version))`"\r?$") {
    throw "pyproject.toml does not declare version $Version."
}

$changes = & git status --porcelain
if ($LASTEXITCODE -ne 0 -or $changes) {
    throw 'Commit all public release changes before packaging.'
}

& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; release was not packaged.' }

& (Join-Path $PSScriptRoot 'build.ps1')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $executable)) {
    throw 'Portable build failed.'
}

if (Test-Path -LiteralPath $selfTest) {
    Remove-Item -LiteralPath $selfTest -Recurse -Force
}
$quotedSelfTest = '"' + $selfTest + '"'
$process = Start-Process -FilePath $executable -ArgumentList '--self-test', $quotedSelfTest -Wait -PassThru -WindowStyle Hidden
if ($process.ExitCode -ne 0) { throw 'Packaged application self-test failed.' }
$result = Get-Content -LiteralPath (Join-Path $selfTest 'selftest.json') -Raw | ConvertFrom-Json
if ($result.status -ne 'passed') { throw 'Packaged application did not report a passing self-test.' }

if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
if (Test-Path -LiteralPath $checksum) { Remove-Item -LiteralPath $checksum -Force }
Compress-Archive -LiteralPath 'dist\EliosColorizer' -DestinationPath $archive -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $checksum -Value "$hash  $(Split-Path -Leaf $archive)" -Encoding ascii

Write-Host "Release package: $archive"
Write-Host "SHA-256: $hash"
Write-Host "Upload the ZIP and .sha256 file to the matching GitHub release after tagging the verified commit."
