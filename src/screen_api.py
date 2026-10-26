"""Contrat public de la vision ecran (R-S5).

Point d'entree unique et stable pour les consommateurs : agents
et scripts tiers. Facade mince : la logique reste dans
`screen_content` et `screen_context`, rien n'est duplique ici.

DEUX NIVEAUX, ET LEUR NOM PRETAIT A CONFUSION
---------------------------------------------

`screen_context.get_screen_snapshot()` s'appelle "snapshot" mais ne rend que
le CONTEXTE : quelle application, quel document, quelle fenetre. Il ne
regarde aucun pixel. Ce module le reexporte sous un nom qui le dit :

    get_context_snapshot()   contexte seul, ~7 ms, sans cv2
    get_vision_snapshot()    vision complete, pixels compris
    list_controls()          ce qui est cliquable, 27 a 215 ms, sans cv2

Le premier repond a "ou est l'utilisateur". Le second a "qu'est-ce qui est affiche".
Le troisieme (R-S8) a "que puis-je cliquer ici" -- la question inverse de
`get_context_snapshot(target=...)`, qui cherche un controle deja nomme.
Un agent qui veut seulement savoir sur quel site il se trouve n'a aucune
raison de payer une capture d'ecran.

Cout mesure : le contexte seul evite de charger cv2 (159 ms d'import sur ce
poste). L'import de ce module ne charge donc RIEN de lourd tant qu'aucune
fonction n'est appelee.

GARANTIES DU CONTRAT
--------------------

  1. Retour JSON-serialisable. Aucune image, aucun tableau numpy ne sort de
     l'API : `keep_frames` est force a faux et n'est pas exposable.
  2. Aucune interface graphique. Ni `cv2.imshow`, ni `screen_vision`, ni
     fenetre OpenCV sur le chemin API. Verifie par analyse syntaxique dans
     le smoke.
  3. Aucune actuation. Lecture seule : rien n'est clique, deplace ni saisi.
     Interdit jusqu'aux jalons G1 a G7.
  4. Aucun daemon. Un appel, une reponse (decision I7). Le rythme des
     appels appartient a l'appelant.
  5. Ne leve pas sur une condition d'environnement. Moniteur absent, arbre
     d'accessibilite indisponible, OCR manquant : le champ concerne porte
     `available: false` et une `reason`. Les erreurs de programmation, elles,
     remontent normalement.
  6. Abstention plutot qu'invention. Un champ absent est absent ; aucun bbox,
     aucune URL, aucun texte n'est devine.

SCHEMAS
-------

  contexte : context/screen-context-schema-v1.md    (`v: 1`)
  vision   : context/screen-instant-schema-v2.md    (`v: 2`)
  controles: context/screen-controls-schema-v1.md   (`v: 1`)

VIE PRIVEE
----------

Le retour contient ce qui est reellement affiche : URL, titres d'onglets,
favoris, texte incruste. Donnees personnelles. Un consommateur qui les
journalise doit le faire localement et de facon retenue.

Local only. Aucun reseau.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "API_VERSION",
    "SCHEMA_CONTEXTE",
    "SCHEMA_CONTROLES",
    "SCHEMA_VISION",
    "describe_contract",
    "get_context_snapshot",
    "get_vision_snapshot",
    "list_controls",
]

# Version du CONTRAT, distincte de la version des schemas. Elle change quand
# une signature ou une garantie change, pas quand un champ est ajoute.
#
# 1.1 (R-S8) : ajout de `list_controls` et du parametre `hwnd` sur
# `get_vision_snapshot`. Aucune signature existante n'est modifiee, aucune
# garantie n'est retiree : un appelant ecrit pour 1.0 fonctionne tel quel.
API_VERSION = "1.1"

SCHEMA_CONTEXTE = 1
SCHEMA_VISION = 2
SCHEMA_CONTROLES = 1


def get_context_snapshot(
    *,
    hwnd: int | None = None,
    target: str | None = None,
    with_uia: bool = True,
) -> dict[str, Any]:
    """Contexte seul : quelle application, quel document, quelle fenetre.

    ~7 ms. Ne capture aucun pixel et **ne charge pas cv2**.

    `target` : nom d'un controle a localiser (ex "Postuler"). Absent de la
    page, le champ `target` vaut None et `target_found` est faux. Aucun bbox
    n'est jamais devine.

    Schema : context/screen-context-schema-v1.md

    >>> ctx = get_context_snapshot()
    >>> ctx["process"]["exe_name"], ctx["confidence"]
    """
    from screen_context import get_screen_snapshot

    return get_screen_snapshot(hwnd=hwnd, target=target, with_uia=with_uia)


def list_controls(
    hwnd: int | None = None,
    *,
    roles: list[str] | tuple[str, ...] | None = None,
    scope: str = "window",
    limit: int = 400,
    with_occlusion: bool = True,
) -> dict[str, Any]:
    """Que puis-je cliquer dans cette fenetre ? Retour JSON-serialisable.

    Complement de `get_context_snapshot(target=...)`, qui repond a la
    question inverse : "ou est le bouton que je nomme deja ?".

    `hwnd`  None = la fenetre au premier plan.
    `roles` None = les douze roles actionnables. Restreindre accelere.
    `scope` "window" (defaut, chrome du navigateur compris) ou "document".

    Chaque controle porte `name`, `role`, `bbox` (ecran absolu), `class`,
    `in_document` et `occluded`.

    DEUX CHAMPS A NE PAS IGNORER AVANT DE CLIQUER :

      `occluded: true`   un clic au centre atteindrait une AUTRE fenetre.
      `occluded: null`   le gestionnaire de fenetres n'a pas repondu : on ne
                         sait pas, et on ne tranche pas.
      `in_document`      faux = ce controle appartient au chrome de
                         l'application (onglet, barre d'outils). Il ne sera
                         PAS retrouve par `get_context_snapshot(target=...)`,
                         qui cherche dans les documents.

    Cout mesure (poste de mesure) : 27 a 215 ms selon la densite de la fenetre,
    plus 0,1 a 1 ms pour l'occultation.

    VIE PRIVEE : les noms de controles sont du contenu d'ecran.
    """
    from screen_context import list_monitors, list_windows

    out: dict[str, Any] = {
        "v": SCHEMA_CONTROLES,
        "available": False,
        "reason": "",
        "hwnd": hwnd,
        "controls": [],
        "counts": {},
    }
    monitors = list_monitors()
    fenetres = list_windows(monitors=monitors)
    if hwnd is None:
        cible = next((w for w in fenetres if w.is_foreground), None)
        if cible is None:
            out["reason"] = "aucune fenetre au premier plan"
            return out
    else:
        cible = next((w for w in fenetres if w.hwnd == hwnd), None)
        if cible is None:
            out["reason"] = f"fenetre {hwnd} introuvable ou fermee"
            return out

    import screen_uia

    inv = screen_uia.inventory(
        cible.hwnd,
        roles=roles,
        scope=scope,
        limite=max(1, int(limit)),
        monitors=monitors,
        window_rect=tuple(cible.rect_visible),
        with_occlusion=with_occlusion,
    )
    inv["hwnd"] = cible.hwnd
    inv["exe_name"] = cible.exe_name
    inv["monitor_index"] = cible.monitor_index
    inv["v"] = SCHEMA_CONTROLES
    return inv


def get_vision_snapshot(
    monitor: int | None = None,
    *,
    hwnd: int | None = None,
    all_monitors: bool = False,
    depth: str | None = None,
    profile: str | None = None,
    with_cursor: bool = False,
    cursor_region: int = 0,
    save_dir: str | None = None,
    with_ocr: bool | None = None,
    ocr_region: str | None = None,
    with_vlm: bool | None = None,
    vlm_region: str | None = None,
    ocr_lang: str = "fra+eng",
    ocr_min_conf: int = 45,
) -> dict[str, Any]:
    """Ce qui est affiche, maintenant. Retour JSON-serialisable.

    `monitor`   index 1-based. None et `all_monitors` faux : l'ecran qui
                porte la fenetre au premier plan.
    `hwnd`      analyser CETTE fenetre, meme si elle n'est pas au premier
                plan. Prime sur `monitor` et `all_monitors`, et le signale
                dans `notes`. Fenetre fermee : `error`, pas d'exception.
    `depth`     "minimal" (~60 ms) | "standard" | "rich" (~1 a 2 s).
    `profile`   "film" | "agent" | "full" : pose les defauts d'OCR et de VLM.
                Les arguments explicites priment toujours sur le profil.
    `with_cursor` / `cursor_region`  position du pointeur, et capture
                optionnelle d'un carre autour de lui.

    Schema : context/screen-instant-schema-v2.md

    >>> from screen_api import get_vision_snapshot
    >>> snap = get_vision_snapshot(monitor=1, depth="rich", with_cursor=True)
    >>> snap["v"], len(snap["screens"])
    (2, 1)
    """
    from screen_content import DEFAULT_DEPTH, DEFAULT_PROFILE, PROFILES, get_instant_screen

    prof = PROFILES[profile or DEFAULT_PROFILE]
    return get_instant_screen(
        monitor=monitor,
        hwnd=hwnd,
        all_monitors=all_monitors,
        depth=depth or DEFAULT_DEPTH,
        with_ocr=prof["with_ocr"] if with_ocr is None else with_ocr,
        ocr_region=ocr_region or prof["ocr_region"],
        with_vlm=prof["with_vlm"] if with_vlm is None else with_vlm,
        vlm_region=vlm_region or prof["vlm_region"],
        save_dir=save_dir,
        ocr_lang=ocr_lang,
        ocr_min_conf=ocr_min_conf,
        with_cursor=with_cursor or bool(cursor_region),
        cursor_region=max(0, int(cursor_region)),
        # Non exposable : une frame numpy briserait la garantie
        # JSON-serialisable du contrat.
        keep_frames=False,
    )


def describe_contract() -> dict[str, Any]:
    """Ce que l'API garantit, sous forme lisible par un programme.

    Permet a un consommateur de verifier a l'execution la version du contrat
    et les profondeurs disponibles, sans lire la documentation.
    """
    from screen_content import DEFAULT_DEPTH, DEFAULT_PROFILE, DEPTHS, PROFILES, REGIONS
    from screen_uia import ROLES_ACTIONNABLES

    return {
        "api_version": API_VERSION,
        "schemas": {
            "context": {"v": SCHEMA_CONTEXTE, "doc": "context/screen-context-schema-v1.md"},
            "vision": {"v": SCHEMA_VISION, "doc": "context/screen-instant-schema-v2.md"},
            "controls": {"v": SCHEMA_CONTROLES, "doc": "context/screen-controls-schema-v1.md"},
        },
        "roles_actionnables": list(ROLES_ACTIONNABLES),
        "depths": {n: {"cout": d["cout"], "pourquoi": d["pourquoi"]} for n, d in DEPTHS.items()},
        "default_depth": DEFAULT_DEPTH,
        "profiles": {n: {"cout": p["cout"], "pourquoi": p["pourquoi"]} for n, p in PROFILES.items()},
        "default_profile": DEFAULT_PROFILE,
        "regions": list(REGIONS),
        "garanties": [
            "retour JSON-serialisable",
            "aucune interface graphique",
            "aucune actuation, lecture seule",
            "aucun daemon, un appel une reponse",
            "ne leve pas sur une condition d'environnement",
            "abstention plutot qu'invention",
        ],
    }
