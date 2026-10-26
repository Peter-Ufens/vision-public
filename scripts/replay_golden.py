"""Harnais de non-regression OCR (R-S3a) : rejeu du golden set.

R-S2d a montre que les vrais defauts ne sortent que face a un vrai ecran.
Optimiser la latence OCR (~2,5 s en `rich`) sans harnais de non-regression,
ce serait optimiser a l'aveugle : on saurait qu'on a gagne du temps, pas si
on a casse la lecture.

Difference avec `bakeoff_ocr.py`, qui existe deja :

  bakeoff       compare des MOTEURS et des reglages entre eux, pour choisir
  replay (ici)  rejoue la configuration DE PRODUCTION contre des seuils,
                pour detecter une regression

Le second sert aussi a trancher une question laissee ouverte par R-S2b :
faut-il passer la production en `tess-psm6-x3` ? Le bake-off le recommandait
sur un corpus SYNTHETIQUE. La regle retenue est stricte : on n'applique le
reglage que si au moins deux echantillons REELS le confirment.

Usage :
  python scripts/replay_golden.py                 # verdict a l'ecran
  python scripts/replay_golden.py --report        # + rapport dans preuves/
  python scripts/replay_golden.py --soft          # ne jamais sortir en erreur

Codes de sortie :
  0  rien a mesurer (golden vide), ou tous les seuils tenus
  1  regression : des seuils mesures ne sont pas tenus

Golden vide n'est PAS une erreur : le harnais fonctionne, il n'a rien a
mesurer. C'est l'etat normal tant que l'utilisateur n'a pas depose d'echantillons.

VIE PRIVEE : le rapport ne contient QUE des metriques et des noms
d'echantillons. Jamais le texte lu, jamais la verite terrain.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import bakeoff_ocr as bo  # noqa: E402  (metriques et chargement : une seule source)

PREUVES_DIR = ROOT / "preuves"

# Configuration reellement utilisee par la production aujourd'hui.
# `ocr_screen.ocr_boxes` appelle `image_to_data` sans `--psm`, donc psm 3, et
# sans agrandissement. C'est la reference a battre.
VARIANTE_PROD = "tess-psm3-x1"

# Candidate issue du bake-off R-S2b (corpus synthetique : CER divise par 4,5).
VARIANTE_CANDIDATE = "tess-psm6-x3"

# Mode de verite terrain. `golden/README.md` demande explicitement a l'utilisateur de
# ne transcrire que deux ou trois lignes representatives, "autant de donnees
# personnelles en moins sur le disque". La verite est donc PARTIELLE par
# construction, et le CER, qui compte les insertions, punit exactement cela.
#
# Mesure sur le golden reel (31/08) : sur film-votark-01, le rappel est de
# 1,000 (tous les mots attendus sont trouves) alors que le CER vaut 0,394 ;
# sur film-youtube-01, l'OCR lit 284 caracteres pour une verite de 77, et les
# insertions expliquent 2,69 des 2,766 de CER, soit 97 % de "l'erreur".
#
# Autrement dit, mon propre mode d'emploi et ma propre metrique se
# contredisaient. En verite partielle, le CER n'est pas un critere : il est
# rendu a titre indicatif, et le verdict se prend sur le rappel.
MODE_VERITE_DEFAUT = "partial"

# Seuils de context/ocr-quality-criteria.md § 3.
SEUILS: dict[str, float] = {
    "cer_moy_max": 0.02,
    "cer_max_max": 0.05,
    "rappel_moy_min": 0.95,
    "rappel_min_min": 0.80,
}

# Nombre d'echantillons reels en dessous duquel on refuse de conclure.
# Deux, c'est deja peu ; en dessous, une seule page atypique deciderait pour
# tout le produit.
MIN_ECHANTILLONS_POUR_CONCLURE = 2


def evaluer(
    resultat: dict[str, Any],
    seuils: dict[str, float],
    mode_verite: str = MODE_VERITE_DEFAUT,
) -> tuple[bool, list[str]]:
    """(seuils tenus, liste des manquements).

    En verite PARTIELLE, le CER n'est pas retenu comme critere : il mesure
    aussi le texte que l'OCR a lu en plus, et la verite ne pretend pas
    couvrir toute la bande. Le rappel, lui, reste valide : il demande si ce
    qui etait attendu a ete trouve, sans rien exiger sur le reste.

    Ce n'est PAS un abaissement de seuil. Les seuils de rappel sont
    inchanges ; c'est le critere inapplicable qui est retire.
    """
    if not resultat.get("ok"):
        return (False, ["le moteur n'a produit aucune mesure"])
    manques = []
    if mode_verite == "complete":
        if resultat["cer_moy"] > seuils["cer_moy_max"]:
            manques.append(f"CER moyen {resultat['cer_moy']:.4f} > {seuils['cer_moy_max']}")
        if resultat["cer_max"] > seuils["cer_max_max"]:
            manques.append(f"CER max {resultat['cer_max']:.4f} > {seuils['cer_max_max']}")
    if resultat["rappel_moy"] < seuils["rappel_moy_min"]:
        manques.append(f"rappel moyen {resultat['rappel_moy']:.3f} < {seuils['rappel_moy_min']}")
    if resultat["rappel_min"] < seuils["rappel_min_min"]:
        manques.append(f"rappel min {resultat['rappel_min']:.3f} < {seuils['rappel_min_min']}")
    return (not manques, manques)


def diagnostic_tokens(echantillons: list[Any], moteur: Any) -> list[dict[str, Any]]:
    """Par echantillon : combien de mots attendus, combien trouves, et leurs
    longueurs. Aucun mot n'est reproduit.

    Sert a voir POURQUOI un rappel est bas. Mesure sur le golden reel : sur
    film-youtube-01, les deux tokens manquants font 9 et 31 caracteres. Un
    token de 31 caracteres n'est pas un mot : c'est une phrase ou une adresse
    collee, et `rappel_mots` exige une correspondance exacte, donc une seule
    faute de lecture le fait echouer entierement. Le meme piege avait ete
    documente en R-S2b sur les URL.

    La metrique n'est PAS corrigee ici : elle est partagee avec le bake-off,
    et la retoucher changerait en silence des chiffres deja publies. Le
    probleme est expose, sa correction reste une decision humaine.
    """
    out = []
    for e in echantillons:
        try:
            sortie = moteur(e.frame)
        except Exception:  # noqa: BLE001
            continue
        lu = bo.normalise(sortie)
        mots = [m for m in bo.normalise(e.verite).split() if len(m) >= 4]
        manquants = [m for m in mots if m not in lu]
        out.append({
            "echantillon": e.name,
            "attendus": len(mots),
            "trouves": len(mots) - len(manquants),
            "manquants": len(manquants),
            "longueurs_manquants": sorted(len(m) for m in manquants),
            "ratio_longueur": round(len(lu) / max(1, len(bo.normalise(e.verite))), 2),
        })
    return out


def recommander_x3(
    prod: dict[str, Any] | None,
    cand: dict[str, Any] | None,
    n_echantillons: int,
    mode_verite: str = MODE_VERITE_DEFAUT,
) -> dict[str, Any]:
    """Faut-il passer la production en agrandissement x3 ?

    Trois conditions cumulatives, et l'absence d'echantillons suffit a dire
    non. Le bake-off R-S2b concluait sur du synthetique ; un reglage de
    production se decide sur du reel.
    """
    out: dict[str, Any] = {
        "appliquer": False,
        "echantillons": n_echantillons,
        "minimum_requis": MIN_ECHANTILLONS_POUR_CONCLURE,
    }
    if n_echantillons < MIN_ECHANTILLONS_POUR_CONCLURE:
        out["raison"] = (
            f"{n_echantillons} echantillon(s) reel(s), il en faut au moins "
            f"{MIN_ECHANTILLONS_POUR_CONCLURE}. Le bake-off R-S2b concluait sur "
            "un corpus synthetique : insuffisant pour changer la production."
        )
        return out
    if not prod or not cand or not prod.get("ok") or not cand.get("ok"):
        out["raison"] = "une des deux variantes n'a produit aucune mesure"
        return out

    # En verite partielle, le CER n'est pas comparable : on arbitre sur le
    # rappel, qui reste valide. Le sens du gain est inverse (plus haut = mieux).
    if mode_verite == "complete":
        gain = prod["cer_moy"] - cand["cer_moy"]
        out["critere"] = "CER"
    else:
        gain = cand["rappel_moy"] - prod["rappel_moy"]
        out["critere"] = "rappel"
        out["rappel_prod"] = prod["rappel_moy"]
        out["rappel_candidate"] = cand["rappel_moy"]
    tenus, manques = evaluer(cand, SEUILS, mode_verite)
    out["cer_prod"] = prod["cer_moy"]
    out["cer_candidate"] = cand["cer_moy"]
    out["gain_cer"] = round(gain, 4)
    out["candidate_tient_les_seuils"] = tenus
    if gain <= 0:
        out["raison"] = (
            f"la candidate ne fait pas mieux que la production sur du reel "
            f"(critere {out['critere']}, ecart {gain:+.4f})"
        )
        return out
    if not tenus:
        out["raison"] = "la candidate est meilleure mais ne tient pas les seuils : " + " ; ".join(manques)
        return out
    out["appliquer"] = True
    out["raison"] = (
        f"{n_echantillons} echantillons reels, CER {prod['cer_moy']:.4f} -> "
        f"{cand['cer_moy']:.4f}, seuils tenus"
    )
    return out


def rapport(
    echantillons: list[Any],
    mesures: list[dict[str, Any]],
    verdict: str,
    manquements: list[str],
    reco: dict[str, Any],
    mode_verite: str = MODE_VERITE_DEFAUT,
    diagnostic: list[dict[str, Any]] | None = None,
) -> str:
    lignes = [
        "Rejeu golden - non-regression OCR - vision-public R-S3a",
        f"Date : {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"Echantillons reels : {len(echantillons)}",
    ]
    for e in echantillons:
        lignes.append(f"  - {e.name} [{e.origine}] {e.frame.shape[1]}x{e.frame.shape[0]}")
    lignes.append("")
    if not echantillons:
        lignes += [
            "GOLDEN VIDE : rien a mesurer, et ce n'est pas une erreur.",
            "Le harnais fonctionne ; il attend des echantillons.",
            "Mode d'emploi : golden/README.md",
            "",
        ]
    else:
        lignes.append(f"{'variante':<22} {'CER moy':>9} {'CER max':>9} {'rappel':>9} {'rap.min':>9} {'ms p50':>9}")
        lignes.append("-" * 72)
        for m in mesures:
            if not m.get("ok"):
                lignes.append(f"{m['moteur']:<22} {'ECHEC':>9}")
                continue
            lignes.append(
                f"{m['moteur']:<22} {m['cer_moy']:>9.4f} {m['cer_max']:>9.4f} "
                f"{m['rappel_moy']:>9.3f} {m['rappel_min']:>9.3f} {m['ms_p50']:>9.1f}"
            )
        lignes.append("")
        lignes.append(f"Mode de verite terrain : {mode_verite}")
        if mode_verite != "complete":
            lignes += [
                "  La verite ne couvre qu'une partie de la bande (golden/README.md",
                "  demande deux ou trois lignes representatives). Le CER compte aussi",
                "  le texte lu EN PLUS : il est rendu a titre indicatif et n'entre pas",
                "  dans le verdict. Les seuils de rappel, eux, sont inchanges.",
            ]
        lignes.append("")
        lignes.append("Seuils (context/ocr-quality-criteria.md § 3) :")
        for cle, val in SEUILS.items():
            actif = "" if (mode_verite == "complete" or not cle.startswith("cer")) else "   (non retenu)"
            lignes.append(f"  {cle:<18} {val}{actif}")
        lignes.append("")
        if diagnostic:
            lignes.append("Diagnostic par echantillon (configuration de production) :")
            lignes.append(f"  {'echantillon':<22} {'attendus':>9} {'trouves':>8} {'manquants':>10} {'ratio':>7}")
            for d in diagnostic:
                lignes.append(
                    f"  {d['echantillon']:<22} {d['attendus']:>9} {d['trouves']:>8} "
                    f"{d['manquants']:>10} {d['ratio_longueur']:>7}"
                )
                if d["longueurs_manquants"]:
                    lignes.append(
                        f"    longueurs des tokens manquants : {d['longueurs_manquants']}"
                    )
            lignes += [
                "",
                "  ratio = longueur lue / longueur de la verite. Un ratio eleve dit que",
                "  l'OCR a lu bien plus que ce que la verite decrit : attendu en verite",
                "  partielle, ce n'est pas un defaut de lecture.",
                "",
                "  Un token manquant tres long n'est pas un mot mais une phrase ou une",
                "  adresse collee. `rappel_mots` exige une correspondance exacte : une",
                "  seule faute de lecture le fait echouer en entier. Meme piege qu'en",
                "  R-S2b sur les URL. La metrique n'a PAS ete retouchee ici.",
                "",
            ]

    lignes.append(f"VERDICT : {verdict}")
    for m in manquements:
        lignes.append(f"  - {m}")
    lignes.append("")
    lignes.append("Reglage x3 en production :")
    lignes.append(f"  appliquer : {'OUI' if reco['appliquer'] else 'NON'}")
    lignes.append(f"  raison    : {reco.get('raison', '')}")
    lignes.append("")
    lignes.append("Vie privee : ce rapport ne contient aucun texte lu ni aucune verite")
    lignes.append("terrain. Seules les metriques et les noms d'echantillons y figurent.")
    return "\n".join(lignes)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Rejeu golden, non-regression OCR (R-S3a)")
    p.add_argument("--lang", default="fra+eng")
    p.add_argument("--report", action="store_true",
                   help="Ecrire preuves/golden-replay-<date>.txt")
    p.add_argument("--json", action="store_true", help="Sortie JSON des metriques")
    p.add_argument("--soft", action="store_true",
                   help="Toujours sortir en 0, meme si les seuils ne sont pas tenus")
    p.add_argument("--truth-mode", default=MODE_VERITE_DEFAUT, choices=("partial", "complete"),
                   help="complete = la verite couvre toute la bande, le CER devient un critere")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    echantillons, soucis = bo.charger_golden()
    try:
        import ocr_screen  # noqa: F401  (positionne tesseract_cmd)
        import pytesseract

        version = str(pytesseract.get_tesseract_version())
    except Exception as exc:  # noqa: BLE001
        print(f"Tesseract indisponible : {exc}")
        return 1

    print("=" * 72)
    print("REJEU GOLDEN - non-regression OCR (R-S3a)")
    print("=" * 72)
    print(f"Tesseract {version}   langues {args.lang}")
    print(f"Echantillons reels : {len(echantillons)}")
    for s in soucis:
        print(f"  avertissement : {s}")
    print()

    variantes = bo._variantes_tesseract(args.lang)
    voulues = {n: f for n, f in variantes.items() if n in (VARIANTE_PROD, VARIANTE_CANDIDATE)}
    mesures = bo.lancer(echantillons, voulues) if echantillons else []
    par_nom = {m["moteur"]: m for m in mesures}
    prod = par_nom.get(VARIANTE_PROD)
    cand = par_nom.get(VARIANTE_CANDIDATE)

    diagnostic = None
    if not echantillons:
        verdict = "RIEN A MESURER (golden vide)"
        manquements: list[str] = []
    else:
        tenus, manquements = evaluer(prod or {}, SEUILS, args.truth_mode)
        verdict = "SEUILS TENUS" if tenus else "REGRESSION : seuils non tenus"
        if VARIANTE_PROD in variantes:
            diagnostic = diagnostic_tokens(echantillons, variantes[VARIANTE_PROD])

    reco = recommander_x3(prod, cand, len(echantillons), args.truth_mode)
    texte = rapport(echantillons, mesures, verdict, manquements, reco,
                    args.truth_mode, diagnostic)
    print(texte)

    if args.json:
        print()
        print(json.dumps({"mesures": mesures, "verdict": verdict,
                          "manquements": manquements, "x3": reco},
                         indent=2, ensure_ascii=False))

    if args.report:
        PREUVES_DIR.mkdir(parents=True, exist_ok=True)
        cible = PREUVES_DIR / f"golden-replay-{time.strftime('%Y-%m-%d')}.txt"
        cible.write_text(texte + "\n", encoding="utf-8")
        print()
        print(f"Rapport ecrit : {cible}")

    if args.soft or not echantillons:
        return 0
    return 0 if not manquements else 1


if __name__ == "__main__":
    raise SystemExit(main())
