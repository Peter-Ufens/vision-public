"""Smoke du digest de session et du pont vers le duo (R-S17).

Usage :
  python scripts/smoke_session_digest.py

Sortie : exit 0 si tous les controles passent, 1 sinon. **Aucun Ollama
requis** : tout ce qui compte se teste hors ligne, sur des traces
synthetiques ecrites ici meme.

CE QUE CE SMOKE SURVEILLE VRAIMENT

Le digest est ce qui part a un modele. Trois choses doivent rester vraies,
et chacune a deja coute quelque chose a quelqu'un :

  1. **le texte lu a l'ecran ne part jamais** sans un opt-in explicite.
     R-S14 : au niveau `texte`, la boite Executer a livre des chemins de
     fichiers personnels tires de son historique ;
  2. **une zone noire produit un fait indetermine**, jamais une cause.
     R-S15 : quatre modeles sur cinq affirmaient "DRM" avec une confiance
     de 1,0 la ou la trace refuse de trancher ;
  3. **une session tourne sans Ollama.** Le digest n'appelle rien, et
     `session_screen.py` ne connait ni le digest ni le duo.

Les traces sont SYNTHETIQUES : un smoke qui depend d'une trace reelle
presente sur le poste ne teste plus rien le jour ou elle n'y est pas. Une
trace reelle est utilisee en supplement si elle existe, et le bloc est
declare saute sinon.
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
    SAUTES.append((bloc, raison))
    print(f"  [   ] {bloc} -- NON EXERCE : {raison}")


def _l(**kw: Any) -> dict[str, Any]:
    return {"v": 1, "session_id": "session-synthetique", **kw}


def _keyframe(t: float) -> dict[str, Any]:
    """Une image-cle realiste : c'est elle qui pese 88 % d'une vraie trace."""
    return _l(type="session.keyframe", t_s=t, etat={
        "moniteurs": [[1, 2560, 1440], [2, 1920, 1080]],
        "actif": {"exe": "Code.exe", "hote": "a1b2c3d4", "confiance": 1.0},
        "fenetres": {str(1000 + i): {
            "exe": f"app{i}.exe", "classe": "Chrome_WidgetWin_1",
            "bbox": [0, 0, 2560, 1392], "ecran": 1, "premier_plan": i == 0}
            for i in range(8)}})


def trace_riche() -> list[dict[str, Any]]:
    """Une session complete : fenetres, contenu lisible, zone noire, decor."""
    lignes = [_l(type="session.start", t_s=0.0, profil="film", cadence_s=5.0,
                 duree_prevue_s=1800.0, contenu="texte", riche_cadence_s=60.0)]
    lignes += [_keyframe(t * 60.0) for t in range(30)]
    lignes.append(_l(type="session.diff", t_s=41.0, changements=[
        {"quoi": "fenetre.apparue", "exe": "cmd.exe", "classe": "ConsoleWindowClass",
         "bbox": [820, 410, 1740, 930], "ecran": 1}]))
    lignes.append(_l(type="session.contenu", t_s=41.0, quoi="contenu.fenetre",
                     exe="cmd.exe", classe="ConsoleWindowClass", lisible=True,
                     mots=3, lignes=1, capture_ms=22.4, ocr_ms=381.0,
                     texte=["BONJOUR VISION", "C:/Users/exemple/Desktop/prive.txt"]))  # filet-ok: donnee de test, chemin a effacer
    lignes.append(_l(type="session.diff", t_s=43.0, changements=[
        {"quoi": "fenetre.disparue", "exe": "cmd.exe",
         "classe": "ConsoleWindowClass"}]))
    lignes.append(_l(type="session.contenu", t_s=120.0, quoi="contenu.fenetre",
                     exe="msedge.exe", classe="Chrome_WidgetWin_1", lisible=False,
                     luminance=0.3, capture_ms=31.2,
                     raison=("zone noire : rien a lire. Flux protege (DRM), ecran "
                             "eteint ou scene tres sombre, indiscernables sur ce "
                             "seul signal."),
                     identite={"pid": 53096, "exe": "msedge.exe",
                               "demarre_depuis_s": 611.1}))
    lignes.append(_l(type="session.decor", t_s=0.2, quoi="decor.initial", ecran=1,
                     taille=[2560, 1440], luminance=21.1, theme="sombre",
                     dominante=[0, 0, 0], noir=False, densite_texte=0.029,
                     texte_present=True))
    lignes.append(_l(type="session.decor", t_s=60.2, ecran=1, changements=[
        {"quoi": "decor.luminance", "de": 21.1, "vers": 88.9}]))
    lignes.append(_l(type="session.end", t_s=1800.0, duree_reelle_s=1800.0,
                     duree_prevue_s=1800.0, ticks=360, changements=3,
                     images_cles=30, erreurs_tick=0, contenu="texte",
                     captures_armees=2, lectures_faites=2, lectures_en_echec=0,
                     captures_abandonnees=0, captures_non_lues=0,
                     ticks_attendus=360, ticks_sautes=0,
                     raison_arret="duree_atteinte",
                     fichier="D:/projets/vision/data/sessions/x.jsonl"))
    return lignes


def trace_trouee() -> list[dict[str, Any]]:
    """Une session interrompue, qui declare elle-meme ses trous."""
    return [
        _l(type="session.start", t_s=0.0, profil="activite", cadence_s=1.0,
           duree_prevue_s=600.0, contenu="resume"),
        _keyframe(0.2),
        _l(type="session.erreur", t_s=88.0, erreur="lecture contenu : COMError"),
        _l(type="session.end", t_s=430.0, duree_reelle_s=430.0, ticks=383,
           changements=12, images_cles=7, erreurs_tick=4, ticks_attendus=430,
           ticks_sautes=47, captures_non_lues=3, captures_abandonnees=2,
           relectures_perdues=1, raison_arret="interruption"),
    ]


def main() -> int:  # noqa: C901
    import session_digest as sd
    from llm_stream import Faits, corriger

    print("=" * 74)
    print("Smoke digest de session + pont (R-S17)")
    print("=" * 74)

    riche = trace_riche()
    dig = sd.digerer(riche)

    # --- Le poids ------------------------------------------------------------ #
    print()
    print("Le poids : une trace de 30 min doit tenir dans le contexte")

    brut = "\n".join(json.dumps(l, ensure_ascii=False) for l in riche)
    texte = dig.texte_pour_modele()
    jetons_brut, jetons_dig = len(brut) // 4, len(texte) // 4
    check("la trace brute depasse bien le contexte par defaut", jetons_brut > 8192,
          f"~{jetons_brut} jetons contre 8192")
    check("le digest tient largement dedans", jetons_dig < 2000,
          f"~{jetons_dig} jetons, soit {len(brut) / max(1, len(texte)):.1f} fois plus petit")
    check("les images-cles au-dela de la premiere sont jetees",
          dig.images_cles_jetees == 29, f"{dig.images_cles_jetees} jetees sur 30")
    check("mais l'ancrage du debut est garde",
          dig.debut.get("fenetres_ouvertes") == 8 and dig.debut.get("actif_exe"),
          "sans lui, une trace differentielle ne sait plus dire ce qu'il y avait au depart")
    check("aucun etat complet ne subsiste dans le digest",
          "premier_plan" not in texte and "bbox" not in texte)

    # --- Le decor ne doit pas grossir sans fin ------------------------------- #
    print()
    print("Le decor est replie, pas recopie")

    # R-S18, session reelle de 30 min : la section `decors` faisait 84 % du
    # digest (11 839 o sur 12 598). Le digest reproduisait, un cran plus bas,
    # la pathologie des images-cles qu'il etait cense corriger. Ces controles
    # sont ce qui manquait pour le voir venir.
    def trace_decor(n: int, avec_noir: bool = False) -> list[dict[str, Any]]:
        lignes = [_l(type="session.start", t_s=0.0, profil="film", cadence_s=5.0,
                     duree_prevue_s=1800.0, contenu="texte", riche_cadence_s=20.0),
                  _keyframe(0.2),
                  _l(type="session.decor", t_s=0.2, quoi="decor.initial", ecran=1,
                     taille=[2560, 1440], luminance=50.0, theme="sombre",
                     dominante=[0, 0, 0], noir=False, densite_texte=0.03,
                     texte_present=True)]
        for i in range(n):
            lignes.append(_l(type="session.decor", t_s=20.0 * (i + 1), ecran=1,
                             changements=[{"quoi": "decor.luminance",
                                           "de": 50.0 + i, "vers": 51.0 + i}]))
            lignes.append(_l(type="session.decor", t_s=20.0 * (i + 1),
                             quoi="decor.texte", ecran=1, lisible=True,
                             mots=130 + i, lignes=20, ocr_ms=900.0))
        if avec_noir:
            lignes.append(_l(type="session.decor", t_s=20.0 * (n + 1), ecran=1,
                             changements=[{"quoi": "decor.noir", "de": False,
                                           "vers": True}]))
        lignes.append(_l(type="session.end", t_s=1800.0, duree_reelle_s=1800.0,
                         ticks=360, ticks_attendus=360, ticks_sautes=0,
                         raison_arret="duree_atteinte"))
        return lignes

    petit = len(sd.digerer(trace_decor(20)).texte_pour_modele())
    grand = len(sd.digerer(trace_decor(400)).texte_pour_modele())
    check("vingt fois plus d'echantillons ne fait pas vingt fois le digest",
          grand < petit * 2, f"{petit} o pour 20 echantillons, {grand} o pour 400")
    check("un digest de 400 echantillons tient largement dans le contexte",
          grand // 4 < 2000, f"~{grand // 4} jetons")

    dg = sd.digerer(trace_decor(400))
    bilan = dg.decor_bilan.get("1") or {}
    check("le bilan garde le compte des echantillons",
          bilan.get("echantillons") == 401, str(bilan.get("echantillons")))
    check("le bilan garde la plage de luminance parcourue",
          bilan.get("luminance_min") == 50.0 and bilan.get("luminance_max") == 450.0,
          f"{bilan.get('luminance_min')} a {bilan.get('luminance_max')}")
    # Chercher le mot "texte" serait naif : il apparait legitimement dans des
    # noms de cles (`texte_present_au_depart`, `niveau_contenu`). Ce qui doit
    # etre absent, c'est le TABLEAU des lignes lues.
    check("le bilan compte les lectures de texte sans recopier les mots",
          bilan.get("lectures_texte") == 400
          and '"texte":[' not in dg.texte_pour_modele(),
          f"{bilan.get('lectures_texte')} lectures comptees")
    check("les moments notables restent bornes", len(dg.decor_moments) <= 20,
          f"{len(dg.decor_moments)} moments")

    dn = sd.digerer(trace_decor(400, avec_noir=True))
    check("un passage au noir survit TOUJOURS au repli",
          any(m.get("quoi") == "decor.noir" for m in dn.decor_moments),
          "c'est le signal DRM : il ne se resume pas, il se garde")
    cles_noir = {f["cle"] for f in sd.faits_depuis_digest(dn)}
    check("et il devient un fait", "ecran1_passages_au_noir" in cles_noir)

    # --- Vie privee ---------------------------------------------------------- #
    print()
    print("Vie privee : ce qui ne part jamais")

    for motif in ("BONJOUR VISION", "Users", "exemple", "prive.txt"):
        check(f"'{motif}' n'est pas dans le digest par defaut", motif not in texte)
    check("le chemin absolu du pied est retire aussi",
          "projets/vision" not in texte and "fichier" not in texte,
          "il porte un nom d'utilisateur")

    avec = sd.digerer(riche, avec_texte=True).texte_pour_modele()
    check("--avec-texte inclut le texte, explicitement", "BONJOUR VISION" in avec,
          "l'opt-in doit vraiment changer quelque chose, sinon il ment")
    check("et il coute plus cher", len(avec) > len(texte),
          f"{len(avec)} o contre {len(texte)} o")

    # --- Codes stables ------------------------------------------------------- #
    print()
    print("Codes stables plutot que phrases recopiables")

    noir = [c for c in dig.contenus if not c["lisible"]]
    check("la zone noire recoit un code stable",
          noir and noir[0].get("raison_code") == "zone_noire_indiscernable",
          str(noir[0].get("raison_code")) if noir else "aucune capture illisible")
    check("la phrase francaise d'origine ne part PAS au modele",
          "indiscernables" not in texte and "DRM" not in texte,
          "R-S15 : 4 modeles sur 5 la recopiaient dans un champ 'cause certaine'")
    check("l'identite du processus survit, elle", "53096" in texte,
          "c'est le repli du technicien quand l'ecran ne montre rien")

    # --- Faits --------------------------------------------------------------- #
    print()
    print("Faits : compatibles avec le correcteur generique")

    bruts = sd.faits_depuis_digest(dig)
    faits = Faits.depuis(bruts)
    check("les faits se chargent dans llm_stream.Faits", len(faits.faits) == len(bruts),
          f"{len(bruts)} faits")
    cles = {f["cle"] for f in bruts}

    indet = [f for f in bruts if f.get("indetermine")]
    check("la zone noire produit un fait INDETERMINE", len(indet) == 1,
          str([f["cle"] for f in indet]))
    check("ce fait porte des marqueurs Vision utiles",
          indet and "drm" in (indet[0].get("marqueurs") or []),
          str(indet[0].get("marqueurs")) if indet else "")
    check("les unites sont dans le nom des cles",
          "secondes_entre_deux_observations" in cles and "cadence_s" not in cles,
          "mesure R-S17 : 'cadence_s' s'est fait lire '5 images par seconde'")
    check("les trous declares deviennent un fait",
          "trous_declares" in cles or not dig.trous)
    check("les executables vus sont listes", "executables_vus" in cles)

    # Le booleen `contenu_lisible` : seulement s'il est faux sans ambiguite.
    check("session mixte : pas de booleen 'lisible' impose",
          "contenu_lisible" not in cles,
          "une capture lisible et une illisible : dire lisible=False serait faux")
    tout_noir = [l for l in riche if not (l.get("type") == "session.contenu"
                                          and l.get("lisible"))]
    cles_noir = {f["cle"] for f in sd.faits_depuis_digest(sd.digerer(tout_noir))}
    check("session entierement illisible : le booleen apparait",
          "contenu_lisible" in cles_noir)

    # --- Le tour complet ----------------------------------------------------- #
    print()
    print("Le tour complet : un brouillon fautif rencontre ces faits")

    r = corriger("La session a observe une fenetre cmd.exe. "
                 "La zone noire vient du DRM de Prime Video.", faits)
    check("une cause affirmee sur la zone noire est neutralisee",
          any(a.genre == "cause_promue" for a in r.anomalies)
          and "DRM" not in r.texte, r.texte)
    check("l'invariant de soustraction tient avec des faits Vision",
          r.invariant_respecte, "le correcteur de R-S16 n'a pas ete touche")

    faits_noir = Faits.depuis(sd.faits_depuis_digest(sd.digerer(tout_noir)))
    r = corriger("Tout le contenu etait lisible.", faits_noir)
    check("une contradiction sur 'lisible' est attrapee",
          any(a.genre == "contradiction" for a in r.anomalies), str(r.anomalies))
    r = corriger("Le contenu n'etait pas lisible.", faits_noir)
    check("le temoin negatif passe, lui",
          not any(a.genre == "contradiction" for a in r.anomalies),
          "sans ce temoin, un detecteur qui refuse tout aurait l'air parfait")

    r = corriger("Une fenetre firefox.exe est apparue.", faits)
    check("un executable jamais vu est retire",
          any(a.genre == "fait_absent" for a in r.anomalies)
          and "firefox.exe" not in r.texte)

    # --- Trace abimee -------------------------------------------------------- #
    print()
    print("Traces abimees")

    dt = sd.digerer(trace_trouee())
    check("les trous du pied sont remontes", len(dt.trous) >= 4, str(dt.trous))
    check("l'arret anticipe est dit",
          any("interruption" in t for t in dt.trous))
    check("les ticks sautes sont dits",
          any("47" in t for t in dt.trous))

    sans_pied = [l for l in trace_trouee() if l.get("type") != "session.end"]
    dsp = sd.digerer(sans_pied)
    check("une trace sans pied est signalee comme coupee",
          any("coupee" in t for t in dsp.trous), str(dsp.trous))

    tmp = Path(tempfile.mkdtemp(prefix="smoke-digest-"))
    f = tmp / "abimee.jsonl"
    f.write_text("\n".join(json.dumps(l) for l in trace_trouee())
                 + '\n{"type": "session.end", "t_s": 1', encoding="utf-8")
    d2 = sd.digerer_fichier(f)
    check("une derniere ligne tronquee ne fait pas echouer la lecture",
          d2.lignes_lues >= 3, f"{d2.lignes_lues} lignes lues")
    check("le digest reste utilisable", bool(sd.faits_depuis_digest(d2)))

    vide = tmp / "vide.jsonl"
    vide.write_text("", encoding="utf-8")
    dv = sd.digerer_fichier(vide)
    check("une trace vide ne plante pas et se declare coupee",
          dv.lignes_lues == 0 and any("coupee" in t for t in dv.trous))

    # --- Perimetre ----------------------------------------------------------- #
    print()
    print("Perimetre : Vision reste independant du modele")

    arbre = ast.parse(inspect.getsource(sd))
    imports = {a.name.split(".")[0] for n in ast.walk(arbre)
               if isinstance(n, ast.Import) for a in n.names}
    imports |= {(n.module or "").split(".")[0] for n in ast.walk(arbre)
                if isinstance(n, ast.ImportFrom)}
    check("le digest n'appelle aucun reseau",
          not (imports & {"urllib", "requests", "http", "socket"}),
          str(sorted(imports)))
    check("le digest n'importe pas le duo", "llm_stream" not in imports,
          "digerer une trace ne doit pas dependre d'un modele")

    session = (ROOT / "scripts" / "session_screen.py").read_text(encoding="utf-8")
    check("session_screen n'appelle ni le digest ni le duo",
          "session_digest" not in session and "llm_stream" not in session,
          "une session doit tourner Ollama eteint")
    check("session_screen ne parle pas d'Ollama du tout",
          "ollama" not in session.lower())

    from llm_stream import correction as mod_corr
    src_corr = inspect.getsource(mod_corr)
    check("l'invariant de soustraction de R-S16 est intact",
          "Le correcteur ne peut que retirer" in src_corr
          and len(mod_corr.CATALOGUE) == 3,
          f"catalogue de {len(mod_corr.CATALOGUE)} phrases")

    pont = (ROOT / "scripts" / "controle_session.py").read_text(encoding="utf-8")
    check("le pont ecrit dans un fichier SEPARE",
          "data" in pont and "journaux" in pont
          and "vision_events" not in pont,
          "une trace est un constat ; l'interpretation se range ailleurs")
    check("data/journaux est gitignore",
          "data/journaux/" in (ROOT / ".gitignore").read_text(encoding="utf-8"))
    check("le pont ne telecharge rien", "pull" not in pont.lower())

    shutil.rmtree(tmp, ignore_errors=True)

    # --- Trace reelle, en supplement ----------------------------------------- #
    print()
    print("Trace reelle du poste, si elle existe")
    reelles = sorted((ROOT / "data" / "sessions").glob("session-*.jsonl")) \
        if (ROOT / "data" / "sessions").exists() else []
    if not reelles:
        saute("digest d'une trace reelle", "aucune trace dans data/sessions")
    else:
        pire = max(reelles, key=lambda p: p.stat().st_size)
        d = sd.digerer_fichier(pire)
        t = d.texte_pour_modele()
        o = pire.stat().st_size
        check("la plus grosse trace locale tient dans le contexte apres digest",
              len(t) // 4 < 8192,
              f"{o // 4} jetons -> {len(t) // 4} jetons ({o / max(1, len(t)):.1f}x)")
        check("et elle donne des faits exploitables",
              len(sd.faits_depuis_digest(d)) > 5)

    print()
    print("=" * 74)
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
