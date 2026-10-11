<p align="center">
  <img src="favicon.png" alt="FFMPEG Master logo" width="200">
</p>
<p align="center">
  <a href="https://www.paypal.com/paypalme/DaTTcz">
    <img src="https://img.shields.io/badge/%E2%9D%A4%EF%B8%8F_Podpo%C5%99_projekt-PayPal-ffc439?style=for-the-badge&logo=paypal&logoColor=ffc439&labelColor=003087" alt="Podpořit přes PayPal">
  </a>
  &nbsp;&nbsp;
  <a href="https://ko-fi.com/dattcz">
    <img src="https://img.shields.io/badge/%E2%98%95_Ko--fi-dattcz-ff5e5b?style=for-the-badge&logo=ko-fi&logoColor=white" alt="Podpořit na Ko-fi">
  </a>
</p>

# FFMPEG Master

Desktopová aplikace pro Windows i Linux (Python + CustomTkinter) pro dávkovou konverzi videa přes **FFmpeg** — hromadné překódování do H.264, HEVC nebo AV1 (NVENC na Windows, na Linuxu i software/VA-API kodeky), výběr audio/titulkových stop, normalizace hlasitosti (loudnorm, 2-průchodová analýza), generování NFO a přesun/přejmenování zpracovaných souborů.

## Funkce

- Drag & drop i výběr souborů, fronta ke zpracování s možností řazení (nahoru/dolů), úpravy (tlačítko „Upravit“) a mazání položek
- **Dialog výběru stop** při přidání i úpravě každého souboru — tabulka audio/titulkových stop (kodek, jazyk, kanály, bitrate, název), checkboxy s auto-výběrem podle nastavených jazyků, varování u obrázkových PGS titulků, tlačítko „Výchozí výběr“, volitelné per-soubor vypnutí NFO a „Kopírovat video beze změny“ pro poškozené/problematické zdroje
- **`config.json`** vedle programu — cesty k `ffmpeg`/`ffprobe`, výstupní/cílová složka, CQ/preset/rozlišení, audio bitrate, parametry `loudnorm`, generování NFO, chování se zdrojem zvlášť pro lokální a síťové disky (přesun/přejmenování/ponechat), motiv, uložená geometrie okna
- **Menu lišta**: Nastavení (editace `config.json` z UI, včetně přepínače na vlastní FFmpeg příkaz) a O programu (verze, logo, kontakt, kontrola aktualizací)
- **Kontrola aktualizací z GitHub Releases** — tichá kontrola při startu s nenápadným oznámením, manuální kontrola i stažení/instalace nové verze (Windows `.exe`, Linux AppImage, `.deb` i `.rpm`)
- Překódování do **HEVC** (`hevc_nvenc`) nebo **AV1** (`av1_nvenc`, NVIDIA RTX 40xx a novější), obojí 10bit `p010le`, případně **H.264** (`h264_nvenc`, 8bit High@4.1 pro starší přehrávače a TV); zvuk **Opus** nebo **AAC** (u H.264 vždy AAC) — volba kodeku v Nastavení (každý kodek si pamatuje vlastní CQ/preset, tlačítko „Otestovat“ ověří, že ho grafika umí) i u jednotlivého souboru, parametry CQ/preset/rozlišení podle configu, nebo plně vlastní FFmpeg parametry
- Normalizace hlasitosti dvouprůchodovým `loudnorm` samostatně pro každou vybranou audio stopu (down-mix na stereo → přesné změření → normalizace), s fallbackem na 1-průchodový režim
- **NFO generátor** s editovatelným titulem, rokem, žánrem (dropdown z configu) a plotem, volitelně vypnutelný pro konkrétní soubor
- **Uložení/načtení fronty (`.ffm`)** — menu Fronta (Ctrl+S / Ctrl+O), dvojklik na `.ffm` ve správci souborů (Windows i Linux), přetažení `.ffm` do okna nebo `ffmpeg-master fronta.ffm`; ukládá vybrané stopy, NFO, ořez i střih, při načtení zdroje znovu ověří (velikost, délka, stopy)
- **Ořez černých okrajů** — automatická detekce (cropdetect na 8 místech filmu, největší obdélník), náhled s vyznačeným ořezem a ruční doladění po stranách; při překódování filtr `crop`, při kopírování videa (H.264/HEVC) zápis do streamu bez překódování
- **Střih (smart cut)** — okno pro jeden soubor s náhledem, krokováním po snímku/klíčovém snímku a značkami začátek/konec; úseky „ponechat“ nebo „vystřihnout“ (reklamy). Řez je přesný na snímek: mezi klíčovými snímky se video kopíruje beze ztráty, překódují se jen krátké okraje řezů (H.264, HEVC, MPEG-2), zvuk a titulky se kopírují. Výstup do originálního formátu, do fronty (i se spuštěním) nebo do `.ffm`; úseky jde upravit i u položky ve frontě (tlačítko „Střih…“)
- Log průběhu (včetně hlášek o jednotlivých průchodech), progress bar na soubor i celkový, zvukové upozornění po dokončení

