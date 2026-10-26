#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Le profil chiffres lit 30049 sur une jauge dessinee. Pas de fenetre."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ocr_hud import (  # noqa: E402
    comparer_hud, jauge, lire_alternatif, lire_hud, lire_jauge_image, lire_ligne_rapid,
    moteur_alternatif, trouver_barre,
)


def _ok(cond: bool, msg: str) -> None:
    if not cond:
        raise SystemExit("KO " + msg)
    print("OK", msg)


def _dessiner(texte: str) -> np.ndarray:
    """Chiffres clairs sur fond noir, avec la police livree DANS Pillow (epinglee).

    Pas cv2.putText : OpenCV 4.12 dessine la police Hershey (un « 3 » a haut plat,
    lu « 5 » par Tesseract) et OpenCV 5.0 une autre police. Le smoke passait ou
    cassait selon la version d'OpenCV, pas selon le code (F14, mesure audit 26/10 :
    lot temoin de 40 nombres, graine 2610, memes lectures sous 4.12 et 5.0 avec Pillow).
    """
    im = Image.new("RGB", (220, 48), (0, 0, 0))
    ImageDraw.Draw(im).text((8, 6), texte, font=ImageFont.load_default(size=34), fill=(240, 240, 240))
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def main() -> int:
    img = _dessiner("30049")
    lu = lire_hud(img, chiffres=True, echelle=3)
    _ok("30049" in lu["nombres"], "chiffres HUD " + str(lu["nombres"]))
    cmp = comparer_hud(img)
    _ok(cmp["apres_n"] >= 1 or cmp["nombres"], "apres prepare au moins un token " + str(cmp))
    alt = moteur_alternatif()
    _ok(isinstance(alt, str), "moteur alternatif = " + (alt or "aucun"))
    # Jauge : la barre lue « 1 » ou perdue (mesure du 26/10 sur un jeu), pur, sans OCR
    _ok(jauge("1472471147247") == {"actuel": "147247", "max": "147247", "sur": True, "note": "barre lue comme 1"},
        "jauge barre lue comme 1")
    _ok(jauge("147247147247")["actuel"] == "147247", "jauge barre perdue")
    _ok(jauge("90000/147247") == {"actuel": "90000", "max": "147247", "sur": True}, "jauge avec barre")
    _ok(jauge("900001147247")["sur"] is False, "jauge non pleine sans barre : on ne devine pas")
    # Barre dessinee : on la trouve par sa pente, pas en devinant un 1 au milieu.
    ligne = np.zeros((42, 320, 3), np.uint8)
    cv2.putText(ligne, "90000/147247", (6, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (245, 245, 245), 2, cv2.LINE_AA)
    barre = trouver_barre(ligne)
    _ok(barre is not None and 40 < barre < 280, f"barre / vue a x={barre}")
    if alt in {"rapidocr_onnxruntime", "rapidocr"}:
        vue = lire_jauge_image(ligne)
        _ok(vue.get("sur") is True and vue.get("actuel") == "90000" and vue.get("max") == "147247",
            "jauge non pleine lue par la barre " + str(vue))
    if alt in {"rapidocr_onnxruntime", "rapidocr"}:
        # Voie RapidOCR jouee seulement si le paquet est la (fait du poste affiche, pas exige).
        ligne = lire_ligne_rapid(img)
        _ok("30049" in ligne["texte"].replace(" ", ""), "RapidOCR ligne lit 30049 " + str(ligne))
        zone = lire_alternatif(img)
        _ok(any("30049" in m.replace(" ", "") for m in zone["mots"]), "RapidOCR zone lit 30049 " + str(zone))
    else:
        print("INFO RapidOCR absent : voie non jouee (pas un succes)")
    print("smoke_ocr_hud OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
