"""Pont grille -> oeil (V1.1 · amorce 1.7 du plan) : lire une case ou une plage.

Une case (``1:B7``), une sous-case (``1:B7.5``) ou une plage (``1:F8-1:K9``)
devient un rectangle de pixels. Le rectangle est capture **une seule fois**,
puis decrit :

  - couleurs nommees (proportions), luminance, contraste  (mesure)
  - formes sommaires : rectangles, triangles, ronds, contours, densite (mesure)
  - texte, par OCR                                        (opt-in, mesure)
  - description par un modele de vision LOCAL             (opt-in, a verifier)

Pour une plage, chaque case est decrite a partir de la MEME image : un ecran
vivant bouge entre deux captures (lecon R-S18).

Garde-fous :
  - lecture seule, aucune actuation, aucune image ecrite sur disque ;
  - une zone noire ne se devine pas : OCR et description s'abstiennent ;
  - une case qui n'existe pas est refusee avec un message clair (``1:L16``) ;
  - aucun telechargement : Ollama doit deja avoir le modele, Moondream se
    charge en local seulement.

Les ecrans se designent par leur index (``screen_context.list_monitors`` :
primaire d'abord, puis gauche), comme l'overlay ``scripts/grille.py``. Jamais
par le nom Windows ``\\\\.\\DISPLAYn``.
"""

from __future__ import annotations

import base64
import json
import math
import os
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from screen_grid import (
    GridSpec,
    _cell_bounds,
    _col_letters,
    _sub_bounds,
    cell_at_pixel,
    grid_for_monitor,
    parse_label,
)

__all__ = [
    "Cible",
    "parse_cible",
    "nommer_couleurs",
    "formes_sommaires",
    "lire",
    "decrire_ollama",
    "rapport_mesure",
    "MODELE_DEFAUT",
]

OLLAMA = os.environ.get("VISION_OLLAMA_URL", "http://127.0.0.1:11434")
# Garde-fou (18/10) : l'image de l'ecran ne quitte pas ce PC. Une
# VISION_OLLAMA_URL vers une autre machine est REFUSEE, sauf accord explicite
# VISION_OLLAMA_DISTANT=1 (variable posee a la main, jamais par defaut).
HOTES_LOCAUX = {"127.0.0.1", "localhost", "::1"}
# Mesure R-S15 (09/09) sur image : seul modele du parc a lire 3/3 jetons et a
# voir la zone noire, en ~4 s. Deja installe : aucun pull.
MODELE_DEFAUT = "qwen3.5:9b-gpu"
# Mesure 23/10 : le tag Ollama 1B ne lit pas une image (carre rouge -> texte
# hors sujet). Ce n'est pas Moondream3 (cache transformers). Refuse sans HTTP,
# sauf VISION_OLLAMA_MODELE_NON_FIABLE=1.
MODELES_NON_FIABLES = {
    "moondream:latest": (
        "moondream:latest (Ollama, 1B) est non fiable. "
        "Le second regard du projet est Moondream3 local "
        "(moondream/moondream3-preview). "
        "VISION_OLLAMA_MODELE_NON_FIABLE=1 pour forcer l'appel."
    ),
}
TIMEOUT_DESCRIPTION_S = 180
COTE_MAX_DESCRIPTION = 1024

CONSIGNE_DESCRIPTION = (
    "Tu regardes une zone d'un ecran d'ordinateur. Decris en deux ou trois "
    "phrases courtes ce qu'on y voit : formes, couleurs, elements d'interface. "
    "Recopie le texte lisible s'il y en a. Si la zone est noire, vide ou "
    "illisible, dis-le sans deviner ce qu'elle contient. N'invente rien. "
    "Reponds en francais."
)

ABSTENTION_NOIRE = "zone noire : rien a lire ni a decrire (flux protege, ecran eteint ou image noire)"


# --------------------------------------------------------------------------- #
# Cible : case, sous-case ou plage
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Cible:
    demande: str
    ecran: int
    spec: GridSpec
    origine: tuple[int, int]  # left, top du moniteur (coordonnees virtuelles)
    col0: int
    row0: int
    col1: int
    row1: int
    sub: int | None = None

    @property
    def nb_cases(self) -> int:
        return (self.col1 - self.col0 + 1) * (self.row1 - self.row0 + 1)

    @property
    def label(self) -> str:
        a = f"{self.ecran}:{_col_letters(self.col0)}{self.row0 + 1}"
        if self.sub is not None:
            return f"{a}.{self.sub}"
        if self.nb_cases == 1:
            return a
        return f"{a}-{self.ecran}:{_col_letters(self.col1)}{self.row1 + 1}"

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """Rectangle en coordonnees du bureau virtuel (left, top, right, bottom)."""
        l0, t0, r0, b0 = _cell_bounds(self.spec, self.col0, self.row0)
        if self.sub is not None:
            l0, t0, r0, b0 = _sub_bounds(l0, t0, r0, b0, self.sub)
            r1, b1 = r0, b0
        else:
            _, _, r1, b1 = _cell_bounds(self.spec, self.col1, self.row1)
        ox, oy = self.origine
        return (l0 + ox, t0 + oy, r1 + ox, b1 + oy)

    def cases(self) -> list[tuple[str, tuple[int, int, int, int]]]:
        """(label, bbox virtuelle) de chaque case couverte, ligne par ligne."""
        if self.sub is not None:
            return [(self.label, self.bbox)]
        ox, oy = self.origine
        out = []
        for row in range(self.row0, self.row1 + 1):
            for col in range(self.col0, self.col1 + 1):
                l, t, r, b = _cell_bounds(self.spec, col, row)
                out.append((f"{self.ecran}:{_col_letters(col)}{row + 1}", (l + ox, t + oy, r + ox, b + oy)))
        return out


