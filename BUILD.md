# Build (PyInstaller) — FFMPEG Master

Návod na zabalení `FFMPEG_Master_v0_5.pyw` do jednoho `.exe` pro Windows.

## 1. Příprava prostředí (na Windows)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install customtkinter tkinterdnd2 pillow pyinstaller
```

## 2. Build

Spusť z kořene repozitáře:

```bash
pyinstaller --noconfirm --onefile --windowed ^
  --name FFMPEG_Master ^
  --icon favicon.ico ^
  --add-data "favicon.ico;." ^
  --add-data "favicon.png;." ^
  FFMPEG_Master_v0_5.pyw
```

Vysvětlení:
- `--onefile` — vše zabalené do jednoho `.exe` (ikony se rozbalí za běhu do dočasné složky, viz `resource_path()` v kódu).
- `--windowed` — bez konzolového okna na pozadí.
- `--add-data "soubor;."` — zabalí `favicon.ico`/`favicon.png` dovnitř exe (jsou to jen ikony/logo, nejsou uživatelsky editovatelné, proto je v pořádku je zabalit).
- **`config.json` se NEBALÍ dovnitř exe.** Aplikace ho očekává (a při prvním spuštění sama vytvoří) ve stejné složce, kde leží `.exe` — viz `base_dir()` v kódu, který pro zmrazený build použije `os.path.dirname(sys.executable)`. Díky tomu jde konfiguraci měnit i po zabalení, bez nutnosti znovu buildit.

## 3. Výstup

Hotový `FFMPEG_Master.exe` najdeš v `dist/`. Zkopíruj ho do cílové složky — `config.json` se tam při prvním spuštění vytvoří automaticky s výchozími hodnotami (uprav si ho pak v aplikaci přes menu **Nastavení**, nebo ručně).

Kód obsahuje `sys.platform` větvení pro `creationflags` u `subprocess.Popen` (na Windows potlačuje černé konzolové okno, jinde se flag vynechává) — `hevc_nvenc` a ovládání ikony v liště (`ctypes.windll`) jsou ale stále Windows/NVIDIA specifické, takže plná multiplatformnost zatím není cílem v0.5.
