"""Runner des scenarios de validation V1-V8 (R-S3a).

Ce script ne prouve rien tout seul : il OBSERVE l'ecran tel qu'il est et
rend, pour chaque scenario, un statut honnete. Sa seule vertu est de ne
jamais dire "pass" quand il ne sait pas.

Vocabulaire des statuts, et c'est le coeur du sujet :

  pass     verifie, sans ambiguite
  fail     verifie, et le resultat est mauvais
  skip     prerequis absent (pas d'Edge ouvert, pas de PDF...) : rien n'a
           ete teste, et ce n'est ni bon ni mauvais
  unknown  observe, mais indecidable sans l'utilisateur

La distinction `skip` / `unknown` compte. `skip` dit "je n'ai pas regarde".
`unknown` dit "j'ai regarde et je ne peux pas conclure seul". Les confondre
reviendrait a masquer une observation utile.

Le cas V1 / V8 illustre pourquoi. Sur une page d'offre d'emploi, trouver un
bouton "Postuler" est un succes (V1) ou un echec grave (V8, faux positif),
selon que la page en comporte un ou non. Le script ne peut pas le savoir.
Il rend donc `unknown` avec son observation, sauf si l'utilisateur declare son
intention par `--v1` ou `--v8`.

Usage :
  python scripts/run_validation_scenarios.py
  python scripts/run_validation_scenarios.py --v8      # page SANS bouton Postuler
  python scripts/run_validation_scenarios.py --v1      # page AVEC bouton Postuler
  python scripts/run_validation_scenarios.py --report

Aucune actuation : le script ne bascule aucune fenetre, ne clique pas, ne
deplace pas la souris. Il regarde ce qui est deja la.

VIE PRIVEE : le rapport ne contient que des statuts, des comptes et des noms
d'executables. Ni URL, ni titre, ni texte lu.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from screen_context import (  # noqa: E402
    classify_app,
    get_screen_snapshot,
    list_monitors,
    list_windows,
)

PREUVES_DIR = ROOT / "preuves"

STATUTS = ("pass", "fail", "skip", "unknown")


def _res(vid: str, statut: str, raison: str, **details: Any) -> dict[str, Any]:
    assert statut in STATUTS, statut
    out = {"id": vid, "status": statut, "reason": raison}
    if details:
        out["details"] = details
    return out


def _navigateurs(fenetres: list[Any]) -> list[Any]:
    return [w for w in fenetres
            if classify_app(w.exe_name).get("category") == "browser"
            and not w.is_overlay and not w.is_cloaked]


def _trouver_hote(fenetres: list[Any], fragment: str) -> tuple[Any, dict[str, Any]] | None:
    """Premiere fenetre de navigateur dont le document contient `fragment`."""
    for w in _navigateurs(fenetres):
        snap = get_screen_snapshot(hwnd=w.hwnd)
        doc = snap.get("document") or {}
        if fragment in (doc.get("host") or "").lower():
            return (w, snap)
    return None


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #

def v_document(vid: str, fenetres: list[Any], fragment: str, libelle: str) -> dict[str, Any]:
    """V2, V3 : le document attendu est-il identifie ?"""
    if not _navigateurs(fenetres):
        return _res(vid, "skip", "aucune fenetre de navigateur ouverte")
    trouve = _trouver_hote(fenetres, fragment)
    if not trouve:
        return _res(vid, "skip", f"aucun onglet {libelle} ouvert")
    w, snap = trouve
    doc = snap.get("document") or {}
    return _res(
        vid, "pass", f"document {libelle} identifie",
        navigateur=w.exe_name,
        host_len=len(doc.get("host") or ""),
        confiance=snap.get("confidence"),
        url_normalisee=bool(doc.get("url_normalized")),
    )


def v1_v8_postuler(fenetres: list[Any], attendu: str | None) -> list[dict[str, Any]]:
    """V1 et V8 : la cible d'action est-elle trouvee, et fallait-il la trouver ?

    Un meme constat sert les deux scenarios avec des conclusions opposees.
    Sans declaration d'intention, on rend l'observation et on s'abstient de
    conclure.
    """
    import screen_uia as su

    if not _navigateurs(fenetres):
        raison = "aucune fenetre de navigateur ouverte"
        return [_res("V1", "skip", raison), _res("V8", "skip", raison)]
    trouve = _trouver_hote(fenetres, "emploi.example")
    if not trouve:
        raison = "aucun onglet d'offre d'emploi ouvert"
        return [_res("V1", "skip", raison), _res("V8", "skip", raison)]

    w, _ = trouve
    hit = su.find_control(w.hwnd, "Postuler", window_rect=tuple(w.rect_visible),
                          monitors=list_monitors())
    present = hit is not None
    details = {
        "navigateur": w.exe_name,
        "controle_trouve": present,
        "methode": hit.to_dict().get("method") if hit else None,
        "bbox_dans_la_fenetre": hit.to_dict().get("in_window") if hit else None,
    }

    if attendu == "v1":
        return [
            _res("V1", "pass" if present else "fail",
                 "bouton Postuler localise" if present
                 else "bouton Postuler introuvable alors qu'il etait declare present",
                 **details),
            _res("V8", "skip", "page declaree AVEC bouton : V8 demande une page sans"),
        ]
    if attendu == "v8":
        return [
            _res("V1", "skip", "page declaree SANS bouton : V1 demande une page avec"),
            _res("V8", "fail" if present else "pass",
                 "FAUX POSITIF : un bouton a ete rendu sur une page qui n'en a pas"
                 if present else "abstention correcte, aucun bbox invente",
                 **details),
        ]

    raison = (
        "page d'offre d'emploi ouverte, controle "
        + ("trouve" if present else "absent")
        + ". Indecidable seul : relancer avec --v1 (page avec bouton) ou "
          "--v8 (page sans bouton)."
    )
    return [_res("V1", "unknown", raison, **details),
            _res("V8", "unknown", raison, **details)]


def v4_multi_ecran(fenetres: list[Any], moniteurs: list[Any]) -> dict[str, Any]:
    """V4 : chaque fenetre est-elle rattachee au bon ecran ?"""
    if len(moniteurs) < 2:
        return _res("V4", "skip", f"{len(moniteurs)} ecran(s) : le scenario en demande 2")
    reelles = [w for w in fenetres if not w.is_overlay and not w.is_cloaked]
    if not reelles:
        return _res("V4", "skip", "aucune fenetre applicative")
    mauvaises = []
    for w in reelles:
        if w.monitor_index is None:
            mauvaises.append(w.exe_name)
            continue
        mon = next((m for m in moniteurs if m.index == w.monitor_index), None)
        if mon is None:
            mauvaises.append(w.exe_name)
            continue
        cx = (w.rect_visible[0] + w.rect_visible[2]) // 2
        cy = (w.rect_visible[1] + w.rect_visible[3]) // 2
        # Le centre peut sortir de l'ecran pour une fenetre a cheval : on
        # verifie alors seulement le recouvrement, qui est le critere reel.
        recouvre = not (w.rect_visible[2] <= mon.bbox[0] or w.rect_visible[0] >= mon.bbox[2]
                        or w.rect_visible[3] <= mon.bbox[1] or w.rect_visible[1] >= mon.bbox[3])
        if not recouvre:
            mauvaises.append(f"{w.exe_name} (centre {cx},{cy})")
    if mauvaises:
        return _res("V4", "fail", f"{len(mauvaises)} fenetre(s) mal rattachee(s)",
                    fenetres_en_cause=len(mauvaises))
    ecrans = sorted({w.monitor_index for w in reelles})
    return _res("V4", "pass", "toutes les fenetres sont rattachees a un ecran qui les recouvre",
                fenetres=len(reelles), ecrans_occupes=ecrans)


def v5_changement_fenetre() -> dict[str, Any]:
    """V5 : suite d'events sans trou pendant un basculement de fenetres."""
    return _res(
        "V5", "unknown",
        "demande un basculement de fenetres pendant environ 2 minutes. Le "
        "runner ne bascule aucune fenetre (ce serait de l'actuation) et ne "
        "tourne pas en boucle (decision I7). A jouer par l'utilisateur en R-S3b.",
    )