def _grille_txt(spec: GridSpec) -> str:
    return (
        f"grille {spec.cols}x{spec.rows} : colonnes A a {_col_letters(spec.cols - 1)}, "
        f"lignes 1 a {spec.rows}"
    )


def _verifier_case(texte: str, ecran: int, spec: GridSpec, col: int, row: int) -> None:
    fautes = []
    if not 0 <= col < spec.cols:
        fautes.append(f"la colonne {_col_letters(col)} n'existe pas "
                      f"(derniere colonne : {_col_letters(spec.cols - 1)})")
    if not 0 <= row < spec.rows:
        fautes.append(f"la ligne {row + 1} n'existe pas (derniere ligne : {spec.rows})")
    if not fautes:
        return
    conseil = ""
    if row >= spec.rows:
        bas = max(0, spec.rows - 2)
        c_a = _col_letters(max(0, spec.cols // 2 - 3))
        c_b = _col_letters(min(spec.cols - 1, spec.cols // 2 + 2))
        conseil = (f" Le bas de l'ecran (sous-titres), ce sont les lignes {bas + 1} et "
                   f"{spec.rows} : par exemple {ecran}:{c_a}{bas + 1}-{ecran}:{c_b}{spec.rows}.")
    detail = " ; ".join(fautes)
    raise ValueError(
        f"case hors grille : {texte}. L'ecran {ecran} a une {_grille_txt(spec)}. "
        + detail[:1].upper() + detail[1:] + "." + conseil
    )


def parse_cible(texte: str, moniteurs: list[Any]) -> Cible:
    """Transforme ``1:B7``, ``1:B7.5``, ``1:F8-1:K9`` ou ``1:F8-K9`` en Cible.

    ``moniteurs`` : objets avec index, left, top, width, height (duck typing,
    ``screen_context.Monitor`` en production). Leve ValueError, en francais,
    pour toute case qui n'existe pas : une case inventee doit faire du bruit.
    """
    brut = (texte or "").strip().upper().replace(" ", "")
    if not brut:
        raise ValueError("cible vide : donner une case (1:B7) ou une plage (1:F8-1:K9)")
    morceaux = brut.split("-")
    if len(morceaux) > 2:
        raise ValueError(f"plage invalide : {texte}. Forme attendue : 1:F8-1:K9")
    try:
        ea, ca, ra, sa = parse_label(morceaux[0])
    except ValueError:
        raise ValueError(f"case illisible : {morceaux[0]}. Forme attendue : 1:B7, 1:B7.5 ou 1:F8-1:K9") from None
    par_index = {int(m.index): m for m in moniteurs}
    dispo = ", ".join(str(i) for i in sorted(par_index))
    if ea is None:
        raise ValueError(f"precise l'ecran devant la case : 1:{morceaux[0]} ou 2:{morceaux[0]} "
                         f"(ecrans disponibles : {dispo})")
    if ea not in par_index:
        raise ValueError(f"ecran {ea} inconnu : ecrans disponibles {dispo}")
    mon = par_index[ea]
    spec = grid_for_monitor(mon)
    _verifier_case(morceaux[0], ea, spec, ca, ra)

    if len(morceaux) == 1:
        return Cible(texte, ea, spec, (int(mon.left), int(mon.top)), ca, ra, ca, ra, sa)

    if sa is not None:
        raise ValueError("une sous-case (1:B7.5) ne se combine pas avec une plage")
    second = morceaux[1] if ":" in morceaux[1] else f"{ea}:{morceaux[1]}"
    try:
        eb, cb, rb, sb = parse_label(second)
    except ValueError:
        raise ValueError(f"case illisible : {morceaux[1]}. Forme attendue : 1:F8-1:K9") from None
    if sb is not None:
        raise ValueError("une sous-case (1:B7.5) ne se combine pas avec une plage")
    if eb != ea:
        raise ValueError(f"une plage reste sur un seul ecran : {texte} melange l'ecran {ea} "
                         f"et l'ecran {eb}. Lire les deux separement.")
    _verifier_case(second, ea, spec, cb, rb)
    return Cible(texte, ea, spec, (int(mon.left), int(mon.top)),
                 min(ca, cb), min(ra, rb), max(ca, cb), max(ra, rb), None)


# --------------------------------------------------------------------------- #
# Mesures sur les pixels
# --------------------------------------------------------------------------- #

def _reduire(frame: np.ndarray, cote_max: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if max(h, w) <= cote_max:
        return frame
    f = cote_max / max(h, w)
    return cv2.resize(frame, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)


def nom_moyen(mean_bgr: np.ndarray) -> str:
    """Nomme le pixel moyen (HSV) : un second code, independant de l'histogramme.

    Deplace ici le 26/10 depuis un script de test prive : ``smoke_lire_cases.py``
    n'importe ainsi que des fichiers publies.
    L'ancien nom par canal BGR le plus fort disait « vert » pour un marron et
    « rouge » pour un jaune. Seuils = ceux de ``nommer_couleurs`` pour un pixel.
    """
    bgr = np.clip(np.round(np.asarray(mean_bgr, dtype=np.float64)), 0, 255).astype(np.uint8)
    h, s, v = (int(x) for x in cv2.cvtColor(bgr.reshape(1, 1, 3), cv2.COLOR_BGR2HSV)[0, 0])
    if v < 50:
        return "noir"
    if s < 40:
        return "blanc" if v >= 200 else "gris"
    if h < 8 or h >= 170:
        nom = "rouge"
    elif h < 20:
        nom = "orange"
    elif h < 34:
        nom = "jaune"
    elif h < 85:
        nom = "vert"
    elif h < 100:
        nom = "cyan"
    elif h < 130:
        nom = "bleu"
    elif h < 150:
        nom = "violet"
    else:
        nom = "rose"
    if 8 <= h < 34 and v < 130:
        return "marron"
    return nom


def nommer_couleurs(frame: np.ndarray, seuil_pct: float = 1.0) -> list[dict[str, Any]]:
    """Proportion de chaque couleur nommee (francais), de la plus presente a la moins.

    Classement HSV volontairement simple : noir / blanc / gris d'abord (peu de
    saturation ou peu de lumiere), puis la teinte. C'est une mesure, pas une
    perception : un bleu tres sombre compte comme « noir ».
    """
    if frame is None or frame.size == 0:
        return []
    petite = _reduire(frame, 160)
    hsv = cv2.cvtColor(petite, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.int32)
    h, s, v = hsv[:, 0], hsv[:, 1], hsv[:, 2]
    noms = np.empty(h.shape[0], dtype=object)
    noms[:] = ""
    noir = v < 50
    neutre = (~noir) & (s < 40)
    noms[noir] = "noir"
    noms[neutre & (v >= 200)] = "blanc"
    noms[neutre & (v < 200)] = "gris"
    teinte = (~noir) & (~neutre)
    tranches = (
        ("rouge", (h < 8) | (h >= 170)),
        ("orange", (h >= 8) & (h < 20)),
        ("jaune", (h >= 20) & (h < 34)),
        ("vert", (h >= 34) & (h < 85)),
        ("cyan", (h >= 85) & (h < 100)),
        ("bleu", (h >= 100) & (h < 130)),
        ("violet", (h >= 130) & (h < 150)),
        ("rose", (h >= 150) & (h < 170)),
    )
    for nom, masque in tranches:
        noms[teinte & masque] = nom
    # Un orange ou un jaune sombre se lit « marron » a l'oeil.
    noms[teinte & (h >= 8) & (h < 34) & (v < 130)] = "marron"
    valeurs, comptes = np.unique(noms, return_counts=True)
    total = float(noms.shape[0])
    out = [
        {"nom": str(n), "pct": round(float(100.0 * c / total), 1)}
        for n, c in zip(valeurs, comptes)
        if 100.0 * c / total >= seuil_pct
    ]
    out.sort(key=lambda d: -d["pct"])
    return out


def luminance(frame: np.ndarray) -> tuple[float, float, bool]:
    """(luminance moyenne, contraste = ecart-type, zone noire ?) : meme regle que screen_content."""
    from screen_content import region_stats

    return region_stats(frame)


def formes_sommaires(frame: np.ndarray) -> dict[str, Any]:
    """Mesure grossiere des formes : ce n'est PAS une reconnaissance d'objets.

    - ``rectangles`` : contours fermes a quatre coins (boutons, cadres, fenetres) ;
    - ``triangles`` : contours fermes a trois coins ;
    - ``ronds`` : contours bien circulaires (pas un carre : un carre vaut ~0,79) ;
    - ``autres_contours`` : contours de taille notable qui ne sont pas des rectangles
      (triangles et ronds y sont compris) ;
    - ``details`` : part des pixels de contour (0 = zone unie).
    """
    vide = {"rectangles": 0, "triangles": 0, "ronds": 0, "autres_contours": 0,
            "details_pct": 0.0, "niveau": "vide"}
    if frame is None or frame.size == 0:
        return vide
    image = _reduire(frame, 800)
    gris = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    bords = cv2.Canny(gris, 60, 180)
    details = float(np.count_nonzero(bords)) / max(1, bords.size)
    epais = cv2.dilate(bords, np.ones((3, 3), np.uint8), iterations=1)
    # RETR_LIST : un disque dessine DANS un cadre reste visible.
    # RETR_EXTERNAL ne voyait que le cadre (mesure web 23/10 : cercle orange = 0 rond).
    # Mais RETR_LIST rend aussi le bord interieur de chaque trait epaissi : sans
    # filtre, chaque forme comptait double (mesure audit 23/10 : 1 disque = 2 ronds,
    # 3 cadres = 6 rectangles). On garde le plus grand contour, et on saute ceux
    # dont le cadre colle au sien a quelques pixels pres (meme forme).
    contours, _ = cv2.findContours(epais, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    deja: list[tuple[int, int, int, int]] = []
    aire_min = max(64.0, 0.002 * gris.shape[0] * gris.shape[1])
    rect = triangles = ronds = autres = 0
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w * h < aire_min:
            continue
        tol = max(8.0, 0.08 * max(w, h))
        if any(abs(x - x2) <= tol and abs(y - y2) <= tol
               and abs((x + w) - (x2 + w2)) <= tol and abs((y + h) - (y2 + h2)) <= tol
               for x2, y2, w2, h2 in deja):
            continue
        deja.append((x, y, w, h))
        peri = cv2.arcLength(c, True)
        if peri < 1.0:
            continue
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            rect += 1
            continue
        aire = cv2.contourArea(c)
        circularite = 4.0 * math.pi * aire / (peri * peri) if aire > 0 else 0.0
        if len(approx) == 3 and cv2.isContourConvex(approx):
            triangles += 1
            autres += 1
        elif circularite >= 0.82 and len(approx) >= 6:
            ronds += 1
            autres += 1
        else:
            autres += 1
    if details < 0.002:
        niveau = "unie"
    elif details < 0.03:
        niveau = "peu de details"
    elif details < 0.10:
        niveau = "details moyens"
    else:
        niveau = "beaucoup de details"
    return {"rectangles": rect, "triangles": triangles, "ronds": ronds,
            "autres_contours": autres,
            "details_pct": round(100.0 * details, 2), "niveau": niveau}


# --------------------------------------------------------------------------- #
# Description par un modele de vision local (opt-in)
# --------------------------------------------------------------------------- #

def ollama_local(url: str) -> bool:
    """Vrai si l'adresse du modele designe ce PC."""
    return (urllib.parse.urlparse(url).hostname or "").lower() in HOTES_LOCAUX


def _distant_refuse() -> str:
    if ollama_local(OLLAMA) or os.environ.get("VISION_OLLAMA_DISTANT") == "1":
        return ""
    return ("adresse du modele hors de ce PC refusee (VISION_OLLAMA_URL) : l'image de "
            "l'ecran ne sort pas ; VISION_OLLAMA_DISTANT=1 pour l'autoriser en connaissance de cause")


def _post(chemin: str, corps: dict[str, Any], timeout: float) -> dict[str, Any]:
    refus = _distant_refuse()
    if refus:
        raise PermissionError(refus)
    req = urllib.request.Request(OLLAMA + chemin, data=json.dumps(corps).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def decrire_ollama(frame: np.ndarray, modele: str = MODELE_DEFAUT,
                   timeout: float = TIMEOUT_DESCRIPTION_S,
                   consigne: str | None = None) -> dict[str, Any]:
    """Demande a un modele Ollama DEJA INSTALLE de decrire l'image.

    Ne leve pas. Ne telecharge jamais : si le modele n'est pas dans la liste
    d'Ollama, on s'abstient et on le dit (aucun appel a /api/pull ici).
    ``consigne`` remplace le texte envoye au modele. Par defaut, la consigne
    historique (decrire, ne pas inventer).
    """
    res: dict[str, Any] = {"available": False, "moteur": "ollama", "modele": modele,
                           "texte": None, "reason": "", "elapsed_ms": 0.0}
    # Ollama lit « moondream » comme « moondream:latest » : on compare le nom
    # normalise (audit 23/10 : « moondream » seul passait la garde).
    nom = modele.strip().lower()
    if ":" not in nom:
        nom += ":latest"
    if nom in MODELES_NON_FIABLES and os.environ.get("VISION_OLLAMA_MODELE_NON_FIABLE") != "1":
        res["reason"] = MODELES_NON_FIABLES[nom]
        return res
    t0 = time.perf_counter()
    refus = _distant_refuse()
    if refus:
        res["reason"] = refus
        return res
    try:
        tags = json.loads(urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5).read())
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"Ollama injoignable ({type(exc).__name__}) : description impossible"
        return res
    noms = {m.get("name") for m in tags.get("models") or []} | {m.get("model") for m in tags.get("models") or []}
    if modele not in noms:
        res["reason"] = f"modele {modele} absent d'Ollama : aucun telechargement tente"
        return res
    ok, png = cv2.imencode(".png", _reduire(frame, COTE_MAX_DESCRIPTION))
    if not ok:
        res["reason"] = "encodage de l'image impossible"
        return res
    corps = {
        "model": modele,
        "messages": [{"role": "user", "content": consigne or CONSIGNE_DESCRIPTION,
                      "images": [base64.b64encode(png.tobytes()).decode("ascii")]}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": 400},
    }
    try:
        d = _post("/api/chat", corps, timeout)
    except urllib.error.HTTPError as exc:
        res["reason"] = f"HTTP {exc.code} : {exc.read().decode('utf-8', 'replace')[:120]}"
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        return res
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"appel Ollama echoue ({type(exc).__name__})"
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
        return res
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    m = d.get("message") or {}
    texte = (m.get("content") or "").strip()
    if not texte:
        # Lecon R-S15 : un modele peut tout mettre dans « thinking » et rien
        # dans la reponse. On ne recopie pas sa pensee comme si c'etait une
        # description.
        res["reason"] = ("reponse vide (le modele a ecrit dans thinking)"
                         if (m.get("thinking") or "").strip() else "reponse vide")
        return res
    res["available"] = True
    res["texte"] = texte
    res["fin"] = d.get("done_reason")
    # Scan V1.1b : une phrase coupee par num_predict (« length ») s'arretait
    # au milieu d'un mot sans que rien ne le dise.
    res["tronquee"] = d.get("done_reason") == "length"
    res["a_verifier"] = True
    return res


def _decrire_moondream(frame: np.ndarray) -> dict[str, Any]:
    """Moondream3 local (chemin de juillet, via screen_content). Lent : ~50 s a froid."""
    from screen_content import Capture, _vlm

    mean, std, noir = luminance(frame)
    cap = Capture(region="cases", bbox=[0, 0, int(frame.shape[1]), int(frame.shape[0])],
                  width=int(frame.shape[1]), height=int(frame.shape[0]), elapsed_ms=0.0,
                  mean_luma=mean, std_luma=std, looks_black=noir, frame=frame)
    r = _vlm(cap, None)
    return {"available": bool(r.get("available")), "moteur": "moondream",
            "modele": "moondream/moondream3-preview", "texte": r.get("caption"),
            "reason": r.get("reason", ""), "elapsed_ms": r.get("elapsed_ms", 0.0)}


# --------------------------------------------------------------------------- #
# Lecture
# --------------------------------------------------------------------------- #

def _overlay_actif() -> bool:
    etat = Path(os.environ.get("VISION_GRILLE_STATE")
                or (Path(tempfile.gettempdir()) / "vision-public-grille-state.json"))
    return etat.is_file()


def lire(
    cible: str,
    *,
    avec_texte: bool = False,
    decrire: bool = False,
    moteur: str = "ollama",
    modele: str = MODELE_DEFAUT,
    moniteurs: list[Any] | None = None,
    grab: Callable[[int, int, int, int], np.ndarray] | None = None,
    decrire_fn: Callable[[np.ndarray], dict[str, Any]] | None = None,
    ocr_lang: str = "fra+eng",
    ocr_min_conf: int = 45,
    rapport_mesure: bool = False,
) -> dict[str, Any]:
    """Lit une case, une sous-case ou une plage. Leve ValueError si la cible n'existe pas.

    ``grab(left, top, width, height)`` et ``decrire_fn(frame)`` sont injectables
    pour les tests ; en production : ``screen_capture.grab_region`` et le
    moteur choisi.

    ``rapport_mesure`` : ajoute des phrases ecrites par le CODE a partir des
    mesures (gabarit ``context/vision-rapport-mesure.md``), sans aucun modele.
    Il lit le texte (OCR local) tout seul, parce que les mots-reperes en
    dependent.
    """
    if rapport_mesure:
        avec_texte = True
    if moniteurs is None:
        from screen_context import list_monitors

        moniteurs = list_monitors()
    c = parse_cible(cible, moniteurs)
    if grab is None:
        from screen_capture import grab_region as grab

    l, t, r, b = c.bbox
    t0 = time.perf_counter()
    frame = grab(l, t, r - l, b - t)
    capture_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    mean, std, noir = luminance(frame)

    out: dict[str, Any] = {
        "demande": cible,
        "cible": c.label,
        "ecran": c.ecran,
        "grille": f"{c.spec.cols}x{c.spec.rows}",
        "cases": c.nb_cases,
        "bbox": [l, t, r, b],
        "taille": [int(frame.shape[1]), int(frame.shape[0])],
        "capture_ms": capture_ms,
        "zone_noire": bool(noir),
        "luminance": round(mean, 1),
        "contraste": round(std, 1),
        "couleurs": nommer_couleurs(frame),
        "formes": formes_sommaires(frame),
        "par_case": [],
        "texte": None,
        "description": None,
        "notes": [],
    }
    if _overlay_actif():
        out["notes"].append("overlay grille actif pendant la lecture : il est exclu de la "
                            "capture si l'exclusion Windows marche (grille.py test-exclusion)")

    if c.nb_cases > 1:
        for label, (cl, ct, cr, cb) in c.cases():
            morceau = frame[ct - t:cb - t, cl - l:cr - l]
            m2, s2, n2 = luminance(morceau)
            coul = nommer_couleurs(morceau)
            out["par_case"].append({
                "case": label,
                "luminance": round(m2, 1),
                "zone_noire": bool(n2),
                "couleur": coul[0]["nom"] if coul else None,
                "pct": coul[0]["pct"] if coul else 0.0,
            })

    if avec_texte:
        out["texte"] = _lire_texte(frame, c, (l, t, r, b), mean, std, noir, ocr_lang, ocr_min_conf)

    if decrire:
        if noir:
            out["description"] = {"available": False, "moteur": moteur, "texte": None,
                                  "reason": ABSTENTION_NOIRE, "elapsed_ms": 0.0}
        else:
            if decrire_fn is not None:
                fn = decrire_fn
            elif moteur == "moondream":
                fn = _decrire_moondream
            else:
                def fn(img: np.ndarray) -> dict[str, Any]:
                    return decrire_ollama(img, modele)
            desc = fn(frame)
            if desc.get("available"):
                # Le drapeau voyage AVEC la phrase : un programme qui ne lit
                # que `description` doit le voir aussi (scan V1.1b).
                desc.setdefault("a_verifier", True)
                desc.setdefault("tronquee", False)
            out["description"] = desc
            out["notes"].append("description = phrase d'un modele, a verifier ; "
                                "couleurs, formes et texte = mesures")
    if rapport_mesure:
        out["rapport_mesure"] = _faire_rapport(out)
    return out


# --------------------------------------------------------------------------- #
# Rapport-mesure (V1.1c) : des phrases ecrites par le code, sans modele
# --------------------------------------------------------------------------- #

GABARIT = Path(__file__).resolve().parents[1] / "context" / "vision-rapport-mesure.md"
SEUIL_CONF_RAPPORT = 55   # meme filtre que le protocole du scan V1.1b
MAX_LIGNES_RAPPORT = 12
MAX_MOTS_LIBELLE = 8      # au-dela, la ligne est une phrase, pas un libelle d'interface
NEUTRES = ("noir", "blanc", "gris")


class _Trous(dict):
    """format_map qui laisse un {trou} inconnu tel quel au lieu de lever."""

    def __missing__(self, cle: str) -> str:
        return "{" + cle + "}"


def _sans_accents(s: str) -> str:
    """Minuscules sans accents, MEME LONGUEUR que s (un caractere pour un)."""
    return "".join(unicodedata.normalize("NFKD", ch)[0] for ch in s).lower()


def _nb(x: float) -> str:
    """Nombre a la francaise : 92 ou 4,7."""
    return f"{x:.0f}" if abs(x) >= 10 else f"{x:.1f}".replace(".", ",")


def _lire_gabarit(chemin: Path) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Les deux tableaux du gabarit : phrases (cle -> phrase) et mots-reperes."""
    phrases: dict[str, str] = {}
    reperes: list[tuple[str, str]] = []
    section = ""
    entete_vu = False
    for brut in chemin.read_text(encoding="utf-8").splitlines():
        ligne = brut.strip()
        if ligne.startswith("## "):
            titre = _sans_accents(ligne[3:])
            section = "phrases" if titre.startswith("phrases") else ("reperes" if titre.startswith("mots-reperes") else "")
            entete_vu = False
            continue
        if not section or not ligne.startswith("|"):
            continue
        cellules = [c.strip() for c in ligne.strip("|").split("|")]
        if len(cellules) != 2 or set(cellules[0]) <= set("-: "):
            continue
        if not entete_vu:  # premiere ligne du tableau = les titres de colonnes
            entete_vu = True
            continue
        if section == "phrases":
            phrases[cellules[0]] = cellules[1]
        else:
            reperes.append((cellules[0], cellules[1]))
    return phrases, reperes


def _disposition(par_case: list[dict[str, Any]]) -> str:
    """« A a B : clair, C a P : sombre » : la lumiere moyenne de chaque colonne."""
    colonnes: dict[str, list[float]] = {}
    for pc in par_case:
        nom = pc["case"].split(":")[1].rstrip("0123456789")
        colonnes.setdefault(nom, []).append(float(pc["luminance"]))
    if len(colonnes) < 2:
        return ""
    ordre = sorted(colonnes, key=lambda n: (len(n), n))
    tons = [(n, "clair" if sum(v) / len(v) >= 128 else "sombre") for n, v in colonnes.items()]
    tons.sort(key=lambda t: ordre.index(t[0]))
    morceaux, debut = [], 0
    for i in range(1, len(tons) + 1):
        if i == len(tons) or tons[i][1] != tons[debut][1]:
            a, b = tons[debut][0], tons[i - 1][0]
            morceaux.append(f"{a} : {tons[debut][1]}" if a == b else f"{a} à {b} : {tons[debut][1]}")
            debut = i
    return ", ".join(morceaux)


def _faire_rapport(r: dict[str, Any], gabarit: Path | None = None) -> dict[str, Any]:
    """Transforme un resultat de ``lire`` en phrases. Fonction pure : aucun
    appel reseau, aucun modele, meme entree = meme sortie."""
    chemin = Path(os.environ.get("VISION_GABARIT_RAPPORT") or gabarit or GABARIT)
    base = {"texte": None, "phrases": [], "reperes": [], "determine": True,
            "moteur": "gabarit", "gabarit": chemin.name}
    try:
        P, catalogue = _lire_gabarit(chemin)
    except Exception as exc:  # noqa: BLE001
        base["raison"] = f"gabarit illisible ({type(exc).__name__}) : aucun rapport"
        return base

    def dire(cle: str, **trous: Any) -> None:
        if cle in P:
            base["phrases"].append(P[cle].format_map(_Trous({k: str(v) for k, v in trous.items()})))

    pct = {c["nom"]: float(c["pct"]) for c in r.get("couleurs") or []}
    largeur, hauteur = (r.get("taille") or [0, 0])[:2]
    dire("zone", cible=r.get("cible"), ecran=r.get("ecran"), cases=r.get("cases"),
         largeur=largeur, hauteur=hauteur)
    if r.get("zone_noire"):
        dire("noire", lum=_nb(float(r.get("luminance") or 0)))
        dire("fin")
        base["texte"] = "\n".join(base["phrases"])
        return base

    if pct:
        dire("couleurs", couleurs=", ".join(f"{n} {_nb(v)} %" for n, v in list(pct.items())[:4]))
    if pct.get("blanc", 0) >= 90:
        dire("fond_clair", blanc=_nb(pct["blanc"]))
    elif pct.get("noir", 0) >= 90:
        dire("fond_sombre", noir=_nb(pct["noir"]))
    elif pct.get("blanc", 0) >= 20 and pct.get("noir", 0) + pct.get("gris", 0) >= 20:
        dire("fond_mixte")
    touches = [(n, v) for n, v in pct.items() if n not in NEUTRES and v >= 1.5]
    if touches:
        dire("touches", touches=", ".join(f"{n} {_nb(v)} %" for n, v in touches))
    contraste = float(r.get("contraste") or 0)
    dire("lumiere", lum=_nb(float(r.get("luminance") or 0)), contraste=_nb(contraste),
         contraste_mot="faible" if contraste < 30 else ("moyen" if contraste < 80 else "fort"))
    f = r.get("formes") or {}
    if f:
        niveau = str(f.get("niveau") or "").replace("details", "détails")
        dire("formes", niveau=niveau, details=_nb(float(f.get("details_pct") or 0)),
             rectangles=f.get("rectangles"), autres=f.get("autres_contours"))
    dispo = _disposition(r.get("par_case") or [])
    if dispo:
        dire("disposition", disposition=dispo)

    t = r.get("texte")
    sures: list[dict[str, Any]] = []
    if t is None:
        dire("texte_non_demande")
    elif not t.get("available"):
        dire("texte_indispo", raison=t.get("reason") or "?")
    else:
        sures = [ln for ln in t.get("lignes") or []
                 if (ln.get("conf_moy") or 0) >= SEUIL_CONF_RAPPORT and len(ln["texte"].strip()) >= 2]
        if not sures:
            dire("texte_aucun")
        else:
            dire("texte_intro", n=len(sures))
            # Un meme morceau repete dans la meme case (« 120 » x4 sur la
            # colonne Version d'Hyper-V) est dit une fois, avec le compte.
            vus: dict[tuple[Any, str], int] = {}
            for ln in sures:
                cle_ln = (ln.get("case"), ln["texte"])
                vus[cle_ln] = vus.get(cle_ln, 0) + 1
            uniques = list(vus.items())
            for (case, texte), fois in uniques[:MAX_LIGNES_RAPPORT]:
                dire("texte_ligne", case=case, texte=texte)
                if fois > 1:
                    base["phrases"][-1] += f" (x{fois})"
            if len(uniques) > MAX_LIGNES_RAPPORT:
                dire("texte_reste", k=len(uniques) - MAX_LIGNES_RAPPORT)

    # Mots-reperes : seulement sur le texte SUR, et on cite le morceau EXACT
    # tel que l'OCR l'a lu (meme longueur grace a _sans_accents).
    # Essai live du 17/10 : un message de chat qui CITAIT « Gestionnaire
    # Hyper-V » a fait conclure « cette zone montre Hyper-V ». Un mot vu ne
    # suffit pas : entre guillemets, c'est une citation ; au milieu d'une
    # longue phrase, c'est peut-etre un texte qui en parle. Dans ces deux cas
    # le rapport dit le mot, mais n'affirme pas la scene.
    for mot, scene in catalogue:
        cle = _sans_accents(mot)
        for ln in sures:
            texte_ln = ln["texte"]
            pos = _sans_accents(texte_ln).find(cle)
            if pos < 0:
                continue
            lu = texte_ln[pos:pos + len(mot)]
            avant, apres = texte_ln[:pos], texte_ln[pos + len(mot):]
            cite = (avant.count("«") > avant.count("»") and "»" in apres) \
                or avant.count('"') % 2 == 1
            nb_mots = len(texte_ln.split())
            if cite:
                dire("repere_cite", case=ln.get("case"), mot=lu)
            elif nb_mots > MAX_MOTS_LIBELLE:
                dire("repere_phrase", case=ln.get("case"), mot=lu, n=nb_mots)
            else:
                dire("repere", case=ln.get("case"), mot=lu, scene=scene)
            base["reperes"].append({"mot": lu, "case": ln.get("case"), "scene": scene,
                                    "affirme": not cite and nb_mots <= MAX_MOTS_LIBELLE})
            break
    if t is not None and t.get("available") and not base["reperes"]:
        dire("sans_repere")
    dire("fin")
    base["texte"] = "\n".join(base["phrases"])
    return base


rapport_mesure = _faire_rapport


def _segments(mots: list[tuple[int, int, int, int, str, int]]) -> list[list[tuple]]:
    """Mots OCR -> morceaux de ligne, sans coller deux fenetres voisines.

    1. Rangees : meme regle que screen_content._group_lines (recouvrement
       vertical).
    2. Puis on COUPE une rangee la ou l'ecart entre deux mots depasse une
       espace normale (2,5 fois la hauteur du texte). Sans cette coupe, sur une
       zone large, le texte de deux fenetres cote a cote devenait une seule
       « ligne » rangee dans la case du milieu (scan V1.1b du 17/10).
    """
    items = sorted(mots, key=lambda b: (b[1] + b[3] / 2, b[0]))
    rangees: list[list[tuple]] = []
    for b in items:
        cy = b[1] + b[3] / 2
        for grp in rangees:
            ref = grp[0]
            if abs(cy - (ref[1] + ref[3] / 2)) <= max(6, ref[3] * 0.6):
                grp.append(b)
                break
        else:
            rangees.append([b])
    segs: list[list[tuple]] = []
    for grp in rangees:
        grp.sort(key=lambda b: b[0])
        hauteur = sorted(b[3] for b in grp)[len(grp) // 2]
        seuil = max(2.5 * hauteur, 20)
        cur = [grp[0]]
        for b in grp[1:]:
            prec = cur[-1]
            if b[0] - (prec[0] + prec[2]) > seuil:
                segs.append(cur)
                cur = [b]
            else:
                cur.append(b)
        segs.append(cur)
    return segs


def _lire_texte(frame: np.ndarray, c: Cible, bbox: tuple[int, int, int, int],
                mean: float, std: float, noir: bool, lang: str, min_conf: int) -> dict[str, Any]:
    """OCR de la zone. Chaque morceau de ligne est range dans la case ou il
    COMMENCE (``case``), avec toutes les cases qu'il couvre (``cases``)."""
    from screen_grid import cells_overlapping_bbox

    res: dict[str, Any] = {"available": False, "reason": "", "mots": 0,
                           "elapsed_ms": 0.0, "lignes": []}
    if noir:
        res["reason"] = ABSTENTION_NOIRE
        return res
    try:
        from ocr_screen import ocr_boxes
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"OCR indisponible : {exc}"
        return res
    t0 = time.perf_counter()
    try:
        mots = ocr_boxes(frame, lang, min_conf)
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"Tesseract KO : {exc}"
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return res
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    res["available"] = True
    res["mots"] = len(mots)
    l, t = bbox[0], bbox[1]
    ox, oy = c.origine
    lignes = []
    for seg in _segments(mots):
        texte = " ".join(b[4] for b in seg).strip()
        if not texte:
            continue
        x0 = min(b[0] for b in seg) + l
        y0 = min(b[1] for b in seg) + t
        x1 = max(b[0] + b[2] for b in seg) + l
        y1 = max(b[1] + b[3] for b in seg) + t
        debut = cell_at_pixel(c.spec, x0, (y0 + y1) // 2, origin_left=ox, origin_top=oy)
        couvre = cells_overlapping_bbox(c.spec, (x0, y0, x1, y1), origin_left=ox, origin_top=oy)
        confs = [b[5] for b in seg]
        lignes.append({"case": debut.label if debut else None,
                       "cases": [k.label for k in couvre],
                       "texte": texte, "bbox": [x0, y0, x1, y1],
                       "conf_moy": round(sum(confs) / len(confs), 1)})
    lignes.sort(key=lambda d: (d["bbox"][1], d["bbox"][0]))
    res["lignes"] = lignes
    return res
