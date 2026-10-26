"""Smoke de la session ecran bornee (R-S10).

Usage :
  python scripts/smoke_session_screen.py

Sortie : exit 0 si tous les controles passent, 1 sinon. Duree totale <= 5 s.

CE QUE CE SMOKE SURVEILLE VRAIMENT

Une session est un runtime : elle tourne, elle ecrit, elle s'arrete. Trois
choses peuvent mal tourner sans qu'on le voie :

  1. la duree n'est plus bornee (retour au daemon, interdit I7) ;
  2. le pied de session manque apres une interruption -- une trace tronquee
     devient alors indiscernable d'une session qui n'a rien vu ;
  3. un titre de fenetre ou une URL se glisse dans la trace.

Les controles ci-dessous portent d'abord sur ces trois-la.

La sequence d'etats est INJECTEE pour la partie qui compte : sans cela, on ne
pourrait verifier les diffs qu'en esperant que quelque chose bouge a l'ecran
pendant les cinq secondes du smoke. Un test qui depend de la chance n'est pas
un test.

VIE PRIVEE : aucun titre, aucune URL, aucun nom de controle affiche.
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

CHECKS: list[tuple[str, bool]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def lire(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def fen(exe: str, bbox: list[int], ecran: int = 1, fg: bool = False) -> dict[str, Any]:
    return {"exe": exe, "classe": "ConsoleWindowClass", "bbox": bbox,
            "ecran": ecran, "premier_plan": fg}


def etat(fenetres: dict[str, Any], hote: str = "a1b2c3d4") -> dict[str, Any]:
    return {"moniteurs": [[1, 2560, 1440]],
            "actif": {"exe": "brave.exe", "hote": hote, "confiance": "high"},
            "fenetres": fenetres}


def main() -> int:  # noqa: C901
    print("=" * 70)
    print("SMOKE session ecran bornee (R-S10)")
    print("=" * 70)

    import session_screen as ss

    # --- I7 : la duree est obligatoire et bornee ---------------------------- #
    print()
    print("I7 - la session est bornee")
    sans_duree = False
    import contextlib
    import io

    # argparse ecrit son usage sur stderr : on l'avale, sinon un smoke qui
    # PASSE afficherait un "error:" en tete et se lirait comme un echec.
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            ss.parse_args([])
        except SystemExit:
            sans_duree = True
    check("lancer sans --duree est refuse", sans_duree,
          "il n'existe pas de session infinie par defaut")

    check("10m se lit 600 s", ss.parse_duree("10m") == 600.0)
    check("30m se lit 1800 s", ss.parse_duree("30m") == 1800.0)
    check("90s se lit 90 s", ss.parse_duree("90s") == 90.0)
    check("un nombre nu se lit en secondes", ss.parse_duree("45") == 45.0)
    for mauvais in ("", "abc", "-5", "0", "10j"):
        leve = False
        try:
            ss.parse_duree(mauvais)
        except Exception:  # noqa: BLE001
            leve = True
        if not check(f"duree invalide refusee : {mauvais!r}", leve):
            break
    check("aucun profil ne propose une cadence nulle ou negative",
          all(p["cadence_s"] > 0 for p in ss.PROFILS.values()),
          " ".join(f"{n}={p['cadence_s']}s" for n, p in ss.PROFILS.items()))

    # --- G-c : les differences --------------------------------------------- #
    print()
    print("G-c - differences entre deux ticks")
    base = etat({"1": fen("brave.exe", [0, 0, 100, 100], fg=True)})
    check("premier tick : rien a comparer, pas d'invention",
          ss.differences(None, base) == [])
    check("rien ne bouge : aucune difference", ss.differences(base, base) == [])

    apparue = etat({"1": fen("brave.exe", [0, 0, 100, 100], fg=True),
                    "2": fen("cmd.exe", [5, 5, 60, 60])})
    d = ss.differences(base, apparue)
    check("une fenetre qui apparait est vue", [x["quoi"] for x in d] == ["fenetre.apparue"])
    check("l'exe de la fenetre apparue est rendu",
          d and d[0]["exe"] == "cmd.exe", "le cas Awareness PC")
    check("une fenetre qui disparait est vue",
          [x["quoi"] for x in ss.differences(apparue, base)] == ["fenetre.disparue"])
    check("un deplacement est vu",
          [x["quoi"] for x in ss.differences(
              base, etat({"1": fen("brave.exe", [10, 0, 110, 100], fg=True)}))]
          == ["fenetre.deplacee"])
    check("un changement de document est vu",
          [x["quoi"] for x in ss.differences(base, etat(base["fenetres"], hote="zzzzzzzz"))]
          == ["document.change"])
    check("un ecran branche ou debranche est vu",
          any(x["quoi"] == "ecrans.change" for x in ss.differences(
              base, {**base, "moniteurs": [[1, 2560, 1440], [2, 1920, 1080]]})))

    # --- G-a / G-b : la trace, de bout en bout ------------------------------ #
    print()
    print("G-a / G-b - la trace, sequence injectee")
    tmp = Path(tempfile.mkdtemp(prefix="smoke-session-"))
    sequence = [
        base,
        base,
        apparue,                                            # cmd.exe apparait
        etat({"1": fen("brave.exe", [0, 0, 100, 100], fg=True)}),  # puis disparait
    ]
    compteur = {"i": 0}

    def faux_etat(avec_hotes: bool = False) -> dict[str, Any]:
        e = sequence[min(compteur["i"], len(sequence) - 1)]
        compteur["i"] += 1
        return e

    vrai = ss.etat_ecran
    ss.etat_ecran = faux_etat
    try:
        pied = ss.executer(1.2, 0.3, "activite", dossier=tmp, image_cle_s=999.0,
                           silencieux=True)
    finally:
        ss.etat_ecran = vrai

    trace = Path(pied["fichier"])
    check("le fichier de session est cree", trace.is_file(), trace.name)
    lignes = lire(trace)
    types = [l["type"] for l in lignes]
    check("la trace commence par session.start", types and types[0] == "session.start")
    check("la trace finit par session.end", types and types[-1] == "session.end")
    check("une image-cle ouvre la session", "session.keyframe" in types,
          "sinon une trace en diffs seule serait irrattrapable")
    check("les changements injectes sont enregistres", "session.diff" in types,
          str([t for t in types if t == "session.diff"]))

    quoi = [c["quoi"] for l in lignes if l["type"] == "session.diff"
            for c in l["changements"]]
    check("la fenetre apparue puis disparue est tracee",
          "fenetre.apparue" in quoi and "fenetre.disparue" in quoi, str(quoi))

    # G-b : identite de session
    check("chaque ligne porte le session_id",
          all(l.get("session_id") == pied["session_id"] for l in lignes))
    check("chaque ligne porte un horodatage relatif",
          all(isinstance(l.get("t_s"), (int, float)) for l in lignes))
    check("les horodatages sont croissants",
          all(a["t_s"] <= b["t_s"] for a, b in zip(lignes, lignes[1:])))
    check("chaque ligne porte la version de schema",
          all(l.get("v") == ss.SCHEMA_SESSION for l in lignes))

    # Pied de session
    for champ in ("duree_reelle_s", "ticks", "changements", "images_cles",
                  "raison_arret", "erreurs_tick"):
        if not check(f"le pied porte {champ}", champ in pied):
            break
    check("la raison d'arret est la fin de duree", pied["raison_arret"] == "duree_atteinte",
          pied["raison_arret"])
    check("la duree reelle ne depasse pas la duree demandee",
          pied["duree_reelle_s"] <= pied["duree_prevue_s"] + 0.5,
          f"{pied['duree_reelle_s']} <= {pied['duree_prevue_s']}")
    check("des ticks ont eu lieu", pied["ticks"] >= 2, str(pied["ticks"]))

    # --- Le pied survit a une interruption ---------------------------------- #
    print()
    print("Arret propre")

    def etat_qui_interrompt(avec_hotes: bool = False) -> dict[str, Any]:
        if compteur.get("stop"):
            raise KeyboardInterrupt
        compteur["stop"] = True
        return base

    ss.etat_ecran = etat_qui_interrompt
    try:
        pied2 = ss.executer(30.0, 0.1, "activite", dossier=tmp, silencieux=True)
    finally:
        ss.etat_ecran = vrai

    check("une interruption arrete la session sans attendre la duree",
          pied2["duree_reelle_s"] < 5.0, f"{pied2['duree_reelle_s']} s au lieu de 30")
    check("la raison d'arret dit l'interruption", pied2["raison_arret"] == "interruption",
          pied2["raison_arret"])
    lignes2 = lire(Path(pied2["fichier"]))
    check("le pied est ecrit MALGRE l'interruption",
          bool(lignes2) and lignes2[-1]["type"] == "session.end",
          "sans lui, une trace coupee passerait pour une session vide")

    # --- Vie privee ---------------------------------------------------------- #
    print()
    print("Vie privee")
    brut = trace.read_text(encoding="utf-8") + Path(pied2["fichier"]).read_text(encoding="utf-8")
    check("aucun titre de fenetre dans la trace", "title" not in brut and "titre" not in brut)
    check("aucune URL dans la trace", "http" not in brut)
    # Liste blanche VOLONTAIRE : tout champ qui entre dans la trace doit etre
    # ajoute ici explicitement. C'est ce qui empeche une donnee de s'y glisser
    # sans que personne l'ait decide -- le garde a fait son travail en R-S13,
    # ou les compteurs de contenu ont d'abord ete refuses.
    permis = {"v", "session_id", "type", "t_s", "etat", "changements", "profil",
              "cadence_s", "duree_prevue_s", "t_wall_ms", "hotes_en_clair",
              "duree_reelle_s", "ticks", "images_cles", "erreurs_tick",
              "cout_tick_p50_ms", "raison_arret", "fichier", "erreur",
              # R-S13, contenu declenche : des COMPTES, jamais du texte.
              "contenu", "prechauffe_ms", "captures_armees", "lectures_faites",
              "lectures_en_echec", "captures_abandonnees", "captures_non_lues",
              # R-S14, riche periodique et relecture. Tous des COMPTES ou des
              # reglages : la cadence riche, ce qu'elle a coute, combien de
              # ticks structurels elle a fait sauter, la taille de la trace.
              # Aucun ne transporte de contenu d'ecran -- le contenu vit dans
              # les lignes `session.decor`, filtrees par le niveau demande.
              "relecture", "relectures_faites", "relectures_perdues",
              "riche_cadence_s", "riches_echantillons", "riche_cout_total_ms",
              "decor_changements", "ticks_attendus", "ticks_sautes",
              "taille_avant_pied_o"}
    surplus = {k for l in lignes + lignes2 for k in l} - permis
    check("aucun champ inattendu au niveau de la ligne", not surplus, str(sorted(surplus)))

    src = inspect.getsource(ss)
    check("l'hote est reduit a une empreinte par defaut", "empreinte(hote)" in src)
    check("data/sessions est gitignore",
          "data/sessions/" in (ROOT / ".gitignore").read_text(encoding="utf-8"))

    # --- Ce que ce lot ne doit PAS toucher ----------------------------------- #
    print()
    print("Perimetre")
    arbre = ast.parse(src)
    noms = {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
    noms |= {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
    imports = {a.name.split(".")[0] for n in ast.walk(arbre)
               if isinstance(n, ast.Import) for a in n.names}
    imports |= {(n.module or "").split(".")[0] for n in ast.walk(arbre)
                if isinstance(n, ast.ImportFrom)}
    check("aucune actuation",
          not (noms & {"SetCursorPos", "mouse_event", "keybd_event", "SendInput", "click"}))
    check("aucune camera : ni cv2, ni mvp_webcam, ni banque de visages",
          not (imports & {"cv2", "mvp_webcam", "face_bank", "emotions_soft", "face_track"}),
          str(sorted(imports)))
    check("aucun modele, aucun OCR dans le tick de base",
          not (imports & {"pytesseract", "torch", "transformers", "ocr_screen"}))
    check("la session n'ecrit pas dans le journal d'events ponctuels",
          "vision_events" not in imports and "EventBus" not in noms,
          "son plafond de 2000 lignes detruirait le debut de la session")

    import vision_events
    check("le plafond du journal ponctuel est INCHANGE",
          vision_events.MAX_EVENT_LINES == 2000, str(vision_events.MAX_EVENT_LINES))

    import screen_api
    check("le contrat photo est intact", screen_api.API_VERSION == "1.1",
          screen_api.API_VERSION)

    # --- Session reelle, bornee, courte -------------------------------------- #
    print()
    print("Session reelle (bornee, <= 3 s)")
    t0 = time.perf_counter()
    pied3 = ss.executer(2.0, 0.5, "activite", dossier=tmp, silencieux=True)
    ecoule = time.perf_counter() - t0
    check("la session s'arrete d'elle-meme", ecoule < 5.0, f"{ecoule:.1f} s")
    check("des ticks reels ont eu lieu", pied3["ticks"] >= 2, str(pied3["ticks"]))
    check("le cout du tick est mesure et rendu", pied3["cout_tick_p50_ms"] is not None,
          f"{pied3['cout_tick_p50_ms']} ms")
    check("aucune erreur de tick", pied3["erreurs_tick"] == 0, str(pied3["erreurs_tick"]))
    check("la trace reelle se relit", bool(lire(Path(pied3["fichier"]))))

    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 70)
    ko = [label for label, ok in CHECKS if not ok]
    print(f"{len(CHECKS) - len(ko)}/{len(CHECKS)} controles OK")
    if ko:
        print("SMOKE KO")
        for label in ko:
            print(f"  - {label}")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
