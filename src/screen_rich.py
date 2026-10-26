"""Vision ecran riche (R-S2c) : ce qui est visible, structure.

L'auteur valide le socle (`--profile film` : app, site, titre) mais veut
beaucoup plus : les onglets ouverts, la barre de favoris, le titre exact de
la video, les recommandations de la barre laterale, les sous-titres visibles.

Choix d'architecture central, et il va a l'encontre de la lettre du brief :
**la structure vient d'UIA, pas de l'OCR.**

Mesures faites pendant ce lot sur la fenetre Brave de l'utilisateur :

  - 12 onglets rendus par UIA en 58 ms, avec leur titre COMPLET (100
    caracteres pour le premier). L'OCR de la bande chrome, lui, ne peut lire
    que le libelle tronque affiche dans l'onglet.
  - 419 liens de page rendus en 59 ms, avec leur nom accessible et leur bbox.
  - Pour comparaison, un seul OCR de bande coute ~300 ms et fragmente
    ("D a >", "on louveau", constats R-S2a).

Autrement dit, l'OCR donnerait ici un resultat a la fois plus lent et moins
bon. Il garde une place, mais une seule : lire ce qui n'existe QUE en pixels,
c'est-a-dire les sous-titres incrustes dans une video. C'est le seul endroit
ou ce module l'appelle.

Chaque champ porte sa provenance (`source`) : "uia" ou "ocr". Un consommateur
doit pouvoir savoir si une valeur a ete lue dans l'arbre ou devinee sur des
pixels.

L'arbre d'accessibilite d'un navigateur vivant est VOLATILE : mesure a une
minute d'intervalle sur la meme fenetre, 12 onglets puis 0, et un
`COMError: pointeur non valide` en pleine iteration. Toute collecte passe
donc par `screen_uia.collect`, qui rend ce qu'il a pu et compte les pannes.
Un resultat vide est distingue d'un arbre indisponible.

VIE PRIVEE : ce module rend les titres d'onglets, les favoris et les liens
reellement affiches. Donnees personnelles. Ne jamais coller une sortie dans
un fichier commite.

Local only. Aucun reseau.
"""

from __future__ import annotations

import re
import time
from typing import Any

import cv2
import numpy as np

__all__ = [
    "attacher_meta",
    "densite_contours",
    "chrome_zones",
    "classer_meta",
    "colors_of",
    "parse_youtube_link",
    "read_overlay_text",
    "rich_browser",
    "strip_site_suffix",
]

# Hauteur maximale d'un controle de barre d'onglets ou de favoris. Au-dela,
# ce n'est plus du chrome de navigateur.
MAX_HAUTEUR_CHROME = 48

# Distance sous l'omnibox ou l'on accepte de trouver la barre de favoris.
FENETRE_FAVORIS_PX = 70

# Tolerance verticale pour considerer deux controles sur la meme rangee.
TOLERANCE_RANGEE_PX = 8


# --------------------------------------------------------------------------- #
# Titres
# --------------------------------------------------------------------------- #

# Noms de navigateurs et de sites que Windows accole au titre de fenetre.
_MARQUES = (
    "Brave", "Google Chrome", "Chrome", "Microsoft Edge", "Mozilla Firefox",
    "Firefox", "Vivaldi", "Opera", "YouTube",
)

# Le separateur n'est pas toujours le meme caractere : Brave utilise un tiret
# cadratin, Chrome un tiret simple, et certaines versions un tiret demi-cadratin
# ou une espace insecable. Observe sur le poste de l'utilisateur : le titre finissait par
# un separateur qui n'etait aucun de ceux d'une liste ecrite a la main. Une
# expression reguliere sur la CLASSE de caracteres evite ce piege.
_RE_SUFFIXE = re.compile(
    r"[\s ]*[-‐-―−|][\s ]*(" + "|".join(
        re.escape(m) for m in _MARQUES
    ) + r")[\s ]*$",
    re.IGNORECASE,
)


