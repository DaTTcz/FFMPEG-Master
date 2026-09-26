# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec pro Linux build (z něj se v CI dělá AppImage, .deb i .rpm).
# Spouští se z kořene repozitáře:  pyinstaller --noconfirm packaging/linux/ffmpeg-master.spec
#
# Proč spec a ne jen parametry příkazové řádky: PyInstaller neumí z příkazové řádky vyřadit
# konkrétní sdílené knihovny, a to je pro univerzální Linux build potřeba (viz EXCLUDE_LIBS).

import glob
import os
import re

from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))


def _version_key(path):
    return [int(x) for x in re.findall(r"\d+", os.path.basename(path))]


# Hlavní skript = FFMPEG_Master_v*.pyw s nejvyšší verzí (stejně jako v build.yml), ať se spec
# nemusí měnit s každou verzí.
MAIN_SCRIPT = max(glob.glob(os.path.join(ROOT, "FFMPEG_Master_v*.pyw")), key=_version_key)
print(f"[spec] Hlavní skript: {MAIN_SCRIPT}")

# Knihovny, které se NESMÍ přibalit - musí se vzít ze systému, na kterém appka běží.
#
# fontconfig čte systémové konfigurační soubory v /etc/fonts. Starší přibalená verze (z Ubuntu
# 24.04, kde se builduje) nerozumí novější syntaxi na rolling distribucích (openSUSE Slowroll/
# Tumbleweed, Fedora, Arch) -> záplava "Fontconfig warning: ... invalid attribute 'xsi:nil'"
# a písma se vykreslují jinak než ve zbytku systému. freetype a knihovny, na kterých systémový
# freetype/fontconfig závisí (libpng, brotli, expat), musí jít spolu s nimi, jinak by si systémový
# fontconfig natáhl starší přibalené verze. Odpovídá to oficiálnímu AppImage "excludelistu"
# (knihovny, které má každý desktopový Linux). Balíčky .deb/.rpm je mají v závislostech.
EXCLUDE_LIBS = (
    "libfontconfig.so",
    "libfreetype.so",
    "libexpat.so",
    "libpng16.so",
    "libbrotlicommon.so",
    "libbrotlidec.so",
)

datas, binaries, hiddenimports = [], [], ["PIL._tkinter_finder"]
for pkg in ("tkinterdnd2", "customtkinter"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
datas += [
    (os.path.join(ROOT, "favicon.ico"), "."),
    (os.path.join(ROOT, "favicon.png"), "."),
]

a = Analysis(
    [MAIN_SCRIPT],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

_before = len(a.binaries)
a.binaries = [b for b in a.binaries if not os.path.basename(b[0]).startswith(EXCLUDE_LIBS)]
_removed = _before - len(a.binaries)
print(f"[spec] Vyřazeno systémových knihoven z bundlu: {_removed}")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ffmpeg-master",
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
)
