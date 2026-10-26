#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OCR d'une bande HUD : crop deja fait, upscale, binarisation, chiffres.

N'installe rien. N'ouvre pas de fenetre. N'ecrit pas d'image.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ocr_screen import ocr_boxes  # noqa: E402


def preparer(bgr: np.ndarray, echelle: int = 3) -> np.ndarray:
    """Niveau de gris, agrandi, Otsu, texte sombre sur fond clair."""
    if bgr is None or bgr.size == 0:
        return np.zeros((8, 8), dtype=np.uint8)
    gris = bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if echelle > 1:
        gris = cv2.resize(gris, None, fx=echelle, fy=echelle, interpolation=cv2.INTER_CUBIC)
    gris = cv2.GaussianBlur(gris, (3, 3), 0)
    _, th = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(th.mean()) < 127:
        th = 255 - th
    return th


def _mots_tesseract(image: np.ndarray, chiffres: bool) -> list[str]:
    import pytesseract

    config = "--psm 7"
    if chiffres:
        config += " -c tessedit_char_whitelist=0123456789"
    rgb = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    data = pytesseract.image_to_data(rgb, lang="eng" if chiffres else "fra+eng", config=config, output_type=pytesseract.Output.DICT)
    mots = []
    for text, conf in zip(data["text"], data["conf"]):
        try:
            c = int(float(conf))
        except (TypeError, ValueError):
            c = -1
        t = (text or "").strip()
        if c >= 40 and t:
            mots.append(t)
    return mots


def lire_brut(bgr: np.ndarray) -> list[str]:
    """OCR tel quel, sans upscale. C'est le « avant »."""
    try:
        boxes = ocr_boxes(bgr, lang="fra+eng", min_conf=40)
    except Exception:  # noqa: BLE001
        return []
    return [t for *_, t, _c in boxes if (t or "").strip()]


def lire_hud(bgr: np.ndarray, *, chiffres: bool = False, echelle: int = 3) -> dict[str, Any]:
    image = preparer(bgr, echelle=echelle)
    try:
        mots = _mots_tesseract(image, chiffres=chiffres)
        err = ""
    except Exception as exc:  # noqa: BLE001
        mots = []
        err = type(exc).__name__
    if chiffres and not any(ch.isdigit() for m in mots for ch in m) and echelle < 4:
        return lire_hud(bgr, chiffres=True, echelle=4) | {"repli_x4": True}
    nombres = []
    for mot in mots:
        digits = "".join(ch for ch in mot if ch.isdigit())
        if digits:
            nombres.append(digits)
    return {
        "mots": mots,
        "n_mots": len(mots),
        "nombres": nombres,
        "echelle": echelle,
        "chiffres": chiffres,
        "erreur": err,
    }


def lire_upscale(bgr: np.ndarray, echelle: int = 3, chiffres: bool = False) -> dict[str, Any]:
    """Agrandi seulement, sans binariser. Souvent moins destructeur qu'Otsu sur un HUD."""
    if bgr is None or bgr.size == 0:
        return {"mots": [], "n_mots": 0, "nombres": [], "echelle": echelle, "erreur": "vide"}
    gris = bgr if bgr.ndim == 2 else cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if echelle > 1:
        gris = cv2.resize(gris, None, fx=echelle, fy=echelle, interpolation=cv2.INTER_CUBIC)
    try:
        mots = _mots_tesseract(gris, chiffres=chiffres)
        err = ""
    except Exception as exc:  # noqa: BLE001
        mots = []
        err = type(exc).__name__
    nombres = ["".join(ch for ch in mot if ch.isdigit()) for mot in mots]
    nombres = [n for n in nombres if n]
    return {"mots": mots, "n_mots": len(mots), "nombres": nombres, "echelle": echelle, "erreur": err}


def comparer_hud(bgr: np.ndarray) -> dict[str, Any]:
    avant = lire_brut(bgr)
    up = lire_upscale(bgr, 3, chiffres=False)
    otsu = lire_hud(bgr, chiffres=False, echelle=3)
    jauge = lire_hud(bgr, chiffres=True, echelle=3)
    jauge_up = lire_upscale(bgr, 4, chiffres=True)
    return {
        "avant_n": len(avant),
        "avant_mots": avant[:12],
        "upscale_n": up["n_mots"],
        "upscale_mots": up["mots"][:12],
        "apres_n": otsu["n_mots"],
        "apres_mots": otsu["mots"][:12],
        "nombres": jauge["nombres"][:8],
        "nombres_upscale": jauge_up["nombres"][:8],
        "echelle": jauge.get("echelle"),
    }