def strip_site_suffix(titre: str) -> tuple[str, list[str]]:
    """Retire les suffixes de navigateur et de site du titre de fenetre.

    Le titre de fenetre Windows n'est PAS tronque, contrairement au libelle
    visible de l'onglet. Mesure sur la fenetre de l'utilisateur : le titre complet
    fait 100 caracteres la ou l'onglet n'en montre qu'une vingtaine. C'est
    donc la meilleure source pour le titre exact d'une video, et elle coute
    zero.

    On retire en boucle : un titre YouTube dans Brave en porte deux.
    """
    out = " ".join((titre or "").split())
    retires: list[str] = []
    for _ in range(4):  # borne : jamais de boucle infinie sur une entree tordue
        m = _RE_SUFFIXE.search(out)
        if not m:
            break
        retires.append(m.group(1))
        out = out[: m.start()].strip()
    return (out, retires)


# --------------------------------------------------------------------------- #
# Liens YouTube
# --------------------------------------------------------------------------- #

# Les noms accessibles YouTube encodent titre, chaine, vues et duree dans une
# seule chaine. Les motifs different selon la langue de l'interface.
_RE_VUES = re.compile(
    r"(\d[\d\s .,]*)\s*(k|K|M|Md|B)?\s*(?:de\s+)?(?:vues|views)", re.IGNORECASE
)
_RE_DUREE = re.compile(
    r"(\d+)\s*(?:heures?|hours?)|(\d+)\s*(?:minutes?)|(\d+)\s*(?:secondes?|seconds?)",
    re.IGNORECASE,
)
_RE_CHAINE = re.compile(r"\b(?:de|par|by)\s+(.{2,60}?)(?=\s+\d|\s+il y a|\s+\d+\s|$)", re.IGNORECASE)


