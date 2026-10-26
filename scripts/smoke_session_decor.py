"""Smoke du regime riche PERIODIQUE et de la relecture declenchee (R-S14).

Usage :
  python scripts/smoke_session_decor.py

Sortie : exit 0 si tous les controles passent, 1 sinon.

CE QUE CE SMOKE SURVEILLE VRAIMENT

Le regime periodique regarde **tout l'ecran, en boucle**. Ce n'est pas la
meme promesse que le declenche de R-S13, qui ne saisit qu'une console venant
d'apparaitre. Quatre choses doivent donc rester vraies :

  1. il est OPT-IN -- sans `--riche`, aucun ecran n'est capture ;
  2. au niveau `resume`, **aucun mot n'est lu ni ecrit** ;
  3. on ecrit des DIFFERENCES : un decor immobile ne produit aucune ligne ;
  4. les ticks structurels sautes sont COMPTES dans le pied.

Le cas `Boo` (C1) a sa propre section : deux captures d'une meme
fenetre doivent se reunir en un seul evenement, sans perdre la ligne que la
premiere avait manquee.

Les etats sont INJECTES : un test qui depend de ce qui passe a l'ecran
pendant cinq secondes n'est pas un test.

VIE PRIVEE : ce smoke n'affiche aucun texte lu a l'ecran, seulement des
comptes.
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

CHECKS: list[tuple[str, bool]] = []
SAUTES: list[tuple[str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, ok))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def saute(bloc: str, raison: str) -> None:
    """Un bloc non exerce n'est PAS un bloc reussi : il doit se voir."""
    SAUTES.append((bloc, raison))
    print(f"  [   ] {bloc} -- NON EXERCE : {raison}")


