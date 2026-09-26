# Build — FFMPEG Master

Aplikace běží na Windows i Linuxu. Hotové binárky staví GitHub Actions (viz níže). Ruční
PyInstaller build zůstává pro lokální testování.

## Automatický build na GitHubu (doporučeno)

`.github/workflows/build.yml` má tři úrovně podle toho, co workflow spustí:

- **push do `main`** → jen rychlá kontrola (`quick-check`): nainstalují se závislosti, hlavní
  skript se zkompiluje (`python -m py_compile`) a proběhne `--selftest` proti ffmpeg z Ubuntu
  s malým testovacím videem. Trvá to pár desítek vteřin a nesestavuje se žádná binárka.
- **ruční spuštění** (**Actions → Build binaries → Run workflow**) → quick-check + plný build
  všech balíčků + test na distribucích, ale **bez vydání releasu**. Hodí se na vyzkoušení změn
  v buildu; hotové soubory jsou ke stažení jako artefakty daného běhu.
- **push tagu `v*`** (např. `v0.7.4`) → plný build, test na distribucích a GitHub Release:
  1. Windows `.exe` (na `windows-latest`),
  2. Linux PyInstaller binárka (na `ubuntu-24.04`, viz „Minimální verze distribuce") a z ní:
     - univerzální `FFMPEG_Master-x86_64.AppImage`,
     - `FFMPEG_Master_<verze>_amd64.deb` (Debian/Ubuntu/Mint),
     - `FFMPEG_Master-<verze>.x86_64.rpm` (Fedora/openSUSE),
  3. test všech linuxových balíčků v kontejnerech Ubuntu 24.04 a 26.04, Debian 13, Fedora, openSUSE
     Tumbleweed a Arch (viz níže),
  4. GitHub Release se všemi čtyřmi soubory — **jen pokud všechny testy prošly**.

Tag musí odpovídat `VERSION` v hlavním skriptu (`VERSION = "v0.7.4"` → tag `v0.7.4`), jinak build
skončí chybou. Vydání nové verze tedy vypadá takto:

```bash
git tag v0.7.4
git push origin v0.7.4
```

Workflow si hlavní skript hledá sám (`FFMPEG_Master_v*.pyw` v kořeni repozitáře s nejvyšší verzí
přes `sort -V`), takže při nové verzi stačí vytvořit nový `.pyw` — workflow ani spec se měnit nemusí.

## Soubory pro Linux (`packaging/linux/`)

| Soubor | K čemu |
|---|---|
| `ffmpeg-master.spec` | PyInstaller spec — onefile binárka `dist/ffmpeg-master`, vyřazení systémových knihoven z bundlu |
| `nfpm.yaml` | popis `.deb` i `.rpm` balíčku pro [nfpm](https://nfpm.goreleaser.com) (obsah, závislosti, skripty) |
| `ffmpeg-master.desktop` | položka v menu (sdílí ji AppImage i balíčky) |
| `postinst.sh` | po instalaci/odinstalaci balíčku obnoví menu a cache ikon |

Všechny tři linuxové formáty vznikají ze **stejné** PyInstaller binárky — žádná duplicitní práce.

## Univerzální Linux build — co je potřeba ohlídat

Binárka z PyInstalleru nese vlastní knihovny z Ubuntu 24.04, kde se staví. Aby fungovala na
jakékoliv novější distribuci, řeší se tři věci:

### 1. Spouštěné systémové programy nesmí dědit zabalené knihovny

PyInstaller při startu nastaví `LD_LIBRARY_PATH` na svou dočasnou složku `/tmp/_MEIxxxx`. Tuto
proměnnou dědí každý program, který appka spustí — `ffprobe`, `ffmpeg`, `pkexec`, `xdg-open`... Ty
pak místo knihoven svého systému načtou ty zabalené. Na Ubuntu/Mintu 24.04 to nevadí, ale na
rolling distribucích systémový ffprobe spadne dřív, než otevře soubor, např. na openSUSE Slowroll:

```
ffprobe: symbol lookup error: /lib64/libldap.so.2: undefined symbol: EVP_md2, version OPENSSL_3.0.0
```

(Zabalený `libcrypto` z Ubuntu vs. systémový `libldap`.) Do v0.7.3 se to projevovalo jen hláškou
„Nelze analyzovat soubor (žádné video?)". Od v0.7.4 appka hned při startu vrátí `LD_LIBRARY_PATH`
na původní hodnotu (`_restore_system_library_env()` — PyInstaller ji ukládá do
`LD_LIBRARY_PATH_ORIG`). Běžící appku to neovlivní, dynamický linker si proměnnou čte jen při
startu procesu. Log navíc při selhání ffprobe vypíše skutečný důvod.

### 2. Knihovny písem se berou ze systému

`fontconfig` čte konfiguraci z `/etc/fonts`. Starší zabalená verze z Ubuntu nerozumí syntaxi
novějších distribucí → záplava `Fontconfig warning: ... invalid attribute 'xsi:nil'` a písma
vypadají jinak než ve zbytku systému. `ffmpeg-master.spec` proto z bundlu vyřadí `fontconfig`,
`freetype` a jejich závislosti (`libpng16`, `brotli`, `expat`) — odpovídá to oficiálnímu AppImage
„excludelistu" knihoven, které má každý desktopový Linux. Balíčky `.deb`/`.rpm` je mají
v závislostech.

### 3. AppImage bez `libfuse2`

AppImage se staví novým [appimagetool](https://github.com/AppImage/appimagetool) (verze
připnutá v `build.yml`), který přibalí statický runtime. Hotová AppImage tak už nepotřebuje
`libfuse2`/`libfuse2t64`, kterou novější distribuce nemají v základu.

### Minimální verze distribuce

Binárka je slinkovaná proti glibc build stroje, proto je `build-linux` záměrně připnutý na
`ubuntu-24.04` (glibc 2.39), ne na `ubuntu-latest` (ten se od října 2026 přepíná na 26.04 a build
z něj by na starších systémech nenaběhl). Běží tak na Ubuntu
24.04+, Mintu 22+, Debianu 13+, aktuální Fedoře, openSUSE Tumbleweed/Slowroll a Archu. Starší
distribuce (Ubuntu 22.04, Debian 12) se záměrně nepodporují. Kdyby to bylo potřeba, řešení je
stavět uvnitř staršího kontejneru (např. `ubuntu:22.04`) — glibc je zpětně kompatibilní.

## Test na distribucích (`test-linux`)

Po buildu se v kontejnerech jednotlivých distribucí:

1. nainstaluje `.deb` (Ubuntu, Debian) nebo `.rpm` (Fedora, openSUSE) **přes správce balíčků**
   — tím se ověří i závislosti (ffmpeg, knihovny písem),
2. spustí `ffmpeg-master --selftest test.mkv` a totéž s AppImage (na Archu jen AppImage),
   pod virtuálním X serverem (Xvfb).

`--selftest` (funkce `run_selftest()` v hlavním skriptu) nic nezapisuje a ověří, že:

- binárka naběhne (Python + zabalený Tk),
- `LD_LIBRARY_PATH` pro spouštěné programy neobsahuje zabalené knihovny,
- ffmpeg a ffprobe z configu (nebo z `PATH`) jde spustit, a vypíše dostupné NVENC enkodéry,
- jde vytvořit okno Tk s drag&drop (tkdnd) a změřit text písmem přes systémový fontconfig
  (jen když je nastavený `DISPLAY`),
- testovací video projde stejnou analýzou jako při přidání do fronty.

Test navíc selže, pokud se na stderr objeví `Fontconfig warning`. Stejný příkaz jde spustit
i ručně pro diagnostiku na libovolném počítači:

```bash
ffmpeg-master --selftest ~/Videos/nejake-video.mkv
./FFMPEG_Master-x86_64.AppImage --selftest ~/Videos/nejake-video.mkv
```

## Jak appka pozná, čím byla spuštěná

`linux_run_mode()` v hlavním skriptu rozliší tyto režimy:

- **`appimage`** — pozná se podle proměnné `APPIMAGE`, kterou nastavuje AppImage runtime. Používá
  se jako stabilní cesta pro `.desktop` `Exec=` i cíl auto-update výměny, protože `sys.executable`
  by uvnitř AppImage ukazoval do dočasného mount pointu. Config je vedle `.AppImage` souboru.
  Auto-update stáhne novou AppImage a nahradí starou.
- **`deb`** / **`rpm`** — `sys.executable` leží pod `/usr/` nebo `/opt/`; který z nich, se pozná
  z databáze balíčků (`/var/lib/dpkg/info/ffmpeg-master.list`, resp. `rpm -q ffmpeg-master`).
  V tomto režimu appka ukládá config do `~/.config/ffmpeg-master/` (binárka je root-owned),
  sebe-registraci `.desktop` záznamu přeskočí (o to se postará balíček) a auto-update řeší stažením
  nového `.deb`/`.rpm` z GitHub Release a instalací přes `pkexec dpkg -i`, resp.
  `pkexec rpm -U` (vyskočí systémové heslové okno). Po instalaci se appka sama restartuje.
- **`portable`** — cokoliv jiného zmrazeného (ruční PyInstaller build spuštěný odkudkoliv).
  Config vedle binárky.
- **`script`** — spuštěno jako `.pyw` ze zdrojáků.

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

### ffmpeg na Fedoře a openSUSE

Oficiální `ffmpeg` z repozitářů Fedory (`ffmpeg-free`) i openSUSE je kvůli patentům ořezaný —
neumí H.264/HEVC ani NVENC. `.rpm` balíček proto závisí jen na souborech `/usr/bin/ffmpeg`
a `/usr/bin/ffprobe`, což splní jakákoliv varianta (oficiální, RPM Fusion, Packman). Pro plnou
funkčnost je potřeba přepnout na RPM Fusion (Fedora) nebo Packman (openSUSE: `opi codecs`).

## Ruční lokální build/spuštění (bez CI)

Na Linuxu je potřeba systémový Tkinter (`pip` ho neřeší, je to binární knihovna mimo PyPI):

```bash
sudo apt install python3-tk tk-dev          # Debian/Ubuntu/Mint
sudo zypper install python3-tk              # openSUSE
sudo dnf install python3-tkinter            # Fedora
```

Na Debian/Ubuntu odvozených distribucích (Mint apod.), pokud tam už je z apt nainstalovaný
`python3-pil` (běžné — bývá závislost jiných desktopových nástrojů), přijde appka o `ImageTk`
(`cannot import name 'ImageTk' from 'PIL'`) — ten je v apt balíčku úmyslně vyndaný zvlášť, protože
závisí na Tkinteru. Netýká se to CI ani hotových buildů — jen ručního spuštění `.pyw` ze zdrojáku
systémovým Pythonem. Bez toho appka nespadne, jen použije textový fallback místo loga/ikonky:

```bash
sudo apt install python3-pil.imagetk
```

```bash
pip install -r requirements.txt pyinstaller

# Windows
pyinstaller --noconfirm --onefile --windowed --name FFMPEG_Master \
  --icon favicon.ico --add-data "favicon.ico;." --add-data "favicon.png;." \
  --collect-all tkinterdnd2 --collect-all customtkinter \
  --hidden-import PIL._tkinter_finder FFMPEG_Master_v0.7.4.pyw

# Linux (výsledek: dist/ffmpeg-master)
pyinstaller --noconfirm packaging/linux/ffmpeg-master.spec
dist/ffmpeg-master --selftest
```

Balíčky z linuxové binárky (potřeba [nfpm](https://nfpm.goreleaser.com/docs/install/)):

```bash
VERSION=0.7.4 nfpm package -f packaging/linux/nfpm.yaml -p deb -t FFMPEG_Master_0.7.4_amd64.deb
VERSION=0.7.4 nfpm package -f packaging/linux/nfpm.yaml -p rpm -t FFMPEG_Master-0.7.4.x86_64.rpm
```

Sestavení AppImage — viz krok `Sestav AppImage` v `.github/workflows/build.yml`, jde spustit
i lokálně (potřeba `wget`).
