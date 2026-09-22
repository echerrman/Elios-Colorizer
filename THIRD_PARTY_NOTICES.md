# Third-party components

Elios Colorizer uses unmodified third-party libraries. Their own licenses apply independently of this application's source. The portable distribution keeps shared libraries in `_internal` and ships package metadata/license text in that directory and `licenses/`.

| Component | Upstream | License family |
|---|---|---|
| Python | https://www.python.org/ | PSF / accompanying notices |
| NumPy | https://numpy.org/ | BSD-3-Clause / bundled component notices |
| SciPy | https://scipy.org/ | BSD-3-Clause / bundled component notices |
| OpenCV Python headless | https://github.com/opencv/opencv-python | Apache-2.0 / bundled third-party notices |
| FFmpeg runtime inside OpenCV | https://ffmpeg.org/ | See OpenCV LICENSE-3RD-PARTY.txt for the bundled build |
| PySide6, Shiboken6, Qt | https://www.qt.io/qt-for-python | LGPLv3 / GPL alternatives and component notices |
| laspy | https://github.com/laspy/laspy | BSD-2-Clause |
| lazrs | https://github.com/laz-rs/laz-rs | MIT |
| MCAP, MCAP ROS2 support | https://github.com/foxglove/mcap | MIT |
| LZ4, zstandard | https://github.com/python-lz4/python-lz4 ; https://github.com/indygreg/python-zstandard | BSD / component notices |
| PyYAML | https://pyyaml.org/ | MIT |
| PyInstaller bootloader | https://pyinstaller.org/ | GPL with bootloader exception |

Consult the bundled license texts for exact terms and copyright holders. The application does not modify Qt/PySide6; these remain dynamically loaded and replaceable. The application source is supplied in this project, and the unmodified upstream source is available from the linked projects at the versions recorded in `requirements-lock.txt`. No restriction on reverse engineering for debugging modifications to LGPL components is imposed by this project.

Elios and Inspector are Flyability product names. This project is an independent local development tool and is not a Flyability product or endorsement.

