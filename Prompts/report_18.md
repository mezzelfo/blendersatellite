Tutte e cinque le parti (A–E) sono finite e nessun criterio di STOP è scattato. La nuova cache dei 570 tile è completa, convertita nel frame di render, e il render dei frame 0 e 500 del chief non ha alcuno spostamento rispetto alle immagini esistenti. Ho corretto un bug vero nella conversione e aggiunto alcune funzioni mancanti. Due risultati non coincidono con le attese del piano; li spiego più sotto. `data/swarm_terrain` è intatta (cartella ancora datata 5 novembre) e non ho fatto commit.

## A. Pre-check

| Check | Risultato |
|---|---|
| A.1 i quattro file esistono e compilano | ✅ Ma sono **già tracciati da git**, nel commit `145b0e0`, quindi `git status` non li mostra come nuovi. Non li ho considerati mancanti e ho proseguito. |
| A.2 RUN | `output/orbits_melbourne/20261109T180310Z_run_v2` |
| A.3 ambiente e URL | numpy 2.5.4, scipy 1.18.1, PIL 13.0.0, pyproj 3.8.0 (Python 3.12.16); HEAD su `A1-11.zip` risponde 200 |
| A.4 risorse | 81 GB liberi su `/`, 60 GB RAM (55 disponibili), 24 CPU |

Sulla scelta di RUN: tutti e 6 i `cameras.json` del repo hanno lo stesso `origin_ecef_m`, `local_from_ecef` e spacing di `cache_frame.json`. Ho scelto `run_v2` perché è quello che ha prodotto i render di `eevee_full_1000` (lo dice `render_report.jsonl`), e i suoi 570 `selected_tiles` coincidono esattamente con i 570 `.npz` della vecchia cache.

## B. Test sintetico (`scripts/test_terrain_cache.py`): 27/27 passati

| Assert | SMALL 1 m | SMALL 2.5 m | BIG 1 m | BIG 2.5 m |
|---|---|---|---|---|
| copertura 100% | ✅ 1.0000 | ✅ | ✅ | ✅ |
| tetto = 30 | ✅ 30.000000 | ✅ | ✅ | ✅ |
| suolo in (50.5, 50.5) | ✅ 0.505000 | ✅ 0.512500 (*) | ✅ 0.000000 | ✅ 0.000000 |
| rgb in (200.5, 250.5) entro 2 DN da (170, 213, 50) | ✅ (171, 213, 51) | ✅ (171, 214, 50) | ✅ (171, 213, 51) | ✅ (171, 214, 50) |
| x0, y0 multipli dello spacing | ✅ | ✅ | ✅ | ✅ |
| riga 0 = sud (test mio in più) | ✅ | ✅ | ✅ | ✅ |
| griglie di render 1 m vs 2.5 m | ✅ max\|dvert\| 0.00050 m, max\|dRGB\| 1 DN | | ✅ 0.00000 m, 1 DN | |
| binning senza bias (test mio in più) | ❌ sul codice originale (0.0025 m), ✅ dopo la correzione | | | |

(*) A 2.5 m la cella che contiene (50.5, 50.5) è centrata in 51.25, quindi la media vale 0.5125, non 0.505. Il test confronta con il valore al centro cella.

## Modifiche ai file (diff completo con `git diff`)

1. **`terrain_to_render.py`, `bin_to_spacing`: bug corretto.** Ogni cella da 1 m finiva intera nel bin da 2.5 m che conteneva il suo centro. Siccome 2.5/1 non è intero, il baricentro di ogni bin risultava spostato di -0.25 m in x e in y: tutto il terreno convertito era traslato di circa 0.35 m verso sud-ovest, cioè circa 0.07 px. Ora i pesi sono proporzionali all'area di sovrapposizione; se cache e render hanno lo stesso spacing il risultato non cambia. Ho aggiornato la docstring.
2. **`terrain_to_render.py`:** `--output` è ora facoltativo, con default `RUN/terrain_render` come richiesto.
3. **`terrain_cache.py`:**
   - l'output di default è `data/terrain` (prima `data/terrain_cache`): il comando del pilota, che non passa `--output`, si aspettava `data/terrain/...`;
   - nuova funzione `describe_dataset` che scrive `dataset.json` e `tile_grid.json` (copiato da `data/melbourne_tile_grid.json`, altrimenti scaricato).
