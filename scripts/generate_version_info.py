"""Generate a PyInstaller Windows version-info file from a git tag.

Usage: python scripts/generate_version_info.py <tag>

Converts a tag like ``v2.1.3`` into ``version_info.txt`` at the repo root,
embedding ``2.1.3`` as the exe's FileVersion/ProductVersion. Only used by
GovCrawler.spec when the file is present (see release.yaml); local dev
builds fall back to no version resource.
"""

import re
import sys

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric},
    prodvers={numeric},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'GovCrawler'),
        StringStruct(u'FileDescription', u'GovCrawler'),
        StringStruct(u'FileVersion', u'{version}'),
        StringStruct(u'InternalName', u'GovCrawler'),
        StringStruct(u'OriginalFilename', u'GovCrawler.exe'),
        StringStruct(u'ProductName', u'GovCrawler'),
        StringStruct(u'ProductVersion', u'{version}')])
      ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"""


def main() -> None:
    tag = sys.argv[1] if len(sys.argv) > 1 else "v0.0.0"
    version = tag[1:] if tag.startswith("v") else tag

    parts = [int(p) for p in re.findall(r"\d+", version)][:4]
    parts += [0] * (4 - len(parts))
    numeric = tuple(parts)

    with open("version_info.txt", "w", encoding="utf-8") as fh:
        fh.write(TEMPLATE.format(numeric=numeric, version=version))

    print(f"Generated version_info.txt: version={version!r} filevers={numeric}")


if __name__ == "__main__":
    main()
