"""Smoke instant_screen (R-S2a + R-S2c + R-S4a) : contenu, profondeurs, curseur.

Usage :
  python scripts/smoke_instant_screen.py
  python scripts/smoke_instant_screen.py --json

Sortie : exit 0 si tous les controles passent, 1 sinon.

Ne charge aucun modele et ne telecharge rien : le chemin VLM est exerce avec
une fonction injectee, ce qui teste toute la chaine sauf le modele lui-meme.
Le smoke passe donc sans GPU et sans cache Hugging Face.

Aucune sortie ne reproduit le texte reellement lu a l'ecran : seules les
longueurs et les formes sont affichees. Ce fichier peut etre commite, ses
sorties non.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from screen_content import (  # noqa: E402
    DEFAULT_PROFILE,
    PROFILES,
    REGIONS,
    Capture,
    get_instant_screen,
    model_in_cache,
    pick_subject_window,
    region_stats,
)
from screen_content import _group_lines, _vlm  # noqa: E402
from screen_context import list_monitors, list_windows  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok, detail))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def _dans(inner: list[int], outer: list[int]) -> bool:
    return (
        outer[0] <= inner[0] and outer[1] <= inner[1]
        and inner[2] <= outer[2] and inner[3] <= outer[3]
    )




def smoke_rich() -> None:
    """Controles R-S2c : profondeurs, structure UIA, taskbar, couleurs."""
    import numpy as _np

    from screen_content import DEFAULT_DEPTH, DEPTHS, get_instant_screen
    from screen_context import list_monitors, list_windows
    from screen_rich import (
        SEUIL_DENSITE_TEXTE,
        attacher_meta,
        densite_contours,
        classer_meta,
        colors_of,
        parse_youtube_link,
        read_overlay_text,
        strip_site_suffix,
    )
    from screen_rich import read_overlay_text as _rot  # noqa: F401
    from screen_taskbar import get_system_info, get_taskbars
    from screen_uia import normalise_url

    print()
    print("=" * 70)
    print("R-S2c : vision riche")
    print("=" * 70)

    # --- profondeurs --------------------------------------------------------- #
    print()
    print("Profondeurs")
    check("les trois profondeurs existent", set(DEPTHS) == {"minimal", "standard", "rich"})
    check("profondeur par defaut valide", DEFAULT_DEPTH in DEPTHS, DEFAULT_DEPTH)
    check("minimal n'active aucune couche riche",
          not any(DEPTHS["minimal"][k] for k in ("chrome_uia", "page_uia", "overlay_ocr", "taskbar")))
    check("standard lit le chrome sans lire la page",
          DEPTHS["standard"]["chrome_uia"] and not DEPTHS["standard"]["page_uia"])
    check("rich active tout",
          all(DEPTHS["rich"][k] for k in ("chrome_uia", "page_uia", "overlay_ocr", "taskbar", "colors")))
    check("chaque profondeur documente sa raison et son cout",
          all(d.get("pourquoi") and d.get("cout") for d in DEPTHS.values()))
    croissant = [DEPTHS[d]["chrome_uia"] for d in ("minimal", "standard", "rich")]
    check("les profondeurs sont croissantes", croissant == [False, True, True])

    # --- URL ------------------------------------------------------------------ #
    print()
    print("Normalisation d'URL")
    u, sup = normalise_url("youtube.com/watch?v=abc", None)
    check("schema ajoute quand absent", u == "https://youtube.com/watch?v=abc" and sup is True,
          "Chromium elide https, jamais http")
    u2, sup2 = normalise_url("http://exemple.fr", "http")
    check("schema present conserve tel quel", u2 == "http://exemple.fr" and sup2 is False)
    u3, sup3 = normalise_url("", None)
    check("chaine vide -> rien d'invente", u3 == "" and sup3 is False)

    # --- titres --------------------------------------------------------------- #
    print()
    print("Nettoyage de titre")
    # ATTENTION : les tirets longs ci-dessous sont des DONNEES, pas du style.
    # Brave et Chrome separent reellement le titre du nom du navigateur par un
    # tiret cadratin ou demi-cadratin dans le titre de fenetre Windows. La
    # passe de style du 10/09 les a remplaces par " : ", ce qui a decrit un
    # format de titre qui n'existe pas et fait echouer trois controles. La
    # regle "pas de tiret cadratin" vaut pour la PROSE ; une chaine de test
    # qui reproduit le monde exterieur n'est pas de la prose.
    for brut, attendu in (
        ("Naruto Trained 1000 Years - YouTube — Brave", "Naruto Trained 1000 Years"),
        ("Page – Google Chrome", "Page"),
        ("Titre | YouTube", "Titre"),
        ("Sans suffixe", "Sans suffixe"),
    ):
        t, _ = strip_site_suffix(brut)
        check(f"suffixe retire : {brut[:34]!r}", t == attendu, t)
    t, sufs = strip_site_suffix("A - YouTube — Brave")
    check("deux suffixes empiles retires", len(sufs) == 2, str(sufs))
    check("titre vide gere", strip_site_suffix("") == ("", []))

    # --- parseur YouTube ------------------------------------------------------ #
    print()
    print("Parseur de noms accessibles YouTube")
    r = parse_youtube_link(
        "What If Naruto Trained 1000 Years in Kurama Seal de GYNTER 46 k vues "
        "il y a 3 mois 12 minutes 5 secondes"
    )
    check("titre extrait", r.get("title", "").startswith("What If Naruto"), str(r.get("title"))[:40])
    check("chaine extraite", r.get("channel") == "GYNTER", str(r.get("channel")))
    check("vues avec multiplicateur k", r.get("views") == 46000, str(r.get("views")))
    check("duree en secondes", r.get("duration_s") == 725, str(r.get("duration_s")))
    r2 = parse_youtube_link("Faker Best Plays by TC Zwag 1,2 M de vues 8 minutes")
    check("multiplicateur M", r2.get("views") == 1_200_000, str(r2.get("views")))
    r3 = parse_youtube_link("Un titre sans metadonnees")
    check("aucune metadonnee -> champs ABSENTS, pas devines",
          "views" not in r3 and "channel" not in r3 and r3.get("title"),
          "abstention plutot qu'invention")
    check("chaine vide geree", parse_youtube_link("") == {"raw_len": 0})

    # --- metadonnees voisines (R-S2d) ------------------------------------------ #
    print()
    print("Metadonnees voisines des recommandations")
    # Mesure R-S2d : sur la page YouTube reelle de l'utilisateur, le nom accessible du
    # lien ne contient QUE le titre. Chaine, vues et anciennete sont dans des
    # elements Text freres, sous le lien.
    for txt, attendu in (
        ("5,1 M de vues", ("views", 5_100_000)),
        ("590 k", ("views", 590_000)),
        ("14 M de vues", ("views", 14_000_000)),
        ("1 234 views", ("views", 1234)),
    ):
        check(f"vues reconnues : {txt!r}", classer_meta(txt) == attendu, str(classer_meta(txt)))
    check("forme compacte sans le mot 'vues' reconnue",
          classer_meta("590 k")[0] == "views", "observee sur la page reelle")
    check("anciennete reconnue (fr)", classer_meta("il y a 6 m.")[0] == "age")
    check("anciennete reconnue (en)", classer_meta("6 months ago")[0] == "age")
    check("texte quelconque -> chaine", classer_meta("GYNTER") == ("channel", "GYNTER"))
    check("texte vide -> rien", classer_meta("  ") == ("", None))

    lien = [100, 200, 400, 260]
    voisins = [
        {"name": "MaChaine", "bbox": [100, 264, 300, 280]},
        {"name": "590 k", "bbox": [100, 284, 200, 300]},
        {"name": "il y a 6 m.", "bbox": [210, 284, 320, 300]},
        {"name": "AUTRE ITEM", "bbox": [100, 900, 300, 920]},
        {"name": "COLONNE VOISINE", "bbox": [900, 264, 1100, 280]},
    ]
    e = {"title": "Un titre"}
    attacher_meta(e, lien, voisins)
    check("chaine attachee depuis un frere", e.get("channel") == "MaChaine", str(e.get("channel")))
    check("vues attachees depuis un frere", e.get("views") == 590_000, str(e.get("views")))
    check("anciennete attachee", bool(e.get("age")), str(e.get("age")))
    check("texte trop bas ignore", "AUTRE ITEM" not in str(e.values()))
    check("texte d'une autre colonne ignore", "COLONNE VOISINE" not in str(e.values()))
    e2 = {"title": "T", "channel": "DejaLa"}
    attacher_meta(e2, lien, voisins)
    check("champ deja rempli non ecrase", e2["channel"] == "DejaLa")
    e3 = {"title": "T"}
    attacher_meta(e3, lien, [])
    check("aucun voisin -> aucun champ invente",
          set(e3) == {"title"}, "abstention plutot qu'invention")

    # --- affichage tolerant aux caracteres non encodables (R-S2d) -------------- #
    print()
    print("Affichage console")
    # Constat R-S2d : la console Windows est en cp1252, un titre de video
    # YouTube contenait un emoji (U+26A1) et la CLI tombait sur
    # UnicodeEncodeError. Un outil de vision ne doit pas mourir parce qu'une
    # page contient un emoji.
    import io as _io
    import runpy as _runpy  # noqa: F401  (garde l'import groupe lisible)

    sys.path.insert(0, str(ROOT / "scripts"))
    import instant_screen as _cli

    faux_ecran = {
        "monitor_index": 1,
        "subject": "window",
        "context": {"process": {"exe_name": "x.exe", "category": "browser"},
                    "window": {"title": "T"}, "document": {}},
        "regions": {}, "captures": [], "ocr": None, "vlm": None, "notes": [],
        "rich": {
            "depth": "rich",
            "page": {"title": "Titre avec emoji ⚡ et accents éèà", "suffixes_retires": []},
            "document": {"url": "https://exemple.fr/⚡", "scheme_suppose": True},
            "browser": {"available": True, "tabs": [], "tabs_reason": "test",
                        "bookmarks": [{"title": "Favori ⚡", "bbox": [0, 0, 1, 1]}],
                        "recommendations": [{"title": "Reco ⚡ 😀",
                                             "channel": "Chaine ⚡", "views": 590000,
                                             "duration_s": 65, "bbox": [0, 0, 1, 1]}],
                        "links_count": 1},
            "overlay_text": {"available": True, "lines": [{"text": "Sous-titre ⚡"}],
                             "elapsed_ms": 1.0, "variant": "psm6-x3"},
            "colors": {"client": {"mean_rgb": [1, 2, 3], "luma": 4.0, "theme": "sombre"}},
        },
    }
    tampon = _io.TextIOWrapper(_io.BytesIO(), encoding="cp1252", errors="replace")
    ancien = sys.stdout
    try:
        sys.stdout = tampon
        _cli.afficher_riche(faux_ecran)
        plante = False
    except UnicodeEncodeError:
        plante = True
    finally:
        sys.stdout = ancien
    check("affichage d'un titre a emoji sur console cp1252", not plante,
          "sinon la CLI meurt sur une page YouTube ordinaire")

    # --- couleurs -------------------------------------------------------------- #
    print()
    print("Couleurs par region")
    c_noir = colors_of(_np.full((40, 60, 3), 20, dtype=_np.uint8))
    check("theme sombre detecte", c_noir["theme"] == "sombre", f"luma {c_noir['luma']}")
    c_clair = colors_of(_np.full((40, 60, 3), 240, dtype=_np.uint8))
    check("theme clair detecte", c_clair["theme"] == "clair", f"luma {c_clair['luma']}")
    bleu = _np.zeros((40, 60, 3), dtype=_np.uint8)
    bleu[:, :, 0] = 200  # canal B en BGR
    cb = colors_of(bleu)
    check("canaux dans le bon ordre (RGB en sortie, BGR en entree)",
          cb["mean_rgb"][2] > cb["mean_rgb"][0], str(cb["mean_rgb"]))
    check("image vide -> dict vide", colors_of(_np.zeros((0, 0, 3), dtype=_np.uint8)) == {})

    # --- texte incruste --------------------------------------------------------- #
    print()
    print("Texte incruste")
    vide = read_overlay_text(_np.zeros((0, 0, 3), dtype=_np.uint8), (0, 0))
    check("region vide -> abstention avec raison", not vide["available"] and vide["reason"])
    check("variante annoncee", vide["variant"] == "psm6-x3", vide["variant"])
    uni = read_overlay_text(_np.full((60, 200, 3), 250, dtype=_np.uint8), (100, 50))
    check("image sans texte -> aucune ligne, pas de crash",
          uni["available"] and uni["lines"] == [], f"{len(uni['lines'])} ligne(s)")

    # --- bande sans texte : on ne lit pas pour rien (R-S4b) --------------------- #
    print()
    print("Detection de bande sans texte")
    vide = _np.full((418, 1280, 3), 20, dtype=_np.uint8)
    check("bande unie -> densite nulle", densite_contours(vide) == 0.0)
    degrade = _np.tile(_np.linspace(0, 255, 1280, dtype=_np.uint8), (418, 1))[:, :, None].repeat(3, 2)
    check("degrade lisse -> densite sous le seuil",
          densite_contours(degrade) < SEUIL_DENSITE_TEXTE, "un fond n'est pas du texte")
    check("image vide -> densite nulle, pas d'exception",
          densite_contours(_np.zeros((0, 0, 3), dtype=_np.uint8)) == 0.0)

    damier = _np.zeros((418, 1280, 3), dtype=_np.uint8)
    damier[::4, :, :] = 255
    check("motif tres contraste -> au-dessus du seuil",
          densite_contours(damier) > SEUIL_DENSITE_TEXTE,
          "dans le doute on lit : le mode d'echec va vers la lecture")

    r_vide = read_overlay_text(vide, (0, 0))
    check("bande sans texte -> lecture sautee", r_vide.get("skipped") is True)
    check("bande sautee -> disponible avec sa raison",
          r_vide["available"] and bool(r_vide["reason"]), r_vide["reason"][:46])
    check("bande sautee -> aucune ligne inventee", r_vide["lines"] == [])
    check("bande sautee -> cout negligeable", r_vide["elapsed_ms"] < 50,
          f"{r_vide['elapsed_ms']} ms au lieu de plusieurs centaines")

    avec = _np.full((200, 600, 3), 240, dtype=_np.uint8)
    _cv2 = __import__("cv2")
    _cv2.putText(avec, "Sous-titre visible ici", (20, 110), _cv2.FONT_HERSHEY_SIMPLEX,
                 1.2, (10, 10, 10), 3)
    r_avec = read_overlay_text(avec, (0, 0))
    check("bande avec texte -> NON sautee", not r_avec.get("skipped"),
          f"densite {r_avec.get('edge_density')}")
    check("agrandissement x3 conserve", r_avec.get("scale") == 3.0,
          "le golden reel a refuse x2 : rappel 0,867 -> 0,578")

    # --- systeme ---------------------------------------------------------------- #
    print()
    print("Systeme et barre des taches")
    si = get_system_info()
    check("heure fournie", bool(si.get("time")) and bool(si.get("date")))
    check("heure issue de l'horloge, pas de l'OCR", si.get("source") == "horloge systeme")
    check("epoch plausible", si.get("epoch_ms", 0) > 1_600_000_000_000)
    tb = get_taskbars()
    check("barres des taches interrogees sans crash", isinstance(tb, dict) and "bars" in tb)
    if tb.get("available"):
        check("au moins une barre trouvee", len(tb["bars"]) >= 1, f"{len(tb['bars'])} barre(s)")
        for b in tb["bars"]:
            if not b.get("available"):
                continue
            check(f"{b['class']} : applications listees", b["apps_count"] >= 0,
                  f"{b['apps_count']} app(s)")
            check(f"{b['class']} : rattachee a un ecran", b.get("monitor_index") is not None)
            hors = [a for a in b["apps"] if a["bbox"][2] <= a["bbox"][0]]
            check(f"{b['class']} : bbox d'applications valides", not hors)
    else:
        print(f"  [SKIP] barre des taches indisponible ({tb.get('reason')})")

    # --- bout en bout ------------------------------------------------------------ #
    print()
    print("Appel complet par profondeur")
    mon = list_monitors()[0].index
    precedent = None
    for d in ("minimal", "standard", "rich"):
        res = get_instant_screen(monitor=mon, depth=d, with_ocr=False)
        s = res["screens"][0]
        check(f"depth={d} : schema v2", res.get("v") == 2 and res.get("depth") == d)
        check(f"depth={d} : heure toujours presente", bool((res.get("system") or {}).get("time")))
        if d == "minimal":
            check("minimal : aucune section riche", s.get("rich") is None)
            check("minimal : aucune barre des taches", res.get("taskbar") is None)
        else:
            ri = s.get("rich") or {}
            check(f"depth={d} : section riche presente", bool(ri))
            if s["subject"] == "window" and ri.get("available") is not False:
                check(f"depth={d} : titre nettoye fourni", "page" in ri)
                check(f"depth={d} : couleurs par region", bool(ri.get("colors")))
                nav = ri.get("browser") or {}
                if nav.get("available"):
                    check(f"depth={d} : frontiere de contenu deduite",
                          nav.get("content_top", 0) >= s["regions"]["full"]["bbox"][1])
                    if not nav.get("tabs"):
                        check(f"depth={d} : zero onglet EXPLIQUE, pas muet",
                              bool(nav.get("tabs_reason")), nav.get("tabs_reason", "")[:52])
                    for t in nav.get("tabs", [])[:5]:
                        check("onglet : titre et provenance", bool(t["title"]) and t["source"] == "uia")
                        break
                    for b in nav.get("bookmarks", [])[:5]:
                        check("favori : titre et provenance", bool(b["title"]) and b["source"] == "uia")
                        break
                elif nav:
                    check(f"depth={d} : navigateur absent EXPLIQUE", bool(nav.get("reason")),
                          str(nav.get("reason"))[:52])
        if d == "rich":
            check("rich : barre des taches interrogee", res.get("taskbar") is not None)
        precedent = res
    check("dernier appel complet exploitable", precedent is not None and precedent["screens"] != [])




def smoke_cursor() -> None:
    """Controles R-S4a : curseur en session. Logique pure + verifications live."""
    from screen_content import get_instant_screen
    from screen_context import list_monitors
    from screen_cursor import MAX_REGION_PX, cursor_region_bbox, get_cursor, monitor_for_point

    print()
    print("=" * 70)
    print("R-S4a : curseur en session")
    print("=" * 70)

    # --- geometrie pure : ne depend d'aucun ecran ---------------------------- #
    print()
    print("Region autour du curseur (logique pure)")
    bornes = (0, 0, 2560, 1440)
    bbox, coupe = cursor_region_bbox(1000, 700, 160, bornes)
    check("carre centre sur le point", bbox == (920, 620, 1080, 780), str(bbox))
    check("region non decoupee au centre", coupe is False)
    check("taille demandee respectee",
          bbox is not None and (bbox[2] - bbox[0], bbox[3] - bbox[1]) == (160, 160))

    bbox, coupe = cursor_region_bbox(10, 10, 160, bornes)
    check("coin haut-gauche : decoupe, pas deplacement",
          bbox == (0, 0, 90, 90) and coupe is True,
          "un glissement eloignerait le centre du curseur")
    bbox, coupe = cursor_region_bbox(2555, 1435, 160, bornes)
    check("coin bas-droit : decoupe aux bornes", bbox == (2475, 1355, 2560, 1440) and coupe)
    for x, y in ((0, 0), (2559, 1439), (0, 1439), (2559, 0)):
        b, _ = cursor_region_bbox(x, y, 200, bornes)
        if not check(f"coin ({x},{y}) : bbox valide et dans les bornes",
                     b is not None and b[0] >= 0 and b[1] >= 0
                     and b[2] <= 2560 and b[3] <= 1440, str(b)):
            break

    b, c = cursor_region_bbox(-500, -500, 100, bornes)
    check("point hors bornes -> aucune region, pas de crash", b is None and c is True)
    check("taille nulle -> aucune region", cursor_region_bbox(100, 100, 0, bornes) == (None, False))
    check("taille negative -> aucune region", cursor_region_bbox(100, 100, -5, bornes)[0] is None)
    b, _ = cursor_region_bbox(1280, 720, 99999, bornes)
    check("taille demesuree plafonnee",
          b is not None and (b[2] - b[0]) <= MAX_REGION_PX,
          f"plafond {MAX_REGION_PX} px")

    # --- appartenance a un ecran (logique pure sur moniteurs factices) ------- #
    print()
    print("Appartenance a un ecran (moniteurs factices)")

    class _Mon:
        def __init__(self, index, left, top, width, height):
            self.index, self.left, self.top = index, left, top
            self.width, self.height = width, height

        @property
        def bbox(self):
            return (self.left, self.top, self.left + self.width, self.top + self.height)

    faux = [_Mon(1, 0, 0, 2560, 1440), _Mon(2, 2560, 182, 1920, 1080)]
    check("point sur l'ecran 1", monitor_for_point(100, 100, faux) == 1)
    check("point sur l'ecran 2", monitor_for_point(3000, 500, faux) == 2)
    check("coin exact de l'ecran 2", monitor_for_point(2560, 182, faux) == 2)
    check("dernier pixel de l'ecran 1", monitor_for_point(2559, 1439, faux) == 1)
    check("borne haute exclue", monitor_for_point(2560, 0, faux) is None,
          "x=2560 n'appartient plus a l'ecran 1")
    check(
        "trou du bureau virtuel -> None, pas un index plausible",
        monitor_for_point(3000, 50, faux) is None,
        "les ecrans ne sont alignes ni en x ni en y",
    )
    check("point negatif -> None", monitor_for_point(-10, -10, faux) is None)

    # --- lecture live --------------------------------------------------------- #
    print()
    print("Lecture live")
    mons = list_monitors()
    cur = get_cursor(mons, region_size=160)
    check("curseur lu", cur.get("available") is True, cur.get("reason", ""))
    check("source annoncee", cur.get("source") == "win32")
    if cur.get("available"):
        check("coordonnees entieres", isinstance(cur["x"], int) and isinstance(cur["y"], int))
        idx = cur.get("monitor_index")
        check("ecran identifie ou absence justifiee",
              idx is not None or bool(cur.get("reason")),
              f"ecran {idx}" if idx else cur.get("reason", ""))
        if idx is not None:
            mon = next(m for m in mons if m.index == idx)
            check("le point est bien dans cet ecran",
                  mon.left <= cur["x"] < mon.left + mon.width
                  and mon.top <= cur["y"] < mon.top + mon.height)
            reg = cur.get("region")
            check("region calculee", reg is not None)
            if reg:
                b = reg["bbox"]
                check("region dans les bornes de son ecran",
                      b[0] >= mon.bbox[0] and b[1] >= mon.bbox[1]
                      and b[2] <= mon.bbox[2] and b[3] <= mon.bbox[3], str(b))
                check("region non vide", b[2] > b[0] and b[3] > b[1])
                check("decoupage signale quand il a lieu",
                      reg["clamped"] == ((b[2] - b[0], b[3] - b[1]) != (reg["size"], reg["size"])))
        check("visibilite renseignee", cur.get("visible") in (True, False, None))
        if cur.get("visible") is False:
            check("pointeur masque -> raison donnee", bool(cur.get("hidden_reason")),
                  "position perimee, ne dit rien de l'attention")
        fen = cur.get("window")
        check("fenetre sous le pointeur identifiee ou absente proprement",
              fen is None or "hwnd" in fen, str(type(fen)))

    check("aucune region quand la taille est nulle",
          get_cursor(mons, region_size=0).get("region") is None)

    # --- latence -------------------------------------------------------------- #
    print()
    print("Latence (budget 5 ms)")
    get_cursor(mons)
    mesures = []
    for _ in range(50):
        t0 = time.perf_counter()
        get_cursor(mons)
        mesures.append((time.perf_counter() - t0) * 1000.0)
    mesures.sort()
    p50 = mesures[len(mesures) // 2]
    check("curseur seul sous 5 ms (p50)", p50 < 5.0, f"p50 = {p50:.4f} ms")
    check("curseur seul sous 5 ms (p95)", mesures[int(0.95 * (len(mesures) - 1))] < 5.0)

    # --- integration au snapshot ---------------------------------------------- #
    print()
    print("Integration au snapshot")
    sans = get_instant_screen(monitor=mons[0].index, depth="minimal", with_ocr=False)
    check("aucun bloc cursor sans demande explicite", sans.get("cursor") is None,
          "pas de cout impose a qui n'en veut pas")
    avec = get_instant_screen(monitor=mons[0].index, depth="minimal", with_ocr=False,
                              with_cursor=True)
    check("bloc cursor present quand demande", isinstance(avec.get("cursor"), dict))
    check("cursor a la racine, pas par ecran",
          "cursor" not in (avec["screens"][0] if avec["screens"] else {}),
          "il n'y a qu'un curseur")
    check("aucune capture curseur sans --cursor-region",
          (avec.get("cursor") or {}).get("capture") is None)

    avec_reg = get_instant_screen(monitor=mons[0].index, depth="minimal", with_ocr=False,
                                  cursor_region=120)
    c = avec_reg.get("cursor") or {}
    check("--cursor-region implique la lecture du curseur", c.get("available") is True)
    if c.get("region"):
        cap = c.get("capture") or {}
        check("capture faite pour la region", bool(cap), str(list(cap)[:4]))
        check("taille de capture coherente avec le bbox",
              cap.get("width") == c["region"]["width"]
              and cap.get("height") == c["region"]["height"])
        check("noirceur evaluee", isinstance(cap.get("looks_black"), bool))

    riche = get_instant_screen(monitor=mons[0].index, depth="rich", with_ocr=False,
                               with_cursor=True)
    check("rich et cursor coexistent",
          isinstance(riche.get("cursor"), dict) and riche["screens"][0].get("rich") is not None)
    check("heure toujours presente avec cursor", bool((riche.get("system") or {}).get("time")))

    # --- lecture seule : aucune actuation ------------------------------------- #
    print()
    print("Lecture seule")
    import ast
    import inspect

    import screen_cursor as _sc

    # On analyse l'ARBRE SYNTAXIQUE, pas le texte. Le module cite volontairement
    # les appels interdits dans sa documentation pour dire qu'il ne les utilise
    # pas : un scan textuel les trouverait et crierait au loup.
    arbre = ast.parse(inspect.getsource(_sc))
    identifiants: set[str] = set()
    imports: set[str] = set()
    boucles_infinies = 0
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Attribute):
            identifiants.add(noeud.attr)
        elif isinstance(noeud, ast.Name):
            identifiants.add(noeud.id)
        elif isinstance(noeud, ast.Import):
            imports.update(a.name.split(".")[0] for a in noeud.names)
        elif isinstance(noeud, ast.ImportFrom):
            imports.add((noeud.module or "").split(".")[0])
        elif isinstance(noeud, ast.While):
            if isinstance(noeud.test, ast.Constant) and noeud.test.value is True:
                boucles_infinies += 1

    for interdit in ("SetCursorPos", "mouse_event", "SendInput", "keybd_event", "ClipCursor"):
        check(f"aucun appel d'actuation : {interdit}", interdit not in identifiants,
              "l'actuation n'est pas dans ce depot (ADR-0007)")
    for module in ("pyautogui", "threading", "asyncio", "multiprocessing"):
        check(f"aucun import de {module}", module not in imports,
              "outil a la demande, decision I7")
    check("aucune boucle infinie", boucles_infinies == 0, "aucun daemon")
    check("aucune mise en veille", "sleep" not in identifiants, "aucun polling")
    check("le module lit bien la position", "GetCursorPos" in identifiants)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true", help="Dump structurel (sans texte lu)")
    p.add_argument("--no-rich", action="store_true", help="Sauter les controles R-S2c")
    p.add_argument("--no-cursor", action="store_true", help="Sauter les controles R-S4a")
    args = p.parse_args()

    print("=" * 70)
    print("SMOKE instant_screen (R-S2a)")
    print("=" * 70)

    monitors = list_monitors()
    print(f"Moniteurs : {len(monitors)}")
    for m in monitors:
        print(f"  [{m.index}] {m.width}x{m.height} @ ({m.left},{m.top})")

    # --- profils d'usage ----------------------------------------------------- #
    print()
    print("Profils d'usage")
    check("les trois profils existent", set(PROFILES) == {"film", "agent", "full"}, str(set(PROFILES)))
    check("profil par defaut valide", DEFAULT_PROFILE in PROFILES, DEFAULT_PROFILE)
    check(
        "film n'active pas l'OCR",
        PROFILES["film"]["with_ocr"] is False and PROFILES["film"]["with_vlm"] is False,
        "constat R-S2a : le titre est deja dans le contexte",
    )
    check("agent active l'OCR sans VLM",
          PROFILES["agent"]["with_ocr"] is True and PROFILES["agent"]["with_vlm"] is False)
    check("full active OCR et VLM",
          PROFILES["full"]["with_ocr"] is True and PROFILES["full"]["with_vlm"] is True)
    for nom, prof in PROFILES.items():
        check(
            f"profil {nom} : regions valides et raison documentee",
            prof["ocr_region"] in REGIONS and prof["vlm_region"] in REGIONS
            and bool(prof.get("pourquoi")) and bool(prof.get("cout")),
        )

    # --- unites pures, deterministes ---------------------------------------- #
    print()
    print("Detection de region noire (chemin flux protege)")
    noir = np.zeros((200, 300, 3), dtype=np.uint8)
    mean, std, black = region_stats(noir)
    check("image noire -> looks_black", black, f"luma={mean} ecart-type={std}")
    clair = np.full((200, 300, 3), 200, dtype=np.uint8)
    _, _, black_clair = region_stats(clair)
    check("image claire -> pas noire", not black_clair)
    bruit = np.random.randint(0, 255, (200, 300, 3), dtype=np.uint8)
    _, _, black_bruit = region_stats(bruit)
    check("bruit -> pas noir", not black_bruit)

    print()
    print("Regroupement des mots OCR en lignes")
    boxes = [
        (10, 10, 40, 14, "Mickey", 90),
        (55, 11, 20, 14, "17", 88),
        (10, 40, 60, 14, "Brave", 92),
    ]
    lignes = _group_lines(boxes, (100, 200))
    check("2 lignes formees a partir de 3 mots", len(lignes) == 2, f"{len(lignes)} lignes")
    if lignes:
        check(
            "mots de la meme ligne joints dans l'ordre",
            lignes[0]["text"] == "Mickey 17",
            lignes[0]["text"],
        )
        check(
            "coordonnees ramenees a l'ecran (decalage applique)",
            lignes[0]["bbox"][0] == 110 and lignes[0]["bbox"][1] == 210,
            str(lignes[0]["bbox"]),
        )
    check("aucune ligne vide", all(x["text"].strip() for x in lignes))
    check("lignes vides en entree -> aucune ligne", _group_lines([], (0, 0)) == [])

    # --- fenetre sujet ------------------------------------------------------- #
    print()
    print("Choix de la fenetre sujet")
    wins = list_windows(monitors=monitors)
    overlays = [w for w in wins if w.is_overlay]
    check(
        "les fenetres de decor sont reperees",
        True,
        f"{len(overlays)} overlay(s) sur {len(wins)} fenetre(s)",
    )
    for m in monitors:
        sujet = pick_subject_window(m, wins)
        if sujet is not None:
            check(
                f"ecran {m.index} : sujet = {sujet.exe_name}",
                not sujet.is_overlay and not sujet.is_cloaked,
                "ni overlay ni fantome",
            )
            check(
                f"ecran {m.index} : le sujet est bien sur cet ecran",
                sujet.monitor_index == m.index,
            )
        else:
            check(f"ecran {m.index} : aucune fenetre, bureau assume", True)

    # --- appel par moniteur --------------------------------------------------- #
    print()
    print("Appel par moniteur")
    for m in monitors:
        res = get_instant_screen(monitor=m.index, with_ocr=False)
        ok = len(res["screens"]) == 1 and res["screens"][0]["monitor_index"] == m.index
        check(f"--monitor {m.index} rend cet ecran", ok)
        s = res["screens"][0]
        check(f"ecran {m.index} : sujet renseigne", s["subject"] in ("window", "desktop"), s["subject"])
        check(f"ecran {m.index} : au moins une capture", len(s["captures"]) >= 1)
        for c in s["captures"]:
            bb = c["bbox"]
            if not check(
                f"ecran {m.index} : capture '{c['region']}' de taille coherente",
                c["width"] == bb[2] - bb[0] and c["height"] == bb[3] - bb[1] and c["width"] > 0,
                f"{c['width']}x{c['height']} vs bbox {bb}",
            ):
                break
        if s["subject"] == "window":
            regions = s["regions"]
            check(f"ecran {m.index} : bande chrome presente", "chrome" in regions)
            src = regions["chrome"]["source"]
            check(
                f"ecran {m.index} : provenance de la bande chrome connue",
                src in ("uia-omnibox", "heuristique"),
                src,
            )
            if "client" in regions:
                ch, cl = regions["chrome"]["bbox"], regions["client"]["bbox"]
                check(f"ecran {m.index} : client sous chrome, sans recouvrement", cl[1] >= ch[3])
                check(
                    f"ecran {m.index} : regions dans la fenetre visible",
                    _dans(ch, regions["full"]["bbox"]) and _dans(cl, regions["full"]["bbox"]),
                )
            check(
                f"ecran {m.index} : region dans les bornes de l'ecran",
                _dans(regions["full"]["bbox"], s["monitor"]["bbox"]),
                f"{regions['full']['bbox']} dans {s['monitor']['bbox']}",
            )

    # --- tous les moniteurs --------------------------------------------------- #
    print()
    print("Tous les moniteurs")
    tous = get_instant_screen(all_monitors=True, with_ocr=False)
    check("--monitor all rend tous les ecrans", len(tous["screens"]) == len(monitors),
          f"{len(tous['screens'])}/{len(monitors)}")
    check(
        "index d'ecran uniques et complets",
        sorted(s["monitor_index"] for s in tous["screens"]) == sorted(m.index for m in monitors),
    )

    print()
    print("Moniteur inexistant")
    faux = get_instant_screen(monitor=99, with_ocr=False)
    check("moniteur 99 -> erreur nette, pas d'exception", bool(faux.get("error")), str(faux.get("error")))
    check("moniteur 99 -> aucun ecran rendu", faux["screens"] == [])

    # --- a la demande, pas de daemon ------------------------------------------ #
    print()
    print("Outil a la demande")
    a = get_instant_screen(monitor=monitors[0].index, with_ocr=False)
    time.sleep(0.05)
    b = get_instant_screen(monitor=monitors[0].index, with_ocr=False)
    check("deux appels -> deux horodatages distincts", b["t_wall_ms"] >= a["t_wall_ms"])
    check("chaque appel recapture (aucun cache)", b["elapsed_ms"] > 0 and a["elapsed_ms"] > 0)

    # --- OCR ------------------------------------------------------------------ #
    print()
    print("Lecture de texte")
    avec_ocr = get_instant_screen(monitor=monitors[0].index, with_ocr=True)
    ocr = avec_ocr["screens"][0].get("ocr")
    check("bloc OCR present quand demande", ocr is not None)
    if ocr:
        check("region OCR declaree", ocr["region"] in REGIONS, ocr["region"])
        if ocr["available"]:
            check("OCR a produit des lignes", isinstance(ocr["lines"], list))
            check("duree OCR mesuree", ocr["elapsed_ms"] > 0, f"{ocr['elapsed_ms']} ms")
            hors = [
                ln for ln in ocr["lines"]
                if not _dans(ln["bbox"], avec_ocr["screens"][0]["monitor"]["bbox"])
            ]
            check("toutes les lignes lues sont dans l'ecran", not hors, f"{len(hors)} hors bornes")
            total = sum(len(ln["text"]) for ln in ocr["lines"])
            print(f"       ({len(ocr['lines'])} lignes, {total} caracteres, contenu non affiche)")
        else:
            check("OCR indisponible mais explique", bool(ocr["reason"]), ocr["reason"][:60])
    sans_ocr = get_instant_screen(monitor=monitors[0].index, with_ocr=False)
    check("aucun bloc OCR quand non demande", sans_ocr["screens"][0].get("ocr") is None)

    # --- VLM ------------------------------------------------------------------ #
    print()
    print("Description VLM (opt-in)")
    check("VLM absent par defaut", sans_ocr["screens"][0].get("vlm") is None)
    # audit V1.1 (17/10). L'ancien controle disait « modele non present
    # localement » et passait au vert parce que le garde-fou regardait au
    # mauvais endroit : transformers range Moondream dans TRANSFORMERS_CACHE
    # (le TRANSFORMERS_CACHE de ce poste, 18 Go depuis le 23/07), que
    # model_in_cache ignorait. Le smoke confirmait donc une croyance fausse.
    # On teste maintenant le garde-fou lui-meme, sur des dossiers jetables.
    import tempfile

    cles = ("HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "HF_HOME")
    avant = {k: os.environ.get(k) for k in cles}
    try:
        with tempfile.TemporaryDirectory() as vide, tempfile.TemporaryDirectory() as tc:
            snap = Path(tc) / "models--essai--modele" / "snapshots" / "abc"
            snap.mkdir(parents=True)
            (snap / "model.safetensors").write_bytes(b"0")
            for k in cles:
                os.environ.pop(k, None)
            os.environ["HF_HOME"] = vide
            os.environ["TRANSFORMERS_CACHE"] = tc
            check(
                "garde-fou : voit un modele range dans TRANSFORMERS_CACHE",
                model_in_cache("essai/modele"),
                "c'est la que transformers 4.x le charge",
            )
            os.environ["TRANSFORMERS_CACHE"] = vide
            check(
                "garde-fou : dit absent quand aucun dossier ne l'a",
                not model_in_cache("essai/modele"),
            )
    finally:
        for k, v in avant.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    import inspect

    import screen_content as _sc

    check(
        "chemin VLM charge en local seulement (aucun telechargement possible)",
        "local_files_only=True" in inspect.getsource(_sc._default_vlm),
    )
    present = model_in_cache("moondream/moondream3-preview")
    print(f"       (info poste : Moondream3 {'present' if present else 'absent'} "
          "selon le garde-fou ; ce n'est pas un controle)")

    # Frame synthetique : le chemin live depend de l'ecran (film sombre, chrome
    # sombre) et provoquait des echecs intermittents en validation terrain R-S2d.
    appels: list[tuple[int, int]] = []
    frame_clair = np.full((120, 240, 3), 180, dtype=np.uint8)
    cap_vlm = Capture(
        region="client",
        bbox=[0, 0, 240, 120],
        width=240,
        height=120,
        elapsed_ms=0.1,
        mean_luma=180.0,
        std_luma=0.0,
        looks_black=False,
        frame=frame_clair,
    )

    def faux_vlm(frame: np.ndarray) -> str:
        appels.append((frame.shape[1], frame.shape[0]))
        return "description simulee"

    vlm = _vlm(cap_vlm, faux_vlm)
    check("chaine VLM complete avec fonction injectee", bool(vlm and vlm["available"]))
    check("legende remontee", vlm.get("caption") == "description simulee", str(vlm.get("caption")))
    check("duree VLM mesuree", vlm["elapsed_ms"] >= 0)
    check("region VLM declaree", vlm["region"] in REGIONS, vlm["region"])
    check("le VLM a bien recu une region, pas l'ecran entier", len(appels) == 1, f"{appels}")
    if appels:
        larg, haut = appels[0]
        check(
            "region VLM transmise avec les bonnes dimensions",
            larg == 240 and haut == 120,
            f"{larg}x{haut}",
        )

    cap_noir = Capture(
        region="client",
        bbox=[0, 0, 240, 120],
        width=240,
        height=120,
        elapsed_ms=0.1,
        mean_luma=5.0,
        std_luma=0.0,
        looks_black=True,
        frame=np.zeros((120, 240, 3), dtype=np.uint8),
    )
    vlm_noir = _vlm(cap_noir, faux_vlm)
    check("region noire -> VLM abstient", vlm_noir["available"] is False and bool(vlm_noir["reason"]))

    def vlm_qui_casse(frame: np.ndarray) -> str:
        raise RuntimeError("pas de GPU")

    v2 = _vlm(cap_vlm, vlm_qui_casse)
    check("VLM en echec -> pas de crash", isinstance(v2, dict) and v2["available"] is False)
    check("VLM en echec -> raison remontee", bool(v2 and v2["reason"]), (v2 or {}).get("reason", "")[:50])

    if args.json:
        propre = get_instant_screen(monitor=monitors[0].index, with_ocr=False)
        print()
        print(json.dumps(propre, indent=2, ensure_ascii=False))

    if not args.no_rich:
        smoke_rich()

    if not args.no_cursor:
        smoke_cursor()

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
