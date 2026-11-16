"""Tables and extrapolation of the step-20 study (B, C, D) from the files the other scripts wrote.

  python -I summarize.py --study STUDY --ref CBD=REF_x4,SUBURB=REF_x2

Writes STUDY/summary.json and STUDY/tables.md. Every number comes from the study files:
  - per variant and block: triangles, texture megapixels, disk size zipped (ZIP deflate of the files
    a renderer needs: DEC OBJ + MTL + JPG; REF = the original archives; GRID / WALL = the .npz),
    build wall time and peak RSS (GNU time, largest single process), Hausdorff REF -> DEC (per-tile
    samples pooled with weight = tile REF surface / tile samples, i.e. uniform in area);
  - per render: GPU peak - baseline (nvidia-smi, 0.2 s polling), wall time, peak RSS, setup time;
  - C.3 table from compare/metrics.json;
  - D: linear model GPU MiB = m0 + a * triangles + b * texture megapixels fitted on all completed
    renders, and extrapolation to the 570 Melbourne tiles and to a 375 km^2 scene.
"""
import argparse
import json
import re
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
TILE_KM2 = 0.28 * 0.28


def gnu_time(path):
    text = Path(path).read_text()
    wall = re.search(r'Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)', text).group(1)
    parts = [float(x) for x in wall.split(':')]
    return dict(wall_s=sum(p * 60 ** i for i, p in enumerate(reversed(parts))),
                peak_rss_gb=int(re.search(r'Maximum resident set size \(kbytes\): (\d+)', text).group(1)) / 1e6)


def zipped_bytes(files, tmp):
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.parent.name + "/" + f.name)
    size = tmp.stat().st_size
    tmp.unlink()
    return size


