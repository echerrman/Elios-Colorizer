# Installation

## Portable Windows application

1. Open the [latest GitHub release](https://github.com/echerrman/Elios-Colorizer/releases/latest).
2. Download the Windows x64 portable ZIP from **Assets**.
3. Extract the entire ZIP to a normal writable folder.
4. Open the extracted `EliosColorizer` folder and run `EliosColorizer.exe`.

Keep the `_internal` directory beside the executable. The EXE is not a
standalone single-file program. No Python, ROS, cloud account, or external video
decoder installation is required for the portable build.

CUDA acceleration requires a compatible NVIDIA GPU and current NVIDIA driver.
The portable application includes its native CUDA processing component; end
users do not need to install the CUDA Toolkit or Visual Studio. Systems without
a usable CUDA device continue with the CPU fallback.

Windows may display a reputation warning for an unsigned early release. Verify
that the file came from this repository's Releases page and compare its SHA-256
checksum when one is published with the release.

## Updating

Portable releases do not modify previous installations. Extract the new release
into a separate folder, verify it, and remove the older folder after confirming
your workflow. Camera profiles can remain in a separate permanent location.

## Building from source

Install 64-bit Python 3.12 and clone the repository. To include the NVIDIA
CUDA integration checkpoint, also install the NVIDIA CUDA Toolkit and the
Visual Studio 2022 **Desktop development with C++** workload. If either native
build dependency is absent, the build remains usable with the CPU fallback.

```powershell
git clone https://github.com/echerrman/Elios-Colorizer.git
cd Elios-Colorizer
./scripts/setup.ps1
& ./.venv/Scripts/python.exe -m pytest
./scripts/build.ps1
```

The portable application is written to `dist/EliosColorizer`. Development
dependencies and build output remain local and are ignored by Git.

Run the packaged CUDA diagnostic with:

```powershell
./dist/EliosColorizer/EliosColorizer.exe --cuda-check ./cuda-checkpoint
Get-Content ./cuda-checkpoint/cuda-checkpoint.json
```
