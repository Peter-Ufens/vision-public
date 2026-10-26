"""Contenu ecran a l'instant T (R-S2a) : contexte + pixels + texte + sens.

Repond a la demande de l'auteur du 31/08 : "voir ce qui est affiche maintenant",
sur l'ecran 1 puis l'ecran 2, quand on le demande. Le cas d'usage de tete est
un film en pause dans Brave, pas une offre d'emploi. Ce cas reste un
scenario de securite pour les agents qui cliquent, pas le produit.

Empilement, du moins cher au plus cher :

  1. Contexte    `screen_context.get_screen_snapshot()`     ~7 ms
  2. Pixels      capture des regions utiles                 ~40 ms par region
  3. Texte       OCR Tesseract sur la bande chrome          ~340 ms
  4. Sens        VLM sur une region, OPT-IN                 non mesure ici

Trois choix de conception qui portent le module :

  - La bande "chrome" n'est pas une constante magique. Quand la fenetre est
    un navigateur, sa hauteur est deduite du bbox reel de l'omnibox, obtenu
    par UIA en R-S1b. Le texte lu ainsi contient les titres d'onglets, ce qui
    est precisement ce que l'utilisateur veut savoir d'un film en cours.
  - Le VLM ne tourne JAMAIS sur une frame 2560x1440 par defaut. Il travaille
    sur une region, et seulement si on le demande.
  - Un rectangle video noir n'est pas une panne. C'est le comportement normal
    d'un flux protege (Widevine). On le mesure, on le signale, on ne le
    contourne pas.

OUTIL A LA DEMANDE (decision I7) : un appel, un dict. Aucune boucle,
aucun thread, aucun daemon.

VIE PRIVEE : le resultat contient le texte reellement affiche a l'ecran,
c'est-a-dire des titres d'onglets, des noms de fichiers, des courriels. Ces
donnees sont personnelles. Ne jamais coller une sortie reelle dans un fichier
commite, un ticket ou une capture de preuve.

Local only. Aucun reseau.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from screen_capture import grab_region
from screen_context import (
    Monitor,
    WindowInfo,
    get_screen_snapshot,
    list_monitors,
    list_windows,
)

__all__ = [
    "DEFAULT_DEPTH",
    "DEFAULT_PROFILE",
    "DEPTHS",
    "PROFILES",
    "REGIONS",
    "get_instant_screen",
    "pick_subject_window",
    "region_stats",
]

# Regions adressables par --ocr-region / --vlm-region
REGIONS = ("chrome", "client", "full")

# Profils d'usage. Ils encodent le constat de R-S2a : les trois questions que
# l'utilisateur pose n'ont pas le meme cout, et payer l'OCR pour connaitre le titre
# d'un film revient a payer cinquante fois trop cher.
#
# Source unique : la CLI lit cette table, le smoke la verifie.
PROFILES: dict[str, dict[str, Any]] = {
    "film": {
        "with_ocr": False,
        "with_vlm": False,
        "ocr_region": "chrome",
        "vlm_region": "client",
        "pourquoi": (
            "quel film, quel site : la reponse est deja dans le contexte "
            "(titre de fenetre + host). Captures gardees pour detecter un "
            "flux protege, OCR inutile."
        ),
        "cout": "~64 ms",
    },
    "agent": {
        "with_ocr": True,
        "with_vlm": False,
        "ocr_region": "chrome",
        "vlm_region": "client",
        "pourquoi": (
            "lire ce qui est ecrit dans le bandeau : onglets ouverts, "
            "libelles d'interface. C'est le profil des agents qui doivent "
            "verifier avant d'agir."
        ),
        "cout": "~830 ms",
    },
    "full": {
        "with_ocr": True,
        "with_vlm": True,
        "ocr_region": "chrome",
        "vlm_region": "client",
        "pourquoi": (
            "tout, description VLM comprise. Exige un modele deja present "
            "localement : sinon la description est refusee avec sa raison, "
            "aucun telechargement n'est declenche."
        ),
        "cout": "OCR + duree du VLM",
    },
}
DEFAULT_PROFILE = "agent"

# Profondeurs d'analyse (R-S2c). Orthogonales aux profils : le profil dit
# POURQUOI on regarde, la profondeur dit JUSQU'OU on descend.
#
# Choix qui merite d'etre explicite : `standard` et `rich` ne lisent PAS la
# bande chrome par OCR. Les onglets et les favoris viennent d'UIA, qui les
# rend en ~60 ms avec leur titre COMPLET, la ou l'OCR coute ~300 ms et ne
# peut lire que le libelle tronque affiche. L'OCR reste en repli si l'arbre
# d'accessibilite ne repond pas, et garde une place propre en `rich` : lire
# les sous-titres incrustes, qui n'existent qu'en pixels.
DEPTHS: dict[str, dict[str, Any]] = {
    "minimal": {
        "chrome_uia": False,
        "page_uia": False,
        "overlay_ocr": False,
        "taskbar": False,
        "colors": False,
        "ocr_fallback": False,
        "pourquoi": "quelle app, quel site, quel titre. Le socle valide en R-S2a.",
        "cout": "~60 ms",
    },
    "standard": {
        "chrome_uia": True,
        "page_uia": False,
        "overlay_ocr": False,
        "taskbar": False,
        "colors": True,
        "ocr_fallback": True,
        "pourquoi": "+ onglets, favoris, URL complete, titre nettoye, couleurs.",
        "cout": "~150 ms",
    },
    "rich": {
        "chrome_uia": True,
        "page_uia": True,
        "overlay_ocr": True,
        "taskbar": True,
        "colors": True,
        "ocr_fallback": True,
        "pourquoi": "+ liens et recommandations, sous-titres incrustes, barre des taches, heure.",
        "cout": "~1 a 2 s",
    },
}
DEFAULT_DEPTH = "minimal"

# Part basse de la zone client ou l'on cherche du texte incruste. Les
# sous-titres graves vivent la ; chercher ailleurs coute cher pour rien.
BANDE_SOUSTITRES = 0.30


# Hauteur de repli de la bande chrome quand aucun omnibox n'est expose.
# Heuristique assumee : la barre de titre plus une barre d'outils.
CHROME_FALLBACK_PX = 120

# Marge sous le bas de l'omnibox, pour ne pas couper les descendantes.
CHROME_MARGIN_PX = 12

# Seuils "noir" repris de clip_screen : une seule source de verite pour la
# detection de flux protege.
try:  # pragma: no cover
    from clip_screen import BLACK_MEAN_MAX, BLACK_STD_MAX
except Exception:  # noqa: BLE001  # pragma: no cover
    BLACK_MEAN_MAX, BLACK_STD_MAX = 18.0, 12.0


# --------------------------------------------------------------------------- #
# Geometrie
# --------------------------------------------------------------------------- #

def _clip(rect: tuple[int, int, int, int], box: tuple[int, int, int, int]):
    """Intersection. Retourne None si vide."""
    x0 = max(rect[0], box[0])
    y0 = max(rect[1], box[1])
    x1 = min(rect[2], box[2])
    y1 = min(rect[3], box[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def pick_subject_window(
    monitor: Monitor,
    windows: list[WindowInfo] | None = None,
) -> WindowInfo | None:
    """La fenetre que l'utilisateur regarde sur cet ecran, ou None si le bureau.

    Priorite au premier plan si elle est sur cet ecran, sinon la plus haute
    dans l'ordre Z. On ecarte les fenetres de decor (overlays, Program
    Manager) : elles couvrent l'ecran entier et masqueraient la vraie
    reponse.
    """
    wins = windows if windows is not None else list_windows()
    candidats = [
        w
        for w in wins
        if not w.is_overlay and not w.is_cloaked and w.monitor_index == monitor.index
    ]
    if not candidats:
        return None
    for w in candidats:
        if w.is_foreground:
            return w
    return min(candidats, key=lambda w: (w.z_order if w.z_order >= 0 else 10**6))


def _chrome_band(
    win: WindowInfo,
    visible: tuple[int, int, int, int],
    snapshot: dict[str, Any] | None,
) -> tuple[tuple[int, int, int, int], str]:
    """(bbox de la bande chrome, provenance).

    Sur un navigateur, la hauteur vient du bbox reel de l'omnibox lu par UIA.
    C'est ce qui fait que la bande contient les onglets, donc les titres des
    pages ouvertes, plutot qu'une tranche arbitraire de pixels.
    """
    bottom = None
    source = "heuristique"
    if snapshot:
        doc = snapshot.get("document") or {}
        if doc:
            try:
                import screen_uia

                root = screen_uia.element_from_hwnd(win.hwnd)
                iuia, mod = screen_uia._uia()
                if root is not None and iuia is not None:
                    edit = root.FindFirst(
                        mod.TreeScope_Descendants,
                        screen_uia._type_cond(iuia, mod, "Edit"),
                    )
                    if screen_uia._ok(edit):
                        bbox = screen_uia._bbox(edit)
                        if bbox and bbox[3] > visible[1]:
                            bottom = bbox[3] + CHROME_MARGIN_PX
                            source = "uia-omnibox"
            except Exception:  # noqa: BLE001
                bottom = None
    if bottom is None:
        bottom = visible[1] + CHROME_FALLBACK_PX
    bottom = min(bottom, visible[3])
    return ((visible[0], visible[1], visible[2], bottom), source)


# --------------------------------------------------------------------------- #
# Pixels
# --------------------------------------------------------------------------- #

@dataclass
class Capture:
    region: str
    bbox: list[int]
    width: int
    height: int
    elapsed_ms: float
    mean_luma: float
    std_luma: float
    looks_black: bool
    saved: str | None = None
    frame: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("frame", None)
        return d


def region_stats(frame: np.ndarray) -> tuple[float, float, bool]:
    """(luminance moyenne, ecart-type, ressemble a un noir de flux protege).

    Meme criteres que clip_screen.is_mostly_black, appliques a une region et
    non a la frame entiere : c'est le rectangle video qui devient noir sous
    Widevine, pas la fenetre.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (160, 90))
    mean = float(np.mean(small))
    std = float(np.std(small))
    return mean, std, (mean <= BLACK_MEAN_MAX and std <= BLACK_STD_MAX)