4. **`scripts/compare_terrain_caches.py`:** nuova opzione `--max-slope`, spenta di default. La pendenza è calcolata sulla vecchia griglia con differenze centrali, prendendo il massimo sull'intorno 3×3, così le celle accanto al bordo di un tetto non contano come piane.

Le convenzioni della cache (griglia allineata a multipli dello spacing, riga 0 = sud, altezza e colore come media delle sotto-celle coperte) non le ho toccate.

## C. Pilota

| Tile | Archivio MB | Download s | Totale s | Cache MB | Coperto | Facce | Shape |
|---|---|---|---|---|---|---|---|
| A1-11 | 33.4 | 12.8 | 16.7 | 0.085 | 57.3% | 226,281 | 126×192 |
| A1-12 | 30.9 | 9.2 | 12.6 | 0.078 | 64.2% | 203,430 | 127×153 |
| A1-16 | 67.6 | 16.3 | 23.2 | 0.177 | 51.4% | 442,162 | 213×264 |
| D4-04 (prova sul tile più grande, in più) | 821.1 | 132.0 | 276.1 | 0.48 | 99.3% | 7,744,167 | 282×282 |

- **Picco di memoria (RSS):** 216 MB nel pilota, 2.79 GB su D4-04.
- **Pulizia:** dopo il pilota `downloads/` era vuota e `dataset.json` e `tile_grid.json` erano presenti (`tile_grid.json` è identico byte per byte a quello originale).

**C.3: l'attesa nuovo ≥ vecchio non è rispettata se si confrontano le frazioni.** Vecchio 0.5886 / 0.6500 / 0.5264, nuovo a 1 m 0.5735 / 0.6418 / 0.5145. È un effetto della risoluzione: sui bordi irregolari della mesh una cella da 1 m segue il contorno più da vicino di una da 2.5 m. A parità di griglia (2.5 m) l'area coperta è invece maggiore su tutti e 3: 14 750 contro 14 162 m², 13 231 contro 12 638, 30 225 contro 29 644.

**C.4: proiezione e consuntivo**

| | Proiezione | Misurato |
|---|---|---|
| Download totale | 113.67 GB (HEAD su tutti i 570 tile) | 113.67 GB |
| W | 12 (12 × 2.79 GB = 33.5 GB, il 56% della RAM) | — |
| Tempo con W = 12 | circa 0.9 h | circa 75 min (40 + 34.6), limitato dalla banda: circa 1.5 GB/min aggregati |
| Picco su disco | al massimo 9.9 GB, contro una soglia di 71 GB | 3.8 GB osservati |
| Cache finale | 100–285 MB | 236 MB |

## D. Run completo e conversione

- **D.1:** 570/570 tile, 0 fallimenti, nessun rerun necessario.
  - Il run interrotto per lo stacco della SSH ha completato 321 tile. Ho cancellato gli archivi a metà e ripreso: la ripresa ha fatto i 249 rimanenti in 34 min 37 s, con picco di 2.73 GB.
  - `log.jsonl` ha 564 righe invece di 570: lo script scrive il log nell'ordine di invio, quindi 6 tile finiti prima dell'interruzione non sono stati registrati. I loro `.npz` sono completi.
- **D.2:** 14 s. 570 tile, 7,024,880 vertici, 13,797,886 triangoli (conteggio esatto), 87 MB su disco. La vecchia cache: 6,960,533 vertici, 13,670,356 triangoli, 87 MB.
  - Lo script stampa "14,049,760 triangoli circa" perché stima semplicemente 2 × vertici; non l'ho cambiato.

## E. Validazione

**E.1, tutte le coppie (6,961,409):** \|dz\| mediana 0.33 m, p95 6.98 m, p99 15.55 m; \|dRGB\| media 23.2 DN, p95 84.3 DN.

I 10 tile peggiori per `dz_p95_m` sono C4-26 (33.4), D4-30 (30.1), C4-24 (29.4), C4-27 (28.7), C4-03 (27.6), C4-09 (27.2), D4-04 (26.8), C4-19 (25.8), C4-11 (25.3), E5-01 (23.9). Sono tutti tile del CBD con grattacieli, coerenti con i bordi dei tetti.

**E.2, solo zone piane (pendenza < 0.1, 1,305,601 coppie):** \|dz\| mediana 0.007 m, p95 0.137 m, p99 0.60 m. La geometria quindi è a posto.

