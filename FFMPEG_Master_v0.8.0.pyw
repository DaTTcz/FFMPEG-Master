import os
import sys
import subprocess
import json
import threading
import shutil
import re
import time
import copy
import bisect
import base64
import array
import shlex
import tempfile
import webbrowser
import urllib.request
import urllib.error
import urllib.parse
import socket
import ctypes
import customtkinter as ctk
from tkinter import messagebox, Listbox, filedialog, EXTENDED, SINGLE, Menu, Canvas, PhotoImage, StringVar
from tkinterdnd2 import DND_FILES, TkinterDnD

VERSION = "v0.8.0"
GITHUB_REPO = "DaTTcz/FFMPEG-Master"


def _restore_system_library_env():
    """Vrátí proměnnou LD_LIBRARY_PATH do stavu před spuštěním PyInstaller binárky.

    PyInstaller (onefile) při startu nastaví LD_LIBRARY_PATH na svou dočasnou složku /tmp/_MEIxxxx
    se zabalenými knihovnami (buildí se na Ubuntu 24.04). Tuto proměnnou pak dědí KAŽDÝ spuštěný
    systémový program - ffprobe, ffmpeg, pkexec, xdg-open (prohlížeč), update skript... Ty pak
    místo knihoven svého systému načtou ty zabalené, starší. Na Mintu/Ubuntu 24.04 se nic nestane
    (verze sedí), ale na rolling distribucích (openSUSE Tumbleweed/Slowroll, Fedora, Arch) systémový
    ffprobe spadne dřív, než vůbec otevře soubor - např.
        ffprobe: symbol lookup error: /lib64/libldap.so.2: undefined symbol: EVP_md2, version OPENSSL_3.0.0
    (zabalený libcrypto z Ubuntu vs. systémový libldap ze Slowrollu) a appka hlásila jen
    „Nelze analyzovat soubor (žádné video?)".

    Oprava v os.environ je bezpečná i pro samotnou běžící appku: dynamický linker si LD_LIBRARY_PATH
    přečte jen jednou při startu procesu, takže změna ovlivní výhradně nově spouštěné programy.
    Původní hodnotu PyInstaller ukládá do LD_LIBRARY_PATH_ORIG (pokud žádná nebyla, chybí úplně)."""
    if sys.platform == "win32" or not getattr(sys, "frozen", False):
        return
    orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig:
        os.environ["LD_LIBRARY_PATH"] = orig
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)


_restore_system_library_env()


def _configure_ssl_certs():
    """Nastaví systémové CA certifikáty pro HTTPS (kontrola a stažení aktualizací z GitHubu).

    Zabalený Python + OpenSSL pochází z Ubuntu a certifikáty hledá tam, kde je má Debian/Ubuntu
    (/usr/lib/ssl -> /etc/ssl/certs/ca-certificates.crt). openSUSE je má v /etc/ssl/ca-bundle.pem,
    Fedora v /etc/pki/tls/certs/ca-bundle.crt - tam by ověření certifikátu GitHubu selhalo a appka
    by hlásila "chybí připojení k internetu?". Proměnnou SSL_CERT_FILE čte OpenSSL při vytváření
    každého SSL kontextu, takže stačí nastavit jednou při startu (a jen pokud ji uživatel nemá)."""
    if sys.platform == "win32" or not getattr(sys, "frozen", False) or os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import ssl
        default = ssl.get_default_verify_paths()
        if default.cafile and os.path.exists(default.cafile):
            return
    except Exception:
        pass
    for candidate in ("/etc/ssl/certs/ca-certificates.crt",               # Debian, Ubuntu, Mint, Arch
                      "/etc/pki/tls/certs/ca-bundle.crt",                 # Fedora, RHEL
                      "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
                      "/etc/ssl/ca-bundle.pem",                           # openSUSE
                      "/var/lib/ca-certificates/ca-bundle.pem",           # openSUSE
                      "/etc/ssl/cert.pem"):                               # Arch, Alpine
        if os.path.exists(candidate):
            os.environ["SSL_CERT_FILE"] = candidate
            break
    if not os.environ.get("SSL_CERT_DIR") and os.path.isdir("/etc/ssl/certs"):
        os.environ["SSL_CERT_DIR"] = "/etc/ssl/certs"


_configure_ssl_certs()

# --- OPRAVA IKONY V LIŠTĚ WINDOWS ---
try:
    myappid = f'moje.ffmpeg.master.{VERSION}'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception:
    pass


def resource_path(relative_path):
    """Cesta k souborům zabaleným uvnitř exe (ikony, obrázky) - funguje v .py i ve zmrazeném PyInstaller exe.

    Zkusí víc kandidátních umístění (PyInstaller onefile extrakční adresář _MEIPASS, adresář vedle
    samotné binárky, adresář vedle .pyw skriptu) a vrátí první, které skutečně existuje. Díky tomu
    funguje i workaround "dej favicon.ico/favicon.png ručně vedle binárky", kdyby se z nějakého
    důvodu nezabalily dovnitř (a do konzole/logu se v takovém případě napíše varování - viz volající)."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, relative_path))
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), relative_path))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path))
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    # Nic nenalezeno - vrať první kandidát (obvyklé chování dřív), volající si existenci stejně
    # ověřuje přes os.path.exists a chybějící soubor jen zaloguje/přeskočí.
    return candidates[0]


def _linux_user_config_dir():
    """Standardní XDG adresář pro config.json u .deb instalace (~/.config/ffmpeg-master, nebo
    $XDG_CONFIG_HOME). Binárka u .deb instalace leží v /usr/bin - sdílená mezi všemi uživateli
    a bez rootu nezapisovatelná - takže na rozdíl od AppImage/přenosného buildu nejde config
    ukládat vedle samotné binárky."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "ffmpeg-master")


def base_dir():
    """Adresář, kam patří config.json (a dočasná složka pro auto-update) - obvykle vedle .exe
    (frozen) nebo vedle .pyw (dev). Výjimky jsou Linux AppImage a .deb instalace (viz
    linux_run_mode() níže): u AppImage by sys.executable ukazoval do dočasného, read-only mount
    pointu (mění se při každém spuštění), takže se použije adresář vedle samotného .AppImage
    souboru - stejná konvence jako u přenosné .exe/.pyw (config jde vidět a upravovat vedle
    binárky, jde i přenést spolu s ní). U .deb instalace je binárka v /usr/bin (sdílená, bez
    rootu nezapisovatelná), takže se použije standardní XDG adresář v domovské složce uživatele."""
    if getattr(sys, 'frozen', False):
        if sys.platform != "win32":
            mode = linux_run_mode()
            if mode == "appimage":
                return os.path.dirname(os.path.abspath(os.environ["APPIMAGE"]))
            if mode in ("deb", "rpm"):
                return _linux_user_config_dir()
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def linux_run_mode():
    """Rozpozná, jak byla aplikace na Linuxu spuštěna - ovlivňuje jak self-registraci .desktop
    záznamu níže, tak logiku auto-update (viz start_update_download/_download_and_install):

    - "appimage" - běží zabalená jako AppImage (pozná se podle proměnné prostředí APPIMAGE, kterou
                   nastavuje samotný AppImage runtime při spuštění). sys.executable tu ukazuje do
                   dočasného mount pointu (mění se při každém spuštění) - pro .desktop Exec= i pro
                   auto-update je potřeba stabilní cesta k .AppImage souboru z proměnné APPIMAGE.
    - "deb"      - nainstalováno přes .deb balíček (binárka leží v /usr/bin, kam ji dá dpkg).
                   .desktop záznam a ikonu si přinese sám balíček (viz DEBIAN/postinst v CI) -
                   sebe-registraci níže tedy netřeba spouštět. Auto-update se navíc chová jinak:
                   binárka je v /usr/bin (root-owned), takže appka ji sama nepřepíše - jen upozorní
                   a odkáže na stažení nového .deb (viz start_update_download).
    - "rpm"      - totéž jako "deb", jen nainstalováno přes .rpm balíček (Fedora, openSUSE). Liší se
                   jen tím, jaký soubor se stáhne a čím se nainstaluje při auto-update (rpm -U).
    - "portable" - cokoliv jiného zmrazeného (ruční/lokální PyInstaller build spuštěný odkudkoliv) -
                   původní chování, cesta ze sys.executable.
    - "script"   - spuštěno jako .pyw/.py skript (vývoj), ne zmrazený build.
    """
    if not getattr(sys, "frozen", False):
        return "script"
    if os.environ.get("APPIMAGE"):
        return "appimage"
    exe = os.path.realpath(sys.executable)
    if exe.startswith("/usr/") or exe.startswith("/opt/"):
        return _linux_package_kind()
    return "portable"


_PACKAGE_KIND_CACHE = []


def _linux_package_kind():
    """Rozliší, jestli binárku v /usr nainstaloval dpkg (.deb) nebo rpm (.rpm). Podle databáze
    balíčků, ne podle distribuce - na Debianu jde doinstalovat rpm a naopak. Výsledek se cachuje
    (volá se opakovaně a rpm -q by jinak běžel pokaždé)."""
    if not _PACKAGE_KIND_CACHE:
        _PACKAGE_KIND_CACHE.append(_detect_linux_package_kind())
    return _PACKAGE_KIND_CACHE[0]


def _detect_linux_package_kind():
    if os.path.exists("/var/lib/dpkg/info/ffmpeg-master.list"):
        return "deb"
    if shutil.which("rpm"):
        try:
            res = subprocess.run(["rpm", "-q", "ffmpeg-master"], capture_output=True, timeout=5)
            if res.returncode == 0:
                return "rpm"
        except Exception:
            pass
    # Nerozpoznáno (ruční kopie do /usr/local/bin apod.) - chová se jako balíček: config v ~/.config,
    # bez sebe-registrace do menu. Auto-update pak podle toho, jaký správce balíčků systém má.
    return "rpm" if shutil.which("rpm") and not shutil.which("dpkg") else "deb"


def ensure_linux_desktop_entry():
    """Na Linuxu (jen ve zabalené binárce) zaregistruje aplikaci do standardní XDG nabídky/launcheru.

    Samotné spuštění binárky o tohle nijak nepečuje - ikona nastavená přes iconphoto() v kódu výše
    platí jen pro běžící okno (titulkový pruh, přepínač oken), ale start menu/launcher/dock potřebuje
    samostatný .desktop soubor + ikonu na standardním místě (~/.local/share/applications,
    ~/.local/share/icons/...). Bez rootu, jen do domovského adresáře uživatele; bezpečně se přeskočí
    (žádná chyba), pokud cokoliv selže - jde čistě o kosmetické vylepšení, ne o nutnou podmínku běhu."""
    mode = linux_run_mode()
    if mode in ("deb", "rpm"):
        # .desktop záznam a ikonu si appka přinese už v .deb balíčku (system-wide v /usr/share/...
        # - viz DEBIAN/postinst v CI), sebe-registrace do ~/.local/share by jen vytvořila druhou,
        # zbytečnou položku v menu. Navíc podle XDG specifikace má uživatelský záznam přednost před
        # systémovým - pokud appka byla dřív vyzkoušená jako AppImage/přenosná verze (ta se sama
        # zaregistrovala právě do ~/.local/share), ten starý záznam by teď kliknutí v menu potichu
        # posílal na neexistující/starou binárku místo na nově nainstalovaný .deb. Proto se tu
        # případný starý self-registrovaný záznam rovnou uklidí.
        try:
            data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
            stale_desktop = os.path.join(data_home, "applications", "ffmpeg-master.desktop")
            stale_icon = os.path.join(data_home, "icons", "hicolor", "256x256", "apps", "ffmpeg-master.png")
            cleaned = False
            for stale_path in (stale_desktop, stale_icon):
                if os.path.exists(stale_path):
                    os.remove(stale_path)
                    cleaned = True
            if cleaned:
                subprocess.run(["update-desktop-database", os.path.join(data_home, "applications")],
                                capture_output=True, timeout=5)
        except Exception as e:
            print(f"[FFMPEG Master] Úklid starého .desktop záznamu selhal (nekritické): {e}", file=sys.stderr)
        return
    try:
        data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        icons_dir = os.path.join(data_home, "icons", "hicolor", "256x256", "apps")
        apps_dir = os.path.join(data_home, "applications")
        os.makedirs(icons_dir, exist_ok=True)
        os.makedirs(apps_dir, exist_ok=True)

        icon_dest = os.path.join(icons_dir, "ffmpeg-master.png")
        src_icon = resource_path("favicon.png")
        icon_value = "ffmpeg-master"
        if os.path.exists(src_icon):
            try:
                if not os.path.exists(icon_dest) or os.path.getmtime(src_icon) > os.path.getmtime(icon_dest):
                    shutil.copyfile(src_icon, icon_dest)
            except Exception:
                pass
        else:
            # Ikona se nikam nezkopírovala (nenašla se) - použij rovnou absolutní cestu k PNG vedle
            # binárky/skriptu jako Icon=, ať launcher aspoň má šanci ji najít.
            icon_value = src_icon

        desktop_path = os.path.join(apps_dir, "ffmpeg-master.desktop")
        if mode == "appimage":
            # Stabilní cesta k .AppImage souboru - sys.executable by tu ukazoval do dočasného
            # mount pointu, který se mezi spuštěními mění (viz linux_run_mode()).
            exe_path = os.environ["APPIMAGE"]
        elif getattr(sys, "frozen", False):
            exe_path = sys.executable
        else:
            exe_path = os.path.abspath(__file__)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=FFMPEG Master\n"
            "Comment=Davkovy konvertor videa pres FFmpeg\n"
            f"Exec=\"{exe_path}\"\n"
            f"Icon={icon_value}\n"
            "Terminal=false\n"
            "Categories=AudioVideo;Video;\n"
        )
        needs_write = True
        if os.path.exists(desktop_path):
            try:
                with open(desktop_path, "r", encoding="utf-8") as f:
                    needs_write = f.read() != content
            except Exception:
                needs_write = True
        if needs_write:
            with open(desktop_path, "w", encoding="utf-8") as f:
                f.write(content)
            try:
                os.chmod(desktop_path, 0o755)
            except Exception:
                pass
            # Nepovinné - jen pokud je nástroj po ruce; některá desktopová prostředí si soubor
            # všimnou i bez toho, jiná potřebují databázi obnovit ručně.
            try:
                subprocess.run(["update-desktop-database", apps_dir], capture_output=True, timeout=5)
            except Exception:
                pass
    except Exception as e:
        print(f"[FFMPEG Master] Registrace .desktop záznamu selhala (nekritické): {e}", file=sys.stderr)


# --- CROSS-PLATFORM PŘÍPRAVA ---
if sys.platform == "win32":
    SUBPROCESS_FLAGS = 0x08000000  # CREATE_NO_WINDOW
    DETACHED_FLAGS = 0x00000008    # DETACHED_PROCESS (spuštění updater .bat mimo běžící proces)
else:
    SUBPROCESS_FLAGS = 0
    DETACHED_FLAGS = 0

try:
    from PIL import Image
    HAS_PILLOW = True
except Exception:
    HAS_PILLOW = False


# ==========================================================================
#  KONFIGURACE (config.json)
# ==========================================================================

CONFIG_FILENAME = "config.json"
CONFIG_PATH = os.path.join(base_dir(), CONFIG_FILENAME)

# Výchozí cesty se liší podle platformy - na Windows natvrdo (jak bylo zvykem), na Linuxu se
# spoléhá na ffmpeg/ffprobe v PATH (běžné z balíčku distribuce) a výstupní složky pod domovským adresářem.
if sys.platform == "win32":
    _DEFAULT_FFMPEG_PATH = r"C:\FFMPEG\bin\ffmpeg.exe"
    _DEFAULT_FFPROBE_PATH = r"C:\FFMPEG\bin\ffprobe.exe"
    _DEFAULT_OUTPUT_DIR = r"C:\!VIDEO\VIDEO_OUT\OUTPUT"
    _DEFAULT_DONE_DIR = r"C:\!VIDEO\VIDEO_OUT\HOTOVO"
else:
    _DEFAULT_FFMPEG_PATH = "ffmpeg"
    _DEFAULT_FFPROBE_PATH = "ffprobe"
    _DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Videos", "FFMPEG_Master", "OUTPUT")
    _DEFAULT_DONE_DIR = os.path.join(os.path.expanduser("~"), "Videos", "FFMPEG_Master", "HOTOVO")

DEFAULT_CONFIG = {
    "ffmpeg_path": _DEFAULT_FFMPEG_PATH,
    "ffprobe_path": _DEFAULT_FFPROBE_PATH,
    "output_dir": _DEFAULT_OUTPUT_DIR,
    "done_dir": _DEFAULT_DONE_DIR,
    "video": {
        "codec": "hevc_nvenc",
        "pix_fmt": "p010le",
        "profile": "main10",
        "rc": "vbr",
        "cq": 28.5,
        "preset": "p7",
        "max_width": 1280,
        "max_height": 720
    },
    "audio": {
        "codec": "libopus",
        "bitrate": "96k"
    },
    "loudnorm": {
        "I": -18,
        "TP": -1.5,
        "LRA": 11
    },
    "generate_nfo": True,
    "source_action_local": "move",    # "move" | "rename" | "none" - zdroj na lokálním disku
    "source_action_network": "rename",  # "move" | "rename" | "none" - zdroj na síťovém disku (UNC / mapovaná síťová jednotka)
    "use_custom_encode_args": False,
    "custom_encode_args": "",  # pokud use_custom_encode_args=True a toto je prázdné, použije se výchozí dopočítané z video/audio
    "nfo_genres": [
        "Animovaný", "Akční", "Komedie", "Drama", "Sci-Fi", "Fantasy",
        "Horor", "Dokument", "Thriller", "Rodinný", "Neurčeno"
    ],
    "default_genre": "Animovaný",
    "theme": "dark",
    "window_geometry": "750x740",
    "auto_select_languages": ["CZE", "CES"],
    "github_repo": GITHUB_REPO,
    "check_updates_on_startup": True
}


def _deep_merge_defaults(cfg, defaults):
    """Doplní do existujícího configu chybějící klíče z DEFAULT_CONFIG (nedestruktivně), rekurzivně."""
    for key, val in defaults.items():
        if key not in cfg:
            cfg[key] = copy.deepcopy(val)
        elif isinstance(val, dict) and isinstance(cfg.get(key), dict):
            _deep_merge_defaults(cfg[key], val)
    return cfg


def load_config():
    if not os.path.exists(CONFIG_PATH):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        save_config(cfg)
        return cfg
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # Migrace ze staršího jednotného "source_action" (v0.5.0) na oddělené local/network (v0.5.1)
        if "source_action" in cfg:
            old = cfg.pop("source_action")
            cfg.setdefault("source_action_local", old)
            cfg.setdefault("source_action_network", old)
            save_config(cfg)
        cfg = _deep_merge_defaults(cfg, DEFAULT_CONFIG)
        return cfg
    except Exception:
        try:
            shutil.copy(CONFIG_PATH, CONFIG_PATH + ".bak")
        except Exception:
            pass
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        save_config(cfg)
        return cfg


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


# ==========================================================================
#  PROBE (ffprobe) - detailní info o stopách pro dialog výběru
# ==========================================================================

PGS_CODECS = ("hdmv_pgs_subtitle", "pgssub")


