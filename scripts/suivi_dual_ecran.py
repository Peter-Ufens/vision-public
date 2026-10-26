#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suiveur dual-ecran : trames, pas une camera.

Grab des deux ecrans, diff de pixels par case 16x9, seuil adaptatif,
petites animations en boucle ignorees. Pas d'OCR a chaque trame.
Pas d'image sur disque sauf ``--images`` (dossier hors depot).

  python scripts/suivi_dual_ecran.py --duree 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))


from screen_grid import cell_at_pixel, grid_for_size  # noqa: E402


def _gris(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def mesures_grille(prev: np.ndarray, curr: np.ndarray, ecran: int) -> dict[str, Any]:
    """MAD et part de pixels changes, par case. Pur : aucune capture."""
    if prev.shape != curr.shape:
        raise ValueError("les deux trames n'ont pas la meme taille")
    g0 = _gris(prev)
    g1 = _gris(curr)
    # Reduit les grands ecrans : le suivi doit rester proche du grab, pas de l'OCR.
    if max(g0.shape) > 480:
        f = 480.0 / max(g0.shape)
        taille = (max(1, int(g0.shape[1] * f)), max(1, int(g0.shape[0] * f)))
        g0 = cv2.resize(g0, taille, interpolation=cv2.INTER_AREA)
        g1 = cv2.resize(g1, taille, interpolation=cv2.INTER_AREA)
    h, w = g1.shape[:2]
    spec = grid_for_size(w, h, screen_index=ecran, rows=9)
    g0 = g0.astype(np.int16)
    g1 = g1.astype(np.int16)
    mad = np.zeros((spec.rows, spec.cols), dtype=np.float32)
    frac = np.zeros((spec.rows, spec.cols), dtype=np.float32)
    labels: list[list[str]] = []
    for row in range(spec.rows):
        ligne = []
        for col in range(spec.cols):
            cx = min(w - 1, int((col + 0.5) * spec.cell_w))
            cy = min(h - 1, int((row + 0.5) * spec.cell_h))
            cell = cell_at_pixel(spec, cx, cy)
            if cell is None:
                ligne.append("")
                continue
            a = g0[cell.top:cell.bottom, cell.left:cell.right]
            b = g1[cell.top:cell.bottom, cell.left:cell.right]
            if a.size == 0:
                ligne.append(cell.label)
                continue
            diff = np.abs(a - b)
            mad[row, col] = float(diff.mean())
            frac[row, col] = float((diff >= 18).mean())
            ligne.append(cell.label)
        labels.append(ligne)
    return {"mad": mad, "frac": frac, "labels": labels, "cols": spec.cols, "rows": spec.rows}


def classer(
    historique: list[dict[str, Any]],
    *,
    plancher: float = 4.0,
    facteur: float = 3.0,
    frac_petite: float = 0.06,
    frac_grosse: float = 0.15,
) -> dict[str, Any]:
    """Seuil = max(plancher, mediane des cases x facteur).

    Une case souvent changeante mais petite (buff qui pulse) est du bruit.
    Une case dont une grande part des pixels change reste une zone active.
    """
    dernier = historique[-1]
    mad = dernier["mad"]
    frac = dernier["frac"]
    med = float(np.median(mad))
    med_f = float(np.median(frac))
    seuil = max(plancher, med * facteur)
    # Si tout l'ecran bouge (video), la part mediane monte : on ne garde que les cases au-dessus.
    seuil_frac = max(frac_grosse, med_f * 1.8)
    brut = (mad >= seuil) | (frac >= seuil_frac)
    bruit = np.zeros_like(brut, dtype=bool)
    if len(historique) >= 6:
        stack_f = np.stack([h["frac"] for h in historique[-6:]], axis=0)
        stack_m = np.stack([h["mad"] for h in historique[-6:]], axis=0)
        souvent = (stack_f >= 0.008).sum(axis=0) >= 4
        petit = stack_f.mean(axis=0) < frac_petite
        pas_gros = stack_m.max(axis=0) < max(seuil * 2.5, plancher * 3)
        bruit = souvent & petit & pas_gros & ~ (frac >= frac_grosse)
    actif = brut & ~bruit
    labels = dernier["labels"]

    def noms(masque: np.ndarray) -> list[str]:
        out = []
        for r in range(masque.shape[0]):
            for c in range(masque.shape[1]):
                if masque[r, c] and labels[r][c]:
                    out.append(labels[r][c])
        return out

    return {
        "seuil": round(seuil, 2),
        "seuil_frac": round(seuil_frac, 3),
        "mediane_mad": round(med, 2),
        "zones_actives": noms(actif),
        "bruit_boucle": noms(bruit),
    }


def suivre_suite(trames: dict[int, list[np.ndarray]]) -> dict[str, Any]:
    """Plusieurs images deja en memoire, par index d'ecran. Pas de disque."""
    ecrans = []
    for index, images in trames.items():
        hist: list[dict[str, Any]] = []
        for prev, curr in zip(images, images[1:]):
            hist.append(mesures_grille(prev, curr, index))
        if not hist:
            ecrans.append({"ecran": index, "zones_actives": [], "bruit_boucle": [], "paires": 0})
            continue
        verdict = classer(hist)
        ecrans.append({"ecran": index, "paires": len(hist), **verdict})
    return {"ecrans": ecrans, "ocr_par_trame": False}


class _GrabParallele:
    """Un fil et un mss PAR ecran, gardes ouverts (audit 26/10).

    Mesure ce PC : ecran 1 ~28 ms, ecran 2 ~20 ms ; l'un apres l'autre ~49 ms,
    en parallele ~34 ms meme en rouvrant mss a chaque fois. Rend du gris
    directement : le suivi ne compare que la lumiere des cases.
    """

    def __init__(self, ecrans: list[Any]) -> None:
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from liste_ecrans_mss import ouvrir_mss

        self._ouvrir = ouvrir_mss
        self._local = threading.local()
        self._pool = ThreadPoolExecutor(max_workers=max(1, len(ecrans)))
        self._ecrans = ecrans

    def _un(self, e: Any) -> tuple[int, np.ndarray]:
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._local.sct = self._ouvrir()
        brut = np.asarray(sct.grab({"left": e.left, "top": e.top, "width": e.width, "height": e.height}))
        return e.index, cv2.cvtColor(brut, cv2.COLOR_BGRA2GRAY)

    def __call__(self) -> dict[int, np.ndarray]:
        return dict(self._pool.map(self._un, self._ecrans))

    def fermer(self) -> None:
        self._pool.shutdown(wait=True)


def boucle_live(duree_s: float = 3.0, parallele: bool = True) -> dict[str, Any]:
    """Grab continu des ecrans reels. N'ecrit aucune image."""
    from plateforme.capture import capturer
    from plateforme.ecrans import lister
    from plateforme.session import nom as nom_session

    ecrans = lister()  # Wayland : refuse ici (BackendManquant), avant tout grab
    if len(ecrans) < 1:
        return {"ok": False, "raison": "aucun ecran"}
    grab_par = _GrabParallele(ecrans) if parallele else None
    debut = time.perf_counter()
    latences: list[float] = []
    hist: dict[int, list[dict[str, Any]]] = {e.index: [] for e in ecrans}
    prec: dict[int, np.ndarray] = {}
    paires = 0
    while time.perf_counter() - debut < duree_s:
        t0 = time.perf_counter()
        if grab_par is not None:
            imgs = grab_par()
        else:
            imgs = {e.index: capturer(e.left, e.top, e.width, e.height) for e in ecrans}
        latences.append((time.perf_counter() - t0) * 1000.0)
        for e in ecrans:
            image = imgs[e.index]
            if e.index in prec and prec[e.index].shape == image.shape:
                hist[e.index].append(mesures_grille(prec[e.index], image, e.index))
            prec[e.index] = image
        paires += 1
    elapsed = time.perf_counter() - debut
    if grab_par is not None:
        grab_par.fermer()
    lat = np.array(latences, dtype=np.float64) if latences else np.array([0.0])
    rapport_ecrans = []
    for e in ecrans:
        h = hist[e.index]
        base = {
            "ecran": e.index,
            "w": e.width,
            "h": e.height,
            "x": e.left,
            "y": e.top,
            "paires_diff": len(h),
        }
        if h:
            base.update(classer(h))
        else:
            base.update({"zones_actives": [], "bruit_boucle": [], "seuil": None})
        rapport_ecrans.append(base)
    return {
        "ok": True,
        "session": nom_session(),
        "duree_s": round(elapsed, 3),
        "paires": paires,
        "fps_paires": round(paires / elapsed, 2) if elapsed else 0,
        "latence_paire_ms": {
            "n": int(lat.size),
            "mediane": round(float(np.median(lat)), 2),
            "p90": round(float(np.percentile(lat, 90)), 2),
        },
        "ecrans": rapport_ecrans,
        "ocr_par_trame": False,
        "images_ecrites": False,
        "grab": "parallele" if parallele else "serie",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Suivi d'activite dual-ecran (trames)")
    ap.add_argument("--duree", type=float, default=3.0)
    ap.add_argument("--sortie", type=Path, default=None)
    ap.add_argument("--serie", action="store_true", help="ancien grab, un ecran apres l'autre")
    args = ap.parse_args()
    rapport = boucle_live(args.duree, parallele=not args.serie)
    texte = json.dumps(rapport, ensure_ascii=False, indent=2)
    if args.sortie:
        # Le depot n'est pas un dossier d'images. JSON de mesures seulement.
        if args.sortie.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}:
            print("REFUSE : pas d'image. JSON seulement.")
            return 2
        args.sortie.write_text(texte + "\n", encoding="utf-8")
        print("WROTE", args.sortie.name)
    else:
        print(texte)
    return 0 if rapport.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
