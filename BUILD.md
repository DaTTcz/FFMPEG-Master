# Build — FFMPEG Master

Aplikace je teď obojetná (Windows i Linux) a na Linuxu se distribuuje ve dvou formátech.
Preferovaná cesta k binárkám je automatický GitHub Actions build (viz níže) — ruční PyInstaller
build zůstává pro lokální testování.

## Automatický build na GitHub (doporučeno)

`.github/workflows/build.yml` má dvě různé úrovně podle toho, co se pushne:

- **push do `main`** (nebo ruční spuštění bez tagu) → proběhne jen rychlá kontrola
  (`quick-check`): nainstalují se závislosti (`pip install -r requirements.txt`) a hlavní skript
  se zkompiluje (`python -m py_compile`). Odhalí to syntax chyby i rozbité/chybějící závislosti
  během pár vteřin, ale nesestavuje žádnou binárku.
- **push tagu ve tvaru `v*`** (např. `v0.7`, `v0.7.1`) → proběhne skutečný build:
  1. sestaví se Windows `.exe` (na `windows-latest` runneru),
  2. sestaví se univerzální Linux `.AppImage` (viz „AppImage vs. .deb" níže),
  3. sestaví se Linux `.deb` balíček (viz tamtéž),
  4. vytvoří se GitHub Release z toho tagu a všechny tři soubory se k němu přiloží jako assety.

Stačí tedy:

```bash
git tag v0.7
git push origin v0.7
```

...a za pár minut je release s `.exe`, `.AppImage` i `.deb` hotový — nic se neděje ručně. Workflow
jde spustit i bez tagu přes záložku **Actions → Build binaries → Run workflow** — v tom případě
proběhne jen `quick-check`, ne plný build.

Workflow si hlavní skript hledá sám (`FFMPEG_Master_v*.pyw` v kořeni repozitáře, vybere ten s
nejvyšší verzí přes `sort -V` — funguje spolehlivě i když v repu zůstaly starší `.pyw` soubory),
takže při příští verzi stačí přejmenovat/vytvořit nový `.pyw` soubor — workflow se upravovat nemusí.

## AppImage vs. .deb — dva formáty, dva účely

Do v0.6.x repo obsahovalo jednu holou `FFMPEG_Master-linux-x86_64` ELF binárku bez instalace. Od
v0.7 se místo toho nabízí dvě varianty vedle sebe, protože každá řeší jiný případ použití:

**AppImage** (`FFMPEG_Master-x86_64.AppImage`) — jeden spustitelný soubor, žádná instalace do
systému, funguje na jakékoliv distribuci stejně (`chmod +x` a spustit). Hodí se, když člověk chce
appku jen vyzkoušet, spustit z USB/síťového disku, nebo nemá/nechce root práva. Nevýhoda: appka
sama sebe umí bez problémů nahradit novou verzí (auto-update na klik), ale spuštění vyžaduje na
novějších systémech (Ubuntu 22.04+/24.04) nainstalovaný `libfuse2`/`libfuse2t64` a v souborovém
manažeru bývá potřeba ručně zaškrtnout „spustit jako program" (Nemo/Nautilus si spustitelné právo
u stažených souborů nepamatuje samo).

**`.deb`** (`FFMPEG_Master_<verze>_amd64.deb`) — pohodlná instalace na Debian/Ubuntu odvozené
distribuce (Mint, Ubuntu, Pop!_OS...). Dvojklik → systémový instalátor balíčků → binárka skončí
v `/usr/bin/ffmpeg-master`, `.desktop` záznam a ikonka se zaregistrují automaticky (`postinst`
skript spustí `update-desktop-database`/`gtk-update-icon-cache`), bez ručního odškrtávání čehokoliv.
Nevýhoda: binárka je v `/usr/bin` (root-owned), takže appka ji nemůže tiše přepsat sama sebou jako
u AppImage — update se řeší jinak (viz níže).

Obě varianty se v `build-linux` sestavují z té samé PyInstaller binárky (`dist/ffmpeg-master`) a
sdíleného `ffmpeg-master.desktop` souboru — žádná duplicitní práce.

## Jak appka pozná, čím byla spuštěná

`linux_run_mode()` v hlavním skriptu rozliší tři režimy podle `sys.executable`/proměnných
prostředí:

- **`appimage`** — pozná se podle proměnné `APPIMAGE`, kterou nastavuje AppImage runtime. Používá
  se jako stabilní cesta pro `.desktop` `Exec=` i cíl auto-update výměny, protože `sys.executable`
  by uvnitř AppImage ukazoval do dočasného mount pointu, který se mezi spuštěními mění.
- **`deb`** — pozná se podle toho, že `sys.executable` leží pod `/usr/` nebo `/opt/`. V tomto
  režimu appka: config.json ukládá do `~/.config/ffmpeg-master/` (ne vedle binárky, ta je
  root-owned), sebe-registraci `.desktop` záznamu přeskočí (o to se postará balíček), a auto-update
  řeší stažením nového `.deb` z GitHub Release a instalací přes `pkexec dpkg -i` (vyskočí systémové
  heslové okno, stejné jako u jakéhokoliv jiného rootovského zásahu na Linuxu) — po úspěšné
  instalaci appka sama zavře a znovu spustí novou verzi.
