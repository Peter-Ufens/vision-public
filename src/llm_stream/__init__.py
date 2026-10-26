"""Duo streaming LLM + agent de correction (R-S16).

UN COMPOSANT, PAS UN MORCEAU DE VISION
---------------------------------------

Ce paquet n'importe RIEN de Vision. Les faits entrent par une interface
(une liste de dictionnaires), jamais par un chemin `session_*`. C'est ce qui
permettra de le reprendre tel quel dans une application, et plus tard dans une
couche de fusion multimodale, sans en detacher un fil.

Le smoke verifie cette independance par lecture de l'arbre syntaxique : un
import de `session_contenu` ou de `screen_*` fait echouer le controle.

LES DEUX MOITIES
----------------

  client.py      parler a Ollama en flux, sans bloquer l'appelant, en
                 pouvant s'arreter au milieu d'une phrase
  correction.py  relire ce flux contre des FAITS et refuser ce qui n'y est
                 pas. Deterministe d'abord.
  duo.py         faits -> flux -> correction -> journal final

LA REGLE QUI TIENT TOUT
------------------------

**Le correcteur ne peut que retirer ou neutraliser. Il n'ajoute jamais.**

Un correcteur qui reecrit librement est un second generateur, donc une
seconde source d'invention. Celui-ci travaille par soustraction : il coupe
une phrase tronquee, il retire un fait absent des sources, il ramene une
cause affirmee a la formulation prudente des faits. Le smoke verifie
l'invariant mot a mot.

Usage :
    from llm_stream import StreamLLM, Faits, corriger, duo

    faits = Faits.depuis([
        {"cle": "exe", "valeur": "cmd.exe"},
        {"cle": "lisible", "valeur": True},
    ])
    flux = StreamLLM().demarrer("Resume ces faits.")
    for morceau in flux.morceaux():
        print(morceau.texte, end="", flush=True)
    resultat = corriger(flux.texte(), faits)
"""

from __future__ import annotations

from .client import (
    Flux,
    Morceau,
    OllamaIndisponible,
    StreamBloque,
    StreamLLM,
    MODELE_DEFAUT,
    SYSTEM_MINIMAL,
)
from .correction import Anomalie, Faits, Resultat, corriger
from .duo import journal_duo

__all__ = [
    "Anomalie",
    "Faits",
    "Flux",
    "MODELE_DEFAUT",
    "Morceau",
    "OllamaIndisponible",
    "Resultat",
    "SYSTEM_MINIMAL",
    "StreamBloque",
    "StreamLLM",
    "corriger",
    "journal_duo",
]