def _short_stderr(text, max_lines=4):
    """Posledních pár neprázdných řádků chybového výstupu - do logu stačí to podstatné."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(lines[-max_lines:])


def probe_file(file_path, ffprobe_path):
    """Vrátí (info, None) s duration, video info a detailním seznamem audio/titulkových stop,
    nebo (None, "důvod") pokud soubor nejde analyzovat - důvod se vypíše do logu, ať je hned vidět,
    jestli chybí/padá samotný ffprobe, nebo je problém v souboru."""
    cmd = [ffprobe_path, "-v", "error",
           "-analyzeduration", "20000000", "-probesize", "20000000",
           "-show_entries",
           "format=duration:stream=index,codec_type,codec_name,width,height,coded_width,coded_height,channels,channel_layout,bit_rate:stream_tags=language,title",
           "-of", "json", file_path]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             creationflags=SUBPROCESS_FLAGS)
    except FileNotFoundError:
        return None, f"ffprobe nenalezen ({ffprobe_path}) - zkontroluj cestu v Nastavení"
    except Exception as e:
        return None, f"ffprobe nešel spustit ({ffprobe_path}): {e}"
    if res.returncode != 0:
        detail = _short_stderr(res.stderr) or "bez chybového výstupu"
        return None, f"ffprobe skončil s kódem {res.returncode}: {detail}"
    try:
        data = json.loads(res.stdout or "{}")
    except Exception as e:
        return None, f"nečitelný výstup z ffprobe ({e}): {_short_stderr(res.stderr) or (res.stdout or '')[:200]}"

    duration = float(data.get("format", {}).get("duration", 0) or 0)
    video = None
    audio_streams, sub_streams = [], []

    for s in data.get("streams", []):
        idx = str(s.get("index"))
        stype = s.get("codec_type")
        tags = s.get("tags", {}) or {}
        lang = str(tags.get("language", "und")).upper()
        title = tags.get("title", "")
        codec_name = s.get("codec_name", "?")
        if stype == "video" and video is None:
            video = {"index": idx, "codec_name": codec_name,
                      "res": f"{s.get('width')}x{s.get('height')}",
                      "width": s.get("width"), "height": s.get("height"),
                      "coded_width": s.get("coded_width"), "coded_height": s.get("coded_height")}
        elif stype == "audio":
            audio_streams.append({
                "index": idx, "codec_name": codec_name, "lang": lang, "title": title,
                "channels": s.get("channels"), "channel_layout": s.get("channel_layout") or "?",
                "bitrate": s.get("bit_rate")
            })
        elif stype == "subtitle":
            sub_streams.append({
                "index": idx, "codec_name": codec_name, "lang": lang, "title": title,
                "is_pgs": codec_name in PGS_CODECS
            })

    if video is None:
        return None, "soubor neobsahuje žádnou video stopu"
    return {"duration": duration, "video": video, "audio_streams": audio_streams, "sub_streams": sub_streams}, None


# ==========================================================================
#  PŘETAŽENÉ SOUBORY - převod URL (file://, smb://) na lokální cestu
# ==========================================================================

VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov', '.ts', '.m2ts', '.wmv', '.flv', '.webm', '.m4v')


# ==========================================================================
#  OŘEZ ČERNÝCH OKRAJŮ
# ==========================================================================
#
# Ořez se ukládá jako počet pixelů ubraných z každé strany zdroje + rozměr zdroje, pro který platí:
# {"top", "bottom", "left", "right", "src_w", "src_h"} (sudé hodnoty - kvůli 4:2:0 chromě).
# Detekce: cropdetect na několika krátkých úsecích rozložených po filmu (ne celý film - trvalo by
# to dlouho) a z nich NEJVĚTŠÍ obdélník. Tmavé scény dávají menší obdélník, takže by nejčastější
# hodnota mohla uříznout obraz; sjednocení je bezpečné (nanejvýš zůstane kousek černé).
# Při překódování se ořízne filtrem crop; při kopírování videa jen u H.264/HEVC - zápisem ořezu
# do SPS přes bitstream filtr (bez dekódování, přehrávač ořez provede sám).

CROP_SAMPLES = 8
CROP_SAMPLE_SECONDS = 2
CROP_SAMPLE_FRAMES = 50
CROP_COPY_CODECS = {"h264": "h264_metadata", "hevc": "hevc_metadata"}


def crop_sample_times(duration, n=CROP_SAMPLES):
    """Časy vzorků pro detekci/náhled - 10-90 % délky (bez úvodu a závěrečných titulků)."""
    if not duration or duration <= 0:
        return [0.0]
    if duration < 30:
        return [duration * (i + 1) / (n + 1) for i in range(n)]
    start, end = duration * 0.10, duration * 0.90 - CROP_SAMPLE_SECONDS
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def detect_crop_at(ffmpeg_path, path, t, env=None, frames=CROP_SAMPLE_FRAMES):
    """cropdetect na krátkém úseku od času t. Vrací (x0, y0, x1, y1) nebo None (černý/nečitelný úsek).
    Úsek je daný počtem snímků, ne časem: u .ts s dlouhým GOP dekodér po skoku zahazuje snímky až
    do dalšího klíčového - časově omezené okno by pak nemuselo obsahovat žádný snímek."""
    cmd = [ffmpeg_path, "-hide_banner", "-nostats", "-ss", f"{t:.3f}", "-i", path, "-frames:v", str(frames),
           "-map", "0:v:0", "-vf", "cropdetect=limit=24:round=2:reset=0", "-an", "-sn", "-dn", "-f", "null", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=180, creationflags=SUBPROCESS_FLAGS, env=env)
    except Exception:
        return None
    found = re.findall(r"crop=(-?\d+):(-?\d+):(-?\d+):(-?\d+)", res.stderr or "")
    if not found:
        return None
    w, h, x, y = map(int, found[-1])
    if w <= 0 or h <= 0:
        return None
    return (x, y, x + w, y + h)


def normalize_crop(crop):
    """Sudé hodnoty, v mezích zdroje, výsledek aspoň 64x64. Vrátí None, když je ořez nulový/neplatný."""
    if not crop:
        return None
    try:
        src_w, src_h = int(crop["src_w"]), int(crop["src_h"])
        vals = {k: max(0, int(crop.get(k, 0))) // 2 * 2 for k in ("top", "bottom", "left", "right")}
    except (KeyError, TypeError, ValueError):
        return None
    if src_w - vals["left"] - vals["right"] < 64 or src_h - vals["top"] - vals["bottom"] < 64:
        return None
    if not any(vals.values()):
        return None
    return dict(vals, src_w=src_w, src_h=src_h)


def merge_crop_boxes(boxes, src_w, src_h):
    """Sjednocení obdélníků ze vzorků -> ořez. Vrací (crop nebo None, počet použitelných vzorků)."""
    boxes = [b for b in boxes if b]
    if not boxes:
        return None, 0
    x0 = max(0, min(b[0] for b in boxes)); y0 = max(0, min(b[1] for b in boxes))
    x1 = min(src_w, max(b[2] for b in boxes)); y1 = min(src_h, max(b[3] for b in boxes))
    crop = normalize_crop({"left": x0, "top": y0, "right": src_w - x1, "bottom": src_h - y1,
                           "src_w": src_w, "src_h": src_h})
    return crop, len(boxes)


def crop_size(crop):
    return crop["src_w"] - crop["left"] - crop["right"], crop["src_h"] - crop["top"] - crop["bottom"]


def crop_describe(crop):
    w, h = crop_size(crop)
    sides = [f"{lbl} {crop[k]}" for k, lbl in (("top", "nahoře"), ("bottom", "dole"), ("left", "vlevo"), ("right", "vpravo")) if crop[k]]
    return f"{w}×{h} ({', '.join(sides)})"


def crop_filter(crop):
    w, h = crop_size(crop)
    return f"crop={w}:{h}:{crop['left']}:{crop['top']}"


def crop_bitstream_filter(crop, video):
    """Bitstream filtr, který zapíše ořez do SPS (H.264/HEVC) - bez překódování. None = kodek to neumí.
    Hodnoty se počítají od KÓDOVANÉHO rozměru (1080p H.264 je kódované jako 1088 a ořez 8 px dole už
    ve streamu je) - filtr stávající ořez nahrazuje, takže se k němu musí přičíst."""
    codec = (video.get("codec_name") or "").lower()
    name = CROP_COPY_CODECS.get(codec)
    if not name:
        return None
    top, bottom, left, right = crop["top"], crop["bottom"], crop["left"], crop["right"]
    if codec == "h264":
        # U prokládaného H.264 (TV vysílání) je svislá jednotka ořezu 4 px - zaokrouhli dolů
        # (radši 2 px černé navíc než chyba filtru).
        top -= top % 4
        bottom -= bottom % 4
    w, h = int(video.get("width") or crop["src_w"]), int(video.get("height") or crop["src_h"])
    cw, ch = int(video.get("coded_width") or 0), int(video.get("coded_height") or 0)
    # ffprobe kódovaný rozměr u H.264 často hlásí už oříznutý (1080 místo 1088) - H.264 se ale vždy
    # kóduje po makroblocích 16x16 (u prokládaného 1080 také vychází 1088), HEVC po blocích aspoň 8.
    unit = 16 if codec == "h264" else 8
    cw = max(cw, -(-w // unit) * unit) if codec == "h264" else (cw if cw >= w else -(-w // unit) * unit)
    ch = max(ch, -(-h // unit) * unit) if codec == "h264" else (ch if ch >= h else -(-h // unit) * unit)
    pad_r, pad_b = cw - w, ch - h
    return f"{name}=crop_left={left}:crop_right={right + pad_r}:crop_top={top}:crop_bottom={bottom + pad_b}"


def extract_frame_png(ffmpeg_path, path, t, out_png, width=640, env=None):
    """Jeden snímek jako PNG pro náhled - zmenšený a srovnaný na zobrazovací poměr stran (SAR)."""
    cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", path,
           "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale=iw*sar:ih,scale={width}:-2", "-y", out_png]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=120, creationflags=SUBPROCESS_FLAGS, env=env)
        return res.returncode == 0 and os.path.exists(out_png)
    except Exception:
        return False


# ==========================================================================
#  STŘIH - index snímků a smart cut
# ==========================================================================
#
# Úsek = {"start": s, "end": e} v sekundách od začátku souboru (stejně jako ffmpeg -ss), start je
# čas PRVNÍHO ponechaného snímku, end čas prvního snímku, který už ve výsledku NENÍ (nebo konec).
#
# Smart cut jednoho úseku [A, B):
#   hlava  [A, K1)  - od řezu k prvnímu klíčovému snímku: překódovat (stejný kodek/parametry)
#   střed  [K1, X]  - kopírovat beze ztráty (-c copy), přesně daný počet paketů v pořadí dekódování
#   ocas   (X, B)   - zbytek do konce úseku: překódovat
# Střed se kopíruje po klíčový snímek K2 (poslední v úseku) v pořadí DEKÓDOVÁNÍ. U "open GOP"
# (TV vysílání) se B-snímky zobrazené těsně před K2 dekódují až po něm - ty do kopie nepatří a
# překóduje je ocas (X = poslední zkopírovaný snímek, spočítá se z indexu). Kopie se po vytvoření
# ověří počtem paketů; když nesedí, úsek se od K1 celý překóduje (vždy správně, jen pomaleji).
# Video kousky jsou MPEG-TS (SPS/PPS v proudu - jiné parametry kódování mezi kousky nevadí) a
# spojí se concat demuxerem. Zvuk a titulky se NEřežou po kouscích videa: berou se rovnou z
# originálu concat demuxerem s inpoint/outpoint/duration, takže se časy nesčítají s chybou a
# synchronizace se nerozjede.

SMARTCUT_ENCODERS = {"h264": ("libx264", "h264_nvenc"), "hevc": ("libx265", "hevc_nvenc"),
                     "mpeg2video": ("mpeg2video",)}
# Titulkové kodeky, které daný kontejner neumí - při střihu do originálního formátu se vynechají.
_CONTAINER_BAD_SUBS = {".mkv": {"dvb_teletext", "mov_text"}, ".mp4": {"dvb_teletext", "dvb_subtitle", "hdmv_pgs_subtitle",
                       "subrip", "ass", "ssa", "webvtt"}, ".m4v": {"dvb_teletext", "dvb_subtitle", "hdmv_pgs_subtitle",
                       "subrip", "ass", "ssa", "webvtt"}, ".ts": {"mov_text", "subrip", "ass", "ssa", "webvtt"}}
_ENCODER_CACHE = {}


def available_encoders(ffmpeg_path):
    if ffmpeg_path not in _ENCODER_CACHE:
        try:
            res = subprocess.run([ffmpeg_path, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=30, creationflags=SUBPROCESS_FLAGS)
            _ENCODER_CACHE[ffmpeg_path] = set(re.findall(r"^\s*V\S*\s+(\S+)", res.stdout or "", re.M))
        except Exception:
            _ENCODER_CACHE[ffmpeg_path] = set()
    return _ENCODER_CACHE[ffmpeg_path]


def fmt_tc(sec):
    """Čas s milisekundami: 1:02:03.040"""
    sec = max(float(sec or 0), 0.0)
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s_, ms = divmod(ms, 1000)
    return f"{h}:{m:02d}:{s_:02d}.{ms:03d}"


def parse_tc(text):
    """'1:02:03.5', '62:03', '3723.5' -> sekundy (float), nebo None."""
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(p < 0 for p in parts):
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def probe_cut_info(ffprobe_path, path):
    """Údaje o souboru potřebné pro střih: první video stopa (kodek, profil, level, pix_fmt,
    prokládání, fps), začátek a délka souboru a seznam všech stop. Vrací (info, None) / (None, důvod)."""
    cmd = [ffprobe_path, "-v", "error", "-show_entries",
           "format=start_time,duration:stream=index,codec_type,codec_name,profile,level,pix_fmt,field_order,has_b_frames,"
           "width,height,sample_aspect_ratio,r_frame_rate,avg_frame_rate:stream_tags=language,title",
           "-of", "json", path]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=120, creationflags=SUBPROCESS_FLAGS)
        data = json.loads(res.stdout or "{}")
    except Exception as e:
        return None, f"ffprobe selhal: {e}"
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((st for st in streams if st.get("codec_type") == "video"), None)
    if video is None:
        return None, "soubor neobsahuje video"
    return {"start_time": float(fmt.get("start_time") or 0), "duration": float(fmt.get("duration") or 0),
            "video": video, "streams": streams}, None


class FrameIndex:
    """Časy všech snímků první video stopy (z paketů, bez dekódování) - pro krokování po snímku,
    přichytávání řezů na snímky a plán smart cutu. packets = [(pts, je_klíčový)] v pořadí dekódování,
    časy relativně k začátku souboru."""

    def __init__(self, packets):
        self.packets = packets
        self.pts = sorted({p for p, _ in packets})
        self.keys = sorted({p for p, k in packets if k})
        diffs = sorted(b - a for a, b in zip(self.pts[:2000], self.pts[1:2001]) if b > a)
        self.frame_dur = diffs[len(diffs) // 2] if diffs else 0.04
        self.end = (self.pts[-1] + self.frame_dur) if self.pts else 0.0
        self._key_pos = {}
        for pos, (p, k) in enumerate(packets):
            if k and p not in self._key_pos:
                self._key_pos[p] = pos

    def __len__(self):
        return len(self.pts)

    def frame_no(self, t):
        """Číslo snímku zobrazeného v čase t (poslední snímek s pts <= t)."""
        i = bisect.bisect_right(self.pts, t + 1e-6) - 1
        return min(max(i, 0), len(self.pts) - 1)

    def snap(self, t):
        return self.pts[self.frame_no(t)] if self.pts else 0.0

    def step(self, t, n):
        i = min(max(self.frame_no(t) + n, 0), len(self.pts) - 1)
        return self.pts[i]

    def next_key(self, t):
        i = bisect.bisect_right(self.keys, t + 1e-6)
        return self.keys[i] if i < len(self.keys) else None

    def prev_key(self, t):
        i = bisect.bisect_left(self.keys, t - 1e-6) - 1
        return self.keys[i] if i >= 0 else None

    def after(self, t):
        """Čas snímku následujícího po snímku v čase t (= exkluzivní konec úseku končícího tímto snímkem)."""
        i = self.frame_no(t) + 1
        return self.pts[i] if i < len(self.pts) else self.end

    def count(self, a, b):
        return bisect.bisect_left(self.pts, b - 1e-6) - bisect.bisect_left(self.pts, a - 1e-6)


def build_frame_index(ffprobe_path, path, start_time=0.0, duration=0.0, progress=None, stop=None, env=None):
    """Přečte časy paketů první video stopy (rychlé, bez dekódování - jen čtení souboru).
    progress(podíl 0-1) se volá průběžně, stop() -> True přeruší. Vrací (FrameIndex, None) / (None, důvod)."""
    cmd = [ffprobe_path, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", path]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS, env=env)
    except Exception as e:
        return None, f"ffprobe nešel spustit: {e}"
    packets = []
    try:
        for n, line in enumerate(proc.stdout):
            parts = line.strip().split(",")
            if len(parts) < 2 or parts[0] in ("", "N/A"):
                continue
            try:
                t = float(parts[0]) - start_time
            except ValueError:
                continue
            packets.append((round(t, 6), "K" in parts[1]))
            if n % 5000 == 0:
                if stop and stop():
                    proc.kill()
                    return None, "přerušeno"
                if progress and duration > 0:
                    progress(min(max(t / duration, 0.0), 1.0))
    finally:
        proc.wait()
    if not packets:
        return None, "ve videu se nenašly žádné snímky (ffprobe nevrátil časy paketů)"
    return FrameIndex(packets), None


def normalize_segments(segments, end=None):
    """Seřadí, ořízne do [0, end] a spojí překrývající se úseky; zahodí prázdné."""
    segs = []
    for sg in segments or []:
        try:
            a, b = float(sg["start"]), float(sg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        a = max(a, 0.0)
        if end:
            b = min(b, end)
        if b - a > 1e-6:
            segs.append([a, b])
    segs.sort()
    merged = []
    for a, b in segs:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [{"start": round(a, 6), "end": round(b, 6)} for a, b in merged]


def invert_segments(segments, end):
    """Z úseků 'vystřihnout' udělá úseky 'ponechat' (doplněk v rámci [0, end])."""
    keep, t = [], 0.0
    for sg in normalize_segments(segments, end):
        if sg["start"] > t + 1e-6:
            keep.append({"start": t, "end": sg["start"]})
        t = sg["end"]
    if end - t > 1e-6:
        keep.append({"start": t, "end": end})
    return keep


def segments_duration(segments):
    return sum(sg["end"] - sg["start"] for sg in segments or [])


def plan_segment(index, a, b, k1=None):
    """Kousky videa jednoho úseku [a, b): [{"kind": "enc"|"copy", "start", "end", "frames"}].
    k1 = klíčový snímek, od kterého se kopíruje (None = první klíčový v úseku, False = vše překódovat).
    Hranice jsou časy snímků z indexu, takže součet délek kousků = délka úseku přesně."""
    pieces = []
    a = index.snap(a)
    if b < index.end - 1e-6:
        b = index.snap(b)
    if b - a < 1e-6:
        return pieces

    def enc(x, y):
        n = index.count(x, y)
        if n > 0:
            pieces.append({"kind": "enc", "start": x, "end": y, "frames": n})

    if k1 is None:
        k1 = index.next_key(a - 1e-6)
    if k1 is None or k1 is False or k1 >= b - 1e-6 or k1 < a - 1e-6:
        enc(a, b)
        return pieces
    enc(a, k1)
    k2 = index.prev_key(b + 1e-6)
    p1, p2 = index._key_pos.get(k1), index._key_pos.get(k2) if k2 is not None else None
    if k2 is None or k2 <= k1 + 1e-6 or p1 is None or p2 is None or p2 <= p1:
        enc(k1, b)
        return pieces
    copied = sorted(p for p, _ in index.packets[p1:p2] if p >= k1 - 1e-6)
    i1 = index.frame_no(k1)
    expected = index.pts[i1:i1 + len(copied)]
    if not copied or len(set(copied)) != len(copied) or any(abs(x - y) > 1e-4 for x, y in zip(copied, expected)):
        enc(k1, b)            # neobvyklé pořadí snímků - radši překódovat
        return pieces
    tail_start = index.after(copied[-1])
    if tail_start > b + 1e-6:
        enc(k1, b)
        return pieces
    pieces.append({"kind": "copy", "start": k1, "end": tail_start, "frames": len(copied)})
    enc(tail_start, b)
    return pieces


def copy_candidates(index, a, b, n=3):
    """Prvních n klíčových snímků v úseku - kandidáti na začátek kopie (další pokus, když spoj nesedí)."""
    a = index.snap(a)
    i = bisect.bisect_left(index.keys, a - 1e-6)
    return [k for k in index.keys[i:i + n] if k < b - 1e-6]


TS_EXTENSIONS = (".ts", ".m2ts", ".mts", ".trp")


def extract_frame_data(ffmpeg_path, path, t, start_time=0.0, width=768, env=None):
    """PNG data jednoho snímku v čase t (od začátku souboru) pro náhled. MKV/MP4 skáčou přesně (-ss),
    u MPEG-TS umí demuxer skočit i ZA cíl - tam se skočí s rezervou a snímek vybere podle pts."""
    vf_scale = f"scale=iw*sar:ih,scale={width}:-2"
    if os.path.splitext(path)[1].lower() in TS_EXTENSIONS:
        ss = max(t - 2.0, 0.0)
        cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-copyts", "-ss", f"{ss:.6f}", "-i", path,
               "-map", "0:v:0", "-vf", f"trim=start={t + start_time - 0.005:.6f},{vf_scale}"]
    else:
        cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-ss", f"{max(t - 0.005, 0):.6f}", "-i", path,
               "-map", "0:v:0", "-vf", vf_scale]
    cmd += ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=SUBPROCESS_FLAGS, env=env)
        return res.stdout if res.returncode == 0 and res.stdout else None
    except Exception:
        return None


WAVE_RATE = 4000      # vzorkovací frekvence pro průběh zvuku v okně Střih (stačí na obálku)


def extract_audio_chunk(ffmpeg_path, path, t0, t1, start_time=0.0, stream_index=None, env=None):
    """Mono PCM (array 'h', WAVE_RATE) úseku zvuku [t0, t1) od začátku souboru, nebo None.
    Výběr podle absolutních pts (-copyts + atrim) - MPEG-TS neumí skočit přesně."""
    t0 = max(t0, 0.0)
    cmd = [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-copyts", "-ss", f"{max(t0 - 2.0, 0):.6f}", "-i", path,
           "-map", f"0:{stream_index}" if stream_index is not None else "0:a:0",
           "-af", f"atrim=start={t0 + start_time:.6f}:end={t1 + start_time:.6f},aresample={WAVE_RATE}",
           "-ac", "1", "-f", "s16le", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=SUBPROCESS_FLAGS, env=env)
    except Exception:
        return None
    raw = res.stdout or b""
    if not raw:
        return None
    pcm = array.array("h")
    pcm.frombytes(raw[:len(raw) // 2 * 2])
    if sys.byteorder == "big":
        pcm.byteswap()
    return pcm


def find_ffplay(ffmpeg_path):
    """ffplay vedle ffmpeg (stejná složka), jinak z PATH. None = není."""
    d = os.path.dirname(ffmpeg_path or "")
    if d:
        cand = os.path.join(d, "ffplay.exe" if sys.platform == "win32" else "ffplay")
        if os.path.isfile(cand):
            return cand
    return shutil.which("ffplay")


def _concat_quote(path):
    return "'" + path.replace("'", "'\\''") + "'"


class SmartCutter:
    """Provede smart cut podle plánu. run(cmd, délka_v_s, popisek) -> návratový kód (None = přerušeno/
    nespuštěno), log(text, error=False). Do výstupu jde první video stopa + zadané audio/titulkové
    stopy (indexy z originálu, None = všechny, které kontejner výstupu unese)."""

    def __init__(self, ffmpeg_path, ffprobe_path, src, info, index, run, log, env=None, any_codec=False):
        self.ffmpeg, self.ffprobe, self.src = ffmpeg_path, ffprobe_path, src
        self.info, self.index, self.run, self.log, self.env = info, index, run, log, env
        v = info["video"]
        self.codec = (v.get("codec_name") or "").lower()
        encs = available_encoders(ffmpeg_path)
        self.encoder = next((e for e in SMARTCUT_ENCODERS.get(self.codec, ()) if e in encs), None)
        try:
            reorder = int(v.get("has_b_frames") or 0)
        except (TypeError, ValueError):
            reorder = 0
        # Víc než 2 B-snímky v řadě (reorder > 2) překódovaný H.264 kousek nedokáže napodobit -> spoje
        # by nebyly na snímek přesné; takový zdroj se v úsecích překóduje celý.
        self.allow_copy = not (self.codec == "h264" and reorder > 2)
        if any_codec and not self.encoder:
            # Fronta video stejně překóduje -> u kodeku, který smart cut neumí (VC-1, MPEG-4...), stačí
            # úseky přesně vyříznout přes mezisoubor H.264 ve vysoké kvalitě.
            self.encoder = "libx264" if "libx264" in encs else None
            self.allow_copy = False
            self._forced = True

    def supported(self):
        """(True, None) nebo (False, důvod) - smart cut potřebuje kodér pro kodek zdroje."""
        if getattr(self, "_forced", False):
            return (True, None) if self.encoder else (False, "ffmpeg nemá kodér libx264")
        if self.codec not in SMARTCUT_ENCODERS:
            return False, f"smart cut zatím umí H.264, HEVC a MPEG-2 (zdroj je {self.codec or '?'})"
        if not self.encoder:
            return False, f"ffmpeg nemá kodér pro {self.codec} ({' / '.join(SMARTCUT_ENCODERS[self.codec])})"
        return True, None

    def _encode_args(self, frames=0):
        v = self.info["video"]
        pix = v.get("pix_fmt") or "yuv420p"
        field = (v.get("field_order") or "progressive").lower()
        interlaced = field in ("tt", "tb", "bb", "bt")
        tff = field in ("tt", "tb")
        e = self.encoder
        # Dlouhý kousek (nouzové překódování celého úseku): hardwarový kodér, je-li k dispozici -
        # libx265 na CPU by u filmu trval hodiny. Krátké okraje řezů zůstávají na ověřeném x264/x265.
        hw = {"h264": "h264_nvenc", "hevc": "hevc_nvenc"}.get(self.codec)
        if frames > 30 / max(self.index.frame_dur, 0.001) and hw and hw in available_encoders(self.ffmpeg) \
                and not getattr(self, "_forced", False) and not getattr(self, "_nvenc_failed", False):
            e = hw
        if e == "libx264" and getattr(self, "_forced", False):
            return ["-c:v", e, "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p"]
        if e == "libx264":
            args = ["-c:v", e, "-preset", "veryfast", "-crf", "15", "-pix_fmt", pix]
            prof = (v.get("profile") or "").lower().replace(" ", "")
            if prof in ("high", "main", "baseline", "high10", "high422", "high444"):
                args += ["-profile:v", prof]
            lvl = v.get("level")
            if isinstance(lvl, int) and 9 < lvl < 70:
                args += ["-level", f"{lvl / 10:.1f}"]
            # Překódovaný kousek musí mít v SPS reorder (max_num_reorder_frames) aspoň jako originál:
            # když na spoji s kopií reorder buffer naroste, dekodér zahodí "opožděné" B-snímky
            # (ověřeno: reorder 0/1 -> ztráta ~10 snímků, reorder 2 = jako x264 zdroj -> beze ztráty).
            # B-pyramida dává reorder 2, víc x264 neumí - zdroje s větším se kopírovat nebudou.
            x264p = "bframes=3:b-pyramid=normal"
            if interlaced:
                args += ["-flags", "+ildct+ilme"]
                x264p += ":tff=1" if tff else ":bff=1"
            return args + ["-x264-params", x264p]
        if e == "libx265":
            return ["-c:v", e, "-preset", "fast", "-crf", "15", "-pix_fmt", pix, "-x265-params", "log-level=error"]
        if e in ("h264_nvenc", "hevc_nvenc"):
            args = ["-c:v", e, "-preset", "p6", "-rc", "vbr", "-cq", "15", "-b:v", "0", "-bf", "3", "-b_ref_mode", "middle"]
            if pix in ("yuv420p", "nv12"):
                args += ["-pix_fmt", "yuv420p"]
            elif "10" in pix and e == "hevc_nvenc":
                args += ["-pix_fmt", "p010le"]
            return args
        if e == "mpeg2video":
            # B-snímky kvůli stejnému "low_delay" jako zdroj (jinak dekodér na spoji zahazuje snímky),
            # uzavřený GOP - kousek nesmí odkazovat mimo sebe.
            args = ["-c:v", e, "-q:v", "2", "-pix_fmt", pix, "-g", "12", "-bf", "2", "-sc_threshold", "1000000000"]
            args += ["-flags", "+cgop+ildct+ilme", "-top", "1" if tff else "0"] if interlaced else ["-flags", "+cgop"]
            return args
        return ["-c:v", e]

    def _count_packets(self, path):
        try:
            res = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
                                  "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", path],
                                 capture_output=True, text=True, timeout=120, creationflags=SUBPROCESS_FLAGS)
            # MPEG-TS vypíše hodnotu dvakrát (program + stopa) - stačí první neprázdný řádek
            first = next((ln.split(",")[0] for ln in (res.stdout or "").splitlines() if ln.strip()), "0")
            return int(first)
        except Exception:
            return -1

    def _src_md5s(self, t, count):
        """MD5 `count` snímků ORIGINÁLU od snímku v čase t. Ne přes prosté -ss: demuxer MPEG-TS umí
        skočit i za cíl -> skok s rezervou a výběr podle absolutních pts (stejně jako při tvorbě kousků)."""
        st = self.info.get("start_time") or 0.0
        eps = self.index.frame_dur / 4
        cmd = [self.ffmpeg, "-hide_banner", "-v", "error", "-copyts", "-ss", f"{max(t - 3.0, 0):.6f}", "-i", self.src,
               "-map", "0:v:0", "-vf", f"trim=start={t + st - eps:.6f}", "-frames:v", str(count),
               "-fps_mode", "passthrough", "-f", "framemd5", "-"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=300, creationflags=SUBPROCESS_FLAGS, env=self.env)
        except Exception:
            return []
        return [ln.rsplit(",", 1)[-1].strip() for ln in (res.stdout or "").splitlines() if ln and not ln.startswith("#")]

    def _frame_md5(self, path, ss=None):
        """MD5 prvního dekódovaného snímku (od času ss) - ověření, že kopie začíná správným snímkem."""
        cmd = [self.ffmpeg, "-hide_banner", "-v", "error"] + (["-ss", f"{ss:.6f}"] if ss is not None else []) + \
              ["-i", path, "-map", "0:v:0", "-frames:v", "1", "-f", "framemd5", "-"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, creationflags=SUBPROCESS_FLAGS,
                                 env=self.env)
            lines = [ln for ln in (res.stdout or "").splitlines() if ln and not ln.startswith("#")]
            return lines[0].rsplit(",", 1)[-1].strip() if lines else None
        except Exception:
            return None

    def _join_ok(self, enc_path, enc_frames, copy_path, copy_start, copy_frames):
        """Dekóduje spoj překódovaná hlava + začátek kopie (spojené po bajtech jako ve výsledku) a porovná
        snímky kopie s originálem (MD5). Zachytí ztrátu/poškození snímků na spoji (jiné SPS, reorder,
        číslování POC u open GOP...) - pak se zkusí jiný klíčový snímek nebo se úsek překóduje celý."""
        m = min(60, copy_frames)
        joined_path = copy_path + ".join.ts"
        try:
            with open(joined_path, "wb") as out:
                for pth in (enc_path, copy_path):
                    with open(pth, "rb") as inp:
                        shutil.copyfileobj(inp, out)
            # -fps_mode passthrough: bez něj by ffmpeg mohl snímky "srovnat" a porovnání by lhalo
            joined = subprocess.run([self.ffmpeg, "-hide_banner", "-v", "error", "-i", joined_path, "-map", "0:v:0",
                                     "-frames:v", str(enc_frames + m), "-fps_mode", "passthrough", "-f", "framemd5", "-"],
                                    capture_output=True, text=True, timeout=300, creationflags=SUBPROCESS_FLAGS, env=self.env)
            want = self._src_md5s(copy_start, m)
        except Exception:
            return False
        finally:
            try:
                os.remove(joined_path)
            except OSError:
                pass
        md5 = lambda out: [ln.rsplit(",", 1)[-1].strip() for ln in (out or "").splitlines() if ln and not ln.startswith("#")]
        got = md5(joined.stdout)
        return len(want) == m and got[enc_frames:enc_frames + m] == want

    def _first_pts(self, path):
        """Nejmenší PTS videa mezi prvními pakety kousku (= čas prvního zobrazeného snímku)."""
        try:
            res = subprocess.run([self.ffprobe, "-v", "error", "-select_streams", "v:0", "-read_intervals", "%+#60",
                                  "-show_entries", "packet=pts_time", "-of", "csv=p=0", path],
                                 capture_output=True, text=True, timeout=120, creationflags=SUBPROCESS_FLAGS)
            vals = [float(x.split(",")[0]) for x in (res.stdout or "").split() if x.split(",")[0] not in ("", "N/A")]
            return min(vals) if vals else None
        except Exception:
            return None

    def _fix_start(self, path, expected, label):
        """Ověří, že první snímek překódovaného kousku má PTS přesně tam, kde má být, jinak kousek
        přebalí s posunem. Některé kodéry časy na výstupu posunou (libx265 o zpoždění B-snímků -
        ověřeno A/V testem: hlavy o 2 snímky napřed)."""
        first = self._first_pts(path)
        if first is None:
            return False
        delta = expected - first
        if abs(delta) < self.index.frame_dur / 4:
            return True
        fixed = path + ".fix.ts"
        cmd = [self.ffmpeg, "-hide_banner", "-y", "-copyts", "-i", path, "-map", "0:v:0", "-c", "copy",
               "-bsf:v", f"setts=pts=PTS+{delta:.6f}/TB:dts=DTS+{delta:.6f}/TB",
               "-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", fixed]
        rc = self.run(cmd, 0, f"{label} (korekce času {delta * 1000:+.0f} ms)")
        if rc != 0 or not os.path.exists(fixed):
            return None if rc is None else False
        os.replace(fixed, path)
        return True

    def _make_piece(self, pc, path, off):
        """Vytvoří kousek videa (MPEG-TS, časy = absolutní časy originálu). Vrací True/False/None.
        Skok se dělá vždy hrubě S REZERVOU a přesný výběr snímků podle absolutních pts (-copyts):
        demuxer MPEG-TS umí skočit i ZA cíl (ověřeno: open GOP .ts o 13 snímků), MKV jen na začátek
        clusteru. Když skok přistál pozdě, zkusí se větší rezerva.
        off = posun (s): kousek dostane rovnou FINÁLNÍ časy výstupu (absolutní čas - off), takže se kousky
        pak jen spojí za sebe po bajtech - bez concat demuxeru, který začátek kousku odvozoval z DTS
        (kopie se předbíhaly o ~2 snímky) a s inpoint znovu nepřesně skákal."""
        eps = self.index.frame_dur / 4
        st = self.info.get("start_time") or 0.0
        a_abs = pc["start"] + st
        last = self.index.pts[self.index.frame_no(pc["end"] - eps)]
        b_abs = last + st
        mux = ["-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts", path]
        is_copy = pc["kind"] == "copy"
        label = f"{'kopie' if is_copy else 'překódování'} {fmt_tc(pc['start'])}–{fmt_tc(pc['end'])}"
        want = (self._src_md5s(pc["start"], 1) or [None])[0] if is_copy else None
        prerolls = (3.0, 15.0, None)
        for attempt, pre in enumerate(prerolls):
            ss = 0.0 if pre is None else max(pc["start"] - pre, 0.0)
            head = [self.ffmpeg, "-hide_banner", "-y", "-copyts", "-ss", f"{ss:.6f}",
                    "-t", f"{last - ss + 2:.6f}", "-i", self.src, "-map", "0:v:0", "-an", "-sn", "-dn"]
            if is_copy:
                # Pakety mimo [K1, X] zahodí bitstream filtr noise podle pts - to zároveň zahodí "open GOP"
                # B-snímky před K1, které se dekódují až po něm (patří do hlavy).
                bsf = f"noise=drop=lt(pts*tb\\,{a_abs - eps:.6f})+gt(pts*tb\\,{b_abs + eps:.6f})"
                bsf += f",setts=pts=PTS-{off:.6f}/TB:dts=DTS-{off:.6f}/TB"
                if self.codec in ("h264", "hevc") and os.path.splitext(self.src)[1].lower() not in (".ts", ".m2ts", ".mts", ".trp"):
                    # SPS/PPS jen v hlavičce (MKV/MP4): převod na Annex B AŽ po zahození paketů
                    bsf += f",{self.codec}_mp4toannexb"
                cmd = head + ["-c:v", "copy", "-bsf:v", bsf] + mux
            else:
                vf = f"trim=start={a_abs - eps:.6f}:end={b_abs + eps:.6f},setpts=PTS-{off:.6f}/TB"
                if self.encoder == "libx265":
                    # libx265 počítá DTS, jako by 1 tik časové základny = 1 snímek -> s časovou základnou
                    # zdroje (1/1000 MKV, 1/90000 TS) vyjdou PTS v pořadí dekódování (B-snímky by se
                    # zobrazily ve špatném čase). Časová základna 1/fps to srovná.
                    fr = str(self.info["video"].get("r_frame_rate") or "")
                    if re.fullmatch(r"\d+/\d+", fr) and not fr.startswith("0"):
                        num, den = fr.split("/")
                        vf += f",settb={den}/{num}"
                # setts: kousek o 1 snímku (jen I) dostal od muxeru prázdné DTS (ověřeno na MPEG-2)
                enc_args = self._encode_args(pc["frames"])
                if "-c:v" in enc_args and enc_args[enc_args.index("-c:v") + 1] != self.encoder:
                    self.log(f"  [střih] {label}: dlouhý úsek - kóduji přes {enc_args[enc_args.index('-c:v') + 1]}")
                cmd = head + ["-vf", vf] + enc_args + \
                      ["-bsf:v", "setts=dts=if(eq(DTS\\,NOPTS)\\,PTS\\,DTS)"] + mux
            rc = self.run(cmd, pc["end"] - pc["start"], label)
            if rc is None:
                return None
            if rc != 0 or not os.path.exists(path):
                if is_copy:
                    self.log(f"  [střih] {label}: kopie selhala (ffmpeg starší než 5.1 neumí noise=drop?)", debug=True)
                    return False
                if "nvenc" in " ".join(enc_args):
                    self._nvenc_failed = True      # NVENC nejde (ovladač/GPU) -> další pokus softwarově
                    self.log(f"  [střih] {label}: NVENC selhal, zkouším softwarový kodér.")
                continue
            n = self._count_packets(path)
            if is_copy:
                if n == pc["frames"] and want and self._frame_md5(path) == want:
                    return True
                self.log(f"  [střih] {label}: kopie nesedí ({n}/{pc['frames']} snímků), pokus {attempt + 1}", debug=True)
                continue
            if n == pc["frames"]:
                return self._fix_start(path, a_abs - off, label)
            at_eof = pc["end"] >= self.index.end - eps
            if n > 0 and (at_eof or pre is None):
                # Konec souboru / poškozený TS - dekodér pár snímků nevydá. Kousek se přijme a v concat
                # seznamu si nechá plánovanou délku: přehrávač podrží snímek a zvuk zůstane synchronní.
                self.log(f"  [střih] {label}: dekodér vydal {n} z {pc['frames']} snímků - chybějící se podrží.", error=True)
                return self._fix_start(path, a_abs - off, label)
            self.log(f"  [střih] {label}: {n}/{pc['frames']} snímků, skok přistál pozdě - větší rezerva", debug=True)
        return False

    def _build_segment(self, pieces, workdir, off):
        """Vytvoří kousky jednoho úseku. Vrací (True/False/None, [(cesta, délka)])."""
        files = []
        for j, pc in enumerate(pieces):
            self._piece_no += 1
            path = os.path.abspath(os.path.join(workdir, f"v{self._piece_no:05d}.ts"))
            res = self._make_piece(pc, path, off)
            if res is None:
                return None, files
            if res is False:
                return False, files + [(path, 0)]
            if pc["kind"] == "copy" and j > 0 and pieces[j - 1]["kind"] == "enc":
                if not self._join_ok(files[-1][0], pieces[j - 1]["frames"], path, pc["start"], pc["frames"]):
                    self.log(f"  [střih] spoj u {fmt_tc(pc['start'])} nesedí na snímek", debug=True)
                    return False, files + [(path, 0)]
            files.append((path, pc["end"] - pc["start"]))
        return True, files

    def execute(self, segments, out_path, workdir, audio=None, subs=None):
        """Vrací (True, None), (False, důvod) nebo (None, None) při přerušení."""
        ok, why = self.supported()
        if not ok:
            return False, why
        if not segments:
            return False, "žádný úsek k zachování"
        if not self.allow_copy:
            self.log("  [střih] Zdroj má víc než 2 B-snímky v řadě - úseky se překódují celé.")
        self.log(f"  [střih] {len(segments)} úsek(ů), kodér pro okraje řezů: {self.encoder}.")
        self._piece_no = 0
        done = []
        t_copy = t_enc = 0.0
        # Úseky přichycené na snímky (stejně jako v plan_segment) - video i zvuk musí mít shodné hranice.
        snapped = []
        for sg in segments:
            a = self.index.snap(sg["start"])
            b = sg["end"] if sg["end"] >= self.index.end - 1e-6 else self.index.snap(sg["end"])
            if b - a > 1e-6:
                snapped.append({"start": a, "end": b})
        segments = snapped
        st = self.info.get("start_time") or 0.0
        # rezerva, ať časy výstupu nejsou záporné (DTS hlavy je o pár snímků před PTS); celé snímky,
        # aby časy překódovaných snímků vycházely přesně na časovou základnu 1/fps
        base = round(10.0 / self.index.frame_dur) * self.index.frame_dur
        cum = 0.0
        for si, sg in enumerate(segments):
            off = sg["start"] + st - cum - base
            # Kopie od prvního klíčového snímku; když spoj s překódovanou hlavou nesedí na snímek
            # (u open GOP H.264 záleží na číslování snímků POC), zkusí se další klíčový snímek
            # a nakonec se úsek překóduje celý - výsledek je vždy přesný, jen pomalejší.
            attempts = (copy_candidates(self.index, sg["start"], sg["end"]) if self.allow_copy else []) + [False]
            for k1 in attempts:
                pieces = plan_segment(self.index, sg["start"], sg["end"], k1)
                if not pieces:
                    break
                res, files = self._build_segment(pieces, workdir, off)
                if res is None:
                    return None, None
                if res:
                    done.extend(files)
                    for pc in pieces:
                        if pc["kind"] == "copy":
                            t_copy += pc["end"] - pc["start"]
                        else:
                            t_enc += pc["end"] - pc["start"]
                    break
                for path, _ in files:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                if k1 is False:
                    return False, f"úsek {si + 1} ({fmt_tc(sg['start'])}) se nepodařilo vytvořit"
                self.log(f"  [střih] Úsek {si + 1}: kopie od {fmt_tc(k1)} nesedí na snímek, "
                         f"zkouším {'další klíčový snímek' if attempts.index(k1) < len(attempts) - 2 else 'překódovat celý úsek'}.")
            cum += sg["end"] - sg["start"]
        if not done:
            return False, "žádný úsek k zachování"
        self.log(f"  [střih] Video: {fmt_duration(t_copy)} zkopírováno beze ztráty, {fmt_duration(t_enc)} překódováno.")

        # Kousky už nesou finální časy -> spojí se prostě za sebe (MPEG-TS se tak spojovat dá).
        video_ts = os.path.abspath(os.path.join(workdir, "video.ts"))
        with open(video_ts, "wb") as out:
            for path, _ in done:
                with open(path, "rb") as inp:
                    shutil.copyfileobj(inp, out)
                try:
                    os.remove(path)
                except OSError:
                    pass

        # Zvuk + titulky: po úsecích z originálu, kopií. Stejný problém jako u videa - demuxer skočí
        # na indexový bod (u MKV začátek clusteru) a concat demuxer s inpoint by vzal i pakety před
        # řezem. Proto: hrubý skok, pakety mimo [A, B) zahodí noise podle pts, mezisoubor NUT
        # (unese jakýkoliv kodek a nemění časy). Bez -copyts je časová osa výstupu přesně
        # "čas od začátku souboru - ss", takže A, B jsou v ní známé a concat (inpoint/outpoint/
        # duration) každý úsek položí přesně za předchozí - bez sčítání chyb.
        ext = os.path.splitext(out_path)[1].lower()
        bad_subs = _CONTAINER_BAD_SUBS.get(ext, {"dvb_teletext"})
        av_streams = []
        codecs = {str(st.get("index")): st.get("codec_name") for st in self.info["streams"]}
        for st in self.info["streams"]:
            idx, kind, codec = str(st.get("index")), st.get("codec_type"), st.get("codec_name")
            if kind == "audio" and (audio is None or idx in audio):
                av_streams.append(idx)
            elif kind == "subtitle" and (subs is None or idx in subs):
                if subs is None and codec in bad_subs:
                    continue
                av_streams.append(idx)
        alist = None
        # Mezisoubor zvuku/titulků: MPEG-TS pro TS výstup (unese aac_latm z DVB-T2, teletext, DVB titulky -
        # MKV ne), jinak MKV (unese SRT/ASS/PGS - MPEG-TS ne).
        part_ts = ext in TS_EXTENSIONS
        if av_streams:
            alist = os.path.abspath(os.path.join(workdir, "av.ffconcat"))
            entries = []
            for si, sg in enumerate(segments):
                a, b = sg["start"], sg["end"]
                ss = max(a - 5.0, 0.0)
                # Zvuk: jen pakety CELÉ uvnitř úseku - paket přečnívající konec by se překryl se začátkem
                # dalšího úseku a nemonotónní časy v MPEG-TS ffmpeg/přehrávače "opravují" posunem zvuku.
                # Mezera kratší než jeden paket nevadí. Titulky podle začátku (přesah konce řeže přehrávač).
                drop_a = (f"lt(pts*tb\\,{a - ss - 0.0005:.6f})"
                          f"+gt((pts+duration)*tb\\,{b - ss + 0.0005:.6f})")
                drop_s = f"lt(pts*tb\\,{a - ss - 0.0005:.6f})+gte(pts*tb\\,{b - ss - 0.0005:.6f})"
                part = os.path.abspath(os.path.join(workdir, f"a{si:04d}{'.ts' if part_ts else '.mkv'}"))
                cmd = [self.ffmpeg, "-hide_banner", "-y", "-ss", f"{ss:.6f}", "-t", f"{b - ss + 2:.6f}",
                       "-i", self.src]
                for idx in av_streams:
                    cmd += ["-map", f"0:{idx}"]
                cmd += ["-c", "copy"]
                for n, idx in enumerate(av_streams):
                    if codecs.get(idx) == "mov_text":
                        cmd += [f"-c:{n}", "srt"]      # mov_text MKV neunese - zpět se převede při MP4 výstupu
                # setts: úsek v mezisouboru začíná přesně v 0 (A) -> concat s "inpoint 0" bez skoku uprostřed
                # souboru (MPEG-TS skáče nepřesně) a bez odhadu začátku podle prvního paketu.
                shift = f"setts=pts=PTS-{a - ss:.6f}/TB:dts=DTS-{a - ss:.6f}/TB"
                cmd += ["-bsf:a", f"noise=drop={drop_a},{shift}", "-bsf:s", f"noise=drop={drop_s},{shift}"]
                cmd += (["-muxdelay", "0", "-muxpreload", "0", "-f", "mpegts"] if part_ts else ["-f", "matroska"]) + [part]
                rc = self.run(cmd, b - a, f"zvuk/titulky úseku {si + 1}")
                if rc is None:
                    return None, None
                if rc != 0:
                    return False, f"zvuk/titulky úseku {si + 1} se nepodařilo vyříznout"
                entries.append(f"file {_concat_quote(part)}\ninpoint 0\nduration {b - a:.6f}\n")
            with open(alist, "w", encoding="utf-8") as f:
                f.write("ffconcat version 1.0\n" + "".join(entries))

        # -copyts: ffmpeg jinak od každého vstupu odečte jeho "začátek" - u videa odvozený z DTS (o pár
        # snímků dřív než první PTS), což by obraz proti zvuku posunulo. Časy se tak řídí jen tím,
        # co jsme nastavili sami (video: -base níže, zvuk: concat demuxer od nuly).
        cmd = [self.ffmpeg, "-hide_banner", "-y", "-copyts", "-i", video_ts]
        if alist:
            cmd += ["-f", "concat", "-safe", "0", "-i", alist]
        # DTS videa se přepočítají rovnoměrně podle pořadí paketů: kousky mají různé zpoždění DTS za PTS
        # (kopie z open GOP až 5 snímků, překódované 2), takže by na spojích DTS klesalo a muxer by je
        # "opravil" na DTS > PTS - takové snímky přehrávač zahodí. N-tý paket dostane DTS =
        # začátek + (N - 4) snímků: vždy roste a nepředběhne PTS (reorder v kouscích je nejvýš 2-3).
        fd = self.index.frame_dur
        # Video nese časy posunuté o "base" (rezerva proti záporným časům v kouscích) - tady se vrátí
        # na nulu, aby sedělo se zvukem z concat demuxeru, který začíná v nule.
        cmd += ["-map", "0:v:0", "-bsf:v",
                f"setts=pts=PTS-{base:.6f}/TB:dts=STARTPTS-{base:.6f}/TB+(N-4)*{fd:.9f}/TB"]
        for n in range(len(av_streams)):
            cmd += ["-map", f"1:{n}"]
        cmd += ["-c", "copy"]
        if ext in (".mp4", ".m4v", ".mov") and any(codecs.get(i) == "mov_text" for i in av_streams):
            cmd += ["-c:s", "mov_text"]
        cmd += ["-max_interleave_delta", "0", out_path]
        rc = self.run(cmd, segments_duration(segments), "spojení")
        if rc is None:
            return None, None
        if rc != 0:
            return False, "spojení kousků selhalo (viz log)"
        return True, None


# ==========================================================================
#  SOUBOR FRONTY (.ffm) - uložení/načtení připravené fronty
# ==========================================================================
#
# .ffm je JSON se seznamem položek fronty. Ukládá se jen to, co nastavil uživatel (vybrané stopy,
# NFO, kopírování videa, ořez, střih) + identita zdrojového souboru (cesta, velikost, délka).
# Data z ffprobe (stopy, rozlišení) se neukládají - po načtení se soubor analyzuje znovu, takže
# položka vždy odpovídá skutečnému souboru na disku a změna zdroje se pozná (velikost/délka).
# Jeden formát pokrývá celou uloženou frontu i výstup z nástroje Střih (fronta s 1 položkou).
# "version" se zvýší jen při nekompatibilní změně; nové volitelné klíče verzi nemění
# (starší verze programu neznámé klíče ignoruje).

QUEUE_FILE_EXT = ".ffm"
QUEUE_FILE_FORMAT = "ffmpeg-master-queue"
QUEUE_FILE_VERSION = 1
QUEUE_ITEM_SETTINGS = ("selected_audio", "selected_subs", "nfo", "generate_nfo", "copy_video", "crop", "segments")


def fit_filename(base, suffix, limit=240):
    """base + suffix tak, aby název nepřekročil limit bajtů (Linux: max 255 B na název souboru;
    TV nahrávky mívají názvy skoro na limitu a přípona "_strih"/".ffm" by je přetáhla)."""
    room = limit - len(suffix.encode("utf-8"))
    raw = base.encode("utf-8")
    if len(raw) > room:
        base = raw[:room].decode("utf-8", errors="ignore").rstrip(" ._-")
    return base + suffix


def queue_item_to_ffm(item, ffm_dir):
    """Převede položku fronty na záznam do .ffm. Cesta se uloží absolutně a navíc relativně k .ffm,
    pokud zdroj leží ve stejném stromu - pak jde složku s .ffm i videi přesunout/připojit jinam."""
    path = os.path.abspath(item["path"])
    entry = {"path": path}
    try:
        rel = os.path.relpath(path, ffm_dir)
        if not rel.startswith(".."):
            entry["rel_path"] = rel.replace(os.sep, "/")
    except ValueError:   # Windows: jiný disk než .ffm
        pass
    try:
        entry["size"] = os.path.getsize(path)
    except OSError:
        pass
    entry["duration"] = item.get("duration")
    for key in QUEUE_ITEM_SETTINGS:
        if key in item:
            entry[key] = copy.deepcopy(item[key])
    return entry


def save_queue_file(ffm_path, items):
    ffm_dir = os.path.dirname(os.path.abspath(ffm_path))
    data = {
        "format": QUEUE_FILE_FORMAT,
        "version": QUEUE_FILE_VERSION,
        "app_version": VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "items": [queue_item_to_ffm(it, ffm_dir) for it in items],
    }
    # Dočasný soubor s krátkým názvem (název .ffm může být těsně u limitu 255 B) - atomicky přejmenovat,
    # ať při chybě zápisu nezůstane rozbitý .ffm.
    fd, tmp_path = tempfile.mkstemp(prefix=".ffm_", suffix=".tmp", dir=ffm_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, ffm_path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def load_queue_file(ffm_path):
    """Načte .ffm a vrátí seznam záznamů; každý má navíc "_resolved" = cesta ke zdroji, která
    existuje (relativní k .ffm má přednost před uloženou absolutní). Chybu hlásí ValueError s českou
    zprávou (OSError při čtení propadne volajícímu)."""
    with open(ffm_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"soubor není platný JSON ({e})")
    if not isinstance(data, dict) or data.get("format") != QUEUE_FILE_FORMAT:
        raise ValueError("soubor není fronta FFMPEG Master")
    ver = data.get("version")
    if not isinstance(ver, int) or ver < 1:
        raise ValueError(f"neznámá verze formátu ({ver!r})")
    if ver > QUEUE_FILE_VERSION:
        raise ValueError(f"fronta je uložená novější verzí programu ({data.get('app_version', '?')}, "
                         f"formát {ver}) - aktualizuj FFMPEG Master")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("v souboru chybí seznam položek")
    ffm_dir = os.path.dirname(os.path.abspath(ffm_path))
    out = []
    for entry in items:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        candidates = []
        if entry.get("rel_path"):
            candidates.append(os.path.normpath(os.path.join(ffm_dir, entry["rel_path"])))
        candidates.append(os.path.normpath(entry["path"]))
        entry["_resolved"] = next((c for c in candidates if os.path.isfile(c)), candidates[-1])
        out.append(entry)
    return out


def _same_host(a, b):
    """Porovná dva názvy/adresy serveru - "192.168.1.2" vs. "nas" vs. "NAS.local" apod."""
    a, b = (a or "").lower().strip("[]"), (b or "").lower().strip("[]")
    if not a or not b:
        return False
    if a == b or a.split(".")[0] == b.split(".")[0] and not a.replace(".", "").isdigit():
        return True
    try:
        return socket.gethostbyname(a) == socket.gethostbyname(b)
    except Exception:
        return False


def _unescape_mount_field(value):
    """/proc/mounts kóduje mezery a spol. oktalově (\\040) - vrátí původní text."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def _smb_to_local_path(host, share, rest, mounts_file="/proc/mounts", gvfs_dirs=None):
    """Najde lokální cestu k souboru na SMB sdílení, pokud je sdílení připojené:
    1) klasický CIFS mount (mount -t cifs, fstab, autofs, vlastní mount skripty) - z /proc/mounts,
    2) GVFS (GNOME, Cinnamon/Mint, XFCE) - /run/user/<uid>/gvfs/smb-share:server=...,share=...
    Vrací cestu, nebo None."""
    rest = rest.strip("/")
    try:
        with open(mounts_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3 or parts[2] not in ("cifs", "smb3", "smbfs"):
                    continue
                source = _unescape_mount_field(parts[0]).replace("\\", "/")
                mountpoint = _unescape_mount_field(parts[1])
                m = re.match(r"^//([^/]+)/([^/]+)(?:/(.*))?$", source)
                if not m or not _same_host(m.group(1), host) or m.group(2).lower() != share.lower():
                    continue
                subdir = (m.group(3) or "").strip("/")
                if subdir:
                    if rest.lower() != subdir.lower() and not rest.lower().startswith(subdir.lower() + "/"):
                        continue
                    rel = rest[len(subdir):].lstrip("/")
                else:
                    rel = rest
                candidate = os.path.join(mountpoint, rel)
                if os.path.exists(candidate):
                    return candidate
    except Exception:
        pass

    if gvfs_dirs is None:
        gvfs_dirs = []
        if hasattr(os, "getuid"):
            gvfs_dirs.append(f"/run/user/{os.getuid()}/gvfs")
        gvfs_dirs.append(os.path.join(os.path.expanduser("~"), ".gvfs"))
    for gvfs in gvfs_dirs:
        try:
            entries = os.listdir(gvfs)
        except Exception:
            continue
        for entry in entries:
            if not entry.startswith("smb-share:"):
                continue
            opts = dict(kv.split("=", 1) for kv in entry[len("smb-share:"):].split(",") if "=" in kv)
            if _same_host(urllib.parse.unquote(opts.get("server", "")), host) and \
                    urllib.parse.unquote(opts.get("share", "")).lower() == share.lower():
                candidate = os.path.join(gvfs, entry, rest)
                if os.path.exists(candidate):
                    return candidate
    return None


def _kio_fuse_mount(url, timeout=45):
    """Požádá službu kio-fuse (KDE) o zpřístupnění KIO adresy (smb://, sftp://, ftp://...) jako
    lokální cesty - stejné D-Bus volání, jaké KDE samo používá při "Otevřít pomocí" v aplikaci,
    která KIO nezná. Dolphin při drag&drop takovou cestu neposílá (jen původní URL), proto to
    appka udělá sama. Přihlašovací údaje bere kio-fuse z KDE (KWallet) - případně se zeptá dialogem,
    proto delší timeout. Vrací lokální cestu, nebo None (kio-fuse není / URL nešlo připojit)."""
    commands = []
    if shutil.which("gdbus"):
        commands.append(["gdbus", "call", "--session", "--dest", "org.kde.KIOFuse",
                         "--object-path", "/org/kde/KIOFuse", "--method", "org.kde.KIOFuse.VFS.mountUrl", url])
    if shutil.which("dbus-send"):
        commands.append(["dbus-send", "--session", "--print-reply", "--type=method_call",
                         "--dest=org.kde.KIOFuse", "/org/kde/KIOFuse",
                         "org.kde.KIOFuse.VFS.mountUrl", "string:" + url])
    for cmd in commands:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=timeout)
        except Exception:
            continue
        if res.returncode != 0:
            continue
        # gdbus:     ('/run/user/1000/kio-fuse-XXXX/smb/...',)
        # dbus-send: method return ...\n   string "/run/user/1000/kio-fuse-XXXX/smb/..."
        m = re.search(r"\('(.*)',\)", res.stdout, re.S) or re.search(r'string "(.*)"', res.stdout, re.S)
        if m and os.path.exists(m.group(1)):
            return m.group(1)
    return None