def _nombre_vues(txt: str) -> int | None:
    m = _RE_VUES.search(txt)
    if not m:
        return None
    brut = m.group(1).replace(" ", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        val = float(brut)
    except ValueError:
        return None
    mult = {"k": 1_000, "K": 1_000, "M": 1_000_000, "Md": 1_000_000_000, "B": 1_000_000_000}
    return int(val * mult.get(m.group(2) or "", 1))


def parse_youtube_link(nom: str) -> dict[str, Any]:
    """Decompose un nom accessible YouTube en {title, channel, views, duration_s}.

    Les champs non trouves sont **absents**, jamais devines. Sur une interface
    dont la langue ou le format changerait, on rendrait donc un titre seul
    plutot qu'une structure inventee.
    """
    txt = " ".join((nom or "").split())
    out: dict[str, Any] = {"raw_len": len(txt)}
    if not txt:
        return out

    vues = _nombre_vues(txt)
    if vues is not None:
        out["views"] = vues

    duree = 0
    for h, mn, sec in _RE_DUREE.findall(txt):
        duree += int(h or 0) * 3600 + int(mn or 0) * 60 + int(sec or 0)
    if duree:
        out["duration_s"] = duree

    chaine = _RE_CHAINE.search(txt)
    if chaine:
        c = chaine.group(1).strip(" ,;-")
        if c:
            out["channel"] = c

    # Le titre est ce qui precede le premier marqueur reconnu.
    coupe = len(txt)
    for motif in (_RE_VUES, _RE_CHAINE):
        m = motif.search(txt)
        if m:
            coupe = min(coupe, m.start())
    titre = txt[:coupe].strip(" ,;-·|")
    if titre:
        out["title"] = titre
    return out



# --------------------------------------------------------------------------- #
# Metadonnees voisines d'un lien (R-S2d)
# --------------------------------------------------------------------------- #

# Mesure sur la page YouTube reelle de l'utilisateur (31/08) : le nom accessible d'un
# lien de barre laterale contient le TITRE SEUL. La chaine, le nombre de vues
# et l'anciennete vivent dans des elements `Text` FRERES, places juste sous le
# lien. Chercher ces champs dans le nom du lien etait donc voue a l'echec,
# quelle que soit l'expression reguliere.
#
# Geometrie relevee : metadonnees entre le bas du lien et +45 px, avec
# recouvrement horizontal. Une rangee de trois vignettes cote a cote se
# desambigue par le recouvrement en x.
FENETRE_META_PX = 48

# "5,1 M de vues" ou "1 234 views"
_RE_VUES_EXPLICITE = re.compile(
    r"^\s*(\d[\d\s  .,]*?)\s*(k|K|M|Md|B)?\s*(?:de\s+)?(?:vues|views)\s*$",
    re.IGNORECASE,
)
# "590 k" : forme compacte, sans le mot "vues", observee sur la page reelle
_RE_VUES_COMPACTE = re.compile(r"^\s*(\d[\d\s  .,]*?)\s*(k|K|M|Md|B)\s*$")
# "il y a 6 m." / "6 months ago"
_RE_AGE = re.compile(r"(il\s+y\s+a\b|\bago\b)", re.IGNORECASE)

_MULT = {"k": 1_000, "K": 1_000, "M": 1_000_000, "Md": 1_000_000_000, "B": 1_000_000_000}


def _vers_entier(brut: str, suffixe: str | None) -> int | None:
    net = brut.replace(" ", "").replace(" ", "").replace(" ", "")
    net = net.replace(".", "").replace(",", ".")
    try:
        return int(float(net) * _MULT.get(suffixe or "", 1))
    except ValueError:
        return None


def classer_meta(texte: str) -> tuple[str, Any]:
    """Classe un texte voisin : ('views', n) | ('age', str) | ('channel', str).

    L'ordre compte : une chaine qui s'appellerait "590 k" serait lue comme des
    vues. C'est un compromis assume, et il va dans le bon sens : mieux vaut
    un champ manquant qu'un nom de chaine invente.
    """
    t = " ".join((texte or "").split())
    if not t:
        return ("", None)
    m = _RE_VUES_EXPLICITE.match(t)
    if m:
        n = _vers_entier(m.group(1), m.group(2))
        if n is not None:
            return ("views", n)
    m = _RE_VUES_COMPACTE.match(t)
    if m:
        n = _vers_entier(m.group(1), m.group(2))
        if n is not None:
            return ("views", n)
    if _RE_AGE.search(t):
        return ("age", t)
    return ("channel", t)


def attacher_meta(
    entree: dict[str, Any],
    lien_bbox: list[int],
    textes: list[dict[str, Any]],
) -> None:
    """Complete une recommandation avec les textes voisins. N'invente rien.

    Un champ deja rempli par le nom du lien n'est pas ecrase, et un champ
    introuvable reste ABSENT.
    """
    lx0, _, lx1, lbas = lien_bbox
    voisins = []
    for t in textes:
        tx0, ty0, tx1, _ = t["bbox"]
        if not (lbas - 4 <= ty0 <= lbas + FENETRE_META_PX):
            continue
        if min(lx1, tx1) - max(lx0, tx0) <= 0:  # pas de recouvrement horizontal
            continue
        voisins.append((ty0, tx0, t["name"]))
    if not voisins:
        return
    voisins.sort()
    for _, _, nom in voisins:
        cle, val = classer_meta(nom)
        if not cle or val is None:
            continue
        if cle == "channel" and len(str(val)) > 60:
            continue  # trop long pour un nom de chaine : sans doute un titre
        entree.setdefault(cle, val)


# --------------------------------------------------------------------------- #
# Zones du chrome navigateur
# --------------------------------------------------------------------------- #

def _rangees(items: list[dict[str, Any]], tol: int = TOLERANCE_RANGEE_PX):
    """Groupe des controles par rangee horizontale (meme haut, a la tolerance)."""
    rangs: dict[int, list[dict[str, Any]]] = {}
    for it in items:
        y = it["bbox"][1]
        cle = next((k for k in rangs if abs(k - y) <= tol), y)
        rangs.setdefault(cle, []).append(it)
    return rangs


def chrome_zones(
    root: Any,
    visible: tuple[int, int, int, int],
    omnibox_bbox: list[int] | None,
) -> dict[str, Any]:
    """Onglets, favoris et frontiere du contenu, par UIA.

    Les frontieres ne sont pas des constantes : elles sont deduites de
    controles reels. Le bas des onglets vient des `TabItem`, le bas de la
    barre d'outils vient du bbox de l'omnibox, et la barre de favoris est la
    rangee de boutons qui vient juste apres.
    """
    import screen_uia as su

    res: dict[str, Any] = {
        "available": False,
        "tabs": [],
        "bookmarks": [],
        "content_top": visible[1],
        "errors": 0,
        "source": "uia",
    }

    tabitems, err_t = su.collect(root, "TabItem")
    boutons, err_b = su.collect(root, "Button")
    res["errors"] = err_t + err_b
    if not tabitems and not boutons:
        res["reason"] = (
            "arbre d'accessibilite indisponible ou vide au moment de l'appel"
            if res["errors"]
            else "aucun controle de chrome expose"
        )
        res["tabs_reason"] = res["reason"]
        return res
    res["available"] = True

    # --- onglets : le role suffit, aucune geometrie a deviner --------------- #
    onglets = [
        t for t in tabitems
        if not t["offscreen"] and (t["bbox"][3] - t["bbox"][1]) <= MAX_HAUTEUR_CHROME
    ]
    onglets.sort(key=lambda t: t["bbox"][0])
    res["tabs"] = [
        {"title": t["name"], "bbox": t["bbox"], "source": "uia"} for t in onglets
    ]
    if not onglets:
        # Distinguer les causes : sans cela, "0 onglet" ne veut rien dire.
        #
        # Cette explication a longtemps ete FAUSSE. Jusqu'en R-S8, le role
        # "TabItem" n'etait pas enregistre dans screen_uia._ROLES : la
        # requete rendait une liste vide sans erreur, et ce message
        # attribuait a une barre repliee ce qui etait un role non interroge.
        # Onze TabItem nommes etaient presents dans l'arbre au moment ou ce
        # code affirmait qu'il n'y en avait aucun. Le role est desormais
        # enregistre, et `collect` leve sur un role inconnu.
        res["tabs_reason"] = (
            "arbre indisponible pendant l'appel"
            if err_t
            else (
                "aucun TabItem expose par l'application : barre masquee, "
                "repliee, ou fenetre sans onglets"
                if boutons
                else "aucun controle de chrome expose"
            )
        )
    tabs_bas = max((t["bbox"][3] for t in onglets), default=visible[1])

    # --- favoris : rangee de boutons juste sous l'omnibox ------------------- #
    barre_bas = omnibox_bbox[3] if omnibox_bbox else tabs_bas
    candidats = [
        b for b in boutons
        if not b["offscreen"]
        and (b["bbox"][3] - b["bbox"][1]) <= MAX_HAUTEUR_CHROME
        and barre_bas < b["bbox"][1] <= barre_bas + FENETRE_FAVORIS_PX
    ]
    favoris_bas = barre_bas
    if candidats:
        rangs = _rangees(candidats)
        # La barre de favoris est la rangee la plus fournie de cette zone.
        meilleure = max(rangs.values(), key=len)
        if len(meilleure) >= 2:
            meilleure.sort(key=lambda b: b["bbox"][0])
            res["bookmarks"] = [
                {"title": b["name"], "bbox": b["bbox"], "source": "uia"} for b in meilleure
            ]
            favoris_bas = max(b["bbox"][3] for b in meilleure)

    res["content_top"] = max(tabs_bas, barre_bas, favoris_bas)
    return res


# --------------------------------------------------------------------------- #
# Contenu de page
# --------------------------------------------------------------------------- #

def page_links(
    root: Any,
    visible: tuple[int, int, int, int],
    content_top: int,
    *,
    host: str = "",
    limite: int = 400,
) -> dict[str, Any]:
    """Liens de la page, et recommandations si la mise en page s'y prete.

    La colonne de droite d'une page video (la barre laterale) est identifiee
    geometriquement : des liens nommes, alignes, dans le tiers droit de la
    fenetre, sous le contenu principal. Rien de specifique a un site dans
    cette detection ; seule l'interpretation des noms est propre a YouTube.
    """
    import screen_uia as su

    res: dict[str, Any] = {
        "available": False,
        "links_count": 0,
        "recommendations": [],
        "errors": 0,
        "source": "uia",
    }
    liens, err = su.collect(root, "Hyperlink", limite=limite)
    res["errors"] = err
    utiles = [
        x for x in liens
        if not x["offscreen"]
        and x["bbox"][1] >= content_top
        and x["bbox"][1] < visible[3]
        and len(x["name"].strip()) >= 8
    ]
    if not liens:
        res["reason"] = (
            "arbre indisponible au moment de l'appel" if err else "aucun lien expose"
        )
        return res
    res["available"] = True
    res["links_count"] = len(utiles)

    largeur = visible[2] - visible[0]
    seuil_droite = visible[0] + int(largeur * 0.62)
    colonne = [x for x in utiles if x["bbox"][0] >= seuil_droite]
    colonne.sort(key=lambda x: x["bbox"][1])

    # Les metadonnees ne sont PAS dans le nom du lien (mesure R-S2d) : elles
    # vivent dans des elements Text freres, juste sous lui.
    textes, err_t = su.collect(root, "Text", limite=limite)
    res["errors"] += err_t
    voisins = [
        t for t in textes
        if not t["offscreen"] and t["name"].strip() and t["bbox"][0] >= seuil_droite
    ]

    est_youtube = "youtube" in (host or "").lower()
    for x in colonne[:25]:
        entree: dict[str, Any] = {"bbox": x["bbox"], "source": "uia"}
        if est_youtube:
            entree.update(parse_youtube_link(x["name"]))
        else:
            entree["title"] = " ".join(x["name"].split())
        attacher_meta(entree, x["bbox"], voisins)
        res["recommendations"].append(entree)
    res["sidebar_texts"] = len(voisins)
    res["sidebar_detected"] = bool(colonne)
    if not utiles:
        # UIA marque hors ecran ce qui est defile hors du viewport. Mesure sur
        # la page de l'utilisateur : 428 des 439 liens etaient hors ecran, et c'est
        # correct. Un compte a zero n'est donc pas une panne.
        res["links_reason"] = (
            f"{len(liens)} liens dans l'arbre, aucun visible dans le viewport "
            "(page defilee ou contenu hors ecran)"
        )
    return res


# --------------------------------------------------------------------------- #
# Texte incruste : le seul endroit ou l'OCR est le bon outil
# --------------------------------------------------------------------------- #

# Densite de contours en dessous de laquelle on considere qu'une bande ne
# contient AUCUN texte, et qu'il est inutile d'y lancer Tesseract.
#
# Profilage R-S4b : Tesseract represente 98 a 99 % du cout de cette fonction
# (712 ms sur 720 pour une bande 2560x418 agrandie x3). Redimensionnement 1,7
# a 6 ms, conversion de couleur 1 a 2 ms, post-traitement 0,01 ms : optimiser
# ces etages n'aurait aucun effet mesurable.
#
# Reduire le nombre de pixels reduirait le cout, mais degrade la lecture : sur
# le golden reel de l'utilisateur, passer de x3 a x2 fait tomber le rappel de 0,867 a
# 0,578. Mesure faite, changement refuse.
#
# Le seul gain sans contrepartie est de NE PAS LIRE une bande qui ne contient
# rien. Sur un film en pause sans sous-titre affiche, c'est le cas le plus
# frequent, et on paie aujourd'hui pres de deux secondes pour ne rien lire.
#
# Marges mesurees (bande 2560x418) :
#   bande vide, unie, degradee ou bruitee     densite 0,00000
#   1 ligne de sous-titre minuscule et pale   densite 0,00212   soit 10,6 x le seuil
#   1 ligne ordinaire                         densite 0,00502   soit 25,1 x le seuil
#   bandes du golden reel                     densite 0,024 a 0,064
#
# Mode d'echec voulu : dans le doute, on lit. Un contour parasite suffit a
# repasser en lecture complete ; seule une bande reellement vide est sautee.
SEUIL_DENSITE_TEXTE = 0.0002


def densite_contours(frame: np.ndarray) -> float:
    """Proportion de pixels de contour. Proxy tres bon marche de "y a-t-il du
    texte ici ?".

    Mesure : 0,2 a 1 ms, contre 300 a 700 ms pour Tesseract. Une bande unie,
    degradee ou simplement bruitee rend exactement 0,00000 ; la moindre ligne
    de texte depasse 0,002.
    """
    if frame is None or frame.size == 0:
        return 0.0
    gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if max(gris.shape) > 1200:
        gris = cv2.resize(gris, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    contours = cv2.Canny(gris, 60, 180)
    return float(np.count_nonzero(contours)) / max(1, contours.size)


def read_overlay_text(
    frame: np.ndarray,
    origin: tuple[int, int],
    *,
    lang: str = "fra+eng",
    min_conf: int = 45,
    variante: str = "psm6-x3",
) -> dict[str, Any]:
    """Lit le texte incruste dans une image (sous-titres graves).

    C'est le seul contenu que l'arbre d'accessibilite ne peut pas donner :
    un sous-titre grave dans la video n'existe qu'en pixels.

    Segmentation en bloc uniforme, heritee du bake-off R-S2b. Ce reglage
    reste LOCAL a cette fonction : `ocr_screen` et la bande chrome gardent le
    leur, et le golden reel a confirme en R-S3b qu'il ne fallait PAS leur
    appliquer l'agrandissement (candidate moins bonne que la production).

    L'agrandissement reste x3. R-S4b avait mesure sur un sous-titre
    SYNTHETIQUE que x2 donnait le meme resultat pour 1,7 fois moins cher, et
    a failli l'appliquer. Le golden REEL de l'utilisateur dit l'inverse :

      echelle x3   CER moyen 3,451   rappel moyen 0,867
      echelle x2   CER moyen 3,278   rappel moyen 0,578
      echelle x1   CER moyen 2,391   rappel moyen 0,711

    Le rappel tombe de 0,867 a 0,578 en x2. Le synthetique etait du texte gras
    a fort contraste, ou tout passe. Changement refuse : qualite avant vitesse.

    Le gain de latence vient d'ailleurs, et sans contrepartie : on ne lit pas
    une bande qui ne contient aucun texte (voir SEUIL_DENSITE_TEXTE).
    """
    res: dict[str, Any] = {
        "available": False,
        "reason": "",
        "lines": [],
        "elapsed_ms": 0.0,
        "variant": variante,
        "source": "ocr",
    }
    if frame is None or frame.size == 0:
        res["reason"] = "region vide"
        return res
    try:
        import pytesseract

        import ocr_screen  # noqa: F401  (positionne tesseract_cmd)
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"OCR indisponible : {exc}"
        return res

    psm, echelle = 6, 3.0
    if "x2" in variante:
        echelle = 2.0
    if "x1" in variante:
        echelle = 1.0
    if "psm11" in variante:
        psm = 11
    res["scale"] = echelle

    # Bande sans texte : inutile de payer Tesseract pour ne rien lire.
    t_det = time.perf_counter()
    densite = densite_contours(frame)
    res["edge_density"] = round(densite, 5)
    res["detect_ms"] = round((time.perf_counter() - t_det) * 1000.0, 2)
    if densite < SEUIL_DENSITE_TEXTE:
        res["available"] = True
        res["skipped"] = True
        res["words"] = 0
        res["elapsed_ms"] = res["detect_ms"]
        res["reason"] = (
            f"aucun texte detecte dans la bande (densite de contours "
            f"{densite:.5f} < {SEUIL_DENSITE_TEXTE}) : lecture inutile"
        )
        return res

    hauteur, largeur = frame.shape[:2]
    res["megapixels"] = round(hauteur * largeur * echelle * echelle / 1e6, 2)

    t0 = time.perf_counter()
    try:
        img = (
            frame if echelle == 1.0
            else cv2.resize(frame, None, fx=echelle, fy=echelle, interpolation=cv2.INTER_CUBIC)
        )
        data = pytesseract.image_to_data(
            cv2.cvtColor(img, cv2.COLOR_BGR2RGB),
            lang=lang,
            config=f"--psm {psm}",
            output_type=pytesseract.Output.DICT,
        )
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"Tesseract KO : {exc}"
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return res
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    res["available"] = True

    mots: list[tuple[int, int, int, int, str, int]] = []
    for i in range(len(data["text"])):
        texte = (data["text"][i] or "").strip()
        try:
            conf = int(float(data["conf"][i]))
        except (TypeError, ValueError):
            conf = -1
        if not texte or conf < min_conf:
            continue
        # Remise a l'echelle 1x, puis decalage vers les coordonnees ecran.
        x = int(data["left"][i] / echelle)
        y = int(data["top"][i] / echelle)
        w = int(data["width"][i] / echelle)
        h = int(data["height"][i] / echelle)
        mots.append((x, y, w, h, texte, conf))

    from screen_content import _group_lines

    res["lines"] = _group_lines(mots, origin)
    res["words"] = len(mots)
    return res


# --------------------------------------------------------------------------- #
# Couleurs
# --------------------------------------------------------------------------- #

def colors_of(frame: np.ndarray) -> dict[str, Any]:
    """Statistiques couleur d'une region : moyenne RGB, luminance, dominante."""
    if frame is None or frame.size == 0:
        return {}
    petite = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
    b, g, r = (float(np.mean(petite[:, :, i])) for i in range(3))
    gris = cv2.cvtColor(petite, cv2.COLOR_BGR2GRAY)
    luma = float(np.mean(gris))
    # Teinte dominante : le pixel le plus frequent apres quantification.
    quant = (petite // 32 * 32).reshape(-1, 3)
    valeurs, comptes = np.unique(quant, axis=0, return_counts=True)
    dom = valeurs[int(np.argmax(comptes))]
    return {
        "mean_rgb": [round(r, 1), round(g, 1), round(b, 1)],
        "luma": round(luma, 1),
        "dominant_rgb": [int(dom[2]), int(dom[1]), int(dom[0])],
        "theme": "sombre" if luma < 110 else "clair",
    }


# --------------------------------------------------------------------------- #
# Assemblage
# --------------------------------------------------------------------------- #

def rich_browser(
    hwnd: int,
    visible: tuple[int, int, int, int],
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Couche riche pour une fenetre de navigateur. Ne leve jamais."""
    import screen_uia as su

    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "timings_ms": {},
    }
    if not su.is_available():
        out["reason"] = su.unavailable_reason() or "UIA indisponible"
        return out

    # Root frais : un element mis en cache devient invalide des que l'arbre
    # est reconstruit (COMError observe pendant ce lot).
    root = su.element_from_hwnd(hwnd)
    if root is None:
        out["reason"] = "ElementFromHandle a echoue"
        return out
    out["available"] = True

    doc = snapshot.get("document") or {}
    omnibox = None
    try:
        iuia, mod = su._uia()
        edit = root.FindFirst(mod.TreeScope_Descendants, su._type_cond(iuia, mod, "Edit"))
        if su._ok(edit):
            bb = su._bbox(edit)
            if bb:
                omnibox = list(bb)
    except Exception:  # noqa: BLE001
        omnibox = None
    out["omnibox_bbox"] = omnibox

    t0 = time.perf_counter()
    zones = chrome_zones(root, visible, omnibox)
    out["timings_ms"]["chrome_zones"] = round((time.perf_counter() - t0) * 1000.0, 1)
    out["tabs"] = zones["tabs"]
    out["bookmarks"] = zones["bookmarks"]
    out["content_top"] = zones["content_top"]
    out["chrome_errors"] = zones["errors"]
    if not zones["available"]:
        out["chrome_reason"] = zones.get("reason", "")
    if not out.get("tabs"):
        out["tabs_reason"] = (
            zones.get("tabs_reason")
            or zones.get("reason")
            or out.get("chrome_reason")
            or "aucun onglet expose"
        )

    t0 = time.perf_counter()
    page = page_links(root, visible, zones["content_top"], host=doc.get("host", ""))
    out["timings_ms"]["page_links"] = round((time.perf_counter() - t0) * 1000.0, 1)
    out["links_count"] = page["links_count"]
    out["recommendations"] = page["recommendations"]
    out["sidebar_detected"] = page.get("sidebar_detected", False)
    out["sidebar_texts"] = page.get("sidebar_texts")
    out["page_errors"] = page["errors"]
    if page.get("links_reason"):
        out["links_reason"] = page["links_reason"]
    if not page["available"]:
        out["page_reason"] = page.get("reason", "")

    return out
