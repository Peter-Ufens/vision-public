"""Lire une case de la grille, ou une plage de cases (V1.1).

Usage :
  python scripts/lire_cases.py 1:A1                      # couleurs, formes
  python scripts/lire_cases.py 1:F8-1:K9 --avec-texte    # bande du bas + OCR
  python scripts/lire_cases.py 2:C4 --decrire            # + phrase d'un modele local
  python scripts/lire_cases.py 2:C4 --decrire --moteur moondream   # Moondream3 (lent)
  python scripts/lire_cases.py 2:A1-2:P3 --rapport-mesure    # phrases du code, sans modele
  python scripts/lire_cases.py 2:A1-2:P3 --rapport-mesure --decrire   # les deux cote a cote
  python scripts/lire_cases.py 1:A1 2:P9 --json          # plusieurs cibles, JSON
  python scripts/lire_cases.py --grille                  # taille de la grille par ecran

La grille est la meme que celle de l'overlay (scripts/grille.py) : 9 lignes,
colonnes A, B, C... Une case qui n'existe pas (1:L16 sur une grille 16x9) est
refusee avec un message clair, et le code de sortie vaut 2.

Lecture seule : aucune image ecrite, rien d'enregistre, aucun telechargement.
Ce qui est lu de l'ecran s'affiche ici et nulle part ailleurs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from screen_cells import MODELE_DEFAUT, lire  # noqa: E402


def _afficher(r: dict) -> None:
    print("=" * 72)
    print(f"{r['cible']}   ecran {r['ecran']}, grille {r['grille']}, {r['cases']} case(s), "
          f"pixels {r['bbox'][0]},{r['bbox'][1]} -> {r['bbox'][2]},{r['bbox'][3]}")
    print("=" * 72)
    couleurs = ", ".join(f"{c['nom']} {c['pct']:.0f} %" for c in r["couleurs"][:4]) or "?"
    print(f"  Couleurs   : {couleurs}")
    print(f"  Lumiere    : {r['luminance']:.0f}/255, contraste {r['contraste']:.0f}"
          + ("   ZONE NOIRE" if r["zone_noire"] else ""))
    f = r["formes"]
    print(f"  Formes     : {f['niveau']} ({f['details_pct']} % de contours), "
          f"{f['rectangles']} rectangle(s) net(s), {f['autres_contours']} autre(s) contour(s), "
          f"{f.get('triangles', 0)} triangle(s), {f.get('ronds', 0)} rond(s)")
    if r["par_case"]:
        print("  Par case   :")
        ligne = []
        for pc in r["par_case"]:
            noir = " NOIR" if pc["zone_noire"] else ""
            ligne.append(f"{pc['case'].split(':')[1]} {pc['couleur'] or '?'} {pc['pct']:.0f}% L{pc['luminance']:.0f}{noir}")
            if len(ligne) == 4:
                print("    " + " | ".join(ligne))
                ligne = []
        if ligne:
            print("    " + " | ".join(ligne))
    t = r.get("texte")
    if t is not None:
        if not t["available"]:
            print(f"  Texte      : non lu ({t['reason']})")
        elif not t["lignes"]:
            print(f"  Texte      : aucun texte lisible ({t['elapsed_ms']:.0f} ms)")
        else:
            print(f"  Texte      : {len(t['lignes'])} ligne(s), {t['mots']} mot(s), {t['elapsed_ms']:.0f} ms")
            for ln in t["lignes"][:25]:
                cases = ln.get("cases") or []
                ou = ln["case"] if len(cases) <= 1 else f"{cases[0]}-{cases[-1].split(':')[1]}"
                print(f"    [{ou}] {ln['texte']}")
    d = r.get("description")
    if d is not None:
        if d.get("available"):
            print(f"  Description ({d.get('moteur')} {d.get('modele', '')}, {d['elapsed_ms']:.0f} ms, A VERIFIER) :")
            print(f"    {d['texte']}")
            if d.get("tronquee"):
                print("    (phrase COUPEE par la limite de longueur : la fin manque)")
        else:
            print(f"  Description : indisponible ({d.get('reason')})")
    rm = r.get("rapport_mesure")
    if rm is not None:
        if rm.get("texte"):
            print("  Rapport-mesure (ecrit par le code, sans modele, rien de devine) :")
            for ph in rm["phrases"]:
                print(f"    {ph}")
        else:
            print(f"  Rapport-mesure : indisponible ({rm.get('raison')})")
    for n in r["notes"]:
        print(f"  note : {n}")
    print(f"  capture {r['capture_ms']:.0f} ms, taille {r['taille'][0]}x{r['taille'][1]}")


def _grille() -> int:
    from screen_context import list_monitors
    from screen_grid import _col_letters, grid_for_monitor

    for m in list_monitors():
        g = grid_for_monitor(m)
        prim = " (primaire)" if m.is_primary else ""
        print(f"ecran {m.index}{prim} : {m.width}x{m.height} a ({m.left},{m.top}), "
              f"grille {g.cols}x{g.rows} : colonnes A a {_col_letters(g.cols - 1)}, "
              f"lignes 1 a {g.rows}, case {g.cell_w:.0f}x{g.cell_h:.0f} px")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")  # un caractere exotique lu par l'OCR ne doit pas planter
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description="Lire une case ou une plage de la grille (V1.1).")
    p.add_argument("cibles", nargs="*", help="1:B7, 1:B7.5 ou 1:F8-1:K9")
    p.add_argument("--avec-texte", action="store_true", help="lire le texte (OCR Tesseract)")
    p.add_argument("--decrire", action="store_true",
                   help="demander une phrase a un modele de vision local (a verifier)")
    p.add_argument("--moteur", choices=("ollama", "moondream"), default="ollama",
                   help="ollama (defaut, ~5 s) ou moondream (Moondream3 local, ~50 s a froid)")
    p.add_argument("--modele", default=MODELE_DEFAUT, help=f"modele Ollama (defaut {MODELE_DEFAUT})")
    p.add_argument("--rapport-mesure", action="store_true",
                   help="phrases ecrites par le code a partir des mesures, sans modele "
                        "(gabarit context/vision-rapport-mesure.md) ; lit le texte tout seul")
    p.add_argument("--json", action="store_true", help="sortie JSON")
    p.add_argument("--grille", action="store_true", help="afficher la grille de chaque ecran")
    p.add_argument("--lang", default="fra+eng", help="langues Tesseract")
    p.add_argument("--min-conf", type=int, default=45, help="confiance OCR minimale")
    a = p.parse_args(argv)

    if a.grille:
        return _grille()
    if not a.cibles:
        p.error("donner au moins une case, par exemple 1:A1")

    resultats, refus = [], 0
    for cible in a.cibles:
        try:
            r = lire(cible, avec_texte=a.avec_texte, decrire=a.decrire, moteur=a.moteur,
                     modele=a.modele, ocr_lang=a.lang, ocr_min_conf=a.min_conf,
                     rapport_mesure=a.rapport_mesure)
        except ValueError as exc:
            refus += 1
            if a.json:
                resultats.append({"demande": cible, "refus": str(exc)})
            else:
                print(f"REFUS {cible} : {exc}")
            continue
        if a.json:
            resultats.append(r)
        else:
            _afficher(r)
    if a.json:
        print(json.dumps(resultats if len(resultats) != 1 else resultats[0], ensure_ascii=False, indent=2))
    return 2 if refus else 0


if __name__ == "__main__":
    raise SystemExit(main())
