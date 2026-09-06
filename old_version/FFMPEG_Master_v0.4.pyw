import os
import sys
import subprocess
import json
import threading
import shutil
import re
import time
import ctypes
import customtkinter as ctk
from tkinter import messagebox, Listbox, filedialog, EXTENDED
from tkinterdnd2 import DND_FILES, TkinterDnD

VERSION = "v0.4"

# --- OPRAVA IKONY V LIŠTĚ WINDOWS ---
try:
    myappid = f'moje.ffmpeg.master.{VERSION}'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception: pass

def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)

try:
    from PIL import Image
    HAS_PILLOW = True
except Exception:
    HAS_PILLOW = False

# --- KONFIGURACE CEST ---
FFMPEG_PATH = r"C:\FFMPEG\bin\ffmpeg.exe"
FFPROBE_PATH = r"C:\FFMPEG\bin\ffprobe.exe"
BASE_OUT_DIR = r"C:\!VIDEO\VIDEO_OUT\OUTPUT"
BASE_DONE_DIR = r"C:\!VIDEO\VIDEO_OUT\HOTOVO"

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
            except: pass

        if not logo_loaded:
            self.logo_label = ctk.CTkLabel(self, text=f"FFMPEG Master {VERSION}", font=("Arial", 32, "bold"), text_color="#1f538d")
            self.logo_label.pack(pady=(60, 20))

        ctk.CTkLabel(self, text=f"FFMPEG Master {VERSION}", font=("Arial", 16, "bold"), text_color="#28a745").pack(pady=(5, 0))
        ctk.CTkLabel(self, text="©2026 David Trubka", font=("Arial", 10, "italic"), text_color="#2871a7").pack(pady=(0, 5))
        self.label_status = ctk.CTkLabel(self, text="Inicializace...", font=("Arial", 11, "italic"), text_color="gray")
        self.label_status.pack(pady=(20, 0))
        self.prog = ctk.CTkProgressBar(self, width=350, height=4, progress_color="#1f538d")
        self.prog.pack(pady=20); self.prog.set(0)

    def run_progress(self):
        quotes = ["Inicializace kodeků...", "Detekce CUDA jader...", "Příprava FFmpeg...", "Optimalizace procesů...", "Vše je připraveno!"]
        for i in range(1, 101):
            self.prog.set(i / 100)
            idx = min((i - 1) * len(quotes) // 100, len(quotes) - 1)
            self.label_status.configure(text=quotes[idx])
            self.update()
            time.sleep(0.06)
        self.destroy()

class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)
        self.title(f"FFMPEG Master {VERSION}")
        self.geometry("750x850")
        ctk.set_appearance_mode("dark")
        
        try: self.iconbitmap(resource_path("favicon.ico"))
        except: pass

        self.is_running = False
        self.stop_requested = False
        self.current_process = None
        self.log_entries = []  # (text, is_debug)
        self.run_completed = False  # True po dokončení nebo zastavení běhu
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
        self.btn_del = ctk.CTkButton(self.list_btns, text="Smazat", fg_color="#a11a1a", command=self.remove_selected)
        self.btn_del.pack(pady=20, fill="x")

        log_header = ctk.CTkFrame(self, fg_color="transparent")
        log_header.pack(fill="x", padx=20, pady=(10, 0))
        ctk.CTkLabel(log_header, text="Log:", font=("Arial", 11)).pack(side="left")
        self.debug_var = ctk.BooleanVar(value=False)
        self.debug_var.trace_add("write", lambda *_: self.redraw_log())
        ctk.CTkCheckBox(log_header, text="debug zprávy", variable=self.debug_var, font=("Arial", 11), checkbox_width=16, checkbox_height=16).pack(side="right")

        self.textbox = ctk.CTkTextbox(self, height=250, font=("Consolas", 10))
        self.textbox.pack(padx=20, pady=(2, 10), fill="x")
        self.textbox._textbox.tag_configure("debug", foreground="#888888")
        self.textbox._textbox.tag_configure("error", foreground="#ff4444")

        self.label_p1 = ctk.CTkLabel(self, text="Aktuální soubor: ", font=("Arial", 11, "bold")); self.label_p1.pack(padx=20, anchor="w")
        self.prog_file = ctk.CTkProgressBar(self, height=12, progress_color="#1f538d"); self.prog_file.pack(pady=(0, 10), padx=20, fill="x"); self.prog_file.set(0)
        self.label_p2 = ctk.CTkLabel(self, text="Celkový postup: 0/0", font=("Arial", 11, "bold")); self.label_p2.pack(padx=20, anchor="w")
        self.prog_total = ctk.CTkProgressBar(self, height=12, progress_color="#28a745"); self.prog_total.pack(pady=(0, 20), padx=20, fill="x"); self.prog_total.set(0)

        self.bot_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bot_frame.pack(pady=(0, 20))
        self.btn_start = ctk.CTkButton(self.bot_frame, text="SPUSTIT", height=50, width=160, fg_color="#28a745", font=("Arial", 15, "bold"), command=self.start_thread)
        self.btn_start.pack(side="left", padx=10)
        self.btn_stop = ctk.CTkButton(self.bot_frame, text="STOP", height=50, width=120, fg_color="#a11a1a", font=("Arial", 15, "bold"), command=self.stop_process, state="disabled")
        self.btn_stop.pack(side="left", padx=10)

        ctk.CTkLabel(self, text="DaTT.cz  © 2026", font=("Arial", 10), text_color="#3a3a3a").pack(side="bottom", pady=(0, 6))

    def log(self, text, debug=False, error=False):
        self.log_entries.append((text, debug, error))
        if not debug or self.debug_var.get():
            tag = "error" if error else ("debug" if debug else "")
            self.textbox._textbox.insert("end", text + "\n", tag)
            self.textbox.see("end")
        self.update_idletasks()

    def redraw_log(self):
        """Překreslí log podle aktuálního stavu debug checkboxu."""
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
        self.btn_del.configure(state=s)

    def analyze_loudnorm(self, file_path, audio_mapping, duration, filename, done_count, total_files):
        """
        Průchod 1: spustí ffmpeg s loudnorm=print_format=json, čte progress a zobrazuje ho na progress baru.
        Vrátí dict s klíči input_i, input_lra, input_tp, input_thresh, target_offset nebo None při chybě.
        """
        self.log("  [2-průchod] Průchod 1/2: Analýza hlasitosti (loudnorm)...")
        self.label_p1.configure(text=f"Průchod 1/2 (analýza): 0% | {filename}")
        cmd = [FFMPEG_PATH,
            "-fflags", "+discardcorrupt+genpts",
            "-err_detect", "ignore_err",
            "-analyzeduration", "20000000",
            "-probesize", "20000000",
            "-i", file_path] + audio_mapping + [
            "-vn",
            "-af", "loudnorm=I=-18:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-"
        ]
        import time as _time
        _delays = [2, 5, 10]
        for _attempt in range(3):
            try:
                _pass1_proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    universal_newlines=True, encoding="utf-8", errors="replace",
                    creationflags=0x08000000
                )
                break
            except PermissionError:
                if _attempt < 2:
                    self.log(f"  [2-průchod] Přístup odepřen, čekám {_delays[_attempt]}s (pokus {_attempt+2}/3)...", debug=True)
                    _time.sleep(_delays[_attempt])
                else:
                    raise
        try:
            pass  # blok try pokračuje níže
            output_lines = []
            for line in _pass1_proc.stdout:
                if self.stop_requested:
                    _pass1_proc.terminate()
                    return None
                output_lines.append(line)
                if any(kw in line for kw in ("Error", "error", "Invalid", "No such", "failed", "Cannot")):
                    self.log(f"  [ffmpeg P1] {line.strip()}", error=True)
                match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                if match and duration > 0:
                    h_p, m_p, s_p = map(float, match.groups())
                    perc = min((h_p * 3600 + m_p * 60 + s_p) / duration, 1.0)
                    # Průchod 1 zabírá "první polovinu" celkového postupu daného souboru
                    self.prog_file.set(perc * 0.5)
                    self.prog_total.set((done_count + perc * 0.5) / total_files)
                    self.label_p1.configure(text=f"Průchod 1/2 (analýza): {int(perc*100)}% | {filename}")
                    self.label_p2.configure(text=f"Celkově: {int(((done_count + perc*0.5)/total_files)*100)}% | Zpracovávám {done_count+1} z {total_files}")
                    self.update()
            _pass1_proc.wait()
            output = "".join(output_lines)
            json_match = re.search(r'\{[^{}]*"input_i"[^{}]*\}', output, re.DOTALL)
            if json_match:
                stats = json.loads(json_match.group(0))
                self.log(f"  [2-průchod] Naměřeno: I={stats.get('input_i')} LUFS, "
                         f"LRA={stats.get('input_lra')} LU, TP={stats.get('input_tp')} dBTP")
                return stats
            else:
                self.log("  [2-průchod] VAROVÁNÍ: loudnorm statistiky nenalezeny, použiji 1-průchodový fallback.")
                return None
        except FileNotFoundError:
            self.log(f"  [2-průchod] CHYBA: ffmpeg.exe nenalezen na cestě: {FFMPEG_PATH}", debug=True)
            return None
        except PermissionError as e:
            self.log(f"  [2-průchod] CHYBA oprávnění ({e}) — zkus spustit aplikaci jako správce.", debug=True)
            return None
        except Exception as e:
            self.log(f"  [2-průchod] CHYBA analýzy: {type(e).__name__}: {e}", debug=True)
            return None

    def get_stream_indices(self, file_path):
        cmd = [FFPROBE_PATH, "-v", "error",
               "-analyzeduration", "20000000", "-probesize", "20000000",
               "-show_entries",
               "format=duration:stream=index,codec_type,codec_name,width,height:stream_tags=language", 
               "-of", "json", file_path]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
            data = json.loads(res.stdout)
            dur = float(data.get('format', {}).get('duration', 0))
            v_idx, res_str = None, "Unknown"
            audios_info, subs_info = [], []
            audios = {'cz': [], 'no_lang': [], 'others': []}
            subs = {'cz': [], 'no_lang': [], 'others': []}
            for s in data.get('streams', []):
                idx = str(s.get('index')); stype = s.get('codec_type')
                lang = str(s.get('tags', {}).get('language', 'und')).upper()
                if stype == 'video' and v_idx is None:
                    v_idx = idx; res_str = f"{s.get('width')}x{s.get('height')}"
                elif stype == 'audio':
                    audios_info.append(f"{lang}({idx})")
                    if lang in ['CZE', 'CES']: audios['cz'].append(idx)
                    elif lang in ['UND', '']: audios['no_lang'].append(idx)
                    else: audios['others'].append(idx)
                elif stype == 'subtitle':
                    subs_info.append(f"{lang}({idx})")
                    if lang in ['CZE', 'CES']: subs['cz'].append(idx)
                    elif lang in ['UND', '']: subs['no_lang'].append(idx)
                    else: subs['others'].append(idx)
            return dur, v_idx, res_str, audios, subs, audios_info, subs_info
        except: return 0, None, "", {}, {}, [], []

    def log_analysis(self, filename, res, dur_str, a_all, s_all, audios, subs, prefix="[ ANALÝZA:"):
        self.log(f"\n{prefix} {filename} ]")
        self.log(f"  Vstup:   Video: {res}, Délka: {dur_str}")
        self.log(f"           Audio: {', '.join(a_all) if a_all else 'žádné'}")
        self.log(f"           Titulky: {', '.join(s_all) if s_all else 'žádné'}")
        sel_a = f"CZE({', '.join(audios['cz'])})" if audios['cz'] else (f"AUTO({', '.join(audios['no_lang'])})" if audios['no_lang'] else f"ORIG({', '.join(audios['others'])})")
        sel_s = f"CZE({', '.join(subs['cz'])})" if subs['cz'] else (f"AUTO({', '.join(subs['no_lang'])})" if subs['no_lang'] else "žádné")
        self.log(f"  Výstup:  Audio: {sel_a}, Titulky: {sel_s}")
        self.log("-" * 50)
        return sel_a, sel_s

    def create_nfo(self, file_path):
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        nfo_path = os.path.join(BASE_OUT_DIR, base_name + ".nfo")
        
        # Extrakce roku ze závorek
        year_match = re.search(r'\((\d{4})\)', base_name)
        year = year_match.group(1) if year_match else ""
        
        # Tvorba titulku: nahrazení _ za :
        title = base_name.replace('_', ':')
        
        try:
            with open(nfo_path, "w", encoding="utf-8") as f:
                f.write('<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n')
                f.write('<movie>\n')
                f.write(f'    <title>{title}</title>\n')
                f.write(f'    <year>{year}</year>\n')
                f.write('    <plot></plot>\n')
                f.write('    <genre>Animovaný</genre>\n')
                f.write('</movie>')
            return os.path.basename(nfo_path)
        except: return None

    def process_new_file(self, path):
        filename = os.path.basename(path)
        dur, v_idx, res, audios, subs, a_all, s_all = self.get_stream_indices(path)
        if v_idx is None: return
        m, s = divmod(int(dur), 60); h, m = divmod(m, 60)
        dur_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
        self.log_analysis(filename, res, dur_str, a_all, s_all, audios, subs)

    def handle_drop(self, event):
        if self.is_running: return
        paths = re.findall(r'\{(.*?)\}|(\S+)', event.data)
        valid = [os.path.normpath(x[0] if x[0] else x[1]) for x in paths
                 if (x[0] if x[0] else x[1]).lower().endswith(('.mkv','.mp4','.avi','.mov','.ts','.m2ts','.wmv','.flv','.webm','.m4v'))]
        if valid and not self.is_running and self.run_completed:
            self.log_entries = []
            self.run_completed = False
            self.textbox.configure(state="normal")
            self.textbox.delete("1.0", "end")
        for path in valid:
            self.listbox.insert("end", path); self.process_new_file(path)

    def browse_files(self):
        files = filedialog.askopenfilenames(filetypes=[("Video soubory", "*.mkv *.mp4 *.avi *.mov *.ts *.m2ts *.wmv *.flv *.webm *.m4v"), ("Všechny soubory", "*.*")])
        if files and not self.is_running and self.run_completed:
            # Vyčisti log jen pokud předchozí běh skončil
            self.log_entries = []
            self.run_completed = False
            self.textbox.configure(state="normal")
            self.textbox.delete("1.0", "end")
        for f in files:
            path = os.path.normpath(f); self.listbox.insert("end", path); self.process_new_file(path)

    def move_up(self):
        pos = self.listbox.curselection()
        if pos and pos[0] > 0:
            for idx in pos:
                t = self.listbox.get(idx); self.listbox.delete(idx); self.listbox.insert(idx-1, t); self.listbox.select_set(idx-1)

    def move_down(self):
        pos = list(self.listbox.curselection())
        if pos and pos[-1] < self.listbox.size()-1:
            for idx in reversed(pos):
                t = self.listbox.get(idx); self.listbox.delete(idx); self.listbox.insert(idx+1, t); self.listbox.select_set(idx+1)

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()): self.listbox.delete(i)

    def stop_process(self):
        if messagebox.askyesno("STOP", "Opravdu zastavit kódování?"):
            self.stop_requested = True
            if self.current_process: 
                self.current_process.terminate()

    def start_thread(self):
        if self.listbox.size() == 0: return
        self.is_running = True
        self.stop_requested = False
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.set_ui_state(False)
        threading.Thread(target=self.run_process, daemon=True).start()

    def run_process(self):
        total_files = self.listbox.size()
        done_count = 0
        os.makedirs(BASE_OUT_DIR, exist_ok=True)
        os.makedirs(BASE_DONE_DIR, exist_ok=True)
        
        while self.listbox.size() > 0 and not self.stop_requested:
            file_path = self.listbox.get(0)
            filename = os.path.basename(file_path)
            duration, v_idx, res_str, audios, subs, a_all, s_all = self.get_stream_indices(file_path)
            
            if v_idx is None:
                self.listbox.delete(0)
                continue
            
            m, s = divmod(int(duration), 60); h, m = divmod(m, 60)
            dur_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
            self.log(f"\n>>> SPUŠTĚNO: {filename}")
            sel_a, sel_s = self.log_analysis(filename, res_str, dur_str, a_all, s_all, audios, subs, prefix="[ INFO:")

            mapping = ["-map", f"0:{v_idx}"]
            a_sel = audios['cz'] if audios['cz'] else (audios['no_lang'] if audios['no_lang'] else audios['others'])
            for idx in a_sel: mapping += ["-map", f"0:{idx}"]
            s_sel = subs['cz'] if subs['cz'] else (subs['no_lang'] if subs['no_lang'] else [])
            for idx in s_sel: mapping += ["-map", f"0:{idx}"]

            # Pro loudnorm analýzu stačí první audio stream z výběru
            first_audio_idx = a_sel[0] if a_sel else None
            audio_only_mapping = ["-map", f"0:{first_audio_idx}"] if first_audio_idx is not None else ["-map", "0:a:0"]

            out_file = os.path.join(BASE_OUT_DIR, os.path.splitext(filename)[0] + ".mkv")

            # --- Sestavení audio filtru podle zvoleného módu ---
            two_pass = True  # vždy 2-průchodová analýza hlasitosti
            if two_pass:
                stats = self.analyze_loudnorm(file_path, audio_only_mapping, duration, filename, done_count, total_files)
                if stats and stats.get("input_i") not in (None, "-inf", "inf", "-inf "):
                    af_audio = (
                        f"loudnorm=I=-18:TP=-1.5:LRA=11:linear=true"
                        f":measured_I={stats['input_i']}"
                        f":measured_LRA={stats['input_lra']}"
                        f":measured_TP={stats['input_tp']}"
                        f":measured_thresh={stats['input_thresh']}"
                        f":offset={stats['target_offset']}"
                    )
                    self.log("  [2-průchod] Průchod 2/2: Kódování s naměřenými hodnotami...")
                else:
                    # Fallback na 1-průchod pokud analýza selhala
                    af_audio = "loudnorm=I=-18:TP=-1.5:LRA=11"
                    self.log("  [2-průchod] Fallback: použit standardní 1-průchodový loudnorm.")
            else:
                af_audio = "loudnorm=I=-18:TP=-1.5:LRA=11"

            # Sestavení mapování
            s_map = []
            for sidx in s_sel:
                s_map += ["-map", f"0:{sidx}"]
            a_map = []
            for aidx in a_sel:
                a_map += ["-map", f"0:{aidx}"]

            # Sestavení filter_complex se scalingem videa a normalizací každého audio streamu
            fc_parts = [f"[0:{v_idx}]setparams=color_trc=bt709:colorspace=bt709:color_primaries=bt709:range=tv,format=yuv420p,scale=1280:720:force_original_aspect_ratio=decrease:flags=lanczos,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p10le[vout]"]
            fc_out_maps = ["-map", "[vout]"]
            for i, aidx in enumerate(a_sel):
                lbl = f"aout{i}"
                fc_parts.append(f"[0:{aidx}]aformat=sample_fmts=fltp:sample_rates=48000,aresample=async=1:first_pts=0,{af_audio},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[{lbl}]")
                fc_out_maps += ["-map", f"[{lbl}]"]

            s_map = []
            for sidx in s_sel:
                s_map += ["-map", f"0:{sidx}"]

            cmd = [FFMPEG_PATH,
                "-fflags", "+discardcorrupt+genpts",
                "-err_detect", "ignore_err",
                "-max_error_rate", "1.0",
                "-analyzeduration", "20000000",
                "-probesize", "20000000",
                "-i", file_path,
                "-filter_complex", ";".join(fc_parts),
            ] + fc_out_maps + s_map + [
                "-c:v", "hevc_nvenc", "-pix_fmt", "p010le", "-profile:v", "main10", "-rc", "vbr", "-cq", "28.5", "-preset", "p7",
                "-c:a", "libopus", "-b:a", "96k",
                "-c:s", "copy",
                "-max_muxing_queue_size", "1024",
                out_file, "-y"
            ]
            
            self.log(f"  [CMD] {' '.join(cmd)}", debug=True)
            self.current_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8', errors='replace', creationflags=0x08000000)
            
            for line in self.current_process.stdout:
                line = line.rstrip()
                if any(kw in line for kw in ("Error", "error", "Invalid", "No such", "failed", "Cannot", "Conversion failed", "Driver", "minimum required", "not support", "warning", "Warning")):
                    self.log(f"  [ffmpeg] {line}", error=True)
                match = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
                if match and duration > 0:
                    h_p, m_p, s_p = map(float, match.groups())
                    perc = min((h_p * 3600 + m_p * 60 + s_p) / duration, 1.0)
                    # V 2-průchodovém módu průchod 2 zabírá druhou polovinu progress baru
                    file_prog = (0.5 + perc * 0.5) if two_pass else perc
                    self.prog_file.set(file_prog)
                    self.prog_total.set((done_count + file_prog) / total_files)
                    pass_label = " [2/2]" if two_pass else ""
                    self.label_p1.configure(text=f"Průchod{pass_label}: {int(perc*100)}% | {filename}")
                    self.label_p2.configure(text=f"Celkově: {int(((done_count + file_prog)/total_files)*100)}% | Zpracovávám {done_count+1} z {total_files}")
                    self.update()
            
            self.current_process.wait()
            self.log(f"  [returncode] {self.current_process.returncode}", debug=True)

            if self.current_process.returncode == 0 and not self.stop_requested:
                nfo_file = self.create_nfo(file_path)
                if nfo_file: 
                    self.log(f"  NFO:     Soubor {nfo_file} byl úspěšně vygenerován.")
                
                status_msg = ""
                try:
                    src_drive = os.path.splitdrive(os.path.abspath(file_path))[0].upper()
                    dst_drive = os.path.splitdrive(os.path.abspath(BASE_DONE_DIR))[0].upper()
                    
                    if src_drive == dst_drive:
                        shutil.move(file_path, os.path.join(BASE_DONE_DIR, filename))
                        status_msg = f"  STATUS:  Soubor přesunut do složky {os.path.basename(BASE_DONE_DIR)}"
                    else:
                        ext = os.path.splitext(filename)[1]
                        new_name_path = os.path.join(os.path.dirname(file_path), os.path.splitext(filename)[0] + "_hotovo" + ext)
                        os.rename(file_path, new_name_path)
                        status_msg = f"  STATUS:  Soubor ponechán na místě a přejmenován."
                except Exception as e:
                    status_msg = f"  STATUS:  Chyba při manipulaci: {e}"

                self.log(status_msg)
                self.log(f"--- HOTOVO: {filename} ---")
                self.listbox.delete(0)
                done_count += 1
            else:
                if self.stop_requested: 
                    self.log(">>> PŘERUŠENO UŽIVATELEM.")
                self.current_process = None
                time.sleep(0.5)
                if os.path.exists(out_file):
                    try: os.remove(out_file)
                    except: pass
                break
        
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

if __name__ == "__main__":
    app = App()
    app.attributes('-alpha', 0.0)
    splash = SplashScreen(app)
    splash.run_progress()
    app.attributes('-alpha', 1.0)
    app.deiconify()
    app.mainloop()