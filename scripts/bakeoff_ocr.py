"""Bake-off OCR (R-S2b) : choisir le moteur par la mesure, pas par habitude.

Constat qui declenche ce lot (R-S2a) : Tesseract fragmente le texte
d'interface. Fragments observes sur l'ecran de l'utilisateur : "D a >", "on louveau".
Les titres d'onglets passent, les petits libelles non.

Hypothese a tester avant de changer de moteur : le probleme n'est pas
Tesseract, c'est la TAILLE. Le texte d'interface fait 11 a 14 px de haut
quand Tesseract attend une hauteur de capitale d'environ 30 px. Agrandir
avant de lire coute presque rien et pourrait suffire.

Ce script compare donc d'abord des variantes de Tesseract entre elles
(segmentation, agrandissement, seuillage), sans aucune dependance nouvelle,
puis des moteurs externes s'ils sont installes.

Usage :
  python scripts/bakeoff_ocr.py --self-test        # corpus synthetique
  python scripts/bakeoff_ocr.py                    # golden/ocr-samples/
  python scripts/bakeoff_ocr.py --self-test --report

Echantillons reels : golden/ocr-samples/<nom>.png + <nom>.json
    {"expected_lines": ["..."], "notes": "..."}
Le dossier est gitignore : ces captures viennent de l'ecran de l'utilisateur.

VIE PRIVEE : le rapport ecrit ne contient QUE des metriques et des noms
d'echantillons. Jamais le texte lu, jamais la verite terrain d'un
echantillon reel. Seul le corpus synthetique, ecrit ici, est reproduit.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SAMPLES_DIR = ROOT / "golden" / "ocr-samples"
PREUVES_DIR = ROOT / "preuves"

# Corpus synthetique : du texte d'interface francais, aux tailles reellement
# rencontrees, rendu avec la police d'interface de Windows. Aucune donnee
# personnelle : ces chaines sont ecrites ici, pas lues sur un ecran.
CORPUS_SYNTH: list[dict[str, Any]] = [
    {
        "name": "synth-onglets-clair-12px",
        "size": 12,
        "fg": (32, 32, 32),
        "bg": (245, 245, 245),
        "lines": ["Mickey 17 (2025) - Lecture", "Boite de reception (3)", "Offres d'emploi"],
    },
    {
        "name": "synth-onglets-sombre-12px",
        "size": 12,
        "fg": (225, 225, 225),
        "bg": (32, 33, 36),
        "lines": ["Mickey 17 (2025) - Lecture", "Boite de reception (3)", "Offres d'emploi"],
    },
    {
        "name": "synth-boutons-14px",
        "size": 14,
        "fg": (255, 255, 255),
        "bg": (26, 115, 232),
        "lines": ["Postuler a cette offre", "Exporter les donnees", "Confidentialite"],
    },
    {
        "name": "synth-libelles-11px",
        "size": 11,
        "fg": (96, 96, 96),
        "bg": (250, 250, 250),
        "lines": ["Derniere mise a jour il y a 3 jours", "Contrat a duree indeterminee"],
    },
    {
        "name": "synth-barre-adresse-13px",
        "size": 13,
        "fg": (48, 48, 48),
        "bg": (255, 255, 255),
        "lines": ["candidat.travailpublic.fr/offres/recherche", "chatbot.ai/settings/data-privacy"],
    },
]


# --------------------------------------------------------------------------- #
# Metriques
# --------------------------------------------------------------------------- #

def normalise(texte: str) -> str:
    """Minuscules, espaces normalises. Les accents sont conserves : le corpus
    est francais et un moteur qui les perd doit en payer le prix."""
    return " ".join(texte.lower().split())


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    precedente = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        courante = [i]
        for j, cb in enumerate(b, 1):
            courante.append(
                min(precedente[j] + 1, courante[j - 1] + 1, precedente[j - 1] + (ca != cb))
            )
        precedente = courante
    return precedente[-1]


def cer(predit: str, verite: str) -> float:
    """Taux d'erreur caractere. 0 = parfait, 1 = tout faux. Peut depasser 1
    si le moteur hallucine plus de texte qu'il n'y en a."""
    v = normalise(verite)
    if not v:
        return 0.0
    return levenshtein(normalise(predit), v) / len(v)


def rappel_mots(predit: str, verite: str, longueur_min: int = 4) -> float:
    """Fraction des mots significatifs de la verite retrouves dans la sortie.

    Plus proche de l'usage que le CER : ce qui compte pour un agent, c'est de
    retrouver "Postuler", pas de restituer la ponctuation.
    """
    p = normalise(predit)
    mots = [m for m in normalise(verite).split() if len(m) >= longueur_min]
    if not mots:
        return 1.0
    return sum(1 for m in mots if m in p) / len(mots)


# --------------------------------------------------------------------------- #
# Variantes Tesseract (aucune dependance nouvelle)
# --------------------------------------------------------------------------- #

def _tesseract(frame: np.ndarray, psm: int, lang: str, scale: float, otsu: bool) -> str:
    import pytesseract

    img = frame
    if scale != 1.0:
        img = cv2.resize(
            img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC
        )
    if otsu:
        gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, img = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return str(pytesseract.image_to_string(rgb, lang=lang, config=f"--psm {psm}"))


def _variantes_tesseract(lang: str) -> dict[str, Callable[[np.ndarray], str]]:
    specs = [
        # (nom, psm, echelle, otsu)  -- psm 3 = segmentation auto, celle
        # qu'utilise ocr_screen aujourd'hui : c'est la baseline a battre.
        ("tess-psm3-x1", 3, 1.0, False),
        ("tess-psm6-x1", 6, 1.0, False),
        ("tess-psm11-x1", 11, 1.0, False),
        ("tess-psm6-x2", 6, 2.0, False),
        ("tess-psm6-x3", 6, 3.0, False),
        ("tess-psm11-x2", 11, 2.0, False),
        ("tess-psm6-x2-otsu", 6, 2.0, True),
        ("tess-psm6-x3-otsu", 6, 3.0, True),
    ]
    return {
        nom: (lambda f, p=psm, s=sc, o=ot: _tesseract(f, p, lang, s, o))
        for nom, psm, sc, ot in specs
    }


# --------------------------------------------------------------------------- #
# Moteurs externes (uniquement s'ils sont deja installes)
# --------------------------------------------------------------------------- #

_EXTERNES = [
    ("rapidocr", "rapidocr_onnxruntime", "pip install rapidocr_onnxruntime"),
    ("paddleocr", "paddleocr", "pip install paddleocr"),
    ("easyocr", "easyocr", "pip install easyocr"),
]


def _moteurs_externes() -> tuple[dict[str, Callable[[np.ndarray], str]], list[str]]:
    """Moteurs presents. Rien n'est installe ni telecharge par ce script."""
    dispo: dict[str, Callable[[np.ndarray], str]] = {}
    absents: list[str] = []
    for nom, module, commande in _EXTERNES:
        if importlib.util.find_spec(module) is None:
            absents.append(f"{nom} (absent : {commande})")
            continue
        if nom == "rapidocr":
            def _rapid(frame: np.ndarray, _m=module) -> str:
                from rapidocr_onnxruntime import RapidOCR

                if "_inst" not in _rapid.__dict__:
                    _rapid._inst = RapidOCR()  # type: ignore[attr-defined]
                res, _ = _rapid._inst(frame)  # type: ignore[attr-defined]
                return " ".join(x[1] for x in (res or []))

            dispo[nom] = _rapid
        elif nom == "paddleocr":
            def _paddle(frame: np.ndarray) -> str:
                from paddleocr import PaddleOCR

                if "_inst" not in _paddle.__dict__:
                    _paddle._inst = PaddleOCR(use_angle_cls=False, lang="fr", show_log=False)  # type: ignore[attr-defined]
                res = _paddle._inst.ocr(frame, cls=False)  # type: ignore[attr-defined]
                mots = []
                for bloc in res or []:
                    for ligne in bloc or []:
                        mots.append(ligne[1][0])
                return " ".join(mots)

            dispo[nom] = _paddle
        elif nom == "easyocr":
            def _easy(frame: np.ndarray) -> str:
                import easyocr

                if "_inst" not in _easy.__dict__:
                    _easy._inst = easyocr.Reader(["fr", "en"], verbose=False)  # type: ignore[attr-defined]
                return " ".join(_easy._inst.readtext(frame, detail=0))  # type: ignore[attr-defined]

            dispo[nom] = _easy
    return dispo, absents


