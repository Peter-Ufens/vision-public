"""Smoke du contenu declenche (R-S13).

Usage :
  python scripts/smoke_session_contenu.py

Sortie : exit 0 si tous les controles passent, 1 sinon.

CE QUE CE SMOKE SURVEILLE VRAIMENT

Ce lot ajoute des PIXELS a une session qui n'en avait pas. Quatre choses
peuvent mal tourner sans qu'on le voie :

  1. une image atterrit sur le disque ;
  2. le texte lu se retrouve dans la trace alors que le niveau est `resume` ;
  3. une zone noire (Prime Video, Netflix) est racontee comme une scene au
     lieu d'etre declaree illisible ;
  4. la session cesse d'etre legere quand on ne demande aucun contenu.

Les controles ci-dessous portent d'abord sur ces quatre-la.

VIE PRIVEE : aucun texte lu n'est affiche par ce smoke.
"""

from __future__ import annotations

import ast
import inspect
import json
import shutil
import subprocess
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
    SAUTES.append((bloc, raison))
    print(f"  [-- ] {bloc} : non exerce ({raison})")


def lire_trace(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class PriseFactice:
    """Une prise de vue scriptee : noire ou non, sans toucher a l'ecran."""

    def __init__(self, noir: bool, frame: Any = "IMAGE") -> None:
        self.exe = "cmd.exe"
        self.classe = "ConsoleWindowClass"
        self.bbox = [0, 0, 100, 100]
        self.ecran = 1
        self.t_s = 1.0
        self.largeur = 100
        self.hauteur = 100
        self.capture_ms = 20.0
        self.luma = 0.3 if noir else 90.0
        self.ecart_luma = 3.0 if noir else 50.0
        self.noir = noir
        self.frame = frame
        self.libere = False

    def liberer(self) -> None:
        self.frame = None
        self.libere = True


def main() -> int:  # noqa: C901
    print("=" * 70)
    print("SMOKE contenu declenche (R-S13)")
    print("=" * 70)

    import session_contenu as sc
    import session_screen as ss

    # --- Declenchement : sur quoi arme-t-on ? -------------------------------- #
    print()
    print("Declenchement")
    check("une console classique arme", sc.doit_declencher("cmd.exe", "ConsoleWindowClass"))
    check("Windows Terminal arme",
          sc.doit_declencher("wt.exe", "CASCADIA_HOSTING_WINDOW_CLASS"))
    check("une boite de dialogue arme", sc.doit_declencher("setup.exe", "#32770"))
    check("PowerShell arme meme si la classe est inconnue",
          sc.doit_declencher("powershell.exe", "ClasseInattendue"),
          "l'hote de console varie : l'exe sert de filet")
    check("un navigateur n'arme PAS",
          not sc.doit_declencher("msedge.exe", "Chrome_WidgetWin_1"))
    check("un jeu n'arme PAS", not sc.doit_declencher("Spider-Man2.exe", "GameNxApp"))
    check("la liste des classes est extensible",
          sc.doit_declencher("x.exe", "MaClasse", classes=("MaClasse",), exes=()))
    check("une fenetre sans classe ni exe connus n'arme pas",
          not sc.doit_declencher("", ""))

    # --- Niveaux de contenu : ce qui sort, et ce qui ne sort pas ------------- #
    print()
    print("Niveaux de contenu")
    check("trois niveaux, pas plus", set(sc.NIVEAUX) == {"none", "resume", "texte"})
    check("le defaut est resume", sc.NIVEAU_DEFAUT == "resume")
    check("le defaut de la CLI est resume",
          ss.parse_args(["--duree", "5s"]).contenu == "resume")
    check("on peut revenir a une session strictement legere",
          ss.parse_args(["--duree", "5s", "--contenu", "none"]).contenu == "none")

    # --- Zone noire : Prime Video, Netflix, ecran eteint --------------------- #
    print()
    print("Zone noire - le cas Prime / Netflix")
    evt = sc.lire(PriseFactice(noir=True), "texte")
    check("une zone noire n'est PAS declaree lisible", evt["lisible"] is False)
    check("la raison est ecrite", bool(evt.get("raison")), evt.get("raison", "")[:48])
    check("les trois causes possibles restent distinguees",
          "DRM" in evt["raison"] and "eteint" in evt["raison"],
          "on ne conclut pas au DRM sur ce seul signal")
    check("aucun texte n'est invente sur une zone noire", "texte" not in evt)
    check("aucune scene n'est decrite", not any(
        k in evt for k in ("scene", "description", "resume_scene")))
    check("l'identite du processus prend le relais", "identite" in evt,
          "le repli du technicien : dire QUELLE appli tourne")

    # --- Le texte ne sort qu'au niveau texte --------------------------------- #
    print()
    print("Vie privee - le texte")
    faux_ocr = {"available": True, "lines": [{"text": "SECRET"}], "words": 1,
                "elapsed_ms": 10.0, "reason": ""}
    import screen_content as scn

    vrai_ocr = scn._ocr
    scn._ocr = lambda cap, lang, conf: faux_ocr
    try:
        ev_resume = sc.lire(PriseFactice(noir=False), "resume")
        ev_texte = sc.lire(PriseFactice(noir=False), "texte")
    finally:
        scn._ocr = vrai_ocr

    check("au niveau resume, AUCUN texte n'est rendu", "texte" not in ev_resume,
          str(sorted(ev_resume)))
    check("au niveau resume, les comptes le sont", ev_resume.get("mots") == 1)
    check("au niveau texte, le texte est rendu", ev_texte.get("texte") == ["SECRET"])
    check("le niveau resume ne laisse fuir aucun mot lu",
          "SECRET" not in json.dumps(ev_resume, ensure_ascii=False))

    # --- Aucune image sur le disque ------------------------------------------ #
    print()
    print("Aucune image sur disque")
    src_c = inspect.getsource(sc)
    src_s = inspect.getsource(ss)
    check("aucune ecriture d'image dans le module contenu", "imwrite" not in src_c)
    check("aucune ecriture d'image dans la session", "imwrite" not in src_s)
    check("aucun save_dir n'est passe a la capture", "save_dir" not in src_c)
    check("la prise de vue sait se liberer", hasattr(sc.PriseVue, "liberer"))
    p = PriseFactice(noir=False)
    sc.lire(p, "resume")
    check("la frame reste accessible a l'appelant pour etre liberee",
          p.frame is not None, "c'est la session qui libere, explicitement")

    # --- File d'attente bornee ------------------------------------------------ #
    print()
    print("File d'attente")
    check("la file est plafonnee", isinstance(sc.FILE_MAX, int) and sc.FILE_MAX > 0,
          str(sc.FILE_MAX))
    etat = {"fenetres": {"1": {"exe": "cmd.exe", "classe": "ConsoleWindowClass",
                              "bbox": [0, 0, 10, 10], "ecran": 1, "premier_plan": False}}}
    diffs = [{"quoi": "fenetre.apparue", "exe": "cmd.exe", "classe": "ConsoleWindowClass",
              "bbox": [0, 0, 10, 10], "ecran": 1}]
    file: list[Any] = []
    total_armees = total_abandon = 0
    for _ in range(sc.FILE_MAX + 3):
        a, b = ss._armer_captures(diffs, etat, 1.0, sc.CLASSES_DECLENCHEUSES, file)
        total_armees += a
        total_abandon += b
    check("la file ne depasse jamais son plafond", len(file) <= sc.FILE_MAX,
          f"{len(file)} <= {sc.FILE_MAX}")
    check("les captures abandonnees sont COMPTEES, pas perdues en silence",
          total_abandon >= 1, f"{total_abandon} abandonnee(s) sur {total_armees}")
    for pr in file:
        pr.liberer()

    check("un diff sans fenetre apparue n'arme rien",
          ss._armer_captures([{"quoi": "fenetre.deplacee", "exe": "cmd.exe"}],
                             etat, 1.0, sc.CLASSES_DECLENCHEUSES, []) == (0, 0))

    # --- La session : compteurs et vidage de la file -------------------------- #
    print()
    print("Session - compteurs et file videe")
    tmp = Path(tempfile.mkdtemp(prefix="smoke-contenu-"))
    base = {"moniteurs": [[1, 2560, 1440]],
            "actif": {"exe": "brave.exe", "hote": "aaaa1111", "confiance": "high"},
            "fenetres": {"1": {"exe": "brave.exe", "classe": "Chrome_WidgetWin_1",
                               "bbox": [0, 0, 100, 100], "ecran": 1, "premier_plan": True}}}
    avec_console = json.loads(json.dumps(base))
    avec_console["fenetres"]["2"] = {"exe": "cmd.exe", "classe": "ConsoleWindowClass",
                                     "bbox": [10, 10, 60, 60], "ecran": 1,
                                     "premier_plan": False}
    sequence = [base, avec_console, base]
    compteur = {"i": 0}

    def faux_etat(avec_hotes: bool = False) -> dict[str, Any]:
        e = sequence[min(compteur["i"], len(sequence) - 1)]
        compteur["i"] += 1
        return e

    vrai_etat = ss.etat_ecran
    ss.etat_ecran = faux_etat
    try:
        pied = ss.executer(1.2, 0.3, "activite", dossier=tmp, image_cle_s=999.0,
                           silencieux=True, contenu="resume")
    finally:
        ss.etat_ecran = vrai_etat

    for champ in ("contenu", "captures_armees", "lectures_faites",
                  "lectures_en_echec", "captures_abandonnees", "captures_non_lues"):
        if not check(f"le pied porte {champ}", champ in pied):
            break
    check("le niveau de contenu est trace", pied.get("contenu") == "resume")
    check("une console apparue a arme une capture", pied["captures_armees"] >= 1,
          str(pied["captures_armees"]))
    check("la file est VIDE a la fin : rien n'est saisi puis oublie",
          pied["captures_non_lues"] == 0, str(pied["captures_non_lues"]))
    check("toute capture armee a ete lue ou comptee en echec",
          pied["captures_armees"] == pied["lectures_faites"]
          + pied["lectures_en_echec"] + pied["captures_abandonnees"],
          f"{pied['captures_armees']} = {pied['lectures_faites']} + "
          f"{pied['lectures_en_echec']} + {pied['captures_abandonnees']}")

    lignes = lire_trace(Path(pied["fichier"]))
    types = [l["type"] for l in lignes]
    check("un evenement de contenu est trace", "session.contenu" in types, str(set(types)))
    contenus = [l for l in lignes if l["type"] == "session.contenu"]
    if contenus:
        check("l'evenement porte l'exe de la fenetre",
              contenus[0].get("exe") == "cmd.exe")
        check("l'evenement porte l'instant ou la fenetre a ete vue",
              "vue_a_t_s" in contenus[0])
        check("au niveau resume, aucune ligne de texte dans la trace",
              not any("texte" in l for l in contenus))
    check("aucune image n'a ete ecrite dans le dossier de session",
          not list(tmp.glob("*.png")) and not list(tmp.glob("*.jpg")))

    # --- Sans contenu, la session reste strictement legere -------------------- #
    print()
    print("Session legere quand on ne demande rien")
    compteur["i"] = 0
    ss.etat_ecran = faux_etat
    try:
        pied0 = ss.executer(0.6, 0.3, "activite", dossier=tmp, image_cle_s=999.0,
                            silencieux=True, contenu="none")
    finally:
        ss.etat_ecran = vrai_etat
    check("aucune capture n'est armee", pied0["captures_armees"] == 0)
    l0 = lire_trace(Path(pied0["fichier"]))
    check("aucun evenement de contenu",
          not any(l["type"] == "session.contenu" for l in l0))

    # La vraie garantie se mesure en PROCESSUS NEUF : ce smoke importe cv2
    # pour ses propres besoins, donc un test sur sys.modules ici mesurerait
    # son propre effet de bord. Meme parade qu'en R-S5 et R-S8.
    code = (
        "import sys; sys.path.insert(0, r'" + str(ROOT / "src") + "');"
        "sys.path.insert(0, r'" + str(ROOT / "scripts") + "');"
        "import session_screen;"
        "print('CV2', 'cv2' in sys.modules)"
    )
    try:
        pr = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=180)
        charge = None
        for ligne in (pr.stdout or "").splitlines():
            if ligne.startswith("CV2 "):
                charge = ligne.split()[1] == "True"
        check("importer la session ne charge pas cv2 (processus neuf)", charge is False,
              "le tick structurel reste sans pixel")
    except Exception as exc:  # noqa: BLE001
        check("verification cv2 en processus neuf", False, str(exc))

    # --- Perimetre ------------------------------------------------------------ #
    print()
    print("Perimetre")
    arbre = ast.parse(src_c)
    imports = {a.name.split(".")[0] for n in ast.walk(arbre)
               if isinstance(n, ast.Import) for a in n.names}
    imports |= {(n.module or "").split(".")[0] for n in ast.walk(arbre)
                if isinstance(n, ast.ImportFrom)}
    noms = {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
    noms |= {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
    check("aucune camera", not (imports & {"mvp_webcam", "face_bank", "session_camera"}))
    check("aucun VLM, aucun modele nouveau",
          not (imports & {"torch", "transformers", "moondream_screen"}))
    check("aucune actuation",
          not (noms & {"SetCursorPos", "mouse_event", "keybd_event", "SendInput"}))

    import screen_api
    check("le contrat photo est intact", screen_api.API_VERSION == "1.1")
    check("les profils de session n'ont pas bouge",
          ss.PROFIL_DEFAUT == "activite" and set(ss.PROFILS) ==
          {"film", "activite", "debug-avatar"})

    # --- Chemin reel, si l'ecran s'y prete ----------------------------------- #
    print()
    print("Capture reelle")
    try:
        from screen_context import list_monitors, list_windows

        mons = list_monitors()
        fens = [w for w in list_windows(monitors=mons)
                if not w.is_overlay and not w.is_cloaked]
    except Exception:  # noqa: BLE001
        fens = []
    if not fens:
        saute("capture reelle", "aucune fenetre applicative ouverte")
    else:
        sc.prechauffer()
        w = fens[0]
        prise = sc.capturer(w.exe_name, w.class_name, list(w.rect_visible),
                            w.monitor_index, 0.0)
        if prise is None:
            saute("capture reelle", "capture refusee par le systeme")
        else:
            check("une vraie fenetre est capturee", prise.largeur > 0 and prise.hauteur > 0,
                  f"{prise.largeur}x{prise.hauteur} en {prise.capture_ms:.0f} ms")
            evt = sc.lire(prise, "resume", hwnd=w.hwnd)
            check("la lecture rend un evenement complet",
                  evt["quoi"] == "contenu.fenetre" and "lisible" in evt)
            check("une fenetre illisible porte l'identite du processus",
                  evt["lisible"] or "identite" in evt,
                  "lisible" if evt["lisible"] else "identite fournie")
            check("aucun texte au niveau resume", "texte" not in evt)
            prise.liberer()
            check("la frame est liberee apres lecture", prise.frame is None)

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
