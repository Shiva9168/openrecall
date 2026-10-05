import io
import platform
from setuptools import find_packages, setup

with io.open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

install_requires = [
    "Flask>=3.0.0",
    "numpy>=1.24.0",
    "mss>=9.0.0",
    "rapidfuzz>=3.0.0",
    "Pillow>=10.0.0",
    "pytesseract>=0.3.10",
    "rapidocr>=3.9.0",
    "onnxruntime>=1.14.0",
]


extras_require = {
    "windows": ["pywin32", "psutil"],
    "macos": ["pyobjc"],
    "linux": [],
    "dev": ["pytest", "psutil"],
}

current_os = platform.system().lower()
if current_os.startswith("win"):
    install_requires.extend(extras_require.get("windows", []))
elif current_os == "darwin":
    install_requires.extend(extras_require.get("macos", []))

setup(
    name="OpenRecall",
    version="0.9.0",
    packages=find_packages(),
    package_data={"openrecall": ["static/*", "static/**/*"]},
    include_package_data=True,
    install_requires=install_requires,
    long_description=long_description,
    long_description_content_type="text/markdown",
    extras_require=extras_require,
    entry_points={
        "console_scripts": [
            "openrecall=openrecall.app:main",
        ],
        "gui_scripts": [
            "openrecall-bg=openrecall.app:main",
        ],
    },
)
