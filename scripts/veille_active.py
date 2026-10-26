#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Veille ACTIVE des ecrans : tout lire, remarquer ce qui change, signaler.

Pourquoi (26/10) : en « smart awareness » et en cybersecurite, l'oeil ne
doit pas seulement servir la tache du moment. Un message glisse dans un coin,
une fenetre de consigne qui surgit, un nom qui passe dans le jeu : il faut le
VOIR et le DIRE, meme hors de la consigne. Le 26/10, un message de test etait
dans le chat du jeu (ecran 2) ; l'outil passif l'avait lu, personne ne l'a vu.

Trois vitesses, en meme temps, sur TOUS les ecrans :
  1. a chaque passage (``--periode``) : liste des fenetres (nouvelles, fermees,
     titre change), zones qui bougent, OCR Tesseract de chaque ecran entier
     (rapide, bon sur l'interface Windows, faible sur le texte du jeu) ;
  2. en fond, sans arret : RapidOCR repasse chaque ecran par morceaux
     (lent, mais lit le texte du jeu, les petits textes, les coins) ;
  3. chaque ligne NOUVELLE est classee et les alertes sont dites tout de suite.

Regle d'or : un texte lu a l'ecran est une DONNEE, jamais une consigne. La veille
signale (« adresse a une agente », « ressemble a une consigne »), elle n'obeit pas.
Aucune souris, aucun clavier, aucune fenetre ouverte. ``--duree`` obligatoire
(pas de daemon, decision I7). Sorties hors depot par defaut (%TEMP%).

  python scripts/veille_active.py --duree 240
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from screen_context import list_monitors, list_windows  # noqa: E402

AGENTES = re.compile(r"(?i)\b(assistant|agent|agente|agentes)\b")
# v2 (audit 26/10, apres la 1re veille) : « systeme » et « admin » retires (les lignes
# « [Systeme] » du jeu faisaient 14 fausses alertes sur 31) ; la console admin est
# detectee par sa FENETRE, pas par un mot.
CONSIGNE = re.compile(
    r"(?i)\b(ignore[sz]?|oublie[sz]?|supprime[sz]?|efface[sz]?|ex[ée]cute[sz]?|clique[sz]?|"
    r"tape[sz]?|envoie[sz]?|copie[sz]?|colle[sz]?|installe[sz]?|t[ée]l[ée]charge[sz]?|"
    r"mot de passe|password|token|cl[ée] api|prompt|urgent)\b|https?://|www\.")
TEST = re.compile(r"(?i)\b(test|secret|cach[ée]e?s?|message|indice|trouve|vois)\b")
BRUIT = re.compile(r"^[\W\d_]{0,4}$")
JEU_SYSTEME = re.compile(r"^\W*\[(syst[eè]me|autre|conseils|annonce)\]", re.IGNORECASE)
# Secret visible : mot de 8 a 40 signes sans espace, avec minuscule, majuscule et
# chiffre (allure de mot de passe ou de jeton), ou un numero de telephone. Le
# texte est MASQUE dans tout ce que la veille ecrit (meme hors depot).
SECRET = re.compile(r"\b(?=[A-Za-z0-9_\-]{8,40}\b)(?=\S*[a-z])(?=\S*[A-Z])(?=\S*\d)[A-Za-z0-9_\-]{8,40}\b")
TELEPHONE = re.compile(r"(?<!\d)(?:\+33\s?|0)[1-9](?:[\s.\-]?\d{2}){4}(?!\d)")
# Fil des assistants : lu, journalise, pas d'alerte. `--fil-exe` remplace cette liste.
FIL_DEFAUT: tuple[str, ...] = ()
# Jeux : la bande du chat (bas de fenetre) est lue en priorite. `--jeu-exe` remplace
# cette liste (morceau du nom de programme, en minuscules).
JEUX_DEFAUT: tuple[str, ...] = ()
CONSOLES = {"powershell.exe", "pwsh.exe", "cmd.exe", "windowsterminal.exe", "wt.exe", "conhost.exe"}
# Titres / chrome d'apps d'agentes : sonnent « adresse » mais ne sont pas un message (audit 26/10, revue 26/10).
def norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def masquer(texte: str) -> str:
    return TELEPHONE.sub("<telephone-masque>", SECRET.sub("<secret-masque>", texte))


def est_bruit_ui_agente(texte: str) -> bool:
    """Titre d'app ou barre d'envoi : journalise, n'alerte pas (sauf secret / vrai message de test)."""
    n = norm(texte)
    if n in {"assistant", "agent", "agente", "vision public"} or n.endswith(" assistant"):
        return True
    if n.startswith("envoyer un message a ") and "vision public" in n:
        return True
    return False


def classer(texte: str) -> list[str]:
    c = []
    if SECRET.search(texte) or TELEPHONE.search(texte):
        c.append("secret_visible")
    if JEU_SYSTEME.match(texte):
        return c + ["jeu_systeme"]
    if AGENTES.search(texte):
        c.append("adresse_agente")
    if CONSIGNE.search(texte):
        c.append("ressemble_consigne")
    mots_test = len(TEST.findall(texte))
    if mots_test:
        c.append("mot_test")
    if mots_test >= 2:
        c.append("message_test")
    if "?" in texte:
        c.append("question")
    return c


def meme_alerte(a: str, b: str) -> bool:
    """Meme phrase malgre l'ordre des mots (Tesseract melange), pas deux secrets courts."""
    from difflib import SequenceMatcher

    if SequenceMatcher(None, a, b).ratio() >= 0.7:
        return True
    ta = [m for m in a.split() if len(m) > 1]
    tb = [m for m in b.split() if len(m) > 1]
    if len(ta) < 4 or len(tb) < 4:
        return False
    sa, sb = set(ta), set(tb)
    union = len(sa | sb)
    return union > 0 and len(sa & sb) / union >= 0.72


def alerte(classes: list[str], texte: str = "") -> bool:
    s = set(classes)
    if "jeu_systeme" in s and "secret_visible" not in s:
        return False
    # Chrome d'apps : pas d'alerte sauf secret / consigne / vrai « message de test » (2 mots-test)
    if texte and est_bruit_ui_agente(texte) and not (s & {"secret_visible", "ressemble_consigne", "message_test"}):
        return False
    return bool(s & {"adresse_agente", "ressemble_consigne", "message_test", "secret_visible"}) or (
        {"question", "mot_test"} <= s)


class Ecrans:
    def __init__(self) -> None:
        from liste_ecrans_mss import ouvrir_mss

        self.mons = list_monitors()
        self._ouvrir = ouvrir_mss
        self._local = threading.local()

    def grab(self, m: Any) -> np.ndarray:
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._local.sct = self._ouvrir()
        brut = np.asarray(sct.grab({"left": m.left, "top": m.top, "width": m.width, "height": m.height}))
        return np.ascontiguousarray(brut[:, :, :3])


class Registre:
    """Lignes deja vues + journal. Partage entre la boucle rapide et le fond."""

    def __init__(self, sortie: Path, fil: set[str]) -> None:
        self.vues: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.journal = (sortie / "veille.jsonl").open("a", encoding="utf-8")
        self.alertes: list[dict[str, Any]] = []
        self._cles: list[str] = []  # texte brut normalise de chaque alerte, jamais ecrit
        self.fil = fil  # exe dont le texte est notre propre fil de discussion (pas d'alerte)
        self.debut = time.time()

    def ecrire(self, ev: dict[str, Any]) -> None:
        with self.lock:
            self.journal.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.journal.flush()

    def ligne(self, texte: str, ecran: int, x: int, y: int, source: dict[str, Any], moteur: str) -> None:
        brut = texte.strip()
        n = norm(brut)
        if len(n) < 3 or BRUIT.match(brut):
            return
        classes = classer(brut)
        t = masquer(brut)  # un secret lu ne sort jamais en clair, meme dans %TEMP%
        with self.lock:
            if n in self.vues:
                self.vues[n]["revue"] = time.strftime("%H:%M:%S")
                return
            ev = {"heure": time.strftime("%H:%M:%S"), "t_s": round(time.time() - self.debut, 1), "texte": t,
                  "ecran": ecran, "x": x, "y": y, "fenetre": source.get("exe"), "titre": masquer(source.get("titre") or ""),
                  "moteur": moteur, "classes": classes}
            self.vues[n] = ev
        self.ecrire({"type": "ligne", **ev})
        if source.get("exe") in self.fil or not alerte(classes, brut):
            return
        # Comparer le texte BRUT (en memoire seulement) : deux secrets differents,
        # une fois masques, se ressemblent et l'un effacait l'autre (constat v2).
        # L'ordre des mots ne compte pas : Tesseract le melange (constat passe 2).
        with self.lock:
            for i, a in enumerate(self.alertes):
                cle = self._cles[i] if i < len(self._cles) else norm(a.get("texte", ""))
                if meme_alerte(cle, n):
                    a["relue"] = a.get("relue", 0) + 1
                    return
            self.alertes.append(ev)
            self._cles.append(n)
        print(f"ALERTE {ev['heure']} ecran {ecran} ({x},{y}) [{source.get('exe')}] "
              f"{','.join(classes)} : {t[:120]!r}", flush=True)

    def signal(self, genre: str, texte: str, **info: Any) -> None:
        """Alerte qui ne vient pas d'une ligne lue (fenetre, onglet, ecran non dessine)."""
        ev = {"heure": time.strftime("%H:%M:%S"), "t_s": round(time.time() - self.debut, 1), "genre": genre,
              "texte": masquer(texte), **info}
        with self.lock:
            self.alertes.append(ev)
            self._cles.append(f"signal {genre} {norm(texte)}")
        self.ecrire({"type": "signal", **ev})
        print(f"SIGNAL {ev['heure']} {genre} : {ev['texte'][:120]!r} {info}", flush=True)


def source_du_point(fen: list[Any], x: int, y: int) -> dict[str, Any]:
    """Fenetre la plus haute (z) qui contient le point, hors calques plein ecran."""
    cands = [w for w in fen if not w.is_overlay and w.rect_visible[0] <= x < w.rect_visible[2]
             and w.rect_visible[1] <= y < w.rect_visible[3]]
    if not cands:
        return {"exe": "bureau", "titre": ""}
    w = min(cands, key=lambda w: w.z_order if w.z_order >= 0 else 9999)
    return {"exe": w.exe_name, "titre": w.title}


def couper_bloc(bloc: list, fen: list[Any] | None, ox: int, oy: int) -> list[list]:
    """Ne colle pas deux mots de fenetres differentes (affiche + bloc-notes, meme hauteur)."""
    if not bloc:
        return []
    if not fen:
        return [bloc]

    def cle(b: tuple) -> tuple[str, str]:
        src = source_du_point(fen, ox + int(b[0] + b[2] // 2), oy + int(b[1] + max(1, b[3]) // 2))
        return (src.get("exe") or "", src.get("titre") or "")

    parts: list[list] = []
    cur = [bloc[0]]
    cle0 = cle(bloc[0])
    for b in bloc[1:]:
        c = cle(b)
        if c != cle0:
            parts.append(cur)
            cur = [b]
            cle0 = c
        else:
            cur.append(b)
    parts.append(cur)
    return parts


def ocr_tesseract(img: np.ndarray, fen: list[Any] | None = None, ox: int = 0, oy: int = 0) -> list[tuple[str, int, int]]:
    from ocr_screen import ocr_boxes

    boites = ocr_boxes(img, "fra+eng", 45)
    boites.sort(key=lambda b: (b[1] // 14, b[0]))
    lignes, cur, y0 = [], [], None
    for b in boites:
        if y0 is None or abs(b[1] - y0) <= 10:
            cur.append(b)
            y0 = b[1] if y0 is None else y0
        else:
            lignes.append(cur)
            cur, y0 = [b], b[1]
    if cur:
        lignes.append(cur)
    out = []
    for l in lignes:
        # coupe une « ligne » si deux mots sont tres loin, puis si la fenetre change
        blocs = []
        bloc = [l[0]]
        for b in l[1:]:
            if b[0] - (bloc[-1][0] + bloc[-1][2]) > 60:
                blocs.append(bloc)
                bloc = [b]
            else:
                bloc.append(b)
        blocs.append(bloc)
        for bloc in blocs:
            for part in couper_bloc(bloc, fen, ox, oy):
                out.append((" ".join(x[4] for x in part), part[0][0], part[0][1]))
    return out


# Tuile reduite : la detection RapidOCR sur 680 px coute plusieurs secondes sous le jeu.
ECHELLE_TUILE = 0.55


def point_tuile(x: int, y: int, bx: float, by: float, echelle: float = ECHELLE_TUILE) -> tuple[int, int]:
    return int(round(x + bx / echelle)), int(round(y + by / echelle))


def plan_tuiles(
    img: np.ndarray,
    zones: list[tuple[int, int, int, int]] | None = None,
    pas_x: int = 640,
    pas_y: int = 480,
) -> list[tuple[int, int]]:
    """Zones demandees d'abord, puis le reste. Une tuile presque unie est sautee."""
    h, w = img.shape[:2]
    toutes = [(x, y) for y in range(0, h, pas_y) for x in range(0, w, pas_x)]

    def touche(x: int, y: int) -> bool:
        x1, y1 = min(w, x + pas_x + 40), min(h, y + pas_y + 40)
        for zx0, zy0, zx1, zy1 in zones or []:
            if x < zx1 and x1 > zx0 and y < zy1 and y1 > zy0:
                return True
        return False

    def plate(x: int, y: int) -> bool:
        tu = img[y:min(h, y + pas_y), x:min(w, x + pas_x)]
        return tu.size == 0 or float(tu.std()) < 6.0

    prio = [t for t in toutes if touche(*t)]
    reste = [t for t in toutes if t not in prio and not plate(*t)]
    return prio + reste


def morceaux_zone(zone: tuple[int, int, int, int], w: int, h: int,
                  mx: int = 680, my: int = 520) -> list[tuple[int, int, int, int]]:
    """Decoupe une zone en morceaux <= mx x my, dans l'image. Au-dela de 960 px,
    RapidOCR (det_limit max) reduit lui-meme l'image : le petit texte disparait."""
    zx0, zy0, zx1, zy1 = max(0, zone[0]), max(0, zone[1]), min(w, zone[2]), min(h, zone[3])
    out = []
    for y in range(zy0, zy1, my - 40):
        for x in range(zx0, zx1, mx - 40):
            x1, y1 = min(zx1, x + mx), min(zy1, y + my)
            if x1 - x >= 16 and y1 - y >= 12:
                out.append((x, y, x1, y1))
    return out


def ocr_rapid_tuiles(
    img: np.ndarray,
    moteur: Any,
    arret: threading.Event,
    zones: list[tuple[int, int, int, int]] | None = None,
) -> list[tuple[str, int, int]]:
    """1) zones prioritaires a PLEINE taille ; 2) le reste, reduit (rapide).

    audit 26/10 (passe 2) : avec toutes les tuiles reduites a 55 %, le chat du jeu
    devenait illisible (mesure sur la meme capture : lu a 100 %, rate a 75 % et
    55 %). Le 5e message de test de l'utilisateur n'a jamais ete lu en 120 s de veille.
    """
    out = []
    h, w = img.shape[:2]
    for zone in zones or []:
        for x0, y0, x1, y1 in morceaux_zone(zone, w, h):
            if arret.is_set():
                return out
            res, _ = moteur(img[y0:y1, x0:x1])
            for r in res or []:
                (bx, by) = r[0][0]
                out.append((str(r[1]), int(x0 + bx), int(y0 + by)))
    for x, y in plan_tuiles(img, zones):
        if arret.is_set():
            return out
        tuile = img[y:y + 520, x:x + 680]
        vue = cv2.resize(tuile, None, fx=ECHELLE_TUILE, fy=ECHELLE_TUILE, interpolation=cv2.INTER_AREA)
        res, _ = moteur(vue)
        for r in res or []:
            (bx, by) = r[0][0]
            px, py = point_tuile(x, y, bx, by)
            out.append((str(r[1]), px, py))
    return out


# Consoles seulement : elles coupent EN PLEIN MOT. Un bloc-notes coupe entre les
# mots, et son bloc entier (menus compris) donnait 3 alertes en double (rejeu 26/10).
BLOC_EXES = set(CONSOLES)


def blocs_par_fenetre(lignes: list[tuple[str, int, int, dict[str, Any]]]) -> list[tuple[str, int, int, dict[str, Any]]]:
    """Texte entier de chaque console / bloc-notes, dans l'ordre de lecture.

    audit 26/10 (passe 2) : une console etroite coupe la phrase sur 3 lignes
    (« 4eme messa » / « ge de test bouges la souris » / « mets la sur l'icone… ») :
    aucune ligne seule n'a 2 mots-tests, le 4e message n'alertait pas."""
    par: dict[tuple[str, str], list[tuple[int, int, str]]] = {}
    src: dict[tuple[str, str], dict[str, Any]] = {}
    for texte, x, y, source in lignes:
        exe = (source.get("exe") or "").lower()
        if exe not in BLOC_EXES:
            continue
        cle = (exe, source.get("titre") or "")
        par.setdefault(cle, []).append((y, x, texte))
        src[cle] = source
    out = []
    for cle, morceaux in par.items():
        if len(morceaux) < 2:
            continue
        morceaux.sort(key=lambda m: (m[0] // 10, m[1]))
        x, y = morceaux[0][1], morceaux[0][0]
        textes = [t for _, _, t in morceaux]
        # Deux lectures : la console coupe EN PLEIN MOT (« messa » + « ge »), sans
        # tiret. Avec espace a chaque coupure, puis sans : l'une des deux recolle.
        out.append((" ".join(textes), x, y, src[cle]))
        out.append(("".join(textes), x, y, src[cle]))
    return out


def est_console_admin(w: Any) -> bool:
    return (w.exe_name or "").lower() in CONSOLES or bool(re.search(r"(?i)administrat", w.title or ""))


def onglets(fen: list[Any]) -> list[tuple[Any, str]]:
    """Noms des onglets de chaque fenetre (arbre d'accessibilite, lecture seule).

    v2 (audit 26/10) : un onglet cache derriere un autre ne se voit pas a l'image,
    mais son nom se lit. Le 26/10, un Bloc-notes avait 6 onglets, dont deux
    sensibles (allure de mot de passe, numero de telephone)."""
    try:
        import screen_uia as su

        iuia, _ = su._uia()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for w in fen:
        if w.is_overlay:
            continue
        try:
            els = su.element_from_hwnd(w.hwnd).FindAll(4, iuia.CreatePropertyCondition(30003, 50019))
            for i in range(els.Length):
                nom = su._name(els.GetElement(i)) or ""
                if nom.strip():
                    out.append((w, nom.strip()))
        except Exception:  # noqa: BLE001
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Veille active des ecrans (lecture seule)")
    ap.add_argument("--duree", type=float, required=True, help="secondes (pas de daemon)")
    ap.add_argument("--periode", type=float, default=5.0)
    ap.add_argument("--sortie", type=Path, default=Path(tempfile.gettempdir()) / "vision-veille-active")
    ap.add_argument("--sans-rapid", action="store_true")
    ap.add_argument("--images", action="store_true", help="garde une capture par passage (hors depot)")
    ap.add_argument("--fil-exe", action="append", default=None, metavar="PROGRAMME",
                    help="programme dont le texte est lu sans alerte (fil d'assistant) ; repetable")
    ap.add_argument("--jeu-exe", action="append", default=None, metavar="MORCEAU",
                    help="morceau du nom d'un programme de jeu (bande du chat lue en priorite) ; repetable")
    args = ap.parse_args()
    if str(ROOT).lower() in str(args.sortie.resolve()).lower():
        print("REFUSE : sortie dans le depot. Les ecrans de l'utilisateur ne vont pas dans git.")
        return 2
    args.sortie.mkdir(parents=True, exist_ok=True)
    ecrans = Ecrans()
    # Fil des assistants (FIL_DEFAUT ou --fil-exe) : lu, journalise, pas d'alerte.
    # Un vrai message de test est dans le Bloc-notes, une console ou le jeu.
    fil = set(args.fil_exe) if args.fil_exe else set(FIL_DEFAUT)
    jeux = tuple(j.lower() for j in (args.jeu_exe or JEUX_DEFAUT) if j)
    reg = Registre(args.sortie, fil)
    arret = threading.Event()
    etat = {"fen": list_windows(min_side=20), "images": {}, "non_dessinees": set()}
    # v2 : l'etat de depart compte. En v1, une console admin deja ouverte avant
    # la veille n'etait jamais signalee (seules les fenetres NOUVELLES l'etaient).
    for w in etat["fen"]:
        if w.title:
            reg.ligne(w.title, w.monitor_index or 0, w.rect_visible[0], w.rect_visible[1],
                      {"exe": w.exe_name, "titre": w.title}, "titre_initial")
        if est_console_admin(w):
            reg.signal("console_ouverte", w.title, exe=w.exe_name, ecran=w.monitor_index, rect=list(w.rect_visible))

    def fond() -> None:
        try:
            from ocr_hud import _moteur_rapid

            moteur = _moteur_rapid()
        except Exception as exc:  # noqa: BLE001
            reg.ecrire({"type": "fond", "erreur": type(exc).__name__})
            return
        tour = 0
        while not arret.is_set():
            tour += 1
            for m in ecrans.mons:
                if arret.is_set():
                    break
                t0 = time.perf_counter()
                img = ecrans.grab(m)
                fen = etat["fen"]
                h, w = img.shape[:2]
                zones = [(0, 0, 420, 260), (max(0, w - 420), 0, w, 260),
                         (0, max(0, h - 260), 420, h), (max(0, w - 420), max(0, h - 260), w, h),
                         (0, max(0, h - 90), w, h)]
                for win in fen:
                    if win.is_overlay or win.monitor_index != m.index:
                        continue
                    exe = (win.exe_name or "").lower()
                    if exe not in {"notepad.exe", "powershell.exe", "windowsterminal.exe"} and not any(j in exe for j in jeux):
                        continue
                    rx0, ry0 = win.rect_visible[0] - m.left, win.rect_visible[1] - m.top
                    rx1, ry1 = win.rect_visible[2] - m.left, win.rect_visible[3] - m.top
                    # Petite fenetre en entier. Jeu : bande du chat (bas) en priorite.
                    if any(j in exe for j in jeux):
                        zones.append((rx0, ry1 - int((ry1 - ry0) * 0.28), rx1, ry1))
                    else:
                        zones.append((rx0, ry0, rx1, ry1))
                n0 = len(reg.vues)
                for texte, x, y in ocr_rapid_tuiles(img, moteur, arret, zones):
                    reg.ligne(texte, m.index, m.left + x, m.top + y, source_du_point(fen, m.left + x, m.top + y), "rapidocr")
                reg.ecrire({"type": "balayage_fin", "ecran": m.index, "tour": tour,
                            "s": round(time.perf_counter() - t0, 1), "nouvelles": len(reg.vues) - n0})
                print(f"[fond] balayage fin ecran {m.index} tour {tour} : {round(time.perf_counter() - t0, 1)} s, "
                      f"{len(reg.vues) - n0} lignes nouvelles", flush=True)

    th = None
    if not args.sans_rapid:
        th = threading.Thread(target=fond, daemon=True)
        th.start()
    fin = time.time() + args.duree
    passage = 0
    prec: dict[int, np.ndarray] = {}
    print(f"veille active {args.duree:.0f} s, {len(ecrans.mons)} ecrans, sortie hors depot", flush=True)
    while time.time() < fin:
        passage += 1
        t0 = time.perf_counter()
        fen = list_windows(min_side=20)
        avant = {w.hwnd: w for w in etat["fen"]}
        apres = {w.hwnd: w for w in fen}
        for h in apres.keys() - avant.keys():
            w = apres[h]
            ev = {"type": "fenetre_nouvelle", "heure": time.strftime("%H:%M:%S"), "exe": w.exe_name,
                  "titre": w.title, "rect": list(w.rect_visible), "ecran": w.monitor_index}
            reg.ecrire(ev | {"titre": masquer(w.title or "")})
            reg.signal("fenetre_nouvelle", w.title or "", exe=w.exe_name, ecran=w.monitor_index, rect=list(w.rect_visible))
            if est_console_admin(w):
                reg.signal("console_ouverte", w.title or "", exe=w.exe_name, ecran=w.monitor_index)
            if w.title:
                reg.ligne(w.title, w.monitor_index, w.rect_visible[0], w.rect_visible[1], {"exe": w.exe_name, "titre": w.title}, "titre")
        for h in avant.keys() - apres.keys():
            reg.ecrire({"type": "fenetre_fermee", "heure": time.strftime("%H:%M:%S"), "exe": avant[h].exe_name})
        for h in avant.keys() & apres.keys():
            if avant[h].title != apres[h].title:
                reg.ecrire({"type": "titre_change", "heure": time.strftime("%H:%M:%S"), "exe": apres[h].exe_name,
                            "titre": apres[h].title})
                reg.ligne(apres[h].title, apres[h].monitor_index, apres[h].rect_visible[0], apres[h].rect_visible[1],
                          {"exe": apres[h].exe_name, "titre": apres[h].title}, "titre")
        etat["fen"] = fen
        if passage % 5 == 1:
            for w, nom in onglets(fen):
                reg.ligne(nom, w.monitor_index or 0, w.rect_visible[0], w.rect_visible[1],
                          {"exe": w.exe_name, "titre": w.title}, "uia_onglet")
        for m in ecrans.mons:
            img = ecrans.grab(m)
            if args.images:
                cv2.imwrite(str(args.sortie / f"passage-{passage:03d}-ecran-{m.index}.png"), img)
            # v2 : une fenetre visible mais toute unie n'est PAS « rien a lire » :
            # le jeu ne se dessine plus quand il n'a pas le focus (26/10, ecran 2).
            for w in fen:
                if w.is_overlay or w.monitor_index != m.index:
                    continue
                x0, y0, x1, y1 = (w.rect_visible[0] - m.left, w.rect_visible[1] - m.top,
                                  w.rect_visible[2] - m.left, w.rect_visible[3] - m.top)
                x0, y0, x1, y1 = max(0, x0), max(0, y0 + 32), min(m.width, x1), min(m.height, y1)
                if x1 - x0 < 200 or y1 - y0 < 200:
                    continue
                unie = float(img[y0:y1, x0:x1].std()) < 2.0
                if unie and w.hwnd not in etat["non_dessinees"]:
                    etat["non_dessinees"].add(w.hwnd)
                    reg.signal("fenetre_non_dessinee", w.title or "", exe=w.exe_name, ecran=m.index)
                elif not unie:
                    etat["non_dessinees"].discard(w.hwnd)
            gris = cv2.cvtColor(cv2.resize(img, (m.width // 8, m.height // 8), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            if m.index in prec:
                diff = cv2.absdiff(gris, prec[m.index])
                part = float((diff > 18).mean())
                if part > 0.002:
                    ys, xs = np.nonzero(diff > 18)
                    reg.ecrire({"type": "mouvement", "heure": time.strftime("%H:%M:%S"), "ecran": m.index,
                                "part": round(part, 4), "boite": [int(m.left + xs.min() * 8), int(m.top + ys.min() * 8),
                                                                  int(m.left + xs.max() * 8), int(m.top + ys.max() * 8)]})
            prec[m.index] = gris
            lues = []
            for texte, x, y in ocr_tesseract(img, fen, m.left, m.top):
                source = source_du_point(fen, m.left + x, m.top + y)
                reg.ligne(texte, m.index, m.left + x, m.top + y, source, "tesseract")
                lues.append((texte, m.left + x, m.top + y, source))
            for texte, x, y, source in blocs_par_fenetre(lues):
                reg.ligne(texte, m.index, x, y, source, "bloc_fenetre")
        reg.ecrire({"type": "passage", "n": passage, "s": round(time.perf_counter() - t0, 2), "lignes_connues": len(reg.vues)})
        reste = args.periode - (time.perf_counter() - t0)
        if reste > 0:
            time.sleep(min(reste, max(0.0, fin - time.time())))
    arret.set()
    if th:
        th.join(timeout=60)
    bilan = {"duree_s": args.duree, "passages": passage, "lignes_vues": len(reg.vues),
             "alertes": reg.alertes, "fil_exclu_des_alertes": sorted(fil)}
    (args.sortie / "bilan.json").write_text(json.dumps(bilan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"fin : {passage} passages, {len(reg.vues)} lignes distinctes, {len(reg.alertes)} alertes", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
