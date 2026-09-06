import os
import sys
import subprocess
import json
import threading
import shutil
import re
import time
import copy
import shlex
import tempfile
import webbrowser
import urllib.request
import urllib.error
import ctypes
import customtkinter as ctk
from tkinter import messagebox, Listbox, filedialog, EXTENDED, Menu
from tkinterdnd2 import DND_FILES, TkinterDnD

VERSION = "v0.6.0"
GITHUB_REPO = "DaTTcz/FFMPEG-Master"

# --- OPRAVA IKONY V LIŠTĚ WINDOWS ---
try:
    myappid = f'moje.ffmpeg.master.{VERSION}'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception:
    pass


def resource_path(relative_path):
    """Cesta k souborům zabaleným uvnitř exe (ikony, obrázky) - funguje v .py i ve zmrazeném PyInstaller exe."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def base_dir():
    """Adresář vedle .exe (frozen) nebo vedle .pyw (dev) - sem patří config.json, aby šel editovat i po zabalení."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


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

DEFAULT_CONFIG = {
    "ffmpeg_path": r"C:\FFMPEG\bin\ffmpeg.exe",
    "ffprobe_path": r"C:\FFMPEG\bin\ffprobe.exe",
    "output_dir": r"C:\!VIDEO\VIDEO_OUT\OUTPUT",
    "done_dir": r"C:\!VIDEO\VIDEO_OUT\HOTOVO",
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
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


# ==========================================================================
#  PROBE (ffprobe) - detailní info o stopách pro dialog výběru
# ==========================================================================

PGS_CODECS = ("hdmv_pgs_subtitle", "pgssub")


def probe_file(file_path, ffprobe_path):
    """Vrátí dict s duration, video info a detailním seznamem audio/titulkových stop."""
    cmd = [ffprobe_path, "-v", "error",
           "-analyzeduration", "20000000", "-probesize", "20000000",
           "-show_entries",
           "format=duration:stream=index,codec_type,codec_name,width,height,channels,channel_layout,bit_rate:stream_tags=language,title",
           "-of", "json", file_path]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=SUBPROCESS_FLAGS)
        data = json.loads(res.stdout)
    except Exception:
        return None

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
                      "res": f"{s.get('width')}x{s.get('height')}"}
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
        return None
    return {"duration": duration, "video": video, "audio_streams": audio_streams, "sub_streams": sub_streams}


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


def is_network_path(path):
    """True pro UNC cesty (\\\\server\\share\\...) i pro mapované síťové jednotky (Z:\\...)."""
    try:
        abspath = os.path.abspath(path)
        if abspath.startswith("\\\\") or abspath.startswith("//"):
            return True
        if sys.platform == "win32":
            drive = os.path.splitdrive(abspath)[0]
            if drive:
                return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == DRIVE_REMOTE
        return False
    except Exception:
        return False


# ==========================================================================
#  SPLASH SCREEN
# ==========================================================================

