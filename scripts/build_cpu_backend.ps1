$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $projectRoot 'native\cpu\elios_cpu_projection.cpp'
$outputDirectory = Join-Path $projectRoot 'build\cpu'
$output = Join-Path $outputDirectory 'elios_cpu_projection.dll'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$visualStudio = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $visualStudio) { throw 'Microsoft C++ Build Tools were not found.' }
$developerCommand = Join-Path $visualStudio 'Common7\Tools\VsDevCmd.bat'
$compilerEnvironment = & cmd.exe /d /s /c "call `"$developerCommand`" -arch=x64 -host_arch=x64 >nul && set"
$compilerPath = $compilerEnvironment | Where-Object { $_ -match '^PATH=' } | Select-Object -First 1
if ($compilerPath) { [Environment]::SetEnvironmentVariable('Path', $compilerPath.Substring(5), 'Process') }
$compilerEnvironment | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$' -and $matches[1] -ine 'Path') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$object = Join-Path $outputDirectory 'elios_cpu_projection.obj'
$compilerArguments = @(
    '/nologo', '/O2', '/EHsc', '/LD', '/MT',
    $source,
    "/Fo$object",
    "/Fe$output"
)
& cl.exe @compilerArguments
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output)) { throw 'Native CPU backend build failed.' }
Write-Host "Native CPU backend: $output"
