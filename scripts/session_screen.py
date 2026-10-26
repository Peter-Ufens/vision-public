"""Session ecran bornee - l'oeil du labo sur l'ordinateur (R-S10).

Voir ce qui se passe sur le PC **d'un point A a un point B**, sur une duree :
fenetres qui apparaissent, disparaissent, se deplacent, passent au premier
plan. Pas un cliche, pas un film video : une trace des CHANGEMENTS dans le
temps.

CE QUI SEPARE CECI D'UN DAEMON
------------------------------

La duree est **obligatoire**. Il n'existe aucune valeur par defaut infinie,
aucun mode "jusqu'a nouvel ordre". La commande est le GO, sa fin est l'arret.
Rien ne survit a la fermeture du terminal. Decision I7.

CE QU'UN TICK REGARDE, ET CE QU'IL NE REGARDE PAS
-------------------------------------------------

Un tick lit les moniteurs, les fenetres et le contexte de la fenetre active.
**Aucun pixel n'est capture. Aucun OCR, aucun modele, aucune VRAM.** Mesure
R-S9 : 12,8 ms par tick, soit 1,3 % d'un coeur a 1 Hz.

Le cliche `screen_api` reste disponible a cote, inchange, pour qui veut des
pixels a un instant donne.

POURQUOI DES DIFFERENCES ET PAS DES ETATS
------------------------------------------

Une session de 30 min a 1 Hz fait 1800 ticks. Enregistrer 1800 etats complets
serait illisible et gros pour rien : mesure R-S9, ~21 % des ticks seulement
voient un changement. On ecrit donc ce qui a CHANGE, plus une **image-cle**
toutes les 60 s.

L'image-cle n'est pas un luxe : une trace purement differentielle est
irrattrapable si on la rejoint en cours de route ou si une ligne manque. Avec
une ancre par minute, une session de 30 min reste lisible depuis trente
points.

EXEMPLE D'USAGE - CONSCIENCE DE L'ENVIRONNEMENT PC
---------------------------------------------------

Une fenetre `cmd.exe` qui apparait deux secondes puis se ferme passe entre
deux clics et n'est sur aucune photo. Une session a 1 Hz la voit apparaitre,
et la voit disparaitre, avec sa duree de vie.

VIE PRIVEE
----------

**Aucun titre de fenetre n'est jamais enregistre.** L'hote d'un document est
reduit par defaut a une empreinte de 8 caracteres : on voit qu'il a CHANGE
sans conserver ou l'utilisateur est alle. `--avec-hotes` enregistre les hotes en
clair, par choix explicite.

`data/sessions/` est gitignore, purge par age et par taille. Local, aucun
reseau.

Usage :
  python scripts/session_screen.py --duree 10m
  python scripts/session_screen.py --duree 30m --profil film
  python scripts/session_screen.py --duree 90s --profil debug-avatar
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SESSIONS_DIR = ROOT / "data" / "sessions"

SCHEMA_SESSION = 1

# Cadences du plan R-S9 S 5.2, mesurees.
PROFILS: dict[str, dict[str, Any]] = {
    "film": {
        "cadence_s": 5.0,
        "pourquoi": "une seance ne bouge pas vite : contexte a jour, ~0,3 % de CPU",
    },
    "activite": {
        "cadence_s": 1.0,
        "pourquoi": "fenetres, apps deplacees, onglets : 1,3 % de CPU mesure",
    },
    "debug-avatar": {
        "cadence_s": 0.25,
        "pourquoi": "voir un desync dans le temps : 8,3 % de CPU mesure",
    },
}
PROFIL_DEFAUT = "activite"

INTERVALLE_IMAGE_CLE_S = 60.0

# Retention : une session est une trace personnelle, elle ne s'accumule pas.
RETENTION_JOURS_DEFAUT = 7
TAILLE_MAX_MO_DEFAUT = 50


def parse_duree(texte: str) -> float:
    """`10m`, `30m`, `90s`, `600` -> secondes. Leve si ce n'est pas une duree."""
    t = str(texte).strip().lower()
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(s|m|h)?", t)
    if not m:
        raise argparse.ArgumentTypeError(
            f"duree illisible : {texte!r}. Attendu '10m', '30m', '90s' ou un nombre de secondes."
        )
    valeur = float(m.group(1).replace(",", "."))
    facteur = {"s": 1.0, "m": 60.0, "h": 3600.0}.get(m.group(2) or "s", 1.0)
    secondes = valeur * facteur
    if secondes <= 0:
        raise argparse.ArgumentTypeError("la duree doit etre strictement positive")
    return secondes


