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

Desktopová aplikace pro Windows i Linux (Python + CustomTkinter) pro dávkovou konverzi videa přes **FFmpeg** — hromadné překódování do HEVC (NVENC na Windows, na Linuxu i software/VA-API kodeky), výběr audio/titulkových stop, normalizace hlasitosti (loudnorm, 2-průchodová analýza), generování NFO a přesun/přejmenování zpracovaných souborů.

## Funkce

- Drag & drop i výběr souborů, fronta ke zpracování s možností řazení (nahoru/dolů), úpravy (tlačítko „Upravit“) a mazání položek
- **Dialog výběru stop** při přidání i úpravě každého souboru — tabulka audio/titulkových stop (kodek, jazyk, kanály, bitrate, název), checkboxy s auto-výběrem podle nastavených jazyků, varování u obrázkových PGS titulků, tlačítko „Výchozí výběr“, volitelné per-soubor vypnutí NFO a „Kopírovat video beze změny“ pro poškozené/problematické zdroje
- **`config.json`** vedle programu — cesty k `ffmpeg`/`ffprobe`, výstupní/cílová složka, CQ/preset/rozlišení, audio bitrate, parametry `loudnorm`, generování NFO, chování se zdrojem zvlášť pro lokální a síťové disky (přesun/přejmenování/ponechat), motiv, uložená geometrie okna
- **Menu lišta**: Nastavení (editace `config.json` z UI, včetně přepínače na vlastní FFmpeg příkaz) a O programu (verze, logo, kontakt, kontrola aktualizací)
- **Kontrola aktualizací z GitHub Releases** — tichá kontrola při startu s nenápadným oznámením, manuální kontrola i stažení/instalace nové verze (na Windows `.exe`, na Linuxu jako AppImage)
- Překódování do HEVC (`hevc_nvenc`, 10bit `p010le`), parametry CQ/preset/rozlišení podle configu, nebo plně vlastní FFmpeg parametry
- Normalizace hlasitosti dvouprůchodovým `loudnorm` samostatně pro každou vybranou audio stopu (down-mix na stereo → přesné změření → normalizace), s fallbackem na 1-průchodový režim
- **NFO generátor** s editovatelným titulem, rokem, žánrem (dropdown z configu) a plotem, volitelně vypnutelný pro konkrétní soubor
- Log průběhu (včetně hlášek o jednotlivých průchodech), progress bar na soubor i celkový, zvukové upozornění po dokončení

## Stažení hotové verze (doporučeno)

Na [stránce vydání](https://github.com/DaTTcz/FFMPEG-Master/releases/latest) jsou ke stažení dva soubory pro každou verzi:

- **`FFMPEG_Master.exe`** — Windows
- **`FFMPEG_Master-x86_64.AppImage`** — univerzální varianta pro jakoukoliv Linux distribuci (Mint, Ubuntu, Fedora, Arch, openSUSE...) — stačí `chmod +x` a spustit, žádná instalace do systému. Vyžaduje `libfuse2`/`libfuse2t64` (na novějších distribucích, které FUSE2 nemají v základu, doinstaluj — bez něj hlásí AppImage chybu o chybějícím `libfuse.so.2`; případně spusť s `--appimage-extract-and-run`)

## Požadavky

- Windows nebo Linux
- Nainstalovaný [FFmpeg](https://ffmpeg.org/) — na Windows `ffmpeg.exe`/`ffprobe.exe` (výchozí cesta `C:\FFMPEG\bin\...`, upravitelná v Nastavení), na Linuxu se očekává `ffmpeg`/`ffprobe` v `PATH`
- Python 3.10+ s balíčky ze `requirements.txt` (`customtkinter`, `tkinterdnd2`, `pillow`) — na Linuxu navíc systémový Tk/Tcl (`sudo apt install python3-tk tk-dev` na Debianu/Ubuntu) — **jen pro spuštění ze zdrojového `.pyw`**, hotové binárky/balíčky výše si vše potřebné nesou samy
- GPU s podporou NVENC (HEVC) pro hardwarové kódování — na Linuxu bez proprietárního NVIDIA driveru je potřeba v Nastavení přepnout na jiný kodek (`libx265`/`libx264`, nebo VA-API `hevc_vaapi`)

```bash
pip install -r requirements.txt
```

## Spuštění ze zdrojového kódu

```bash
python FFMPEG_Master_v0.7.0.pyw
```

Při prvním spuštění se vedle scriptu (nebo vedle zabalené binárky/AppImage, viz [BUILD.md](BUILD.md)) vytvoří `config.json` s výchozími hodnotami — uprav si ho přes menu **Nastavení** v aplikaci, nebo ručně (vzor v [config.example.json](config.example.json)).

## Licence

Tento projekt je licencován pod [PolyForm Noncommercial License 1.0.0](LICENSE) — volné použití pro nekomerční účely (osobní, výzkumné, vzdělávací atd.), komerční použití vyžaduje samostatnou licenci od autora.

## Autor

David Trubka (DaTT.cz)