- **`portable`** — cokoliv jiného zmrazeného (ruční/lokální PyInstaller build spuštěný odkudkoliv).
  Původní chování, cesta ze `sys.executable`.

### NVIDIA hybridní grafika (Optimus) — PRIME render offload

Na noteboocích s Intel + NVIDIA grafikou v režimu „On-Demand" NVIDIA karta bez explicitního
„probuzení" pro CUDA/NVENC neexistuje — ffmpeg pak na `hevc_nvenc`/`h264_nvenc` padá s
`CUDA_ERROR_NO_DEVICE: no CUDA-capable device is detected`, i když je ovladač v pořádku
nainstalovaný. Appka proto při každém spuštění ffmpeg subprocessu na Linuxu nastaví
`__NV_PRIME_RENDER_OFFLOAD=1` a `__GLX_VENDOR_LIBRARY_NAME=nvidia` (stejný mechanismus jako
`prime-run <příkaz>`) — neškodí to na systémech bez hybridní grafiky, proměnné se prostě ignorují.

Pozor, tohle neřeší situaci, kdy NVIDIA ovladač vůbec neběží (`nvidia-smi` hlásí "couldn't
communicate with the NVIDIA driver"). To bývá nejčastěji Secure Boot, který odmítne nepodepsaný/
špatně zapsaný DKMS modul (`modprobe: ERROR: could not insert 'nvidia': Key was rejected by
service`) — řeší se zapsáním MOK klíče (`sudo mokutil --import /var/lib/shim-signed/mok/MOK.der`,
restart, „Enroll MOK" na modré obrazovce) nebo vypnutím Secure Bootu v UEFI.

### Podpora starších distribucí

PyInstaller onefile binárka je dynamicky slinkovaná proti glibc/Tcl-Tk knihovnám sestavovacího
stroje — binárka postavená na novějším systému na starším nenaběhne (`GLIBC_2.3x not found`).
Protože `ubuntu-latest` runner odpovídá aktuální Ubuntu LTS (dnes 24.04, glibc 2.39) a David cílí
jen na svůj vlastní Mint (na bázi 24.04, tedy stejná glibc), nemá smysl kvůli tomu komplikovat
build starším kontejnerem — podpora Mintu/Ubuntu 22.04 a starších se záměrně neřeší. Pokud by to
časem bylo potřeba, řešení je sestavit uvnitř staršího Docker image (např. `ubuntu:22.04`) — glibc
je zpětně kompatibilní, takže starší základna dělá binárku univerzálnější, nikdy ne méně
kompatibilní.

## Ruční lokální build/spuštění (bez CI)

Na Linuxu potřeba systémový Tkinter (`pip` ho neřeší, je to binární knihovna mimo PyPI):

```bash
sudo apt install python3-tk tk-dev
```

Na Debian/Ubuntu odvozených distribucích (Mint apod.), pokud tam už je z apt nainstalovaný
`python3-pil` (běžné - bývá závislost jiných desktopových nástrojů), přijde appka o `ImageTk`
(`cannot import name 'ImageTk' from 'PIL'`) - ten je v apt balíčku úmyslně vyndaný zvlášť, protože
závisí na Tkinteru. Netýká se to CI ani hotových `.deb`/AppImage buildů (tam je Pillow buď čistě
z `pip`, nebo rovnou zabalené uvnitř PyInstaller binárky) - jen ručního spuštění `.pyw` ze zdrojáku
systémovým Pythonem. Bez tohohle appka neshodí, jen doskočí na textový fallback místo loga/ikonky:

```bash
sudo apt install python3-pil.imagetk
```

```bash
pip install -r requirements.txt pyinstaller

# Windows
pyinstaller --noconfirm --onefile --windowed --name FFMPEG_Master \
  --icon favicon.ico --add-data "favicon.ico;." --add-data "favicon.png;." \
  --collect-all tkinterdnd2 --collect-all customtkinter \
  --hidden-import PIL._tkinter_finder FFMPEG_Master_v0.7.1.pyw

# Linux
pyinstaller --noconfirm --onefile --windowed --name ffmpeg-master \
  --add-data "favicon.ico:." --add-data "favicon.png:." \
  --collect-all tkinterdnd2 --collect-all customtkinter \
  --hidden-import PIL._tkinter_finder FFMPEG_Master_v0.7.1.pyw
```

Ruční sestavení AppImage/`.deb` z výsledku výše (mimo CI) — viz odpovídající kroky v
`.github/workflows/build.yml` (`Sestav .deb balíček` / `Sestav AppImage`), jde je spustit i lokálně
na jakékoliv Debian/Ubuntu odvozené distribuci (potřeba nástroje `dpkg-deb`, `wget`, `file`).