def v6_pdf(fenetres: list[Any]) -> dict[str, Any]:
    pdfs = [w for w in fenetres if classify_app(w.exe_name).get("category") == "pdf"]
    if not pdfs:
        return _res("V6", "skip", "aucune visionneuse PDF ouverte", bloquant=False)
    w = pdfs[0]
    snap = get_screen_snapshot(hwnd=w.hwnd)
    return _res("V6", "pass", "fenetre PDF analysee sans erreur",
                application=w.exe_name, confiance=snap.get("confidence"), bloquant=False)


def v7_plein_ecran(fenetres: list[Any], moniteurs: list[Any]) -> dict[str, Any]:
    """V7 : une fenetre couvrant tout un ecran ne doit pas faire tomber la chaine."""
    for w in fenetres:
        if w.is_overlay or w.is_cloaked or w.monitor_index is None:
            continue
        mon = next((m for m in moniteurs if m.index == w.monitor_index), None)
        if mon is None:
            continue
        if (w.rect_visible[2] - w.rect_visible[0] >= mon.width
                and w.rect_visible[3] - w.rect_visible[1] >= mon.height):
            try:
                snap = get_screen_snapshot(hwnd=w.hwnd)
            except Exception as exc:  # noqa: BLE001
                return _res("V7", "fail", f"exception sur fenetre plein ecran : {exc}",
                            bloquant=False)
            return _res("V7", "pass", "fenetre plein ecran analysee sans crash",
                        application=w.exe_name, confiance=snap.get("confidence"),
                        bloquant=False)
    return _res("V7", "skip", "aucune fenetre en plein ecran", bloquant=False)