def moteur_alternatif() -> str:
    """Nom d'un OCR local deja installe, ou vide. N'installe rien, ne telecharge rien."""
    for nom in ("rapidocr_onnxruntime", "rapidocr"):
        try:
            __import__(nom)
            return nom
        except ImportError:
            continue
    try:
        import easyocr  # noqa: F401
        return "easyocr-present-non-lance"
    except ImportError:
        return ""


# RapidOCR (installe le 26/10, accord de l'auteur : `rapidocr_onnxruntime` 1.4.4, modeles
# PP-OCRv4 livres DANS le paquet, aucun telechargement au lancement).
# Mesures du 26/10 sur une capture de jeu, processeur a 99 % (jeu ouvert) :
#   - reglage par defaut : la detection agrandit chaque petite zone a 736 px de
#     cote minimum, 1,4 a 1,8 s par zone ; fenetre entiere 14 s ;
#   - detection bornee (det_limit_type="max") : 70 a 270 ms par zone ;
#   - lecture seule d'une ligne deja decoupee : 50 a 130 ms ;
#   - l'image binarisee (Otsu) abime ce modele : on lui donne la couleur agrandie ;
#   - recreer le moteur a chaque appel coutait ~420 ms : il est garde.
_RAPID: Any = None


def _moteur_rapid() -> Any:
    global _RAPID
    if _RAPID is None:
        nom = moteur_alternatif()
        if nom == "rapidocr_onnxruntime":
            from rapidocr_onnxruntime import RapidOCR
        elif nom == "rapidocr":
            from rapidocr import RapidOCR
        else:
            raise ImportError("aucun RapidOCR installe")
        _RAPID = RapidOCR(det_limit_type="max", det_limit_side_len=960)
    return _RAPID


def lire_alternatif(bgr: np.ndarray, echelle: int = 3) -> dict[str, Any]:
    """RapidOCR (detection bornee) sur une zone. EasyOCR n'est pas lance (il telecharge)."""
    nom = moteur_alternatif()
    if nom == "easyocr-present-non-lance":
        return {"moteur": nom, "mots": [], "raison": "easyocr present, non lance pour ne pas telecharger"}
    if not nom:
        return {"moteur": "", "mots": [], "raison": "aucun OCR alternatif installe"}
    if bgr is None or bgr.size == 0:
        return {"moteur": nom, "mots": [], "raison": "vide"}
    try:
        image = cv2.resize(bgr, None, fx=echelle, fy=echelle, interpolation=cv2.INTER_CUBIC) if echelle > 1 else bgr
        resultat, _ = _moteur_rapid()(image)
        mots = [str(l[1]).strip() for l in (resultat or []) if len(l) >= 2 and str(l[1]).strip()]
        return {"moteur": nom, "mots": mots[:12], "n": len(mots), "raison": ""}
    except Exception as exc:  # noqa: BLE001
        return {"moteur": nom, "mots": [], "raison": type(exc).__name__}


def lire_ligne_rapid(bgr: np.ndarray, echelle: int = 3) -> dict[str, Any]:
    """Une SEULE ligne deja decoupee (jauge, coordonnees, horloge) : reconnaissance
    sans detection, la voie la plus rapide. Rend aussi le score du modele."""
    nom = moteur_alternatif()
    if not nom or nom == "easyocr-present-non-lance":
        return {"moteur": nom, "texte": "", "score": 0.0, "raison": "aucun RapidOCR installe"}
    if bgr is None or bgr.size == 0:
        return {"moteur": nom, "texte": "", "score": 0.0, "raison": "vide"}
    try:
        image = cv2.resize(bgr, None, fx=echelle, fy=echelle, interpolation=cv2.INTER_CUBIC) if echelle > 1 else bgr
        resultat, _ = _moteur_rapid()(image, use_det=False, use_cls=False, use_rec=True)
        texte, score = (str(resultat[0][0]), float(resultat[0][1])) if resultat else ("", 0.0)
        return {"moteur": nom, "texte": texte.strip(), "score": round(score, 3), "raison": ""}
    except Exception as exc:  # noqa: BLE001
        return {"moteur": nom, "texte": "", "score": 0.0, "raison": type(exc).__name__}