# --------------------------------------------------------------------------- #
# Echantillons
# --------------------------------------------------------------------------- #

@dataclass
class Echantillon:
    name: str
    frame: np.ndarray
    verite: str
    origine: str  # "synthetique" ou "golden"


def _rendre_synthetique(spec: dict[str, Any]) -> np.ndarray | None:
    """Rend du texte d'interface avec la police systeme de Windows.

    On ne dessine pas avec OpenCV : sa police vectorielle ne ressemble pas a
    du texte d'interface. Segoe UI rendu par PIL, aux tailles reelles, est
    bien plus representatif de ce que Tesseract rencontre a l'ecran.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return None
    police = None
    # Windows d'abord (Segoe, la police de l'interface de l'utilisateur).
    # Linux : DejaVu ou Liberation, presents sur Ubuntu.
    for chemin in (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\tahoma.ttf",
                   r"C:\Windows\Fonts\arial.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                   "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if Path(chemin).exists():
            try:
                police = ImageFont.truetype(chemin, spec["size"])
                break
            except Exception:  # noqa: BLE001
                continue
    if police is None:
        return None

    marge, interligne = 12, int(spec["size"] * 1.9)
    hauteur = marge * 2 + interligne * len(spec["lines"])
    largeur = 900
    img = Image.new("RGB", (largeur, hauteur), tuple(spec["bg"]))
    dessin = ImageDraw.Draw(img)
    for i, ligne in enumerate(spec["lines"]):
        dessin.text((marge, marge + i * interligne), ligne, font=police, fill=tuple(spec["fg"]))
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def charger_synthetiques() -> tuple[list[Echantillon], list[str]]:
    out, soucis = [], []
    for spec in CORPUS_SYNTH:
        frame = _rendre_synthetique(spec)
        if frame is None:
            soucis.append(f"{spec['name']} : rendu impossible (PIL ou police absente)")
            continue
        out.append(
            Echantillon(spec["name"], frame, " ".join(spec["lines"]), "synthetique")
        )
    return out, soucis


def charger_golden() -> tuple[list[Echantillon], list[str]]:
    out, soucis = [], []
    if not SAMPLES_DIR.is_dir():
        return out, [f"dossier absent : {SAMPLES_DIR}"]
    for png in sorted(SAMPLES_DIR.glob("*.png")):
        meta = png.with_suffix(".json")
        if not meta.exists():
            soucis.append(f"{png.name} : pas de {meta.name}, echantillon ignore")
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            lignes = data.get("expected_lines") or []
            if not lignes:
                soucis.append(f"{png.name} : 'expected_lines' vide")
                continue
            frame = cv2.imread(str(png))
            if frame is None:
                soucis.append(f"{png.name} : image illisible")
                continue
            out.append(Echantillon(png.stem, frame, " ".join(lignes), "golden"))
        except Exception as exc:  # noqa: BLE001
            soucis.append(f"{png.name} : {exc}")
    return out, soucis


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

def lancer(
    echantillons: list[Echantillon],
    moteurs: dict[str, Callable[[np.ndarray], str]],
) -> list[dict[str, Any]]:
    resultats = []
    for nom, fn in moteurs.items():
        cers, rappels, durees, echecs = [], [], [], 0
        par_ech: dict[str, dict[str, float]] = {}
        for ech in echantillons:
            t0 = time.perf_counter()
            try:
                sortie = fn(ech.frame)
            except Exception:  # noqa: BLE001
                echecs += 1
                durees.append((time.perf_counter() - t0) * 1000.0)
                continue
            duree = (time.perf_counter() - t0) * 1000.0
            durees.append(duree)
            c, r = cer(sortie, ech.verite), rappel_mots(sortie, ech.verite)
            cers.append(c)
            rappels.append(r)
            par_ech[ech.name] = {"cer": round(c, 4), "rappel": round(r, 3),
                                 "ms": round(duree, 1)}
        if not cers:
            resultats.append({"moteur": nom, "ok": False, "echecs": echecs})
            continue
        durees.sort()
        resultats.append(
            {
                "moteur": nom,
                "ok": True,
                "n": len(cers),
                "echecs": echecs,
                "cer_moy": round(sum(cers) / len(cers), 4),
                "cer_max": round(max(cers), 4),
                "rappel_moy": round(sum(rappels) / len(rappels), 4),
                "rappel_min": round(min(rappels), 4),
                "ms_p50": round(durees[len(durees) // 2], 1),
                "ms_total": round(sum(durees), 1),
                "par_echantillon": par_ech,
            }
        )
    resultats.sort(key=lambda r: (not r["ok"], r.get("cer_moy", 9)))
    return resultats


def rapport(
    resultats: list[dict[str, Any]],
    echantillons: list[Echantillon],
    absents: list[str],
    soucis: list[str],
) -> str:
    lignes = []
    lignes.append("Bake-off OCR - vision-public R-S2b")
    lignes.append(f"Date : {time.strftime('%Y-%m-%d %H:%M')}")
    lignes.append("")
    synth = sum(1 for e in echantillons if e.origine == "synthetique")
    golden = sum(1 for e in echantillons if e.origine == "golden")
    lignes.append(f"Echantillons : {len(echantillons)} ({synth} synthetiques, {golden} golden)")
    for e in echantillons:
        lignes.append(f"  - {e.name} [{e.origine}] {e.frame.shape[1]}x{e.frame.shape[0]}")
    lignes.append("")
    if not echantillons:
        lignes.append("AUCUN ECHANTILLON : rien a mesurer.")
        lignes.append("Ajouter des PNG + JSON dans golden/ocr-samples/ (voir golden/README.md)")
        lignes.append("ou lancer avec --self-test pour le corpus synthetique.")
        return "\n".join(lignes)

    lignes.append(f"{'moteur':<22} {'CER moy':>8} {'CER max':>8} {'rappel':>8} {'rap.min':>8} {'ms p50':>8}")
    lignes.append("-" * 70)
    for r in resultats:
        if not r["ok"]:
            lignes.append(f"{r['moteur']:<22} {'ECHEC':>8}  ({r['echecs']} erreur(s))")
            continue
        lignes.append(
            f"{r['moteur']:<22} {r['cer_moy']:>8.3f} {r['cer_max']:>8.3f} "
            f"{r['rappel_moy']:>8.3f} {r['rappel_min']:>8.3f} {r['ms_p50']:>8.1f}"
        )
    lignes.append("")

    # Detail par echantillon : la moyenne cache ou ca casse reellement.
    reussis = [r for r in resultats if r["ok"]]
    baseline = next((r for r in reussis if r["moteur"] == "tess-psm3-x1"), None)
    meilleur = reussis[0] if reussis else None
    if meilleur and baseline and meilleur["moteur"] != baseline["moteur"]:
        lignes.append(
            f"Detail par echantillon : {baseline['moteur']} (utilise aujourd'hui) "
            f"vs {meilleur['moteur']} (meilleur)"
        )
        lignes.append(
            f"{'echantillon':<30} {'CER act.':>9} {'rap.act.':>9} {'CER best':>9} {'rap.best':>9}"
        )
        lignes.append("-" * 70)
        for e in echantillons:
            a_ = baseline["par_echantillon"].get(e.name)
            b_ = meilleur["par_echantillon"].get(e.name)
            if not a_ or not b_:
                continue
            lignes.append(
                f"{e.name:<30} {a_['cer']:>9.3f} {a_['rappel']:>9.2f} "
                f"{b_['cer']:>9.3f} {b_['rappel']:>9.2f}"
            )
        lignes.append("")

    lignes.append("CER : taux d'erreur caractere, 0 = parfait. Peut depasser 1 si le")
    lignes.append("      moteur produit plus de texte qu'il n'y en a.")
    lignes.append("rappel : fraction des mots de 4 lettres ou plus retrouves.")
    lignes.append("         Attention : une URL est UN seul mot. Une erreur d'un")
    lignes.append("         caractere y fait tomber le rappel a 0 alors que le CER")
    lignes.append("         reste bas. Ne pas juger la barre d'adresse au rappel :")
    lignes.append("         une URL se lit par UIA (0,21 ms, exacte), pas par OCR.")
    lignes.append("")
    if absents:
        lignes.append("Moteurs externes non testes (non installes, rien n'a ete telecharge) :")
        for a in absents:
            lignes.append(f"  - {a}")
        lignes.append("")
    if soucis:
        lignes.append("Avertissements :")
        for s in soucis:
            lignes.append(f"  - {s}")
        lignes.append("")
    lignes.append("Vie privee : ce rapport ne contient aucun texte lu ni aucune verite")
    lignes.append("terrain d'echantillon reel. Seules les metriques sont conservees.")
    return "\n".join(lignes)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bake-off OCR (R-S2b)")
    p.add_argument("--self-test", action="store_true",
                   help="Ajouter le corpus synthetique integre")
    p.add_argument("--only-synth", action="store_true",
                   help="Corpus synthetique seul, ignorer golden/")
    p.add_argument("--lang", default="fra+eng")
    p.add_argument("--report", action="store_true",
                   help="Ecrire preuves/ocr-bakeoff-<date>.txt")
    p.add_argument("--json", action="store_true", help="Sortie JSON des metriques")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    echantillons: list[Echantillon] = []
    soucis: list[str] = []
    if not args.only_synth:
        g, s = charger_golden()
        echantillons += g
        soucis += s
    if args.self_test or args.only_synth or not echantillons:
        sy, s2 = charger_synthetiques()
        if args.self_test or args.only_synth:
            echantillons += sy
            soucis += s2
        elif not echantillons:
            soucis.append(
                "aucun echantillon golden : relancer avec --self-test pour mesurer "
                "sur le corpus synthetique"
            )

    try:
        import ocr_screen  # noqa: F401  (positionne tesseract_cmd)
        import pytesseract

        version = str(pytesseract.get_tesseract_version())
    except Exception as exc:  # noqa: BLE001
        print(f"Tesseract indisponible : {exc}")
        return 1

    moteurs = _variantes_tesseract(args.lang)
    externes, absents = _moteurs_externes()
    moteurs.update(externes)

    print("=" * 70)
    print("BAKE-OFF OCR (R-S2b)")
    print("=" * 70)
    print(f"Tesseract {version}   langues {args.lang}")
    print(f"Variantes testees : {len(moteurs)}")
    print(f"Echantillons      : {len(echantillons)}")
    print()

    resultats = lancer(echantillons, moteurs) if echantillons else []
    texte = rapport(resultats, echantillons, absents, soucis)
    print(texte)

    if args.json:
        print()
        print(json.dumps({"resultats": resultats, "externes_absents": absents}, indent=2))

    if args.report:
        PREUVES_DIR.mkdir(parents=True, exist_ok=True)
        cible = PREUVES_DIR / f"ocr-bakeoff-{time.strftime('%Y-%m-%d')}.txt"
        cible.write_text(texte + "\n", encoding="utf-8")
        print()
        print(f"Rapport ecrit : {cible}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