class SplashScreen(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.overrideredirect(True)
        w, h = 450, 380
        x = (self.winfo_screenwidth() // 2) - (w // 2)
        y = (self.winfo_screenheight() // 2) - (h // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.configure(fg_color="#1a1a1a")

        logo_loaded = False
        if HAS_PILLOW:
            try:
                img_path = resource_path("favicon.png")
                if os.path.exists(img_path):
                    img_raw = Image.open(img_path)
                    logo_img = ctk.CTkImage(light_image=img_raw, dark_image=img_raw, size=(160, 160))
                    self.logo_label = ctk.CTkLabel(self, image=logo_img, text="")
                    self.logo_label.pack(pady=(30, 10))
                    logo_loaded = True
            except Exception:
                pass

        if not logo_loaded:
            self.logo_label = ctk.CTkLabel(self, text=f"FFMPEG Master {VERSION}", font=("Arial", 32, "bold"), text_color="#1f538d")
            self.logo_label.pack(pady=(60, 20))

        ctk.CTkLabel(self, text=f"FFMPEG Master {VERSION}", font=("Arial", 16, "bold"), text_color="#28a745").pack(pady=(5, 0))
        ctk.CTkLabel(self, text="©2026 David Trubka", font=("Arial", 10, "italic"), text_color="#2871a7").pack(pady=(0, 5))
        self.label_status = ctk.CTkLabel(self, text="Inicializace...", font=("Arial", 11, "italic"), text_color="gray")
        self.label_status.pack(pady=(20, 0))
        self.prog = ctk.CTkProgressBar(self, width=350, height=4, progress_color="#1f538d")
        self.prog.pack(pady=20)
        self.prog.set(0)

    def run_progress(self):
        quotes = ["Inicializace kodeků...", "Detekce CUDA jader...", "Příprava FFmpeg...", "Optimalizace procesů...", "Vše je připraveno!"]
        for i in range(1, 101):
            self.prog.set(i / 100)
            idx = min((i - 1) * len(quotes) // 100, len(quotes) - 1)
            self.label_status.configure(text=quotes[idx])
            self.update()
            time.sleep(0.06)
        self.destroy()


# ==========================================================================
#  DIALOG: VÝBĚR STOP (+ NFO metadata) PŘI PŘIDÁNÍ SOUBORU
# ==========================================================================

class TrackSelectionDialog(ctk.CTkToplevel):
    def __init__(self, parent, filename, probe, cfg, mode="add", initial=None):
        """mode: "add" (soubor se přidává do fronty) nebo "edit" (úprava už zařazeného souboru).
        initial: při "edit" dict {"selected_audio", "selected_subs", "nfo"} s aktuálním stavem položky."""
        super().__init__(parent)
        self.mode = mode
        self.title(f"{'Úprava výběru' if mode == 'edit' else 'Výběr stop'} – {filename}")
        self.geometry("820x640")
        self.minsize(700, 500)
        self.transient(parent)
        self.grab_set()
        self.resizable(True, True)

        self.probe = probe
        self.cfg = cfg
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
            "copy_video": self.copy_video_var.get()
        }
        self.grab_release()
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
        self.e_ffmpeg = path_row("FFmpeg.exe", cfg["ffmpeg_path"])
        self.e_ffprobe = path_row("FFprobe.exe", cfg["ffprobe_path"])
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
        self.grab_set()
        self.resizable(False, False)

        if HAS_PILLOW:
            try:
                img_path = resource_path("favicon.png")
                if os.path.exists(img_path):
                    img_raw = Image.open(img_path)
                    logo_img = ctk.CTkImage(light_image=img_raw, dark_image=img_raw, size=(96, 96))
                    ctk.CTkLabel(self, image=logo_img, text="").pack(pady=(20, 8))
            except Exception:
                pass

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

class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        self.cfg = load_config()
        ctk.set_appearance_mode(self.cfg.get("theme", "dark"))

        self.title(f"FFMPEG Master {VERSION}")
        self.geometry(self.cfg.get("window_geometry", "750x740"))

        try:
            self.iconbitmap(resource_path("favicon.ico"))
        except Exception:
            pass

        self.is_running = False
        self.stop_requested = False
        self.current_process = None
        self.log_entries = []  # (text, is_debug, is_error)
        self.run_completed = False
        self.queue = []  # list of dict - viz add_file()

        self._build_menu()

        # --- UI ---
        ctk.CTkLabel(self, text=f"FFMPEG Master {VERSION}", font=("Arial", 22, "bold")).pack(pady=10)

        self.top_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.top_frame.pack(padx=20, pady=5, fill="x")

        self.drop_frame = ctk.CTkFrame(self.top_frame, height=80, border_width=2, border_color="#1f538d")
        self.drop_frame.pack(side="left", fill="x", expand=True)
        self.drop_frame.pack_propagate(False)
        self.drop_label = ctk.CTkLabel(self.drop_frame, text="Sem přetáhni video soubory (MKV, MP4, AVI...)")
        self.drop_label.pack(expand=True)
        self.drop_frame.drop_target_register(DND_FILES)
        self.drop_frame.dnd_bind('<<Drop>>', self.handle_drop)

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

        ctk.CTkLabel(self, text="DaTT.cz  © 2026", font=("Arial", 10), text_color="#3a3a3a").pack(side="bottom", pady=(0, 6))

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.cfg.get("check_updates_on_startup", True):
            self.after(1500, lambda: threading.Thread(target=self._startup_update_check, daemon=True).start())

    # --- MENU ---
    def _build_menu(self):
        menubar = Menu(self)
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

    def start_update_download(self, info):
        exe_asset = next((a for a in info["assets"] if (a.get("name") or "").lower().endswith(".exe")), None)
        if not getattr(sys, "frozen", False):
            # Vývojový/skriptový režim - automatická výměna souboru nedává smysl, otevři stránku s vydáním.
            self.log("Aktualizace: běžím jako .pyw skript, otevírám stránku s vydáním v prohlížeči.")
            webbrowser.open(info["html_url"])
            return
        if not exe_asset:
            messagebox.showwarning("Aktualizace", "V nejnovějším vydání nebyl nalezen soubor .exe. Otevírám stránku s vydáním.")
            webbrowser.open(info["html_url"])
            return
        threading.Thread(target=self._download_and_install, args=(exe_asset,), daemon=True).start()

    def _download_and_install(self, asset):
        try:
            update_dir = os.path.join(base_dir(), "update")
            os.makedirs(update_dir, exist_ok=True)
            new_exe = os.path.join(update_dir, asset["name"])
            self.log(f"Stahuji aktualizaci: {asset['name']}...")
            urllib.request.urlretrieve(asset["url"], new_exe)
            self.log("Stažení dokončeno, instaluji...")

            current_exe = sys.executable
            bat_path = os.path.join(update_dir, "update.bat")
            with open(bat_path, "w", encoding="utf-8") as f:
                f.write(f"""@echo off
timeout /t 2 /nobreak >nul
move /y "{new_exe}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
""")
            subprocess.Popen(["cmd", "/c", bat_path], creationflags=DETACHED_FLAGS)
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

    # --- FRONTA SOUBORŮ ---
    def _maybe_clear_log(self):
        if not self.is_running and self.run_completed:
            self.log_entries = []
            self.run_completed = False
            self.textbox.configure(state="normal")
            self.textbox.delete("1.0", "end")

    def add_file(self, path):
        filename = os.path.basename(path)
        probe = probe_file(path, self.cfg["ffprobe_path"])
        if probe is None:
            self.log(f"[CHYBA] Nelze analyzovat soubor (žádné video?): {filename}", error=True)
            return

        dialog = TrackSelectionDialog(self, filename, probe, self.cfg, mode="add")
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
        }
        self.queue.append(item)
        self.listbox.insert("end", filename)

        a_n, s_n = len(item["selected_audio"]), len(item["selected_subs"])
        self.log(f"\n[ PŘIDÁNO: {filename} ]")
        self.log(f"  Video: {probe['video']['res']}, Délka: {fmt_duration(probe['duration'])}")
        self.log(f"  Vybráno: {a_n} audio stopa/y, {s_n} titulková/é stopa/y")
        if item["copy_video"]:
            self.log("  Video:   bez překódování (kopírováno beze změny).")
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
        }

        dialog = TrackSelectionDialog(self, item["filename"], probe, self.cfg, mode="edit", initial=initial)
        self.wait_window(dialog)
        if dialog.result is None:
            return

        item["selected_audio"] = dialog.result["selected_audio"]
        item["selected_subs"] = dialog.result["selected_subs"]
        item["copy_video"] = dialog.result.get("copy_video", False)
        if dialog.nfo_enabled:
            item["nfo"] = dialog.result["nfo"]
            item["generate_nfo"] = dialog.result.get("generate_nfo_for_file", True)

        a_n, s_n = len(item["selected_audio"]), len(item["selected_subs"])
        self.log(f"\n[ UPRAVENO: {item['filename']} ]")
        self.log(f"  Vybráno: {a_n} audio stopa/y, {s_n} titulková/é stopa/y")
        if item["copy_video"]:
            self.log("  Video:   bez překódování (kopírováno beze změny).")
        if self.cfg.get("generate_nfo", True) and not item.get("generate_nfo", True):
            self.log("  NFO:     pro tento soubor vypnuto.")
        self.log("-" * 50)

    def handle_drop(self, event):
        if self.is_running:
            return
        paths = re.findall(r'\{(.*?)\}|(\S+)', event.data)
        valid = [os.path.normpath(x[0] if x[0] else x[1]) for x in paths
                 if (x[0] if x[0] else x[1]).lower().endswith(('.mkv', '.mp4', '.avi', '.mov', '.ts', '.m2ts', '.wmv', '.flv', '.webm', '.m4v'))]
        self._maybe_clear_log()
        for path in valid:
            self.add_file(path)

    def browse_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Video soubory", "*.mkv *.mp4 *.avi *.mov *.ts *.m2ts *.wmv *.flv *.webm *.m4v"), ("Všechny soubory", "*.*")])
        if not files:
            return
        self._maybe_clear_log()
        for f in files:
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
        threading.Thread(target=self.run_process, daemon=True).start()

    # --- Spuštění ffmpeg příkazu se sledováním průběhu (společné pro mix/analýzu/kódování) ---
    def _run_tracked_ffmpeg(self, cmd, duration, filename, done_count, total_files,
                             frac_base, frac_span, pass_label, log_tag="ffmpeg"):
        """Spustí ffmpeg cmd, čte stdout, hlásí „Invalid timestamps“ souhrnně (běžné
        u MPEG-TS zdrojů), průběžně aktualizuje progress bary/popisky podle 'time='.
        Vrací (returncode, plný výstup jako text, počet „Invalid timestamps“ hlášek).
        returncode je None, pokud proces nešlo spustit nebo byl přerušen uživatelem."""
        delays = [2, 5, 10]
        proc = None
        for attempt in range(3):
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, encoding="utf-8", errors="replace",
                    creationflags=SUBPROCESS_FLAGS
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
        try:
            for line in proc.stdout:
                if self.stop_requested:
                    proc.terminate()
                    return None, "".join(output_lines), invalid_ts_count
                output_lines.append(line)
                stripped = line.rstrip()
                if "Invalid timestamps" in line:
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
                    self.label_p1.configure(text=f"{pass_label}: {int(perc * 100)}% | {filename}")
                    self.label_p2.configure(text=f"Celkově: {int(((done_count + file_frac) / total_files) * 100)}% | Zpracovávám {done_count + 1} z {total_files}")
                    self.update()
        except Exception as e:
            self.log(f"  [{log_tag}] CHYBA: {type(e).__name__}: {e}", debug=True)
        proc.wait()
        self.current_process = None
        if invalid_ts_count:
            self.log(f"  [{log_tag}] Info: ffmpeg nahlásil {invalid_ts_count}x „Invalid timestamps“ "
                     f"(běžné u MPEG-TS zdrojů, zpravidla neškodné - zapni „debug zprávy“ pro detail).")
        return proc.returncode, "".join(output_lines), invalid_ts_count

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

            ln = cfg["loudnorm"]
            base_loudnorm = f"I={ln['I']}:TP={ln['TP']}:LRA={ln['LRA']}"
            n_tracks = len(a_sel)
            total_passes = 2 * n_tracks + 1  # (mix + analýza) za každou stopu, + finální kódování
            pass_no = 0
            tmp_dir = tempfile.mkdtemp(prefix="ffmpegmaster_")
            track_af_audio = []  # af_audio (loudnorm řetězec) pro každou vybranou stopu, ve stejném pořadí jako a_sel
            rc = None

            try:
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
                fc_parts = []
                fc_out_maps = []
                direct_maps = []

                if copy_video:
                    # Video se nedekóduje ani nefiltruje, jen se přímo namapuje a níže vynutí '-c:v copy'.
                    direct_maps += ["-map", f"0:{v_idx}"]
                    self.log("  [video] Kopírování beze změny (bez dekódování/zmenšení).", debug=True)
                else:
                    vw, vh = cfg["video"]["max_width"], cfg["video"]["max_height"]
                    fc_parts.append(f"[0:{v_idx}]setparams=color_trc=bt709:colorspace=bt709:color_primaries=bt709:range=tv,format=yuv420p,"
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
                       ] + filter_args + direct_maps + fc_out_maps + s_map + encode_args + \
                      ["-max_interleave_delta", "0", out_file, "-y"]

                self.log(f"  [CMD] {' '.join(cmd)}", debug=True)
                label_encode = f"Průchod {pass_no}/{total_passes} (kódování)"
                self.log(f"  [kódování] {label_encode}...")
                rc, _, _ = self._run_tracked_ffmpeg(cmd, duration, filename, done_count, total_files,
                                                     (pass_no - 1) / total_passes, 1 / total_passes, label_encode,
                                                     log_tag="ffmpeg")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

            if rc is None and not self.stop_requested:
                # ffmpeg se nepodařilo spustit (viz chybová hláška výše) - dál nemá smysl pokračovat.
                break

            self.log(f"  [returncode] {rc}", debug=True)

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
                        new_name_path = os.path.join(os.path.dirname(file_path), os.path.splitext(filename)[0] + "_hotovo" + ext)
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


if __name__ == "__main__":
    app = App()
    app.attributes('-alpha', 0.0)
    splash = SplashScreen(app)
    splash.run_progress()
    app.attributes('-alpha', 1.0)
    app.deiconify()
    app.mainloop()
