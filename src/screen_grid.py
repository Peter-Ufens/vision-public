"""Quadrillage bataille navale par ecran (V1 · plan 15/10).

Module **pur** : pas d'appel Win32. On lui passe largeur/hauteur (et un index
d'ecran). Les moniteurs reels viennent de `screen_context.list_monitors`.

Convention (ADR plan audit V19 / exec V1) :
  - 9 lignes (1..9)
  - colonnes = round(9 * largeur / hauteur) → lettres A, B, C…
  - label = ``<index_ecran>:<COLONNE><LIGNE>`` ex. ``2:B7``
  - sous-case pavé 3×3 : ``2:B7.5`` (1=NW … 5=centre … 9=SE)
  - la case sert a se parler ; le clic vise l'element detecte, pas le centre case

Ne jamais designer un ecran par le nom Windows ``\\\\.\\DISPLAYn`` (inversable).
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Iterable

__all__ = [
    "GridSpec",
    "Cell",
    "grid_for_size",
    "grid_for_monitor",
    "label_of",
    "parse_label",
    "cell_at_pixel",
    "cells_overlapping_bbox",
    "pixel_of_subcell",
]


_LABEL_RE = re.compile(
    r"^(?:(?P<screen>\d+):)?(?P<col>[A-Z]+)(?P<row>\d+)(?:\.(?P<sub>[1-9]))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GridSpec:
    """Grille pour un ecran (ou une surface virtuelle de meme taille)."""

    screen_index: int
    width: int
    height: int
    cols: int
    rows: int = 9

    @property
    def cell_w(self) -> float:
        return self.width / self.cols

    @property
    def cell_h(self) -> float:
        return self.height / self.rows


@dataclass(frozen=True)
class Cell:
    screen_index: int
    col: int  # 0-based
    row: int  # 0-based
    left: int
    top: int
    right: int
    bottom: int
    sub: int | None = None  # 1..9 si sous-case

    @property
    def label(self) -> str:
        base = f"{self.screen_index}:{_col_letters(self.col)}{self.row + 1}"
        if self.sub is None:
            return base
        return f"{base}.{self.sub}"

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


def _col_letters(col_0: int) -> str:
    """0 → A, 25 → Z, 26 → AA (reserve ; V1 vise une seule lettre)."""
    if col_0 < 0:
        raise ValueError("colonne negative")
    n = col_0
    letters: list[str] = []
    while True:
        n, r = divmod(n, 26)
        letters.append(chr(ord("A") + r))
        if n == 0:
            break
        n -= 1
    return "".join(reversed(letters))


def _col_index(letters: str) -> int:
    letters = letters.upper()
    n = 0
    for ch in letters:
        if not ("A" <= ch <= "Z"):
            raise ValueError(f"lettre colonne invalide: {letters!r}")
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def grid_for_size(width: int, height: int, screen_index: int = 1, rows: int = 9) -> GridSpec:
    """Calcule la grille. ``cols = round(rows * width / height)`` (audit)."""
    if width <= 0 or height <= 0:
        raise ValueError("largeur/hauteur doivent etre > 0")
    if rows <= 0:
        raise ValueError("rows doit etre > 0")
    cols = max(1, int(round(rows * width / height)))
    return GridSpec(
        screen_index=int(screen_index),
        width=int(width),
        height=int(height),
        cols=cols,
        rows=int(rows),
    )


def grid_for_monitor(mon: object, rows: int = 9) -> GridSpec:
    """Accepte un `Monitor` (duck typing : index, width, height)."""
    return grid_for_size(
        width=int(mon.width),  # type: ignore[attr-defined]
        height=int(mon.height),  # type: ignore[attr-defined]
        screen_index=int(mon.index),  # type: ignore[attr-defined]
        rows=rows,
    )


def label_of(screen_index: int, col_0: int, row_0: int, sub: int | None = None) -> str:
    cell = Cell(
        screen_index=screen_index,
        col=col_0,
        row=row_0,
        left=0,
        top=0,
        right=0,
        bottom=0,
        sub=sub,
    )
    return cell.label


def parse_label(label: str) -> tuple[int | None, int, int, int | None]:
    """Retourne (screen|None, col_0, row_0, sub|None)."""
    m = _LABEL_RE.match(label.strip())
    if not m:
        raise ValueError(f"label invalide: {label!r}")
    screen = int(m.group("screen")) if m.group("screen") else None
    col = _col_index(m.group("col"))
    row = int(m.group("row")) - 1
    if row < 0:
        raise ValueError(f"ligne invalide: {label!r}")
    sub_s = m.group("sub")
    sub = int(sub_s) if sub_s else None
    return screen, col, row, sub


def _cell_bounds(spec: GridSpec, col_0: int, row_0: int) -> tuple[int, int, int, int]:
    # Une case qui n'existe pas est REFUSEE (audit V1, 15/10). Avant, `2:Q5`
    # (pas de colonne Q sur une grille A..P) donnait un point hors de l'ecran
    # sans erreur, et `1:Q5` tombait meme sur l'ecran 2. Pour un outil
    # anti-hallucination (« vous dites Q5 »), c'est le pire cas : une case
    # inventee doit faire du bruit, pas designer un autre ecran.
    if not (0 <= col_0 < spec.cols and 0 <= row_0 < spec.rows):
        raise ValueError(
            f"case hors grille : colonne {_col_letters(col_0) if col_0 >= 0 else col_0}"
            f", ligne {row_0 + 1} (grille {spec.cols}x{spec.rows}, "
            f"colonnes A..{_col_letters(spec.cols - 1)}, lignes 1..{spec.rows})"
        )
    left = int(math.floor(col_0 * spec.cell_w))
    top = int(math.floor(row_0 * spec.cell_h))
    right = int(math.floor((col_0 + 1) * spec.cell_w)) if col_0 + 1 < spec.cols else spec.width
    bottom = int(math.floor((row_0 + 1) * spec.cell_h)) if row_0 + 1 < spec.rows else spec.height
    return left, top, right, bottom


def _sub_bounds(
    left: int, top: int, right: int, bottom: int, sub: int
) -> tuple[int, int, int, int]:
    """Pavé téléphone 1..9 (1=NW, 5=centre, 9=SE)."""
    if sub < 1 or sub > 9:
        raise ValueError("sub doit etre 1..9")
    # 1 2 3
    # 4 5 6
    # 7 8 9
    sx = (sub - 1) % 3
    sy = (sub - 1) // 3
    w = right - left
    h = bottom - top
    sl = left + int(math.floor(sx * w / 3))
    st = top + int(math.floor(sy * h / 3))
    sr = left + int(math.floor((sx + 1) * w / 3)) if sx < 2 else right
    sb = top + int(math.floor((sy + 1) * h / 3)) if sy < 2 else bottom
    return sl, st, sr, sb


def _index_of(pos: int, taille: float, n: int) -> int:
    """Indice de case qui contient `pos`, avec la MEME regle que `_cell_bounds`.

    Les bords sont `floor(i * taille)`. Diviser puis tronquer (`int(pos /
    taille)`) donne parfois la case voisine quand la taille n'est pas entiere :
    mesure audit V1, 20 pixels de bord mal ranges par ligne sur un 3440x1440
    (UltraGear prevu au plan), un 1366x768 ou un 2560x1080. Zero sur les deux
    ecrans actuels, parce que 160 et 120 tombent juste.
    """
    i = min(n - 1, max(0, int(pos / taille)))
    while i > 0 and pos < math.floor(i * taille):
        i -= 1
    while i < n - 1 and pos >= math.floor((i + 1) * taille):
        i += 1
    return i


def cell_at_pixel(
    spec: GridSpec,
    x: int,
    y: int,
    *,
    origin_left: int = 0,
    origin_top: int = 0,
    with_sub: bool = False,
) -> Cell | None:
    """Pixel en coordonnees ecran virtuel (ou locales si origin=0)."""
    lx = x - origin_left
    ly = y - origin_top
    if lx < 0 or ly < 0 or lx >= spec.width or ly >= spec.height:
        return None
    col = _index_of(lx, spec.cell_w, spec.cols)
    row = _index_of(ly, spec.cell_h, spec.rows)
    left, top, right, bottom = _cell_bounds(spec, col, row)
    sub = None
    if with_sub:
        # position dans la case
        cx = lx - left
        cy = ly - top
        cw = max(1, right - left)
        ch = max(1, bottom - top)
        sx = min(2, int(3 * cx / cw))
        sy = min(2, int(3 * cy / ch))
        sub = sy * 3 + sx + 1
        left, top, right, bottom = _sub_bounds(
            left + origin_left,
            top + origin_top,
            right + origin_left,
            bottom + origin_top,
            sub,
        )
        return Cell(spec.screen_index, col, row, left, top, right, bottom, sub)
    return Cell(
        spec.screen_index,
        col,
        row,
        left + origin_left,
        top + origin_top,
        right + origin_left,
        bottom + origin_top,
        None,
    )


def cells_overlapping_bbox(
    spec: GridSpec,
    bbox: tuple[int, int, int, int],
    *,
    origin_left: int = 0,
    origin_top: int = 0,
) -> list[Cell]:
    """Toutes les cases qui intersectent bbox (coord. virtuelles)."""
    l, t, r, b = bbox
    # clip to monitor
    l = max(l, origin_left)
    t = max(t, origin_top)
    r = min(r, origin_left + spec.width)
    b = min(b, origin_top + spec.height)
    if r <= l or b <= t:
        return []
    out: list[Cell] = []
    seen: set[tuple[int, int]] = set()
    # sample corners + center enough via grid walk
    c0 = _index_of(l - origin_left, spec.cell_w, spec.cols)
    c1 = _index_of(r - 1 - origin_left, spec.cell_w, spec.cols)
    r0 = _index_of(t - origin_top, spec.cell_h, spec.rows)
    r1 = _index_of(b - 1 - origin_top, spec.cell_h, spec.rows)
    for col in range(c0, c1 + 1):
        for row in range(r0, r1 + 1):
            key = (col, row)
            if key in seen:
                continue
            seen.add(key)
            left, top, right, bottom = _cell_bounds(spec, col, row)
            out.append(
                Cell(
                    spec.screen_index,
                    col,
                    row,
                    left + origin_left,
                    top + origin_top,
                    right + origin_left,
                    bottom + origin_top,
                )
            )
    return out


def pixel_of_subcell(
    spec: GridSpec,
    col_0: int,
    row_0: int,
    sub: int = 5,
    *,
    origin_left: int = 0,
    origin_top: int = 0,
) -> tuple[int, int]:
    """Centre d'une sous-case (defaut 5 = centre). Pour *verification*, pas clic prod."""
    left, top, right, bottom = _cell_bounds(spec, col_0, row_0)
    sl, st, sr, sb = _sub_bounds(left, top, right, bottom, sub)
    return (origin_left + (sl + sr) // 2, origin_top + (st + sb) // 2)


def iter_cell_labels(spec: GridSpec) -> Iterable[str]:
    for row in range(spec.rows):
        for col in range(spec.cols):
            yield label_of(spec.screen_index, col, row)
