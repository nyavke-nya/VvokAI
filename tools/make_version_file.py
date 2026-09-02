"""Write the VERSIONINFO block that goes into VvokAI.exe.

    python tools\make_version_file.py build\launcher\version_info.txt

An executable with no version resource at all - no company, no description,
no product name - is one of the things Defender's machine-learning models
weigh, and a PyInstaller onefile that downloads Python and then starts other
processes is already most of the rest of that shape. Filling this in does not
make an unsigned binary trusted, and it is not meant to: it removes one of the
free reasons to distrust it, and it is what tells the properties dialog what
the file is when somebody right-clicks it to check.

Deliberately stdlib only. build_exe.bat falls back to whatever Python is on
PATH, because launcher.py needs nothing else, and a build step that suddenly
wanted Pillow would break that.

The numbers come from PYLA_VERSION in src/utils.py, read rather than imported
- importing it would pull in requests, torch and the rest of a bot that has no
business being loaded to build a launcher.
"""

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def project_version():
    """PYLA_VERSION from src/utils.py, as a four-part tuple.

    Falls back to 0.0.0 rather than failing the build. A missing version
    string is worth a warning; it is not worth an exe that does not exist.
    """
    try:
        text = (ROOT / "src" / "utils.py").read_text(encoding="utf-8")
        found = re.search(r"^PYLA_VERSION\s*=\s*[\"']([0-9.]+)[\"']",
                          text, re.MULTILINE)
    except OSError:
        found = None
    if not found:
        print("[WARN] PYLA_VERSION not found in src/utils.py; using 0.0.0")
        return (0, 0, 0, 0)
    parts = [int(piece) for piece in found.group(1).split(".")[:4]]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers},
    prodvers={vers},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [StringStruct('CompanyName', 'nyavke'),
         StringStruct('FileDescription', 'VvokAI setup and launcher for Brawl Stars'),
         StringStruct('FileVersion', '{dotted}'),
         StringStruct('InternalName', 'VvokAI'),
         StringStruct('LegalCopyright', 'CC BY-NC 4.0. See LICENSE.'),
         StringStruct('OriginalFilename', 'VvokAI.exe'),
         StringStruct('ProductName', 'VvokAI'),
         StringStruct('ProductVersion', '{dotted}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
    target = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "build" / "launcher" / "version_info.txt")
    version = project_version()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        TEMPLATE.format(vers=version, dotted=".".join(str(n) for n in version)),
        encoding="utf-8")
    print(f"[INFO] Version resource: {target} ({'.'.join(str(n) for n in version)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