def resolve_dropped_path(item):
    """Převede položku z drag&drop na lokální cestu. Vrací (cesta, None) nebo (None, "důvod").

    Správce souborů nemusí posílat cestu, ale URL: file:///home/... (některá prostředí), nebo
    smb://uzivatel@server/sdileni/... - typicky Dolphin (KDE) u souborů ze síťového umístění
    otevřeného přímo v okně, bez připojení do systému. Takové URL ffprobe/ffmpeg neotevřou
    spolehlivě a zbytek appky (výstup, přesun/přejmenování zdroje) s ním pracovat vůbec nemůže.
    Proto se smb:// převede na cestu v už připojeném sdílení (CIFS mount / GVFS), a když žádné
    není, vrátí se srozumitelný důvod."""
    if "://" not in item:
        return os.path.normpath(item), None
    parsed = urllib.parse.urlparse(item)
    scheme = parsed.scheme.lower()
    if scheme == "file":
        return os.path.normpath(urllib.parse.unquote(parsed.path)), None
    if scheme in ("smb", "cifs"):
        host = parsed.hostname or ""
        path_parts = urllib.parse.unquote(parsed.path).lstrip("/").split("/", 1)
        share = path_parts[0]
        rest = path_parts[1] if len(path_parts) > 1 else ""
        local = _smb_to_local_path(host, share, rest)
        if local:
            return os.path.normpath(local), None
    if sys.platform != "win32":
        # Není připojené klasicky ani přes GVFS - zkus kio-fuse (KDE). Funguje i pro sftp:// apod.
        local = _kio_fuse_mount(item)
        if local:
            return os.path.normpath(local), None
    if scheme in ("smb", "cifs"):
        return None, (f"soubor je na síťovém sdílení \\\\{host}\\{share}, které není připojené jako složka "
                      f"(správce souborů poslal jen adresu smb://). Připoj sdílení do systému (CIFS mount / fstab), "
                      f"na KDE nainstaluj balíček kio-fuse, na GNOME/Mint otevři sdílení ve správci souborů "
                      f"- a pak soubor přetáhni znovu.")
    return None, (f"nepodporovaný typ adresy {scheme}:// - přetáhni soubor z lokální nebo připojené složky "
                  f"(na KDE pomůže balíček kio-fuse)")


def default_selection(streams, auto_langs, fallback_to_others):
    """Vybere výchozí sadu indexů stop podle jazyka: preferovaný jazyk > bez jazyka > (volitelně) ostatní."""
    cz, no_lang, others = [], [], []
    for s in streams:
        lang = s["lang"]
        if lang in auto_langs:
            cz.append(s["index"])
        elif lang in ("UND", ""):
            no_lang.append(s["index"])
        else:
            others.append(s["index"])
    if cz:
        return cz
    if no_lang:
        return no_lang
    if fallback_to_others:
        return others
    return []


def default_nfo(filename):
    base_name = os.path.splitext(filename)[0]
    year_match = re.search(r"\((\d{4})\)", base_name)
    return {
        "title": base_name.replace("_", ":"),
        "year": year_match.group(1) if year_match else "",
        "genre": None,   # doplní se z config default_genre při otevření dialogu
        "plot": ""
    }


def fmt_bitrate(b):
    if not b:
        return "N/A"
    try:
        return f"{int(b) // 1000} kb/s"
    except Exception:
        return str(b)