def _capture(
    name: str,
    bbox: tuple[int, int, int, int],
    save_dir: Path | None,
    tag: str,
) -> Capture:
    t0 = time.perf_counter()
    frame = grab_region(bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1])
    elapsed = (time.perf_counter() - t0) * 1000.0
    mean, std, black = region_stats(frame)
    cap = Capture(
        region=name,
        bbox=list(bbox),
        width=int(frame.shape[1]),
        height=int(frame.shape[0]),
        elapsed_ms=round(elapsed, 2),
        mean_luma=round(mean, 2),
        std_luma=round(std, 2),
        looks_black=black,
        frame=frame,
    )
    if save_dir is not None:
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            path = save_dir / f"{tag}-{name}.png"
            cv2.imwrite(str(path), frame)
            cap.saved = str(path)
        except Exception as exc:  # noqa: BLE001
            print(f"capture non enregistree ({exc})")
    return cap


# --------------------------------------------------------------------------- #
# Texte
# --------------------------------------------------------------------------- #

def _group_lines(
    boxes: list[tuple[int, int, int, int, str, int]],
    origin: tuple[int, int],
) -> list[dict[str, Any]]:
    """Regroupe les mots OCR en lignes.

    Un mot isole ne dit rien ; une ligne "Votark - Mickey 17 (2025)" dit ce
    que l'utilisateur regarde. Le regroupement se fait sur le chevauchement vertical.
    """
    if not boxes:
        return []
    items = sorted(boxes, key=lambda b: (b[1] + b[3] / 2, b[0]))
    lignes: list[list[tuple]] = []
    for b in items:
        cy = b[1] + b[3] / 2
        place = False
        for grp in lignes:
            ref = grp[0]
            if abs(cy - (ref[1] + ref[3] / 2)) <= max(6, ref[3] * 0.6):
                grp.append(b)
                place = True
                break
        if not place:
            lignes.append([b])

    out: list[dict[str, Any]] = []
    for grp in lignes:
        grp.sort(key=lambda b: b[0])
        texte = " ".join(b[4] for b in grp).strip()
        if not texte:
            continue
        x0 = min(b[0] for b in grp) + origin[0]
        y0 = min(b[1] for b in grp) + origin[1]
        x1 = max(b[0] + b[2] for b in grp) + origin[0]
        y1 = max(b[1] + b[3] for b in grp) + origin[1]
        confs = [b[5] for b in grp]
        out.append(
            {
                "text": texte,
                "bbox": [x0, y0, x1, y1],
                "words": len(grp),
                "conf_min": min(confs),
                "conf_moy": round(sum(confs) / len(confs), 1),
            }
        )
    out.sort(key=lambda d: (d["bbox"][1], d["bbox"][0]))
    return out


