$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $projectRoot 'native\cuda\elios_cuda_probe.cu'
$outputDirectory = Join-Path $projectRoot 'build\cuda'
$output = Join-Path $outputDirectory 'elios_cuda_probe.dll'
Remove-Item -LiteralPath $output -Force -ErrorAction SilentlyContinue

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$visualStudio = if (Test-Path -LiteralPath $vswhere) {
    & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
}
if (-not $visualStudio) {
    Write-Warning 'Microsoft C++ Build Tools were not found. The portable app will retain CPU fallback only.'
    exit 0
}
$developerCommand = Join-Path $visualStudio 'Common7\Tools\VsDevCmd.bat'

$nvcc = $env:ELIOS_CUDA_NVCC
if (-not $nvcc) {
    $cudaRoot = Join-Path $env:ProgramFiles 'NVIDIA GPU Computing Toolkit\CUDA'
    $nvcc = Get-ChildItem -LiteralPath $cudaRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object { [version]($_.Name.TrimStart('v')) } -Descending |
        ForEach-Object { Join-Path $_.FullName 'bin\nvcc.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}
if (-not $nvcc -or -not (Test-Path -LiteralPath $nvcc)) {
    Write-Warning 'CUDA compiler not found. The portable app will retain CPU fallback only.'
    exit 0
}

# Import the x64 compiler environment into this PowerShell process before nvcc
# delegates host compilation to cl.exe.
$compilerEnvironment = & cmd.exe /d /s /c "call `"$developerCommand`" -arch=x64 -host_arch=x64 >nul && set"
$compilerPath = $compilerEnvironment | Where-Object { $_ -match '^PATH=' } | Select-Object -First 1
if ($compilerPath) {
    [Environment]::SetEnvironmentVariable('Path', $compilerPath.Substring(5), 'Process')
}
$compilerEnvironment | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$' -and $matches[1] -ine 'Path') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$cudaArchitectures = @(
    '-gencode=arch=compute_75,code=sm_75',
    '-gencode=arch=compute_80,code=sm_80',
    '-gencode=arch=compute_86,code=sm_86',
    '-gencode=arch=compute_89,code=sm_89',
    '-gencode=arch=compute_90,code=sm_90',
    '-gencode=arch=compute_100,code=sm_100',
    '-gencode=arch=compute_120,code=sm_120',
    '-gencode=arch=compute_120,code=compute_120'
)
& $nvcc $source -o $output --shared --cudart static -O3 $cudaArchitectures -Xcompiler=/MT
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output)) {
    throw 'CUDA checkpoint DLL build failed.'
}
Write-Host "CUDA checkpoint DLL: $output"
