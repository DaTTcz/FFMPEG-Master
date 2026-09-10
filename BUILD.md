# Build — FFMPEG Master

Aplikace je teď obojetná (Windows i Linux). Preferovaná cesta k binárkám je automatický
GitHub Actions build (viz níže) — ruční PyInstaller build zůstává pro lokální testování.

## Automatický build na GitHub (doporučeno)

`.github/workflows/build.yml` při pushnutí tagu ve tvaru `v*` (např. `v0.7`) automaticky:

1. sestaví Windows `.exe` (na `windows-latest` runneru),
2. sestaví Linux binárku `FFMPEG_Master-linux-x86_64` (na `ubuntu-latest` runneru),
3. vytvoří GitHub Release z toho tagu a oba soubory k němu přiloží jako assety.

Stačí tedy:

```bash
git tag v0.7
git push origin v0.7
```

...a za pár minut je release s oběma binárkami hotový — nic se neděje ručně. Workflow jde
spustit i bez tagu přes záložku **Actions → Build binaries → Run workflow** (jen si sestaví
a nahraje artefakty ke stažení, release ale nevytvoří/needituje).

Workflow si hlavní skript hledá sám (`FFMPEG_Master_v*.pyw` v kořeni repozitáře), takže při
příští verzi stačí přejmenovat/vytvořit nový `.pyw` soubor — workflow se upravovat nemusí.

Aplikace pak v menu **O programu** (i tiše při startu) porovná `tag_name` nejnovějšího release
s konstantou `VERSION` v kódu a nabídne stažení + instalaci — na Windows vybere `.exe` asset a
vymění se přes pomocný `update.bat`, na Linuxu vybere asset s `linux` v názvu a vymění se přes
`update.sh` (obě varianty počkají, až proces skončí, nahradí binárku a znovu ji spustí).

## Ruční build (lokálně)

### Windows

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm --onefile --windowed ^
  --name FFMPEG_Master ^
  --icon favicon.ico ^
  --add-data "favicon.ico;." ^
  --add-data "favicon.png;." ^
  --collect-all tkinterdnd2 ^
  --collect-all customtkinter ^
  --hidden-import PIL._tkinter_finder ^
  FFMPEG_Master_v0.6.4.pyw
```

### Linux

Potřebuješ systémový Tk/Tcl (na Debianu/Ubuntu `sudo apt install python3-tk tk-dev`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm --onefile --windowed \
  --name FFMPEG_Master-linux-x86_64 \
  --add-data "favicon.ico:." \
  --add-data "favicon.png:." \
  --collect-all tkinterdnd2 \
  --collect-all customtkinter \
  --hidden-import PIL._tkinter_finder \
  FFMPEG_Master_v0.6.4.pyw
chmod +x dist/FFMPEG_Master-linux-x86_64
```

Vysvětlení společných přepínačů:
- `--onefile` — vše zabalené do jednoho spustitelného souboru (ikony/logo se rozbalí za běhu do dočasné složky, viz `resource_path()` v kódu).
- `--windowed` — bez konzolového okna na pozadí (na Linuxu bez efektu, ale neškodí).
- `--add-data "soubor;."` / `"soubor:."` — zabalí `favicon.ico`/`favicon.png` dovnitř (oddělovač je `;` na Windows, `:` na Linuxu).
- `--collect-all tkinterdnd2` — zabalí i nativní tkdnd binárky, jinak drag & drop ve zmrazeném buildu nefunguje.
- `--collect-all customtkinter` — zabalí i interní datové soubory CustomTkinter (barevná témata ve formátu JSON apod.), o které PyInstaller sám neví, protože nejsou naimportované jako Python kód. Bez tohoto přepínače hrozí na některých systémech chybějící/špatně vykreslené prvky UI.
- **`--hidden-import PIL._tkinter_finder`** — skutečná (potvrzená) příčina chybějících obrázků/ikon v zabaleném buildu. `PIL.ImageTk` (používá se pro ikonu okna a logo v UI) si tenhle modul hledá dynamicky za běhu, takže ho PyInstaller při statické analýze kódu nezachytí a nezabalí — bez tohoto přepínače selže s `No module named 'PIL._tkinter_finder'` (viditelné na stderr, viz diagnostika níže) a obrázky/ikony se nikde nezobrazí.
- **`config.json` se NEBALÍ dovnitř.** Aplikace ho očekává (a při prvním spuštění sama vytvoří) ve stejné složce, kde leží binárka — viz `base_dir()` v kódu, který pro zmrazený build použije `os.path.dirname(sys.executable)`. Díky tomu jde konfiguraci měnit i po zabalení, bez nutnosti znovu buildit.