def fmt_duration(dur):
    m, s = divmod(int(dur), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"


def default_encode_args(cfg):
    """Sestaví seznam FFmpeg argumentů pro kódování (video+audio kodek) ze strukturovaného configu.
    Používá se jako výchozí hodnota, když vlastní příkaz není zapnutý/vyplněný."""
    v, a = cfg["video"], cfg["audio"]
    return ["-c:v", str(v["codec"]), "-pix_fmt", str(v["pix_fmt"]), "-profile:v", str(v["profile"]),
            "-rc", str(v["rc"]), "-cq", str(v["cq"]), "-preset", str(v["preset"]),
            "-c:a", str(a["codec"]), "-b:a", str(a["bitrate"]),
            "-c:s", "copy", "-max_muxing_queue_size", "1000000"]


def force_copy_video(encode_args):
    """Nahradí (nebo doplní) '-c:v <cokoliv>' za '-c:v copy' - použije se, když má daný soubor
    zapnuté 'Kopírovat video beze změny'. Funguje jak na výchozí, tak na vlastní encode_args.
    Zároveň odstraní enkodérové přepínače, které jsou u 'copy' bezpředmětné (jinak ffmpeg jen
    hlásí neškodná, ale rušivá varování o nevyužitých volbách)."""
    args = list(encode_args)
    # Smaž dvojice "-flag hodnota" pro přepínače relevantní jen při skutečném překódování videa.
    for flag in ("-pix_fmt", "-profile:v", "-rc", "-cq", "-preset"):
        while flag in args:
            idx = args.index(flag)
            del args[idx:idx + 2]
    if "-c:v" in args:
        idx = args.index("-c:v")
        if idx + 1 < len(args):
            args[idx + 1] = "copy"
        else:
            args.append("copy")
    else:
        args = ["-c:v", "copy"] + args
    return args


DRIVE_REMOTE = 4  # Windows GetDriveTypeW konstanta pro síťovou jednotku


# Souborové systémy, které na Linuxu znamenají "soubor je na síti" (CIFS/SMB, NFS, a FUSE
# zprostředkovatelé síťových protokolů: GVFS na GNOME/Mint, kio-fuse na KDE, sshfs, rclone).
_LINUX_NETWORK_FS = ("cifs", "smb3", "smbfs", "nfs", "nfs4", "9p", "fuse.gvfsd-fuse", "fuse.kio-fuse",
                     "fuse.sshfs", "fuse.rclone", "fuse.davfs", "davfs")


def _linux_mount_fstype(path, mounts_file="/proc/mounts"):
    """Typ souborového systému, na kterém cesta leží (nejdelší shodný mount point)."""
    real = os.path.realpath(path)
    best, best_type = "", None
    with open(mounts_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 3:
                continue
            mnt = _unescape_mount_field(parts[1]).rstrip("/") or "/"
            if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and len(mnt) > len(best):
                best, best_type = mnt, parts[2]
    return best_type


def is_network_path(path):
    """True pro UNC cesty (\\\\server\\share\\...) i pro mapované síťové jednotky (Z:\\...) na Windows;
    na Linuxu pro soubory na síťovém souborovém systému (CIFS, NFS, GVFS, kio-fuse, sshfs...).
    Podle toho se volí source_action_network místo source_action_local."""
    try:
        abspath = os.path.abspath(path)
        if abspath.startswith("\\\\") or abspath.startswith("//"):
            return True
        if sys.platform == "win32":
            drive = os.path.splitdrive(abspath)[0]
            if drive:
                return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOTE
            return False
        return (_linux_mount_fstype(abspath) or "") in _LINUX_NETWORK_FS
    except Exception:
        return False


# ==========================================================================
#  DIALOG: VÝBĚR STOP (+ NFO metadata) PŘI PŘIDÁNÍ SOUBORU
# ==========================================================================

class TrackSelectionDialog(ctk.CTkToplevel):
    def __init__(self, parent, filename, probe, cfg, mode="add", initial=None, path=None):
        """mode: "add" (soubor se přidává do fronty) nebo "edit" (úprava už zařazeného souboru).
        path: cesta ke zdroji (pro detekci ořezu a náhled).
        initial: při "edit" dict {"selected_audio", "selected_subs", "nfo"} s aktuálním stavem položky."""
        super().__init__(parent)
        self.mode = mode
        self.title(f"{'Úprava výběru' if mode == 'edit' else 'Výběr stop'} – {filename}")
        self.geometry("820x640")
        self.minsize(700, 500)
        self.transient(parent)
        # update_idletasks() před grab_set() - grab vyžaduje, aby okno bylo už "viewable"
        # (namapované window managerem), což se nestane hned po vytvoření/geometry() ale až
        # asynchronně. Bez tohohle grab_set() občas (podle přesného časování, viz stejný bug u
        # AboutDialog) spadne na "grab failed: window not viewable".
        self.update_idletasks()
        self.grab_set()
        self.resizable(True, True)

        self.probe = probe
        self.cfg = cfg
        self.path = path
        self.crop = copy.deepcopy((initial or {}).get("crop"))
        self.result = None  # zůstane None při zrušení

        auto_langs = cfg.get("auto_select_languages", ["CZE", "CES"])
        self.default_audio_sel = default_selection(probe["audio_streams"], auto_langs, fallback_to_others=True)
        self.default_sub_sel = default_selection(probe["sub_streams"], auto_langs, fallback_to_others=False)
        # Počáteční zaškrtnutí checkboxů: při úpravě aktuální výběr položky, jinak automatický default.
        init_audio_sel = initial["selected_audio"] if initial else self.default_audio_sel
        init_sub_sel = initial["selected_subs"] if initial else self.default_sub_sel
        init_nfo = (initial or {}).get("nfo") or None

        # --- Hlavička ---
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(head, text=filename, font=("Arial", 14, "bold")).pack(anchor="w")
        v = probe["video"]
        ctk.CTkLabel(head, text=f"Video: {v['res']} ({v['codec_name']})   •   Délka: {fmt_duration(probe['duration'])}",
                     font=("Arial", 11), text_color="gray").pack(anchor="w", pady=(2, 0))

        self.copy_video_var = ctk.BooleanVar(value=(initial or {}).get("copy_video", False))
        ctk.CTkCheckBox(head, text="🎬 Kopírovat video beze změny (bez překódování, bez zmenšení)",
                         variable=self.copy_video_var).pack(anchor="w", pady=(6, 0))
        ctk.CTkLabel(head, text="Video se nebude dekódovat ani zmenšovat - jen se zkopíruje. Výstup bude větší a "
                                 "beze změny rozlišení, ale nehrozí pád na poškozených snímcích. Zvuk se normalizuje normálně.",
                     font=("Arial", 10), text_color="gray", wraplength=760, justify="left").pack(anchor="w", pady=(1, 0))

        crop_row = ctk.CTkFrame(head, fg_color="transparent")
        crop_row.pack(anchor="w", fill="x", pady=(8, 0))
        ctk.CTkLabel(crop_row, text="▣ Ořez okrajů:", font=("Arial", 12, "bold")).pack(side="left")
        ctk.CTkButton(crop_row, text="Nastavit ořez…", width=130, command=self._open_crop,
                      state="normal" if path else "disabled").pack(side="left", padx=(10, 0))
        self.btn_crop_clear = ctk.CTkButton(crop_row, text="Zrušit ořez", width=100, fg_color="#555555",
                                            command=self._clear_crop)
        self.btn_crop_clear.pack(side="left", padx=(6, 0))
        self.crop_label = ctk.CTkLabel(crop_row, text="", font=("Arial", 11), anchor="w", justify="left",
                                       wraplength=420)
        self.crop_label.pack(side="left", padx=(10, 0), fill="x", expand=True)
        self.copy_video_var.trace_add("write", lambda *_: self._update_crop_label())
        self._update_crop_label()

        # --- Scrollovatelná oblast s tabulkou stop ---
        scroll = ctk.CTkScrollableFrame(self, label_text="Zvukové a titulkové stopy")
        scroll.pack(fill="both", expand=True, padx=16, pady=6)

        self.audio_vars = {}
        self.sub_vars = {}

        if probe["audio_streams"]:
            ctk.CTkLabel(scroll, text="🔊 Audio", font=("Arial", 12, "bold")).pack(anchor="w", pady=(4, 2))
            self._header_row(scroll, ["", "#", "Kodek", "Jazyk", "Kanály", "Bitrate", "Název"])
            for s in probe["audio_streams"]:
                var = ctk.BooleanVar(value=s["index"] in init_audio_sel)
                self.audio_vars[s["index"]] = var
                self._stream_row(scroll, var, s, is_audio=True)
        else:
            ctk.CTkLabel(scroll, text="Žádné audio stopy nenalezeny.", text_color="gray").pack(anchor="w", pady=4)

        if probe["sub_streams"]:
            ctk.CTkLabel(scroll, text="💬 Titulky", font=("Arial", 12, "bold")).pack(anchor="w", pady=(14, 2))
            self._header_row(scroll, ["", "#", "Kodek", "Jazyk", "", "", "Název"])
            any_pgs = False
            for s in probe["sub_streams"]:
                var = ctk.BooleanVar(value=s["index"] in init_sub_sel)
                self.sub_vars[s["index"]] = var
                self._stream_row(scroll, var, s, is_audio=False)
                if s["is_pgs"]:
                    any_pgs = True
            if any_pgs:
                ctk.CTkLabel(scroll, text="⚠ PGS titulky jsou obrázkové (bitmapa) – nelze je textově upravovat, "
                                           "kopírují se tak jak jsou a v přehrávači musí být podporované.",
                             text_color="#e0a030", font=("Arial", 10, "italic"), wraplength=740, justify="left").pack(anchor="w", pady=(2, 4))
        else:
            ctk.CTkLabel(scroll, text="Žádné titulkové stopy nenalezeny.", text_color="gray").pack(anchor="w", pady=4)

        # --- NFO metadata (jen pokud je generování NFO zapnuté v Nastavení) ---
        self.nfo_enabled = cfg.get("generate_nfo", True)
        self.entry_title = self.entry_year = self.text_plot = self.genre_var = self.genre_menu = None
        self.nfo_file_var = ctk.BooleanVar(value=(initial or {}).get("generate_nfo", True))

        if self.nfo_enabled:
            nfo_frame = ctk.CTkFrame(self)
            nfo_frame.pack(fill="x", padx=16, pady=(6, 6))

            nfo_header = ctk.CTkFrame(nfo_frame, fg_color="transparent")
            nfo_header.grid(row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 4))
            ctk.CTkLabel(nfo_header, text="📄 NFO metadata", font=("Arial", 12, "bold")).pack(side="left")
            ctk.CTkCheckBox(nfo_header, text="Generovat NFO pro tento soubor", variable=self.nfo_file_var,
                             command=self._toggle_nfo_fields).pack(side="left", padx=(20, 0))

            # init_nfo může být "prázdný" dict, pokud byla položka přidána s vypnutým NFO - v tom
            # případě radši dopočítat rozumné výchozí hodnoty než zobrazit prázdná pole.
            has_init_nfo = init_nfo and any(init_nfo.get(k) for k in ("title", "year", "genre", "plot"))
            nfo = init_nfo if has_init_nfo else default_nfo(filename)
            ctk.CTkLabel(nfo_frame, text="Titul:").grid(row=1, column=0, sticky="w", padx=(10, 4), pady=4)
            self.entry_title = ctk.CTkEntry(nfo_frame, width=260)
            self.entry_title.insert(0, nfo.get("title", ""))
            self.entry_title.grid(row=1, column=1, sticky="w", padx=(0, 20), pady=4)

            ctk.CTkLabel(nfo_frame, text="Rok:").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=4)
            self.entry_year = ctk.CTkEntry(nfo_frame, width=80)
            self.entry_year.insert(0, nfo.get("year", ""))
            self.entry_year.grid(row=1, column=3, sticky="w", pady=4)

            ctk.CTkLabel(nfo_frame, text="Žánr:").grid(row=2, column=0, sticky="w", padx=(10, 4), pady=4)
            genres = cfg.get("nfo_genres") or ["Neurčeno"]
            default_genre = nfo.get("genre") or cfg.get("default_genre", genres[0])
            self.genre_var = ctk.StringVar(value=default_genre if default_genre in genres else genres[0])
            self.genre_menu = ctk.CTkOptionMenu(nfo_frame, values=genres, variable=self.genre_var, width=180)
            self.genre_menu.grid(row=2, column=1, sticky="w", pady=4)

            ctk.CTkLabel(nfo_frame, text="Plot:").grid(row=3, column=0, sticky="nw", padx=(10, 4), pady=(4, 10))
            self.text_plot = ctk.CTkTextbox(nfo_frame, width=560, height=50)
            self.text_plot.insert("1.0", nfo.get("plot", ""))
            self.text_plot.grid(row=3, column=1, columnspan=3, sticky="w", padx=(0, 10), pady=(4, 10))

            self._toggle_nfo_fields()

        # --- Tlačítka ---
        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkButton(btns, text="Výchozí výběr", width=140, fg_color="#555555", command=self.reset_defaults).pack(side="left")
        cancel_text = "Zrušit úpravu" if mode == "edit" else "Zrušit (nepřidávat)"
        confirm_text = "Uložit změny" if mode == "edit" else "Potvrdit výběr"
        ctk.CTkButton(btns, text=cancel_text, width=160, fg_color="#a11a1a", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btns, text=confirm_text, width=160, fg_color="#28a745", command=self._confirm).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._cancel)

    def _header_row(self, parent, labels):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        widths = [30, 34, 90, 60, 70, 90, 1]
        for lbl, w in zip(labels, widths):
            ctk.CTkLabel(row, text=lbl, font=("Arial", 10, "bold"), text_color="gray", width=w, anchor="w").pack(side="left", padx=2)

    def _stream_row(self, parent, var, s, is_audio):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=1)
        ctk.CTkCheckBox(row, text="", variable=var, width=20, checkbox_width=18, checkbox_height=18).pack(side="left", padx=2)
        ctk.CTkLabel(row, text=s["index"], width=34, anchor="w").pack(side="left", padx=2)
        codec_text = s["codec_name"] + (" ⚠PGS" if s.get("is_pgs") else "")
        ctk.CTkLabel(row, text=codec_text, width=90, anchor="w",
                     text_color="#e0a030" if s.get("is_pgs") else None).pack(side="left", padx=2)
        ctk.CTkLabel(row, text=s["lang"], width=60, anchor="w").pack(side="left", padx=2)
        if is_audio:
            ch = s.get("channels")
            ctk.CTkLabel(row, text=(f"{ch}ch" if ch else "?"), width=70, anchor="w").pack(side="left", padx=2)
            ctk.CTkLabel(row, text=fmt_bitrate(s.get("bitrate")), width=90, anchor="w").pack(side="left", padx=2)
        else:
            ctk.CTkLabel(row, text="", width=70).pack(side="left", padx=2)
            ctk.CTkLabel(row, text="", width=90).pack(side="left", padx=2)
        ctk.CTkLabel(row, text=s.get("title") or "", anchor="w").pack(side="left", padx=2, fill="x", expand=True)

    def _update_crop_label(self):
        if not self.crop:
            self.crop_label.configure(text="bez ořezu", text_color="gray")
            self.btn_crop_clear.configure(state="disabled")
            return
        text = crop_describe(self.crop)
        color = None
        codec = (self.probe["video"].get("codec_name") or "").lower()
        if self.copy_video_var.get():
            if codec in CROP_COPY_CODECS:
                text += " – bez překódování (zápis do streamu)"
            else:
                text += f" – při kopírování videa se u {codec} NEPROVEDE"
                color = "#e0a030"
        self.crop_label.configure(text=text, text_color=color or ("gray10", "gray90"))
        self.btn_crop_clear.configure(state="normal")

    def _open_crop(self):
        dlg = CropDialog(self, self.path, self.probe["video"], self.probe["duration"], self.cfg, initial=self.crop)
        self.wait_window(dlg)
        try:
            self.grab_set()   # grab se při zavření podřízeného dialogu vrací sem
        except Exception:
            pass
        if dlg.result is None:
            return
        self.crop = dlg.result or None
        self._update_crop_label()

    def _clear_crop(self):
        self.crop = None
        self._update_crop_label()

    def reset_defaults(self):
        for idx, var in self.audio_vars.items():
            var.set(idx in self.default_audio_sel)
        for idx, var in self.sub_vars.items():
            var.set(idx in self.default_sub_sel)

    def _toggle_nfo_fields(self):
        state = "normal" if self.nfo_file_var.get() else "disabled"
        for w in (self.entry_title, self.entry_year, self.text_plot, self.genre_menu):
            if w is not None:
                w.configure(state=state)

    def _cancel(self):
        self.result = None
        self.grab_release()
        self.destroy()

    def _confirm(self):
        sel_audio = [idx for idx, var in self.audio_vars.items() if var.get()]
        sel_subs = [idx for idx, var in self.sub_vars.items() if var.get()]
        if self.nfo_enabled:
            nfo = {
                "title": self.entry_title.get().strip(),
                "year": self.entry_year.get().strip(),
                "genre": self.genre_var.get(),
                "plot": self.text_plot.get("1.0", "end").strip()
            }
            nfo_for_file = self.nfo_file_var.get()
        else:
            nfo = {"title": "", "year": "", "genre": "", "plot": ""}
            nfo_for_file = False
        self.result = {
            "selected_audio": sel_audio,
            "selected_subs": sel_subs,
            "nfo": nfo,
            "generate_nfo_for_file": nfo_for_file,
            "copy_video": self.copy_video_var.get(),
            "crop": self.crop,
        }
        self.grab_release()
        self.destroy()


# ==========================================================================
#  DIALOG: OŘEZ ČERNÝCH OKRAJŮ
# ==========================================================================