def _ocr(cap: Capture, lang: str, min_conf: int) -> dict[str, Any]:
    res: dict[str, Any] = {
        "region": cap.region,
        "available": False,
        "reason": "",
        "lines": [],
        "words": 0,
        "elapsed_ms": 0.0,
    }
    if cap.looks_black:
        res["reason"] = "region noire : rien a lire (flux protege ou ecran eteint)"
        return res
    try:
        from ocr_screen import ocr_boxes
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"OCR indisponible : {exc}"
        return res
    t0 = time.perf_counter()
    try:
        boxes = ocr_boxes(cap.frame, lang, min_conf)
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"Tesseract KO : {exc}"
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return res
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    res["available"] = True
    res["lines"] = _group_lines(boxes, (cap.bbox[0], cap.bbox[1]))
    res["words"] = len(boxes)
    return res


# --------------------------------------------------------------------------- #
# Sens (opt-in)
# --------------------------------------------------------------------------- #

_vlm_cache: dict[str, Any] = {"model": None, "tried": False, "reason": ""}


def model_in_cache(model_id: str) -> bool:
    """Le modele est-il deja sur le disque ?

    Garde-fou volontaire : `from_pretrained` telechargerait plusieurs Go en
    silence au premier appel. Sur un outil a la demande, une commande qui
    part telecharger pendant un quart d'heure sans prevenir est un piege. On
    verifie d'abord, et on demande a l'utilisateur de declencher le telechargement
    explicitement s'il le veut.
    """
    import os

    # Tous les dossiers ou transformers / huggingface_hub peuvent ranger un
    # modele, pas seulement le premier. audit V1.1 (17/10) : l'ancienne version
    # s'arretait au premier trouve et ignorait TRANSFORMERS_CACHE, la ou
    # transformers 4.x charge vraiment. Sur ce poste, Moondream3 (18 Go) dort
    # dans le TRANSFORMERS_CACHE du poste depuis le 23/07, et le garde-fou disait
    # « absent » depuis le 31/08 : le VLM refusait de tourner sans raison.
    bases = [
        os.environ.get("HUGGINGFACE_HUB_CACHE"),
        os.environ.get("TRANSFORMERS_CACHE"),
        os.path.join(os.environ["HF_HOME"], "hub") if os.environ.get("HF_HOME") else None,
        os.path.join(Path.home(), ".cache", "huggingface", "hub"),
    ]
    nom = "models--" + model_id.replace("/", "--")
    for base in bases:
        if not base:
            continue
        dossier = Path(base) / nom
        if dossier.is_dir() and any(dossier.rglob("*.safetensors")):
            return True
    return False


