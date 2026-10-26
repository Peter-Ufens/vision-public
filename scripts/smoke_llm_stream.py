"""Smoke du duo streaming LLM + agent de correction (R-S16).

Usage :
  python scripts/smoke_llm_stream.py

Sortie : exit 0 si tous les controles passent, 1 sinon. **Aucun serveur
Ollama n'est requis** : le transport est injecte, donc tout ce qui compte se
teste sans GPU et sans reseau.

CE QUE CE SMOKE SURVEILLE VRAIMENT

Le correcteur est un garde-fou. Un garde-fou qui se degrade en silence est
pire que pas de garde-fou du tout. Quatre choses doivent rester vraies :

  1. **le correcteur n'ajoute jamais de contenu** -- tout mot du resultat
     vient du brouillon, des faits, ou d'un catalogue fixe de trois
     phrases d'abstention ;
  2. une cause declaree indeterminee n'est **jamais** promue en certitude
     (la faute mesuree en R-S15, quatre modeles sur cinq) ;
  3. un relecteur LLM qui invente est **rejete**, pas obei ;
  4. le composant ne connait pas Vision, et Vision ne l'appelle pas.

Le flux, lui, doit se couper a la demande, ne perdre aucun jeton quand le
lecteur traine, et echouer proprement quand le serveur est absent.
"""

from __future__ import annotations

import ast
import inspect
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterator

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


def transport(morceaux: tuple[str, ...], *, lent: float = 0.0,
              muet: bool = False, done: str = "stop"):
    """Un faux Ollama. Rend le smoke deterministe et sans GPU."""
    def t(corps: dict[str, Any], arret: threading.Event) -> Iterator[dict[str, Any]]:
        if muet:
            for _ in range(200):
                if arret.is_set():
                    return
                time.sleep(0.05)
            return
        for m in morceaux:
            if arret.is_set():
                return
            if lent:
                time.sleep(lent)
            yield {"message": {"content": m}}
        yield {"done": True, "done_reason": done, "eval_count": len(morceaux)}
    return t


FAITS_BRUTS = [
    {"cle": "exe", "valeur": "cmd.exe"},
    {"cle": "classe", "valeur": "ConsoleWindowClass"},
    {"cle": "t_apparue", "valeur": 41.0},
    {"cle": "t_disparue", "valeur": 43.0},
    {"cle": "mots", "valeur": 3},
    {"cle": "lisible", "valeur": False, "marqueurs": ("lisible",)},
    {"cle": "cause_zone_noire", "valeur": None, "indetermine": True,
     "marqueurs": ("drm", "zone noire", "protege", "flux protege")},
]


