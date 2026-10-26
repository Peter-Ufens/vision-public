"""Smoke screen_context : R-S1a (Win32) + R-S1b (UIA, document, events).

Usage :
  python scripts/smoke_screen_context.py
  python scripts/smoke_screen_context.py --json
  python scripts/smoke_screen_context.py --no-uia

Sortie : exit 0 si tous les controles passent, 1 sinon.
Aucun pixel capture, aucun modele charge, aucune ecriture disque.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from screen_context import (  # noqa: E402
    DPI_MODE,
    dpi_awareness,
    get_screen_context,
    list_windows,
    monitor_for_rect,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok, detail))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok



def smoke_uia(ctx: dict) -> None:
    """Controles R-S1b : UIA, identite document, abstention, events."""
    import json as _json
    import tempfile

    from screen_context import (
        CONFIDENCE_CONF,
        classify_app,
        context_signature,
        emit_screen_context,
        event_payload,
        get_screen_snapshot,
        list_windows,
        reset_emit_cache,
    )

    print()
    print("=" * 70)
    print("R-S1b : UIA, identite document, abstention, events")
    print("=" * 70)

    # --- table des categories ---------------------------------------------- #
    print()
    print("Categories d'applications")
    edge = classify_app("msedge.exe")
    check("msedge.exe -> browser", edge.get("category") == "browser", str(edge.get("category")))
    check("msedge.exe -> document par URL", edge.get("document") == "url")
    check("msedge.exe est connu", bool(edge.get("known")))
    term = classify_app("cmd.exe")
    check(
        "cmd.exe -> aucun document attendu",
        term.get("document") is None,
        "distinction 'rien a lire' vs 'je ne sais pas'",
    )
    inconnu = classify_app("un-truc-qui-nexiste-pas.exe")
    check("executable inconnu -> categorie unknown", inconnu.get("category") == "unknown")
    check("executable inconnu -> known faux", inconnu.get("known") is False)
    check(
        "casse ignoree sur le nom d'executable",
        classify_app("MSEDGE.EXE").get("category") == "browser",
    )

    # --- disponibilite UIA -------------------------------------------------- #
    print()
    print("Couche UIA")
    try:
        import screen_uia
    except Exception as exc:  # noqa: BLE001
        check("import screen_uia", False, repr(exc)[:70])
        return
    check("import screen_uia", True)
    dispo = screen_uia.is_available()
    check("UIA disponible", dispo, screen_uia.unavailable_reason() or "comtypes OK")
    if not dispo:
        return

    # --- regles de conception du spike -------------------------------------- #
    print()
    print("Regles de conception (spike R-S0)")
    check(
        "bbox d'aire nulle rejete",
        not screen_uia._on_screen((100, 100, 100, 100), [(0, 0, 2560, 1440)]),
    )
    check(
        "bbox hors de tout ecran rejete",
        not screen_uia._on_screen((-9000, -9000, -8900, -8900), [(0, 0, 2560, 1440)]),
    )
    check(
        "bbox valide accepte",
        screen_uia._on_screen((10, 10, 110, 60), [(0, 0, 2560, 1440)]),
    )
    check("pointeur COM NULL traite comme absent", not screen_uia._ok(None))
    check(
        "valeur sans forme d'URL rejetee",
        not screen_uia._looks_like_url("carte grise en ligne"),
        "une recherche tapee n'est pas une adresse",
    )
    check("valeur avec forme d'URL acceptee", screen_uia._looks_like_url("exemple.fr/page"))

    # --- fenetres reelles ---------------------------------------------------- #
    wins = list_windows()
    browsers = [w for w in wins if classify_app(w.exe_name).get("document") == "url"]
    others = [w for w in wins if classify_app(w.exe_name).get("document") != "url"]

    print()
    print("Snapshot sur fenetres reelles")
    snap_fg = get_screen_snapshot()
    check("snapshot rendu", isinstance(snap_fg, dict) and snap_fg.get("v") == 1)
    for champ in ("t_wall_ms", "confidence", "unknown", "process", "window", "conf"):
        check(f"champ '{champ}' present", champ in snap_fg)
    check(
        "confidence dans le vocabulaire defini",
        snap_fg.get("confidence") in CONFIDENCE_CONF,
        str(snap_fg.get("confidence")),
    )
    check(
        "conf numerique coherent avec confidence",
        snap_fg.get("conf") == CONFIDENCE_CONF.get(snap_fg.get("confidence")),
    )
    check("horloge murale plausible", int(snap_fg.get("t_wall_ms", 0)) > 1_600_000_000_000)

    if others:
        w = others[0]
        snap = get_screen_snapshot(hwnd=w.hwnd)
        check(
            f"app non-navigateur ({w.exe_name}) : aucun document invente",
            snap.get("document") is None,
            "abstention correcte",
        )
        check(
            "app non-navigateur : unknown reste faux",
            snap.get("unknown") is False,
            "rien a lire n'est pas la meme chose que je ne sais pas",
        )

    if browsers:
        w = browsers[0]
        snap = get_screen_snapshot(hwnd=w.hwnd)
        doc = snap.get("document")
        check(f"navigateur detecte ({w.exe_name})", snap["process"]["category"] == "browser")
        if doc:
            check("URL lue", bool(doc.get("host")), f"host de {len(doc['host'])} caracteres")
            check("via renseigne", doc.get("via") in ("omnibox-class", "value-heuristic"), str(doc.get("via")))
            check(
                "confiance elevee quand l'omnibox est reconnue",
                snap["confidence"] in ("high", "medium"),
                snap["confidence"],
            )
        else:
            check(
                "navigateur sans URL -> unknown vrai",
                snap.get("unknown") is True,
                "trou d'information signale, pas masque",
            )

        # Abstention : une cible qui n'existe pas ne doit JAMAIS rendre un bbox
        absurde = "ZZZ-controle-qui-nexiste-pas-42"
        snap_abs = get_screen_snapshot(hwnd=w.hwnd, target=absurde)
        check(
            "cible inexistante -> target null (abstention, jalon G3)",
            snap_abs.get("target") is None and snap_abs.get("target_found") is False,
        )
        check("cible inexistante -> target_searched vrai", snap_abs.get("target_searched") is True)
    else:
        print("  [SKIP] aucun navigateur ouvert : lecture d'URL non exercee")
        print("         relancer avec Edge ouvert pour couvrir ce chemin")

    # --- bbox de cible : garanties ------------------------------------------- #
    print()
    print("Garanties sur le bbox de cible")
    tested = False
    for w in wins:
        snap = get_screen_snapshot(hwnd=w.hwnd, target=w.title[:20] if w.title else "")
        tgt = snap.get("target")
        if not tgt:
            continue
        tested = True
        x0, y0, x1, y1 = tgt["bbox"]
        check(f"bbox d'aire non nulle ({w.exe_name})", x1 > x0 and y1 > y0, str(tgt["bbox"]))
        idx, area = monitor_for_rect((x0, y0, x1, y1))
        check("bbox rattache a un ecran", idx is not None and area > 0, f"ecran {idx}")
        check("methode renseignee", tgt.get("method") in ("exact", "substring"), str(tgt.get("method")))
        break
    if not tested:
        print("  [SKIP] aucune cible trouvee sur les fenetres ouvertes")

    # --- events -------------------------------------------------------------- #
    print()
    print("Event screen.context v1")
    from events_v1 import SCREEN_CONTEXT, SOURCE_SCREEN
    from vision_events import EventBus

    tmp = Path(tempfile.gettempdir()) / "vision_public_smoke_screen_context.jsonl"
    if tmp.exists():
        tmp.unlink()
    bus = EventBus(path=tmp, zmq_enabled=False, source=SOURCE_SCREEN)
    reset_emit_cache()

    snap1 = get_screen_snapshot()
    sent1 = emit_screen_context(snap1, bus=bus)
    check("premier contexte emis", sent1)

    sent2 = emit_screen_context(get_screen_snapshot(), bus=bus)
    check(
        "contexte inchange -> pas de reemission",
        not sent2,
        "outil a la demande, pas de spam",
    )

    if len(wins) >= 2:
        autre = next((w for w in wins if w.hwnd != snap1["window"]["hwnd"]), None)
        if autre:
            sent3 = emit_screen_context(get_screen_snapshot(hwnd=autre.hwnd), bus=bus)
            check("changement de fenetre -> reemission", sent3)
            check(
                "signatures differentes",
                context_signature(snap1)
                != context_signature(get_screen_snapshot(hwnd=autre.hwnd)),
            )

    lignes = [
        _json.loads(x) for x in tmp.read_text(encoding="utf-8").splitlines() if x.strip()
    ]
    check("journal JSONL ecrit", len(lignes) >= 1, f"{len(lignes)} ligne(s)")
    if lignes:
        ev = lignes[0]
        check("enveloppe v1", ev.get("v") == 1)
        check("type screen.context", ev.get("type") == SCREEN_CONTEXT, str(ev.get("type")))
        check("source vision.screen", ev.get("source") == SOURCE_SCREEN, str(ev.get("source")))
        check("ttl adapte au contexte ecran", ev.get("ttl_ms", 0) >= 1000, f"{ev.get('ttl_ms')} ms")
        check("conf dans [0,1]", 0.0 <= float(ev.get("conf", -1)) <= 1.0)
        payload = ev.get("payload", {})
        attendus = {
            "t_wall_ms", "phase", "monitor_index", "process", "document",
            "window", "target_requested", "target", "confidence", "unknown",
        }
        manquants = attendus - set(payload)
        check(
            "payload conforme au schema v1",
            not manquants,
            f"manquants : {sorted(manquants)}" if manquants else "10 champs",
        )
        check(
            "horloge murale dans le payload",
            isinstance(payload.get("t_wall_ms"), int),
            "tracabilite des preuves possible",
        )
        check(
            "payload identique a event_payload()",
            set(event_payload(snap1)) == set(payload),
        )
    bus.close()
    try:
        tmp.unlink()
    except OSError:
        pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true", help="Dump du snapshot complet")
    p.add_argument("--no-uia", action="store_true", help="Sauter les controles R-S1b (UIA)")
    args = p.parse_args()

    ctx = get_screen_context()
    mons = ctx["monitors"]

    print("=" * 70)
    print("SMOKE screen_context (R-S1a)")
    print("=" * 70)
    print(f"DPI demande  : {DPI_MODE}")
    print(f"DPI constate : {dpi_awareness()}")
    print(f"Snapshot     : {ctx['elapsed_ms']} ms")
    print()

    print("Moniteurs")
    for m in mons:
        prim = " primaire" if m["is_primary"] else ""
        print(f"  [{m['index']}]{prim} {m['width']}x{m['height']} @ ({m['left']},{m['top']})")
    print()

    print("Controles")
    check("au moins un moniteur", len(mons) >= 1, f"{len(mons)} detecte(s)")
    check("DPI per-monitor actif", dpi_awareness() == "per-monitor", dpi_awareness())

    # Anti-divergence : screen_context enumere en Win32, screen_capture via mss.
    # Deux sources qui se contrediraient en silence seraient pire qu'une seule.
    try:
        from screen_capture import list_monitors as capture_monitors

        cap = capture_monitors()
        mine = [(m["index"], m["left"], m["top"], m["width"], m["height"]) for m in mons]
        theirs = [(c.index, c.left, c.top, c.width, c.height) for c in cap]
        check(
            "moniteurs Win32 == moniteurs mss (screen_capture)",
            mine == theirs,
            f"win32={mine} mss={theirs}" if mine != theirs else f"{len(mine)} ecran(s) concordants",
        )
    except Exception as exc:  # noqa: BLE001
        check("moniteurs Win32 == moniteurs mss (screen_capture)", False, repr(exc)[:80])

    # --- mapping : les cas qui cassent une implementation arithmetique ------ #
    print()
    print("Mapping fenetre -> ecran (rectangles synthetiques)")
    ok_map = True
    for m in mons:
        # Rectangle strictement au centre de chaque moniteur
        cx = m["left"] + m["width"] // 2
        cy = m["top"] + m["height"] // 2
        rect = (cx - 50, cy - 50, cx + 50, cy + 50)
        idx, area = monitor_for_rect(rect)
        ok_map &= check(
            f"centre ecran {m['index']} -> ecran {idx}",
            idx == m["index"],
            f"recouvrement {area} px2",
        )

    if len(mons) >= 2:
        m1, m2 = mons[0], mons[1]
        # Piege reel de ce poste : DISPLAY2 est a y=182, pas a y=0.
        # Un rect a la hauteur du HAUT de l'ecran 2 est HORS de l'ecran 2
        # si on raisonne en y=0, et un mapping naif se trompe.
        rect_haut2 = (m2["left"] + 10, m2["top"] + 10, m2["left"] + 210, m2["top"] + 110)
        idx, area = monitor_for_rect(rect_haut2)
        ok_map &= check(
            f"haut de l'ecran 2 (y={m2['top']}) -> ecran {idx}",
            idx == m2["index"],
            f"recouvrement {area} px2",
        )

        # Bande verticale sous l'ecran 2 mais dans l'ecran 1 en x :
        # verifie qu'on ne deborde pas sur le mauvais ecran.
        if m2["top"] > m1["top"]:
            rect_bande = (m1["left"] + 10, m1["top"] + 10, m1["left"] + 110, m1["top"] + 110)
            idx, _ = monitor_for_rect(rect_bande)
            ok_map &= check(f"coin haut-gauche ecran 1 -> ecran {idx}", idx == m1["index"])

        # Fenetre a cheval : doit tomber sur l'ecran qui en a le plus
        overlap_rect = (m2["left"] - 400, m2["top"] + 100, m2["left"] + 100, m2["top"] + 300)
        idx, area = monitor_for_rect(overlap_rect)
        ok_map &= check(
            f"fenetre a cheval -> ecran {idx} (aire max)",
            idx in (m1["index"], m2["index"]),
            f"recouvrement {area} px2",
        )

    # Rectangle hors de tout ecran
    idx, area = monitor_for_rect((-9000, -9000, -8900, -8900))
    check("rect hors ecran -> aucun moniteur", idx is None and area == 0, f"idx={idx}")

    # --- fenetres reelles --------------------------------------------------- #
    print()
    print("Fenetres reelles")
    wins = list_windows()
    check("au moins une fenetre visible", len(wins) >= 1, f"{len(wins)} fenetre(s)")

    bad_map = [w for w in wins if w.monitor_index is None]
    check(
        "toute fenetre visible est rattachee a un ecran",
        not bad_map,
        f"{len(bad_map)} sans ecran" if bad_map else "",
    )

    bad_dwm = [
        w for w in wins
        if not (
            w.rect[0] <= w.rect_visible[0] and w.rect[1] <= w.rect_visible[1]
            and w.rect_visible[2] <= w.rect[2] and w.rect_visible[3] <= w.rect[3]
        )
    ]
    check(
        "bbox visible (DWM) inclus dans bbox Win32",
        not bad_dwm,
        f"{len(bad_dwm)} anomalie(s)" if bad_dwm else "bordures invisibles retirees",
    )

    fg = ctx["foreground"]
    check("fenetre au premier plan identifiee", fg is not None)
    if fg:
        check(
            "premier plan : executable resolu",
            fg["exe_name"] not in ("", "?"),
            fg["exe_name"],
        )
        # `None` est une reponse legitime : une fenetre en cours de deplacement
        # entre deux ecrans, minimisee pendant l'appel, ou posee dans la zone du
        # bureau virtuel que ne couvre aucun moniteur, n'appartient a aucun
        # ecran. Observe une fois sur quatre executions pendant R-S5.
        # On n'affaiblit pas l'assertion : on verifie que l'absence est
        # JUSTIFIEE par la geometrie, au lieu de l'interdire.
        vis = fg["rect_visible"]
        recouvre = any(
            not (vis[2] <= m["left"] or vis[0] >= m["left"] + m["width"]
                 or vis[3] <= m["top"] or vis[1] >= m["top"] + m["height"])
            for m in mons
        )
        check(
            "premier plan : rattache a un ecran, ou absence justifiee",
            (fg["monitor_index"] is not None) == recouvre,
            "rattache" if fg["monitor_index"] is not None
            else "aucun recouvrement : fenetre en transition ou hors ecran",
        )

    print()
    print(f"{'exe':<24} {'ecran':<7} {'bbox visible':<28} titre")
    print("-" * 70)
    for w in wins[:12]:
        marque = "*" if w.is_foreground else " "
        print(
            f"{marque}{w.exe_name:<23} {str(w.monitor_index):<7} "
            f"{str(w.rect_visible):<28} {w.title[:26]}"
        )
    print("  (* = premier plan)")

    if not args.no_uia:
        smoke_uia(ctx)

    if args.json:
        print()
        print(json.dumps(ctx, indent=2, ensure_ascii=False))

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