class CropDialog(ctk.CTkToplevel):
    """Náhled snímku s vyznačeným ořezem, automatická detekce a ruční doladění po stranách.
    result: None = zrušeno, {} = bez ořezu, dict = ořez (viz normalize_crop)."""
    PREVIEW_W = 640
    SIDES = (("top", "Nahoře"), ("bottom", "Dole"), ("left", "Vlevo"), ("right", "Vpravo"))

    def __init__(self, parent, path, video, duration, cfg, initial=None):
        super().__init__(parent)
        self.title(f"Ořez černých okrajů – {os.path.basename(path)}")
        self.transient(parent)
        self.resizable(False, False)
        self.update_idletasks()
        self.grab_set()

        self.path, self.video, self.cfg = path, video, cfg
        self.src_w, self.src_h = int(video.get("width") or 0), int(video.get("height") or 0)
        self.result = None
        self.times = crop_sample_times(duration)
        self.pos = len(self.times) // 2
        self._images = {}          # index vzorku -> PhotoImage
        self._loading = set()
        self._closed = False
        self._detecting = False
        self._setting = False
        self._tmp_dir = tempfile.mkdtemp(prefix="ffmcrop_")
        self._env = App._ffmpeg_env()

        ctk.CTkLabel(self, text=f"Zdroj: {self.src_w}×{self.src_h} ({video.get('codec_name')})",
                     font=("Arial", 11), text_color="gray").pack(anchor="w", padx=16, pady=(12, 4))

        self.canvas = Canvas(self, width=self.PREVIEW_W, height=360, bg="black", highlightthickness=0)
        self.canvas.pack(padx=16)

        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(fill="x", padx=16, pady=(6, 0))
        ctk.CTkButton(nav, text="◀", width=40, command=lambda: self._step(-1)).pack(side="left")
        self.nav_label = ctk.CTkLabel(nav, text="", width=200)
        self.nav_label.pack(side="left", padx=8)
        ctk.CTkButton(nav, text="▶", width=40, command=lambda: self._step(1)).pack(side="left")
        self.result_label = ctk.CTkLabel(nav, text="", font=("Arial", 12, "bold"))
        self.result_label.pack(side="right")

        vals = ctk.CTkFrame(self, fg_color="transparent")
        vals.pack(fill="x", padx=16, pady=(8, 0))
        ctk.CTkLabel(vals, text="Ubrat pixelů:").pack(side="left", padx=(0, 8))
        self.vars = {}
        for key, label in self.SIDES:
            ctk.CTkLabel(vals, text=label).pack(side="left", padx=(8, 4))
            var = StringVar(value="0")
            var.trace_add("write", lambda *_: self._on_values_changed())
            ctk.CTkEntry(vals, textvariable=var, width=60).pack(side="left")
            self.vars[key] = var

        self.status = ctk.CTkLabel(self, text="", font=("Arial", 11), text_color="gray", anchor="w")
        self.status.pack(fill="x", padx=16, pady=(6, 0))

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=(8, 14))
        self.btn_detect = ctk.CTkButton(btns, text="Detekovat automaticky", width=170, command=self._start_detect)
        self.btn_detect.pack(side="left")
        ctk.CTkButton(btns, text="Bez ořezu", width=100, fg_color="#555555", command=self._no_crop).pack(side="left", padx=(8, 0))
        ctk.CTkButton(btns, text="Zrušit", width=100, fg_color="#a11a1a", command=self._cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btns, text="Použít", width=120, fg_color="#28a745", command=self._apply).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Destroy>", self._on_destroy, add="+")

        if initial and (initial.get("src_w"), initial.get("src_h")) == (self.src_w, self.src_h):
            self._set_values(initial)
        else:
            self._start_detect()
        self._show_frame()

    # --- vlákna -> UI ---
    def _ui(self, fn):
        if not self._closed:
            try:
                self.after(0, fn)
            except Exception:
                pass

    def _on_destroy(self, event):
        if event.widget is self:
            self._closed = True
            shutil.rmtree(self._tmp_dir, ignore_errors=True)

    # --- detekce ---
    def _start_detect(self):
        if self._detecting:
            return
        self._detecting = True
        self.btn_detect.configure(state="disabled")
        self.status.configure(text=f"Hledám černé okraje… 0/{len(self.times)}")
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self):
        boxes = []
        for i, t in enumerate(self.times):
            if self._closed:
                return
            boxes.append(detect_crop_at(self.cfg["ffmpeg_path"], self.path, t, env=self._env))
            self._ui(lambda n=i + 1: self.status.configure(text=f"Hledám černé okraje… {n}/{len(self.times)}"))
        crop, used = merge_crop_boxes(boxes, self.src_w, self.src_h)
        self._ui(lambda: self._detect_done(crop, used))

    def _detect_done(self, crop, used):
        self._detecting = False
        self.btn_detect.configure(state="normal")
        if used == 0:
            self.status.configure(text="Okraje se nepodařilo zjistit (žádný použitelný vzorek) - nastav je ručně.")
            return
        if crop is None:
            self._set_values({"top": 0, "bottom": 0, "left": 0, "right": 0})
            self.status.configure(text=f"Černé okraje nenalezeny (vzorků: {used}/{len(self.times)}).")
        else:
            self._set_values(crop)
            self.status.configure(text=f"Nalezeno z {used}/{len(self.times)} vzorků - zkontroluj na více snímcích (◀ ▶).")

    # --- hodnoty ---
    def _set_values(self, crop):
        self._setting = True
        for key, _ in self.SIDES:
            self.vars[key].set(str(int(crop.get(key, 0))))
        self._setting = False
        self._on_values_changed()

    def _current_crop(self):
        try:
            raw = {k: int(self.vars[k].get() or 0) for k, _ in self.SIDES}
        except ValueError:
            return None, "neplatné číslo"
        if any(v < 0 for v in raw.values()):
            return None, "záporná hodnota"
        crop = normalize_crop(dict(raw, src_w=self.src_w, src_h=self.src_h))
        if crop is None and any(raw.values()):
            return None, "ořez je příliš velký"
        return crop, None

    def _on_values_changed(self):
        if self._setting:
            return
        crop, err = self._current_crop()
        if err:
            self.result_label.configure(text=f"⚠ {err}", text_color="#e0a030")
        elif crop:
            w, h = crop_size(crop)
            self.result_label.configure(text=f"Výsledek: {w}×{h}", text_color=("gray10", "gray90"))
        else:
            self.result_label.configure(text="Bez ořezu", text_color="gray")
        self._redraw()

    # --- náhled ---
    def _step(self, d):
        self.pos = (self.pos + d) % len(self.times)
        self._show_frame()

    def _show_frame(self):
        i = self.pos
        self.nav_label.configure(text=f"Snímek {i + 1}/{len(self.times)} – {fmt_duration(self.times[i])}")
        if i in self._images:
            self._redraw()
            return
        self.canvas.delete("all")
        self.canvas.create_text(self.PREVIEW_W // 2, 180, text="Načítám snímek…", fill="gray")
        if i in self._loading:
            return
        self._loading.add(i)
        out_png = os.path.join(self._tmp_dir, f"frame{i}.png")

        def worker():
            ok = extract_frame_png(self.cfg["ffmpeg_path"], self.path, self.times[i], out_png,
                                   width=self.PREVIEW_W, env=self._env)
            self._ui(lambda: self._frame_loaded(i, out_png if ok else None))
        threading.Thread(target=worker, daemon=True).start()

    def _frame_loaded(self, i, png):
        self._loading.discard(i)
        if png:
            try:
                self._images[i] = PhotoImage(master=self, file=png)   # Tk 8.6 čte PNG sám
            except Exception:
                png = None
        if i == self.pos:
            if png:
                self._redraw()
            else:
                self.canvas.delete("all")
                self.canvas.create_text(self.PREVIEW_W // 2, 180, text="Snímek se nepodařilo načíst", fill="#e0a030")

    def _redraw(self):
        img = self._images.get(self.pos)
        if img is None or not self.src_w or not self.src_h:
            return
        pw, ph = img.width(), img.height()
        self.canvas.configure(width=pw, height=ph)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=img)
        crop, _ = self._current_crop()
        if not crop:
            return
        sx, sy = pw / self.src_w, ph / self.src_h
        x0, y0 = crop["left"] * sx, crop["top"] * sy
        x1, y1 = pw - crop["right"] * sx, ph - crop["bottom"] * sy
        for box in ((0, 0, pw, y0), (0, y1, pw, ph), (0, y0, x0, y1), (x1, y0, pw, y1)):
            if box[2] > box[0] and box[3] > box[1]:
                self.canvas.create_rectangle(*box, fill="#ff2020", stipple="gray25", width=0)
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#ffd000", width=2)

    # --- tlačítka ---
    def _apply(self):
        crop, err = self._current_crop()
        if err:
            messagebox.showwarning("Ořez", f"Ořez nejde použít: {err}.", parent=self)
            return
        self.result = crop or {}
        self._close()

    def _no_crop(self):
        self.result = {}
        self._close()

    def _cancel(self):
        self.result = None
        self._close()

    def _close(self):
        self._closed = True
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


# ==========================================================================
#  OKNO: STŘIH (jeden soubor)
# ==========================================================================

class CutWindow(ctk.CTkToplevel):
    """Střih jednoho souboru: náhled s krokováním po snímku, značky začátek/konec, seznam úseků.
    Výstup: originální formát (smart cut, bez překódování), do fronty, do fronty a spustit, .ffm.
    S queue_item upravuje úseky položky, která už ve frontě je."""
    PREVIEW_W, PREVIEW_H, TL_H, WAVE_H = 768, 432, 34, 78
    WAVE_SPAN = 8.0       # okno průběhu zvuku: ±4 s kolem aktuálního snímku
    WAVE_CHUNK = 40.0     # kolik zvuku se načte najednou (krokování pak nemusí nic načítat)
    OUT_ORIGINAL, OUT_QUEUE, OUT_QUEUE_RUN, OUT_FFM = "orig", "queue", "queue_run", "ffm"

    def __init__(self, app, path=None, queue_item=None):
        super().__init__(app)
        self.app, self.cfg = app, app.cfg
        self.queue_item = queue_item
        self.title("Střih")
        self.geometry("1140x960")
        self.minsize(1000, 860)
        self.transient(app)
        self.update_idletasks()
        self.grab_set()
        self.env = App._ffmpeg_env()

        self.path = None
        self.info = None
        self.index = None
        self.duration = 0.0
        self.fd = 0.04
        self.pos = 0.0
        self.mark_in = None
        self.segments = []          # [{"start", "end"}] v režimu, který je zvolený (ponechat / vystřihnout)
        self.running = False
        self.stop_flag = False
        self.proc = None
        self._closed = False
        self._want = None
        self._shown = None
        self._preview_busy = False
        self._img = None
        self._index_gen = 0
        self._wave = None          # (t0, pcm) načtený kus zvuku
        self._wave_want = None
        self._wave_busy = False
        self._audio_index = None
        self.ffplay = find_ffplay(self.cfg["ffmpeg_path"])
        self._play_proc = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Destroy>", lambda e: setattr(self, "_closed", True) if e.widget is self else None, add="+")
        for seq, fn in (("<Left>", lambda: self._step(-1)), ("<Right>", lambda: self._step(1)),
                        ("<Shift-Left>", lambda: self._jump(-1)), ("<Shift-Right>", lambda: self._jump(1)),
                        ("<Control-Left>", lambda: self._jump(-10)), ("<Control-Right>", lambda: self._jump(10)),
                        ("<Up>", lambda: self._jump(60)), ("<Down>", lambda: self._jump(-60)),
                        ("<Prior>", lambda: self._key(-1)), ("<Next>", lambda: self._key(1)),
                        ("<Home>", lambda: self._goto(0)), ("<End>", lambda: self._goto(self.duration)),
                        ("<i>", self._set_in), ("<o>", self._set_out), ("<Delete>", self._delete_segment),
                        ("<space>", self._play)):
            self.bind(seq, lambda e, f=fn: None if self._typing(e) else f())
        if queue_item:
            self._open(queue_item["path"], initial=queue_item.get("segments") or [])
        elif path:
            self._open(path)

    # ---------------- UI ----------------
    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        self.btn_open = ctk.CTkButton(top, text="Otevřít soubor…", width=140, command=self._choose_file)
        self.btn_open.pack(side="left")
        if self.queue_item:
            self.btn_open.configure(state="disabled")
        self.file_label = ctk.CTkLabel(top, text="Žádný soubor", font=("Arial", 13, "bold"), anchor="w")
        self.file_label.pack(side="left", padx=10, fill="x", expand=True)
        self.index_label = ctk.CTkLabel(top, text="", font=("Arial", 11), text_color="gray")
        self.index_label.pack(side="right")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12)
        left = ctk.CTkFrame(body, fg_color="transparent")
        left.pack(side="left", fill="y")
        right = ctk.CTkFrame(body)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        self.preview = Canvas(left, width=self.PREVIEW_W, height=self.PREVIEW_H, bg="black", highlightthickness=0)
        self.preview.pack()
        # Průběh zvuku kolem aktuálního snímku (±4 s) - ať je vidět, kde začíná zvuk reklamy/filmu,
        # i když je obraz ještě černý. Kliknutím se skočí přesně na dané místo.
        self.wave = Canvas(left, width=self.PREVIEW_W, height=self.WAVE_H, bg="#151515", highlightthickness=0,
                           cursor="crosshair")
        self.wave.pack(pady=(4, 0))
        self.wave.bind("<Button-1>", self._wave_click)
        arow = ctk.CTkFrame(left, fg_color="transparent")
        arow.pack(fill="x", pady=(2, 0))
        ctk.CTkLabel(arow, text="Zvuk:", font=("Arial", 11)).pack(side="left")
        self.audio_var = StringVar(value="")
        self.audio_menu = ctk.CTkOptionMenu(arow, variable=self.audio_var, values=["-"], width=260, height=24,
                                            command=lambda _: self._audio_changed())
        self.audio_menu.pack(side="left", padx=6)
        self.btn_play = ctk.CTkButton(arow, text="🔊 Přehrát 2 s (mezerník)", width=190, height=24, command=self._play,
                                      state="normal" if self.ffplay else "disabled")
        self.btn_play.pack(side="left", padx=6)
        if not self.ffplay:
            ctk.CTkLabel(arow, text="(ffplay nenalezen - poslech nejde)", font=("Arial", 10),
                         text_color="gray").pack(side="left")
        self.timeline = Canvas(left, width=self.PREVIEW_W, height=self.TL_H, bg="#202020", highlightthickness=0,
                               cursor="hand2")
        self.timeline.pack(pady=(6, 0))
        self.timeline.bind("<Button-1>", self._tl_click)
        self.timeline.bind("<B1-Motion>", self._tl_click)

        posrow = ctk.CTkFrame(left, fg_color="transparent")
        posrow.pack(fill="x", pady=(6, 0))
        self.pos_label = ctk.CTkLabel(posrow, text="", font=("Consolas", 14, "bold"), anchor="w")
        self.pos_label.pack(side="left")
        self.goto_entry = ctk.CTkEntry(posrow, width=120, placeholder_text="h:mm:ss.mmm")
        self.goto_entry.pack(side="right")
        self.goto_entry.bind("<Return>", lambda e: self._goto_entry())
        ctk.CTkButton(posrow, text="Skok na", width=70, command=self._goto_entry).pack(side="right", padx=4)

        nav = ctk.CTkFrame(left, fg_color="transparent")
        nav.pack(fill="x", pady=(6, 0))
        for text, fn in (("|◀K", lambda: self._key(-1)), ("◀ 1 min", lambda: self._jump(-60)),
                         ("◀ 10 s", lambda: self._jump(-10)), ("◀ 1 s", lambda: self._jump(-1)),
                         ("◀ snímek", lambda: self._step(-1)), ("snímek ▶", lambda: self._step(1)),
                         ("1 s ▶", lambda: self._jump(1)), ("10 s ▶", lambda: self._jump(10)),
                         ("1 min ▶", lambda: self._jump(60)), ("K▶|", lambda: self._key(1))):
            ctk.CTkButton(nav, text=text, width=70, command=fn).pack(side="left", padx=2)

        marks = ctk.CTkFrame(left, fg_color="transparent")
        marks.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(marks, text="[ Začátek (I)", width=120, fg_color="#1f6f8b", command=self._set_in).pack(side="left")
        ctk.CTkButton(marks, text="Konec ] (O)", width=120, fg_color="#1f6f8b", command=self._set_out).pack(side="left", padx=6)
        self.in_label = ctk.CTkLabel(marks, text="", font=("Arial", 11))
        self.in_label.pack(side="left", padx=6)
        self.mode_var = StringVar(value="cut")
        ctk.CTkRadioButton(marks, text="úseky = vystřihnout (reklamy)", variable=self.mode_var, value="cut",
                           command=self._mode_changed).pack(side="right")
        ctk.CTkRadioButton(marks, text="úseky = ponechat", variable=self.mode_var, value="keep",
                           command=self._mode_changed).pack(side="right", padx=8)
        ctk.CTkLabel(left, text="Klávesy: ←/→ snímek · Shift ±1 s · Ctrl ±10 s · ↑/↓ ±1 min · PgUp/PgDn klíčový "
                                "snímek · I/O začátek/konec · mezerník poslech · Delete smaže úsek",
                     font=("Arial", 10), text_color="gray").pack(anchor="w", pady=(4, 0))

        ctk.CTkLabel(right, text="Úseky", font=("Arial", 13, "bold")).pack(anchor="w", padx=10, pady=(8, 2))
        self.seg_list = Listbox(right, bg="#2b2b2b", fg="white", font=("Consolas", 10), borderwidth=0,
                                highlightthickness=0, selectmode=SINGLE, activestyle="none")
        self.seg_list.pack(fill="both", expand=True, padx=10)
        self.seg_list.bind("<Double-Button-1>", lambda e: self._seg_jump(0))
        sb = ctk.CTkFrame(right, fg_color="transparent")
        sb.pack(fill="x", padx=10, pady=6)
        ctk.CTkButton(sb, text="→ začátek", width=90, command=lambda: self._seg_jump(0)).pack(side="left")
        ctk.CTkButton(sb, text="→ konec", width=90, command=lambda: self._seg_jump(1)).pack(side="left", padx=4)
        ctk.CTkButton(sb, text="Smazat", width=80, fg_color="#a11a1a", command=self._delete_segment).pack(side="right")
        self.result_label = ctk.CTkLabel(right, text="", font=("Arial", 12, "bold"), justify="left", anchor="w")
        self.result_label.pack(fill="x", padx=10, pady=(0, 8))

        out = ctk.CTkFrame(self)
        out.pack(fill="x", padx=12, pady=(8, 4))
        self.out_var = StringVar(value=self.OUT_ORIGINAL)
        if self.queue_item:
            ctk.CTkLabel(out, text="Úseky se uloží do položky ve frontě (zpracují se při jejím kódování).",
                         font=("Arial", 12)).pack(side="left", padx=10, pady=8)
        else:
            col = ctk.CTkFrame(out, fg_color="transparent")
            col.pack(side="left", padx=10, pady=6)
            ctk.CTkRadioButton(col, text="Originální formát – bez překódování (smart cut)",
                               variable=self.out_var, value=self.OUT_ORIGINAL, command=self._out_changed).grid(row=0, column=0, sticky="w")
            ctk.CTkRadioButton(col, text="Do fronty – kódování podle nastavení fronty",
                               variable=self.out_var, value=self.OUT_QUEUE, command=self._out_changed).grid(row=1, column=0, sticky="w", pady=2)
            ctk.CTkRadioButton(col, text="MKV s překódováním – přidat do fronty a spustit",
                               variable=self.out_var, value=self.OUT_QUEUE_RUN, command=self._out_changed).grid(row=0, column=1, sticky="w", padx=(30, 0))
            ctk.CTkRadioButton(col, text="Uložit jako .ffm (příprava pro frontu)",
                               variable=self.out_var, value=self.OUT_FFM, command=self._out_changed).grid(row=1, column=1, sticky="w", padx=(30, 0), pady=2)
            pathrow = ctk.CTkFrame(out, fg_color="transparent")
            pathrow.pack(side="left", fill="x", expand=True, padx=10)
            ctk.CTkLabel(pathrow, text="Výstup:").pack(side="left")
            self.out_entry = ctk.CTkEntry(pathrow)
            self.out_entry.pack(side="left", fill="x", expand=True, padx=4)
            self.btn_out = ctk.CTkButton(pathrow, text="…", width=30, command=self._choose_out)
            self.btn_out.pack(side="left")

        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=12, pady=(4, 10))
        self.btn_run = ctk.CTkButton(bot, text="Uložit úseky" if self.queue_item else "Provést", width=140, height=36,
                                     fg_color="#28a745", font=("Arial", 13, "bold"), command=self._execute)
        self.btn_run.pack(side="left")
        self.btn_stop = ctk.CTkButton(bot, text="STOP", width=90, height=36, fg_color="#a11a1a", state="disabled",
                                      command=self._stop)
        self.btn_stop.pack(side="left", padx=6)
        ctk.CTkButton(bot, text="Zavřít", width=90, height=36, fg_color="#555555", command=self._close).pack(side="right")
        prog = ctk.CTkFrame(bot, fg_color="transparent")
        prog.pack(side="left", fill="x", expand=True, padx=10)
        self.status = ctk.CTkLabel(prog, text="", font=("Arial", 11), anchor="w")
        self.status.pack(fill="x")
        self.progress = ctk.CTkProgressBar(prog, height=10)
        self.progress.pack(fill="x")
        self.progress.set(0)
        self.logbox = ctk.CTkTextbox(self, height=70, font=("Consolas", 10))
        self.logbox.pack(fill="x", padx=12, pady=(0, 10))
        self.logbox._textbox.tag_configure("error", foreground="#ff4444")
        self._update_all()

    @staticmethod
    def _typing(event):
        return event.widget.winfo_class() in ("Entry", "Text", "TEntry")

    def _ui(self, fn):
        if not self._closed:
            try:
                self.after(0, fn)
            except Exception:
                pass

    def log(self, text, error=False, debug=False):
        if debug and not self.app.debug_var.get():
            return
        def do():
            self.logbox.insert("end", text.strip() + "\n", "error" if error else None)
            self.logbox.see("end")
        self._ui(do)

    # ---------------- soubor + index ----------------
    def _choose_file(self):
        f = filedialog.askopenfilename(parent=self, filetypes=[("Video soubory", "*.mkv *.mp4 *.ts *.m2ts *.mts *.mov *.m4v *.avi"),
                                                               ("Všechny soubory", "*.*")])
        if f:
            self._open(os.path.normpath(f))

    def _open(self, path, initial=None):
        info, err = probe_cut_info(self.cfg["ffprobe_path"], path)
        if info is None:
            messagebox.showerror("Střih", f"Soubor nejde otevřít: {err}", parent=self)
            return
        self.path, self.info = path, info
        self.duration = info["duration"]
        fr = str(info["video"].get("r_frame_rate") or "25/1")
        try:
            n, d = fr.split("/")
            self.fd = float(d) / float(n) if float(n) else 0.04
        except ValueError:
            self.fd = 0.04
        self.index = None
        self.pos = 0.0
        self.mark_in = None
        self._wave = None
        audios = [st for st in info["streams"] if st.get("codec_type") == "audio"]
        self._audio_map = {}
        for st in audios:
            tags = st.get("tags") or {}
            label = f"#{st.get('index')} {tags.get('language', 'und')} · {st.get('codec_name')}" + \
                    (f" · {tags['title']}" if tags.get("title") else "")
            self._audio_map[label] = st.get("index")
        labels = list(self._audio_map) or ["(žádný zvuk)"]
        self.audio_menu.configure(values=labels)
        self.audio_var.set(labels[0])
        self._audio_index = self._audio_map.get(labels[0])
        if initial:
            self.mode_var.set("keep")
            self.segments = normalize_segments(initial, self.duration)
        else:
            self.segments = []
        v = info["video"]
        self.file_label.configure(text=f"{os.path.basename(path)}   ·   {v.get('codec_name')} {v.get('width')}x{v.get('height')}"
                                       f"   ·   {fmt_tc(self.duration)}")
        if not self.queue_item:
            base, ext = os.path.splitext(os.path.basename(path))
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, os.path.join(self.cfg["output_dir"], fit_filename(base, "_strih" + ext)))
            self._out_changed()
        self._index_gen += 1
        gen = self._index_gen
        self.index_label.configure(text="Indexuji snímky… 0 %")
        threading.Thread(target=self._index_worker, args=(gen,), daemon=True).start()
        self._update_all()
        self._request_frame()

    def _index_worker(self, gen):
        def progress(p):
            self._ui(lambda: self.index_label.configure(text=f"Indexuji snímky… {int(p * 100)} %"))
        idx, err = build_frame_index(self.cfg["ffprobe_path"], self.path, self.info["start_time"], self.duration,
                                     progress=progress, stop=lambda: self._closed or gen != self._index_gen, env=self.env)
        def done():
            if gen != self._index_gen:
                return
            if idx is None:
                self.index_label.configure(text=f"Index selhal: {err}", text_color="#ff4444")
                return
            self.index = idx
            self.fd = idx.frame_dur
            self.duration = idx.end
            self.segments = normalize_segments([{"start": idx.snap(sg["start"]),
                                                 "end": sg["end"] if sg["end"] >= idx.end - 1e-6 else idx.snap(sg["end"])}
                                                for sg in self.segments], idx.end)
            sc = SmartCutter(self.cfg["ffmpeg_path"], self.cfg["ffprobe_path"], self.path, self.info, idx,
                             None, None, env=self.env)
            ok, why = sc.supported()
            self.index_label.configure(text=f"{len(idx)} snímků, {len(idx.keys)} klíčových"
                                            + ("" if ok else f" · bez překódování nejde: {why}"),
                                       text_color="gray" if ok else "#e0a030")
            self._goto(self.pos)
        self._ui(done)

    # ---------------- pohyb ----------------
    def _goto(self, t):
        if not self.path:
            return
        t = min(max(float(t), 0.0), max(self.duration - self.fd, 0.0))
        self.pos = self.index.snap(t) if self.index else t
        self._update_all()
        self._request_frame()

    def _step(self, n):
        if self.index:
            self._goto(self.index.step(self.pos, n))
        else:
            self._goto(self.pos + n * self.fd)

    def _jump(self, sec):
        self._goto(self.pos + sec)

    def _key(self, direction):
        if not self.index:
            return
        k = self.index.next_key(self.pos) if direction > 0 else self.index.prev_key(self.pos)
        if k is not None:
            self._goto(k)

    def _goto_entry(self):
        t = parse_tc(self.goto_entry.get())
        if t is None:
            messagebox.showwarning("Skok", "Zadej čas jako h:mm:ss.mmm, mm:ss nebo sekundy.", parent=self)
            return
        self._goto(t)

    # ---------------- zvuk ----------------
    def _audio_changed(self):
        self._audio_index = self._audio_map.get(self.audio_var.get())
        self._wave = None
        self._draw_wave()

    def _wave_window(self):
        return self.pos - self.WAVE_SPAN / 2, self.pos + self.WAVE_SPAN / 2

    def _wave_click(self, event):
        if self.path:
            t0, _ = self._wave_window()
            self._goto(t0 + min(max(event.x, 0), self.PREVIEW_W) / self.PREVIEW_W * self.WAVE_SPAN)

    def _draw_wave(self):
        cv = self.wave
        cv.delete("all")
        if not self.path or self._audio_index is None:
            return
        w, h = self.PREVIEW_W, self.WAVE_H
        t0, t1 = self._wave_window()
        x = lambda t: (t - t0) / self.WAVE_SPAN * w
        # podklad: ponechané (zelené) / vystřižené (červené) části
        for sg in self.keep_segments() if self.segments else []:
            if sg["end"] > t0 and sg["start"] < t1:
                cv.create_rectangle(max(x(sg["start"]), 0), 0, min(x(sg["end"]), w), h, fill="#13341c", width=0)
        for sg in self.segments:
            for t in (sg["start"], sg["end"]):
                if t0 <= t <= t1:
                    cv.create_line(x(t), 0, x(t), h, fill="#b0b0b0", dash=(3, 2))
        have = self._wave
        # pokrytí podle VYŽÁDANÉHO rozsahu (na konci souboru je zvuku méně - jinak by se načítalo pořád dokola)
        if have and have[0] <= max(t0, 0) + 1e-6 and have[2] >= min(t1, self.duration) - 1e-6:
            pcm, base, peak = have[1], have[0], have[3]
            mid = h / 2
            # Výška: normalizace na nejhlasitější místo načteného kusu + odmocninová komprese, ať je
            # vidět i tichý nástup zvuku (TV zvuk bývá hluboko pod plnou úrovní).
            amp = lambda v: (1 if v >= 0 else -1) * (abs(v) / peak) ** 0.5 * 0.96
            per = self.WAVE_SPAN * WAVE_RATE / w
            for px in range(w):
                a = int(((t0 - base) * WAVE_RATE) + px * per)
                b = int(a + per) + 1
                if b <= 0 or a >= len(pcm):
                    continue
                chunk = pcm[max(a, 0):min(b, len(pcm))]
                if chunk:
                    lo, hi = amp(min(chunk)), amp(max(chunk))
                    cv.create_line(px, mid - hi * mid, px, mid - lo * mid + 1, fill="#4fa3ff")
        else:
            cv.create_text(w // 2, h // 2, text="načítám zvuk…", fill="gray")
            self._request_wave()
        if self.mark_in is not None and t0 <= self.mark_in <= t1:
            cv.create_line(x(self.mark_in), 0, x(self.mark_in), h, fill="#30c0ff", width=2)
        cv.create_line(w / 2, 0, w / 2, h, fill="#ffd000", width=1)
        cv.create_text(4, 2, text=f"-{self.WAVE_SPAN / 2:g} s", anchor="nw", fill="gray", font=("Arial", 8))
        cv.create_text(w - 4, 2, text=f"+{self.WAVE_SPAN / 2:g} s", anchor="ne", fill="gray", font=("Arial", 8))

    def _request_wave(self):
        self._wave_want = (self.pos, self._audio_index)
        if not self._wave_busy:
            self._wave_busy = True
            threading.Thread(target=self._wave_worker, daemon=True).start()

    def _wave_worker(self):
        while not self._closed and self._wave_want:
            pos, aidx = self._wave_want
            self._wave_want = None
            t0 = max(pos - self.WAVE_CHUNK / 2, 0.0)
            pcm = extract_audio_chunk(self.cfg["ffmpeg_path"], self.path, t0, t0 + self.WAVE_CHUNK,
                                      self.info["start_time"], aidx, self.env)
            if aidx == self._audio_index:
                pcm = pcm if pcm is not None else array.array("h")
                peak = max(max(pcm, default=0), -min(pcm, default=0), 64)
                self._wave = (t0, pcm, t0 + self.WAVE_CHUNK, peak)
                self._ui(self._draw_wave)
        self._wave_busy = False

    def _play(self):
        """Přehraje 2 s zvuku od aktuálního snímku (ffplay bez okna)."""
        if not self.ffplay or not self.path:
            return
        if self._play_proc and self._play_proc.poll() is None:
            self._play_proc.terminate()
        cmd = [self.ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{self.pos:.3f}", "-t", "2"]
        if self._audio_index is not None:
            cmd += ["-ast", str(self._audio_index)]
        try:
            self._play_proc = subprocess.Popen(cmd + [self.path], creationflags=SUBPROCESS_FLAGS, env=self.env,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.log(f"[CHYBA] ffplay nešel spustit: {e}", error=True)

    def _tl_click(self, event):
        if self.duration > 0:
            self._goto(min(max(event.x, 0), self.PREVIEW_W) / self.PREVIEW_W * self.duration)

    # ---------------- náhled ----------------
    def _request_frame(self):
        if not self.path:
            return
        self._want = self.pos
        if not self._preview_busy:
            self._preview_busy = True
            threading.Thread(target=self._preview_worker, daemon=True).start()

    def _preview_worker(self):
        while not self._closed:
            t = self._want
            if t is None or t == self._shown:
                break
            data = extract_frame_data(self.cfg["ffmpeg_path"], self.path, t, self.info["start_time"],
                                      self.PREVIEW_W, self.env)
            self._shown = t
            if data:
                self._ui(lambda d=data: self._show(d))
        self._preview_busy = False
        if not self._closed and self._want != self._shown:
            self._ui(self._request_frame)

    def _show(self, data):
        try:
            self._img = PhotoImage(master=self, data=base64.b64encode(data).decode("ascii"))
        except Exception:
            return
        self.preview.delete("frame")
        self.preview.create_image(self.PREVIEW_W // 2, self.PREVIEW_H // 2, image=self._img, tags="frame")

    # ---------------- úseky ----------------
    def _set_in(self):
        if self.path:
            self.mark_in = self.pos
            self._update_all()

    def _set_out(self):
        if not self.path:
            return
        end = self.index.after(self.pos) if self.index else self.pos + self.fd
        if self.mark_in is None:
            messagebox.showinfo("Úsek", "Nejdřív nastav začátek úseku (I).", parent=self)
            return
        a, b = sorted((self.mark_in, end))
        if self.mark_in > self.pos:      # konec před začátkem -> začátek je tento snímek, konec po IN snímku
            a, b = self.pos, (self.index.after(self.mark_in) if self.index else self.mark_in + self.fd)
        self.segments = normalize_segments(self.segments + [{"start": a, "end": b}], self.duration)
        self.mark_in = None
        self._update_all()

    def _selected(self):
        sel = self.seg_list.curselection()
        return sel[0] if sel and sel[0] < len(self.segments) else None

    def _delete_segment(self):
        i = self._selected()
        if i is not None:
            del self.segments[i]
            self._update_all()

    def _seg_jump(self, which):
        i = self._selected()
        if i is None:
            return
        sg = self.segments[i]
        self._goto(sg["start"] if which == 0 else (self.index.step(sg["end"], -1) if self.index else sg["end"] - self.fd))

    def _mode_changed(self):
        self._update_all()

    def keep_segments(self):
        if self.mode_var.get() == "keep":
            return normalize_segments(self.segments, self.duration)
        return invert_segments(self.segments, self.duration)

    # ---------------- vykreslení ----------------
    def _update_all(self):
        if not self.path:
            self.pos_label.configure(text="")
            return
        n = (self.index.frame_no(self.pos) if self.index else int(self.pos / self.fd)) + 1
        total = len(self.index) if self.index else int(self.duration / self.fd)
        key = " K" if self.index and abs((self.index.next_key(self.pos - 1e-6) or -1) - self.pos) < 1e-6 else ""
        self.pos_label.configure(text=f"{fmt_tc(self.pos)}   snímek {n}/{total}{key}")
        self.in_label.configure(text=f"začátek: {fmt_tc(self.mark_in)}" if self.mark_in is not None else "")
        cut_mode = self.mode_var.get() == "cut"
        self.seg_list.delete(0, "end")
        for i, sg in enumerate(self.segments):
            self.seg_list.insert("end", f"{i + 1:2d}. {fmt_tc(sg['start'])} – {fmt_tc(sg['end'])}  ({fmt_tc(sg['end'] - sg['start'])})")
        keep = self.keep_segments()
        if self.segments:
            self.result_label.configure(text=f"{'Vystřihne se' if cut_mode else 'Ponechá se'} {len(self.segments)} úsek(ů)\n"
                                             f"Výsledek: {fmt_tc(segments_duration(keep))} z {fmt_tc(self.duration)}")
        else:
            self.result_label.configure(text="Zatím žádný úsek - označ začátek (I) a konec (O).")
        tl = self.timeline
        tl.delete("all")
        w, h = self.PREVIEW_W, self.TL_H
        if self.duration <= 0:
            return
        x = lambda t: t / self.duration * w
        tl.create_rectangle(0, 8, w, h - 8, fill="#5a1d1d" if not cut_mode or not self.segments else "#1d5a2a", width=0)
        for sg in keep:
            tl.create_rectangle(x(sg["start"]), 8, x(sg["end"]), h - 8, fill="#2e9e46", width=0)
        if self.mark_in is not None:
            tl.create_line(x(self.mark_in), 0, x(self.mark_in), h, fill="#30c0ff", width=2)
        tl.create_line(x(self.pos), 0, x(self.pos), h, fill="#ffd000", width=2)
        self._draw_wave()

    # ---------------- výstup ----------------
    def _out_changed(self):
        if self.queue_item or not self.path:
            return
        mode = self.out_var.get()
        base = os.path.splitext(os.path.basename(self.path))[0]
        cur = self.out_entry.get().strip()
        folder = os.path.dirname(cur) if cur else self.cfg["output_dir"]
        if mode == self.OUT_ORIGINAL:
            path, state = os.path.join(folder, fit_filename(base, "_strih" + os.path.splitext(self.path)[1])), "normal"
        elif mode == self.OUT_FFM:
            path, state = os.path.join(os.path.dirname(self.path), fit_filename(base, QUEUE_FILE_EXT)), "normal"
        else:
            path, state = "(výstup a složka podle nastavení fronty)", "disabled"
        self.out_entry.configure(state="normal")
        self.out_entry.delete(0, "end")
        self.out_entry.insert(0, path)
        self.out_entry.configure(state=state)
        self.btn_out.configure(state=state)

    def _choose_out(self):
        mode = self.out_var.get()
        cur = self.out_entry.get().strip()
        if mode == self.OUT_FFM:
            f = filedialog.asksaveasfilename(parent=self, defaultextension=QUEUE_FILE_EXT, initialfile=os.path.basename(cur),
                                             initialdir=os.path.dirname(cur), filetypes=[("Fronta FFMPEG Master", "*" + QUEUE_FILE_EXT)])
        else:
            ext = os.path.splitext(self.path)[1]
            f = filedialog.asksaveasfilename(parent=self, defaultextension=ext, initialfile=os.path.basename(cur),
                                             initialdir=os.path.dirname(cur), filetypes=[("Video", "*" + ext), ("Všechny soubory", "*.*")])
        if f:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, os.path.normpath(f))

    def _execute(self):
        if self.running or not self.path:
            return
        if not self.index:
            messagebox.showinfo("Střih", "Počkej, až se dokončí indexace snímků.", parent=self)
            return
        keep = self.keep_segments()
        if not self.segments or not keep:
            messagebox.showinfo("Střih", "Není co zachovat - nejdřív označ úseky.", parent=self)
            return
        if self.queue_item:
            self.queue_item["segments"] = keep
            self.app.refresh_queue_labels()
            self.app.log(f"\n[ STŘIH: {self.queue_item['filename']} - {len(keep)} úsek(ů), {fmt_tc(segments_duration(keep))} ]")
            self._close()
            return
        mode = self.out_var.get()
        if mode == self.OUT_ORIGINAL:
            out = self.out_entry.get().strip()
            if not out:
                return
            if os.path.abspath(out) == os.path.abspath(self.path):
                messagebox.showerror("Střih", "Výstup nesmí přepsat zdrojový soubor.", parent=self)
                return
            if os.path.exists(out) and not messagebox.askyesno("Střih", f"Soubor {os.path.basename(out)} existuje. Přepsat?", parent=self):
                return
            self._start_smartcut(keep, out)
            return
        # Fronta / .ffm: stejný dialog výběru stop jako při přidání souboru
        item = self.app.build_queue_item(self.path, parent=self)
        if item is None:
            return
        item["segments"] = keep
        if mode == self.OUT_FFM:
            out = self.out_entry.get().strip()
            try:
                save_queue_file(out, [item])
            except OSError as e:
                messagebox.showerror("Střih", f"Nejde uložit: {e}", parent=self)
                return
            self.log(f"Uloženo: {out}")
            messagebox.showinfo("Střih", f"Uloženo do {os.path.basename(out)}.\nNačteš ho přes Fronta → Otevřít frontu.", parent=self)
            return
        self.app.append_queue_item(item, note="ze střihu")
        if mode == self.OUT_QUEUE_RUN and not self.app.is_running:
            self._close()
            self.app.start_thread()
            return
        messagebox.showinfo("Střih", "Přidáno do fronty.", parent=self)

    # ---------------- smart cut do originálního formátu ----------------
    def _start_smartcut(self, keep, out):
        self.running, self.stop_flag = True, False
        self.btn_run.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress.set(0)
        self._total = max(segments_duration(keep), 0.1) * 3     # video + zvuk + spojení (odhad)
        self._done_dur = 0.0
        threading.Thread(target=self._smartcut_worker, args=(keep, out), daemon=True).start()

    def _run(self, cmd, dur, label):
        """run() pro SmartCutter: spustí ffmpeg, hlásí průběh, vrací návratový kód (None = přerušeno)."""
        if self.stop_flag:
            return None
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", creationflags=SUBPROCESS_FLAGS, env=self.env)
        except Exception as e:
            self.log(f"[CHYBA] ffmpeg nešel spustit: {e}", error=True)
            return 1
        tail = []
        t0 = None
        for line in self.proc.stdout:
            if self.stop_flag:
                self.proc.terminate()
            tail = (tail + [line])[-15:]
            m = re.search(r"time=(-?)(\d+):(\d+):(\d+\.\d+)", line)
            if m and dur > 0 and not m.group(1):
                t = int(m.group(2)) * 3600 + int(m.group(3)) * 60 + float(m.group(4))
                # s -copyts ffmpeg hlásí absolutní čas -> průběh od prvního hlášeného času
                t0 = t if t0 is None else t0
                frac = min((self._done_dur + min(max(t - t0, 0), dur)) / self._total, 1.0)
                self._ui(lambda f=frac, l=label: (self.progress.set(f), self.status.configure(text=f"{l} ({int(f * 100)} %)")))
        rc = self.proc.wait()
        self.proc = None
        self._done_dur += max(dur, 0)
        self._ui(lambda: self.progress.set(min(self._done_dur / self._total, 1.0)))
        if self.stop_flag:
            return None
        if rc != 0:
            self.log(f"[ffmpeg] {label}: chyba {rc}", error=True)
            for ln in tail:
                if any(k in ln for k in ("rror", "nvalid", "ailed", "not ")):
                    self.log(f"   {ln.strip()}", error=True)
        return rc

    def _smartcut_worker(self, keep, out):
        workdir = None
        try:
            os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
            # Mezisoubory vedle výstupu (ne v /tmp - na openSUSE apod. je /tmp v RAM a film se tam nevejde)
            workdir = tempfile.mkdtemp(prefix=".ffm_strih_", dir=os.path.dirname(os.path.abspath(out)))
            self.log(f"Střih: {len(keep)} úsek(ů), výsledná délka {fmt_tc(segments_duration(keep))}")
            sc = SmartCutter(self.cfg["ffmpeg_path"], self.cfg["ffprobe_path"], self.path, self.info, self.index,
                             self._run, self.log, env=self.env)
            ok, why = sc.execute(keep, out, workdir)
        except Exception as e:
            ok, why = False, f"{type(e).__name__}: {e}"
        finally:
            if workdir:
                shutil.rmtree(workdir, ignore_errors=True)
        def done():
            self.running = False
            self.btn_run.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            if ok:
                self.progress.set(1)
                self.status.configure(text=f"Hotovo: {out}")
                self.log(f"HOTOVO: {out}")
                messagebox.showinfo("Střih", f"Hotovo:\n{out}", parent=self)
            else:
                if os.path.exists(out) and not ok:
                    try:
                        os.remove(out)
                    except OSError:
                        pass
                self.status.configure(text="Přerušeno." if ok is None else f"Chyba: {why}")
                if ok is False:
                    self.log(f"[CHYBA] {why}", error=True)
        self._ui(done)

    def _stop(self):
        self.stop_flag = True
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass

    def _close(self):
        if self.running:
            if not messagebox.askyesno("Střih", "Střih ještě běží. Přerušit?", parent=self):
                return
            self._stop()
        if self._play_proc and self._play_proc.poll() is None:
            self._play_proc.terminate()
        self._closed = True
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


# ==========================================================================
#  DIALOG: NASTAVENÍ (config.json)
# ==========================================================================

class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("Nastavení")
        self.geometry("640x760")
        self.transient(parent)
        self.update_idletasks()  # ať je okno "viewable", než se zavolá grab_set() (viz AboutDialog)
        self.grab_set()
        self.resizable(False, True)
        cfg = app.cfg

        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=14, pady=14)

        def section(text):
            ctk.CTkLabel(scroll, text=text, font=("Arial", 13, "bold")).pack(anchor="w", pady=(12, 4))

        def path_row(label, value, is_dir=False):
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=label, width=140, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row)
            entry.insert(0, value)
            entry.pack(side="left", fill="x", expand=True, padx=6)

            def browse():
                if is_dir:
                    p = filedialog.askdirectory(initialdir=os.path.dirname(entry.get()) if entry.get() else None)
                else:
                    p = filedialog.askopenfilename(initialdir=os.path.dirname(entry.get()) if entry.get() else None)
                if p:
                    entry.delete(0, "end")
                    entry.insert(0, os.path.normpath(p))

            ctk.CTkButton(row, text="...", width=36, command=browse).pack(side="left")
            return entry

        def value_row(label, value, width=100, parent=None):
            row = ctk.CTkFrame(parent if parent is not None else scroll, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=label, width=140, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, width=width)
            entry.insert(0, str(value))
            entry.pack(side="left", padx=6)
            return entry

        section("Cesty")
        self.e_ffmpeg = path_row("FFmpeg", cfg["ffmpeg_path"])
        self.e_ffprobe = path_row("FFprobe", cfg["ffprobe_path"])
        self.e_out = path_row("Výstupní složka", cfg["output_dir"], is_dir=True)
        self.e_done = path_row("Složka HOTOVO", cfg["done_dir"], is_dir=True)

        section("Rozlišení výstupu")
        self.e_maxw = value_row("Max šířka (px)", cfg["video"]["max_width"])
        self.e_maxh = value_row("Max výška (px)", cfg["video"]["max_height"])

        section("Kódování (video + audio)")
        self.custom_cmd_var = ctk.BooleanVar(value=cfg.get("use_custom_encode_args", False))
        ctk.CTkCheckBox(scroll, text="Použít vlastní FFmpeg parametry (přeskočí formulář níže)",
                         variable=self.custom_cmd_var, command=self._toggle_encode_mode).pack(anchor="w", pady=(0, 6))

        self.encode_container = ctk.CTkFrame(scroll, fg_color="transparent")
        self.encode_container.pack(fill="x")

        self.structured_frame = ctk.CTkFrame(self.encode_container, fg_color="transparent")
        self.e_cq = value_row("CQ", cfg["video"]["cq"], parent=self.structured_frame)
        self.e_preset = value_row("Preset", cfg["video"]["preset"], parent=self.structured_frame)
        self.e_abitrate = value_row("Audio bitrate", cfg["audio"]["bitrate"], parent=self.structured_frame)

        self.custom_frame = ctk.CTkFrame(self.encode_container, fg_color="transparent")
        ctk.CTkLabel(self.custom_frame,
                     text="Vlastní FFmpeg parametry kódování — vloží se do příkazu za namapované stopy a před\n"
                          "výstupní soubor (tedy náhrada za -c:v/-c:a/-c:s/... níže). Mapování stop, filtr\n"
                          "škálování a loudnorm zůstávají vždy automatické.",
                     font=("Arial", 10), text_color="gray", justify="left", anchor="w").pack(anchor="w", pady=(2, 4))
        self.custom_cmd_box = ctk.CTkTextbox(self.custom_frame, height=70, font=("Consolas", 11))
        self.custom_cmd_box.insert("1.0", cfg.get("custom_encode_args") or " ".join(default_encode_args(cfg)))
        self.custom_cmd_box.pack(fill="x", pady=(0, 4))
        ctk.CTkButton(self.custom_frame, text="Načíst výchozí parametry", width=190, fg_color="#555555",
                      command=self._fill_default_encode_args).pack(anchor="w")

        self._toggle_encode_mode()

        section("Normalizace hlasitosti (loudnorm)")
        self.e_li = value_row("I (LUFS)", cfg["loudnorm"]["I"])
        self.e_ltp = value_row("TP (dBTP)", cfg["loudnorm"]["TP"])
        self.e_llra = value_row("LRA (LU)", cfg["loudnorm"]["LRA"])

        section("Po dokončení")
        self.nfo_var = ctk.BooleanVar(value=cfg.get("generate_nfo", True))
        ctk.CTkCheckBox(scroll, text="Generovat .nfo soubor", variable=self.nfo_var).pack(anchor="w", pady=3)

        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text="Zdroj: lokální disk", width=140, anchor="w").pack(side="left")
        self.source_action_local_var = ctk.StringVar(value=cfg.get("source_action_local", "move"))
        ctk.CTkOptionMenu(row, values=["move", "rename", "none"], variable=self.source_action_local_var, width=140).pack(side="left", padx=6)

        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text="Zdroj: síťový disk", width=140, anchor="w").pack(side="left")
        self.source_action_network_var = ctk.StringVar(value=cfg.get("source_action_network", "rename"))
        ctk.CTkOptionMenu(row, values=["move", "rename", "none"], variable=self.source_action_network_var, width=140).pack(side="left", padx=6)

        ctk.CTkLabel(scroll, text="move = přesunout do složky HOTOVO, rename = přejmenovat na místě, none = ponechat beze změny.\n"
                                   "Síťový disk = UNC cesta (\\\\server\\sdílená_složka\\...) nebo mapovaná síťová jednotka; rozlišuje se automaticky podle cesty zdrojového souboru.",
                     font=("Arial", 10), text_color="gray", justify="left").pack(anchor="w", pady=(0, 6))

        section("NFO žánry (jeden na řádek)")
        self.genres_box = ctk.CTkTextbox(scroll, height=110)
        self.genres_box.insert("1.0", "\n".join(cfg.get("nfo_genres", [])))
        self.genres_box.pack(fill="x", pady=3)

        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text="Výchozí žánr", width=140, anchor="w").pack(side="left")
        self.e_default_genre = ctk.CTkEntry(row, width=180)
        self.e_default_genre.insert(0, cfg.get("default_genre", ""))
        self.e_default_genre.pack(side="left", padx=6)

        section("Vzhled")
        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=3)
        ctk.CTkLabel(row, text="Motiv", width=140, anchor="w").pack(side="left")
        self.theme_var = ctk.StringVar(value=cfg.get("theme", "dark"))
        ctk.CTkOptionMenu(row, values=["dark", "light", "system"], variable=self.theme_var, width=140).pack(side="left", padx=6)

        section("Aktualizace")
        self.update_var = ctk.BooleanVar(value=cfg.get("check_updates_on_startup", True))
        ctk.CTkCheckBox(scroll, text="Kontrolovat aktualizace při spuštění", variable=self.update_var).pack(anchor="w", pady=3)

        section("Jazyky pro automatický výběr (kódy oddělené čárkou)")
        self.e_langs = ctk.CTkEntry(scroll)
        self.e_langs.insert(0, ", ".join(cfg.get("auto_select_languages", [])))
        self.e_langs.pack(fill="x", pady=3)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(0, 14))
        ctk.CTkButton(btns, text="Zrušit", fg_color="#555555", command=self.destroy).pack(side="right", padx=(8, 0))
        ctk.CTkButton(btns, text="Uložit", fg_color="#28a745", command=self.save).pack(side="right")

    def _toggle_encode_mode(self):
        if self.custom_cmd_var.get():
            self.structured_frame.pack_forget()
            self.custom_frame.pack(fill="x")
        else:
            self.custom_frame.pack_forget()
            self.structured_frame.pack(fill="x")

    def _fill_default_encode_args(self):
        self.custom_cmd_box.delete("1.0", "end")
        self.custom_cmd_box.insert("1.0", " ".join(default_encode_args(self.app.cfg)))

    def save(self):
        cfg = self.app.cfg
        try:
            cfg["ffmpeg_path"] = self.e_ffmpeg.get().strip()
            cfg["ffprobe_path"] = self.e_ffprobe.get().strip()
            cfg["output_dir"] = self.e_out.get().strip()
            cfg["done_dir"] = self.e_done.get().strip()
            cfg["video"]["cq"] = float(self.e_cq.get().replace(",", "."))
            cfg["video"]["preset"] = self.e_preset.get().strip()
            cfg["video"]["max_width"] = int(self.e_maxw.get())
            cfg["video"]["max_height"] = int(self.e_maxh.get())
            cfg["audio"]["bitrate"] = self.e_abitrate.get().strip()
            cfg["use_custom_encode_args"] = self.custom_cmd_var.get()
            cfg["custom_encode_args"] = self.custom_cmd_box.get("1.0", "end").strip()
            if cfg["use_custom_encode_args"]:
                try:
                    if not shlex.split(cfg["custom_encode_args"], posix=(sys.platform != "win32")):
                        raise ValueError("prázdné vlastní FFmpeg parametry")
                except ValueError as e:
                    messagebox.showerror("Chyba", f"Neplatné vlastní FFmpeg parametry: {e}")
                    return
            cfg["loudnorm"]["I"] = float(self.e_li.get().replace(",", "."))
            cfg["loudnorm"]["TP"] = float(self.e_ltp.get().replace(",", "."))
            cfg["loudnorm"]["LRA"] = float(self.e_llra.get().replace(",", "."))
            cfg["generate_nfo"] = self.nfo_var.get()
            cfg["source_action_local"] = self.source_action_local_var.get()
            cfg["source_action_network"] = self.source_action_network_var.get()
            genres = [g.strip() for g in self.genres_box.get("1.0", "end").splitlines() if g.strip()]
            cfg["nfo_genres"] = genres or ["Neurčeno"]
            dg = self.e_default_genre.get().strip()
            cfg["default_genre"] = dg if dg else cfg["nfo_genres"][0]
            cfg["theme"] = self.theme_var.get()
            cfg["check_updates_on_startup"] = self.update_var.get()
            langs = [x.strip().upper() for x in self.e_langs.get().split(",") if x.strip()]
            cfg["auto_select_languages"] = langs or ["CZE", "CES"]
        except ValueError as e:
            messagebox.showerror("Chyba", f"Neplatná hodnota v nastavení: {e}")
            return

        save_config(cfg)
        ctk.set_appearance_mode(cfg["theme"])
        self.app.log("Nastavení bylo uloženo.")
        self.destroy()