def empreinte(valeur: str) -> str:
    return hashlib.sha256((valeur or "").encode("utf-8")).hexdigest()[:8]


# --------------------------------------------------------------------------- #
# Tick : CPU seul, aucun pixel
# --------------------------------------------------------------------------- #

def etat_ecran(avec_hotes: bool = False) -> dict[str, Any]:
    """Ce que l'oeil voit a cet instant. Ne capture aucun pixel."""
    from screen_api import get_context_snapshot
    from screen_context import list_monitors, list_windows

    moniteurs = list_monitors()
    fenetres = [w for w in list_windows(monitors=moniteurs)
                if not w.is_overlay and not w.is_cloaked]
    snap = get_context_snapshot()
    doc = snap.get("document") or {}
    hote = doc.get("host") or ""
    proc = snap.get("process") or {}

    return {
        "moniteurs": [[m.index, m.width, m.height] for m in moniteurs],
        "actif": {
            "exe": proc.get("exe_name"),
            "hote": hote if avec_hotes else (empreinte(hote) if hote else None),
            "confiance": snap.get("confidence"),
        },
        # Cle = hwnd. Sur une session bornee, la reutilisation d'un handle par
        # Windows apres fermeture est assez improbable pour ne pas fausser le
        # suivi ; sur 24 h elle le fausserait, raison de plus pour borner.
        "fenetres": {
            str(w.hwnd): {
                "exe": w.exe_name,
                "classe": w.class_name,
                "bbox": list(w.rect_visible),
                "ecran": w.monitor_index,
                "premier_plan": w.is_foreground,
            }
            for w in fenetres
        },
    }


def differences(avant: dict[str, Any] | None, apres: dict[str, Any]) -> list[dict[str, Any]]:
    """Ce qui a change entre deux ticks. Liste vide = rien n'a bouge."""
    if avant is None:
        return []
    out: list[dict[str, Any]] = []
    fa, fp = avant["fenetres"], apres["fenetres"]

    for hwnd in fp.keys() - fa.keys():
        w = fp[hwnd]
        out.append({"quoi": "fenetre.apparue", "exe": w["exe"], "classe": w["classe"],
                    "bbox": w["bbox"], "ecran": w["ecran"]})
    for hwnd in fa.keys() - fp.keys():
        w = fa[hwnd]
        out.append({"quoi": "fenetre.disparue", "exe": w["exe"], "classe": w["classe"]})
    for hwnd in fa.keys() & fp.keys():
        a, b = fa[hwnd], fp[hwnd]
        if a["bbox"] != b["bbox"]:
            out.append({"quoi": "fenetre.deplacee", "exe": b["exe"],
                        "de": a["bbox"], "vers": b["bbox"], "ecran": b["ecran"]})
        if a["ecran"] != b["ecran"]:
            out.append({"quoi": "fenetre.change_ecran", "exe": b["exe"],
                        "de": a["ecran"], "vers": b["ecran"]})
        if a["premier_plan"] != b["premier_plan"] and b["premier_plan"]:
            out.append({"quoi": "fenetre.premier_plan", "exe": b["exe"]})

    if avant["actif"]["hote"] != apres["actif"]["hote"]:
        out.append({"quoi": "document.change", "exe": apres["actif"]["exe"],
                    "hote": apres["actif"]["hote"]})
    if avant["moniteurs"] != apres["moniteurs"]:
        out.append({"quoi": "ecrans.change", "moniteurs": apres["moniteurs"]})
    return out


