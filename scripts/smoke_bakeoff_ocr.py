"""Smoke bake-off OCR (R-S2b) : metriques, variantes, mode a vide.

Usage :
  python scripts/smoke_bakeoff_ocr.py

Sortie : exit 0 si tous les controles passent, 1 sinon.

N'installe rien, ne telecharge rien, ne lit aucun echantillon reel. Le
corpus synthetique est rendu a la volee. Le mode a vide (aucun echantillon
golden) est exerce explicitement : c'est le cas nominal tant que l'utilisateur n'a
pas rempli le dossier.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import bakeoff_ocr as bo  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok, detail))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def main() -> int:
    print("=" * 70)
    print("SMOKE bake-off OCR (R-S2b)")
    print("=" * 70)

    # --- metriques ----------------------------------------------------------- #
    print()
    print("Metriques")
    check("levenshtein identique = 0", bo.levenshtein("postuler", "postuler") == 0)
    check("levenshtein une substitution = 1", bo.levenshtein("postuler", "pastuler") == 1)
    check("levenshtein contre vide = longueur", bo.levenshtein("", "abcd") == 4)
    check("normalisation : casse et espaces", bo.normalise("  Postuler   ICI ") == "postuler ici")
    check(
        "normalisation : accents conserves",
        "é" in bo.normalise("Confidentialité"),
        "un moteur qui perd les accents doit en payer le prix",
    )
    check("CER parfait = 0", bo.cer("postuler a cette offre", "Postuler a cette offre") == 0.0)
    check("CER verite vide = 0", bo.cer("bruit", "") == 0.0)
    check(
        "CER penalise l'hallucination au-dela de 1",
        bo.cer("a" * 100, "abc") > 1.0,
        "produire plus de texte qu'il n'y en a est une faute",
    )
    check("rappel complet = 1", bo.rappel_mots("postuler a cette offre", "postuler offre") == 1.0)
    check("rappel nul = 0", bo.rappel_mots("rien du tout ici", "postuler") == 0.0)
    check(
        "rappel : mots courts ignores",
        bo.rappel_mots("xxxx", "a de la") == 1.0,
        "les mots de moins de 4 lettres ne comptent pas",
    )
    demi = bo.rappel_mots("postuler", "postuler exporter")
    check("rappel partiel = 0,5", abs(demi - 0.5) < 1e-9, f"{demi}")

    # --- variantes ----------------------------------------------------------- #
    print()
    print("Variantes et moteurs")
    variantes = bo._variantes_tesseract("fra+eng")
    check("plusieurs variantes Tesseract", len(variantes) >= 6, f"{len(variantes)} variantes")
    check("la baseline actuelle est presente", "tess-psm3-x1" in variantes,
          "psm 3 sans agrandissement = ce qu'utilise ocr_screen")
    check("une variante agrandie est presente", any("x2" in v or "x3" in v for v in variantes))
    check("toutes les variantes sont appelables", all(callable(f) for f in variantes.values()))

    externes, absents = bo._moteurs_externes()
    check(
        "detection des moteurs externes sans rien installer",
        isinstance(externes, dict) and isinstance(absents, list),
        f"{len(externes)} present(s), {len(absents)} absent(s)",
    )
    check(
        "chaque moteur absent indique sa commande d'installation",
        all("pip install" in a for a in absents),
        "aucun telechargement automatique",
    )

    # --- corpus synthetique --------------------------------------------------- #
    print()
    print("Corpus synthetique")
    ech, soucis = bo.charger_synthetiques()
    check("corpus rendu", len(ech) >= 3, f"{len(ech)} echantillon(s), {len(soucis)} souci(s)")
    check("toutes les images ont du contenu", all(e.frame.size > 0 for e in ech))
    check("toutes les verites sont renseignees", all(e.verite.strip() for e in ech))
    check("origine marquee synthetique", all(e.origine == "synthetique" for e in ech))
    tailles = {e.frame.shape[0] for e in ech}
    check("hauteurs realistes de bandeau", all(30 <= h <= 200 for h in tailles), str(sorted(tailles)))

    # --- mode a vide (cas nominal aujourd'hui) -------------------------------- #
    print()
    print("Mode a vide")
    with tempfile.TemporaryDirectory() as tmp:
        ancien = bo.SAMPLES_DIR
        bo.SAMPLES_DIR = Path(tmp) / "inexistant"
        vide, soucis_vide = bo.charger_golden()
        bo.SAMPLES_DIR = ancien
    check("dossier absent -> aucun echantillon, aucune exception", vide == [])
    check("dossier absent -> souci explique", len(soucis_vide) >= 1)

    resultats_vides = bo.lancer([], variantes)
    check("lancer sans echantillon -> aucun resultat exploitable",
          all(not r["ok"] for r in resultats_vides))
    texte = bo.rapport([], [], absents, [])
    check("rapport a vide le dit clairement", "AUCUN ECHANTILLON" in texte)
    check("rapport a vide oriente vers la suite", "golden/README.md" in texte)

    # --- mesure reelle sur un echantillon ------------------------------------- #
    print()
    print("Mesure sur le corpus synthetique")
    petit = [e for e in ech if "boutons" in e.name] or ech[:1]
    sous_ensemble = {k: variantes[k] for k in ("tess-psm3-x1", "tess-psm6-x2") if k in variantes}
    res = bo.lancer(petit, sous_ensemble)
    check("resultats produits", len(res) == len(sous_ensemble))
    ok_res = [r for r in res if r["ok"]]
    check("au moins une variante a lu quelque chose", len(ok_res) >= 1)
    for r in ok_res:
        check(
            f"{r['moteur']} : metriques completes",
            all(k in r for k in ("cer_moy", "cer_max", "rappel_moy", "ms_p50", "par_echantillon")),
        )
        check(f"{r['moteur']} : CER dans une plage plausible", 0.0 <= r["cer_moy"] <= 2.0,
              f"CER {r['cer_moy']}")
        check(f"{r['moteur']} : detail par echantillon renseigne",
              len(r["par_echantillon"]) == len(petit))
    check("resultats tries par CER croissant",
          [r["cer_moy"] for r in ok_res] == sorted(r["cer_moy"] for r in ok_res))

    # --- non-regression de l'hypothese d'agrandissement ----------------------- #
    print()
    print("Hypothese : agrandir avant de lire")
    complet = bo.lancer(ech, variantes)
    reussis = [r for r in complet if r["ok"]]
    base = next((r for r in reussis if r["moteur"] == "tess-psm3-x1"), None)
    best = reussis[0] if reussis else None
    if base and best:
        check(
            "la meilleure variante bat la configuration actuelle",
            best["cer_moy"] <= base["cer_moy"],
            f"{best['moteur']} CER {best['cer_moy']} vs actuel {base['cer_moy']}",
        )
        check(
            "la meilleure variante utilise un agrandissement",
            "x2" in best["moteur"] or "x3" in best["moteur"],
            best["moteur"],
        )

    # --- rapport : aucune fuite de texte -------------------------------------- #
    print()
    print("Vie privee du rapport")
    faux = bo.Echantillon(
        "faux-echantillon",
        np.full((60, 400, 3), 250, dtype=np.uint8),
        "SECRETABSOLU 12 rue des Tilleuls Jean Exemple",
        "golden",
    )
    rap = bo.rapport(bo.lancer([faux], sous_ensemble), [faux], absents, [])
    check("le rapport cite le nom de l'echantillon", "faux-echantillon" in rap)
    check(
        "le rapport ne contient AUCUNE verite terrain",
        "SECRETABSOLU" not in rap and "Tilleuls" not in rap and "Exemple" not in rap,
        "metriques seulement",
    )

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


if __name__ == "__main__":
    raise SystemExit(main())
