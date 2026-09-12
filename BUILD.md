# Build — FFMPEG Master

Aplikace je teď obojetná (Windows i Linux). Preferovaná cesta k binárkám je automatický
GitHub Actions build (viz níže) — ruční PyInstaller build zůstává pro lokální testování.

## Automatický build na GitHub (doporučeno)

`.github/workflows/build.yml` při pushnutí tagu ve tvaru `v*` (např. `v0.7`) automaticky:

1. sestaví Windows `.exe` (na `windows-latest` runneru),
2. sestaví univerzální Linux `.AppImage` (viz „Proč AppImage a ne holá binárka" níže),
3. vytvoří GitHub Release z toho tagu a oba soubory k němu přiloží jako assety.

Stačí tedy:

```bash
git tag v0.7
git push origin v0.7
```

...a za pár minut je release s `.exe` i `.AppImage` hotový — nic se neděje ručně. Workflow jde
spustit i bez tagu přes záložku **Actions → Build binaries → Run workflow** (jen si sestaví
a nahraje artefakty ke stažení, release ale nevytvoří/needituje).

Workflow si hlavní skript hledá sám (`FFMPEG_Master_v*.pyw` v kořeni repozitáře), takže při
příští verzi stačí přejmenovat/vytvořit nový `.pyw` soubor — workflow se upravovat nemusí.

Aplikace pak v menu **O programu** (i tiše při startu) porovná `tag_name` nejnovějšího release
s konstantou `VERSION` v kódu a nabídne aktualizaci — na Windows vybere `.exe` asset a vymění se
přes pomocný `update.bat`, na Linuxu vybere `.AppImage` asset a vymění se přes `update.sh`. Cíl
výměny je na Linuxu soubor z proměnné prostředí `APPIMAGE` (tu nastavuje AppImage runtime na
stabilní cestu k `.AppImage` souboru — `sys.executable` by tu ukazoval do dočasného mount pointu,
který se mezi spuštěními mění, viz `linux_run_mode()` v kódu). Obě varianty počkají, až proces
skončí, nahradí binárku a znovu ji spustí.

## Proč AppImage a ne holá binárka

Do v0.6.x repo obsahovalo jednu holou `FFMPEG_Master-linux-x86_64` ELF binárku bez instalace.
`FFMPEG_Master-x86_64.AppImage` je funkčně skoro totéž (jeden spustitelný soubor, žádná instalace
do systému, `chmod +x` a spustit), navíc má vestavěnou ikonu a `.desktop` metadata, takže jde
spustit na jakékoliv distribuci stejným způsobem a chová se jako "pořádná" nainstalovaná appka
(ikona, kategorie), i když nic neinstaluje.

Build se dělá přímo na `ubuntu-latest` runneru (bez staršího kontejneru/glibc kompatibilitní
vrstvy — na to se cíleně nehraje, viz „Podpora starších distribucí" níže).

Aplikace pozná, že běží jako AppImage (proměnná prostředí `APPIMAGE`, nastavená AppImage runtimem
— viz `linux_run_mode()` v kódu), a použije ji jako stabilní cestu pro `.desktop` `Exec=` i pro
cíl auto-update výměny — `sys.executable` by uvnitř AppImage ukazoval do dočasného mount pointu,
který se mezi spuštěními mění.

### Podpora starších distribucí

PyInstaller onefile binárka je dynamicky slinkovaná proti glibc/Tcl-Tk knihovnám sestavovacího
stroje — binárka postavená na novějším systému na starším nenaběhne (`GLIBC_2.3x not found`).
Protože `ubuntu-latest` runner odpovídá aktuální Ubuntu LTS (dnes 24.04, glibc 2.39) a David cílí
jen na svůj vlastní Mint (na bázi 24.04, tedy stejná glibc), nemá smysl kvůli tomu komplikovat
build starším kontejnerem — podpora Mintu/Ubuntu 22.04 a starších se záměrně neřeší. Pokud by to
časem bylo potřeba, řešení je sestavit uvnitř staršího Docker image (např. `ubuntu:22.04`) - glibc
je zpětně kompatibilní, takže starší základna dělá binárku univerzálnější, nikdy ne méně
kompatibilní.

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
  FFMPEG_Master_v0.7.0.pyw
```

### Linux — samotná binárka

Potřebuješ systémový Tk/Tcl (na Debianu/Ubuntu `sudo apt install python3-tk tk-dev`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm --onefile --windowed \
  --name ffmpeg-master \
  --add-data "favicon.ico:." \
  --add-data "favicon.png:." \
  --collect-all tkinterdnd2 \
  --collect-all customtkinter \
  --hidden-import PIL._tkinter_finder \
  FFMPEG_Master_v0.7.0.pyw
chmod +x dist/ffmpeg-master
```

Výsledná binárka bude kompatibilní se stejně starým nebo novějším systémem, ne se staršími (viz
„Podpora starších distribucí" výše) — pro jiný cíl sestav uvnitř odpovídajícího Dockeru.

### Linux — zabalení do AppImage

Vychází z `dist/ffmpeg-master` výše. Potřebuješ `.desktop` soubor (`Exec=ffmpeg-master`,
`Icon=ffmpeg-master`) a `AppDir` strukturu:

```bash
mkdir -p AppDir/usr/bin AppDir/usr/share/applications AppDir/usr/share/icons/hicolor/256x256/apps
cp dist/ffmpeg-master AppDir/usr/bin/ffmpeg-master
cp favicon.png AppDir/usr/share/icons/hicolor/256x256/apps/ffmpeg-master.png
cp favicon.png AppDir/ffmpeg-master.png
cp ffmpeg-master.desktop AppDir/usr/share/applications/ffmpeg-master.desktop
cp ffmpeg-master.desktop AppDir/ffmpeg-master.desktop
printf '#!/bin/sh\nHERE="$(dirname "$(readlink -f "$0")")"\nexec "$HERE/usr/bin/ffmpeg-master" "$@"\n' > AppDir/AppRun
chmod +x AppDir/AppRun

wget -q https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage -O appimagetool.AppImage
chmod +x appimagetool.AppImage
./appimagetool.AppImage AppDir FFMPEG_Master-x86_64.AppImage   # potřebuje FUSE; bez něj viz build.yml (--appimage-extract + spustit squashfs-root/AppRun ručně)
```

Přesné znění (`.desktop` obsah, `AppRun` skript) viz krok „Sestav AppImage" v `build.yml` — to je
zdroj pravdy.

Vysvětlení společných PyInstaller přepínačů:
- `--onefile` — vše zabalené do jednoho spustitelného souboru (ikony/logo se rozbalí za běhu do dočasné složky, viz `resource_path()` v kódu).
- `--windowed` — bez konzolového okna na pozadí (na Linuxu bez efektu, ale neškodí).
- `--add-data "soubor;."` / `"soubor:."` — zabalí `favicon.ico`/`favicon.png` dovnitř (oddělovač je `;` na Windows, `:` na Linuxu).
- `--collect-all tkinterdnd2` — zabalí i nativní tkdnd binárky, jinak drag & drop ve zmrazeném buildu nefunguje.
- `--collect-all customtkinter` — zabalí i interní datové soubory CustomTkinter (barevná témata ve formátu JSON apod.), o které PyInstaller sám neví, protože nejsou naimportované jako Python kód. Bez tohoto přepínače hrozí na některých systémech chybějící/špatně vykreslené prvky UI.
- **`--hidden-import PIL._tkinter_finder`** — skutečná (potvrzená) příčina chybějících obrázků/ikon v zabaleném buildu. `PIL.ImageTk` (používá se pro ikonu okna a logo v UI) si tenhle modul hledá dynamicky za běhu, takže ho PyInstaller při statické analýze kódu nezachytí a nezabalí — bez tohoto přepínače selže s `No module named 'PIL._tkinter_finder'` (viditelné na stderr, viz diagnostika níže) a obrázky/ikony se nikde nezobrazí.
- **`config.json` se NEBALÍ dovnitř.** Aplikace ho očekává (a při prvním spuštění sama vytvoří) vedle binárky/skriptu (viz `base_dir()` v kódu — u AppImage vedle samotného `.AppImage` souboru). Díky tomu jde konfiguraci měnit i po zabalení, bez nutnosti znovu buildit.

Hotový soubor najdeš v `dist/`. Zkopíruj ho do cílové složky — `config.json` se tam při prvním spuštění vytvoří automaticky s výchozími hodnotami (uprav si ho pak v aplikaci přes menu **Nastavení**, nebo ručně, vzor v [config.example.json](config.example.json)).

## Poznámka k Linuxu

- `hevc_nvenc` funguje i na Linuxu s proprietárním NVIDIA driverem a ffmpeg buildem s podporou NVENC — bez toho je potřeba v Nastavení přepnout na jiný video kodek (software `libx265`/`libx264`, nebo VA-API `hevc_vaapi` u AMD/Intel GPU).
- `ffmpeg`/`ffprobe` se na Linuxu očekávají v `PATH` (výchozí hodnoty configu jsou prostě `ffmpeg`/`ffprobe`), na Windows zůstává výchozí cesta natvrdo `C:\FFMPEG\bin\...`.
- Drag & drop potřebuje systémový Tcl balíček `tkdnd` (na většině distribucí ho tkinterdnd2 nese už zabalený ve wheelu) — pokud by na nějakém systému chyběl, aplikace to detekuje a spustí se dál bez drag & drop (jen s tlačítkem „Přidat soubory“), nespadne.
- Ikona v okně/liště se na Linuxu/macOS nastavuje z `favicon.png` (`iconphoto`), na Windows z `favicon.ico` (`iconbitmap`) — řeší se to samo podle platformy.
- **Chybějící obrázky/ikony v zabalené Linux binárce — potvrzená příčina: chybějící `PIL._tkinter_finder`.** `PIL.ImageTk` (ikona okna, logo na splash screenu i v „O programu“) si tenhle modul hledá dynamicky za běhu, takže ho PyInstaller sám nezabalí bez explicitního `--hidden-import PIL._tkinter_finder` (viz výše) — bez něj selže s `No module named 'PIL._tkinter_finder'`. Od v0.6.3 to aplikace při startu vypíše na stderr (spusť binárku z terminálu, ne dvojklikem, ať to uvidíš), takže se podobné problémy dají příště rychle odhalit. `resource_path()` navíc zkouší víc míst (PyInstaller `_MEIPASS`, adresář vedle binárky, adresář vedle skriptu) — jako nouzové řešení stačí zkopírovat `favicon.ico`/`favicon.png` do stejné složky, kde leží binárka.
- **Binárka vůbec nenaběhne / hlásí `GLIBC_2.3x not found`.** Znamená to, že sestavovací stroj (GitHub Actions runner) má novější glibc než cílový systém — řešení je sestavit uvnitř Docker image odpovídajícího tomu cílovému systému nebo staršího (viz „Podpora starších distribucí" výše; od v0.7.0 se tohle záměrně neřeší, protože `ubuntu-latest` a Davidův Mint mají stejnou glibc základnu). Pokud binárka/AppImage nenaběhne i tak, spusť ji z terminálu (ne dvojklikem) a zjisti přesnou hlášku — nemusí jít o glibc vůbec.
- **AppImage hlásí `error loading libfuse.so.2` / „AppImages require FUSE to run“.** Novější distribuce (včetně novějších Ubuntu/Mint) nemusí mít FUSE2 v základu — doinstaluj `libfuse2` (na některých `libfuse2t64`), nebo spusť AppImage s `--appimage-extract-and-run`.
- **Ikona v systémové nabídce/launcheru (start menu, dock, kategorie Multimedia).** To se zabalenou binárkou/AppImage samo nestane — potřebuje samostatný `.desktop` soubor + ikonu na XDG místě. Aplikace si ho při prvním spuštění na Linuxu sama vytvoří (`ensure_linux_desktop_entry()` v kódu, běží nezávisle na nastavení ikony okna výše — jedno selhání druhé neblokuje) do `~/.local/share/applications/ffmpeg-master.desktop` a `~/.local/share/icons/hicolor/256x256/apps/ffmpeg-master.png` — bez root práv. Některá desktopová prostředí potřebují odhlásit/znovu přihlásit se (nebo restartovat launcher/`update-desktop-database`), než si nové ikony/položky všimnou.
