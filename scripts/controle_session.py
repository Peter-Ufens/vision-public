"""Digerer une session, puis la faire relire par un LLM local (R-S17).

LE CHEMIN COMPLET, ET SES DEUX MOITIES INDEPENDANTES
------------------------------------------------------

  trace JSONL  ->  digest  ->  faits        (aucun reseau, toujours possible)
                                  |
                                  v
                            stream + correction  (optionnel, si Ollama tourne)

**La premiere moitie marche toujours.** Sans serveur, la commande rend le
digest et les faits, dit pourquoi elle s'arrete la, et sort avec le code 0 :
digerer une trace n'a jamais eu besoin d'un modele.

**Rien ici n'est appele par `session_screen.py`.** Une session d'observation
ne connait pas ce script et tourne exactement pareil Ollama eteint. Le
controle est une lecture d'apres coup, pas un etage du runtime.

OU VA LE JOURNAL
----------------

Dans `data/journaux/`, a COTE de la trace, jamais dedans. Melanger
"la luminance valait 0,3" et "c'etait probablement Prime Video" dans le
meme fichier reproduirait exactement ce que R-S13 a refuse de faire : une
trace est un constat d'instruments, l'interpretation est autre chose et
elle se range ailleurs.

Usage :
  python scripts/controle_session.py                       # derniere session
  python scripts/controle_session.py --session <fichier.jsonl>
  python scripts/controle_session.py --digest-seul         # sans modele
  python scripts/controle_session.py --faits               # voir les faits
  python scripts/controle_session.py --avec-texte          # DONNEES PERSONNELLES
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

from llm_stream import Faits, OllamaIndisponible, StreamLLM, corriger  # noqa: E402
from session_digest import (digerer_fichier, fabriquer_prompt,  # noqa: E402
                            faits_depuis_digest)

SESSIONS = ROOT / "data" / "sessions"
JOURNAUX = ROOT / "data" / "journaux"


def derniere_session() -> Path | None:
    if not SESSIONS.exists():
        return None
    fichiers = sorted(SESSIONS.glob("session-*.jsonl"),
                      key=lambda p: p.stat().st_mtime)
    return fichiers[-1] if fichiers else None


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    p = argparse.ArgumentParser(
        description="Digerer une session d'observation, et la faire relire (R-S17)",
        epilog="Sans Ollama, le digest et les faits sortent quand meme.")
    p.add_argument("--session", type=Path, default=None,
                   help="trace JSONL (defaut : la plus recente de data/sessions)")
    p.add_argument("--digest-seul", action="store_true",
                   help="ne pas appeler de modele du tout")
    p.add_argument("--faits", action="store_true", help="afficher les faits extraits")
    p.add_argument("--avec-texte", action="store_true",
                   help="inclure le TEXTE LU A L'ECRAN dans ce qui part au modele. "
                        "Donnees personnelles : a n'utiliser qu'en connaissance de cause.")
    p.add_argument("--modele", default=None)
    p.add_argument("--journal", type=Path, default=None,
                   help="fichier de sortie (defaut : data/journaux/<session>-controle.jsonl)")
    p.add_argument("--sans-journal", action="store_true")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    chemin = args.session or derniere_session()
    if chemin is None or not Path(chemin).exists():
        print("Aucune trace de session trouvee.")
        print(f"Attendu dans {SESSIONS}, ou passez --session <fichier.jsonl>.")
        return 1

    # --- Moitie 1 : toujours possible ---------------------------------------- #
    dig = digerer_fichier(chemin, avec_texte=args.avec_texte)
    faits_bruts = faits_depuis_digest(dig)
    texte_digest = dig.texte_pour_modele()
    brut = Path(chemin).stat().st_size

    print(f"Session  : {dig.session_id or Path(chemin).name}")
    print(f"Trace    : {brut} o, environ {brut // 4} jetons")
    print(f"Digest   : {len(texte_digest)} o, environ {len(texte_digest) // 4} jetons "
          f"({brut / max(1, len(texte_digest)):.1f} fois plus petit)")
    print(f"           {dig.images_cles_jetees} image(s)-cle jetee(s), "
          f"{len(dig.evenements)} evenement(s), {len(dig.contenus)} capture(s), "
          f"{len(dig.decors)} echantillon(s) de decor")
    print(f"Faits    : {len(faits_bruts)}")
    if args.avec_texte:
        print("ATTENTION : --avec-texte inclut le texte lu a l'ecran.")
    if dig.trous:
        print("Trous declares par la session :")
        for t in dig.trous:
            print(f"  - {t}")

    if args.faits:
        print()
        print("Faits extraits :")
        for f in faits_bruts:
            marque = ""
            if f.get("indetermine"):
                marque = "   <- INDETERMINE, aucune cause ne sera acceptee"
            print(f"  {f['cle']:38s} = "
                  f"{json.dumps(f.get('valeur'), ensure_ascii=False)}{marque}")

    if args.digest_seul:
        print()
        print("Mode digest seul : aucun modele n'a ete appele.")
        if args.json:
            print(json.dumps({"digest": dig.to_dict(), "faits": faits_bruts},
                             ensure_ascii=False, indent=2))
        return 0

    # --- Moitie 2 : optionnelle ---------------------------------------------- #
    cli = StreamLLM(**({"modele": args.modele} if args.modele else {}))
    up, version = cli.disponible()
    if not up:
        print()
        print(f"Ollama injoignable sur 127.0.0.1 ({version}).")
        print("Le digest et les faits ci-dessus restent valables : les produire "
              "n'a jamais demande de modele.")
        print("Aucun telechargement n'est tente.")
        return 0

    faits = Faits.depuis(faits_bruts)
    prompt = fabriquer_prompt(dig)
    print()
    print(f"Ollama {version}, modele {cli.modele}")
    print()
    print("Journal (temps reel) :")
    print("  ", end="", flush=True)

    debut = time.time()
    try:
        flux = cli.demarrer(prompt)
        for morceau in flux.morceaux():
            print(morceau.texte, end="", flush=True)
    except OllamaIndisponible as exc:
        print()
        print(f"Echec propre : {exc}")
        return 1

    brouillon = flux.texte()
    resultat = corriger(brouillon, faits)
    mesures = flux.mesures.resume()

    print()
    print()
    print(f"Mesures  : premier mot en {mesures.get('ttft_ms')} ms, "
          f"total {mesures.get('total_ms')} ms, fin '{mesures.get('done_reason')}'")
    print()
    print("Apres relecture :")
    print(f"  {resultat.texte}")
    if resultat.anomalies:
        print()
        print(f"Anomalies ({sum(1 for a in resultat.anomalies if a.grave)} grave(s)) :")
        for a in resultat.anomalies:
            print(f"  - {a}")
    else:
        print()
        print("Aucune anomalie : le journal tenait deja dans les faits.")
    print()
    print(f"Invariant respecte : {resultat.invariant_respecte}")

    journal = {
        "v": 1,
        "t_wall": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(debut)),
        "session_id": dig.session_id,
        "trace": Path(chemin).name,
        "modele": cli.modele,
        "jetons_digest_estimes": len(texte_digest) // 4,
        "avec_texte": bool(args.avec_texte),
        "mesures": mesures,
        "brouillon": brouillon,
        "corrige": resultat.texte,
        "controle": resultat.resume(),
        "retires": resultat.retires,
        "trous": dig.trous,
    }
    if not args.sans_journal:
        cible = args.journal or (JOURNAUX / f"{dig.session_id or 'session'}-controle.jsonl")
        cible.parent.mkdir(parents=True, exist_ok=True)
        with cible.open("a", encoding="utf-8") as f:
            f.write(json.dumps(journal, ensure_ascii=False) + "\n")
        print(f"Journal  : {cible}")
        print("           (fichier separe : la trace de session n'est pas modifiee)")
    if args.json:
        print()
        print(json.dumps(journal, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
