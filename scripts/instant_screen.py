"""Instant T : ce qui est affiche maintenant, ecran par ecran (R-S2a).

Usage :
  python scripts/instant_screen.py                    # ecran de la fenetre active
  python scripts/instant_screen.py --monitor 1
  python scripts/instant_screen.py --monitor 2
  python scripts/instant_screen.py --monitor all
  python scripts/instant_screen.py --json
  python scripts/instant_screen.py --save captures/   # PNG (dossier gitignore)
  python scripts/instant_screen.py --vlm              # description VLM, opt-in

Curseur (--with-cursor) : ou pointe l'utilisateur, lu a l'appel. Aucun suivi en
arriere-plan, aucun clic, aucun deplacement : lecture seule.

Profils (--profile) :
  film   contexte seul, pas d'OCR      ~64 ms   "quel film, quel site"
  agent  + OCR du bandeau              ~830 ms  "que dit l'ecran"
  full   + description VLM             + duree du modele

Le profil `film` vient d'un constat mesure en R-S2a : le titre de la video
est deja dans le titre de fenetre et le site dans l'URL, a 7 ms. Payer 300 ms
d'OCR pour cela revient a payer cinquante fois trop cher.

Les drapeaux explicites priment toujours sur le profil : `--profile film
--ocr` lit quand meme le texte.

Outil A LA DEMANDE : un appel, une reponse. Relancer pour rafraichir. Aucun
daemon, aucune boucle.

VIE PRIVEE : la sortie contient le texte reellement affiche (titres
d'onglets, noms de fichiers, courriels). Elle est destinee a l'ecran de
l'utilisateur. Ne pas la coller dans un fichier commite ni dans un ticket.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# La console Windows est souvent en cp1252, et ce que l'oeil lit a l'ecran ne
# l'est pas : un titre de video avec un emoji faisait planter l'affichage sur
# UnicodeEncodeError (constat R-S2d, caractere U+26A1 dans une recommandation
# YouTube). Un outil de vision qui tombe parce qu'une page contient un emoji
# est inutilisable. On remplace les caracteres non encodables au lieu de lever.
for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001  (flux redirige ou non reconfigurable)
        pass

from screen_content import (  # noqa: E402
    DEFAULT_DEPTH,
    DEFAULT_PROFILE,
    DEPTHS,
    PROFILES,
    REGIONS,
    get_instant_screen,
)


def _ligne(car: str = "-", n: int = 74) -> None:
    print(car * n)


def afficher(res: dict) -> None:
    print()
    _ligne("=")
    prof = res.get("profile") or {}
    titre = f"INSTANT ECRAN  ({res['elapsed_ms']} ms)"
    if res.get("depth"):
        titre += f"   profondeur : {res['depth']}"
    if prof:
        titre += f"   profil : {prof['nom']}"
    print(titre)
    _ligne("=")
    ecrans = ", ".join(
        f"[{m['index']}]{'*' if m['is_primary'] else ''} {m['width']}x{m['height']}"
        for m in res["monitors"]
    )
    print(f"Moniteurs : {ecrans}   (* = primaire)")

    for s in res["screens"]:
        print()
        _ligne()
        ctx = s.get("context") or {}
        proc = ctx.get("process") or {}
        doc = ctx.get("document") or {}
        win = ctx.get("window") or {}

        titre = f"ECRAN {s['monitor_index']}"
        if s["subject"] == "desktop":
            titre += "  ::  bureau (aucune fenetre applicative)"
        else:
            libelle = proc.get("label") or proc.get("exe_name") or "?"
            titre += f"  ::  {libelle}"
        print(titre)
        _ligne()

        if s["subject"] == "window":
            print(f"  application : {proc.get('exe_name')}  ({proc.get('category')})")
            if win.get("title"):
                print(f"  fenetre     : {win['title'][:88]}")
            if doc:
                url = doc.get("url_raw") or ""
                print(f"  document    : {url[:88]}")
                print(f"                host={doc.get('host')}  via={doc.get('via')}")
            elif proc.get("document_source") == "url":
                print("  document    : (aucune adresse lue)")
            print(
                f"  confiance   : {ctx.get('confidence')}"
                f"   unknown={ctx.get('unknown')}"
            )

        if s["captures"]:
            print()
            print(f"  {'region':<9} {'taille':<12} {'ms':>7} {'luma':>7} {'noir':>6}")
            for c in s["captures"]:
                print(
                    f"  {c['region']:<9} {str(c['width']) + 'x' + str(c['height']):<12} "
                    f"{c['elapsed_ms']:>7} {c['mean_luma']:>7} {str(c['looks_black']):>6}"
                    + (f"   -> {c['saved']}" if c.get("saved") else "")
                )

        ocr = s.get("ocr")
        if ocr:
            print()
            if ocr["available"]:
                print(f"  TEXTE LU  (region {ocr['region']}, {ocr['words']} mots, {ocr['elapsed_ms']} ms)")
                for ln in ocr["lines"][:14]:
                    print(f"    | {ln['text'][:86]}")
                reste = len(ocr["lines"]) - 14
                if reste > 0:
                    print(f"    | ... {reste} ligne(s) de plus (--json pour tout voir)")
            else:
                print(f"  TEXTE LU  : indisponible  ({ocr['reason']})")

        vlm = s.get("vlm")
        if vlm:
            print()
            if vlm["available"]:
                print(f"  DESCRIPTION  (region {vlm['region']}, {vlm['elapsed_ms']} ms)")
                print(f"    {vlm['caption']}")
            else:
                print(f"  DESCRIPTION : indisponible  ({vlm['reason']})")

        afficher_riche(s)

        for n in s.get("notes", []):
            print(f"  note : {n}")
    afficher_curseur(res)
    afficher_systeme(res)
    print()




def afficher_riche(s: dict) -> None:
    """Sections structurees de la profondeur standard / rich."""
    ri = s.get("rich")
    if not ri:
        return
    if ri.get("available") is False:
        print(f"  RICHE : indisponible  ({ri.get('reason')})")
        return

    doc = ri.get("document") or {}
    if doc.get("url"):
        suppose = "  (schema suppose)" if doc.get("scheme_suppose") else ""
        print()
        print(f"  URL COMPLETE : {doc['url'][:92]}{suppose}")
    page = ri.get("page") or {}
    if page.get("title"):
        print(f"  TITRE        : {page['title'][:92]}")
        if page.get("suffixes_retires"):
            print(f"                 (suffixes retires : {', '.join(page['suffixes_retires'])})")

    nav = ri.get("browser") or {}
    if nav.get("available") is False:
        print(f"  NAVIGATEUR   : {nav.get('reason')}")
    elif nav:
        tabs = nav.get("tabs") or []
        print()
        if tabs:
            print(f"  ONGLETS ({len(tabs)})")
            for t in tabs[:12]:
                print(f"    | {t['title'][:86]}")
            if len(tabs) > 12:
                print(f"    | ... {len(tabs) - 12} de plus")
        else:
            print(f"  ONGLETS : aucun  ({nav.get('tabs_reason', 'non expose')})")

        bm = nav.get("bookmarks") or []
        if bm:
            print()
            print(f"  FAVORIS ({len(bm)}) : " + " · ".join(b["title"][:22] for b in bm[:10]))

        recos = nav.get("recommendations") or []
        if recos:
            print()
            print(f"  RECOMMANDATIONS ({len(recos)})")
            for r in recos[:8]:
                bout = []
                if r.get("channel"):
                    bout.append(r["channel"][:22])
                if r.get("views"):
                    bout.append(f"{r['views']:,}".replace(",", " ") + " vues")
                if r.get("duration_s"):
                    bout.append(f"{r['duration_s'] // 60}:{r['duration_s'] % 60:02d}")
                suffixe = ("   [" + " · ".join(bout) + "]") if bout else ""
                print(f"    | {(r.get('title') or '?')[:62]}{suffixe}")
        elif nav.get("links_count") is not None:
            print()
            print(f"  LIENS VISIBLES : {nav['links_count']}"
                  + (f"  ({nav['links_reason']})" if nav.get("links_reason") else ""))

    ov = ri.get("overlay_text")
    if ov:
        print()
        if ov.get("available"):
            lignes = ov.get("lines") or []
            print(f"  TEXTE INCRUSTE ({len(lignes)} ligne(s), {ov['elapsed_ms']} ms, {ov['variant']})")
            for ln in lignes[:6]:
                print(f"    | {ln['text'][:86]}")
        else:
            print(f"  TEXTE INCRUSTE : aucun  ({ov.get('reason')})")

    cols = ri.get("colors") or {}
    if cols:
        print()
        print(f"  {'region':<9} {'RGB moyen':<22} {'luma':>7}  theme")
        for nom, c in cols.items():
            if not c:
                continue
            rgb = "(" + ", ".join(str(v) for v in c["mean_rgb"]) + ")"
            print(f"  {nom:<9} {rgb:<22} {c['luma']:>7}  {c['theme']}")


def afficher_curseur(res: dict) -> None:
    cur = res.get("cursor")
    if not cur:
        return
    print()
    _ligne()
    print("CURSEUR")
    _ligne()
    if not cur.get("available"):
        print(f"  indisponible  ({cur.get('reason')})")
        return
    ecran = cur.get("monitor_index")
    print(f"  position : ({cur['x']}, {cur['y']})   ecran : {ecran if ecran else 'aucun'}"
          f"   ({cur.get('elapsed_ms')} ms)")
    if cur.get("visible") is False:
        print(f"  visible  : NON  -- {cur.get('hidden_reason', 'pointeur masque')}")
    elif cur.get("visible"):
        print("  visible  : oui")
    if cur.get("reason"):
        print(f"  note     : {cur['reason']}")
    fen = cur.get("window") or {}
    if fen:
        print(f"  pointe sur : {fen.get('exe_name', '?')}   (classe {fen.get('class_name', '?')})")
    reg = cur.get("region")
    if reg:
        etat = "  [decoupee au bord de l'ecran]" if reg.get("clamped") else ""
        print(f"  region   : {reg['width']}x{reg['height']} px  bbox {reg['bbox']}{etat}")
    cap = cur.get("capture")
    if cap:
        print(f"  capture  : {cap['width']}x{cap['height']}  luma {cap['mean_luma']}"
              f"  noir={cap['looks_black']}  ({cap['elapsed_ms']} ms)"
              + (f"   -> {cap['saved']}" if cap.get("saved") else ""))
        if cap.get("note"):
            print(f"             {cap['note']}")


def afficher_systeme(res: dict) -> None:
    sysinfo = res.get("system") or {}
    tb = res.get("taskbar")
    if not sysinfo and not tb:
        return
    print()
    _ligne()
    print("SYSTEME")
    _ligne()
    if sysinfo.get("time"):
        print(f"  heure : {sysinfo['time']}  {sysinfo.get('date')}  ({sysinfo.get('weekday')})"
              f"   source : {sysinfo.get('source')}")
    if not tb:
        return
    if not tb.get("available"):
        print(f"  barre des taches : indisponible  ({tb.get('reason')})")
        return
    for b in tb.get("bars", []):
        if not b.get("available"):
            print(f"  {b['class']} : {b.get('reason')}")
            continue
        print(f"  barre ecran {b.get('monitor_index')} : {b['apps_count']} application(s), "
              f"{len(b.get('tray_text', []))} texte(s) de zone systeme")
        noms = [a["name"].split(" - ")[0][:18] for a in b.get("apps", [])[:10]]
        if noms:
            print("    " + " · ".join(noms))
        for t in b.get("tray_text", [])[:4]:
            print(f"    zone systeme : {t['text'][:60]}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ce qui est affiche a l'instant T, ecran par ecran (R-S2a)"
    )
    p.add_argument(
        "--monitor",
        default=None,
        help="1, 2, ... ou 'all'. Par defaut : l'ecran de la fenetre active.",
    )
    p.add_argument("--json", action="store_true", help="Sortie JSON brute")
    p.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        choices=tuple(PROFILES),
        help=(
            "film = contexte seul, pas d'OCR (" + PROFILES["film"]["cout"] + ") | "
            "agent = + OCR du bandeau (" + PROFILES["agent"]["cout"] + ") | "
            "full = + description VLM. "
            "Defaut : " + DEFAULT_PROFILE + ". Les drapeaux explicites priment."
        ),
    )

    p.add_argument(
        "--depth",
        default=DEFAULT_DEPTH,
        choices=tuple(DEPTHS),
        help=(
            "minimal = app, site, titre (" + DEPTHS["minimal"]["cout"] + ") | "
            "standard = + onglets, favoris, URL complete, couleurs (" + DEPTHS["standard"]["cout"] + ") | "
            "rich = + liens, sous-titres, barre des taches (" + DEPTHS["rich"]["cout"] + ")"
        ),
    )

    # Tri-etat : None = suivre le profil, True/False = choix explicite de l'utilisateur.
    g_ocr = p.add_mutually_exclusive_group()
    g_ocr.add_argument("--ocr", dest="ocr", action="store_true", default=None,
                       help="Forcer la lecture de texte")
    g_ocr.add_argument("--no-ocr", dest="ocr", action="store_false", default=None,
                       help="Forcer l'absence de lecture de texte")
    p.add_argument(
        "--ocr-region", default=None, choices=REGIONS, help="Region a lire (defaut : profil)"
    )
    p.add_argument("--lang", default="fra+eng", help="Langues Tesseract")
    p.add_argument("--min-conf", type=int, default=45, help="Confiance OCR minimale 0-100")

    g_vlm = p.add_mutually_exclusive_group()
    g_vlm.add_argument("--vlm", dest="vlm", action="store_true", default=None,
                       help="Description par VLM (opt-in : GPU + modele deja en cache)")
    g_vlm.add_argument("--no-vlm", dest="vlm", action="store_false", default=None,
                       help="Forcer l'absence de description VLM")
    p.add_argument(
        "--vlm-region", default=None, choices=REGIONS, help="Region a decrire (defaut : profil)"
    )
    p.add_argument(
        "--with-cursor",
        action="store_true",
        help="Ajouter la position du curseur (ou pointe l'utilisateur). Lecture seule.",
    )
    p.add_argument(
        "--cursor-region",
        type=int,
        default=0,
        metavar="N",
        help="Capturer un carre de N px autour du curseur (implique --with-cursor)",
    )
    p.add_argument("--save", default=None, help="Dossier ou ecrire les PNG (ex captures/)")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    monitor = None
    all_monitors = False
    if args.monitor:
        if str(args.monitor).lower() in ("all", "tous", "*"):
            all_monitors = True
        else:
            try:
                monitor = int(args.monitor)
            except ValueError:
                print(f"--monitor attend un entier ou 'all', recu : {args.monitor!r}")
                return 2

    profil = PROFILES[args.profile]
    with_ocr = profil["with_ocr"] if args.ocr is None else args.ocr
    with_vlm = profil["with_vlm"] if args.vlm is None else args.vlm
    ocr_region = args.ocr_region or profil["ocr_region"]
    vlm_region = args.vlm_region or profil["vlm_region"]

    res = get_instant_screen(
        monitor=monitor,
        all_monitors=all_monitors,
        with_ocr=with_ocr,
        ocr_region=ocr_region,
        with_vlm=with_vlm,
        vlm_region=vlm_region,
        save_dir=args.save,
        ocr_lang=args.lang,
        ocr_min_conf=args.min_conf,
        depth=args.depth,
        with_cursor=args.with_cursor or bool(args.cursor_region),
        cursor_region=max(0, args.cursor_region),
    )
    res["profile"] = {
        "nom": args.profile,
        "with_ocr": with_ocr,
        "with_vlm": with_vlm,
        "pourquoi": profil["pourquoi"],
    }

    if res.get("error"):
        print(res["error"])
        return 2

    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        afficher(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
