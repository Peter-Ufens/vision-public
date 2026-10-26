"""Smoke de l'exposition UI (R-S8) : U1 inventaire, U2 roles, U3 hwnd, U7 occultation.

Usage :
  python scripts/smoke_ui_inventory.py

Sortie : exit 0 si tous les controles passent, 1 sinon.

CE QUE CE SMOKE SURVEILLE VRAIMENT

R-S8 corrige un mode de defaillance precis : une requete UIA sur un role non
enregistre rendait une liste VIDE SANS ERREUR, indiscernable de "cette
application n'expose aucun controle de ce type". Le code en avait tire une
explication inventee (barre d'onglets repliee), propagee jusqu'au north star.

Le controle central ci-dessous verifie donc qu'un role inconnu **leve**, et
que chaque role enregistre est reellement interrogeable. Sans cela, le meme
piege se retendrait au prochain role ajoute.

VIE PRIVEE : aucun nom de controle n'est affiche. Comptes, longueurs et
durees seulement.
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CHECKS: list[tuple[str, bool]] = []
SAUTES: list[tuple[str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def saute(bloc: str, raison: str) -> None:
    """Un controle qui n'a pas ete joue, dit a voix haute.

    Sans ce compteur, le nombre de controles varie avec l'etat de l'ecran et
    la sortie affiche "SMOKE OK" dans les deux cas. Observe en R-S9 : 37/37
    puis 51/51 selon les fenetres ouvertes, sans que rien ne signale les
    quatorze controles absents. Un controle saute doit se voir, sinon il se
    confond avec un controle reussi -- meme piege que le vide silencieux
    corrige en R-S8.
    """
    SAUTES.append((bloc, raison))
    print(f"  [-- ] {bloc} : non exerce ({raison})")


def main() -> int:  # noqa: C901
    print("=" * 70)
    print("SMOKE exposition UI (R-S8)")
    print("=" * 70)

    import screen_api
    import screen_uia as su
    from screen_context import list_monitors, list_windows

    monitors = list_monitors()
    fenetres = [w for w in list_windows(monitors=monitors)
                if not w.is_overlay and not w.is_cloaked]

    # --- U2 : les roles, et le piege du vide silencieux --------------------- #
    print()
    print("U2 - roles interrogeables")
    for role in ("TabItem", "TreeItem", "SplitButton", "Slider", "ToolBar"):
        check(f"role {role} enregistre", role in su._ROLES)

    leve = False
    try:
        su.collect(None, "RoleQuiNExistePas")
    except ValueError:
        leve = True
    except Exception:  # noqa: BLE001
        pass
    check("un role inconnu LEVE au lieu de rendre un vide silencieux", leve,
          "c'est ce vide qui avait fait inventer une barre d'onglets repliee")

    leve_inv = False
    try:
        su.inventory(0, roles=("Button", "RoleQuiNExistePas"))
    except ValueError:
        leve_inv = True
    except Exception:  # noqa: BLE001
        pass
    check("inventory refuse aussi un role inconnu", leve_inv)

    leve_scope = False
    try:
        su.inventory(0, scope="nimporte-quoi")
    except ValueError:
        leve_scope = True
    except Exception:  # noqa: BLE001
        pass
    check("inventory refuse un scope inconnu", leve_scope)

    # Chaque role enregistre doit produire une condition UIA utilisable :
    # un nom present dans _ROLES mais absent de comtypes retendrait le piege.
    iuia, mod = su._uia()
    if iuia is not None:
        sans_condition = [r for r in su._ROLES if su._type_cond(iuia, mod, r) is None]
        check("chaque role enregistre a une condition UIA reelle",
              not sans_condition, str(sans_condition))
    else:
        check("UIA indisponible : roles non verifiables", False, su.unavailable_reason())

    check("les roles actionnables sont tous enregistres",
          all(r in su._ROLES for r in su.ROLES_ACTIONNABLES),
          f"{len(su.ROLES_ACTIONNABLES)} roles")
    check("Text et Document ne sont pas actionnables",
          "Text" not in su.ROLES_ACTIONNABLES and "Document" not in su.ROLES_ACTIONNABLES,
          "on les lit, on ne les clique pas")

    # --- U1 : inventaire ---------------------------------------------------- #
    print()
    print("U1 - inventaire des controles cliquables")
    check("au moins une fenetre applicative ouverte", bool(fenetres),
          f"{len(fenetres)} fenetre(s)")

    inv = screen_api.list_controls()
    check("list_controls rend un resultat", isinstance(inv, dict))
    check("schema annonce", inv.get("v") == screen_api.SCHEMA_CONTROLES)
    check("retour JSON-serialisable", bool(json.dumps(inv)))

    if inv["available"]:
        c = inv["counts"]
        check("les comptes concordent avec la liste", c["total"] == len(inv["controls"]),
              f"{c['total']}")
        check("somme par role = total", sum(c["par_role"].values()) == c["total"])
        champs = {"name", "role", "bbox", "class", "in_document", "occluded"}
        check("chaque controle porte les champs du schema",
              all(champs <= set(ctrl) for ctrl in inv["controls"]))
        check("chaque bbox est non degeneree",
              all(ctrl["bbox"][2] > ctrl["bbox"][0] and ctrl["bbox"][3] > ctrl["bbox"][1]
                  for ctrl in inv["controls"]))
        check("chaque role rendu est un role demande",
              all(ctrl["role"] in inv["roles"] for ctrl in inv["controls"]),
              str(sorted({ctrl["role"] for ctrl in inv["controls"]} - set(inv["roles"]))))
        check("tri par position, de haut en bas",
              all(a["bbox"][1] <= b["bbox"][1]
                  for a, b in zip(inv["controls"], inv["controls"][1:])))
        check("cout mesure et rendu", "total" in inv["timings_ms"],
              f"{inv['timings_ms'].get('total')} ms")
    else:
        check("indisponible : la raison est ecrite", bool(inv["reason"]), inv["reason"])
        saute("U1 inventaire detaille", "arbre UIA indisponible sur la fenetre active")

    # Restreindre les roles doit restreindre le resultat, pas l'inventer.
    seul = screen_api.list_controls(roles=["Button"])
    if not (seul["available"] and inv["available"]):
        saute("U1 restriction de roles", "inventaire indisponible")
    if seul["available"] and inv["available"]:
        check("restreindre les roles ne rend que ce role",
              all(ctrl["role"] == "Button" for ctrl in seul["controls"]))
        check("restreindre ne rend jamais PLUS que l'inventaire complet",
              seul["counts"]["total"] <= inv["counts"]["total"],
              f"{seul['counts']['total']} <= {inv['counts']['total']}")

    borne = screen_api.list_controls(limit=3)
    if not borne["available"]:
        saute("U1 limit", "inventaire indisponible")
    if borne["available"]:
        check("limit borne reellement la liste", borne["counts"]["total"] <= 3,
              f"{borne['counts']['total']}")

    # --- U3 : viser une fenetre qui n'est pas au premier plan --------------- #
    print()
    print("U3 - adresser une fenetre non-foreground")
    arriere = [w for w in fenetres if not w.is_foreground]
    if arriere:
        w = arriere[0]
        vis = screen_api.get_vision_snapshot(hwnd=w.hwnd, depth="minimal")
        check("get_vision_snapshot accepte hwnd", not vis.get("error"), str(vis.get("error")))
        if not vis.get("screens"):
            saute("U3 fenetre analysee", "aucun ecran rendu")
        if vis.get("screens"):
            e = vis["screens"][0]
            vu = ((e.get("context") or {}).get("process") or {}).get("exe_name")
            check("la fenetre analysee est bien celle demandee", vu == w.exe_name)
            check("le retour dit que la fenetre a ete imposee",
                  any("hwnd" in n for n in e.get("notes", [])))
        check("retour JSON-serialisable", bool(json.dumps(vis)))

        ctrl = screen_api.list_controls(hwnd=w.hwnd)
        check("list_controls accepte hwnd", isinstance(ctrl, dict))
        if not ctrl["available"]:
            saute("U3 inventaire cible", ctrl["reason"])
        if ctrl["available"]:
            check("l'inventaire porte sur la fenetre demandee", ctrl["hwnd"] == w.hwnd)
    else:
        saute("U3 fenetre non-foreground", "aucune fenetre d'arriere-plan ouverte")

    # Fenetre inexistante : erreur, jamais exception (garantie 5).
    mort = screen_api.get_vision_snapshot(hwnd=999_999_999)
    check("hwnd inexistant : erreur, pas exception", bool(mort.get("error")))
    mort2 = screen_api.list_controls(hwnd=999_999_999)
    check("list_controls sur hwnd inexistant : abstention",
          mort2["available"] is False and bool(mort2["reason"]))

    # --- U7 : occultation ---------------------------------------------------- #
    print()
    print("U7 - occultation")
    if not (inv["available"] and inv["controls"]):
        saute("U7 occultation", "aucun controle a examiner")
    if inv["available"] and inv["controls"]:
        valeurs = {ctrl["occluded"] for ctrl in inv["controls"]}
        check("occluded vaut vrai, faux ou None -- jamais autre chose",
              valeurs <= {True, False, None}, str(valeurs))
        check("les comptes d'occultation concordent",
              inv["counts"]["occultes"]
              == sum(1 for ctrl in inv["controls"] if ctrl["occluded"] is True))
        check("l'occultation est mesuree", "occultation" in inv["timings_ms"],
              f"{inv['timings_ms'].get('occultation')} ms")

    # La fenetre au premier plan ne doit pas etre occultee en son centre :
    # temoin negatif, sans lequel "tout occulte" passerait pour un succes.
    fg = next((w for w in fenetres if w.is_foreground), None)
    if fg is not None:
        inv_fg = screen_api.list_controls(hwnd=fg.hwnd)
        if inv_fg["available"] and inv_fg["counts"]["total"] >= 3:
            part = inv_fg["counts"]["occultes"] / inv_fg["counts"]["total"]
            check("la fenetre au premier plan n'est pas donnee pour recouverte",
                  part < 0.5, f"{inv_fg['counts']['occultes']}/{inv_fg['counts']['total']}")

    # Desactiver l'occultation doit la retirer, pas la deviner.
    sans = screen_api.list_controls(with_occlusion=False)
    if sans["available"] and sans["controls"]:
        check("with_occlusion=False n'invente aucune valeur",
              all("occluded" not in ctrl for ctrl in sans["controls"]))

    from screen_cursor import window_at_point
    check("window_at_point rend un entier ou None",
          isinstance(window_at_point(10, 10), (int, type(None))))

    # --- lecture seule -------------------------------------------------------- #
    print()
    print("Lecture seule")
    interdits = {"SetCursorPos", "mouse_event", "keybd_event", "SendInput",
                 "SetForegroundWindow", "SetWindowPos", "click", "typewrite"}
    for mod_name in ("screen_uia", "screen_api", "screen_cursor"):
        module = sys.modules.get(mod_name) or __import__(mod_name)
        arbre = ast.parse(inspect.getsource(module))
        noms = {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
        noms |= {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
        check(f"aucune actuation dans {mod_name}", not (noms & interdits),
              str(sorted(noms & interdits)))

    # --- cout d'entree : l'inventaire ne doit pas faire payer les pixels ------ #
    print()
    print("Cout d'entree")
    # En PROCESSUS NEUF : ce smoke importe des modules porteurs de cv2 pour
    # son analyse syntaxique, donc un test sur sys.modules ici mesurerait son
    # propre effet de bord. Meme piege qu'en R-S5, meme parade.
    import subprocess

    code = (
        "import sys; sys.path.insert(0, r'" + str(ROOT / "src") + "');"
        "import screen_api; screen_api.list_controls();"
        "print('CV2', 'cv2' in sys.modules)"
    )
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
        charge = None
        for ligne in (p.stdout or "").splitlines():
            if ligne.startswith("CV2 "):
                charge = ligne.split()[1] == "True"
        check("list_controls n'a pas charge cv2 (processus neuf)", charge is False,
              "un agent qui enumere l'UI ne paie pas les pixels")
    except Exception as exc:  # noqa: BLE001
        check("verification cv2 en processus neuf", False, str(exc))

    # --- contrat --------------------------------------------------------------- #
    print()
    print("Contrat")
    contrat = screen_api.describe_contract()
    check("version de contrat relevee", contrat["api_version"] == screen_api.API_VERSION,
          contrat["api_version"])
    check("le schema des controles est annonce", "controls" in contrat["schemas"])
    doc = ROOT / contrat["schemas"]["controls"]["doc"]
    check("le document de schema annonce existe", doc.is_file(), str(doc.name))
    check("les roles actionnables sont annonces a l'execution",
          contrat.get("roles_actionnables") == list(su.ROLES_ACTIONNABLES))

    # Non-regression du contrat 1.0 : rien n'a ete retire.
    for nom in ("get_vision_snapshot", "get_context_snapshot", "describe_contract"):
        check(f"{nom} toujours exporte", nom in screen_api.__all__)
    check("les schemas contexte et vision n'ont pas bouge",
          contrat["schemas"]["context"]["v"] == 1 and contrat["schemas"]["vision"]["v"] == 2)

    print()
    print("=" * 70)
    ko = [label for label, ok in CHECKS if not ok]
    print(f"{len(CHECKS) - len(ko)}/{len(CHECKS)} controles OK")
    if SAUTES:
        print(f"{len(SAUTES)} bloc(s) NON EXERCE(S) -- l'ecran ne s'y pretait pas :")
        for bloc, raison in SAUTES:
            print(f"  - {bloc} : {raison}")
        print("Un bloc saute n'est pas un bloc reussi.")
    if ko:
        print("SMOKE KO")
        for label in ko:
            print(f"  - {label}")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