Hotový soubor najdeš v `dist/`. Zkopíruj ho do cílové složky — `config.json` se tam při prvním spuštění vytvoří automaticky s výchozími hodnotami (uprav si ho pak v aplikaci přes menu **Nastavení**, nebo ručně, vzor v [config.example.json](config.example.json)).

## Poznámka k Linuxu

- `hevc_nvenc` funguje i na Linuxu s proprietárním NVIDIA driverem a ffmpeg buildem s podporou NVENC — bez toho je potřeba v Nastavení přepnout na jiný video kodek (software `libx265`/`libx264`, nebo VA-API `hevc_vaapi` u AMD/Intel GPU).
- `ffmpeg`/`ffprobe` se na Linuxu očekávají v `PATH` (výchozí hodnoty configu jsou prostě `ffmpeg`/`ffprobe`), na Windows zůstává výchozí cesta natvrdo `C:\FFMPEG\bin\...`.
- Drag & drop potřebuje systémový Tcl balíček `tkdnd` (na většině distribucí ho tkinterdnd2 nese už zabalený ve wheelu) — pokud by na nějakém systému chyběl, aplikace to detekuje a spustí se dál bez drag & drop (jen s tlačítkem „Přidat soubory“), nespadne.
- Ikona v okně/liště se na Linuxu/macOS nastavuje z `favicon.png` (`iconphoto`), na Windows z `favicon.ico` (`iconbitmap`) — řeší se to samo podle platformy.
- **Chybějící obrázky/ikony v zabalené Linux binárce — potvrzená příčina: chybějící `PIL._tkinter_finder`.** `PIL.ImageTk` (ikona okna, logo na splash screenu i v „O programu“) si tenhle modul hledá dynamicky za běhu, takže ho PyInstaller sám nezabalí bez explicitního `--hidden-import PIL._tkinter_finder` (viz výše) — bez něj selže s `No module named 'PIL._tkinter_finder'`. Od v0.6.3 to aplikace při startu vypíše na stderr (spusť binárku z terminálu, ne dvojklikem, ať to uvidíš), takže se podobné problémy dají příště rychle odhalit. `resource_path()` navíc zkouší víc míst (PyInstaller `_MEIPASS`, adresář vedle binárky, adresář vedle skriptu) — jako nouzové řešení stačí zkopírovat `favicon.ico`/`favicon.png` do stejné složky, kde leží binárka.
- **Ikona v systémové nabídce/launcheru (start menu, dock, kategorie Multimedia).** To se zabalenou binárkou samo nestane — potřebuje samostatný `.desktop` soubor + ikonu na XDG místě. Aplikace si ho při prvním spuštění na Linuxu sama vytvoří (`ensure_linux_desktop_entry()` v kódu, běží nezávisle na nastavení ikony okna výše — jedno selhání druhé neblokuje) do `~/.local/share/applications/ffmpeg-master.desktop` a `~/.local/share/icons/hicolor/256x256/apps/ffmpeg-master.png` — bez root práv. Některá desktopová prostředí potřebují odhlásit/znovu přihlásit se (nebo restartovat launcher/`update-desktop-database`), než si nové ikony/položky všimnou.