def jauge(texte: str) -> dict[str, Any]:
    """« 147247/147247 » lu par RapidOCR rend souvent la barre en « 1 »
    (« 1472471147247 ») ou la perd (« 147247147247 »). On ne retrouve la jauge que
    si les deux moities sont EGALES (jauge pleine) ; sinon on le dit, sans deviner."""
    if "/" in texte:
        a, _, b = texte.partition("/")
        a, b = "".join(c for c in a if c.isdigit()), "".join(c for c in b if c.isdigit())
        return {"actuel": a, "max": b, "sur": bool(a and b)}
    d = "".join(c for c in texte if c.isdigit())
    n = len(d)
    if n >= 3 and n % 2 == 1 and d[n // 2] == "1" and d[: n // 2] == d[n // 2 + 1:]:
        return {"actuel": d[: n // 2], "max": d[n // 2 + 1:], "sur": True, "note": "barre lue comme 1"}
    if n >= 2 and n % 2 == 0 and d[: n // 2] == d[n // 2:]:
        return {"actuel": d[: n // 2], "max": d[n // 2:], "sur": True, "note": "barre perdue"}
    return {"actuel": None, "max": None, "sur": False, "brut": d, "note": "separateur incertain"}


def trouver_barre(bgr: np.ndarray) -> int | None:
    """Colonne du « / » vu sur l'image, quand l'OCR le lit comme un 1 ou l'oublie.

    Un 1 est un trait vertical. Une barre de jauge penche : l'encre du haut
    est decalee par rapport a l'encre du bas.
    """
    if bgr is None or bgr.size == 0:
        return None
    gris = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    h, w = gris.shape[:2]
    if h < 8 or w < 20:
        return None
    _, bw = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float((bw > 0).mean()) > 0.65:
        bw = cv2.bitwise_not(bw)
    ink = bw > 0
    # Un « 1 » occupe toute la hauteur. Une barre « / » n'occupe, sur chaque
    # colonne, qu'une bande courte, et cette bande monte quand x avance.
    centres: list[tuple[int, float]] = []
    for x in range(w):
        ys = np.flatnonzero(ink[:, x])
        if len(ys) < 2 or int(ys[-1]) - int(ys[0]) > h * 0.55:
            continue
        centres.append((x, float(ys.mean())))
    meilleure: list[tuple[int, float]] | None = None
    course: list[tuple[int, float]] = []
    for x, y in centres:
        if course and x <= course[-1][0] + 2 and y < course[-1][1] - 0.15:
            course.append((x, y))
        else:
            if len(course) >= 3 and course[0][1] - course[-1][1] > h * 0.28:
                if meilleure is None or len(course) > len(meilleure):
                    meilleure = course
            course = [(x, y)]
    if len(course) >= 3 and course[0][1] - course[-1][1] > h * 0.28:
        if meilleure is None or len(course) > len(meilleure):
            meilleure = course
    if not meilleure:
        return None
    return int(meilleure[len(meilleure) // 2][0])


def lire_jauge_image(bgr: np.ndarray) -> dict[str, Any]:
    """Jauge non pleine : coupe l'image sur la barre vue, puis lit chaque moitie."""
    texte = (lire_ligne_rapid(bgr).get("texte") or "") if bgr is not None else ""
    lu = jauge(texte)
    if lu.get("sur") and "/" in texte:
        return {**lu, "voie": "texte", "barre_x": None}
    x = trouver_barre(bgr)
    if not x or x < 4 or bgr.shape[1] - x < 4:
        return {**lu, "voie": "texte", "barre_x": x}
    gauche = "".join(c for c in (lire_ligne_rapid(bgr[:, :x]).get("texte") or "") if c.isdigit())
    droite = "".join(c for c in (lire_ligne_rapid(bgr[:, x:]).get("texte") or "") if c.isdigit())
    if gauche and droite and gauche != droite:
        return {"actuel": gauche, "max": droite, "sur": True, "note": "barre vue", "barre_x": x, "voie": "image"}
    if gauche and droite and gauche == droite:
        return {"actuel": gauche, "max": droite, "sur": True, "note": "barre vue, moities egales", "barre_x": x, "voie": "image"}
    return {**lu, "voie": "texte", "barre_x": x}
