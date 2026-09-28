"""Copy unmodified dependency licenses beside the portable application."""
from importlib.metadata import distributions
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
destination = root / 'dist' / 'EliosColorizer'
included_distributions = {
    'laspy', 'lazrs', 'lz4', 'mcap', 'mcap-ros2-support', 'numpy',
    'opencv-python-headless', 'pyinstaller', 'pyside6-essentials', 'pyyaml',
    'scipy', 'shiboken6', 'zstandard',
}
for name in ('README.md', 'LICENSE', 'CHANGELOG.md', 'THIRD_PARTY_NOTICES.md', 'requirements-lock.txt'):
    shutil.copy2(root / name, destination / name)
for name in ('CALIBRATION.md', 'INSTALLATION.md', 'OUTPUT_FORMAT.md',
             'TIME_SYNCHRONIZATION.md', 'USER_GUIDE.md'):
    (destination / 'docs').mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / 'docs' / name, destination / 'docs' / name)
for name in ('README.md', 'example_profile.json'):
    target = destination / 'camera_profiles' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / 'camera_profiles' / name, target)
for distribution in distributions():
    name = distribution.metadata['Name']
    if name.lower() not in included_distributions:
        continue
    for relative in distribution.files or ():
        parts = [part.lower() for part in relative.parts]
        basename = relative.name.lower()
        is_named_notice = basename.startswith(('license', 'copying', 'notice'))
        is_text_in_license_dir = (
            'licenses' in parts
            and '__pycache__' not in parts
            and relative.suffix.lower() in ('', '.txt', '.md', '.rst')
        )
        if is_named_notice or is_text_in_license_dir:
            source = distribution.locate_file(relative)
            if not source.is_file():
                continue
            # Preserve nested license names while excluding arbitrary '..' components.
            safe = [part for part in relative.parts if part not in ('..', '.', '/')]
            target = destination / 'licenses' / name / Path(*safe)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
python_license = Path(sys.base_prefix) / 'LICENSE.txt'
if python_license.is_file():
    target = destination / 'licenses' / 'Python'
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_license, target / 'LICENSE.txt')
print(f'Documentation and dependency notices copied to {destination}')
