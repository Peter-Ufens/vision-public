"""Smoke V1.1 : lire une case / une plage (screen_cells + lire_cases).

Usage :
  python scripts/smoke_lire_cases.py

Ne demande pas Ollama : la description est testee contre un faux serveur
local (thread), qui enregistre chaque requete. Une seule lecture reelle de
l'ecran (case 1:A1), dont rien n'est affiche a part des nombres.

Exit 0 si tous les controles passent.
"""

from __future__ import annotations

import ast
import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import screen_cells  # noqa: E402
from screen_cells import (  # noqa: E402
    decrire_ollama,
    formes_sommaires,
    lire,
    luminance,
    nommer_couleurs,
    parse_cible,
)

CHECKS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((label, bool(ok), detail))
    print(f"  [{'OK ' if ok else 'KO '}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(ok)


# Le poste de l'utilisateur (mesure 15/10) : ecran 1 a gauche, ecran 2 decale de 182 px.
MONS = [
    SimpleNamespace(index=1, left=0, top=0, width=2560, height=1440),
    SimpleNamespace(index=2, left=2560, top=182, width=1920, height=1080),
]


def refuse(texte: str, *mots: str, moniteurs=MONS) -> bool:
    try:
        parse_cible(texte, moniteurs)
    except ValueError as exc:
        msg = str(exc)
        manquants = [m for m in mots if m not in msg]
        return check(f"refus {texte!r}", not manquants,
                     msg[:110] if not manquants else f"manque {manquants} dans : {msg[:90]}")
    return check(f"refus {texte!r}", False, "accepte alors qu'il devait etre refuse")


# --------------------------------------------------------------------------- #

def smoke_cibles() -> None:
    print()
    print("Cibles : case, sous-case, plage")
    c = parse_cible("1:A1", MONS)
    check("1:A1 = premiere case de l'ecran 1", c.bbox == (0, 0, 160, 160), str(c.bbox))
    c = parse_cible("2:A1", MONS)
    check("2:A1 tient compte du decalage de l'ecran 2", c.bbox == (2560, 182, 2680, 302), str(c.bbox))
    c = parse_cible("1:F8-1:K9", MONS)
    check("plage 1:F8-1:K9 = bande du bas, 12 cases",
          c.bbox == (800, 1120, 1760, 1440) and c.nb_cases == 12, f"{c.bbox} {c.nb_cases}")
    check("plage dans l'autre sens remise a l'endroit",
          parse_cible("1:K9-1:F8", MONS).label == "1:F8-1:K9")
    check("forme courte 1:F8-K9 acceptee", parse_cible("1:F8-K9", MONS).label == "1:F8-1:K9")
    check("minuscules acceptees", parse_cible("1:f8-k9", MONS).label == "1:F8-1:K9")
    s = parse_cible("1:B7.5", MONS)
    b7 = parse_cible("1:B7", MONS).bbox
    check("sous-case 1:B7.5 au centre de B7",
          s.label == "1:B7.5" and b7[0] < s.bbox[0] and s.bbox[2] < b7[2]
          and b7[1] < s.bbox[1] and s.bbox[3] < b7[3], f"{s.bbox} dans {b7}")
    labels = [lab for lab, _ in parse_cible("1:A1-1:B2", MONS).cases()]
    check("cases() ligne par ligne", labels == ["1:A1", "1:B1", "1:A2", "1:B2"], str(labels))

    print()
    print("Refus clairs (une case inventee doit faire du bruit)")
    refuse("1:L16", "ligne 16", "lignes 1 a 9", "1:F8-1:K9")
    refuse("1:Q5", "colonne Q", "derniere colonne : P")
    refuse("B7", "precise l'ecran", "1:B7")
    refuse("3:A1", "ecran 3 inconnu")
    refuse("1:A1-2:B2", "un seul ecran")
    refuse("1:B7.5-1:C8", "sous-case")
    refuse("hello", "illisible")
    refuse("", "vide")
    refuse("1:A1-1:B2-1:C3", "plage invalide")

    print()
    print("Ecran dont la case ne tombe pas juste (3440x1440, 22 colonnes)")
    large = [SimpleNamespace(index=1, left=0, top=0, width=3440, height=1440)]
    v9 = parse_cible("1:V9", large)
    check("derniere case 1:V9 colle au bord droit et au bas",
          v9.bbox[2] == 3440 and v9.bbox[3] == 1440, str(v9.bbox))
    refuse("1:W1", "colonne W", moniteurs=large)

    print()
    print("Pieges debranchement (18/10 · audit V1.2 · simulation, 0 cable touche)")
    seul = [SimpleNamespace(index=1, left=0, top=0, width=2560, height=1440)]
    refuse("2:A1", "ecran 2 inconnu", moniteurs=seul)
    # Ex-ecran 2 devient primaire index 1 : 1:P9 pointe un autre rectangle physique
    avant = parse_cible("1:P9", MONS).bbox
    reindex = [SimpleNamespace(index=1, left=2560, top=182, width=1920, height=1080)]
    apres = parse_cible("1:P9", reindex).bbox
    # PIEGE CONNU, pas une protection (retour audit V1.3 § 4) : ce controle est
    # vert TANT QUE le piege existe. Le jour ou les cases portent l'identite
    # physique de l'ecran, il passera rouge : le remplacer alors par un refus.
    check("PIEGE CONNU (decrit, pas protege) : ecran 1 debranche, 1:P9 vise l'autre ecran physique",
          avant != apres, f"avant={avant} apres={apres}")


def smoke_mesures() -> None:
    print()
    print("Mesures sur les pixels")
    rouge = np.zeros((90, 160, 3), np.uint8)
    rouge[:] = (0, 0, 220)
    col = nommer_couleurs(rouge)
    check("rouge pur = rouge", col and col[0]["nom"] == "rouge" and col[0]["pct"] == 100.0, str(col))
    noir = np.zeros((90, 160, 3), np.uint8)
    check("noir pur = noir", nommer_couleurs(noir)[0]["nom"] == "noir")
    check("noir pur = zone noire", luminance(noir)[2] is True)
    moitie = np.zeros((90, 160, 3), np.uint8)
    moitie[:, :80] = (255, 255, 255)
    moitie[:, 80:] = (200, 60, 0)
    noms = {c["nom"]: c["pct"] for c in nommer_couleurs(moitie)}
    check("moitie blanc, moitie bleu",
          abs(noms.get("blanc", 0) - 50) < 2 and abs(noms.get("bleu", 0) - 50) < 2, str(noms))
    marron = np.zeros((90, 160, 3), np.uint8)
    marron[:] = (20, 60, 110)
    check("orange sombre = marron", nommer_couleurs(marron)[0]["nom"] == "marron",
          str(nommer_couleurs(marron)))
    jaune = np.zeros((40, 40, 3), np.uint8)
    jaune[:] = (0, 220, 220)
    from screen_cells import nom_moyen  # deplace le 26/10 (vitrine)
    check("jaune pur : le pixel moyen dit jaune", nom_moyen(jaune.mean(axis=(0, 1))) == "jaune")
    # Moyennes reelles passe 1b (ecran 1). Image UNIE : pas de decoupe, pas de DPI.
    # L'ancien nom par canal disait vert et rouge. L'oeil disait marron et jaune.
    mesure_c4 = np.array([54.4, 102.0, 100.9], np.float64)
    img_c4 = np.zeros((40, 40, 3), np.uint8)
    img_c4[:] = np.clip(np.round(mesure_c4), 0, 255).astype(np.uint8)
    check("Mesure C4 : moyenne unie = marron (pas vert)", nom_moyen(mesure_c4) == "marron",
          nom_moyen(mesure_c4))
    check("Mesure C4 : l'oeil sur cette image unie est d'accord",
          nommer_couleurs(img_c4)[0]["nom"] == "marron")
    mesure_plage = np.array([91.4, 124.9, 130.4], np.float64)
    img_plage = np.zeros((40, 40, 3), np.uint8)
    img_plage[:] = np.clip(np.round(mesure_plage), 0, 255).astype(np.uint8)
    check("Mesure B2-D3 : moyenne unie = jaune (pas rouge)", nom_moyen(mesure_plage) == "jaune",
          nom_moyen(mesure_plage))
    check("Mesure B2-D3 : l'oeil sur cette image unie est d'accord",
          nommer_couleurs(img_plage)[0]["nom"] == "jaune")

    unie = np.full((200, 300, 3), 90, np.uint8)
    f = formes_sommaires(unie)
    check("zone unie = unie, 0 rectangle", f["niveau"] == "unie" and f["rectangles"] == 0, str(f))
    cadres = np.full((300, 400, 3), 30, np.uint8)
    for x0, y0 in ((20, 20), (150, 40), (40, 180)):
        cv2.rectangle(cadres, (x0, y0), (x0 + 90, y0 + 60), (230, 230, 230), 2)
    f = formes_sommaires(cadres)
    # Comptes EXACTS (audit 23/10) : « au moins 1 » laissait passer le double compte
    # (chaque trait epaissi a deux bords : 1 disque donnait 2 ronds, 3 cadres 6).
    check("trois cadres dessines = 3 rectangles, pas 6", f["rectangles"] == 3, str(f))
    tri = np.full((300, 400, 3), 20, np.uint8)
    pts = np.array([[200, 40], [60, 250], [340, 250]], np.int32)
    cv2.polylines(tri, [pts], True, (0, 220, 220), 4)
    f = formes_sommaires(tri)
    check("un triangle dessine = 1 triangle", f.get("triangles", 0) == 1, str(f))
    plein = np.full((300, 400, 3), 20, np.uint8)
    cv2.fillPoly(plein, [pts], (0, 220, 220))
    f = formes_sommaires(plein)
    check("un triangle plein = 1 triangle", f.get("triangles", 0) == 1, str(f))
    rond = np.full((300, 400, 3), 20, np.uint8)
    cv2.circle(rond, (200, 150), 80, (220, 180, 40), 4)
    f = formes_sommaires(rond)
    check("un cercle dessine = 1 rond", f.get("ronds", 0) == 1, str(f))
    check("un cercle n'est pas compte comme triangle", f.get("triangles", 0) == 0, str(f))
    cadre = np.full((320, 420, 3), 15, np.uint8)
    cv2.rectangle(cadre, (8, 8), (410, 310), (90, 90, 90), 3)
    cv2.circle(cadre, (210, 160), 70, (20, 140, 230), -1)
    f = formes_sommaires(cadre)
    check("disque plein dans un cadre = 1 rond et 1 rectangle",
          f.get("ronds", 0) == 1 and f["rectangles"] == 1, str(f))
    gigogne = np.full((320, 420, 3), 15, np.uint8)
    cv2.rectangle(gigogne, (8, 8), (410, 310), (90, 90, 90), 3)
    cv2.rectangle(gigogne, (60, 60), (360, 260), (90, 200, 90), 3)
    cv2.circle(gigogne, (210, 160), 50, (20, 140, 230), -1)
    f = formes_sommaires(gigogne)
    check("cadre dans un cadre, disque au centre = 2 rectangles et 1 rond",
          f["rectangles"] == 2 and f.get("ronds", 0) == 1, str(f))


def _image_test() -> np.ndarray:
    """960x320 : colonnes F..H noires, I..K blanches ; texte en grand dans la case H9."""
    img = np.zeros((320, 960, 3), np.uint8)
    img[:, 480:] = (255, 255, 255)
    return img


def smoke_lire() -> None:
    print()
    print("Lecture avec capture injectee")
    appels: list[tuple[int, int, int, int]] = []

    def grab(l, t, w, h):  # noqa: E741
        appels.append((l, t, w, h))
        return _image_test()

    r = lire("1:F8-1:K9", moniteurs=MONS, grab=grab)
    check("une seule capture pour 12 cases", len(appels) == 1, str(appels))
    check("capture du bon rectangle", appels and appels[0] == (800, 1120, 960, 320), str(appels))
    check("12 cases decrites", len(r["par_case"]) == 12, str(len(r["par_case"])))
    cote = {pc["case"]: pc["couleur"] for pc in r["par_case"]}
    check("cases de gauche noires, de droite blanches (meme image)",
          cote.get("1:F8") == "noir" and cote.get("1:H9") == "noir"
          and cote.get("1:I8") == "blanc" and cote.get("1:K9") == "blanc", str(cote))
    check("resultat serialisable en JSON", bool(json.dumps(r)))
    check("pas de texte ni de description sans demande",
          r["texte"] is None and r["description"] is None)
    seul = lire("1:A1", moniteurs=MONS, grab=lambda l, t, w, h: np.full((h, w, 3), 120, np.uint8))
    check("une case seule : pas de detail par case", seul["par_case"] == [])

    print()
    print("Zone noire : on s'abstient, on ne devine pas")
    appels_desc: list[int] = []

    def faux_modele(frame):
        appels_desc.append(frame.shape[1])
        return {"available": True, "moteur": "faux", "texte": "invente", "reason": "", "elapsed_ms": 0.0}

    rn = lire("1:A1-1:B1", moniteurs=MONS, grab=lambda l, t, w, h: np.zeros((h, w, 3), np.uint8),
              avec_texte=True, decrire=True, decrire_fn=faux_modele)
    check("zone noire detectee", rn["zone_noire"] is True)
    check("le modele n'est meme pas appele", appels_desc == [], str(appels_desc))
    check("la description s'abstient avec la raison",
          rn["description"]["available"] is False and "zone noire" in rn["description"]["reason"])
    check("l'OCR s'abstient avec la raison",
          rn["texte"]["available"] is False and "zone noire" in rn["texte"]["reason"])

    rc = lire("1:A1-1:B1", moniteurs=MONS, grab=lambda l, t, w, h: np.full((h, w, 3), 140, np.uint8),
              decrire=True, decrire_fn=faux_modele)
    check("zone claire : le modele recoit la zone entiere", appels_desc == [320], str(appels_desc))
    check("la description est marquee a verifier",
          any("a verifier" in n for n in rc["notes"]), str(rc["notes"]))

    print()
    print("Texte (OCR Tesseract) range par case")
    img = np.full((320, 960, 3), 255, np.uint8)
    # « BONJOUR » centre dans la case H9 de la plage F8-K9 (x 320..480, y 160..320)
    cv2.putText(img, "BONJOUR", (330, 255), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 3, cv2.LINE_AA)
    rt = lire("1:F8-1:K9", moniteurs=MONS, grab=lambda l, t, w, h: img, avec_texte=True)
    lignes = rt["texte"]["lignes"] if rt["texte"] else []
    trouve = [ln for ln in lignes if "BONJOUR" in ln["texte"].upper()]
    check("OCR lit BONJOUR", bool(trouve), str(lignes)[:80])
    check("la ligne est rangee dans la case H9", bool(trouve) and trouve[0]["case"] == "1:H9",
          str(trouve[:1]))

    # audit V1.1b (scan du 17/10) : deux fenetres cote a cote, meme hauteur. Le
    # regroupement par hauteur collait leurs mots en UNE ligne, rangee dans la
    # case du milieu : « fichiers.pdf ... Undo All Keep All » en 1:H4 alors que
    # les mots venaient de B4 a O4.
    deux = np.full((320, 960, 3), 255, np.uint8)
    cv2.putText(deux, "GAUCHE", (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(deux, "DROITE", (810, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3, cv2.LINE_AA)
    rd = lire("1:F8-1:K9", moniteurs=MONS, grab=lambda l, t, w, h: deux, avec_texte=True)
    par = {ln["texte"].upper(): ln["case"] for ln in (rd["texte"] or {}).get("lignes") or []}
    check("deux textes eloignes sur une meme hauteur = deux lignes, pas une",
          "GAUCHE" in par and "DROITE" in par, str(par))
    check("chacun dans SA case (F8 et K8, pas la case du milieu)",
          par.get("GAUCHE") == "1:F8" and par.get("DROITE") == "1:K8", str(par))
    phrase = np.full((320, 960, 3), 255, np.uint8)
    cv2.putText(phrase, "UNE PHRASE QUI DEBORDE", (100, 250), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3, cv2.LINE_AA)
    rp = lire("1:F8-1:K9", moniteurs=MONS, grab=lambda l, t, w, h: phrase, avec_texte=True)
    lp = [ln for ln in (rp["texte"] or {}).get("lignes") or [] if "PHRASE" in ln["texte"].upper()]
    check("une phrase normale reste entiere, avec les cases qu'elle couvre",
          len(lp) == 1 and "DEBORDE" in lp[0]["texte"].upper() and len(lp[0].get("cases") or []) >= 2,
          str(lp)[:120])


class _FauxOllama(http.server.BaseHTTPRequestHandler):
    modeles: list[str] = []
    reponse: dict = {}
    requetes: list[tuple[str, dict]] = []

    def log_message(self, *a):  # silence
        pass

    def _json(self, code: int, obj: dict) -> None:
        corps = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self):  # noqa: N802
        type(self).requetes.append((self.path, {}))
        if self.path == "/api/tags":
            self._json(200, {"models": [{"name": n, "model": n} for n in type(self).modeles]})
        else:
            self._json(404, {})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        corps = json.loads(self.rfile.read(n) or b"{}")
        type(self).requetes.append((self.path, corps))
        if self.path == "/api/chat":
            self._json(200, type(self).reponse)
        else:
            self._json(404, {})


def smoke_ollama() -> None:
    print()
    print("Description Ollama contre un faux serveur (aucun vrai modele)")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FauxOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ancien = screen_cells.OLLAMA
    screen_cells.OLLAMA = f"http://127.0.0.1:{srv.server_address[1]}"
    image = np.full((240, 320, 3), 150, np.uint8)
    try:
        _FauxOllama.modeles = ["autre:latest"]
        _FauxOllama.requetes = []
        r = decrire_ollama(image, "qwen3.5:9b-gpu")
        chemins = [p for p, _ in _FauxOllama.requetes]
        check("modele absent : abstention, aucun telechargement",
              r["available"] is False and "aucun telechargement" in r["reason"], r["reason"])
        check("modele absent : ni /api/pull ni /api/chat", chemins == ["/api/tags"], str(chemins))

        _FauxOllama.modeles = ["qwen3.5:9b-gpu"]
        _FauxOllama.reponse = {"message": {"content": "Une fenetre grise."}, "done_reason": "stop"}
        _FauxOllama.requetes = []
        r = decrire_ollama(image, "qwen3.5:9b-gpu")
        check("modele present : description remontee", r["available"] and r["texte"] == "Une fenetre grise.",
              str(r.get("texte")))
        envoi = [c for p, c in _FauxOllama.requetes if p == "/api/chat"]
        e = envoi[0] if envoi else {}
        msg = (e.get("messages") or [{}])[0]
        check("requete : une image, think false, temperature 0",
              len(msg.get("images") or []) == 1 and e.get("think") is False
              and (e.get("options") or {}).get("temperature") == 0, str({k: e.get(k) for k in ("think", "options")}))
        check("requete : aucune persona (pas de message system)",
              all(m.get("role") != "system" for m in e.get("messages") or []))
        check("phrase marquee a verifier DANS la description (pas seulement en note)",
              r.get("a_verifier") is True and r.get("tronquee") is False, str(r))

        # audit V1.1b : la phrase de la zone 1-bas s'arretait au milieu d'un mot
        # (done_reason « length ») et rien ne le disait.
        _FauxOllama.reponse = {"message": {"content": "Une liste qui s'arr"}, "done_reason": "length"}
        r = decrire_ollama(image, "qwen3.5:9b-gpu")
        check("phrase coupee par la limite : signalee tronquee", r.get("tronquee") is True, str(r))
        import io
        import contextlib

        import lire_cases as _lc

        sortie = io.StringIO()
        with contextlib.redirect_stdout(sortie):
            _lc._afficher({"cible": "1:A1", "ecran": 1, "grille": "16x9", "cases": 1,
                           "bbox": [0, 0, 160, 160], "couleurs": [], "luminance": 50.0,
                           "contraste": 10.0, "zone_noire": False,
                           "formes": {"niveau": "unie", "details_pct": 0, "rectangles": 0, "autres_contours": 0},
                           "par_case": [], "texte": None, "description": r, "notes": [],
                           "capture_ms": 1.0, "taille": [160, 160]})
        check("la ligne de commande dit que la phrase est coupee", "COUPEE" in sortie.getvalue().upper(),
              sortie.getvalue().strip().splitlines()[-2][:80] if sortie.getvalue() else "")

        _FauxOllama.reponse = {"message": {"content": "", "thinking": "je pense que..."}}
        r = decrire_ollama(image, "qwen3.5:9b-gpu")
        check("reponse seulement dans thinking : refusee, pas recopiee",
              r["available"] is False and "thinking" in r["reason"] and r["texte"] is None, r["reason"])
    finally:
        srv.shutdown()
        screen_cells.OLLAMA = "http://127.0.0.1:9"
        r = decrire_ollama(image, "qwen3.5:9b-gpu", timeout=2)
        check("Ollama eteint : abstention propre, pas d'exception",
              r["available"] is False and "injoignable" in r["reason"], r["reason"])
        # Garde-fou (18/10) : une adresse hors de ce PC est refusee AVANT tout envoi
        os.environ.pop("VISION_OLLAMA_DISTANT", None)
        screen_cells.OLLAMA = "http://10.0.0.5:11434"
        t_dist = time.perf_counter()
        r = decrire_ollama(image, "qwen3.5:9b-gpu", timeout=2)
        check("modele sur une autre machine : refuse, l'image ne sort pas",
              r["available"] is False and "hors de ce PC" in r["reason"]
              and time.perf_counter() - t_dist < 0.5, r["reason"])
        check("adresses locales reconnues (127.0.0.1, localhost, ::1)",
              all(screen_cells.ollama_local(u) for u in
                  ("http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"))
              and not screen_cells.ollama_local("http://127.0.0.1.evil.com:11434"))
        os.environ["VISION_OLLAMA_DISTANT"] = "1"
        screen_cells.OLLAMA = "http://0.0.0.0:9"
        r = decrire_ollama(image, "qwen3.5:9b-gpu", timeout=2)
        check("accord explicite VISION_OLLAMA_DISTANT=1 : le garde-fou laisse passer",
              "hors de ce PC" not in r["reason"], r["reason"])
        os.environ.pop("VISION_OLLAMA_DISTANT", None)
        screen_cells.OLLAMA = ancien


def _resultat_hyperv(avec_hyperv: bool) -> dict:
    """Un resultat de lire() fabrique a la main : ecran 2, fond blanc, OCR choisi."""
    lignes = [{"case": "2:I5", "cases": ["2:I5", "2:J5"], "texte": "Aucun point de controle.",
               "bbox": [3500, 700, 3700, 720], "conf_moy": 88.0}]
    if avec_hyperv:
        lignes.insert(0, {"case": "2:A1", "cases": ["2:A1", "2:B1"], "texte": "Gestionnaire Hyper-V",
                          "bbox": [2565, 190, 2760, 205], "conf_moy": 82.0})
    par_case = []
    for i, col in enumerate("ABCD"):
        clair = i < 2
        par_case.append({"case": f"2:{col}1", "luminance": 240.0 if clair else 30.0, "zone_noire": False,
                         "couleur": "blanc" if clair else "noir", "pct": 95.0})
    return {"demande": "2:A1-2:D1", "cible": "2:A1-2:D1", "ecran": 2, "grille": "16x9", "cases": 4,
            "bbox": [2560, 182, 3040, 302], "taille": [480, 120], "capture_ms": 5.0,
            "zone_noire": False, "luminance": 140.0, "contraste": 100.0,
            "couleurs": [{"nom": "blanc", "pct": 92.0}, {"nom": "noir", "pct": 6.0}, {"nom": "rouge", "pct": 2.0}],
            "formes": {"rectangles": 3, "autres_contours": 2, "details_pct": 4.1, "niveau": "details moyens"},
            "par_case": par_case,
            "texte": {"available": True, "reason": "", "mots": 5, "elapsed_ms": 1.0, "lignes": lignes},
            "description": None, "notes": []}


def smoke_rapport_mesure() -> None:
    print()
    print("Rapport-mesure : phrases du code, sans modele (V1.1c)")
    from screen_cells import rapport_mesure

    rh = rapport_mesure(_resultat_hyperv(True))
    texte = rh.get("texte") or ""
    check("rapport produit, marque deterministe, moteur gabarit",
          bool(texte) and rh.get("determine") is True and rh.get("moteur") == "gabarit", texte[:80])
    check("meme entree = meme rapport (deterministe)", rapport_mesure(_resultat_hyperv(True)) == rh)
    check("blanc dominant mentionne avec son pourcentage", "blanc" in texte and "92" in texte, texte[:120])
    check("Hyper-V cite PARCE QUE l'OCR l'a lu", "Gestionnaire Hyper-V" in texte, texte[-200:])
    sans = rapport_mesure(_resultat_hyperv(False)).get("texte") or ""
    check("sans le mot dans l'OCR : Hyper-V n'est pas cite", "Hyper-V" not in sans, sans[-160:])
    # noms de VM qu'un modele avait inventes lors d'un essai (V1.1b), anonymises pour la vitrine
    check("aucun nom de VM invente quand l'OCR ne l'a pas", "SRV-01" not in texte and "VM-JEU" not in texte)
    import re as _re

    citations = _re.findall(r"« ([^»]+) »", texte)
    ocr = [ln["texte"] for ln in _resultat_hyperv(True)["texte"]["lignes"]]
    check("chaque citation entre guillemets est recopiee telle quelle de l'OCR",
          bool(citations) and all(any(cit == ln for ln in ocr) or cit in "".join(ocr) for cit in citations),
          str(citations)[:120])
    check("disposition dite par colonnes (clair a gauche, sombre a droite)",
          "A" in texte and "B" in texte and "clair" in texte and "sombre" in texte, texte[:200])

    # Essai live du 17/10 07:57 : l'OCR a lu un message de chat qui CITAIT
    # « Gestionnaire Hyper-V », et le rapport a conclu « cette zone montre le
    # Gestionnaire Hyper-V ». Rien d'invente, mais une mauvaise conclusion.
    chat = _resultat_hyperv(False)
    chat["texte"]["lignes"] = [{"case": "1:K8", "cases": ["1:K8", "1:L8"], "conf_moy": 90.0,
                                "bbox": [1600, 1200, 2400, 1220],
                                "texte": "le rapport cite bien « Gestionnaire Hyper-V » issu de l'OCR,"}]
    tc = rapport_mesure(chat)
    check("mot-repere entre guillemets (une citation) : scene NON affirmee",
          "montre le Gestionnaire" not in (tc.get("texte") or "")
          and not any(x.get("affirme") for x in tc.get("reperes") or []), (tc.get("texte") or "")[-160:])
    longue = _resultat_hyperv(False)
    longue["texte"]["lignes"] = [{"case": "1:K8", "cases": ["1:K8"], "conf_moy": 90.0,
                                  "bbox": [1600, 1200, 2400, 1220],
                                  "texte": "demain on ouvrira le Gestionnaire Hyper-V pour creer la VM de test"}]
    tl = rapport_mesure(longue)
    check("mot-repere au milieu d'une longue phrase : dit, mais scene non affirmee",
          "Gestionnaire Hyper-V" in (tl.get("texte") or "") and "non affirm" in (tl.get("texte") or "")
          and not any(x.get("affirme") for x in tl.get("reperes") or []), (tl.get("texte") or "")[-160:])
    check("libelle court (vrai titre de fenetre) : scene affirmee",
          any(x.get("affirme") for x in rh.get("reperes") or []))

    noire = _resultat_hyperv(True)
    noire.update(zone_noire=True, luminance=4.0, contraste=1.0,
                 couleurs=[{"nom": "noir", "pct": 100.0}])
    tn = rapport_mesure(noire).get("texte") or ""
    check("zone noire : le rapport s'abstient (pas de texte cite, pas de scene)",
          "noire" in tn and "Hyper-V" not in tn and "«" not in tn, tn[:120])
    vide = _resultat_hyperv(False)
    vide["texte"] = {"available": True, "reason": "", "mots": 0, "elapsed_ms": 1.0, "lignes": []}
    tv = rapport_mesure(vide).get("texte") or ""
    check("OCR vide : le rapport le dit", "aucun texte" in tv.lower(), tv[-120:])

    print()
    print("Rapport-mesure : zero appel reseau, zero modele")
    import urllib.request as _ur

    appels: list[str] = []
    vrai = _ur.urlopen

    def espion(*a, **k):
        appels.append(str(a[0] if a else k))
        raise AssertionError("appel reseau interdit en rapport-mesure")

    _ur.urlopen = espion
    try:
        r = lire("2:A1-2:D1", moniteurs=MONS, grab=lambda l, t, w, h: np.full((h, w, 3), 230, np.uint8),
                 rapport_mesure=True)
    finally:
        _ur.urlopen = vrai
    check("--rapport-mesure seul : aucun appel reseau (Ollama compris)", appels == [], str(appels))
    check("--rapport-mesure seul : pas de description de modele", r.get("description") is None)
    check("--rapport-mesure lit le texte tout seul (OCR local)", r.get("texte") is not None)
    check("bloc JSON dedie, distinct de description",
          (r.get("rapport_mesure") or {}).get("determine") is True)


def smoke_surete() -> None:
    print()
    print("Surete (lecture de l'arbre syntaxique, pas de la prose)")
    interdits = {"SetCursorPos", "mouse_event", "keybd_event", "SendInput", "imwrite"}
    for chemin in (ROOT / "src" / "screen_cells.py", ROOT / "scripts" / "lire_cases.py"):
        arbre = ast.parse(chemin.read_text(encoding="utf-8"))
        noms = {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
        noms |= {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
        chaines = [n.value for n in ast.walk(arbre) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        check(f"{chemin.name} : aucune actuation, aucune image ecrite",
              not (noms & interdits), str(noms & interdits))
        # Les adresses appelees sont des chaines qui COMMENCENT par /api/ ; une
        # docstring qui dit « aucun appel a /api/pull » n'en est pas une.
        adresses = {s for s in chaines if s.startswith("/api/")}
        check(f"{chemin.name} : aucun appel a /api/pull", "/api/pull" not in adresses,
              ", ".join(sorted(adresses)) or "aucune adresse")

    print()
    print("Ligne de commande")
    import lire_cases

    check("1:L16 refusee, code de sortie 2", lire_cases.main(["1:L16"]) == 2)
    check("--grille sort 0", lire_cases.main(["--grille"]) == 0)


def smoke_live() -> None:
    print()
    print("Lecture reelle (case 1:A1, rien du contenu n'est affiche)")
    from screen_context import list_monitors
    from screen_grid import grid_for_monitor

    mons = list_monitors()
    m1 = next(m for m in mons if m.index == 1)
    g = grid_for_monitor(m1)
    attendu = [m1.left, m1.top, m1.left + int(g.cell_w), m1.top + int(g.cell_h)]
    r = lire("1:A1")
    check("1:A1 reelle = premiere case de la grille de l'overlay", r["bbox"] == attendu,
          f"{r['bbox']} vs {attendu}")
    check("1:A1 reelle mesuree", isinstance(r["luminance"], float) and bool(r["couleurs"]),
          f"capture {r['capture_ms']} ms")


def main() -> int:
    print("=" * 70)
    print("V1.1 smoke_lire_cases")
    print("=" * 70)
    smoke_cibles()
    smoke_mesures()
    smoke_lire()
    smoke_ollama()
    smoke_rapport_mesure()
    smoke_surete()
    smoke_live()
    print()
    bons = sum(1 for c in CHECKS if c[1])
    print(f"Resultat : {bons}/{len(CHECKS)} OK")
    for lab, ok, det in CHECKS:
        if not ok:
            print(f"  KO : {lab} {det}")
    return 0 if bons == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