# --------------------------------------------------------------------------- #
# Trace
# --------------------------------------------------------------------------- #

class TraceSession:
    """Le journal de la session. Un fichier par session, jamais partage.

    N'ecrit PAS dans `data/events/vision_events.jsonl` : ce journal-la est
    plafonne a 2000 lignes et garde les DERNIERES, ce qui detruirait le debut
    d'une session -- c'est-a-dire le point A (ecart G-a, R-S9). Son plafond
    reste correct pour ce qu'il fait : des events ponctuels.
    """

    def __init__(self, session_id: str, dossier: Path = SESSIONS_DIR) -> None:
        self.session_id = session_id
        dossier.mkdir(parents=True, exist_ok=True)
        self.path = dossier / f"{session_id}.jsonl"
        self._f = self.path.open("w", encoding="utf-8")
        self.lignes = 0

    def ecrire(self, type_: str, t_s: float, **corps: Any) -> None:
        ligne = {"v": SCHEMA_SESSION, "session_id": self.session_id,
                 "type": type_, "t_s": round(t_s, 3), **corps}
        self._f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        self._f.flush()  # une session interrompue doit rester lisible
        self.lignes += 1

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:  # noqa: BLE001
            pass


def purger(dossier: Path, jours: int, taille_max_mo: int) -> int:
    """Age puis taille, du plus ancien au plus recent. Rend le nombre supprime."""
    if not dossier.exists():
        return 0
    fichiers = sorted(dossier.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    limite = time.time() - jours * 86400
    supprimes = 0
    for p in list(fichiers):
        if p.stat().st_mtime < limite:
            try:
                p.unlink()
                fichiers.remove(p)
                supprimes += 1
            except OSError:
                pass
    total = sum(p.stat().st_size for p in fichiers)
    plafond = taille_max_mo * 1024 * 1024
    for p in fichiers:
        if total <= plafond:
            break
        try:
            total -= p.stat().st_size
            p.unlink()
            supprimes += 1
        except OSError:
            pass
    return supprimes


# --------------------------------------------------------------------------- #

def _armer_captures(diffs: list[dict[str, Any]], etat: dict[str, Any],
                    t_s: float, classes: tuple[str, ...],
                    file: list[Any], a_revoir: list[dict[str, Any]] | None = None,
                    ) -> tuple[int, int]:
    """Saisir les pixels des fenetres qui viennent d'apparaitre (R-S13).

    Appele DANS le tick, juste apres le calcul des differences, parce que la
    fenetre peut avoir disparu au tick suivant : mesure R-S12, des fenetres
    ont vecu 1,0 s et 2,0 s. La capture coute 13 a 31 ms ; la lecture, elle,
    attend le temps mort entre deux ticks.

    `a_revoir` inscrit la fenetre pour une SECONDE capture au tick suivant
    (R-S14, cas `Boo`) : ce qui n'etait pas encore affiche a la premiere
    saisie l'est peut-etre a la seconde.
    """
    import session_contenu as sc

    armees = abandonnees = 0
    for d in diffs:
        if d.get("quoi") != "fenetre.apparue":
            continue
        if not sc.doit_declencher(d.get("exe", ""), d.get("classe", ""), classes=classes):
            continue
        hwnd = None
        for h, w in etat["fenetres"].items():
            if w["exe"] == d.get("exe") and w["bbox"] == d.get("bbox"):
                hwnd = int(h)
                break
        prise = sc.capturer(d.get("exe", ""), d.get("classe", ""),
                            d.get("bbox") or [], d.get("ecran"), t_s)
        if prise is None:
            continue
        if len(file) >= sc.FILE_MAX:
            # Une capture perdue en silence serait pire que pas de capture :
            # on jette la plus ancienne et le pied de session le compte.
            file.pop(0).liberer()
            abandonnees += 1
        setattr(prise, "hwnd", hwnd)
        setattr(prise, "cle", _cle_prise(prise))
        setattr(prise, "attend_relecture", a_revoir is not None)
        file.append(prise)
        armees += 1
        if a_revoir is not None:
            a_revoir.append({"cle": getattr(prise, "cle"), "hwnd": hwnd,
                             "exe": d.get("exe", ""), "classe": d.get("classe", ""),
                             "bbox": d.get("bbox") or [], "ecran": d.get("ecran")})
    return (armees, abandonnees)


def _cle_prise(prise: Any) -> str:
    """Rapprocher deux captures de la MEME fenetre. hwnd si connu, sinon
    l'executable et le rectangle -- les deux seuls invariants disponibles."""
    h = getattr(prise, "hwnd", None)
    if h:
        return f"hwnd:{h}"
    return f"{prise.exe}:{','.join(str(v) for v in prise.bbox)}"


def _relire_fenetres(a_revoir: list[dict[str, Any]], etat: dict[str, Any],
                     t_s: float, file: list[Any]) -> tuple[int, int]:
    """Seconde capture des fenetres encore presentes (R-S14, cas `Boo`).

    Le controle R-S14 a mis l'OCR hors de cause : `Boo !!!` est lu
    parfaitement quand il est DANS l'image. La ligne manquait donc de
    l'image elle-meme, et une seule capture ne peut pas rattraper cela.

    Rend (relectures faites, fenetres deja disparues).
    """
    import session_contenu as sc

    faites = perdues = 0
    for item in a_revoir:
        hwnd = item.get("hwnd")
        courant = etat["fenetres"].get(str(hwnd)) if hwnd else None
        if courant is None:
            perdues += 1
            continue
        prise = sc.capturer(item["exe"], item["classe"], courant["bbox"],
                            courant["ecran"], t_s)
        if prise is None:
            perdues += 1
            continue
        setattr(prise, "hwnd", hwnd)
        setattr(prise, "cle", item["cle"])
        setattr(prise, "attend_relecture", False)
        file.append(prise)
        faites += 1
    a_revoir.clear()
    return (faites, perdues)


def _fusionner_evenements(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Reunir deux lectures de la meme fenetre en un seul evenement."""
    from session_decor import fusionner_lignes

    out = dict(a)
    out["prises"] = 2
    out["lisible"] = bool(a.get("lisible")) or bool(b.get("lisible"))
    out["mots"] = max(int(a.get("mots") or 0), int(b.get("mots") or 0))
    out["ocr_ms"] = round(float(a.get("ocr_ms") or 0.0) + float(b.get("ocr_ms") or 0.0), 1)
    if "texte" in a or "texte" in b:
        lignes = fusionner_lignes(a.get("texte") or [], b.get("texte") or [])
        out["texte"] = lignes
        out["lignes"] = len(lignes)
    else:
        out["lignes"] = max(int(a.get("lignes") or 0), int(b.get("lignes") or 0))
    if out["lisible"]:
        out.pop("raison", None)
    return out


def _echantillonner_decor(rects: list[tuple[int, tuple[int, int, int, int]]],
                          t_s: float, precedents: dict[int, Any],
                          niveau: str, trace: Any,
                          file_bandes: list[Any]) -> tuple[int, int, float]:
    """Un tour de regime riche periodique. Rend (ecrans vus, changements, ms).

    Mesure R-S14 : 33 ms sur un 2560x1440, 22 ms sur un 1920x1080, soit
    ~61 ms pour les deux ecrans -- 26 fois moins que les 1584 ms annonces au
    plan pour `get_vision_snapshot(depth=rich)`, dont l'essentiel partait
    dans UIA et non dans les pixels.
    """
    import session_decor as sd

    t0 = time.perf_counter()
    vus = changes = 0
    for ecran, rect in rects:
        ech = sd.echantillonner(ecran, rect, t_s, garder_bande=(niveau == "texte"))
        if ech is None:
            continue
        vus += 1
        avant = precedents.get(ecran)
        if avant is None:
            trace.ecrire("session.decor", t_s, **ech.ancrage())
        else:
            diffs = sd.differences_decor(avant, ech)
            if diffs:
                trace.ecrire("session.decor", t_s, ecran=ecran, changements=diffs)
                changes += len(diffs)
        if ech.bande is not None:
            if len(file_bandes) >= sd.FILE_MAX:
                file_bandes.pop(0).liberer()
            file_bandes.append(ech)
        precedents[ecran] = ech
    return (vus, changes, (time.perf_counter() - t0) * 1000.0)


def executer(duree_s: float, cadence_s: float, profil: str, *,
             avec_hotes: bool = False, dossier: Path = SESSIONS_DIR,
             image_cle_s: float = INTERVALLE_IMAGE_CLE_S,
             silencieux: bool = False, contenu: str = "none",
             classes_contenu: tuple[str, ...] | None = None,
             riche_s: float | None = None,
             riche_ecrans: tuple[int, ...] | None = None,
             relecture: int = 1) -> dict[str, Any]:
    """Une session bornee. Rend le pied de session.

    Le pied est ecrit dans TOUS les cas -- duree atteinte, Ctrl-C, panne.
    Sans lui, une trace tronquee serait indiscernable d'une session qui n'a
    rien vu : la meme confusion que `unknown` evite dans le contrat photo.
    """
    session_id = time.strftime("session-%Y%m%d-%H%M%S")
    trace = TraceSession(session_id, dossier)
    t0 = time.perf_counter()
    depart = time.time()

    ticks = changements = images_cles = erreurs = 0
    captures = lectures = lectures_ko = abandonnees = 0
    relectures = relectures_perdues = 0
    riches = decor_changements = 0
    riche_cout_ms = 0.0
    file_prises: list[Any] = []
    file_bandes: list[Any] = []
    a_revoir: list[dict[str, Any]] = []
    en_attente: dict[str, tuple[dict[str, Any], int]] = {}
    precedents_decor: dict[int, Any] = {}
    derniers_textes: dict[int, tuple[str, ...]] = {}
    classes = classes_contenu
    prechauffe_ms = None
    if contenu != "none" or riche_s:
        import session_contenu as _sc

        if classes is None:
            classes = _sc.CLASSES_DECLENCHEUSES
        # La premiere capture coute 367 ms a froid contre 25 ms a chaud.
        # On paie ici, ou personne n'attend, plutot que sur la premiere
        # fenetre declenchee -- qui est justement celle qui peut ne vivre
        # qu'une seconde.
        prechauffe_ms = _sc.prechauffer()
    precedent: dict[str, Any] | None = None
    prochaine_image_cle = 0.0
    prochain_riche = 0.0
    raison = "duree_atteinte"
    couts: list[float] = []

    trace.ecrire("session.start", 0.0, profil=profil, cadence_s=cadence_s,
                 duree_prevue_s=round(duree_s, 1), t_wall_ms=int(depart * 1000),
                 hotes_en_clair=avec_hotes, contenu=contenu,
                 prechauffe_ms=prechauffe_ms, riche_cadence_s=riche_s,
                 relecture=relecture if contenu != "none" else 0)
    def traiter_prise(prise: Any, t_courant: float) -> None:
        """Lire une prise, puis l'ecrire -- ou l'attendre.

        Une premiere capture qui attend sa relecture est mise de cote au
        lieu d'etre ecrite : les deux lectures sont reunies en un seul
        evenement, pour ne pas ecrire deux fois la meme console.
        """
        nonlocal lectures, lectures_ko
        try:
            import session_contenu as _sc

            evt = _sc.lire(prise, contenu, hwnd=getattr(prise, "hwnd", None))
            cle = getattr(prise, "cle", "")
            if getattr(prise, "attend_relecture", False):
                en_attente[cle] = (evt, ticks)
                return
            precedent_evt = en_attente.pop(cle, None)
            if precedent_evt is not None:
                evt = _fusionner_evenements(precedent_evt[0], evt)
            trace.ecrire("session.contenu", t_courant, **evt)
            lectures += 1
        except Exception as exc:  # noqa: BLE001
            lectures_ko += 1
            trace.ecrire("session.erreur", t_courant,
                         erreur=f"lecture contenu : {type(exc).__name__}")
        finally:
            prise.liberer()

    if not silencieux:
        print(f"Session {session_id} - profil {profil}, un tick toutes les "
              f"{cadence_s:g} s, duree {duree_s:g} s")
        print(f"Trace : {trace.path}")
        print("Ctrl-C arrete proprement (le pied de session est ecrit).")

    try:
        while True:
            t_s = time.perf_counter() - t0
            if t_s >= duree_s:
                break
            debut_tick = time.perf_counter()
            try:
                etat = etat_ecran(avec_hotes=avec_hotes)
            except Exception as exc:  # noqa: BLE001
                # Condition d'environnement : on note et on continue. Une
                # session ne doit pas mourir parce qu'un arbre UIA a clignote.
                erreurs += 1
                trace.ecrire("session.erreur", t_s, erreur=f"{type(exc).__name__}")
                time.sleep(min(cadence_s, max(0.0, duree_s - t_s)))
                continue
            couts.append((time.perf_counter() - debut_tick) * 1000.0)
            ticks += 1

            # Les differences sont calculees a TOUS les ticks. R-S10 les
            # sautait sur un tick d'image-cle, ce qui privait le regime
            # declenche d'une fenetre sur soixante : celle qui apparaissait
            # pile a la minute n'etait jamais capturee. L'image-cle porte
            # deja l'etat complet, on n'ecrit donc pas de ligne `diff` en
            # plus -- mais on arme les captures.
            diffs = differences(precedent, etat)
            if t_s >= prochaine_image_cle:
                trace.ecrire("session.keyframe", t_s, etat=etat)
                images_cles += 1
                prochaine_image_cle = t_s + image_cle_s
            elif diffs:
                trace.ecrire("session.diff", t_s, changements=diffs)
                changements += len(diffs)
            if contenu != "none" and diffs:
                a, b = _armer_captures(diffs, etat, t_s, classes, file_prises,
                                       a_revoir if relecture else None)
                captures += a
                abandonnees += b
            precedent = etat

            # Seconde capture des fenetres armees au tick precedent : ce qui
            # n'etait pas encore affiche la premiere fois l'est peut-etre
            # maintenant (cas `Boo`, C1).
            if relecture and a_revoir:
                f, p = _relire_fenetres(a_revoir, etat, t_s, file_prises)
                relectures += f
                relectures_perdues += p

            # Regime riche periodique : le decor des ecrans, a cadence lente.
            if riche_s and t_s >= prochain_riche:
                from screen_context import list_monitors

                rects = [(m.index, (m.left, m.top, m.left + m.width, m.top + m.height))
                         for m in list_monitors()
                         if riche_ecrans is None or m.index in riche_ecrans]
                v, c, ms = _echantillonner_decor(rects, t_s, precedents_decor,
                                                 contenu, trace, file_bandes)
                riches += v
                decor_changements += c
                riche_cout_ms += ms
                prochain_riche = t_s + riche_s

            # Lecture DIFFEREE : elle se fait dans le temps mort entre deux
            # ticks, jamais dans le chemin critique de la capture. Mesure
            # R-S13 : une console de 900x500 se lit en ~380 ms, ce qui tient
            # dans le budget d'un tick a 1 Hz. Une fenetre plus grande peut
            # deborder : on la lit quand meme et on compte le tick retarde.
            while file_prises and (time.perf_counter() - debut_tick) < cadence_s:
                traiter_prise(file_prises.pop(0), t_s)

            # Les bandes du regime riche passent APRES : une console
            # ephemere est une preuve perissable, un decor ne l'est pas.
            while file_bandes and (time.perf_counter() - debut_tick) < cadence_s:
                ech = file_bandes.pop(0)
                try:
                    import session_decor as _sd

                    evt = _sd.lire_bande(ech, contenu)
                    # Une lecture identique a la precedente n'est pas un
                    # evenement : on ecrit le sous-titre QUAND IL CHANGE.
                    if evt is not None:
                        sig = _sd.signature_texte(evt.get("texte"))
                        if _sd.texte_a_change(derniers_textes.get(ech.ecran), sig):
                            derniers_textes[ech.ecran] = sig
                            trace.ecrire("session.decor", t_s, **evt)
                            decor_changements += 1
                except Exception as exc:  # noqa: BLE001
                    trace.ecrire("session.erreur", t_s,
                                 erreur=f"lecture decor : {type(exc).__name__}")
                finally:
                    ech.liberer()

            # Une lecture en attente de sa relecture ne doit pas rester en
            # rade si la fenetre a disparu : au bout de deux ticks, on ecrit
            # ce qu'on a. Une preuve incomplete vaut mieux qu'une preuve
            # jamais ecrite.
            for cle in [c for c, (_, i) in en_attente.items() if ticks - i >= 2]:
                evt, _ = en_attente.pop(cle)
                evt["prises"] = 1
                trace.ecrire("session.contenu", t_s, **evt)
                lectures += 1

            reste = cadence_s - (time.perf_counter() - debut_tick)
            restant_session = duree_s - (time.perf_counter() - t0)
            if reste > 0 and restant_session > 0:
                time.sleep(min(reste, restant_session))
    except KeyboardInterrupt:
        raison = "interruption"
    except Exception as exc:  # noqa: BLE001
        raison = f"panne:{type(exc).__name__}"
    finally:
        # Vider la file avant de conclure : une fenetre saisie puis jamais
        # lue serait une preuve perdue sans que personne le sache. On paie
        # la lecture ici, apres la duree, plutot que de la jeter.
        while file_prises:
            traiter_prise(file_prises.pop(0), time.perf_counter() - t0)
        for cle in list(en_attente):
            evt, _ = en_attente.pop(cle)
            evt["prises"] = 1
            trace.ecrire("session.contenu", time.perf_counter() - t0, **evt)
            lectures += 1
        while file_bandes:
            ech = file_bandes.pop(0)
            try:
                import session_decor as _sd

                evt = _sd.lire_bande(ech, contenu)
                sig = _sd.signature_texte(evt.get("texte")) if evt else ()
                if evt is not None and _sd.texte_a_change(
                        derniers_textes.get(ech.ecran), sig):
                    derniers_textes[ech.ecran] = sig
                    trace.ecrire("session.decor", time.perf_counter() - t0, **evt)
            except Exception:  # noqa: BLE001
                pass
            finally:
                ech.liberer()
        reelle = time.perf_counter() - t0
        couts.sort()
        # Un tick saute est un tick annonce : sans ce compte, un regime riche
        # qui mange la cadence passerait pour une session calme.
        attendus = int(reelle / cadence_s) if cadence_s > 0 else 0
        pied = {
            "session_id": session_id,
            "fichier": str(trace.path),
            "duree_prevue_s": round(duree_s, 1),
            "duree_reelle_s": round(reelle, 1),
            "ticks": ticks,
            "changements": changements,
            "images_cles": images_cles,
            "erreurs_tick": erreurs,
            "contenu": contenu,
            "captures_armees": captures,
            "lectures_faites": lectures,
            "lectures_en_echec": lectures_ko,
            "captures_abandonnees": abandonnees,
            "captures_non_lues": len(file_prises),
            "relectures_faites": relectures,
            "relectures_perdues": relectures_perdues,
            "riche_cadence_s": riche_s,
            "riches_echantillons": riches,
            "riche_cout_total_ms": round(riche_cout_ms, 1),
            "decor_changements": decor_changements,
            "ticks_attendus": attendus,
            "ticks_sautes": max(0, attendus - ticks),
            "taille_avant_pied_o": (trace.path.stat().st_size
                                    if trace.path.exists() else None),
            "cout_tick_p50_ms": round(couts[len(couts) // 2], 1) if couts else None,
            "raison_arret": raison,
            "lignes": trace.lignes + 1,
        }
        trace.ecrire("session.end", reelle, **{k: v for k, v in pied.items()
                                               if k not in ("session_id", "lignes")})
        trace.close()

    if not silencieux:
        print()
        print(f"Fin : {raison}   {ticks} ticks, {changements} changements, "
              f"{images_cles} image(s)-cle, {erreurs} erreur(s)")
        print(f"Duree reelle {reelle:.1f} s   cout p50 {pied['cout_tick_p50_ms']} ms")
        print(f"Trace : {trace.path}")
    return pied


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Session ecran bornee (R-S10) - l'oeil du labo sur le PC",
        epilog="La duree est obligatoire : il n'existe pas de session infinie.",
    )
    p.add_argument("--duree", type=parse_duree, required=True,
                   help="OBLIGATOIRE. '10m', '30m', '90s' ou un nombre de secondes.")
    p.add_argument("--profil", choices=sorted(PROFILS), default=PROFIL_DEFAUT,
                   help=f"defaut {PROFIL_DEFAUT} (1 Hz)")
    p.add_argument("--cadence", type=float, default=None,
                   help="secondes entre deux ticks ; prime sur le profil")
    p.add_argument("--contenu", choices=("none", "resume", "texte"), default="resume",
                   help="ce qu'on enregistre du contenu d'une fenetre qui apparait : "
                        "none = rien (session strictement legere) ; "
                        "resume = comptes seulement, SANS le texte (defaut) ; "
                        "texte = les lignes lues (donnees personnelles)")
    p.add_argument("--contenu-classes", default=None,
                   help="classes de fenetres a capturer, separees par des virgules "
                        "(defaut : consoles et boites de dialogue)")
    p.add_argument("--riche", nargs="?", type=float, const=None, default=False,
                   metavar="SECONDES",
                   help="regime riche PERIODIQUE : echantillonne le decor de "
                        "chaque ecran (luminance, teinte, presence de texte). "
                        "OPT-IN : sans ce drapeau, aucun ecran n'est capture "
                        "periodiquement. '--riche' seul = un echantillon par "
                        "minute ; '--riche 30' = toutes les 30 s.")
    p.add_argument("--riche-ecrans", default=None, metavar="1,2",
                   help="restreindre le regime riche a certains moniteurs "
                        "(defaut : tous)")
    p.add_argument("--relecture", type=int, default=1, choices=(0, 1),
                   help="seconde capture d'une fenetre declenchee au tick "
                        "suivant (defaut 1). Rattrape le texte qui n'etait "
                        "pas encore affiche a la premiere saisie.")
    p.add_argument("--avec-hotes", action="store_true",
                   help="enregistrer les hotes en clair (defaut : empreinte de 8 caracteres)")
    p.add_argument("--retention-jours", type=int, default=RETENTION_JOURS_DEFAUT)
    p.add_argument("--taille-max-mo", type=int, default=TAILLE_MAX_MO_DEFAUT)
    p.add_argument("--json", action="store_true", help="pied de session en JSON")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cadence = args.cadence if args.cadence else PROFILS[args.profil]["cadence_s"]
    if cadence <= 0:
        print("cadence invalide")
        return 2

    supprimes = purger(SESSIONS_DIR, args.retention_jours, args.taille_max_mo)
    if supprimes:
        print(f"Retention : {supprimes} session(s) ancienne(s) supprimee(s)")

    classes = None
    if args.contenu_classes:
        classes = tuple(c.strip() for c in args.contenu_classes.split(",") if c.strip())

    # `--riche` absent -> False (regime eteint) ; `--riche` seul -> None,
    # c'est-a-dire la cadence par defaut ; `--riche 30` -> 30 s.
    if args.riche is False:
        riche_s = None
    else:
        from session_decor import CADENCE_DEFAUT_S

        riche_s = float(args.riche) if args.riche else CADENCE_DEFAUT_S
        if riche_s <= 0:
            print("cadence riche invalide")
            return 2
    ecrans = None
    if args.riche_ecrans:
        ecrans = tuple(int(x) for x in args.riche_ecrans.split(",") if x.strip())

    pied = executer(args.duree, cadence, args.profil, avec_hotes=args.avec_hotes,
                    contenu=args.contenu, classes_contenu=classes,
                    riche_s=riche_s, riche_ecrans=ecrans, relecture=args.relecture)
    if args.json:
        print()
        print(json.dumps(pied, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
