"""Orchestrateur leger : faits -> flux -> correction -> journal (R-S16).

CE QUE CE MODULE EST, ET CE QU'IL N'EST PAS
--------------------------------------------

C'est la plus petite chose qui met les deux moities bout a bout. Ce n'est
**pas** le pont Vision : rien ici ne sait lire une session, un digest ou un
evenement. Le brief le reserve a R-S17, et le melanger ici produirait le
monolithe qu'il demande d'eviter.

POURQUOI LE JOURNAL EST ECRIT EN DEUX TEMPS
--------------------------------------------

Le flux donne du texte tout de suite, et c'est ce que l'utilisateur veut voir
defiler. Mais un texte streame n'est pas verifie : il peut se couper au
milieu d'un mot ou nommer un executable qui n'existe pas.

Le journal porte donc deux niveaux :

  `brouillon`  ce qui est arrive, horodate, tel quel
  `corrige`    ce qui a survecu a la relecture, plus la liste des anomalies

Ecraser le premier par le second serait perdre la trace de ce que le modele
avait vraiment ecrit. Meme raisonnement qu'une trace de session : on garde
le constat brut a cote de l'interpretation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from .client import Flux, OllamaIndisponible, StreamLLM
from .correction import Faits, Resultat, corriger


def journal_duo(prompt: str, faits: Faits, *, client: StreamLLM | None = None,
                sur_morceau: Callable[[str], None] | None = None,
                relecteur: Callable[[str, Faits], str] | None = None,
                sortie: Path | None = None) -> dict[str, Any]:
    """Derouler un flux puis le relire. Rend le journal complet.

    `sur_morceau` est appele a chaque fragment recu : c'est le point ou une
    interface, une voix ou un dashboard branche son temps reel. Il ne doit
    pas bloquer ; s'il bloque, la file du flux se remplit et le producteur
    attend (voir `Flux._deposer`).
    """
    cli = client or StreamLLM()
    debut = time.time()
    flux: Flux | None = None
    erreur: str | None = None
    try:
        flux = cli.demarrer(prompt)
        for morceau in flux.morceaux():
            if sur_morceau is not None:
                sur_morceau(morceau.texte)
    except OllamaIndisponible as exc:
        erreur = f"ollama_indisponible: {exc}"
    except Exception as exc:  # noqa: BLE001
        erreur = f"{type(exc).__name__}: {exc}"

    brouillon = flux.texte() if flux is not None else ""
    mesures = flux.mesures.resume() if flux is not None else {}
    resultat: Resultat = corriger(brouillon, faits, relecteur=relecteur)

    journal = {
        "t_wall": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(debut)),
        "modele": cli.modele,
        "erreur": erreur,
        "mesures": mesures,
        "brouillon": brouillon,
        "corrige": resultat.texte,
        "controle": resultat.resume(),
        "retires": resultat.retires,
    }
    if sortie is not None:
        sortie.parent.mkdir(parents=True, exist_ok=True)
        with sortie.open("a", encoding="utf-8") as f:
            f.write(json.dumps(journal, ensure_ascii=False) + "\n")
    return journal


def morceaux_corriges(prompt: str, faits: Faits, *,
                      client: StreamLLM | None = None) -> Iterator[dict[str, Any]]:
    """Variante generateur : rendre chaque fragment, puis le bilan final.

    Utile pour une interface qui veut afficher au fil de l'eau sans attendre
    la fin, tout en recevant la correction quand elle arrive. Le dernier
    element porte `final: True`.
    """
    cli = client or StreamLLM()
    flux = cli.demarrer(prompt)
    for morceau in flux.morceaux():
        yield {"final": False, "texte": morceau.texte, "t_ms": round(morceau.t_ms, 1)}
    resultat = corriger(flux.texte(), faits)
    yield {"final": True, "texte": resultat.texte, "brouillon": flux.texte(),
           "controle": resultat.resume(), "mesures": flux.mesures.resume()}