def weighted_percentile(x, w, q):
    order = np.argsort(x)
    cw = np.cumsum(w[order]); cw /= cw[-1]
    return float(np.interp(q / 100, cw, x[order]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', type=Path, required=True)
    parser.add_argument('--ref', required=True)
    args = parser.parse_args()
    S = args.study
    refs = dict(item.split('=') for item in args.ref.split(','))
    blocks = json.loads((S / 'blocks.json').read_text())['blocks']
    analysis = {p.stem: json.loads(p.read_text()) for p in (S / 'analysis').glob('*.json')}
    out = dict(blocks={}, renders=[], ref_textures=[])

    for name, block in blocks.items():
        tiles = block['tiles']
        a = [analysis[t] for t in tiles]
        bv = {}
        bv['REF'] = dict(triangles=sum(x['A2']['faces'] for x in a),
                         texture_megapixels=sum(x['A2']['texture_megapixels'] for x in a),
                         textures=sum(x['A2']['textures'] for x in a),
                         disk_zipped_bytes=sum(x['A1']['archive_bytes'] for x in a),
                         disk_unzipped_bytes=sum(x['A1']['uncompressed_bytes'] for x in a),
                         surface_m2=sum(x['A3']['surface_m2'] for x in a))
        g = json.loads((S / 'variants' / name / 'GRID' / 'summary.json').read_text())
        gt = gnu_time(S / 'logs' / f'grid_{name}.time')
        bv['GRID'] = dict(triangles=g['triangles'], vertices=g['vertices'], texture_megapixels=0.0,
                          disk_zipped_bytes=g['npz_bytes'], build_wall_s=gt['wall_s'], build_peak_rss_gb=gt['peak_rss_gb'],
                          build_note='from the 1 m cache; cache build itself is terrain_cache.py (step 18)')
        wall_path = S / 'variants' / name / 'WALL' / 'summary.json'
        if wall_path.exists():
            w = json.loads(wall_path.read_text())
            wt = gnu_time(S / 'logs' / f'wall_{name}.time')
            bv['WALL'] = dict(triangles=g['triangles'] + w['added_triangles'], quads=w['quads'],
                              quads_without_hit=w['quads_without_hit'], texture_megapixels=0.0,
                              disk_zipped_bytes=g['npz_bytes'] + sum(t['npz_bytes'] for t in w['tiles']),
                              build_wall_s=gt['wall_s'] + wt['wall_s'], build_peak_rss_gb=max(gt['peak_rss_gb'], wt['peak_rss_gb']),
                              wall_script_seconds=w['seconds'])
        for r in (4, 16, 64):
            root = S / 'variants' / name / f'DEC_{r}'
            per_tile, files, samples, weights = [], [], [], []
            for t, x in zip(tiles, a):
                rec = json.loads((root / t / f'{t}.json').read_text())
                hd = json.loads((root / t / 'hausdorff.json').read_text())
                tm = gnu_time(S / 'logs' / f'dec_{t}_{r}.time')
                hm = gnu_time(S / 'logs' / f'haus_{t}_{r}.time')
                d = np.load(root / f'{t}_hausdorff_samples.npy').astype(np.float64)
                samples.append(d); weights.append(np.full(len(d), x['A3']['surface_m2'] / len(d)))
                files += [p for p in (root / t).iterdir() if p.suffix in ('.obj', '.mtl', '.jpg')]
                per_tile.append(dict(tile=t, ref_faces=rec['ref_faces'], target=rec['target_faces'], faces=rec['faces'],
                                     faces_over_target=rec['faces_over_target'], materials_used=rec['materials_used'],
                                     texture_megapixels=rec['texture_megapixels'],
                                     faces_bad_uv=rec['faces_with_mixed_material_uv'],
                                     uv_stretch_area_gt4=rec['uv_stretch']['area_fraction_rel_gt4'],
                                     uv_stretch_rel_p50_p95=rec['uv_stretch']['rel_p50_p95_p99'][:2],
                                     build_wall_s=tm['wall_s'], build_peak_rss_gb=tm['peak_rss_gb'],
                                     script_seconds=rec['seconds'],
                                     hausdorff=dict(mean_m=hd['mean_m'], p95_m=hd['p95_m'], max_m=hd['max_m']),
                                     hausdorff_wall_s=hm['wall_s'], hausdorff_peak_rss_gb=hm['peak_rss_gb']))
            d = np.concatenate(samples); w = np.concatenate(weights)
            bv[f'DEC_{r}'] = dict(
                triangles=sum(p['faces'] for p in per_tile), target=sum(p['target'] for p in per_tile),
                texture_megapixels=sum(p['texture_megapixels'] for p in per_tile),
                disk_zipped_bytes=zipped_bytes(files, S / 'work' / 'zips' / f'{name}_DEC_{r}.zip'),
                build_wall_s_sum=sum(p['build_wall_s'] for p in per_tile),
                build_peak_rss_gb=max(p['build_peak_rss_gb'] for p in per_tile),
                faces_bad_uv=sum(p['faces_bad_uv'] for p in per_tile),
                hausdorff=dict(mean_m=float(np.average(d, weights=w)), p95_m=weighted_percentile(d, w, 95),
                               max_m=float(d.max()), samples=int(len(d))),
                per_tile=per_tile)
            bv[f'DEC_{r}']['faces_over_target'] = bv[f'DEC_{r}']['triangles'] / bv[f'DEC_{r}']['target']
        out['blocks'][name] = dict(tiles=tiles, variants=bv)
        for p in sorted((S / 'variants' / name / 'REF').glob('tex_x*/summary.json')):
            out['ref_textures'].append(json.loads(p.read_text()))

    # Renders.
    for run in sorted(S.glob('renders/*/*/run.json')) + sorted(S.glob('renders_memory/*/*/run.json')):
        rec = json.loads(run.read_text())
        rep_path = run.parent / 'report.json'
        rep = json.loads(rep_path.read_text()) if rep_path.exists() else {}
        out['renders'].append(dict(block=run.parent.parent.name, variant=run.parent.name, returncode=rec['returncode'],
                                   gpu=rec['gpu'], gpu_peak_minus_baseline_mib=rec['gpu_peak_minus_baseline_mib'],
                                   gpu_baseline_mib=rec['gpu_baseline_mib'], wall_s=rec['wall_s'],
                                   peak_rss_gb=rec['peak_rss_kb'] / 1e6, triangles=rep.get('triangles'),
                                   texture_megapixels=rep.get('texture_megapixels'), materials=rep.get('materials'),
                                   setup_s=rep.get('setup_seconds'),
                                   render_s=[r['seconds'] for r in rep.get('renders', [])]))
    # GPU model. Runs whose peak reached 98 % of the 12288 MiB card are censored (true demand unknown)
    # and left out of the fit.
    capacity = 12288
    ok = [r for r in out['renders'] if r['returncode'] == 0 and r['triangles'] is not None]
    fit = [r for r in ok if r['gpu_peak_minus_baseline_mib'] + r['gpu_baseline_mib'] < 0.98 * capacity]
    censored = [dict(block=r['block'], variant=r['variant'], peak_mib=r['gpu_peak_minus_baseline_mib'] + r['gpu_baseline_mib'])
                for r in ok if r not in fit]
    def design(t, mp, mats):
        return [1.0, t / 1e6, mp, mats / 1e3]
    X = np.array([design(r['triangles'], r['texture_megapixels'], r['materials']) for r in fit])
    y = np.array([r['gpu_peak_minus_baseline_mib'] for r in fit], dtype=float)
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ coef
    model = dict(formula='MiB = m0 + a * Mtriangles + b * texture MP + c * kmaterials',
                 m0_mib=float(coef[0]), mib_per_million_triangles=float(coef[1]), mib_per_texture_megapixel=float(coef[2]),
                 mib_per_thousand_materials=float(coef[3]), capacity_mib=capacity,
                 points=len(fit), censored=censored, residual_max_abs_mib=float(np.abs(resid).max()),
                 residuals=[dict(block=r['block'], variant=r['variant'], measured=m, fitted=float(f))
                            for r, m, f in zip(fit, y, X @ coef)])
    for c in censored:
        r = next(x for x in ok if x['block'] == c['block'] and x['variant'] == c['variant'])
        c['model_mib'] = float(np.array(design(r['triangles'], r['texture_megapixels'], r['materials'])) @ coef)
    out['gpu_model'] = model
    materials = {}
    for r in ok:
        if r['variant'] == 'GRID_PIPELINE570':
            continue
        key = 'REF' if r['variant'].startswith('REF') else r['variant']
        materials[(r['block'], key)] = r['materials']

    # D. Extrapolation.
    log = [json.loads(line) for line in (ROOT / 'data/terrain/melbourne_2020/1m/log.jsonl').read_text().splitlines()]
    known = [r for r in log if r['faces'] is not None]
    head = dict(line.split() for line in (S / 'work' / 'rebuilt_tiles_head.txt').read_text().splitlines())
    faces_per_byte = sum(r['faces'] for r in known) / sum(r['archive_bytes'] for r in known)
    all_bytes = sum(r['archive_bytes'] for r in known) + sum(int(v) for v in head.values())
    all_faces = sum(r['faces'] for r in known) + faces_per_byte * sum(int(v) for v in head.values())
    tiles570 = dict(tiles=len(log), archive_bytes=all_bytes, ref_faces=all_faces, area_km2=len(log) * TILE_KM2,
                    grid_triangles_pipeline=13797886,
                    faces_estimated_for=sorted(head), faces_per_archive_byte=faces_per_byte)
    scale375 = 375.0 / tiles570['area_km2']
    ext = dict(melbourne_570=tiles570, scene_375km2=dict(area_km2=375.0, scale_from_570=scale375,
               assumption='Melbourne-average density of the 570 tiles over 375 km^2 (DEC/REF/WALL); GRID scales with area'),
               variants={})
    for vname in ('REF', 'REF_x2', 'REF_x4', 'REF_x8', 'GRID', 'WALL', 'DEC_4', 'DEC_16', 'DEC_64'):
        per_block = {}
        k_ref = int(vname[5:]) if vname.startswith('REF_x') else 1
        for bname, b in out['blocks'].items():
            v = b['variants'].get(vname.split('_x')[0] if vname.startswith('REF') else vname)
            ref = b['variants']['REF']
            if v is None:
                continue
            per_block[bname] = dict(tri_per_ref_face=v['triangles'] / ref['triangles'],
                                    mp_per_ref_mp=v['texture_megapixels'] / k_ref ** 2 / ref['texture_megapixels'],
                                    materials_per_ref_face=materials.get((bname, 'REF' if vname.startswith('REF') else vname), 0) / ref['triangles'],
                                    disk_per_ref_byte=v['disk_zipped_bytes'] / ref['disk_zipped_bytes'],
                                    mp_per_ref_face=v['texture_megapixels'] / k_ref ** 2 / ref['triangles'],
                                    disk_per_ref_face=v['disk_zipped_bytes'] / ref['triangles'],
                                    build_s_per_ref_face=(v.get('build_wall_s_sum', v.get('build_wall_s', 0)) / ref['triangles']),
                                    peak_rss_gb=v.get('build_peak_rss_gb'))
        if not per_block:
            continue
        rows = {}
        # Build workers on 24 CPUs and ~54 GB of RAM, limited by the largest per-process RSS seen in any block.
        rss_max = max((k['peak_rss_gb'] or 0) for k in per_block.values())
        workers = int(min(24, 54 // rss_max)) if rss_max else 24
        for bname, k in per_block.items():
            if vname == 'GRID':
                tri = tiles570['grid_triangles_pipeline']
                walls = 0
            elif vname == 'WALL':
                g = out['blocks'][bname]['variants']['GRID']['triangles']
                walls = (out['blocks'][bname]['variants']['WALL']['triangles'] - g) / g
                tri = tiles570['grid_triangles_pipeline'] * (1 + walls)
            else:
                tri = k['tri_per_ref_face'] * all_faces
            mp = k['mp_per_ref_face'] * all_faces
            mats = k['materials_per_ref_face'] * all_faces
            disk = k['disk_per_ref_byte'] * all_bytes if vname not in ('GRID', 'WALL') else k['disk_per_ref_face'] * all_faces
            if vname in ('GRID', 'WALL'):
                disk = out['blocks'][bname]['variants'][vname]['disk_zipped_bytes'] / 9 * len(log)
            cpu_s = k['build_s_per_ref_face'] * all_faces
            row = {}
            for case, f in (('melbourne_570', 1.0), ('scene_375km2', scale375)):
                t = tri * f; m = mp * f; n_mat = mats * f
                gpu = float(np.array(design(t, m, n_mat)) @ coef)
                row[case] = dict(triangles=t, texture_megapixels=m, materials=n_mat, disk_bytes=disk * f, build_cpu_s=cpu_s * f,
                                 build_workers=workers, build_wall_s=cpu_s * f / workers, gpu_mib=float(gpu),
                                 fits_12gb=bool(gpu < capacity))
            rows[f'ratios_from_{bname}'] = row
        ext['variants'][vname] = dict(per_block_ratios=per_block, estimates=rows)
    out['extrapolation'] = ext
    (S / 'summary.json').write_text(json.dumps(out, indent=1, default=float) + '\n')
    print(json.dumps(dict(gpu_model={k: v for k, v in model.items() if k != 'residuals'}), indent=1))


if __name__ == '__main__':
    main()