def lire_trace(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def ech(ecran: int = 1, luma: float = 50.0, theme: str = "sombre",
        dom: list[int] | None = None, noir: bool = False,
        densite: float = 0.01, t_s: float = 0.0, bande: Any = None) -> Any:
    import session_decor as sd

    return sd.EchantillonDecor(
        ecran=ecran, taille=[2560, 1440], capture_ms=33.0, luma=luma,
        ecart_luma=20.0, noir=noir, theme=theme, dominante=dom or [0, 0, 0],
        densite_texte=densite, texte_present=densite > 0.0002, t_s=t_s,
        bande=bande)


def main() -> int:  # noqa: C901
    import session_decor as sd
    import session_screen as ss

    print("=" * 70)
    print("Smoke regime riche periodique + relecture (R-S14)")
    print("=" * 70)

    # --- Differences de decor ------------------------------------------------ #
    print()
    print("Le decor s'ecrit en DIFFERENCES")

    base = ech(luma=50.0, densite=0.01)
    check("un decor identique ne produit aucune ligne",
          sd.differences_decor(base, ech(luma=50.0, densite=0.01)) == [],
          "sinon la trace repete 30 fois la meme chose sur une session de 30 min")
    check("le premier echantillon ne diffe de rien",
          sd.differences_decor(None, base) == [])

    petit = sd.differences_decor(base, ech(luma=50.0 + sd.SEUIL_LUMA / 2, densite=0.01))
    check("une variation de luminance SOUS le seuil est ignoree", petit == [],
          f"seuil {sd.SEUIL_LUMA}, plancher de bruit mesure a 0,20 (p90)")

    gros = sd.differences_decor(base, ech(luma=50.0 + sd.SEUIL_LUMA * 2, densite=0.01))
    check("une variation AU-DESSUS du seuil est ecrite",
          [d["quoi"] for d in gros] == ["decor.luminance"], str(gros))

    quoi = lambda diffs: {d["quoi"] for d in diffs}  # noqa: E731
    check("un changement de theme est vu",
          "decor.theme" in quoi(sd.differences_decor(base, ech(theme="clair"))))
    check("un changement de dominante est vu",
          "decor.dominante" in quoi(sd.differences_decor(base, ech(dom=[224, 0, 0]))))
    check("le passage au noir est vu",
          "decor.noir" in quoi(sd.differences_decor(base, ech(noir=True))),
          "cas Prime/Netflix : on le signale sans conclure au DRM")
    check("l'apparition de texte est vue",
          "decor.texte_present" in quoi(
              sd.differences_decor(ech(densite=0.0), ech(densite=0.05))))

    src_decor = inspect.getsource(sd)
    check("aucune ligne de trace ne conclut au DRM",
          "DRM" not in src_decor.split('"""')[2] if src_decor.count('"""') > 2 else True,
          "trois causes donnent la meme image noire")

    # --- Le seuil de ressemblance du texte ----------------------------------- #
    print()
    print("Le texte s'ecrit QUAND IL CHANGE")

    a = sd.signature_texte(["Attendre 1 secondes...", "BONJOUR VISION"])
    check("la signature normalise casse et ponctuation",
          sd.signature_texte(["BONJOUR, VISION !"]) == sd.signature_texte(["bonjour vision"]))
    check("une lecture identique n'est pas un changement",
          not sd.texte_a_change(a, sd.signature_texte(["attendre 1 secondes",
                                                       "bonjour vision"])))
    check("la premiere lecture est toujours ecrite", sd.texte_a_change(None, a))
    check("une lecture completement differente est un changement",
          sd.texte_a_change(a, sd.signature_texte(["rien a voir", "autre chose"])))
    # Le bruit d'OCR mesure : ressemblance 0,80 a 1,00 sur une image immobile.
    proche = sd.signature_texte(["Attendre 1 secondes...", "BONJOUR VISION", "|"])
    check("le bruit d'OCR mesure (ressemblance 0,80) ne declenche PAS de ligne",
          not sd.texte_a_change(a, proche),
          "une egalite stricte aurait ecrit du bruit : refusee a la mesure")
    check("le seuil est entre les deux populations mesurees",
          0.5 < sd.SEUIL_RESSEMBLANCE < 0.8, str(sd.SEUIL_RESSEMBLANCE))

    # --- Le cas Boo ---------------------------------------------------------- #
    print()
    print("Cas `Boo` : deux captures, un seul evenement")

    fusion = sd.fusionner_lignes(["Boo !!!"],
                                 ["Attendre 1 secondes, appuyez sur une touche"])
    check("la relecture rattrape la ligne que la premiere capture a manquee",
          "Boo !!!" in fusion and len(fusion) == 2, str(fusion))
    dedup = sd.fusionner_lignes(["Attendre 1 secondes, appuyez pour continuer..."],
                                ["Attendre 1 secondes, appuyez pour continuer. |"])
    check("une meme ligne lue deux fois n'apparait qu'une fois", len(dedup) == 1,
          str(dedup))
    check("c'est la lecture la PLUS COMPLETE qui est gardee",
          dedup[0].endswith("continuer..."), str(dedup))
    check("une ligne vide ou purement symbolique est ignoree",
          sd.fusionner_lignes(["", "  ", "|"]) == [])

    evt = ss._fusionner_evenements(
        {"quoi": "contenu.fenetre", "exe": "cmd.exe", "lisible": True, "mots": 2,
         "lignes": 1, "ocr_ms": 200.0, "texte": ["Boo !!!"]},
        {"quoi": "contenu.fenetre", "exe": "cmd.exe", "lisible": True, "mots": 8,
         "lignes": 1, "ocr_ms": 210.0, "texte": ["Attendre 1 secondes"]})
    check("l'evenement fusionne porte les deux lignes",
          evt["texte"] == ["Boo !!!", "Attendre 1 secondes"], str(evt.get("texte")))
    check("il declare qu'il vient de DEUX prises", evt.get("prises") == 2)
    check("il additionne le cout des deux lectures", evt["ocr_ms"] == 410.0)
    check("il garde le compte de mots le plus eleve", evt["mots"] == 8)

    sourd = ss._fusionner_evenements(
        {"quoi": "contenu.fenetre", "lisible": False, "raison": "rien lu", "mots": 0},
        {"quoi": "contenu.fenetre", "lisible": True, "mots": 5})
    check("une premiere lecture vide n'efface pas la seconde",
          sourd["lisible"] and sourd["mots"] == 5 and "raison" not in sourd, str(sourd))

    # --- Bout en bout : le regime periodique --------------------------------- #
    print()
    print("Bout en bout, etats injectes")

    tmp = Path(tempfile.mkdtemp(prefix="smoke-decor-"))
    vrai_etat, vrai_ech = ss.etat_ecran, sd.echantillonner

    fenetres: dict[str, Any] = {
        "111": {"exe": "explorer.exe", "classe": "Shell_TrayWnd",
                "bbox": [0, 0, 100, 40], "ecran": 1, "premier_plan": True},
    }

    def etat_fixe(avec_hotes: bool = False) -> dict[str, Any]:
        return {"moniteurs": [[1, 2560, 1440]],
                "actif": {"exe": "explorer.exe", "hote": None, "confiance": 1.0},
                "fenetres": dict(fenetres)}

    suite = [ech(luma=50.0, t_s=0.0), ech(luma=50.0, t_s=1.0),
             ech(luma=90.0, t_s=2.0), ech(luma=90.0, t_s=3.0)]
    tirage = {"i": 0}

    def faux_ech(ecran, rect, t_s, *, garder_bande=False):  # noqa: ANN001
        i = min(tirage["i"], len(suite) - 1)
        tirage["i"] += 1
        e = suite[i]
        e.ecran = ecran
        return e

    ss.etat_ecran, sd.echantillonner = etat_fixe, faux_ech
    try:
        pied = ss.executer(4.0, 0.5, "activite", dossier=tmp, silencieux=True,
                           contenu="resume", riche_s=1.0, image_cle_s=60.0)
    finally:
        ss.etat_ecran, sd.echantillonner = vrai_etat, vrai_ech

    lignes = lire_trace(Path(pied["fichier"]))
    decors = [l for l in lignes if l["type"] == "session.decor"]
    ancres = [l for l in decors if l.get("quoi") == "decor.initial"]
    diffs = [l for l in decors if "changements" in l]

    # Un ancrage PAR ECRAN, et un seul : le nombre de moniteurs reels du
    # poste ne doit pas decider si ce controle passe.
    ecrans_ancres = [a["ecran"] for a in ancres]
    ecrans_diffes = {l["ecran"] for l in diffs}
    check("chaque ecran est ancre une fois et une seule",
          len(ecrans_ancres) == len(set(ecrans_ancres)) and len(ancres) >= 1,
          f"ancrages sur les ecrans {ecrans_ancres}")
    check("aucun ecran ne diffe sans avoir ete ancre d'abord",
          ecrans_diffes <= set(ecrans_ancres),
          "une trace differentielle sans ancre est irrattrapable")
    check("un decor immobile n'ecrit rien du tout",
          len(decors) < pied["riches_echantillons"],
          f"{len(decors)} lignes pour {pied['riches_echantillons']} echantillons")
    check("un changement de luminance ecrit une ligne",
          any(c["quoi"] == "decor.luminance" for l in diffs for c in l["changements"]),
          str(diffs))
    check("le pied declare la cadence riche", pied["riche_cadence_s"] == 1.0)
    check("le pied compte les echantillons", pied["riches_echantillons"] >= 3,
          str(pied["riches_echantillons"]))
    check("le pied compte les ticks attendus et les ticks sautes",
          "ticks_attendus" in pied and "ticks_sautes" in pied
          and pied["ticks_sautes"] == max(0, pied["ticks_attendus"] - pied["ticks"]),
          f"{pied['ticks']} faits / {pied['ticks_attendus']} attendus")
    check("le pied donne la taille de la trace",
          isinstance(pied["taille_avant_pied_o"], int) and pied["taille_avant_pied_o"] > 0,
          f"{pied['taille_avant_pied_o']} o")
    check("le pied dit le cout total du regime riche",
          pied["riche_cout_total_ms"] >= 0.0)

    # --- Opt-in et vie privee ------------------------------------------------ #
    print()
    print("Opt-in et vie privee")

    tirage["i"] = 0
    appels = {"n": 0}

    def ech_compte(ecran, rect, t_s, *, garder_bande=False):  # noqa: ANN001
        appels["n"] += 1
        return ech(t_s=t_s)

    ss.etat_ecran, sd.echantillonner = etat_fixe, ech_compte
    try:
        pied_off = ss.executer(1.5, 0.5, "activite", dossier=tmp, silencieux=True,
                               contenu="resume")
    finally:
        ss.etat_ecran, sd.echantillonner = vrai_etat, vrai_ech

    check("SANS --riche, aucun ecran n'est echantillonne", appels["n"] == 0,
          f"{appels['n']} echantillon(s) : le regime doit rester opt-in")
    check("le pied dit alors que la cadence riche est absente",
          pied_off["riche_cadence_s"] is None)
    check("le regime eteint n'ecrit aucune ligne de decor",
          not [l for l in lire_trace(Path(pied_off["fichier"]))
               if l["type"] == "session.decor"])

    check("au niveau `resume`, la bande n'est jamais gardee en memoire",
          sd.lire_bande(ech(bande="IMAGE"), "resume") is None,
          "aucun OCR : la densite de contours suffit a dire qu'il y a du texte")
    check("au niveau `none` non plus", sd.lire_bande(ech(bande="IMAGE"), "none") is None)
    check("une bande sans contour n'est pas lue",
          sd.lire_bande(ech(densite=0.0, bande="IMAGE"), "texte") is None,
          "inutile de payer 1000 ms pour apprendre qu'une zone unie est vide")

    brut = "".join(Path(p["fichier"]).read_text(encoding="utf-8")
                   for p in (pied, pied_off))
    check("aucun texte lu ne fuit au niveau `resume`", '"texte"' not in brut)
    check("aucun titre de fenetre dans la trace",
          "title" not in brut and "titre" not in brut)
    check("aucune image n'atteint le disque",
          not list(tmp.glob("*.png")) and not list(tmp.glob("*.jpg")))

    src = inspect.getsource(sd) + inspect.getsource(ss)
    check("aucune ecriture d'image dans le code du lot",
          "imwrite" not in src and "save_dir" not in src)
    check("la capture passe toujours save_dir=None",
          '_capture("ecran", (l, t, r, b), None, "periodique")' in inspect.getsource(sd))
    check("la bande gardee est une COPIE, pas une vue sur la frame entiere",
          "bande.copy()" in inspect.getsource(sd),
          "une tranche numpy garderait 25 Mo vivants au lieu de 3")

    # --- La fenetre qui apparait pile sur une image-cle ----------------------- #
    print()
    print("Le trou de l'image-cle (defaut R-S10, corrige)")

    src_ss = inspect.getsource(ss)
    arbre = ast.parse(src_ss)
    fn = next(n for n in ast.walk(arbre)
              if isinstance(n, ast.FunctionDef) and n.name == "executer")
    corps = ast.dump(fn)
    check("les differences sont calculees a TOUS les ticks",
          corps.index("differences") < corps.index("session.keyframe"),
          "R-S10 les sautait sur un tick d'image-cle : une fenetre sur 60 "
          "n'etait jamais capturee")

    armees = {"n": 0}
    vrai_armer = ss._armer_captures

    def armer_compte(diffs, etat, t_s, classes, file, a_revoir=None):  # noqa: ANN001
        armees["n"] += len(([d for d in diffs if d.get("quoi") == "fenetre.apparue"]))
        return (0, 0)

    def etat_qui_ouvre(avec_hotes: bool = False) -> dict[str, Any]:
        e = etat_fixe()
        if tirage.get("ouvert"):
            e["fenetres"]["222"] = {"exe": "cmd.exe", "classe": "ConsoleWindowClass",
                                    "bbox": [10, 10, 500, 300], "ecran": 1,
                                    "premier_plan": True}
        tirage["ouvert"] = True
        return e

    ss.etat_ecran, ss._armer_captures = etat_qui_ouvre, armer_compte
    try:
        # image_cle_s tres court : chaque tick est un tick d'image-cle.
        ss.executer(1.5, 0.5, "activite", dossier=tmp, silencieux=True,
                    contenu="resume", image_cle_s=0.01)
    finally:
        ss.etat_ecran, ss._armer_captures = vrai_etat, vrai_armer

    check("une fenetre apparue sur un tick d'image-cle arme quand meme",
          armees["n"] >= 1, f"{armees['n']} capture(s) armee(s)")

    # --- Perimetre ----------------------------------------------------------- #
    print()
    print("Perimetre")

    arbre_d = ast.parse(inspect.getsource(sd))
    imports = {a.name.split(".")[0] for n in ast.walk(arbre_d)
               if isinstance(n, ast.Import) for a in n.names}
    imports |= {(n.module or "").split(".")[0] for n in ast.walk(arbre_d)
                if isinstance(n, ast.ImportFrom)}
    noms = {n.attr for n in ast.walk(arbre_d) if isinstance(n, ast.Attribute)}

    check("aucune camera", not (imports & {"mvp_webcam", "face_bank", "face_track",
                                           "emotions_soft", "body_pose"}),
          str(sorted(imports)))
    check("aucune actuation",
          not (noms & {"SetCursorPos", "mouse_event", "keybd_event", "SendInput"}))
    check("aucun VLM, aucun telechargement de modele",
          not (imports & {"torch", "transformers", "huggingface_hub"})
          and "moondream" not in src_decor.lower())
    check("aucun code d'agent tiers ni export-cloud",
          not (imports & {"agent_tiers", "export_cloud"}) and "agent_tiers" not in src_decor.lower())
    check("le regime riche n'ouvre aucun daemon",
          "while True" not in src_decor and "schedule" not in src_decor)

    import screen_api
    check("le contrat photo reste fige en 1.1", screen_api.API_VERSION == "1.1",
          screen_api.API_VERSION)
    import vision_events
    check("le plafond du journal ponctuel est INCHANGE",
          vision_events.MAX_EVENT_LINES == 2000, str(vision_events.MAX_EVENT_LINES))
    import session_contenu
    check("le declenche de R-S13 garde ses classes et son plafond",
          session_contenu.FILE_MAX == 8
          and "ConsoleWindowClass" in session_contenu.CLASSES_DECLENCHEUSES)
    check("data/sessions est gitignore",
          "data/sessions/" in (ROOT / ".gitignore").read_text(encoding="utf-8"))

    # --- Cout reel ----------------------------------------------------------- #
    print()
    print("Cout mesure")
    try:
        from screen_content import _capture

        _capture("prechauffe", (0, 0, 8, 8), None, "p")
        cout = sd.cout_echantillon_ms((0, 0, 640, 480))
        check("un echantillon de decor coute moins qu'un dixieme de seconde",
              cout < 100.0, f"{cout} ms sur 640x480")
    except Exception as exc:  # noqa: BLE001
        saute("cout d'un echantillon reel", f"capture indisponible ({type(exc).__name__})")

    shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=" * 70)
    ko = [label for label, ok in CHECKS if not ok]
    print(f"{len(CHECKS) - len(ko)}/{len(CHECKS)} controles OK")
    if SAUTES:
        print(f"{len(SAUTES)} bloc(s) NON EXERCE(S) :")
        for bloc, raison in SAUTES:
            print(f"  - {bloc} : {raison}")
        print("Un bloc saute n'est pas un bloc reussi.")
    if ko:
        print("SMOKE KO")
        for label in ko:
            print(f"  - {label}")
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
