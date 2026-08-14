# FFMPEG Master

Desktopová Windows aplikace (Python + CustomTkinter) pro dávkovou konverzi videa přes **FFmpeg** — hromadné překódování do HEVC (NVENC), automatický výběr audio/titulkových stop, normalizace hlasitosti (loudnorm, 2-průchodová analýza), generování NFO a přesun/přejmenování zpracovaných souborů.

> ⚠️ Aktuální stav: **v0.4** — funkční, ale s natvrdo zapsanými cestami a nastavením přímo ve zdrojovém kódu. V0.5 přináší plnou konfigurovatelnost (viz [Roadmap](#roadmap-v05) níže).

## Funkce (v0.4)

- Drag & drop i výběr souborů, fronta ke zpracování s možností řazení (nahoru/dolů) a mazání položek
- Analýza vstupního souboru přes `ffprobe` — rozlišení, délka, dostupné audio a titulkové stopy
- Automatický výběr české (CZE/CES) audio stopy a titulků, s fallbackem na stopu bez jazyka nebo originál
- Překódování do HEVC (`hevc_nvenc`, 10bit `p010le`, `-cq 28.5`, `-preset p7`)
- Normalizace hlasitosti dvouprůchodovým `loudnorm` (I=-18, TP=-1.5, LRA=11) s fallbackem na 1-průchodový režim
- Generování `.nfo` souboru k výstupu
- Přesun hotového souboru do cílové složky nebo přejmenování na místě
- Log průběhu, progress bar na soubor i celkový, zvukové upozornění po dokončení
- Splash screen s logem při startu

## Požadavky

- Windows (používá `ctypes`/`shell32` pro ikonu v liště a NVENC hardwarové kódování)
- Nainstalovaný [FFmpeg](https://ffmpeg.org/) (`ffmpeg.exe`, `ffprobe.exe`)
- Python 3.10+ s balíčky: `customtkinter`, `tkinterdnd2`, `Pillow` (volitelně, pro splash logo)
- GPU s podporou NVENC (HEVC) pro hardwarové kódování

```bash
pip install customtkinter tkinterdnd2 pillow
```

## Spuštění

```bash
python FFMPEG_Master_v0_4.pyw
```

V0.4 má cesty k FFmpeg a výstupním/cílovým složkám natvrdo v kódu (`FFMPEG_PATH`, `FFPROBE_PATH`, `BASE_OUT_DIR`, `BASE_DONE_DIR`) — před spuštěním je nutné upravit přímo ve zdrojovém souboru.

## Roadmap v0.5

Plánované změny pro příští verzi:

**`config.json`**
- Cesty k `ffmpeg`/`ffprobe`, výstupní složka, složka pro přesun
- CQ, preset, rozlišení, audio bitrate, parametry `loudnorm`
- Přepínače pro generování NFO a přejmenování/přesun zdroje
- Motiv (theme), geometrie okna, uložená pozice

**Dialog výběru stop** (při přidání každého souboru)
- Tabulka všech stop s vlastnostmi — kodek, jazyk, kanály, bitrate, název
- Checkboxy s auto-výběrem CZE, možnost ruční změny uživatelem
- Varování u PGS titulků
- Tlačítko „Výchozí výběr“

**Menu lišta**
- Nastavení — dialog nad `config.json`
- O programu — verze, logo, DaTT.cz, kontakt, kontrola aktualizací

**Aktualizace z GitHubu**
- Kontrola při startu, nenápadné oznámení
- Stažení nového `.exe` z GitHub Releases
- Manuální kontrola v „O programu“

**NFO generátor**
- Žánr jako dropdown místo natvrdo zapsaného „Animovaný“
- Další pole editovatelná

**Distribuce**
- PyInstaller → jeden `.exe`
- `config.json` vedle exe
- Příprava na cross-platform (`sys.platform` pro `creationflags`)

## Licence

Tento projekt je licencován pod [PolyForm Noncommercial License 1.0.0](LICENSE) — volné použití pro nekomerční účely (osobní, výzkumné, vzdělávací atd.), komerční použití vyžaduje samostatnou licenci od autora.

## Autor

DaTT.cz
