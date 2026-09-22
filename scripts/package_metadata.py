"""Copy unmodified dependency licenses beside the portable application."""
from importlib.metadata import distributions
from pathlib import Path
import shutil
import sys

root = Path(__file__).resolve().parents[1]
destination = root / 'dist' / 'EliosColorizer'
for name in ('README.md', 'THIRD_PARTY_NOTICES.md', 'requirements-lock.txt'):
    shutil.copy2(root / name, destination / name)
shutil.copytree(root / 'docs', destination / 'docs', dirs_exist_ok=True)
shutil.copytree(root / 'camera_profiles', destination / 'camera_profiles', dirs_exist_ok=True)
for distribution in distributions():
    name = distribution.metadata['Name']
    for relative in distribution.files or ():
        parts = [part.lower() for part in relative.parts]
        basename = relative.name.lower()
        if ('licenses' in parts or basename.startswith(('license', 'copying', 'notice'))):
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
