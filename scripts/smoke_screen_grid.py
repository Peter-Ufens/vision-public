"""Smoke V1 : quadrillage screen_grid (pur + moniteurs reels).

Usage :
  python scripts/smoke_screen_grid.py

Exit 0 si tous les controles passent.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from screen_grid import (  # noqa: E402
    cell_at_pixel,
    cells_overlapping_bbox,
    grid_for_monitor,
    grid_for_size,
    label_of,
    parse_label,
    pixel_of_subcell,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok, detail))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def main() -> int:
    print("=" * 70)
    print("V1 smoke_screen_grid")
    print("=" * 70)

    # --- densite theorique (fixtures poste de mesure) -------------------------- #
    print()
    print("Densite 16x9 (fixtures)")
    g1 = grid_for_size(2560, 1440, screen_index=1)
    g2 = grid_for_size(1920, 1080, screen_index=2)
    check("ecran1 = 16 cols", g1.cols == 16, str(g1.cols))
    check("ecran1 = 9 rows", g1.rows == 9)
    check("ecran2 = 16 cols", g2.cols == 16, str(g2.cols))
    check("case ecran1 160 px", abs(g1.cell_w - 160) < 0.01 and abs(g1.cell_h - 160) < 0.01)
    check("case ecran2 120 px", abs(g2.cell_w - 120) < 0.01 and abs(g2.cell_h - 120) < 0.01)

    ug = grid_for_size(3440, 1440, screen_index=1)
    check("UltraGear hypo = 22 cols", ug.cols == 22, str(ug.cols))

    # --- labels ------------------------------------------------------------ #
    print()
    print("Labels")
    lab = label_of(2, 1, 6)  # col B, row 7
    check("label 2:B7", lab == "2:B7", lab)
    s, c, r, sub = parse_label("2:B7")
    check("parse screen", s == 2)
    check("parse col B", c == 1)
    check("parse row 7", r == 6)
    check("parse sans sub", sub is None)
    s2, c2, r2, sub2 = parse_label("1:A1.5")
    check("parse sub centre", sub2 == 5 and s2 == 1 and c2 == 0 and r2 == 0)

    # sans prefixe ecran : parse OK mais screen None (interdisant l'ambiguite cote API appelante)
    s3, _, _, _ = parse_label("B7")
    check("label sans ecran = screen None", s3 is None)

    # --- pixel → case ------------------------------------------------------ #
    print()
    print("Pixel -> case")
    # coin haut-gauche ecran 2 en coords bureau virtuel
    ox, oy = 2560, 182
    cell = cell_at_pixel(g2, ox, oy, origin_left=ox, origin_top=oy)
    check("coin = 2:A1", cell is not None and cell.label == "2:A1", cell.label if cell else "None")
    # bord entre A et B : x juste avant / apres 120 px locaux
    cell_a = cell_at_pixel(g2, ox + 119, oy + 60, origin_left=ox, origin_top=oy)
    cell_b = cell_at_pixel(g2, ox + 120, oy + 60, origin_left=ox, origin_top=oy)
    check("x=119 = A", cell_a is not None and cell_a.col == 0, cell_a.label if cell_a else "")
    check("x=120 = B", cell_b is not None and cell_b.col == 1, cell_b.label if cell_b else "")

    # bord de deux cases : bbox qui chevauche
    cells = cells_overlapping_bbox(
        g2, (ox + 100, oy + 50, ox + 140, oy + 90), origin_left=ox, origin_top=oy
    )
    labs = {c.label for c in cells}
    check("bbox bord = A et B", "2:A1" in labs and "2:B1" in labs, str(sorted(labs)))

    # sous-case centre
    px, py = pixel_of_subcell(g2, 1, 6, 5, origin_left=2560, origin_top=182)
    cell_s = cell_at_pixel(g2, px, py, origin_left=2560, origin_top=182, with_sub=True)
    check(
        "sous-case centre = 2:B7.5",
        cell_s is not None and cell_s.label == "2:B7.5",
        cell_s.label if cell_s else "",
    )

    # --- moniteurs reels (pas DISPLAYn) ------------------------------------ #
    print()
    print("Moniteurs reels")
    try:
        from screen_context import list_monitors

        mons = list_monitors()
        check("au moins 1 moniteur", len(mons) >= 1, str(len(mons)))
        specs = [grid_for_monitor(m) for m in mons]
        labels_s1 = set()
        labels_s2 = set()
        for sp in specs:
            for col in range(sp.cols):
                for row in range(sp.rows):
                    lab = label_of(sp.screen_index, col, row)
                    if sp.screen_index == 1:
                        labels_s1.add(lab)
                    else:
                        labels_s2.add(lab)
        if len(mons) >= 2:
            inter = labels_s1 & labels_s2
            check("aucun label identique entre 2 ecrans", len(inter) == 0, str(list(inter))[:80])
        # geometrie : index stables meme si name DISPLAYn inverse
        for m, sp in zip(mons, specs):
            check(
                f"ecran {m.index} grille {sp.cols}x{sp.rows}",
                sp.cols >= 1 and sp.rows == 9,
                f"{m.width}x{m.height}",
            )
    except Exception as exc:  # noqa: BLE001
        check("list_monitors", False, str(exc))

    # --- Ajouts audit V1 (seconde vague, 15/10) ------------------------------ #
    # Deux defauts mesures puis corriges dans screen_grid.py. Les tailles
    # d'ecran actuelles (160 et 120 px par case) les cachaient tous les deux.
    print()
    print("Bords de cases et cases inexistantes (audit V1)")
    for w, h in ((3440, 1440), (1366, 768), (2560, 1080)):
        sp = grid_for_size(w, h)
        mal_ranges = sum(
            1 for x in range(w)
            if not (lambda c: c.left <= x < c.right)(cell_at_pixel(sp, x, h // 2))
        )
        check(f"{w}x{h} : chaque pixel est dans la case renvoyee", mal_ranges == 0,
              f"{mal_ranges} pixels de bord mal ranges (avant correctif : 20)")
    s1 = grid_for_size(2560, 1440, screen_index=1)
    for etiquette in ("1:Q5", "1:B10", "1:Z1"):
        _, col, row, _sub = parse_label(etiquette)
        try:
            pixel_of_subcell(s1, col, row, 5)
            refuse = False
        except ValueError:
            refuse = True
        check(f"case inexistante {etiquette} refusee", refuse,
              "avant : point hors ecran sans erreur ; 1:Q5 tombait sur l'ecran 2")
    check("temoin : la derniere vraie case 1:P9 reste valide",
          pixel_of_subcell(s1, 15, 8, 5)[0] < 2560)

    print()
    ok = all(c[1] for c in CHECKS)
    total = len(CHECKS)
    good = sum(1 for c in CHECKS if c[1])
    print(f"Resultat : {good}/{total} OK")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