def _default_vlm(frame: np.ndarray) -> str:
    """Moondream, charge au premier appel seulement.

    Jamais appele par defaut : le VLM est opt-in. Il exige un GPU CUDA et le
    modele deja present localement ; sans l'un des deux il leve, et l'appelant
    le signale au lieu de faire semblant.
    """
    if _vlm_cache["model"] is None:
        if _vlm_cache["tried"]:
            raise RuntimeError(_vlm_cache["reason"] or "VLM indisponible")
        _vlm_cache["tried"] = True
        try:
            from moondream_screen import DEFAULT_MODEL, FALLBACK_MODEL, MoondreamScreen

            if not (model_in_cache(DEFAULT_MODEL) or model_in_cache(FALLBACK_MODEL)):
                raise RuntimeError(
                    f"modele absent du cache local ({DEFAULT_MODEL}). "
                    "Telechargement non declenche automatiquement : plusieurs Go. "
                    "Pour l'installer volontairement, lancer une fois "
                    "src/moondream_screen.py, puis relancer avec --vlm."
                )
            _vlm_cache["model"] = MoondreamScreen(compile_model=False, local_files_only=True)
        except Exception as exc:  # noqa: BLE001
            _vlm_cache["reason"] = f"{type(exc).__name__}: {exc}"
            raise
    return str(_vlm_cache["model"].query_bgr(frame))