Qui la seconda differenza dalle attese: \|dRGB\| medio vale **10.3 DN** (p95 40.7), più dei "pochi DN" previsti. La spiegazione, misurata:
- la vecchia cache prendeva un solo texel per cella, la nuova fa la media di 16 sotto-campioni per cella da 1 m;
- non c'è bias di colore: la differenza media con segno è -0.21 / -0.25 / -0.11 DN in R/G/B;
- dove la vecchia griglia è omogenea (deviazione standard 3×3 sotto 6 DN, il 44% delle coppie piane) la mediana di \|dRGB\| è 2–3 DN;
- la differenza cresce con la tessitura e arriva a una mediana di 15.7 DN dove la deviazione standard supera 20 DN.

**E.3:**
- frazione media coperta: vecchia 0.9615, nuova (griglia di render da 2.5 m) 0.9616, nuova a 1 m 0.9540;
- 496 tile su 570 hanno nuovo ≥ vecchio.

**E.4:** mosaici da 3393×3399 px: vecchio 17.9 MB, nuovo 14.3 MB.
- Li ho salvati in RGB su sfondo nero perché in RGBA il vecchio pesava 20.2 MB, oltre il limite.
- Ritagli scelti per massimo contrasto: 3 file da circa 1 MB. Il ritaglio 1 e il 3 sono adiacenti, non sovrapposti.
- A vista il vecchio è "puntinato" (un campione per cella), il nuovo da 2.5 m è pulito, quello da 1 m è più nitido.

**E.5: render del chief** (GPU 0, EEVEE con 8 campioni, stesse impostazioni del run originale)

| Frame | Pixel con alpha 255 in entrambi | \|dRGB\| media / mediana / p95 | Spostamento (phase correlation) | Picco | Copertura vecchia → nuova |
|---|---|---|---|---|---|
| 0 | 1,880,342 | 12.54 / 7.67 / 41.0 | **(0, 0)** | 0.471 | 15.142% → 15.153% |
| 500 | 1,880,478 | 12.54 / 7.67 / 41.3 | **(0, 0)** | 0.471 | 15.141% → 15.152% |

- Errore di riproiezione degli angoli: 0.00018 / 0.00031 px, identico ai log originali.
- L'immagine delle differenze ×10 non mostra né bordi né traslazioni: le differenze stanno sulle zone con tessitura, mentre acqua e prati restano scuri.

## File creati

- `scripts/test_terrain_cache.py`: 8.9 kB.
- `data/terrain/`: 236 MB in totale:
  - 570 `.npz`;
  - `log.jsonl` (122 kB);
  - `dataset.json` e `tile_grid.json` (111 kB).
- `RUN/terrain_render/`: 87 MB (570 `.npz` più `cache_frame.json`).
- `RUN/terrain_check/`: 76 MB:
  - `compare_all.jsonl` e `compare_flat.jsonl` (76 kB ciascuno);
  - i due mosaici (17.9 e 14.3 MB);
  - 3 ritagli (circa 1 MB ciascuno) e `crops.json`;
  - `render_compare_frame_0000.png` e `render_compare_frame_0500.png` (14 MB ciascuno) e `render_compare.json`;
  - `render/satellite_00/` con 2 PNG da 8 MB, `poses.jsonl` e `render_report.jsonl`;
  - `logs/`.

Sia `data/terrain` sia le cartelle del RUN sono ignorate da git.

## Tempi

| Fase | Tempo |
|---|---|
| Test B | 5 s |
| Pilota | 54 s |
| Prova su D4-04 | 4 min 38 s |
| Cache completa | circa 75 min in due tranche (40 min interrotta + 34 min 37 s) |
| D.2 | 14 s |
| E.1 + E.2 | 9 + 8 s |
| E.4 | 38 s |
| E.5 | 89 s per due render (circa 28.5 s di setup ciascuno) |

## Problemi aperti (non toccati)

- `terrain_cache.py` scrive il log nell'ordine di invio dei job: se il run viene interrotto, i tile finiti dopo il primo ancora in corso non vengono registrati.
- Il conteggio "triangles approx." di `terrain_to_render.py` sovrastima i triangoli (stampa 2 × vertici).
- La docstring di `compare_terrain_caches.py` cita ancora `data/swarm_terrain_from_cache` nell'esempio.

Mi fermo qui. Puoi cancellare `data/swarm_terrain` dopo aver letto il report.