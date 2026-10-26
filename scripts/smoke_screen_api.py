"""Smoke du contrat API (R-S5) : ce que les consommateurs peuvent tenir pour acquis.

Usage :
  python scripts/smoke_screen_api.py

Sortie : exit 0 si tous les controles passent, 1 sinon.

Headless par construction : aucune fenetre n'est ouverte, aucune image n'est
affichee. C'est precisement ce que ce smoke verifie, et il le verifie sur
l'ARBRE SYNTAXIQUE des modules du chemin API, pas sur leur documentation.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok, detail))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def _identifiants(module) -> tuple[set[str], set[str]]:
    """(identifiants references, modules importes) d'apres l'AST."""
    arbre = ast.parse(inspect.getsource(module))
    ids, imports = set(), set()
    for n in ast.walk(arbre):
        if isinstance(n, ast.Attribute):
            ids.add(n.attr)
        elif isinstance(n, ast.Name):
            ids.add(n.id)
        elif isinstance(n, ast.Import):
            imports.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            imports.add((n.module or "").split(".")[0])
    return ids, imports


def main() -> int:
    print("=" * 70)
    print("SMOKE contrat API (R-S5)")
    print("=" * 70)

    # --- import : rien de lourd tant qu'on n'appelle rien ---------------------- #
    print()
    print("Import")
    avant = set(sys.modules)
    t0 = time.perf_counter()
    import screen_api

    duree = (time.perf_counter() - t0) * 1000.0
    check("import screen_api rapide", duree < 200, f"{duree:.1f} ms")
    check("l'import ne charge pas cv2", "cv2" not in sys.modules,
          "un consommateur qui ne veut que le contexte ne paie pas les pixels")
    check("l'import ne charge pas screen_vision", "screen_vision" not in sys.modules,
          "screen_vision ouvre des fenetres OpenCV")
    check("import stable documente",
          all(hasattr(screen_api, n) for n in
              ("get_vision_snapshot", "get_context_snapshot", "describe_contract")))
    del avant

    # --- aucune interface graphique sur le chemin API -------------------------- #
    print()
    print("Aucune interface graphique, aucune actuation")
    import screen_content
    import screen_context
    import screen_cursor
    import screen_rich
    import screen_taskbar
    import screen_uia

    chemin = {
        "screen_api": screen_api,
        "screen_content": screen_content,
        "screen_context": screen_context,
        "screen_rich": screen_rich,
        "screen_cursor": screen_cursor,
        "screen_taskbar": screen_taskbar,
        "screen_uia": screen_uia,
    }
    gui = ("imshow", "namedWindow", "waitKey", "waitKeyEx", "destroyAllWindows",
           "setMouseCallback", "createTrackbar")
    actuation = ("SetCursorPos", "mouse_event", "SendInput", "keybd_event",
                 "SetForegroundWindow", "ShowWindow", "ClipCursor")
    for nom, mod in chemin.items():
        ids, imports = _identifiants(mod)
        fautes = [g for g in gui if g in ids]
        check(f"{nom} : aucune fenetre OpenCV", not fautes, ", ".join(fautes))
        fautes = [a for a in actuation if a in ids]
        check(f"{nom} : aucune actuation", not fautes, ", ".join(fautes))
        check(f"{nom} : n'importe pas screen_vision", "screen_vision" not in imports)
        check(f"{nom} : aucun import pyautogui", "pyautogui" not in imports)

    # --- contexte seul : le niveau bon marche ---------------------------------- #
    print()
    print("Contexte seul")
    ctx = screen_api.get_context_snapshot()
    check("contexte rendu", isinstance(ctx, dict) and ctx.get("v") == 1, f"v={ctx.get('v')}")

    # La garantie porte sur un consommateur qui n'importe QUE l'API. Dans ce
    # smoke, cv2 est deja charge par les modules importes plus haut pour
    # l'analyse syntaxique : verifier `sys.modules` ici ne prouverait rien.
    # On interroge donc un processus neuf.
    import subprocess

    code = (
        "import sys; sys.path.insert(0, r'" + str(ROOT / "src") + "');"
        "import screen_api; screen_api.get_context_snapshot();"
        "print('cv2' in sys.modules)"
    )
    try:
        sortie = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, timeout=120).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        sortie = f"echec: {exc}"
    check("contexte seul : cv2 jamais charge (processus neuf)", sortie == "False",
          f"cv2 charge = {sortie}")
    for champ in ("process", "window", "confidence", "unknown", "t_wall_ms"):
        check(f"contexte : champ '{champ}'", champ in ctx)
    check("contexte JSON-serialisable", _serialisable(ctx))
    check("contexte : abstention sur cible absente",
          screen_api.get_context_snapshot(target="ZZZ-controle-inexistant-42").get("target") is None,
          "aucun bbox invente")

    # --- vision complete -------------------------------------------------------- #
    print()
    print("Vision complete")
    for depth in ("minimal", "standard", "rich"):
        snap = screen_api.get_vision_snapshot(monitor=1, depth=depth)
        ok = isinstance(snap, dict) and snap.get("v") == 2 and snap.get("depth") == depth
        check(f"depth={depth} : schema v2", ok, f"v={snap.get('v')} depth={snap.get('depth')}")
        check(f"depth={depth} : JSON-serialisable", _serialisable(snap),
              "garantie du contrat")
        check(f"depth={depth} : aucune image exposee",
              all("_frames" not in e for e in snap["screens"]),
              "keep_frames n'est pas exposable")
        for champ in ("t_wall_ms", "monitors", "screens", "elapsed_ms", "system"):
            if not check(f"depth={depth} : champ '{champ}'", champ in snap):
                break

    # --- curseur ---------------------------------------------------------------- #
    print()
    print("Curseur")
    sans = screen_api.get_vision_snapshot(monitor=1, depth="minimal")
    check("aucun bloc curseur sans demande", sans.get("cursor") is None)
    avec = screen_api.get_vision_snapshot(monitor=1, depth="minimal", with_cursor=True)
    check("bloc curseur quand demande", isinstance(avec.get("cursor"), dict))
    reg = screen_api.get_vision_snapshot(monitor=1, depth="minimal", cursor_region=120)
    check("cursor_region implique la lecture du curseur",
          (reg.get("cursor") or {}).get("available") is True)
    check("snapshot avec curseur JSON-serialisable", _serialisable(reg))

    # --- profils et profondeurs -------------------------------------------------- #
    print()
    print("Profils")
    film = screen_api.get_vision_snapshot(monitor=1, profile="film", depth="standard")
    check("profil film : pas d'OCR", film["screens"][0].get("ocr") is None,
          "le titre est deja dans le contexte")
    agent = screen_api.get_vision_snapshot(monitor=1, profile="agent", depth="minimal")
    check("profil agent : OCR actif", agent["screens"][0].get("ocr") is not None)
    force = screen_api.get_vision_snapshot(monitor=1, profile="agent", depth="minimal",
                                           with_ocr=False)
    check("argument explicite prime sur le profil", force["screens"][0].get("ocr") is None,
          "meme regle que la CLI")

    # --- conditions d'environnement : pas d'exception ---------------------------- #
    print()
    print("Conditions d'environnement")
    faux = screen_api.get_vision_snapshot(monitor=99)
    check("moniteur inexistant -> erreur nette, pas d'exception", bool(faux.get("error")),
          str(faux.get("error")))
    check("moniteur inexistant -> aucun ecran invente", faux["screens"] == [])
    tous = screen_api.get_vision_snapshot(all_monitors=True, depth="minimal")
    check("tous les moniteurs", len(tous["screens"]) >= 1, f"{len(tous['screens'])} ecran(s)")

    # --- contrat declare ---------------------------------------------------------- #
    print()
    print("Contrat declare")
    c = screen_api.describe_contract()
    check("version d'API", c.get("api_version") == screen_api.API_VERSION, c.get("api_version"))
    check("schema contexte = v1", c["schemas"]["context"]["v"] == 1)
    check("schema vision = v2", c["schemas"]["vision"]["v"] == 2)
    check("les documents de schema existent",
          all((ROOT / c["schemas"][k]["doc"]).exists() for k in ("context", "vision")))
    check("profondeurs annoncees", set(c["depths"]) == {"minimal", "standard", "rich"})
    check("profils annonces", set(c["profiles"]) == {"film", "agent", "full"})
    check("garanties enoncees", len(c.get("garanties", [])) >= 5)
    check("contrat JSON-serialisable", _serialisable(c))

    failed = [x for x in CHECKS if not x[1]]
    print()
    print("=" * 70)
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} controles OK")
    if failed:
        for label, _, detail in failed:
            print(f"  KO : {label} {detail}")
        return 1
    print("SMOKE OK")
    return 0


def _serialisable(obj) -> bool:
    try:
        json.dumps(obj)
        return True
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