def _vlm(cap: Capture, vlm_fn: Callable[[np.ndarray], str] | None) -> dict[str, Any]:
    res: dict[str, Any] = {
        "region": cap.region,
        "available": False,
        "reason": "",
        "caption": None,
        "elapsed_ms": 0.0,
    }
    if cap.looks_black:
        res["reason"] = "region noire : rien a decrire (flux protege)"
        return res
    fn = vlm_fn or _default_vlm
    t0 = time.perf_counter()
    try:
        caption = fn(cap.frame)
    except Exception as exc:  # noqa: BLE001
        res["reason"] = f"{type(exc).__name__}: {exc}"
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return res
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    res["available"] = True
    res["caption"] = caption
    return res




# --------------------------------------------------------------------------- #
# Couche riche (R-S2c)
# --------------------------------------------------------------------------- #

def _enrich(
    ecran: dict[str, Any],
    win: Any,
    caps: dict[str, Any],
    snap: dict[str, Any],
    reglages: dict[str, Any],
    out_dir: Any,
    tag: str,
) -> None:
    """Ajoute la section `rich` a un ecran. Ne leve jamais.

    Chaque bloc est independant : si la barre des taches echoue, les onglets
    restent. Un bloc absent est signale par sa raison, jamais silencieux.
    """
    import screen_rich as sr

    riche: dict[str, Any] = {"depth": reglages["_nom"], "sources": {}}
    visible = tuple(ecran["regions"]["full"]["bbox"])
    proc = ecran.get("context", {}).get("process") or {}
    doc = (ecran.get("context") or {}).get("document") or {}
    est_navigateur = proc.get("category") == "browser"

    # --- titre nettoye et document complet ---------------------------------- #
    titre_brut = (ecran.get("window") or {}).get("title") or (
        (ecran.get("context") or {}).get("window") or {}
    ).get("title", "")
    titre, suffixes = sr.strip_site_suffix(titre_brut)
    riche["page"] = {"title": titre, "suffixes_retires": suffixes, "source": "window.title"}
    if doc:
        riche["document"] = {
            "url": doc.get("url_normalized") or doc.get("url_raw"),
            "url_raw": doc.get("url_raw"),
            "host": doc.get("host"),
            "scheme_suppose": doc.get("scheme_suppose", False),
            "source": "uia",
        }
        if "youtube" in (doc.get("host") or "").lower():
            riche["video"] = {"title": titre, "source": "window.title"}

    # --- couleurs ------------------------------------------------------------ #
    if reglages["colors"]:
        riche["colors"] = {
            nom: sr.colors_of(cap.frame) for nom, cap in caps.items() if cap.frame is not None
        }
        riche["sources"]["colors"] = "pixels"

    # --- chrome navigateur : onglets, favoris -------------------------------- #
    if reglages["chrome_uia"] and est_navigateur:
        t0 = time.perf_counter()
        try:
            nav = sr.rich_browser(win.hwnd, visible, snap)
        except Exception as exc:  # noqa: BLE001
            nav = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
        riche["browser"] = nav
        riche["sources"]["tabs"] = "uia" if nav.get("tabs") else "absent"
        riche["timings_ms"] = {"browser": round((time.perf_counter() - t0) * 1000.0, 1)}

        # Repli OCR : seulement si l'arbre n'a rien donne.
        if reglages["ocr_fallback"] and not nav.get("tabs") and "chrome" in caps:
            ocr = _ocr(caps["chrome"], "fra+eng", 45)
            if ocr.get("available"):
                riche["browser"]["tabs_ocr_fallback"] = [
                    {"title": ln["text"], "bbox": ln["bbox"], "source": "ocr"}
                    for ln in ocr["lines"][:12]
                ]
                riche["sources"]["tabs"] = "ocr-repli"
    elif reglages["chrome_uia"]:
        riche["browser"] = {
            "available": False,
            "reason": f"l'application n'est pas un navigateur ({proc.get('category')})",
        }

    # --- texte incruste (sous-titres) ---------------------------------------- #
    if reglages["overlay_ocr"]:
        cible = caps.get("client")
        if cible is None or cible.frame is None:
            riche["overlay_text"] = {"available": False, "reason": "aucune region client capturee"}
        elif cible.looks_black:
            riche["overlay_text"] = {
                "available": False,
                "reason": "region noire (flux protege ou scene sombre) : rien a lire",
            }
        else:
            h = cible.frame.shape[0]
            debut = max(0, int(h * (1.0 - BANDE_SOUSTITRES)))
            bande = cible.frame[debut:, :]
            origine = (cible.bbox[0], cible.bbox[1] + debut)
            res = sr.read_overlay_text(bande, origine)
            res["region_bbox"] = [origine[0], origine[1], cible.bbox[2], cible.bbox[3]]
            riche["overlay_text"] = res
            riche["subtitles"] = [ln["text"] for ln in res.get("lines", [])]
            riche["sources"]["subtitles"] = "ocr"
            if out_dir is not None:
                try:
                    import cv2 as _cv2

                    out_dir.mkdir(parents=True, exist_ok=True)
                    _cv2.imwrite(str(out_dir / f"{tag}-soustitres.png"), bande)
                except Exception:  # noqa: BLE001
                    pass

    ecran["rich"] = riche