# --------------------------------------------------------------------------- #

def executer(attendu: str | None) -> list[dict[str, Any]]:
    moniteurs = list_monitors()
    fenetres = list_windows(monitors=moniteurs)
    resultats: list[dict[str, Any]] = []
    resultats += v1_v8_postuler(fenetres, attendu)
    resultats.append(v_document("V2", fenetres, "chat-ia.example", "chat-ia.example"))
    resultats.append(v_document("V3", fenetres, "outlook", "Outlook web"))
    resultats.append(v4_multi_ecran(fenetres, moniteurs))
    resultats.append(v5_changement_fenetre())
    resultats.append(v6_pdf(fenetres))
    resultats.append(v7_plein_ecran(fenetres, moniteurs))
    resultats.sort(key=lambda r: r["id"])
    return resultats


def rapport(resultats: list[dict[str, Any]]) -> str:
    comptes = {s: sum(1 for r in resultats if r["status"] == s) for s in STATUTS}
    lignes = [
        "Scenarios de validation V1-V8 - vision-public R-S3a",
        f"Date : {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"{'id':<5} {'statut':<9} raison",
        "-" * 72,
    ]
    for r in resultats:
        lignes.append(f"{r['id']:<5} {r['status']:<9} {r['reason'][:58]}")
    lignes += [
        "",
        "  ".join(f"{s} = {n}" for s, n in comptes.items()),
        "",
        "Lecture des statuts :",
        "  pass     verifie, sans ambiguite",
        "  fail     verifie, resultat mauvais",
        "  skip     prerequis absent : rien n'a ete teste",
        "  unknown  observe, mais indecidable sans l'utilisateur",
        "",
        "skip et unknown ne sont PAS des succes. Un scenario non joue reste",
        "non joue : c'est ce qui separe ce rapport d'un faux vert.",
        "",
        "Vie privee : ni URL, ni titre, ni texte lu. Statuts, comptes et noms",
        "d'executables seulement.",
    ]
    return "\n".join(lignes)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Runner scenarios V1-V8 (R-S3a)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--v1", action="store_true",
                   help="La page d'offre d'emploi ouverte COMPORTE un bouton Postuler")
    g.add_argument("--v8", action="store_true",
                   help="La page d'offre d'emploi ouverte NE comporte PAS de bouton Postuler")
    p.add_argument("--v2", action="store_true",
                   help="Accepte pour coherence des commandes. Sans effet : V2 se decide "
                        "sur l'hote du document, sans declaration d'intention.")
    p.add_argument("--report", action="store_true",
                   help="Ecrire preuves/scenarios-<date>.txt")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true",
                   help="Sortir en 1 si un scenario est en fail")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    attendu = "v1" if args.v1 else ("v8" if args.v8 else None)

    print("=" * 72)
    print("SCENARIOS DE VALIDATION V1-V8 (R-S3a)")
    print("=" * 72)
    if args.v2:
        print("--v2 est accepte mais sans effet : V2 se decide sur l'hote du")
        print("document, il n'y a aucune ambiguite a lever.")
    if attendu is None:
        print("Aucune intention declaree : V1 et V8 resteront indecidables.")
        print("Relancer avec --v1 ou --v8 sur la page correspondante.")
    print()

    resultats = executer(attendu)
    texte = rapport(resultats)
    print(texte)

    if args.json:
        print()
        print(json.dumps(resultats, indent=2, ensure_ascii=False))

    if args.report:
        PREUVES_DIR.mkdir(parents=True, exist_ok=True)
        cible = PREUVES_DIR / f"scenarios-{time.strftime('%Y-%m-%d')}.txt"
        cible.write_text(texte + "\n", encoding="utf-8")
        print()
        print(f"Rapport ecrit : {cible}")

    if args.strict and any(r["status"] == "fail" for r in resultats):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