# ==========================================================================
#  DIALOG: O PROGRAMU
# ==========================================================================

class AboutDialog(ctk.CTkToplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.title("O programu")
        self.geometry("380x420")
        self.transient(parent)
        # update_idletasks() před grab_set() - bez tohohle grab_set() u zavolání z některých míst
        # (např. kliknutí na banner "dostupná nová verze") spadne na _tkinter.TclError: "grab
        # failed: window not viewable", protože okno ještě nestihlo být window managerem
        # namapované/viditelné, když grab_set() běží hned za sebou. update_idletasks() zpracuje
        # čekající geometry/map požadavky, takže grab_set() pak už narazí na skutečně existující okno.
        self.update_idletasks()
        self.grab_set()
        self.resizable(False, False)

        if HAS_PILLOW:
            try:
                img_path = resource_path("favicon.png")
                if os.path.exists(img_path):
                    img_raw = Image.open(img_path)
                    logo_img = ctk.CTkImage(light_image=img_raw, dark_image=img_raw, size=(96, 96))
                    ctk.CTkLabel(self, image=logo_img, text="").pack(pady=(20, 8))
                else:
                    print(f"[FFMPEG Master] Logo nenalezeno: {img_path}", file=sys.stderr)
            except Exception as e:
                print(f"[FFMPEG Master] Načtení loga selhalo: {e}", file=sys.stderr)

        ctk.CTkLabel(self, text=f"FFMPEG Master {VERSION}", font=("Arial", 18, "bold")).pack(pady=(0, 2))
        ctk.CTkLabel(self, text="Dávkový konvertor videa přes FFmpeg", font=("Arial", 11), text_color="gray").pack()
        ctk.CTkLabel(self, text="© 2026 DaTT.cz", font=("Arial", 11, "italic"), text_color="#2871a7").pack(pady=(10, 0))

        link = ctk.CTkLabel(self, text="github.com/DaTTcz/FFMPEG-Master", font=("Arial", 11, "underline"), text_color="#1f9dff", cursor="hand2")
        link.pack(pady=(4, 0))
        link.bind("<Button-1>", lambda e: webbrowser.open(f"https://github.com/{GITHUB_REPO}"))

        contact = ctk.CTkLabel(self, text="Kontakt: DaTT.cz", font=("Arial", 11, "underline"), text_color="#1f9dff", cursor="hand2")
        contact.pack(pady=(4, 0))
        contact.bind("<Button-1>", lambda e: webbrowser.open("https://datt.cz"))

        self.status_label = ctk.CTkLabel(self, text="", font=("Arial", 10, "italic"), text_color="gray", wraplength=320)
        self.status_label.pack(pady=(16, 4))

        ctk.CTkButton(self, text="Zkontrolovat aktualizace", command=self.manual_check).pack(pady=(6, 4))
        ctk.CTkButton(self, text="Zavřít", fg_color="#555555", command=self.destroy).pack(pady=(4, 12))

        ctk.CTkLabel(self, text="Licence: PolyForm Noncommercial License 1.0.0", font=("Arial", 9), text_color="#3a3a3a").pack(side="bottom", pady=(0, 8))

        # Pokud appka už dřív zjistila novou verzi (proto se vůbec zobrazil banner "dostupná nová
        # verze" na hlavním okně - viz _startup_update_check), rovnou tu informaci ukaž/nabídni
        # update - ať uživatel nemusí po kliknutí na banner ještě jednou ručně mačkat "Zkontrolovat
        # aktualizace" a čekat na nový dotaz na GitHub, když appka tu odpověď už má.
        if getattr(self.app, "_pending_update_info", None):
            self.after(50, lambda: self._show_result(self.app._pending_update_info))

    def manual_check(self):
        self.status_label.configure(text="Kontroluji...")
        threading.Thread(target=self._check_thread, daemon=True).start()

    def _check_thread(self):
        info = check_for_updates(self.app.cfg.get("github_repo", GITHUB_REPO))
        self.after(0, lambda: self._show_result(info))

    def _show_result(self, info):
        if info is None:
            self.status_label.configure(text="Kontrolu se nepodařilo provést (chybí připojení k internetu?).")
            return
        if info["is_newer"]:
            self.status_label.configure(text=f"Dostupná nová verze {info['tag']}!")
            if messagebox.askyesno("Aktualizace dostupná",
                                    f"Je dostupná nová verze {info['tag']} (aktuální: {VERSION}).\n\nChcete ji nyní stáhnout a nainstalovat?"):
                self.app.start_update_download(info)
        else:
            self.status_label.configure(text="Máte nainstalovanou nejnovější verzi.")


# ==========================================================================
#  AKTUALIZACE Z GITHUBU
# ==========================================================================

def _version_tuple(v):
    v = v.strip().lower().lstrip("v")
    parts = re.split(r"[.\-]", v)
    out = []
    for p in parts:
        m = re.match(r"\d+", p)
        out.append(int(m.group(0)) if m else 0)
    return tuple(out) or (0,)


def check_for_updates(repo):
    """Vrátí dict {tag, is_newer, assets, html_url} nebo None při chybě/bez připojení."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "FFMPEG-Master-Updater"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    tag = data.get("tag_name", "")
    if not tag:
        return None
    is_newer = _version_tuple(tag) > _version_tuple(VERSION)
    assets = [{"name": a.get("name"), "url": a.get("browser_download_url")} for a in data.get("assets", [])]
    return {"tag": tag, "is_newer": is_newer, "assets": assets, "html_url": data.get("html_url", f"https://github.com/{repo}/releases")}


# ==========================================================================
#  HLAVNÍ APLIKACE
# ==========================================================================

class _QueueItemFailed(Exception):
    """Přerušení zpracování položky fronty uvnitř bloku try (rc = None: přerušeno, jinak chyba)."""
    def __init__(self, rc):
        super().__init__(rc)
        self.rc = rc


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        try:
            self.TkdndVersion = TkinterDnD._require(self)
            self.dnd_available = True
        except Exception:
            # Na některých (hlavně Linux) systémech nemusí být nainstalovaný tkdnd Tcl balíček -
            # aplikace ať v takovém případě běží dál, jen bez drag & drop (tlačítko "Přidat soubory" funguje vždy).
            self.TkdndVersion = None
            self.dnd_available = False

        self.cfg = load_config()
        ctk.set_appearance_mode(self.cfg.get("theme", "dark"))

        self.title(f"FFMPEG Master {VERSION}")
        # Velikost/pozice okna na uloženou hodnotu z configu se nastaví až v _build_main_ui() -
        # po dobu splash animace (_show_splash() níže) má okno svou vlastní menší velikost, ať
        # nevypadá jako prázdné plátno, a teprve po jejím doběhnutí se "roztáhne" na skutečné UI.

        try:
            if sys.platform == "win32":
                ico_path = resource_path("favicon.ico")
                if os.path.exists(ico_path):
                    self.iconbitmap(ico_path)
                else:
                    print(f"[FFMPEG Master] Ikona nenalezena: {ico_path}", file=sys.stderr)
            elif HAS_PILLOW:
                # .ico/iconbitmap je Windows-only; na Linuxu/macOS se ikona okna/lišty nastavuje přes iconphoto z PNG.
                from PIL import ImageTk
                png_path = resource_path("favicon.png")
                if os.path.exists(png_path):
                    self._app_icon_img = ImageTk.PhotoImage(Image.open(png_path))
                    self.iconphoto(True, self._app_icon_img)
                else:
                    print(f"[FFMPEG Master] Ikona nenalezena: {png_path}", file=sys.stderr)
        except Exception as e:
            print(f"[FFMPEG Master] Nastavení ikony okna selhalo: {e}", file=sys.stderr)

        # Samostatný try/except (nezávislý na nastavení ikony okna výše) - .desktop záznam se má
        # zaregistrovat i kdyby selhalo cokoliv jiného (typicky chybějící PIL._tkinter_finder v
        # zabaleném buildu - viz --hidden-import PIL._tkinter_finder v build.yml/BUILD.md).
        try:
            if sys.platform not in ("win32", "darwin"):
                ensure_linux_desktop_entry()
        except Exception as e:
            print(f"[FFMPEG Master] Registrace .desktop záznamu selhala: {e}", file=sys.stderr)

        self.is_running = False
        self.stop_requested = False
        self.current_process = None
        self.log_entries = []  # (text, is_debug, is_error)
        self.run_completed = False
        self.queue = []  # list of dict - viz add_file()

        # Úvodní "loading" obsah přímo v tomhle (jediném) okně - žádné druhé Toplevel okno se
        # samostatným zobrazováním/skrýváním, které se na různých linuxových WM/kompozitorech
        # chovalo nespolehlivě (viz historie oprav výše/BUILD.md). Okno se ukáže hned normálně,
        # animace doběhne, obsah se zahodí a nahradí skutečným UI (_build_main_ui() níže).
        self._show_splash()
        self._build_main_ui()

    def _show_splash(self):
        """Vykreslí úvodní logo/verzi/progress bar přímo do hlavního okna a chvíli animuje (stejný
        obsah/animace jako dřívější samostatné SplashScreen okno) - viz komentář v __init__ výše.
        Okno má po dobu animace menší, přesně padnoucí velikost (stejné rozměry jako dřívější
        samostatné splash okno) místo aby se hned otevřelo v uložené velikosti hlavního UI -
        na tu se "roztáhne" až _build_main_ui() po doběhnutí animace."""
        w, h = 450, 380
        x = (self.winfo_screenwidth() // 2) - (w // 2)
        y = (self.winfo_screenheight() // 2) - (h // 2)
        # minsize/maxsize na stejnou hodnotu jako geometry - bez toho customtkinter/WM okno umí
        # hned po vytvoření přeplácnout na jinou (větší) velikost, i když je geometry() nastavená
        # explicitně natvrdo hned za sebou. S tímhle je 450x380 vynucená, dokud ji _build_main_ui()
        # po doběhnutí animace záměrně neuvolní zpět na (1, 1)/(screen) a nenastaví uloženou velikost.
        self.minsize(w, h)
        self.maxsize(w, h)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.update_idletasks()

        splash_frame = ctk.CTkFrame(self, fg_color="#1a1a1a")
        splash_frame.pack(fill="both", expand=True)

        logo_loaded = False
        if HAS_PILLOW:
            try:
                img_path = resource_path("favicon.png")
                if os.path.exists(img_path):
                    img_raw = Image.open(img_path)
                    logo_img = ctk.CTkImage(light_image=img_raw, dark_image=img_raw, size=(160, 160))
                    ctk.CTkLabel(splash_frame, image=logo_img, text="").pack(pady=(30, 10))
                    logo_loaded = True
                else:
                    print(f"[FFMPEG Master] Logo nenalezeno: {img_path}", file=sys.stderr)
            except Exception as e:
                print(f"[FFMPEG Master] Načtení loga selhalo: {e}", file=sys.stderr)
        if not logo_loaded:
            ctk.CTkLabel(splash_frame, text=f"FFMPEG Master {VERSION}", font=("Arial", 32, "bold"), text_color="#1f538d").pack(pady=(60, 20))

        ctk.CTkLabel(splash_frame, text=f"FFMPEG Master {VERSION}", font=("Arial", 16, "bold"), text_color="#28a745").pack(pady=(5, 0))
        ctk.CTkLabel(splash_frame, text="©2026 David Trubka", font=("Arial", 10, "italic"), text_color="#2871a7").pack(pady=(0, 5))
        label_status = ctk.CTkLabel(splash_frame, text="Inicializace...", font=("Arial", 11, "italic"), text_color="gray")
        label_status.pack(pady=(20, 0))
        prog = ctk.CTkProgressBar(splash_frame, width=350, height=4, progress_color="#1f538d")
        prog.pack(pady=20)
        prog.set(0)
        self.update()

        quotes = ["Inicializace kodeků...", "Detekce CUDA jader...", "Příprava FFmpeg...", "Optimalizace procesů...", "Vše je připraveno!"]
        for i in range(1, 101):
            prog.set(i / 100)
            idx = min((i - 1) * len(quotes) // 100, len(quotes) - 1)
            label_status.configure(text=quotes[idx])
            self.update()
            time.sleep(0.06)

        splash_frame.destroy()

    def _build_main_ui(self):
        # Uvolni vynucenou splash velikost (viz _show_splash()) a roztáhni okno na uloženou
        # velikost skutečného UI. Tk nemá "zruš maxsize" - nastav ho zpátky na velikost obrazovky
        # (efektivně bez omezení), minsize (700, 500) je rozumné minimum pro hlavní okno.
        self.maxsize(self.winfo_screenwidth(), self.winfo_screenheight())
        self.minsize(700, 500)
        self.geometry(self.cfg.get("window_geometry", "750x740"))

        self._build_menu()

        # --- UI ---
        ctk.CTkLabel(self, text=f"FFMPEG Master {VERSION}", font=("Arial", 22, "bold")).pack(pady=10)

        self.top_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.top_frame.pack(padx=20, pady=5, fill="x")

        self.drop_frame = ctk.CTkFrame(self.top_frame, height=80, border_width=2, border_color="#1f538d")
        self.drop_frame.pack(side="left", fill="x", expand=True)
        self.drop_frame.pack_propagate(False)
        if self.dnd_available:
            self.drop_label = ctk.CTkLabel(self.drop_frame, text="Sem přetáhni video soubory (MKV, MP4, AVI...)")
            self.drop_label.pack(expand=True)
            try:
                self.drop_frame.drop_target_register(DND_FILES)
                self.drop_frame.dnd_bind('<<Drop>>', self.handle_drop)
            except Exception:
                self.dnd_available = False
        if not self.dnd_available:
            self.drop_label = ctk.CTkLabel(self.drop_frame, text="Drag & drop není na tomto systému dostupný - použij „Přidat soubory“")
            self.drop_label.pack(expand=True)

        self.btn_browse = ctk.CTkButton(self.top_frame, text="Přidat soubory", width=140, height=80, command=self.browse_files)
        self.btn_browse.pack(side="right", padx=(10, 0))

        self.mid_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.mid_frame.pack(padx=20, pady=10, fill="both", expand=True)
        self.listbox = Listbox(self.mid_frame, bg="#2b2b2b", fg="white", font=("Arial", 11), borderwidth=0, highlightthickness=0, selectmode=EXTENDED)
        self.listbox.pack(side="left", fill="both", expand=True, padx=(0, 10))

        self.list_btns = ctk.CTkFrame(self.mid_frame, width=150, fg_color="transparent")
        self.list_btns.pack(side="right", fill="y")
        self.btn_up = ctk.CTkButton(self.list_btns, text="↑ Nahoru", command=self.move_up)
        self.btn_up.pack(pady=5, fill="x")
        self.btn_down = ctk.CTkButton(self.list_btns, text="↓ Dolů", command=self.move_down)
        self.btn_down.pack(pady=5, fill="x")
        self.btn_edit = ctk.CTkButton(self.list_btns, text="Upravit", fg_color="#1f538d", command=self.edit_selected)
        self.btn_edit.pack(pady=(20, 5), fill="x")
        self.btn_cut = ctk.CTkButton(self.list_btns, text="Střih…", fg_color="#1f6f8b", command=self.cut_selected)
        self.btn_cut.pack(pady=5, fill="x")
        self.btn_del = ctk.CTkButton(self.list_btns, text="Smazat", fg_color="#a11a1a", command=self.remove_selected)
        self.btn_del.pack(pady=5, fill="x")

        log_header = ctk.CTkFrame(self, fg_color="transparent")
        log_header.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(log_header, text="Log:", font=("Arial", 11)).pack(side="left")
        self.debug_var = ctk.BooleanVar(value=False)
        self.debug_var.trace_add("write", lambda *_: self.redraw_log())
        ctk.CTkCheckBox(log_header, text="debug zprávy", variable=self.debug_var, font=("Arial", 11), checkbox_width=16, checkbox_height=16).pack(side="right")

        self.textbox = ctk.CTkTextbox(self, height=140, font=("Consolas", 10))
        self.textbox.pack(padx=20, pady=(2, 10), fill="x")
        self.textbox._textbox.tag_configure("debug", foreground="#888888")
        self.textbox._textbox.tag_configure("error", foreground="#ff4444")

        self.label_p1 = ctk.CTkLabel(self, text="Aktuální soubor: ", font=("Arial", 11, "bold")); self.label_p1.pack(padx=20, anchor="w")
        # Druhý řádek pod názvem souboru: průchod, %, rychlost kódování, uplynulý/zbývající čas.
        # height=16 (místo výchozích 28) - ať se okno kvůli novému řádku skoro nezvětší.
        self.label_pass = ctk.CTkLabel(self, text="", font=("Arial", 11), height=16, text_color=("gray30", "gray70"))
        self.label_pass.pack(padx=20, pady=(0, 3), anchor="w")
        self.prog_file = ctk.CTkProgressBar(self, height=12, progress_color="#1f538d"); self.prog_file.pack(pady=(0, 10), padx=20, fill="x"); self.prog_file.set(0)
        self.label_p2 = ctk.CTkLabel(self, text="Celkový postup: 0/0", font=("Arial", 11, "bold")); self.label_p2.pack(padx=20, anchor="w")
        self.prog_total = ctk.CTkProgressBar(self, height=12, progress_color="#28a745"); self.prog_total.pack(pady=(0, 10), padx=20, fill="x"); self.prog_total.set(0)

        self.update_banner = ctk.CTkLabel(self, text="", font=("Arial", 11, "underline"), text_color="#e0a030", cursor="hand2")
        self.update_banner.pack(pady=(0, 4))
        self.update_banner.bind("<Button-1>", lambda e: self.open_about())
        self._pending_update_info = None

        self.bot_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bot_frame.pack(pady=(0, 10))
        self.btn_start = ctk.CTkButton(self.bot_frame, text="SPUSTIT", height=50, width=160, fg_color="#28a745", font=("Arial", 15, "bold"), command=self.start_thread)
        self.btn_start.pack(side="left", padx=10)
        self.btn_stop = ctk.CTkButton(self.bot_frame, text="STOP", height=50, width=120, fg_color="#a11a1a", font=("Arial", 15, "bold"), command=self.stop_process, state="disabled")
        self.btn_stop.pack(side="left", padx=10)

        ctk.CTkLabel(self, text="DaTT.cz  © 2026", font=("Arial", 10), text_color=("gray40", "gray60")).pack(side="bottom", pady=(0, 6))

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self._fit_window_to_content)

        if self.cfg.get("check_updates_on_startup", True):
            self.after(1500, lambda: threading.Thread(target=self._startup_update_check, daemon=True).start())

    def _fit_window_to_content(self):
        """Zajistí, aby se do okna vešlo všechno (log, oba průběhy, tlačítka SPUSTIT/STOP).

        Potřebná výška se neodhaduje natvrdo - Tk ji po sestavení UI sám spočítá (winfo_reqheight),
        takže sedí na každém systému bez ohledu na písma, DPI a měřítko. Okno se jen zvětší, nikdy
        nezmenší (uživatel si ho mohl roztáhnout víc), a funguje to i pro starší uloženou velikost
        v config.json, která byla menší. Stejná hodnota se nastaví jako minsize, takže okno nejde
        zmenšit tak, aby se tlačítka ořízla."""
        try:
            self.update_idletasks()
            # customtkinter násobí hodnoty v geometry()/minsize() měřítkem okna, winfo_* vrací
            # skutečné pixely - proto převod zpět na "neškálované" jednotky.
            scale = self._get_window_scaling() if hasattr(self, "_get_window_scaling") else 1.0
            need_h = int(self.winfo_reqheight() / scale) + 10
            screen_h = int(self.winfo_screenheight() / scale) - 80   # rezerva na panely / titulek
            need_h = min(need_h, screen_h)
            self.minsize(700, need_h)
            cur_w = int(self.winfo_width() / scale)
            cur_h = int(self.winfo_height() / scale)
            if cur_h < need_h:
                x, y = self.winfo_x(), self.winfo_y()
                # Pokud by se zvětšené okno nevešlo dolů na obrazovku, posuň ho nahoru.
                y_max = max(int(self.winfo_screenheight() / scale) - need_h - 60, 0)
                self.geometry(f"{max(cur_w, 700)}x{need_h}+{x}+{min(y, y_max)}")
        except Exception as e:
            print(f"[FFMPEG Master] Přizpůsobení velikosti okna selhalo (nekritické): {e}", file=sys.stderr)

    # --- MENU ---
    def _build_menu(self):
        menubar = Menu(self)
        queue_menu = Menu(menubar, tearoff=0)
        queue_menu.add_command(label="Otevřít frontu…", accelerator="Ctrl+O", command=self.open_queue)
        queue_menu.add_command(label="Uložit frontu…", accelerator="Ctrl+S", command=self.save_queue)
        menubar.add_cascade(label="Fronta", menu=queue_menu)
        menubar.add_command(label="Střih…", command=self.open_cut)
        self.queue_menu = queue_menu
        # Zkratky jen v hlavním okně - ne když je otevřený dialog (výběr stop, nastavení...).
        self.bind_all("<Control-o>", lambda e: self.open_queue() if e.widget.winfo_toplevel() is self else None)
        self.bind_all("<Control-s>", lambda e: self.save_queue() if e.widget.winfo_toplevel() is self else None)
        menubar.add_command(label="Nastavení", command=self.open_settings)
        menubar.add_command(label="O programu", command=self.open_about)
        self.configure(menu=menubar)

    def open_settings(self):
        SettingsDialog(self, self)

    def open_about(self):
        AboutDialog(self, self)

    # --- AKTUALIZACE ---
    def _startup_update_check(self):
        info = check_for_updates(self.cfg.get("github_repo", GITHUB_REPO))
        if info and info["is_newer"]:
            self._pending_update_info = info
            self.after(0, lambda: self.update_banner.configure(
                text=f"🔔 Dostupná nová verze {info['tag']} – klikni pro detaily (aktuální: {VERSION})"))

    @staticmethod
    def _pick_update_asset(assets):
        """Vybere release asset odpovídající aktuální platformě/způsobu instalace. Windows build
        z CI se jmenuje '*.exe', Linux AppImage/přenosný build '*.AppImage', Linux .deb instalace
        '*.deb', Linux .rpm instalace '*.rpm' (viz linux_run_mode())."""
        if sys.platform == "win32":
            return next((a for a in assets if (a.get("name") or "").lower().endswith(".exe")), None)
        mode = linux_run_mode()
        if mode in ("deb", "rpm"):
            return next((a for a in assets if (a.get("name") or "").lower().endswith("." + mode)), None)
        return next((a for a in assets if (a.get("name") or "").lower().endswith(".appimage")), None)

    def start_update_download(self, info):
        if not getattr(sys, "frozen", False):
            # Vývojový/skriptový režim - automatická výměna souboru nedává smysl, otevři stránku s vydáním.
            self.log("Aktualizace: běžím jako .pyw skript, otevírám stránku s vydáním v prohlížeči.")
            webbrowser.open(info["html_url"])
            return
        asset = self._pick_update_asset(info["assets"])
        if not asset:
            if sys.platform == "win32":
                plat_name = "Windows (.exe)"
            elif linux_run_mode() in ("deb", "rpm"):
                plat_name = f"Linux (.{linux_run_mode()})"
            else:
                plat_name = "Linux (.AppImage)"
            messagebox.showwarning("Aktualizace", f"V nejnovějším vydání nebyl nalezen soubor pro tuto platformu ({plat_name}). Otevírám stránku s vydáním.")
            webbrowser.open(info["html_url"])
            return
        if sys.platform != "win32" and linux_run_mode() in ("deb", "rpm"):
            # .deb/.rpm instalace: binárka je v /usr/bin (root-owned), appka ji sama nemůže přepsat bez
            # oprávnění - stáhne nový balíček a instalaci provede přes pkexec (systémové heslové okno,
            # stejné jako u jakéhokoliv jiného zásahu vyžadujícího root na Linuxu).
            threading.Thread(target=self._download_and_install_package, args=(asset,), daemon=True).start()
            return
        threading.Thread(target=self._download_and_install, args=(asset,), daemon=True).start()

    def _download_and_install_package(self, asset):
        try:
            update_dir = os.path.join(base_dir(), "update")
            os.makedirs(update_dir, exist_ok=True)
            new_path = os.path.join(update_dir, asset["name"])
            self.log(f"Stahuji aktualizaci: {asset['name']}...")
            urllib.request.urlretrieve(asset["url"], new_path)
            self.log("Stažení dokončeno, instaluji (vyžádá heslo přes systémový dialog)...")

            if linux_run_mode() == "rpm":
                # rpm -U = upgrade (nebo instalace). Závislosti (ffmpeg...) už jsou splněné z první
                # instalace, takže není potřeba jít přes dnf/zypper. Balíček není podepsaný - rpm
                # to u lokálního souboru jen oznámí varováním, instalaci to nebrání.
                install_cmd = ["pkexec", "rpm", "-U", "--replacepkgs", new_path]
                manual_cmd = f'sudo rpm -U "{new_path}"'
            else:
                install_cmd = ["pkexec", "dpkg", "-i", new_path]
                manual_cmd = f'sudo dpkg -i "{new_path}"'
            result = subprocess.run(install_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                self.log(f"CHYBA při instalaci balíčku (kód {result.returncode}): {err}", error=True)
                self.after(0, lambda: messagebox.showerror(
                    "Aktualizace selhala",
                    "Instalace nového balíčku se nezdařila"
                    + (f":\n\n{err[:500]}" if err else " (uživatel zrušil heslové okno?).")
                    + f"\n\nBalíček zůstal stažený zde:\n{new_path}\n\nJde ho zkusit nainstalovat ručně: {manual_cmd}"
                ))
                return

            self.log("Aktualizace nainstalována, spouštím novou verzi...")
            # sys.executable je u .deb/.rpm instalace stabilní cesta (/usr/bin/ffmpeg-master) - dpkg ji
            # právě přepsal na místě, takže jde rovnou znovu spustit stejnou cestou.
            subprocess.Popen([sys.executable], start_new_session=True)
            self.after(0, self.destroy)
        except FileNotFoundError:
            ext = linux_run_mode()
            tool = "rpm -U" if ext == "rpm" else "dpkg -i"
            self.log("CHYBA: pkexec nenalezen (chybí balíček polkit/pkexec?).", error=True)
            self.after(0, lambda: messagebox.showerror(
                "Aktualizace selhala",
                "Nástroj pro instalaci s oprávněním (pkexec) nebyl na systému nalezen.\n\n"
                f"Stáhni si nový .{ext} ručně z GitHubu a nainstaluj ho příkazem \"sudo {tool} <soubor>.{ext}\"."))
        except Exception as e:
            self.log(f"CHYBA při aktualizaci: {e}", error=True)
            self.after(0, lambda: messagebox.showerror("Aktualizace selhala", str(e)))

    def _download_and_install(self, asset):
        try:
            update_dir = os.path.join(base_dir(), "update")
            os.makedirs(update_dir, exist_ok=True)
            new_path = os.path.join(update_dir, asset["name"])
            self.log(f"Stahuji aktualizaci: {asset['name']}...")
            urllib.request.urlretrieve(asset["url"], new_path)
            self.log("Stažení dokončeno, instaluji...")

            # Na Linuxu jako AppImage je stabilní cesta k souboru, který se má nahradit, v
            # proměnné APPIMAGE (viz linux_run_mode) - sys.executable by ukazoval do dočasného
            # mount pointu. Jinde (Windows .exe, přenosný Linux build) je to prostě sys.executable.
            current_exe = os.environ["APPIMAGE"] if sys.platform != "win32" and linux_run_mode() == "appimage" else sys.executable
            if sys.platform == "win32":
                # Běžící .exe nejde přepsat sám sebou - pomocný .bat počká, až proces skončí, teprve pak ho nahradí a znovu spustí.
                bat_path = os.path.join(update_dir, "update.bat")
                with open(bat_path, "w", encoding="utf-8") as f:
                    f.write(f"""@echo off
timeout /t 2 /nobreak >nul
move /y "{new_path}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
""")
                subprocess.Popen(["cmd", "/c", bat_path], creationflags=DETACHED_FLAGS)
            else:
                # Linuxová obdoba .bat postupu - shell skript spuštěný odděleně (start_new_session),
                # ať přežije i po ukončení tohoto procesu.
                os.chmod(new_path, 0o755)
                sh_path = os.path.join(update_dir, "update.sh")
                with open(sh_path, "w", encoding="utf-8") as f:
                    f.write(f"""#!/bin/sh
sleep 2
mv -f "{new_path}" "{current_exe}"
chmod +x "{current_exe}"
"{current_exe}" &
rm -f "{sh_path}"
""")
                os.chmod(sh_path, 0o755)
                subprocess.Popen(["/bin/sh", sh_path], start_new_session=True)
            self.after(0, self.destroy)
        except Exception as e:
            self.log(f"CHYBA při aktualizaci: {e}", error=True)
            self.after(0, lambda: messagebox.showerror("Aktualizace selhala", str(e)))

    # --- LOG ---
    def log(self, text, debug=False, error=False):
        self.log_entries.append((text, debug, error))
        if not debug or self.debug_var.get():
            tag = "error" if error else ("debug" if debug else "")
            self.textbox._textbox.insert("end", text + "\n", tag)
            self.textbox.see("end")
        self.update_idletasks()

    def redraw_log(self):
        self.textbox.configure(state="normal")
        self.textbox._textbox.delete("1.0", "end")
        for text, is_debug, is_error in self.log_entries:
            if not is_debug or self.debug_var.get():
                tag = "error" if is_error else ("debug" if is_debug else "")
                self.textbox._textbox.insert("end", text + "\n", tag)
        self.textbox.see("end")

    def set_ui_state(self, state):
        s = "normal" if state else "disabled"
        self.btn_browse.configure(state=s)
        self.btn_up.configure(state=s)
        self.btn_down.configure(state=s)
        self.btn_edit.configure(state=s)
        self.btn_del.configure(state=s)
        self.btn_cut.configure(state=s)
        try:
            self.queue_menu.entryconfigure(0, state=s)   # Otevřít frontu (uložit jde i za běhu)
        except Exception:
            pass

    # --- FRONTA SOUBORŮ ---
    def _maybe_clear_log(self):
        if not self.is_running and self.run_completed:
            self.log_entries = []
            self.run_completed = False
            self.textbox.configure(state="normal")
            self.textbox.delete("1.0", "end")

    def add_file(self, path):
        filename = os.path.basename(path)
        probe, probe_error = probe_file(path, self.cfg["ffprobe_path"])
        if probe is None:
            self.log(f"[CHYBA] Nelze analyzovat soubor: {filename}", error=True)
            self.log(f"        Důvod: {probe_error}", error=True)
            return

        dialog = TrackSelectionDialog(self, filename, probe, self.cfg, mode="add", path=path)
        self.wait_window(dialog)
        if dialog.result is None:
            self.log(f"Přeskočeno: {filename}")
            return

        item = {
            "path": path,
            "filename": filename,
            "duration": probe["duration"],
            "video": probe["video"],
            "audio_streams": probe["audio_streams"],
            "sub_streams": probe["sub_streams"],
            "selected_audio": dialog.result["selected_audio"],
            "selected_subs": dialog.result["selected_subs"],
            "nfo": dialog.result["nfo"],
            "generate_nfo": dialog.result.get("generate_nfo_for_file", True),
            "copy_video": dialog.result.get("copy_video", False),
            "crop": dialog.result.get("crop"),
            "segments": [],     # úseky ze Střihu - zatím jen přenáší se přes .ffm (další krok)
        }
        self.queue.append(item)
        self.listbox.insert("end", self._queue_label(item))

        a_n, s_n = len(item["selected_audio"]), len(item["selected_subs"])
        self.log(f"\n[ PŘIDÁNO: {filename} ]")
        self.log(f"  Video: {probe['video']['res']}, Délka: {fmt_duration(probe['duration'])}")
        self.log(f"  Vybráno: {a_n} audio stopa/y, {s_n} titulková/é stopa/y")
        if item["copy_video"]:
            self.log("  Video:   bez překódování (kopírováno beze změny).")
        if item["crop"]:
            self.log(f"  Ořez:    {crop_describe(item['crop'])}")
        if self.cfg.get("generate_nfo", True) and not item["generate_nfo"]:
            self.log("  NFO:     pro tento soubor vypnuto.")
        self.log("-" * 50)

    def edit_selected(self):
        if self.is_running:
            return
        pos = self.listbox.curselection()
        if len(pos) != 1:
            messagebox.showinfo("Upravit", "Vyber přesně jeden soubor ve frontě, který chceš upravit.")
            return
        idx = pos[0]
        item = self.queue[idx]

        probe = {
            "duration": item["duration"],
            "video": item["video"],
            "audio_streams": item["audio_streams"],
            "sub_streams": item["sub_streams"],
        }
        initial = {
            "selected_audio": item["selected_audio"],
            "selected_subs": item["selected_subs"],
            "nfo": item["nfo"],
            "generate_nfo": item.get("generate_nfo", True),
            "copy_video": item.get("copy_video", False),
            "crop": item.get("crop"),
        }

        dialog = TrackSelectionDialog(self, item["filename"], probe, self.cfg, mode="edit", initial=initial,
                                      path=item["path"])
        self.wait_window(dialog)
        if dialog.result is None:
            return

        item["selected_audio"] = dialog.result["selected_audio"]
        item["selected_subs"] = dialog.result["selected_subs"]
        item["copy_video"] = dialog.result.get("copy_video", False)
        item["crop"] = dialog.result.get("crop")
        self.listbox.delete(idx)
        self.listbox.insert(idx, self._queue_label(item))
        self.listbox.select_set(idx)
        if dialog.nfo_enabled:
            item["nfo"] = dialog.result["nfo"]
            item["generate_nfo"] = dialog.result.get("generate_nfo_for_file", True)

        a_n, s_n = len(item["selected_audio"]), len(item["selected_subs"])
        self.log(f"\n[ UPRAVENO: {item['filename']} ]")
        self.log(f"  Vybráno: {a_n} audio stopa/y, {s_n} titulková/é stopa/y")
        if item["copy_video"]:
            self.log("  Video:   bez překódování (kopírováno beze změny).")
        self.log(f"  Ořez:    {crop_describe(item['crop']) if item['crop'] else 'bez ořezu'}")
        if self.cfg.get("generate_nfo", True) and not item.get("generate_nfo", True):
            self.log("  NFO:     pro tento soubor vypnuto.")
        self.log("-" * 50)

    # --- STŘIH ---
    def open_cut(self):
        CutWindow(self)

    def cut_selected(self):
        if self.is_running:
            return
        pos = self.listbox.curselection()
        if len(pos) != 1:
            messagebox.showinfo("Střih", "Vyber ve frontě jeden soubor, nebo otevři Střih z menu.")
            return
        CutWindow(self, queue_item=self.queue[pos[0]])

    def refresh_queue_labels(self):
        sel = self.listbox.curselection()
        self.listbox.delete(0, "end")
        for it in self.queue:
            self.listbox.insert("end", self._queue_label(it))
        for i in sel:
            self.listbox.select_set(i)

    def build_queue_item(self, path, parent=None):
        """Analýza + dialog výběru stop -> položka fronty (bez přidání), nebo None."""
        filename = os.path.basename(path)
        probe, probe_error = probe_file(path, self.cfg["ffprobe_path"])
        if probe is None:
            messagebox.showerror("Chyba", f"Nelze analyzovat {filename}: {probe_error}", parent=parent)
            return None
        dialog = TrackSelectionDialog(parent or self, filename, probe, self.cfg, mode="add", path=path)
        self.wait_window(dialog)
        if parent is not None:
            try:
                parent.grab_set()
            except Exception:
                pass
        if dialog.result is None:
            return None
        return {
            "path": path, "filename": filename, "duration": probe["duration"], "video": probe["video"],
            "audio_streams": probe["audio_streams"], "sub_streams": probe["sub_streams"],
            "selected_audio": dialog.result["selected_audio"], "selected_subs": dialog.result["selected_subs"],
            "nfo": dialog.result["nfo"], "generate_nfo": dialog.result.get("generate_nfo_for_file", True),
            "copy_video": dialog.result.get("copy_video", False), "crop": dialog.result.get("crop"), "segments": [],
        }

    def append_queue_item(self, item, note=""):
        self.queue.append(item)
        self.listbox.insert("end", self._queue_label(item))
        self.log(f"\n[ PŘIDÁNO{' ' + note if note else ''}: {item['filename']} ]")
        if item.get("segments"):
            self.log(f"  Střih:   {len(item['segments'])} úsek(ů), výsledná délka {fmt_tc(segments_duration(item['segments']))}")
        if item.get("crop"):
            self.log(f"  Ořez:    {crop_describe(item['crop'])}")

    # --- ULOŽENÍ / NAČTENÍ FRONTY (.ffm) ---
    @staticmethod
    def _queue_label(item):
        """Text položky v seznamu fronty - název + značky ořezu/střihu, ať je vidět, co položka nese."""
        marks = []
        if item.get("crop"):
            marks.append("▣ ořez")
        segs = item.get("segments") or []
        if segs:
            marks.append(f"✂ {len(segs)} úsek{'y' if 2 <= len(segs) <= 4 else 'ů' if len(segs) > 4 else ''}")
        return item["filename"] + (f"   [{', '.join(marks)}]" if marks else "")

    def save_queue(self):
        if not self.queue:
            messagebox.showinfo("Uložit frontu", "Fronta je prázdná - není co uložit.")
            return
        first = self.queue[0]
        initial_name = fit_filename(os.path.splitext(first["filename"])[0] if len(self.queue) == 1 else "fronta", QUEUE_FILE_EXT)
        path = filedialog.asksaveasfilename(
            title="Uložit frontu", defaultextension=QUEUE_FILE_EXT, initialfile=initial_name,
            initialdir=os.path.dirname(first["path"]),
            filetypes=[("Fronta FFMPEG Master", "*" + QUEUE_FILE_EXT), ("Všechny soubory", "*.*")])
        if not path:
            return
        if not path.lower().endswith(QUEUE_FILE_EXT):
            path += QUEUE_FILE_EXT
        try:
            save_queue_file(path, self.queue)
        except OSError as e:
            self.log(f"[CHYBA] Frontu nelze uložit: {e}", error=True)
            messagebox.showerror("Uložit frontu", f"Frontu nelze uložit:\n{e}")
            return
        self.log(f"Fronta uložena (položek: {len(self.queue)}): {path}")

    def open_queue(self):
        if self.is_running:
            return
        path = filedialog.askopenfilename(
            title="Otevřít frontu",
            filetypes=[("Fronta FFMPEG Master", "*" + QUEUE_FILE_EXT), ("Všechny soubory", "*.*")])
        if path:
            self._maybe_clear_log()
            self.load_queue_from(os.path.normpath(path))

    def load_queue_from(self, ffm_path):
        """Načte .ffm a položky PŘIDÁ na konec fronty (nic nepřepisuje) - bez dialogu výběru stop,
        nastavení je uložené v souboru. Každý zdroj se znovu analyzuje a ověří."""
        if self.is_running:
            return
        name = os.path.basename(ffm_path)
        try:
            entries = load_queue_file(ffm_path)
        except (OSError, ValueError) as e:
            self.log(f"[CHYBA] Nelze načíst frontu {name}: {e}", error=True)
            messagebox.showerror("Otevřít frontu", f"Nelze načíst frontu {name}:\n{e}")
            return
        self.log(f"\n[ NAČÍTÁM FRONTU: {name} (položek: {len(entries)}) ]")
        added = sum(1 for e in entries if self._add_item_from_ffm(e))
        self.log(f"Z fronty {name} přidáno {added} z {len(entries)}.")
        self.log("-" * 50)
        if added < len(entries):
            messagebox.showwarning("Otevřít frontu",
                                   f"Přidáno {added} z {len(entries)} položek - podrobnosti jsou v logu.")

    def _add_item_from_ffm(self, entry):
        path = entry["_resolved"]
        filename = os.path.basename(path)
        if not os.path.isfile(path):
            self.log(f"[CHYBA] {filename}: zdrojový soubor nenalezen ({path})", error=True)
            return False
        probe, probe_error = probe_file(path, self.cfg["ffprobe_path"])
        if probe is None:
            self.log(f"[CHYBA] Nelze analyzovat soubor: {filename}", error=True)
            self.log(f"        Důvod: {probe_error}", error=True)
            return False

        warnings = []
        saved_size = entry.get("size")
        try:
            cur_size = os.path.getsize(path)
            if isinstance(saved_size, int) and cur_size != saved_size:
                warnings.append(f"velikost souboru se liší od uložené ({saved_size} → {cur_size} B)")
        except OSError:
            pass
        saved_dur = entry.get("duration")
        if isinstance(saved_dur, (int, float)) and abs(probe["duration"] - saved_dur) > 1.0:
            warnings.append(f"délka se liší od uložené ({fmt_duration(saved_dur)} → {fmt_duration(probe['duration'])})")

        def keep_valid(key, streams, label):
            valid = {s["index"] for s in streams}
            wanted = [str(i) for i in (entry.get(key) or [])]
            kept = [i for i in wanted if i in valid]
            if len(kept) != len(wanted):
                warnings.append(f"{label} stopy {', '.join(i for i in wanted if i not in valid)} v souboru nejsou - vynechány")
            return kept

        item = {
            "path": path,
            "filename": filename,
            "duration": probe["duration"],
            "video": probe["video"],
            "audio_streams": probe["audio_streams"],
            "sub_streams": probe["sub_streams"],
            "selected_audio": keep_valid("selected_audio", probe["audio_streams"], "audio"),
            "selected_subs": keep_valid("selected_subs", probe["sub_streams"], "titulkové"),
            "nfo": entry.get("nfo") if isinstance(entry.get("nfo"), dict) else default_nfo(filename),
            "generate_nfo": bool(entry.get("generate_nfo", True)),
            "copy_video": bool(entry.get("copy_video", False)),
            "crop": normalize_crop(entry.get("crop")),
            "segments": entry.get("segments") or [],
        }
        self.queue.append(item)
        self.listbox.insert("end", self._queue_label(item))

        self.log(f"  + {filename}: {len(item['selected_audio'])} audio, {len(item['selected_subs'])} titulky"
                 + (", video kopírováno" if item["copy_video"] else ""))
        for w in warnings:
            self.log(f"    VAROVÁNÍ: {w}", error=True)
        if warnings and (item["crop"] or item["segments"]):
            self.log("    VAROVÁNÍ: soubor se od uložení změnil - ořez/střih nemusí sedět, zkontroluj ho.", error=True)
        return True

    def handle_drop(self, event):
        if self.is_running:
            return
        try:
            # self.tk.splitlist() nechá parsování na samotném Tcl interpretu (ten drop data vždy
            # vydává jako korektní Tcl seznam, {} kolem položek s mezerou/speciálním znakem
            # včetně). Ruční regex (fallback níže) na některých sestaveních tkdnd - typicky na
            # Linuxu - selhával a cesty s mezerou v názvu rozsekal na dva neplatné kusy.
            raw_paths = self.tk.splitlist(event.data)
        except Exception:
            matches = re.findall(r'\{(.*?)\}|(\S+)', event.data)
            raw_paths = [m[0] if m[0] else m[1] for m in matches]
        self._maybe_clear_log()
        for raw in raw_paths:
            if raw.lower().endswith(QUEUE_FILE_EXT):
                path, reason = resolve_dropped_path(raw)
                if path is None:
                    self.log(f"[CHYBA] Nelze načíst frontu: {reason}", error=True)
                else:
                    self.load_queue_from(path)
                continue
            if not raw.lower().endswith(VIDEO_EXTENSIONS):
                continue
            path, reason = resolve_dropped_path(raw)
            if path is None:
                name = urllib.parse.unquote(raw.rstrip("/").rsplit("/", 1)[-1])
                self.log(f"[CHYBA] Nelze přidat soubor: {name}", error=True)
                self.log(f"        Důvod: {reason}", error=True)
                continue
            self.add_file(path)

    def browse_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Video soubory", "*.mkv *.mp4 *.avi *.mov *.ts *.m2ts *.wmv *.flv *.webm *.m4v"),
                                                       ("Fronta FFMPEG Master", "*" + QUEUE_FILE_EXT),
                                                       ("Všechny soubory", "*.*")])
        if not files:
            return
        self._maybe_clear_log()
        for f in files:
            if f.lower().endswith(QUEUE_FILE_EXT):
                self.load_queue_from(os.path.normpath(f))
            else:
                self.add_file(os.path.normpath(f))

    def _listbox_index_map(self):
        """Vrátí seznam pozic ve self.queue odpovídající aktuálnímu pořadí v listboxu (stejné pořadí, 1:1)."""
        return list(range(len(self.queue)))

    def move_up(self):
        pos = self.listbox.curselection()
        if pos and pos[0] > 0:
            for idx in pos:
                t = self.listbox.get(idx); self.listbox.delete(idx); self.listbox.insert(idx - 1, t); self.listbox.select_set(idx - 1)
                self.queue[idx - 1], self.queue[idx] = self.queue[idx], self.queue[idx - 1]

    def move_down(self):
        pos = list(self.listbox.curselection())
        if pos and pos[-1] < self.listbox.size() - 1:
            for idx in reversed(pos):
                t = self.listbox.get(idx); self.listbox.delete(idx); self.listbox.insert(idx + 1, t); self.listbox.select_set(idx + 1)
                self.queue[idx + 1], self.queue[idx] = self.queue[idx], self.queue[idx + 1]

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
            del self.queue[i]

    def stop_process(self):
        if messagebox.askyesno("STOP", "Opravdu zastavit kódování?"):
            self.stop_requested = True
            if self.current_process:
                self.current_process.terminate()

    def start_thread(self):
        if not self.queue:
            return
        self.is_running = True
        self.stop_requested = False
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.set_ui_state(False)
        self._run_started = time.monotonic()
        threading.Thread(target=self.run_process, daemon=True).start()

    # --- Spuštění ffmpeg příkazu se sledováním průběhu (společné pro mix/analýzu/kódování) ---
    @staticmethod
    def _ffmpeg_env():
        """Prostředí pro ffmpeg subprocess. Na Linuxu s hybridní grafikou (Intel + NVIDIA) v
        režimu "On-Demand" (NVIDIA Prime) je NVIDIA karta bez explicitního "probuzení" pro CUDA
        neviditelná - ffmpeg pak na hevc_nvenc/h264_nvenc padá s
        "CUDA_ERROR_NO_DEVICE: no CUDA-capable device is detected", i když je ovladač v pořádku
        nainstalovaný. Nastavení těchto dvou proměnných je stejný mechanismus jako `prime-run
        <příkaz>` - řekne ovladači, ať pro tento konkrétní proces kartu použije. Neškodí to na
        systémech bez hybridní grafiky (jedna GPU, čistě Intel/AMD apod.) - ffmpeg proměnné
        prostě ignoruje. Na Windows se nic nemění (setdefault by tam bylo jen mrtvé proměnné)."""
        if sys.platform == "win32":
            return None
        env = os.environ.copy()
        env.setdefault("__NV_PRIME_RENDER_OFFLOAD", "1")
        env.setdefault("__GLX_VENDOR_LIBRARY_NAME", "nvidia")
        return env

    def _run_tracked_ffmpeg(self, cmd, duration, filename, done_count, total_files,
                             frac_base, frac_span, pass_label, log_tag="ffmpeg"):
        """Spustí ffmpeg cmd, čte stdout, hlásí „Invalid timestamps“ souhrnně (běžné
        u MPEG-TS zdrojů), průběžně aktualizuje progress bary/popisky podle 'time='.
        Vrací (returncode, plný výstup jako text, počet „Invalid timestamps“ hlášek).
        returncode je None, pokud proces nešlo spustit nebo byl přerušen uživatelem."""
        # -hide_banner: bez úvodního banneru (verze, "configuration: ...", verze knihoven). Banner
        # je k ničemu a řádek configuration navíc u některých buildů (Packman: "-Werror=...")
        # obsahuje slovo "error" - v logu se pak ukazoval jako chyba i s vypnutými debug zprávami.
        if "-hide_banner" not in cmd:
            cmd = [cmd[0], "-hide_banner"] + list(cmd[1:])
        delays = [2, 5, 10]
        proc = None
        for attempt in range(3):
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS, env=self._ffmpeg_env()
                )
                break
            except PermissionError:
                if attempt < 2:
                    self.log(f"  [{log_tag}] Přístup odepřen, čekám {delays[attempt]}s (pokus {attempt + 2}/3)...", debug=True)
                    time.sleep(delays[attempt])
                else:
                    self.log(f"  [{log_tag}] CHYBA oprávnění — zkus spustit aplikaci jako správce.", error=True)
                    return None, "", 0
            except FileNotFoundError:
                self.log(f"  [{log_tag}] CHYBA: ffmpeg nenalezen na cestě: {self.cfg['ffmpeg_path']}", error=True)
                return None, "", 0

        self.current_process = proc
        output_lines = []
        invalid_ts_count = 0
        pass_started = time.monotonic()
        try:
            for line in proc.stdout:
                if self.stop_requested:
                    proc.terminate()
                    return None, "".join(output_lines), invalid_ts_count
                output_lines.append(line)
                stripped = line.rstrip()
                is_banner = stripped.lstrip().startswith(("configuration:", "built with", "ffmpeg version",
                                                          "libav", "libsw", "libpostproc"))
                if is_banner:
                    self.log(f"  [{log_tag}] {stripped}", debug=True)
                elif "Invalid timestamps" in line:
                    # Běžné u MPEG-TS zdrojů (TV nahrávky apod.) - nejde o chybu, jen by zaplavilo log.
                    invalid_ts_count += 1
                    self.log(f"  [{log_tag}] {stripped}", debug=True)
                elif any(kw in line for kw in ("Error", "error", "Invalid", "No such", "failed", "Cannot",
                                                "Conversion failed", "Driver", "minimum required", "not support",
                                                "warning", "Warning")):
                    self.log(f"  [{log_tag}] {stripped}", error=True)
                match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                if match and duration > 0:
                    h_p, m_p, s_p = map(float, match.groups())
                    perc = min((h_p * 3600 + m_p * 60 + s_p) / duration, 1.0)
                    file_frac = min(frac_base + perc * frac_span, 1.0)
                    self.prog_file.set(file_frac)
                    self.prog_total.set((done_count + file_frac) / total_files)
                    stats = self._progress_stats(line, perc, duration, time.monotonic() - pass_started)
                    self.label_p1.configure(text=f"Aktuální soubor: {filename}")
                    self.label_pass.configure(text=f"{pass_label}: {int(perc * 100)}%{stats}")
                    total_elapsed = time.monotonic() - getattr(self, "_run_started", pass_started)
                    self.label_p2.configure(text=f"Celkově: {int(((done_count + file_frac) / total_files) * 100)}% | "
                                                 f"Zpracovávám {done_count + 1} z {total_files} | "
                                                 f"celkem běží {fmt_duration(total_elapsed)}")
                    self.update()
        except Exception as e:
            self.log(f"  [{log_tag}] CHYBA: {type(e).__name__}: {e}", debug=True)
        proc.wait()
        self.current_process = None
        if invalid_ts_count:
            self.log(f"  [{log_tag}] Info: ffmpeg nahlásil {invalid_ts_count}x „Invalid timestamps“ "
                     f"(běžné u MPEG-TS zdrojů, zpravidla neškodné - zapni „debug zprávy“ pro detail).")
        return proc.returncode, "".join(output_lines), invalid_ts_count

    @staticmethod
    def _progress_stats(line, perc, duration, elapsed):
        """Z řádku průběhu ffmpeg ("frame= 1234 fps=172 ... time=... speed=6.9x") sestaví doplněk
        popisku: rychlost kódování (snímky/s - jen u videa), násobek reálného času, uplynulý a
        odhad zbývajícího času aktuálního průchodu. Zbývající čas se počítá z "speed" (stabilnější),
        jinak z poměru uplynulého času a hotové části."""
        parts = []
        fps_m = re.search(r"fps=\s*([\d.]+)", line)
        if fps_m:
            try:
                fps = float(fps_m.group(1))
                if fps > 0:
                    parts.append(f"{fps:.0f} sn/s")
            except ValueError:
                pass
        speed = None
        speed_m = re.search(r"speed=\s*([\d.]+)x", line)
        if speed_m:
            try:
                speed = float(speed_m.group(1))
                if speed > 0:
                    parts.append(f"{speed:.1f}×")
            except ValueError:
                speed = None
        parts.append(f"uplynulo {fmt_duration(elapsed)}")
        remaining = None
        if speed and speed > 0 and duration > 0:
            remaining = max(duration * (1 - perc), 0) / speed
        elif perc > 0.01:
            remaining = elapsed * (1 - perc) / perc
        if remaining is not None and perc < 1.0:
            parts.append(f"zbývá {fmt_duration(remaining)}")
        return " · " + " · ".join(parts)

    # --- PRŮCHOD 1/3 (za každou vybranou audio stopu): down-mix do meziformátu stereo (jen pro účely měření) ---
    def downmix_to_stereo(self, file_path, aidx, tmp_path, duration, filename, done_count, total_files,
                           frac_base, frac_span, pass_label):
        self.log(f"  [mix] {pass_label}: down-mix stopy 0:{aidx} na stereo (pro přesné měření hlasitosti)...")
        cmd = [self.cfg["ffmpeg_path"],
               "-fflags", "+discardcorrupt+genpts",
               "-err_detect", "ignore_err",
               "-analyzeduration", "20000000",
               "-probesize", "20000000",
               "-i", file_path,
               "-map", f"0:{aidx}",
               "-af", "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                      "aresample=async=1000:min_hard_comp=0.100000:first_pts=0",
               "-c:a", "flac",
               "-y", tmp_path]
        rc, _, _ = self._run_tracked_ffmpeg(cmd, duration, filename, done_count, total_files,
                                             frac_base, frac_span, pass_label, log_tag="mix")
        return rc == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0

    # --- PRŮCHOD 2/3 (za každou vybranou audio stopu): analýza hlasitosti (loudnorm) na už zmixovaném stereu ---
    def analyze_loudnorm(self, tmp_path, duration, filename, done_count, total_files,
                          frac_base, frac_span, pass_label):
        self.log(f"  [analýza] {pass_label}: analýza hlasitosti (loudnorm)...")
        ln = self.cfg["loudnorm"]
        cmd = [self.cfg["ffmpeg_path"], "-i", tmp_path,
               "-af", f"loudnorm=I={ln['I']}:TP={ln['TP']}:LRA={ln['LRA']}:print_format=json",
               "-f", "null", "-"]
        rc, output, _ = self._run_tracked_ffmpeg(cmd, duration, filename, done_count, total_files,
                                                  frac_base, frac_span, pass_label, log_tag="analýza")
        if rc is None:
            return None
        json_match = re.search(r'\{[^{}]*"input_i"[^{}]*\}', output, re.DOTALL)
        if json_match:
            stats = json.loads(json_match.group(0))
            self.log(f"  [analýza] Naměřeno: I={stats.get('input_i')} LUFS, LRA={stats.get('input_lra')} LU, TP={stats.get('input_tp')} dBTP")
            return stats
        self.log("  [analýza] VAROVÁNÍ: loudnorm statistiky nenalezeny, použiji 1-průchodový fallback.")
        return None

    # --- NFO ---
    def create_nfo(self, item):
        if not self.cfg.get("generate_nfo", True):
            return None
        if not item.get("generate_nfo", True):
            return None
        base_name = os.path.splitext(item["filename"])[0]
        nfo_path = os.path.join(self.cfg["output_dir"], base_name + ".nfo")
        nfo = item["nfo"]
        try:
            with open(nfo_path, "w", encoding="utf-8") as f:
                f.write('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n')
                f.write('<movie>\n')
                f.write(f'    <title>{nfo.get("title", "")}</title>\n')
                f.write(f'    <year>{nfo.get("year", "")}</year>\n')
                f.write(f'    <plot>{nfo.get("plot", "")}</plot>\n')
                f.write(f'    <genre>{nfo.get("genre", "")}</genre>\n')
                f.write('</movie>')
            return os.path.basename(nfo_path)
        except Exception:
            return None

    def _cut_for_queue(self, item, segments, done_count, total_files, total_passes, label):
        """Střih položky fronty do mezisouboru MKV. Vrací (cesta, složka, a_sel, s_sel, v_idx) s indexy stop
        v mezisouboru, None (přerušeno) nebo False (chyba)."""
        cfg = self.cfg
        src = item["path"]
        info, err = probe_cut_info(cfg["ffprobe_path"], src)
        if info is None:
            self.log(f"  [střih] CHYBA: {err}", error=True)
            return False
        self.label_pass.configure(text=f"{label}: indexuji snímky…")
        idx, err = build_frame_index(cfg["ffprobe_path"], src, info["start_time"], info["duration"],
                                     stop=lambda: self.stop_requested, env=self._ffmpeg_env())
        if idx is None:
            if self.stop_requested:
                return None
            self.log(f"  [střih] CHYBA indexu: {err}", error=True)
            return False
        # Mezisoubor vedle výstupu (ne v /tmp - bývá v RAM a celý film se tam nevejde)
        cut_dir = tempfile.mkdtemp(prefix=".ffm_strih_", dir=cfg["output_dir"])
        # TS zdroj -> TS mezisoubor (aac_latm, teletext a DVB titulky MKV neunese)
        cut_path = os.path.join(cut_dir, "strih.ts" if os.path.splitext(src)[1].lower() in TS_EXTENSIONS else "strih.mkv")
        a_sel, s_sel = list(item["selected_audio"]), list(item["selected_subs"])
        total = max(segments_duration(segments), 0.1) * 3
        state = {"done": 0.0}

        def run(cmd, dur, plabel):
            span = (max(dur, 0) / total) / total_passes
            base = (state["done"] / total) / total_passes
            rc, _, _ = self._run_tracked_ffmpeg(cmd, max(dur, 0.001), item["filename"], done_count, total_files,
                                                base, span, f"{label} – {plabel}", log_tag="střih")
            state["done"] += max(dur, 0)
            return rc

        sc = SmartCutter(cfg["ffmpeg_path"], cfg["ffprobe_path"], src, info, idx, run,
                         lambda t, error=False, debug=False: self.log(t, error=error, debug=debug),
                         env=self._ffmpeg_env(), any_codec=True)
        ok, why = sc.execute(segments, cut_path, cut_dir, audio=a_sel, subs=s_sel)
        if not ok:
            shutil.rmtree(cut_dir, ignore_errors=True)
            if ok is None:
                return None
            self.log(f"  [střih] CHYBA: {why}", error=True)
            return False
        # Indexy stop v mezisouboru: 0 = video, pak vybrané audio/titulky v pořadí originálu
        order = [str(st.get("index")) for st in info["streams"]
                 if (st.get("codec_type") == "audio" and str(st.get("index")) in a_sel)
                 or (st.get("codec_type") == "subtitle" and str(st.get("index")) in s_sel)]
        remap = {old: str(n + 1) for n, old in enumerate(order)}
        return cut_path, cut_dir, [remap[i] for i in a_sel if i in remap], [remap[i] for i in s_sel if i in remap], "0"

    # --- ZPRACOVÁNÍ FRONTY ---
    def run_process(self):
        total_files = len(self.queue)
        done_count = 0
        cfg = self.cfg
        os.makedirs(cfg["output_dir"], exist_ok=True)
        os.makedirs(cfg["done_dir"], exist_ok=True)

        while self.queue and not self.stop_requested:
            item = self.queue[0]
            file_path = item["path"]
            filename = item["filename"]
            duration = item["duration"]
            v_idx = item["video"]["index"]
            a_sel = item["selected_audio"]
            s_sel = item["selected_subs"]

            self.log(f"\n>>> SPUŠTĚNO: {filename}")

            out_file = os.path.join(cfg["output_dir"], os.path.splitext(filename)[0] + ".mkv")
            src_path = file_path       # originál - s ním se po dokončení manipuluje (přesun/přejmenování)
            segments = item.get("segments") or []

            ln = cfg["loudnorm"]
            base_loudnorm = f"I={ln['I']}:TP={ln['TP']}:LRA={ln['LRA']}"
            n_tracks = len(a_sel)
            total_passes = 2 * n_tracks + 1 + (1 if segments else 0)  # (mix + analýza) za stopu, kódování, (+ střih)
            pass_no = 0
            tmp_dir = tempfile.mkdtemp(prefix="ffmpegmaster_")
            cut_dir = None
            track_af_audio = []  # af_audio (loudnorm řetězec) pro každou vybranou stopu, ve stejném pořadí jako a_sel
            rc = None

            try:
                if segments:
                    # --- Průchod 0: střih - úseky do mezisouboru (smart cut: video kopie + překódované okraje,
                    #     zvuk/titulky kopie). Dál se zpracovává mezisoubor, jako by to byl zdroj.
                    pass_no += 1
                    label_cut = f"Průchod {pass_no}/{total_passes} (střih)"
                    self.log(f"  [střih] {label_cut}: {len(segments)} úsek(ů), výsledná délka {fmt_tc(segments_duration(segments))}")
                    cut_res = self._cut_for_queue(item, segments, done_count, total_files, total_passes, label_cut)
                    if cut_res is None:
                        raise _QueueItemFailed(None)
                    if cut_res is False:
                        raise _QueueItemFailed(1)
                    file_path, cut_dir, a_sel, s_sel, v_idx = cut_res
                    duration = segments_duration(segments)
                    item = dict(item, segments=[], duration=duration)

                # --- Průchody 1-2 pro každou vybranou audio stopu: down-mix na stereo do dočasného souboru,
                #     jen proto, abychom na něm mohli přesně změřit hlasitost (stejný signál, jaký nakonec uslyší
                #     posluchač po down-mixu). Samotné finální audio se ale kóduje přímo z originálu (viz níže) -
                #     kdybychom zvuk brali z odděleného souboru, ztratily by se jeho původní časové značky a mohl
                #     by se rozjet vůči videu (u zdrojů s nespojitými timestampy, typicky TV .ts nahrávky).
                for i, aidx in enumerate(a_sel):
                    tmp_path = os.path.join(tmp_dir, f"track{i}.flac")

                    pass_no += 1
                    label_mix = f"Průchod {pass_no}/{total_passes} (mix stopy {i + 1}/{n_tracks} → stereo)"
                    ok = self.downmix_to_stereo(file_path, aidx, tmp_path, duration, filename, done_count, total_files,
                                                 (pass_no - 1) / total_passes, 1 / total_passes, label_mix)

                    pass_no += 1
                    stats = None
                    if ok:
                        label_measure = f"Průchod {pass_no}/{total_passes} (analýza hlasitosti stopy {i + 1}/{n_tracks})"
                        stats = self.analyze_loudnorm(tmp_path, duration, filename, done_count, total_files,
                                                       (pass_no - 1) / total_passes, 1 / total_passes, label_measure)
                    else:
                        self.log(f"  [mix] VAROVÁNÍ: down-mix stopy 0:{aidx} na stereo selhal, "
                                  f"pro tuto stopu se použije 1-průchodový loudnorm.", error=True)

                    if stats and stats.get("input_i") not in (None, "-inf", "inf", "-inf "):
                        af_audio = (
                            f"loudnorm={base_loudnorm}:linear=true"
                            f":measured_I={stats['input_i']}"
                            f":measured_LRA={stats['input_lra']}"
                            f":measured_TP={stats['input_tp']}"
                            f":measured_thresh={stats['input_thresh']}"
                            f":offset={stats['target_offset']}"
                        )
                    else:
                        af_audio = f"loudnorm={base_loudnorm}"
                        if ok:
                            self.log("  [analýza] Fallback: použit standardní 1-průchodový loudnorm.")

                    track_af_audio.append(af_audio)

                # --- Průchod 3/3: finální kódování - video i audio se berou přímo z originálu (kvůli synchronizaci),
                #     audio se jen normalizuje podle hodnot naměřených výše. ---
                pass_no += 1
                s_map = []
                for sidx in s_sel:
                    s_map += ["-map", f"0:{sidx}"]

                copy_video = item.get("copy_video", False)
                crop = item.get("crop")
                crop_vf = ""
                crop_bsf_args = []
                if crop:
                    vinfo = item["video"]
                    if (crop.get("src_w"), crop.get("src_h")) != (vinfo.get("width"), vinfo.get("height")):
                        self.log(f"  [ořez] VAROVÁNÍ: ořez je pro rozlišení {crop.get('src_w')}x{crop.get('src_h')}, "
                                 f"zdroj má {vinfo.get('res')} - ořez se přeskočí.", error=True)
                    elif copy_video:
                        bsf = crop_bitstream_filter(crop, vinfo)
                        if bsf:
                            crop_bsf_args = ["-bsf:v", bsf]
                            self.log(f"  [ořez] {crop_describe(crop)} - zápis do streamu bez překódování ({bsf}).")
                        else:
                            self.log(f"  [ořez] VAROVÁNÍ: při kopírování videa jde ořez jen u H.264/HEVC, "
                                     f"zdroj je {vinfo.get('codec_name')} - ořez se přeskočí.", error=True)
                    else:
                        crop_vf = crop_filter(crop) + ","
                        self.log(f"  [ořez] {crop_describe(crop)}.")
                fc_parts = []
                fc_out_maps = []
                direct_maps = []

                if copy_video:
                    # Video se nedekóduje ani nefiltruje, jen se přímo namapuje a níže vynutí '-c:v copy'.
                    direct_maps += ["-map", f"0:{v_idx}"]
                    self.log("  [video] Kopírování beze změny (bez dekódování/zmenšení).", debug=True)
                else:
                    vw, vh = cfg["video"]["max_width"], cfg["video"]["max_height"]
                    fc_parts.append(f"[0:{v_idx}]{crop_vf}setparams=color_trc=bt709:colorspace=bt709:color_primaries=bt709:range=tv,format=yuv420p,"
                                     f"scale={vw}:{vh}:force_original_aspect_ratio=decrease:flags=lanczos,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p10le[vout]")
                    fc_out_maps += ["-map", "[vout]"]

                for i, aidx in enumerate(a_sel):
                    lbl = f"aout{i}"
                    af_audio = track_af_audio[i]
                    fc_parts.append(f"[0:{aidx}]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
                                     f"aresample=async=1000:min_hard_comp=0.100000,{af_audio},"
                                     f"aformat=sample_fmts=fltp:sample_rates=48000[{lbl}]")
                    fc_out_maps += ["-map", f"[{lbl}]"]

                if cfg.get("use_custom_encode_args") and cfg.get("custom_encode_args", "").strip():
                    try:
                        encode_args = shlex.split(cfg["custom_encode_args"], posix=(sys.platform != "win32"))
                        if not encode_args:
                            raise ValueError("prázdný příkaz")
                        self.log("  [encode] Používám vlastní FFmpeg parametry z Nastavení.", debug=True)
                    except ValueError as e:
                        self.log(f"  [encode] CHYBA ve vlastních FFmpeg parametrech ({e}), použiji výchozí nastavení.", error=True)
                        encode_args = default_encode_args(cfg)
                else:
                    encode_args = default_encode_args(cfg)

                if copy_video:
                    encode_args = force_copy_video(encode_args)

                filter_args = ["-filter_complex", ";".join(fc_parts)] if fc_parts else []

                cmd = [cfg["ffmpeg_path"],
                       "-fflags", "+discardcorrupt+genpts",
                       "-err_detect", "ignore_err",
                       "-max_error_rate", "1.0",
                       "-analyzeduration", "20000000",
                       "-probesize", "20000000",
                       "-i", file_path,
                       ] + filter_args + direct_maps + fc_out_maps + s_map + encode_args + crop_bsf_args + \
                      ["-max_interleave_delta", "0", out_file, "-y"]

                self.log(f"  [CMD] {' '.join(cmd)}", debug=True)
                label_encode = f"Průchod {pass_no}/{total_passes} (kódování)"
                self.log(f"  [kódování] {label_encode}...")
                rc, _, _ = self._run_tracked_ffmpeg(cmd, duration, filename, done_count, total_files,
                                                     (pass_no - 1) / total_passes, 1 / total_passes, label_encode,
                                                     log_tag="ffmpeg")
            except _QueueItemFailed as e:
                rc = e.rc
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                if cut_dir:
                    shutil.rmtree(cut_dir, ignore_errors=True)
            file_path = src_path

            if rc is None and not self.stop_requested:
                # ffmpeg se nepodařilo spustit (viz chybová hláška výše) - dál nemá smysl pokračovat.
                break

            self.log(f"  [returncode] {rc}", debug=True)

            if rc == 0 and not self.stop_requested and crop_bsf_args:
                expect = "x".join(map(str, crop_size(crop)))
                got, _ = probe_file(out_file, cfg["ffprobe_path"])
                if got and got["video"]["res"] != expect:
                    self.log(f"  [ořez] VAROVÁNÍ: výstup má {got['video']['res']} místo {expect} - "
                             f"zkontroluj ho (ořez zapsaný do streamu nesedí).", error=True)

            if rc == 0 and not self.stop_requested:
                nfo_file = self.create_nfo(item)
                if nfo_file:
                    self.log(f"  NFO:     Soubor {nfo_file} byl úspěšně vygenerován.")

                status_msg = ""
                is_net = is_network_path(file_path)
                src_kind = "síťový" if is_net else "lokální"
                action = cfg.get("source_action_network" if is_net else "source_action_local", "move")
                try:
                    if action == "none":
                        status_msg = f"  STATUS:  Zdrojový soubor ({src_kind} disk) ponechán beze změny."
                    elif action == "rename":
                        ext = os.path.splitext(filename)[1]
                        new_name_path = os.path.join(os.path.dirname(file_path), fit_filename(os.path.splitext(filename)[0], "_hotovo" + ext))
                        os.rename(file_path, new_name_path)
                        status_msg = f"  STATUS:  Soubor ({src_kind} disk) ponechán na místě a přejmenován."
                    else:  # move
                        shutil.move(file_path, os.path.join(cfg["done_dir"], filename))
                        status_msg = f"  STATUS:  Soubor ({src_kind} disk) přesunut do složky {os.path.basename(cfg['done_dir'])}"
                except Exception as e:
                    status_msg = f"  STATUS:  Chyba při manipulaci: {e}"

                self.log(status_msg)
                self.log(f"--- HOTOVO: {filename} ---")
                self.listbox.delete(0)
                del self.queue[0]
                done_count += 1
            else:
                self.current_process = None
                time.sleep(0.5)
                if os.path.exists(out_file):
                    try:
                        os.remove(out_file)
                    except Exception:
                        pass
                if self.stop_requested:
                    self.log(">>> PŘERUŠENO UŽIVATELEM.")
                    break
                else:
                    self.log(f"!!! CHYBA: Kódování souboru {filename} selhalo (returncode {rc}). "
                              f"Zdrojový soubor zůstává beze změny, přeskakuji na další v pořadí.", error=True)
                    self.listbox.delete(0)
                    del self.queue[0]
                    done_count += 1

        self.after(0, self.reset_ui)

    def reset_ui(self):
        self.run_completed = True
        self.is_running = False
        self.stop_requested = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.set_ui_state(True)
        self.prog_file.set(0)
        self.prog_total.set(0)
        self.label_p1.configure(text="Aktuální soubor: ")
        self.label_pass.configure(text="")
        self.label_p2.configure(text="Celkový postup: 0/0")
        if not self.stop_requested:
            self.log("\n--- VŠE DOKONČENO ---")
        print('\a')

    def on_close(self):
        if self.is_running:
            if not messagebox.askyesno("Zavřít", "Probíhá zpracování. Opravdu chceš ukončit program?"):
                return
            self.stop_requested = True
            if self.current_process:
                self.current_process.terminate()
        try:
            self.cfg["window_geometry"] = self.geometry()
            save_config(self.cfg)
        except Exception:
            pass
        self.destroy()


def run_selftest(args):
    """Kontrola bez GUI pro CI (a pro ruční diagnostiku): `ffmpeg-master --selftest [video]`.

    Ověří, že binárka na dané distribuci vůbec naběhne (Python + zabalený Tk), že spuštěné
    systémové programy nedědí zabalené knihovny (LD_LIBRARY_PATH) a že ffmpeg/ffprobe z configu
    (nebo výchozí z PATH) jde spustit. Volitelně rovnou zanalyzuje zadané video stejnou cestou
    jako přidání do fronty. Nic nezapisuje (config.json nevytváří). Návratový kód 0 = vše OK."""
    import tkinter
    ok = True

    def report(label, passed, detail=""):
        nonlocal ok
        ok = ok and passed
        print(f"[{'OK' if passed else 'CHYBA'}] {label}" + (f": {detail}" if detail else ""))

    print(f"FFMPEG Master {VERSION} - selftest")
    run_mode = linux_run_mode() if sys.platform != "win32" else "windows"
    print(f"  platforma: {sys.platform}, režim: {run_mode}, Python {sys.version.split()[0]}, Tk {tkinter.TkVersion}")
    print(f"  config: {CONFIG_PATH}{'' if os.path.exists(CONFIG_PATH) else ' (neexistuje, použijí se výchozí hodnoty)'}")

    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    meipass = getattr(sys, "_MEIPASS", None)
    leaked = bool(meipass) and meipass in ld_path
    report("prostředí pro systémové programy", not leaked,
           f"LD_LIBRARY_PATH stále obsahuje {meipass}" if leaked else "bez zabalených knihoven")

    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            report("čtení config.json", False, str(e))

    for key in ("ffmpeg_path", "ffprobe_path"):
        exe = cfg.get(key) or ""
        try:
            res = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=30, creationflags=SUBPROCESS_FLAGS)
            first = (res.stdout or "").splitlines()[0] if res.stdout else ""
            report(f"{key} ({exe})", res.returncode == 0,
                   first if res.returncode == 0 else f"kód {res.returncode}: {_short_stderr(res.stderr)}")
        except FileNotFoundError:
            report(f"{key} ({exe})", False, "nenalezen")
        except Exception as e:
            report(f"{key} ({exe})", False, str(e))

    try:
        res = subprocess.run([cfg.get("ffmpeg_path") or "ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=30, creationflags=SUBPROCESS_FLAGS)
        nvenc = sorted(set(re.findall(r"\b(\w+_nvenc)\b", res.stdout or "")))
        print(f"  NVENC enkodéry v ffmpeg: {', '.join(nvenc) if nvenc else 'žádné (jen informativní)'}")
    except Exception:
        pass

    if sys.platform == "win32" or os.environ.get("DISPLAY"):
        # Skutečné načtení GUI knihoven: Tk + tkdnd (drag&drop) + písma přes systémový fontconfig.
        # Tady se projeví konflikt zabalených a systémových knihoven písem (viz spec soubor).
        try:
            import tkinter.font as tkfont
            root = TkinterDnD.Tk()
            root.withdraw()
            font = tkfont.Font(root=root, family="Arial", size=11)
            width = font.measure("FFMPEG Master")
            family = font.actual("family")
            root.destroy()
            report("GUI knihovny (Tk, drag&drop, písma)", width > 0, f"písmo '{family}'")
        except Exception as e:
            report("GUI knihovny (Tk, drag&drop, písma)", False, str(e))
    else:
        print("  GUI test přeskočen (není nastavený DISPLAY)")

    # HTTPS na GitHub (kontrola aktualizací) - ověří, že se najdou systémové CA certifikáty.
    # Chyba certifikátu = CHYBA; nedostupná síť jen informativně (selftest jde spustit i offline).
    try:
        import ssl
        req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                     headers={"User-Agent": "FFMPEG-Master-Selftest"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            report("HTTPS (kontrola aktualizací)", True,
                   f"GitHub odpověděl {resp.status}, certifikáty: {os.environ.get('SSL_CERT_FILE') or ssl.get_default_verify_paths().cafile}")
    except urllib.error.HTTPError as e:
        report("HTTPS (kontrola aktualizací)", True, f"spojení i certifikát OK (GitHub vrátil HTTP {e.code})")
    except Exception as e:
        reason = getattr(e, "reason", e)
        if "CERTIFICATE" in str(reason).upper() or "SSL" in type(reason).__name__.upper():
            report("HTTPS (kontrola aktualizací)", False, f"chyba certifikátu: {reason}")
        else:
            print(f"  HTTPS test přeskočen (síť nedostupná: {reason})")

    # Formát fronty .ffm: uložit -> načíst -> stejné nastavení (bez GUI, jen dočasné soubory).
    try:
        with tempfile.TemporaryDirectory(prefix="ffmselftest_") as td:
            src = os.path.join(td, "video ž.mkv")
            open(src, "wb").write(b"0" * 1234)
            item = {"path": src, "filename": "video ž.mkv", "duration": 60.0,
                    "selected_audio": ["1"], "selected_subs": ["3"], "nfo": default_nfo("video ž.mkv"),
                    "generate_nfo": False, "copy_video": True, "crop": None,
                    "segments": [{"start": 1.0, "end": 2.0}]}
            ffm = os.path.join(td, "fronta.ffm")
            save_queue_file(ffm, [item])
            back = load_queue_file(ffm)
            same = (len(back) == 1 and back[0]["_resolved"] == os.path.normpath(src) and back[0]["size"] == 1234
                    and all(back[0].get(k) == item[k] for k in QUEUE_ITEM_SETTINGS))
            report("soubor fronty .ffm (uložení/načtení)", same)
    except Exception as e:
        report("soubor fronty .ffm (uložení/načtení)", False, str(e))

    for video_path in args:
        info, err = probe_file(video_path, cfg.get("ffprobe_path") or "ffprobe")
        if info:
            report(f"analýza {os.path.basename(video_path)}", True,
                   f"{info['video']['codec_name']} {info['video']['res']}, {fmt_duration(info['duration'])}, "
                   f"audio {len(info['audio_streams'])}, titulky {len(info['sub_streams'])}")
        else:
            report(f"analýza {os.path.basename(video_path)}", False, err)

    print("VÝSLEDEK: " + ("OK" if ok else "CHYBA"))
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(run_selftest([a for a in sys.argv[1:] if a != "--selftest"]))
    # Splash animace i skutečné UI se sestaví uvnitř App.__init__ (viz _show_splash()/
    # _build_main_ui() výše) - jde pořád o to samé okno, nic se tu už neschovává/neodkrývá.
    app = App()
    for arg in sys.argv[1:]:
        if arg.lower().endswith(QUEUE_FILE_EXT) and os.path.isfile(arg):
            app.after(300, lambda p=os.path.abspath(arg): app.load_queue_from(p))
    app.mainloop()