## Stažení hotové verze (doporučeno)

Na [stránce vydání](https://github.com/DaTTcz/FFMPEG-Master/releases/latest) jsou ke stažení čtyři soubory pro každou verzi:

| Soubor | Pro koho |
|---|---|
| **`FFMPEG_Master.exe`** | Windows |
| **`FFMPEG_Master_<verze>_amd64.deb`** | Debian, Ubuntu, Mint, Pop!_OS… — `sudo apt install ./FFMPEG_Master_*_amd64.deb` (doinstaluje i `ffmpeg`) |
| **`FFMPEG_Master-<verze>.x86_64.rpm`** | Fedora, openSUSE — `sudo dnf install ./FFMPEG_Master-*.rpm`, resp. `sudo zypper install --allow-unsigned-rpm ./FFMPEG_Master-*.rpm` |
| **`FFMPEG_Master-x86_64.AppImage`** | jakákoliv jiná distribuce (Arch…) nebo bez instalace — stačí `chmod +x` a spustit |

Linuxové verze potřebují distribuci s glibc 2.39 nebo novější (Ubuntu 24.04+, Mint 22+, Debian 13+, aktuální Fedora, openSUSE Tumbleweed/Slowroll, Arch). Každé vydání se před zveřejněním automaticky testuje na Ubuntu 24.04 a 26.04, Debianu 13, Fedoře, openSUSE Tumbleweed a Archu.

**Fedora / openSUSE:** oficiální `ffmpeg` z repozitářů distribuce kvůli patentům neumí H.264/HEVC ani NVENC. Pro plnou funkčnost přepni ffmpeg na verzi z [RPM Fusion](https://rpmfusion.org/) (Fedora), resp. z Packmanu (openSUSE: `sudo zypper install opi && opi codecs`).

Pokud aplikace hlásí, že nejde analyzovat soubor, v logu je teď i konkrétní důvod. Pro diagnostiku jde spustit i bez GUI: `ffmpeg-master --selftest cesta/k/videu.mkv` (u AppImage `./FFMPEG_Master-x86_64.AppImage --selftest ...`).

## Požadavky

- Windows nebo Linux
- Nainstalovaný [FFmpeg](https://ffmpeg.org/) — na Windows `ffmpeg.exe`/`ffprobe.exe` (výchozí cesta `C:\FFMPEG\bin\...`, upravitelná v Nastavení), na Linuxu se očekává `ffmpeg`/`ffprobe` v `PATH`
- Python 3.10+ s balíčky ze `requirements.txt` (`customtkinter`, `tkinterdnd2`, `pillow`) — na Linuxu navíc systémový Tk/Tcl (`sudo apt install python3-tk tk-dev` na Debianu/Ubuntu) — **jen pro spuštění ze zdrojového `.pyw`**, hotové binárky/balíčky výše si vše potřebné nesou samy
- GPU s podporou NVENC pro hardwarové kódování (H.264/HEVC: GTX 10xx a novější, AV1: RTX 40xx a novější) — na Linuxu bez proprietárního NVIDIA driveru je potřeba v Nastavení přepnout na jiný kodek (`libx265`/`libx264`, nebo VA-API `hevc_vaapi`)

```bash
pip install -r requirements.txt
```

## Spuštění ze zdrojového kódu

```bash
python FFMPEG_Master_v0.9.0.pyw
```

Při prvním spuštění se vedle scriptu (nebo vedle zabalené binárky/AppImage; u instalace z `.deb`/`.rpm` v `~/.config/ffmpeg-master/`, viz [BUILD.md](BUILD.md)) vytvoří `config.json` s výchozími hodnotami — uprav si ho přes menu **Nastavení** v aplikaci, nebo ručně (vzor v [config.example.json](config.example.json)).

## Licence

Tento projekt je licencován pod [PolyForm Noncommercial License 1.0.0](LICENSE) — volné použití pro nekomerční účely (osobní, výzkumné, vzdělávací atd.), komerční použití vyžaduje samostatnou licenci od autora.

## Autor

David Trubka (DaTT.cz)