def _enrich_cursor(
    resultat: dict[str, Any],
    monitors: Any,
    region_size: int,
    out_dir: Any,
    tag: str,
) -> None:
    """Bloc `cursor` au niveau racine. Il n'y a qu'un curseur, pas un par ecran.

    La capture de la region est faite ici et non dans `screen_cursor`, qui
    reste sans dependance image : un module Win32 pur se charge en quelques
    millisecondes, ce qui compte pour un outil appele a la demande.
    """
    import screen_cursor as sc

    t0 = time.perf_counter()
    cur = sc.get_cursor(monitors, region_size=region_size)
    cur["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 4)

    region = cur.get("region")
    if region:
        bbox = tuple(region["bbox"])
        cap = _capture("cursor", bbox, out_dir, tag)
        cur["capture"] = cap.to_dict()
        if cap.looks_black:
            cur["capture"]["note"] = (
                "region sombre : scene noire, flux protege, ou simplement une "
                "zone d'interface sombre. La mesure ne distingue pas la cause."
            )
    resultat["cursor"] = cur


def _enrich_system(resultat: dict[str, Any], reglages: dict[str, Any], monitors: Any) -> None:
    """Barre des taches et heure. Communes a tous les ecrans, donc au niveau haut."""
    import screen_taskbar as st

    resultat["system"] = st.get_system_info()
    if not reglages["taskbar"]:
        return
    t0 = time.perf_counter()
    try:
        resultat["taskbar"] = st.get_taskbars(monitors)
    except Exception as exc:  # noqa: BLE001
        resultat["taskbar"] = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    resultat["taskbar"]["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

def get_instant_screen(
    monitor: int | None = None,
    *,
    all_monitors: bool = False,
    with_ocr: bool = True,
    ocr_region: str = "chrome",
    with_vlm: bool = False,
    vlm_region: str = "client",  # decrire ce qui est affiche, pas la barre d'onglets
    save_dir: str | Path | None = None,
    ocr_lang: str = "fra+eng",
    ocr_min_conf: int = 45,
    vlm_fn: Callable[[np.ndarray], str] | None = None,
    keep_frames: bool = False,
    depth: str = DEFAULT_DEPTH,
    with_cursor: bool = False,
    cursor_region: int = 0,
    hwnd: int | None = None,
) -> dict[str, Any]:
    """Ce qui est affiche, maintenant, sur un ecran ou sur tous.

    `monitor` : index 1-based. None et `all_monitors` faux = l'ecran qui
    porte la fenetre au premier plan.

    `hwnd` : analyser CETTE fenetre, meme si elle n'est pas au premier plan
    (R-S8, U3). Sans lui, chaque ecran ne rend que sa fenetre-sujet, et une
    fenetre derriere une autre est inatteignable -- observe en R-S7 : un
    navigateur portant onze onglets etait ignore parce que l'editeur etait
    devant. `hwnd` prime alors sur `monitor` et `all_monitors`, et le dit.

    Schema du retour : context/screen-instant-schema-v2.md
    """
    t_start = time.perf_counter()
    monitors = list_monitors()
    windows = list_windows(monitors=monitors)
    tag = time.strftime("instant-%Y%m%d-%H%M%S")
    out_dir = Path(save_dir) if save_dir else None

    fenetre_imposee = None
    notes_globales: list[str] = []
    if hwnd is not None:
        fenetre_imposee = next((w for w in windows if w.hwnd == hwnd), None)
        if fenetre_imposee is None:
            cibles = []
        else:
            if all_monitors or monitor is not None:
                notes_globales.append(
                    "hwnd fourni : monitor et all_monitors sont ignores"
                )
            idx = fenetre_imposee.monitor_index
            cibles = [m for m in monitors if m.index == idx]
            if not cibles:
                # Fenetre sur aucun ecran (bureau virtuel) : on la traite
                # quand meme, rattachee a l'ecran principal pour la decoupe.
                cibles = [m for m in monitors if m.is_primary] or monitors[:1]
                notes_globales.append(
                    "fenetre rattachee a aucun ecran : decoupe sur l'ecran principal"
                )
    elif all_monitors:
        cibles = monitors
    elif monitor is not None:
        cibles = [m for m in monitors if m.index == monitor]
    else:
        fg = next((w for w in windows if w.is_foreground), None)
        idx = fg.monitor_index if fg else next((m.index for m in monitors if m.is_primary), 1)
        cibles = [m for m in monitors if m.index == idx]

    reglages = dict(DEPTHS.get(depth) or DEPTHS[DEFAULT_DEPTH])
    reglages["_nom"] = depth if depth in DEPTHS else DEFAULT_DEPTH

    resultat: dict[str, Any] = {
        "v": 2,
        "phase": "R-S2c",
        "depth": reglages["_nom"],
        "t_wall_ms": int(time.time() * 1000),
        "monitors": [
            {
                "index": m.index,
                "width": m.width,
                "height": m.height,
                "left": m.left,
                "top": m.top,
                "is_primary": m.is_primary,
            }
            for m in monitors
        ],
        "screens": [],
        "elapsed_ms": 0.0,
    }
    if notes_globales:
        resultat["notes"] = notes_globales
    if not cibles:
        resultat["error"] = (
            f"fenetre {hwnd} introuvable ou fermee" if hwnd is not None
            else f"moniteur {monitor} inexistant"
        )
        resultat["elapsed_ms"] = round((time.perf_counter() - t_start) * 1000.0, 2)
        return resultat

    for mon in cibles:
        ecran: dict[str, Any] = {
            "monitor_index": mon.index,
            "monitor": {
                "width": mon.width,
                "height": mon.height,
                "bbox": list(mon.bbox),
                "is_primary": mon.is_primary,
            },
            "subject": "desktop",
            "context": None,
            "regions": {},
            "captures": [],
            "ocr": None,
            "vlm": None,
            "notes": [],
        }
        win = fenetre_imposee if fenetre_imposee is not None else pick_subject_window(mon, windows)
        caps: dict[str, Capture] = {}
        if fenetre_imposee is not None:
            ecran["notes"].append("fenetre imposee par hwnd, pas la fenetre-sujet de l'ecran")

        if win is None:
            ecran["notes"].append("aucune fenetre applicative sur cet ecran : capture du bureau")
            caps["full"] = _capture("full", mon.bbox, out_dir, f"{tag}-ecran{mon.index}")
            ecran["regions"]["full"] = {"bbox": list(mon.bbox), "source": "moniteur"}
        else:
            ecran["subject"] = "window"
            snap = get_screen_snapshot(hwnd=win.hwnd)
            ecran["context"] = snap
            visible = _clip(tuple(win.rect_visible), mon.bbox)
            if visible is None:
                ecran["notes"].append("fenetre hors de cet ecran apres decoupe")
                visible = mon.bbox
            if tuple(win.rect_visible) != tuple(win.rect):
                ecran["notes"].append(
                    "bbox visible corrige par DWM (bordures invisibles retirees)"
                )
            chrome, chrome_src = _chrome_band(win, visible, snap)
            client = (visible[0], chrome[3], visible[2], visible[3])
            ecran["regions"]["chrome"] = {"bbox": list(chrome), "source": chrome_src}
            if client[3] > client[1]:
                ecran["regions"]["client"] = {"bbox": list(client), "source": "sous la bande chrome"}
            ecran["regions"]["full"] = {"bbox": list(visible), "source": "fenetre visible"}

            caps["chrome"] = _capture("chrome", chrome, out_dir, f"{tag}-ecran{mon.index}")
            if client[3] > client[1]:
                caps["client"] = _capture("client", client, out_dir, f"{tag}-ecran{mon.index}")

        # Regions supplementaires uniquement si demandees et pas deja prises
        for besoin, actif in ((ocr_region, with_ocr), (vlm_region, with_vlm)):
            if actif and besoin in ecran["regions"] and besoin not in caps:
                bbox = tuple(ecran["regions"][besoin]["bbox"])
                caps[besoin] = _capture(besoin, bbox, out_dir, f"{tag}-ecran{mon.index}")

        if any(c.looks_black for c in caps.values()):
            ecran["notes"].append(
                "region noire detectee : soit un flux protege (Widevine), soit "
                "une scene sombre. Dans les deux cas ce n'est pas une panne, et "
                "le contexte reste lisible. La mesure distingue le noir, pas sa "
                "cause : ne pas conclure au DRM sur ce seul signal."
            )

        if with_ocr:
            cible = caps.get(ocr_region) or caps.get("chrome") or next(iter(caps.values()), None)
            if cible is not None:
                ecran["ocr"] = _ocr(cible, ocr_lang, ocr_min_conf)
        if with_vlm:
            cible = caps.get(vlm_region) or caps.get("chrome") or next(iter(caps.values()), None)
            if cible is not None:
                ecran["vlm"] = _vlm(cible, vlm_fn)

        if win is not None and reglages["_nom"] != "minimal":
            try:
                _enrich(ecran, win, caps, ecran.get("context") or {}, reglages, out_dir,
                        f"{tag}-ecran{mon.index}")
            except Exception as exc:  # noqa: BLE001
                ecran["rich"] = {"available": False,
                                 "reason": f"{type(exc).__name__}: {exc}"}

        ecran["captures"] = [c.to_dict() for c in caps.values()]
        if keep_frames:
            ecran["_frames"] = {k: c.frame for k, c in caps.items()}
        resultat["screens"].append(ecran)

    if with_cursor or cursor_region:
        try:
            _enrich_cursor(resultat, monitors, cursor_region, out_dir, f"{tag}-curseur")
        except Exception as exc:  # noqa: BLE001
            resultat["cursor"] = {"available": False,
                                  "reason": f"{type(exc).__name__}: {exc}"}

    try:
        _enrich_system(resultat, reglages, monitors)
    except Exception as exc:  # noqa: BLE001
        resultat["system"] = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    resultat["elapsed_ms"] = round((time.perf_counter() - t_start) * 1000.0, 2)
    return resultat
