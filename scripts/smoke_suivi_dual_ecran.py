#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suiveur : une case qui change vraiment, une petite boucle ignoree. Pas de grab."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from screen_grid import cell_at_pixel, grid_for_size  # noqa: E402
from suivi_dual_ecran import suivre_suite  # noqa: E402


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit("KO " + msg)
    print("OK", msg)


def _base() -> np.ndarray:
    return np.full((180, 320, 3), 30, np.uint8)


def _remplir(img: np.ndarray, col: int, row: int, valeur: int, marge: int = 0) -> None:
    spec = grid_for_size(img.shape[1], img.shape[0], 1, 9)
    cx = int((col + 0.5) * spec.cell_w)
    cy = int((row + 0.5) * spec.cell_h)
    cell = cell_at_pixel(spec, cx, cy)
    img[cell.top + marge:cell.bottom - marge, cell.left + marge:cell.right - marge] = valeur


def main() -> int:
    a = _base()
    b = _base()
    calme = suivre_suite({1: [a, b]})
    _ok(calme["ecrans"][0]["zones_actives"] == [], "deux trames identiques : aucune zone")

    gros = _base()
    _remplir(gros, 4, 3, 255)
    brut = suivre_suite({1: [a, gros]})
    _ok("1:E4" in brut["ecrans"][0]["zones_actives"], "grande case active " + str(brut["ecrans"][0]["zones_actives"]))

    images = []
    for i in range(8):
        img = _base()
        if i % 2:
            spec = grid_for_size(320, 180, 1, 9)
            cell = cell_at_pixel(spec, int(0.5 * spec.cell_w), int(0.5 * spec.cell_h))
            img[cell.top:cell.top + 2, cell.left:cell.left + 2] = 255
        images.append(img)
    dernier = images[-1].copy()
    _remplir(dernier, 4, 3, 255)
    images.append(dernier)
    boucle = suivre_suite({1: images, 2: [_base(), _base()]})
    e1 = next(e for e in boucle["ecrans"] if e["ecran"] == 1)
    _ok("1:A1" not in e1["zones_actives"], "petite boucle non retenue " + str(e1["bruit_boucle"]))
    _ok("1:A1" in e1["bruit_boucle"], "petite boucle classee bruit")
    _ok("1:E4" in e1["zones_actives"], "le vrai changement reste")
    _ok(boucle["ocr_par_trame"] is False, "pas d'OCR dans le suivi")
    print("smoke_suivi_dual_ecran OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