def main() -> int:  # noqa: C901
    from llm_stream import (Faits, OllamaIndisponible, StreamBloque, StreamLLM,
                            corriger, journal_duo)
    from llm_stream import client as mod_client
    from llm_stream import correction as mod_corr
    from llm_stream.correction import CATALOGUE, phrases, respecte_invariant

    print("=" * 74)
    print("Smoke duo streaming LLM + correction (R-S16)")
    print("=" * 74)

    faits = Faits.depuis(FAITS_BRUTS)

    # --- Decoupage : le piege des points ------------------------------------ #
    print()
    print("Decoupage en phrases")
    check("un point decimal ne coupe pas une phrase",
          len(phrases("La valeur est 41.0 s.")) == 1,
          str(phrases("La valeur est 41.0 s.")))
    check("un nom de fichier ne coupe pas une phrase",
          len(phrases("Le processus cmd.exe tourne.")) == 1,
          "sinon 'chrome.exe' invente passerait pour deux fragments anodins")
    check("deux vraies phrases sont bien separees",
          len(phrases("Une. Deux.")) == 2)

    # --- Correction deterministe -------------------------------------------- #
    print()
    print("Correction : ce que l'agent doit attraper")

    r = corriger("Une fenetre cmd.exe est apparue a 41.0 s et repartie a 43.0 s. "
                 "Elle a vecu 2 s.", faits)
    check("un brouillon fidele ne declenche aucune anomalie", r.propre,
          str([a.genre for a in r.anomalies]))
    check("un ecart entre deux faits est accepte (43 - 41 = 2)",
          "2 s" in r.texte, "sinon toute soustraction evidente serait une alerte")

    r = corriger("Une fenetre cmd.exe est apparue a 41.0 s. Puis elle a dispar", faits)
    check("une phrase coupee est detectee",
          any(a.genre == "phrase_tronquee" for a in r.anomalies))
    check("la phrase coupee est retiree, jamais completee",
          "dispar" not in r.texte and CATALOGUE["coupe"] in r.texte, r.texte)

    r = corriger("Une fenetre cmd.exe est apparue. Un processus chrome.exe a demarre.",
                 faits)
    check("un executable absent des faits est detecte",
          any(a.genre == "fait_absent" for a in r.anomalies))
    check("la phrase inventee est retiree", "chrome.exe" not in r.texte, r.texte)
    check("la phrase fidele est conservee", "cmd.exe" in r.texte)

    r = corriger("La zone est noire. C'est du DRM, donc le flux est protege.", faits)
    check("une cause declaree indeterminee n'est pas promue en certitude",
          any(a.genre == "cause_promue" for a in r.anomalies),
          "faute mesuree en R-S15 : 4 modeles sur 5")
    check("l'affirmation est remplacee par une abstention",
          CATALOGUE["cause_indeterminee"] in r.texte and "DRM" not in r.texte, r.texte)

    # La regle a ete INVERSEE en R-S17 : nommer une cause indeterminee est
    # signale par defaut, c'est a la phrase de montrer sa prudence. Le risque
    # bascule donc de la sous-detection vers la sur-detection, et ces deux
    # temoins sont ce qui l'empeche de passer inapercue.
    r = corriger("La zone est noire, mais la cause ne peut pas etre etablie : "
                 "DRM, ecran eteint ou scene sombre.", faits)
    check("une phrase PRUDENTE qui nomme la cause passe",
          not any(a.genre == "cause_promue" for a in r.anomalies),
          "sinon le correcteur retirerait l'abstention elle-meme")
    r = corriger("La zone noire vient du DRM de Prime Video.", faits)
    check("une affirmation sans verbe d'affirmation est quand meme attrapee",
          any(a.genre == "cause_promue" for a in r.anomalies),
          "'vient du' : la liste de verbes de R-S16 laissait passer celle-la")

    r = corriger("Le contenu est lisible.", faits)
    check("une contradiction avec un fait faux est detectee",
          any(a.genre == "contradiction" for a in r.anomalies),
          "les faits disent lisible=False")
    r = corriger("Le contenu n'est pas lisible.", faits)
    check("une phrase qui NIE correctement n'est pas une contradiction",
          not any(a.genre == "contradiction" for a in r.anomalies),
          "temoin : sans lui, le detecteur pourrait tout refuser")

    r = corriger("La fenetre est est est est apparue a 41.0 s.", faits)
    check("une boucle de jetons est detectee et repliee",
          any(a.genre == "repetition" for a in r.anomalies)
          and r.texte.count("est est") == 0, r.texte)

    r = corriger("La fenetre a vecu 987 s.", faits)
    check("un chiffre sans source est signale",
          any(a.genre == "chiffre_non_source" for a in r.anomalies))

    check("mais il n'est PAS retire d'office",
          "987" in r.texte, "un calcul legitime ne doit pas disparaitre en silence")

    # R-S18 : la trace disait 1535.297, le modele a ecrit "1535", et le
    # correcteur a crie a la donnee non sourcee. Le fait etait exact. Un faux
    # positif use la confiance autant qu'un rate, donc l'arrondi est accepte
    # a la precision que le texte choisit, et pas plus loin.
    faits_precis = Faits.depuis([{"cle": "t_disparue", "valeur": 1535.297}])
    r = corriger("La fenetre a disparu a 1535 secondes.", faits_precis)
    check("un arrondi legitime n'est PAS signale",
          not any(a.genre == "chiffre_non_source" for a in r.anomalies),
          "1535 pour 1535,297 : la presentation, pas l'invention")
    r = corriger("La fenetre a disparu a 1540 secondes.", faits_precis)
    check("mais un nombre voisin et faux l'est toujours",
          any(a.genre == "chiffre_non_source" for a in r.anomalies),
          "sans ce temoin, la tolerance pourrait tout accepter")

    # --- L'invariant qui tient tout ----------------------------------------- #
    print()
    print("L'invariant : le correcteur ne peut que retirer")
    brouillons = [
        "Une fenetre cmd.exe est apparue a 41.0 s. Puis elle a dispar",
        "Un processus chrome.exe a demarre. C'est du DRM.",
        "Le contenu est lisible et clair.",
        "aaa aaa aaa aaa aaa",
        "",
    ]
    tous = True
    for b in brouillons:
        res = corriger(b, faits)
        if not respecte_invariant(res.texte, b, faits):
            tous = False
            print(f"      brouillon fautif : {b!r} -> {res.texte!r}")
    check("aucun mot du resultat ne sort du brouillon, des faits ou du catalogue",
          tous, "c'est la garantie anti-invention du composant")
    check("le catalogue reste minuscule et ferme", len(CATALOGUE) == 3,
          f"{len(CATALOGUE)} phrases : {sorted(CATALOGUE)}")

    # --- Relecteur LLM optionnel -------------------------------------------- #
    print()
    print("Relecteur optionnel : il obeit a la meme regle")

    def relecteur_menteur(texte: str, f: Faits) -> str:
        return "Une fenetre firefox.exe a plante pendant 12 minutes."

    r = corriger("Une fenetre cmd.exe est apparue.", faits, relecteur=relecteur_menteur)
    check("un relecteur qui invente est REJETE", r.relecteur_rejete,
          "sinon un second modele reintroduirait ce que la regle a retire")
    check("le texte deterministe est conserve tel quel",
          "firefox.exe" not in r.texte and "cmd.exe" in r.texte, r.texte)

    def relecteur_sage(texte: str, f: Faits) -> str:
        return texte

    r = corriger("Une fenetre cmd.exe est apparue.", faits, relecteur=relecteur_sage)
    check("un relecteur qui respecte les sources est accepte",
          not r.relecteur_rejete and "cmd.exe" in r.texte)

    def relecteur_casse(texte: str, f: Faits) -> str:
        raise RuntimeError("modele indisponible")

    r = corriger("Une fenetre cmd.exe est apparue.", faits, relecteur=relecteur_casse)
    check("un relecteur en panne ne casse pas la correction",
          "cmd.exe" in r.texte
          and any(a.genre == "relecteur_en_panne" for a in r.anomalies))

    # --- Le flux ------------------------------------------------------------- #
    print()
    print("Le flux : couper, ne rien perdre, echouer proprement")

    cli = StreamLLM(transport=transport(("Une ", "fenetre ", "cmd.exe ", "est apparue.")))
    flux = cli.demarrer("t")
    lus = [m.texte for m in flux.morceaux()]
    check("les morceaux arrivent dans l'ordre", lus == ["Une ", "fenetre ", "cmd.exe ",
                                                        "est apparue."])
    check("le texte complet se reconstitue",
          flux.texte() == "Une fenetre cmd.exe est apparue.")
    m = flux.mesures.resume()
    check("le delai avant le premier mot est mesure", m["ttft_ms"] is not None)
    check("la raison de fin est rapportee", m["done_reason"] == "stop")

    cli = StreamLLM(transport=transport(tuple(f"m{i} " for i in range(40)), lent=0.05))
    flux = cli.demarrer("t")
    n = 0
    for _ in flux.morceaux():
        n += 1
        if n == 3:
            t0 = time.perf_counter()
            flux.stop()
            break
    fini = flux.attendre(3.0)
    check("stop() coupe la generation au milieu d'une phrase", flux.arrete and n == 3)
    check("le fil producteur se termine tout de suite",
          fini and (time.perf_counter() - t0) < 1.5,
          f"{(time.perf_counter() - t0) * 1000:.0f} ms apres stop()")
    check("le texte partiel reste lisible", flux.texte().startswith("m0 "))

    cli = StreamLLM(transport=transport(tuple(f"m{i} " for i in range(200))))
    flux = cli.demarrer("t", file_max=8)
    recus = 0
    for _ in flux.morceaux():
        recus += 1
        if recus <= 5:
            time.sleep(0.02)
    check("une file bornee ne perd AUCUN morceau quand le lecteur traine",
          recus == 200, f"{recus}/200 -- un mot perdu serait indetectable en aval")
    check("l'attente du producteur est mesuree, pas silencieuse",
          flux.mesures.resume()["attente_file_ms"] >= 0.0)

    cli = StreamLLM(transport=transport((), muet=True))
    flux = cli.demarrer("t", silence_max_s=0.6)
    t0 = time.perf_counter()
    bloque = False
    try:
        for _ in flux.morceaux():
            pass
    except StreamBloque:
        bloque = True
    check("un flux muet declenche le chien de garde", bloque,
          f"{(time.perf_counter() - t0):.1f} s")
    check("le chien de garde arrete aussi le producteur", flux.attendre(2.0))

    cli = StreamLLM(port=1)
    leve = False
    try:
        cli.texte_complet("t")
    except OllamaIndisponible:
        leve = True
    check("serveur absent : exception claire, pas de crash silencieux", leve)
    check("disponible() le dit sans lever", cli.disponible()[0] is False)

    # --- Le duo -------------------------------------------------------------- #
    print()
    print("Le duo : brouillon ET corrige")
    cli = StreamLLM(transport=transport(("Une fenetre cmd.exe est apparue. ",
                                         "Un processus chrome.exe a demarre.")))
    vus: list[str] = []
    j = journal_duo("t", faits, client=cli, sur_morceau=vus.append)
    check("le temps reel a bien recu les morceaux", len(vus) == 2)
    check("le journal garde le brouillon d'origine", "chrome.exe" in j["brouillon"],
          "sans lui on perdrait ce que le modele avait vraiment ecrit")
    check("le journal porte le texte corrige", "chrome.exe" not in j["corrige"])
    check("le journal compte les anomalies", j["controle"]["graves"] >= 1)

    cli = StreamLLM(port=1)
    j = journal_duo("t", faits, client=cli)
    check("Ollama absent : le duo rend un journal, il ne plante pas",
          j["erreur"] is not None and j["corrige"] == "", str(j["erreur"])[:60])

    # --- Perimetre ----------------------------------------------------------- #
    print()
    print("Perimetre : un composant, pas un morceau de Vision")

    src_paquet = (inspect.getsource(mod_client) + inspect.getsource(mod_corr))
    from llm_stream import duo as mod_duo
    src_paquet += inspect.getsource(mod_duo)

    imports: set[str] = set()
    for mod in (mod_client, mod_corr, mod_duo):
        arbre = ast.parse(inspect.getsource(mod))
        imports |= {a.name.split(".")[0] for n in ast.walk(arbre)
                    if isinstance(n, ast.Import) for a in n.names}
        imports |= {(n.module or "").split(".")[0] for n in ast.walk(arbre)
                    if isinstance(n, ast.ImportFrom)}

    interdits = {"session_contenu", "session_decor", "session_screen", "screen_api",
                 "screen_content", "screen_context", "screen_rich", "screen_uia",
                 "ocr_screen", "cv2", "mvp_webcam", "face_bank"}
    check("le coeur n'importe RIEN de Vision", not (imports & interdits),
          str(sorted(imports)))
    check("aucun code d'un agent tiers", "agent_tiers" not in src_paquet.lower())

    # Le telechargement se verifie sur les APPELS, pas sur le texte : la
    # premiere version de ce controle cherchait le mot "pull" et echouait
    # sur la phrase du docstring qui PROMET de ne pas telecharger.
    lance_processus = imports & {"subprocess", "os", "shutil", "pty"}
    chemins = set()
    for mod in (mod_client, mod_corr, mod_duo):
        for n in ast.walk(ast.parse(inspect.getsource(mod))):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if n.value.startswith("/api/"):
                    chemins.add(n.value)
    check("le paquet ne lance aucun processus externe", not lance_processus,
          str(sorted(lance_processus)))
    check("aucun appel a /api/pull : le composant ne telecharge rien",
          "/api/pull" not in chemins, str(sorted(chemins)))
    check("il ne parle qu'aux points d'entree attendus",
          chemins <= {"/api/chat", "/api/version"}, str(sorted(chemins)))
    check("l'hote reste 127.0.0.1, non configurable par chaine libre",
          'HOTE = "127.0.0.1"' in inspect.getsource(mod_client)
          and "host=" not in inspect.getsource(mod_client))
    check("le cablage R-S15 est le defaut : think coupe",
          mod_client.StreamLLM().think is False,
          "sans lui le budget part dans le raisonnement et content reste vide")
    check("le system par defaut n'est pas une persona",
          "compagne" not in mod_client.SYSTEM_MINIMAL.lower()
          and "persona" not in mod_client.SYSTEM_MINIMAL.lower(),
          "persona mesuree a 0/25 en R-S15")
    check("le modele par defaut est celui recommande en R-S15",
          mod_client.MODELE_DEFAUT == "qwen3.5:9b-gpu", mod_client.MODELE_DEFAUT)

    session = (ROOT / "scripts" / "session_screen.py").read_text(encoding="utf-8")
    check("session_screen n'appelle PAS ce composant",
          "llm_stream" not in session,
          "R-S16 est un composant ; le cablage est reserve a R-S17")
    check("une session Vision tourne donc sans Ollama", "ollama" not in session.lower())

    # --- Mesure reelle, si le serveur est la --------------------------------- #
    print()
    print("Mesure sur serveur reel")
    vrai = StreamLLM()
    up, version = vrai.disponible()
    if not up:
        saute("mesure TTFT reelle", f"Ollama injoignable ({version})")
    else:
        flux = vrai.demarrer("Ecris une phrase courte sur une fenetre cmd.exe.")
        for _ in flux.morceaux():
            pass
        r = flux.mesures.resume()
        check("un flux reel rend du texte", r["caracteres"] > 0, f"{r['caracteres']} car")
        check("le delai avant le premier mot est mesure sur du reel",
              r["ttft_ms"] is not None, f"TTFT {r['ttft_ms']} ms, total {r['total_ms']} ms")

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
