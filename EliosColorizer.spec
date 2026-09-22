from PyInstaller.utils.hooks import collect_submodules, copy_metadata

hidden = collect_submodules('mcap') + collect_submodules('mcap_ros2')
data = [(f'assets/{name}', 'assets') for name in
    ('elios_colorizer.svg', 'theme-sun.svg', 'theme-moon.svg')]
for package in ('numpy', 'scipy', 'opencv-python-headless', 'laspy', 'PySide6-Essentials', 'mcap', 'mcap-ros2-support', 'lazrs'):
    data += copy_metadata(package)
a = Analysis(['scripts/desktop_entry.py'], pathex=['src'], binaries=[], datas=data,
             hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'tkinter'], noarchive=False)
# Qt 6.11 imports Windows' unversioned ICU API. Development PATH may also
# contain Poppler's incompatible ICU (version-suffixed symbols). Do not ship
# that DLL, or private API-set stubs collected from unrelated imaging tools.
from pathlib import PureWindowsPath
a.binaries = [entry for entry in a.binaries
              if PureWindowsPath(entry[0]).name.lower() not in ('icuuc.dll', 'icudt78.dll')
              and not PureWindowsPath(entry[0]).name.lower().startswith(('api-ms-win-', 'ext-ms-win-'))]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='EliosColorizer',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False,
          icon='assets/elios_colorizer.ico')
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='EliosColorizer')
