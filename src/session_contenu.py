"""Capture declenchee et lecture differee du contenu d'une fenetre (R-S13).

POURQUOI DECLENCHE, ET PAS PERIODIQUE
--------------------------------------

Mesure sur les sessions reelles de l'utilisateur (R-S12) : les fenetres ephemeres ont
vecu **1,0 s**, **2,0 s** et **0,0 s**. Un echantillon riche toutes les 60 s
les aurait TOUTES manquees ; a 10 s aussi. Une horloge plus rapide ne resout
pas le probleme -- seul un DECLENCHEMENT sur l'evenement le resout.

Le tick structurel de `session_screen` reste donc l'oeil qui detecte ; ce
module est la main qui saisit.

CAPTURER VITE, LIRE TRANQUILLEMENT
-----------------------------------

Mesure R-S13, sur ce poste :

  capture des pixels d'une fenetre     13 a 31 ms   <- seul instant critique
  lecture OCR, console ~900x500       ~380 ms      <- differable
  lecture OCR, fenetre 1 a 2 Mpx      350 a 1270 ms

Une fenetre qui vit 1 s laisse donc une marge de trente fois sur la capture.
La lecture, elle, peut se faire apres que la fenetre a disparu. C'est ce que
la dictee de l'auteur autorisait : "OCR en meme temps si possible, sinon juste
apres".

CE QU'ON NE PROMET PAS
-----------------------

Un flux video protege (Prime Video, Netflix) sort **noir** a la capture :
Widevine plus acceleration materielle. Ce n'est pas une panne de ce module et
ce n'est pas a contourner. Dans ce cas on rend `lisible: false` avec sa
raison, et on se rabat sur l'IDENTITE : quelle application, quel processus,
quel titre de fenetre. Comme un technicien au telephone qui ne voit pas votre
ecran mais peut vous dire quelle application tourne.

**On ne devine jamais une scene.** Une zone noire est rapportee comme noire.

VIE PRIVEE
----------

**Aucune image n'atteint jamais le disque.** La frame vit en memoire le temps
de la lecture, puis disparait. Deux niveaux de restitution :

  resume  (defaut)  des comptes : nombre de mots, de lignes, luminance,
                    lisible ou non. **Pas le texte.**
  texte   (opt-in)  les lignes lues.

Local, aucun reseau.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

NIVEAUX = ("none", "resume", "texte")
NIVEAU_DEFAUT = "resume"

# Classes de fenetres qui meritent une capture immediate : elles portent du
# texte et vivent parfois une seconde. Liste extensible par la CLI.
#
# `ConsoleWindowClass`         cmd.exe, conhost classique
# `CASCADIA_HOSTING_WINDOW_CLASS`  Windows Terminal
# `PseudoConsoleWindow`        conhost moderne
# `#32770`                     boite de dialogue Windows (installeurs, UAC)
CLASSES_DECLENCHEUSES: tuple[str, ...] = (
    "ConsoleWindowClass",
    "CASCADIA_HOSTING_WINDOW_CLASS",
    "PseudoConsoleWindow",
    "#32770",
)

# Filet de securite : certaines consoles changent de classe selon l'hote.
EXES_DECLENCHEURS: tuple[str, ...] = (
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "conhost.exe",
    "windowsterminal.exe",
    "wt.exe",
)

# Plafond de la file d'attente. Au-dela, on jette la plus ancienne et on le
# DIT : une capture perdue en silence serait pire que pas de capture.
FILE_MAX = 8


@dataclass
class PriseVue:
    """Une fenetre saisie au vol. La frame vit en memoire, jamais sur disque."""

    exe: str
    classe: str
    bbox: list[int]
    ecran: int | None
    t_s: float
    largeur: int
    hauteur: int
    capture_ms: float
    luma: float
    ecart_luma: float
    noir: bool
    frame: Any = field(default=None, repr=False, compare=False)

    def liberer(self) -> None:
        self.frame = None


def doit_declencher(exe: str, classe: str, *,
                    classes: tuple[str, ...] = CLASSES_DECLENCHEUSES,
                    exes: tuple[str, ...] = EXES_DECLENCHEURS) -> bool:
    """Cette fenetre merite-t-elle une capture immediate ?

    On declenche sur la CLASSE d'abord (critere de la plateforme, stable) et
    sur l'executable ensuite (filet, car l'hote de console varie).
    """
    c = (classe or "").strip()
    e = (exe or "").strip().lower()
    return c in classes or e in exes


def prechauffer() -> float:
    """Payer le cout de demarrage AVANT que la premiere fenetre apparaisse.

    Mesure R-S13, processus neuf : la premiere capture coute **367 ms**, les
    suivantes **25 ms**. Le chargement de cv2 et l'ouverture du peripherique
    de capture ne se paient qu'une fois -- mais ils se paieraient sur la
    PREMIERE fenetre declenchee, c'est-a-dire precisement celle qui peut ne
    vivre qu'une seconde.

    On les paie donc au demarrage de la session, ou personne n'attend.
    Rend le cout en millisecondes.
    """
    t0 = time.perf_counter()
    try:
        from screen_content import _capture

        _capture("prechauffe", (0, 0, 8, 8), None, "prechauffe")
    except Exception:  # noqa: BLE001
        pass
    return round((time.perf_counter() - t0) * 1000.0, 1)


def capturer(exe: str, classe: str, bbox: list[int] | tuple[int, ...],
             ecran: int | None, t_s: float) -> PriseVue | None:
    """Saisir les pixels de cette fenetre. ~13 a 31 ms. None si impossible.

    Ne leve pas : une fenetre deja fermee, hors ecran, ou une capture refusee
    par le systeme sont des conditions d'environnement, pas des pannes.
    """
    b = tuple(int(v) for v in bbox)
    if len(b) != 4 or b[2] <= b[0] or b[3] <= b[1]:
        return None
    try:
        from screen_content import _capture

        cap = _capture("fenetre", b, None, "declenche")
    except Exception:  # noqa: BLE001
        return None
    return PriseVue(
        exe=exe, classe=classe, bbox=list(b), ecran=ecran, t_s=round(t_s, 3),
        largeur=cap.width, hauteur=cap.height,
        capture_ms=round(cap.elapsed_ms, 1),
        luma=cap.mean_luma, ecart_luma=cap.std_luma, noir=cap.looks_black,
        frame=cap.frame,
    )


def identite_process(hwnd: int | None) -> dict[str, Any]:
    """Qui tourne, quand on ne peut pas voir ce qui est affiche.

    Le repli du technicien au telephone : si l'ecran ne montre rien de
    lisible (flux protege, fenetre noire), on peut au moins dire QUELLE
    application est ouverte. Meme information qu'un Gestionnaire des taches
    ou qu'un `Get-Process`, prise a la source Windows.

    Ne leve jamais : chaque champ absent est simplement absent.
    """
    out: dict[str, Any] = {"source": "win32"}
    if not hwnd:
        return out
    try:
        import win32gui
        import win32process

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        out["pid"] = int(pid)
        out["classe"] = str(win32gui.GetClassName(hwnd) or "")
        try:
            import psutil

            p = psutil.Process(pid)
            out["exe"] = p.name()
            out["demarre_depuis_s"] = round(time.time() - p.create_time(), 1)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    return out


def lire(prise: PriseVue, niveau: str = NIVEAU_DEFAUT, *,
         lang: str = "fra+eng", min_conf: int = 45,
         hwnd: int | None = None) -> dict[str, Any]:
    """Lire une prise de vue. Rend un evenement `contenu.fenetre`.

    `niveau` :
      "resume"  comptes seulement, aucun texte
      "texte"   les lignes lues

    Une region noire n'est pas lue : elle est rapportee comme non lisible,
    avec sa raison, et l'identite du processus prend le relais. Aucune scene
    n'est jamais devinee.
    """
    out: dict[str, Any] = {
        "quoi": "contenu.fenetre",
        "exe": prise.exe,
        "classe": prise.classe,
        "ecran": prise.ecran,
        "vue_a_t_s": prise.t_s,
        "taille": [prise.largeur, prise.hauteur],
        "capture_ms": prise.capture_ms,
        "luminance": prise.luma,
        "lisible": False,
        "raison": "",
    }

    if prise.frame is None:
        out["raison"] = "image liberee avant lecture"
        return out

    if prise.noir:
        # Cas Prime Video / Netflix, et aussi ecran eteint ou scene tres
        # sombre. On ne tranche PAS entre les trois : on dit ce qu'on voit.
        out["raison"] = ("zone noire : rien a lire. Flux protege (DRM), "
                         "ecran eteint ou scene tres sombre -- indiscernables "
                         "sur ce seul signal.")
        out["identite"] = identite_process(hwnd)
        return out

    try:
        from screen_content import Capture, _ocr

        cap = Capture(region="fenetre", bbox=prise.bbox, width=prise.largeur,
                      height=prise.hauteur, elapsed_ms=prise.capture_ms,
                      mean_luma=prise.luma, std_luma=prise.ecart_luma,
                      looks_black=prise.noir, saved=None, frame=prise.frame)
        res = _ocr(cap, lang, min_conf)
    except Exception as exc:  # noqa: BLE001
        out["raison"] = f"lecture impossible ({type(exc).__name__})"
        return out

    out["ocr_ms"] = round(res.get("elapsed_ms", 0.0), 1)
    if not res.get("available"):
        out["raison"] = res.get("reason") or "OCR indisponible"
        out["identite"] = identite_process(hwnd)
        return out

    lignes = res.get("lines") or []
    out["lisible"] = True
    out["mots"] = res.get("words", 0)
    out["lignes"] = len(lignes)
    if out["mots"] == 0:
        out["raison"] = "aucun texte reconnu dans cette fenetre"
        out["identite"] = identite_process(hwnd)

    if niveau == "texte":
        out["texte"] = [str(l.get("text", "")) for l in lignes]
    return out
