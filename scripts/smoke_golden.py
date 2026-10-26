"""Smoke du harnais golden et des scenarios (R-S3a).

Usage :
  python scripts/smoke_golden.py

Sortie : exit 0 si tous les controles passent, 1 sinon.

Le harnais est teste SANS echantillon reel : la logique de seuils, la
decision d'appliquer l'agrandissement x3 et la discipline des statuts sont
verifiees sur des valeurs fabriquees ici. Un harnais de non-regression qui
n'aurait jamais ete exerce serait exactement le travers qu'il denonce.

Aucun echantillon n'est cree ni lu dans golden/ : le dossier de l'utilisateur n'est
pas touche.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import replay_golden as rg  # noqa: E402
import run_validation_scenarios as rv  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok, detail))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def _mesure(cer_moy, cer_max, rappel_moy, rappel_min, nom="test"):
    return {"moteur": nom, "ok": True, "n": 3, "echecs": 0,
            "cer_moy": cer_moy, "cer_max": cer_max,
            "rappel_moy": rappel_moy, "rappel_min": rappel_min,
            "ms_p50": 100.0, "ms_total": 300.0, "par_echantillon": {}}


def main() -> int:
    print("=" * 70)
    print("SMOKE golden + scenarios (R-S3a)")
    print("=" * 70)

    # --- seuils --------------------------------------------------------------- #
    print()
    print("Evaluation des seuils")
    bon = _mesure(0.005, 0.010, 0.99, 0.95)
    tenus, manques = rg.evaluer(bon, rg.SEUILS)
    check("mesure conforme -> seuils tenus", tenus and not manques)

    # Verite COMPLETE : le CER est un critere.
    for champ, mauvais, attendu in (
        ("CER moyen", _mesure(0.10, 0.010, 0.99, 0.95), "CER moyen"),
        ("CER max", _mesure(0.005, 0.90, 0.99, 0.95), "CER max"),
    ):
        t, m = rg.evaluer(mauvais, rg.SEUILS, "complete")
        check(f"verite complete : {champ} hors seuil -> detecte",
              (not t) and any(attendu in x for x in m), "; ".join(m)[:44])

    # Verite PARTIELLE : le CER n'est PAS un critere, car il compte le texte
    # lu en plus, et golden/README.md demande une verite partielle.
    for champ, mauvais in (
        ("CER moyen", _mesure(0.10, 0.010, 0.99, 0.95)),
        ("CER max", _mesure(0.005, 0.90, 0.99, 0.95)),
    ):
        t, m = rg.evaluer(mauvais, rg.SEUILS, "partial")
        check(f"verite partielle : {champ} hors seuil -> NON retenu",
              t and not m, "le CER punit les insertions, pas les erreurs")

    # Le rappel reste un critere dans les DEUX modes : rien n'est abaisse.
    for mode in ("partial", "complete"):
        for champ, mauvais, attendu in (
            ("rappel moyen", _mesure(0.005, 0.010, 0.50, 0.95), "rappel moyen"),
            ("rappel min", _mesure(0.005, 0.010, 0.99, 0.10), "rappel min"),
        ):
            t, m = rg.evaluer(mauvais, rg.SEUILS, mode)
            check(f"{mode} : {champ} hors seuil -> detecte",
                  (not t) and any(attendu in x for x in m))
    check("mode par defaut = verite partielle", rg.MODE_VERITE_DEFAUT == "partial",
          "c'est ce que golden/README.md demande a l'utilisateur")

    t, m = rg.evaluer({"ok": False}, rg.SEUILS)
    check("mesure absente -> non tenue, pas d'exception", (not t) and bool(m))

    # --- diagnostic par echantillon ------------------------------------------- #
    print()
    print("Diagnostic par echantillon")

    class _Ech:
        def __init__(self, nom, verite):
            self.name, self.verite, self.origine = nom, verite, "golden"

            class _F:
                shape = (86, 900, 3)
            self.frame = _F()

    lu = "alpha beta gamma et beaucoup de texte lu en plus par l ocr"
    diag = rg.diagnostic_tokens([_Ech("ech-a", "alpha beta gamma")], lambda f: lu)
    check("diagnostic produit une ligne par echantillon", len(diag) == 1)
    if diag:
        d = diag[0]
        check("tous les mots attendus trouves -> 0 manquant", d["manquants"] == 0,
              f"{d['trouves']}/{d['attendus']}")
        check("ratio de longueur calcule", d["ratio_longueur"] > 1.0, str(d["ratio_longueur"]))
    long_token = "untokentreslongquinexistepas"
    diag2 = rg.diagnostic_tokens([_Ech("ech-b", f"alpha {long_token}")], lambda f: "alpha")
    check("token manquant compte", diag2[0]["manquants"] == 1)
    check("longueur du token manquant exposee, pas son contenu",
          diag2[0]["longueurs_manquants"] == [len(long_token)],
          f"longueur {len(long_token)} rendue, jamais le mot")
    check("le diagnostic ne fuit aucun mot",
          long_token not in str(diag2), "longueurs et comptes seulement")

    # --- decision sur l'agrandissement x3 ------------------------------------- #
    print()
    print("Decision : appliquer l'agrandissement x3 en production ?")
    prod = _mesure(0.030, 0.05, 0.90, 0.80, "prod")
    cand = _mesure(0.005, 0.01, 0.99, 0.95, "candidate")

    r = rg.recommander_x3(prod, cand, 0)
    check("0 echantillon -> NON", r["appliquer"] is False, r["raison"][:52])
    check("0 echantillon -> le minimum requis est dit",
          r.get("minimum_requis") == rg.MIN_ECHANTILLONS_POUR_CONCLURE)
    r = rg.recommander_x3(prod, cand, 1)
    check("1 echantillon -> NON (sous le minimum)", r["appliquer"] is False)
    r = rg.recommander_x3(prod, cand, 2)
    check("2 echantillons, gain reel, seuils tenus -> OUI", r["appliquer"] is True,
          f"CER {r.get('cer_prod')} -> {r.get('cer_candidate')}")

    pire = _mesure(0.040, 0.06, 0.85, 0.70, "candidate")
    r = rg.recommander_x3(prod, pire, 3)
    check("candidate moins bonne -> NON", r["appliquer"] is False, r["raison"][:52])

    mieux_mais_hors = _mesure(0.025, 0.30, 0.90, 0.50, "candidate")
    r = rg.recommander_x3(prod, mieux_mais_hors, 3)
    check("candidate meilleure mais hors seuils -> NON", r["appliquer"] is False,
          "un gain relatif ne suffit pas")
    r = rg.recommander_x3(None, cand, 3)
    check("mesure de production absente -> NON", r["appliquer"] is False)

    # --- rapport : aucune fuite ------------------------------------------------ #
    print()
    print("Vie privee du rapport")
    class _Faux:
        name = "echantillon-test"
        origine = "golden"

        class frame:
            shape = (100, 200, 3)

    texte = rg.rapport([_Faux()], [prod, cand], "SEUILS TENUS", [], rg.recommander_x3(prod, cand, 2))
    check("le rapport nomme l'echantillon", "echantillon-test" in texte)
    check("le rapport porte un verdict explicite", "VERDICT" in texte)
    check("le rapport tranche sur x3", "appliquer" in texte)
    vide = rg.rapport([], [], "RIEN A MESURER (golden vide)", [], rg.recommander_x3(None, None, 0))
    check("golden vide -> le rapport le dit clairement", "GOLDEN VIDE" in vide)
    check("golden vide -> ce n'est pas presente comme une erreur",
          "pas une erreur" in vide, "le harnais fonctionne, il attend des echantillons")
    check("golden vide -> oriente vers le mode d'emploi", "golden/README.md" in vide)

    # --- statuts des scenarios -------------------------------------------------- #
    print()
    print("Discipline des statuts V1-V8")
    check("vocabulaire ferme", set(rv.STATUTS) == {"pass", "fail", "skip", "unknown"})
    check("un statut inconnu est refuse",
          _statut_invalide_rejete(), "assertion dans _res")

    res = rv.executer(None)
    ids = [r["id"] for r in res]
    check("les 8 scenarios sont rendus", ids == sorted(ids) and len(res) == 8, str(ids))
    check("chaque scenario porte un statut valide",
          all(r["status"] in rv.STATUTS for r in res))
    check("chaque scenario porte une raison non vide",
          all(r.get("reason") for r in res))
    check("aucune intention declaree -> V1 et V8 ne sont jamais 'pass'",
          all(r["status"] != "pass" for r in res if r["id"] in ("V1", "V8")),
          "trouver un bouton n'est un succes que si on attendait d'en trouver un")
    check("V5 n'est pas invente en pass",
          next(r for r in res if r["id"] == "V5")["status"] == "unknown")

    v1_v8 = rv.v1_v8_postuler([], "v8")
    check("sans navigateur -> skip, pas fail",
          all(r["status"] == "skip" for r in v1_v8),
          "un prerequis absent n'est pas un echec")
    v1_v8 = rv.v1_v8_postuler([], None)
    check("sans navigateur et sans intention -> skip aussi",
          all(r["status"] == "skip" for r in v1_v8))

    texte_v = rv.rapport(res)
    check("le rapport rappelle que skip n'est pas un succes",
          "ne sont PAS des succes" in texte_v)
    check("le rapport compte chaque statut",
          all(f"{s} =" in texte_v for s in rv.STATUTS))

    # --- aucune actuation dans le runner ----------------------------------------- #
    print()
    print("Aucune actuation dans le runner")
    import ast
    import inspect

    arbre = ast.parse(inspect.getsource(rv))
    identifiants, imports, boucles = set(), set(), 0
    for n in ast.walk(arbre):
        if isinstance(n, ast.Attribute):
            identifiants.add(n.attr)
        elif isinstance(n, ast.Name):
            identifiants.add(n.id)
        elif isinstance(n, ast.Import):
            imports.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            imports.add((n.module or "").split(".")[0])
        elif isinstance(n, ast.While):
            if isinstance(n.test, ast.Constant) and n.test.value is True:
                boucles += 1
    for interdit in ("SetCursorPos", "mouse_event", "SendInput", "SetForegroundWindow",
                     "ShowWindow", "keybd_event"):
        check(f"aucun appel d'actuation : {interdit}", interdit not in identifiants,
              "le runner regarde, il ne touche pas")
    check("aucun import pyautogui", "pyautogui" not in imports)
    check("aucune boucle infinie", boucles == 0)

    failed = [c for c in CHECKS if not c[1]]
    print()
    print("=" * 70)
    print(f"{len(CHECKS) - len(failed)}/{len(CHECKS)} controles OK")
    if failed:
        for label, _, detail in failed:
            print(f"  KO : {label} {detail}")
        return 1
    print("SMOKE OK")
    return 0


def _statut_invalide_rejete() -> bool:
    try:
        rv._res("VX", "peut-etre", "statut hors vocabulaire")
    except AssertionError:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
