"""Agent de correction : relire un flux contre des faits (R-S16).

LE PROBLEME
-----------

Un texte streame par un LLM arrive abime de trois facons differentes, et
elles ne se soignent pas pareil :

  1. il est **coupe** au milieu d'une phrase (budget epuise, arret demande,
     connexion fermee) ;
  2. il contient des **faits absents** des sources : un nom d'executable
     jamais vu, un chiffre qui ne vient de nulle part ;
  3. il **affirme une cause** la ou les sources disent explicitement qu'on
     ne peut pas trancher. C'est la faute mesuree en R-S15 : quatre modeles
     sur cinq recopiaient la phrase "indiscernables sur ce seul signal"
     dans un champ qui demandait une cause certaine.

LA REGLE QUI REND CE MODULE SUR
--------------------------------

**Le correcteur ne peut que retirer ou neutraliser. Il n'ajoute jamais.**

Un correcteur libre serait un second generateur, donc une seconde source
d'invention, et personne ne verifierait le verificateur. Celui-ci travaille
par soustraction. La seule chose qu'il ait le droit d'ecrire est une phrase
prise dans un catalogue FIXE et minuscule, ci-dessous, dont chaque entree
dit une abstention.

L'invariant se verifie mot a mot : tout mot du texte corrige vient du
brouillon, des faits, ou du catalogue. Le smoke le controle.

DETERMINISTE D'ABORD
--------------------

Aucun modele n'est appele par defaut. Un relecteur LLM peut etre branche
(`relecteur=`), mais sa sortie **repasse** par la couche deterministe, et
elle est **rejetee** si elle viole l'invariant. Un modele ne peut donc pas
faire rentrer par la fenetre ce que la regle a sorti par la porte.

CE MODULE NE CONNAIT PAS VISION
--------------------------------

Les faits entrent sous forme de dictionnaires, avec leurs propres marqueurs
lexicaux. Rien ici ne sait ce qu'est une fenetre, un OCR ou un moniteur.
C'est ce qui permettra de le reprendre dans une application et dans une
couche de fusion multimodale sans en detacher un fil.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

# Les SEULES phrases que le correcteur a le droit d'ecrire. Catalogue fixe,
# ferme, et verifie par le smoke. Chacune dit une abstention.
CATALOGUE = {
    "cause_indeterminee": "La cause n'est pas determinee par les faits.",
    "coupe": "(fin de phrase coupee)",
    "retire": "(passage retire : absent des faits)",
}

FIN_DE_PHRASE = ".!?…"
PAIRES = {"(": ")", "[": "]", "{": "}"}

# Marqueurs de PRUDENCE. Une phrase qui nomme une cause declaree
# indeterminee n'est acceptable que si elle dit aussi qu'on ne peut pas
# trancher, ou qu'elle avance une hypothese sans l'affirmer.
#
# POURQUOI CETTE LOGIQUE PLUTOT QU'UNE LISTE DE VERBES D'AFFIRMATION.
# La premiere version listait les tournures affirmatives ("c'est",
# "a cause de", "parce que"...) et laissait passer tout le reste. R-S17 l'a
# prise en defaut sur une phrase parfaitement banale : "La zone noire vient
# du DRM de Prime Video." Aucun verbe de la liste, aucune alerte.
#
# Une liste de tournures fautives ne peut jamais etre complete, alors qu'une
# liste de tournures prudentes se ferme : on inverse donc la charge de la
# preuve. Nommer une cause indeterminee est signale PAR DEFAUT ; c'est a la
# phrase de montrer sa prudence.
PRUDENCES = (
    "ne peut pas", "ne peuvent pas", "ne permet pas", "ne permettent pas",
    "indetermine", "indeterminee", "impossible", "aucune cause", "pas de cause",
    "n'est pas etablie", "n est pas etablie", "sans certitude", "inconnu",
    "inconnue", "peut-etre", "peut etre", "pourrait", "pourraient", "possible",
    "hypothese", "on ignore", "rien ne dit", "indiscernable", "indiscernables",
)

NEGATIONS = ("non", "pas", "aucun", "aucune", "jamais", "ni", "sans",
             "in", "il", "im", "ir")

_MOT = re.compile(r"[0-9A-Za-zÀ-ÿ_.\-]+", re.UNICODE)
_NOMBRE = re.compile(r"\d+(?:[.,]\d+)?")
_IDENTIFIANT = re.compile(r"\b[\w\-]+\.(?:exe|dll|py|jsonl?|md|png|txt)\b", re.I)


def _sans_accent(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _mots(texte: str) -> list[str]:
    return [_sans_accent(m) for m in _MOT.findall(texte or "")]


@dataclass
class Fait:
    """Un fait source. `marqueurs` dit par quels mots il se reconnait.

    `indetermine` marque un fait dont la valeur existe mais dont la CAUSE
    ne peut pas etre etablie. C'est le cas de la zone noire de R-S13 : la
    luminance est un fait, l'explication n'en est pas un.
    """

    cle: str
    valeur: Any = None
    indetermine: bool = False
    marqueurs: tuple[str, ...] = ()

    def mots_marqueurs(self) -> tuple[str, ...]:
        return self.marqueurs or (self.cle,)


@dataclass
class Anomalie:
    genre: str
    detail: str
    extrait: str = ""
    grave: bool = True

    def __str__(self) -> str:
        return f"[{self.genre}] {self.detail}" + (f" : {self.extrait!r}" if self.extrait else "")


@dataclass
class Resultat:
    texte: str
    anomalies: list[Anomalie] = field(default_factory=list)
    retires: list[str] = field(default_factory=list)
    invariant_respecte: bool = True
    relecteur_rejete: bool = False

    @property
    def propre(self) -> bool:
        return not self.anomalies

    def resume(self) -> dict[str, Any]:
        return {
            "anomalies": [str(a) for a in self.anomalies],
            "graves": sum(1 for a in self.anomalies if a.grave),
            "phrases_retirees": len(self.retires),
            "invariant_respecte": self.invariant_respecte,
            "relecteur_rejete": self.relecteur_rejete,
            "caracteres": len(self.texte),
        }


class Faits:
    """Les sources. Rien n'est vrai en dehors d'elles."""

    def __init__(self, faits: Sequence[Fait]) -> None:
        self.faits = list(faits)

    @classmethod
    def depuis(cls, items: Iterable[dict[str, Any]]) -> "Faits":
        out: list[Fait] = []
        for it in items:
            out.append(Fait(
                cle=str(it.get("cle", "")),
                valeur=it.get("valeur"),
                indetermine=bool(it.get("indetermine")),
                marqueurs=tuple(str(m) for m in (it.get("marqueurs") or ())),
            ))
        return cls(out)

    # -- vocabulaire autorise ---------------------------------------------- #

    def mots(self) -> set[str]:
        out: set[str] = set()
        for f in self.faits:
            out.update(_mots(f.cle))
            out.update(_mots(str(f.valeur)))
            for m in f.marqueurs:
                out.update(_mots(m))
        return out

    def identifiants(self) -> set[str]:
        out: set[str] = set()
        for f in self.faits:
            for source in (str(f.valeur), f.cle):
                out.update(m.lower() for m in _IDENTIFIANT.findall(source))
        return out

    def nombres(self) -> set[str]:
        """Les nombres des faits, plus leurs ecarts deux a deux.

        Un ecart est un calcul legitime : une fenetre vue apparaitre a 41 s
        et disparaitre a 43 s a bien vecu 2 s, et ce 2 n'est ecrit nulle
        part. Refuser ce cas produirait une fausse alerte a chaque fois
        qu'un resume fait une soustraction evidente.
        """
        bruts: set[str] = set()
        for f in self.faits:
            bruts.update(_NOMBRE.findall(str(f.valeur)))
            bruts.update(_NOMBRE.findall(f.cle))
        out = set(bruts)
        valeurs = []
        for b in list(bruts)[:60]:  # borne : le croisement est quadratique
            try:
                valeurs.append(float(b.replace(",", ".")))
            except ValueError:
                continue
        for i, a in enumerate(valeurs):
            for b in valeurs[i + 1:]:
                d = abs(a - b)
                out.add(_format_nombre(d))
                out.add(_format_nombre(a + b))
        return out

    def indetermines(self) -> list[Fait]:
        return [f for f in self.faits if f.indetermine]

    def booleens_faux(self) -> list[Fait]:
        return [f for f in self.faits if f.valeur is False]


def _format_nombre(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.10g}"


# --------------------------------------------------------------------------- #
# Decoupage
# --------------------------------------------------------------------------- #

# Une fin de phrase est une ponctuation SUIVIE d'un blanc ou de la fin du
# texte. Ce detail n'en est pas un : couper a chaque point casserait
# `41.0` en `41.` + `0`, et `cmd.exe` en `cmd.` + `exe`, ce qui faisait
# passer un executable invente pour deux fragments anodins.
_FIN_PHRASE = re.compile(r"(?<=[.!?…])(?=\s|$)")


def phrases(texte: str) -> list[str]:
    """Decouper en phrases en gardant la ponctuation et les espaces."""
    if not texte:
        return []
    bouts = _FIN_PHRASE.split(texte)
    return [b for b in bouts if b]


def est_tronque(texte: str) -> bool:
    """Le texte se termine-t-il au milieu de quelque chose ?"""
    t = (texte or "").rstrip()
    if not t:
        return False
    if t[-1] not in FIN_DE_PHRASE and t[-1] not in ")]}\"'":
        return True
    for ouvrant, fermant in PAIRES.items():
        if t.count(ouvrant) != t.count(fermant):
            return True
    if t.count('"') % 2 or t.count("`") % 2:
        return True
    return False


def repetition_en_boucle(texte: str, seuil: int = 4) -> str | None:
    """Le meme mot repete d'affilee : panne de flux classique."""
    mots = _mots(texte)
    compte, precedent = 1, None
    for m in mots:
        if m == precedent:
            compte += 1
            if compte >= seuil:
                return m
        else:
            compte, precedent = 1, m
    return None


# --------------------------------------------------------------------------- #
# Detections
# --------------------------------------------------------------------------- #

def _contient_negation_avant(phrase: str, position: int, fenetre: int = 40) -> bool:
    avant = _sans_accent(phrase[max(0, position - fenetre):position])
    return any(re.search(rf"\b{n}\b", avant) for n in NEGATIONS[:7]) or \
        bool(re.search(r"\b(il|im|in|ir)?lisible", avant))


def _nombre_accepte(brut: str, acceptables: set[str]) -> bool:
    """Ce nombre vient-il des faits, a l'arrondi pres ?

    ARRONDIR EST LEGITIME, ET LE REFUSER COUTE CHER.

    Mesure R-S18 : la trace disait `1535.297`, le modele a ecrit "1535
    secondes", et le correcteur a crie a la donnee non sourcee. Le fait
    etait exact, la presentation seulement plus lisible.

    Un faux positif use la confiance aussi surement qu'un raté : celui qui
    lit les alertes apprend a les ignorer. On accepte donc un nombre des
    qu'une valeur des faits, arrondie **a la precision que le texte a
    choisie**, lui est egale. "1535" accepte 1535,297 ; il n'accepte pas
    1540, et la regle reste donc serree.
    """
    n = brut.replace(",", ".")
    if n in acceptables:
        return True
    try:
        valeur = float(n)
    except ValueError:
        return False
    decimales = len(n.split(".")[1]) if "." in n else 0
    for a in acceptables:
        try:
            if round(float(a.replace(",", ".")), decimales) == valeur:
                return True
        except ValueError:
            continue
    return False


def _detecter(texte: str, faits: Faits) -> list[tuple[str, Anomalie]]:
    """Rend une liste de (phrase fautive, anomalie). Ne modifie rien."""
    trouve: list[tuple[str, Anomalie]] = []
    ids_ok = faits.identifiants()
    nombres_ok = faits.nombres()

    for ph in phrases(texte):
        nu = _sans_accent(ph)

        for ident in _IDENTIFIANT.findall(ph):
            if ident.lower() not in ids_ok:
                trouve.append((ph, Anomalie(
                    "fait_absent",
                    f"'{ident}' n'apparait dans aucun fait source", ph.strip())))

        for nb in _NOMBRE.findall(ph):
            if not _nombre_accepte(nb, nombres_ok):
                trouve.append((ph, Anomalie(
                    "chiffre_non_source",
                    f"le nombre {nb} ne vient d'aucun fait ni d'un ecart entre faits",
                    ph.strip(), grave=False)))

        for f in faits.indetermines():
            if not any(_sans_accent(m) in nu for m in f.mots_marqueurs()):
                continue
            if not any(p in nu for p in PRUDENCES):
                trouve.append((ph, Anomalie(
                    "cause_promue",
                    f"'{f.cle}' est declare indetermine, la phrase nomme une "
                    "cause sans prudence",
                    ph.strip())))

        for f in faits.booleens_faux():
            for marqueur in f.mots_marqueurs():
                m = _sans_accent(marqueur)
                pos = nu.find(m)
                if pos < 0:
                    continue
                if not _contient_negation_avant(ph, pos):
                    trouve.append((ph, Anomalie(
                        "contradiction",
                        f"les faits donnent {f.cle}=False, la phrase l'affirme",
                        ph.strip())))
                break
    return trouve


# --------------------------------------------------------------------------- #
# Correction
# --------------------------------------------------------------------------- #

def corriger(brouillon: str, faits: Faits, *,
             couper_tronquee: bool = True,
             retirer_graves: bool = True,
             relecteur: Callable[[str, Faits], str] | None = None) -> Resultat:
    """Relire un brouillon contre des faits. Ne rajoute jamais de contenu.

    `relecteur` branche un modele en second passage. Sa sortie repasse
    integralement par cette meme fonction, et elle est REJETEE si elle
    introduit un mot qui ne vient ni du brouillon, ni des faits, ni du
    catalogue. Un modele ne peut donc pas contourner la regle.
    """
    anomalies: list[Anomalie] = []
    texte = brouillon or ""

    boucle = repetition_en_boucle(texte)
    if boucle:
        anomalies.append(Anomalie("repetition",
                                  f"le mot '{boucle}' se repete en boucle"))
        texte = re.sub(rf"(?:\b{re.escape(boucle)}\b[ ,]*){{3,}}",
                       f"{boucle} ", texte, flags=re.I)

    if est_tronque(texte):
        anomalies.append(Anomalie("phrase_tronquee",
                                  "le flux s'arrete au milieu d'une phrase",
                                  texte.rstrip()[-40:]))
        if couper_tronquee:
            coupe = _couper_derniere_phrase(texte)
            texte = (coupe + " " + CATALOGUE["coupe"]) if coupe else CATALOGUE["coupe"]

    detections = _detecter(texte, faits)
    anomalies.extend(a for _, a in detections)

    retires: list[str] = []
    if retirer_graves and detections:
        a_retirer = {ph for ph, a in detections
                     if a.grave and a.genre in ("fait_absent", "contradiction")}
        a_neutraliser = {ph for ph, a in detections if a.genre == "cause_promue"}
        morceaux = []
        for ph in phrases(texte):
            if ph in a_retirer:
                retires.append(ph.strip())
                morceaux.append(" " + CATALOGUE["retire"])
            elif ph in a_neutraliser:
                retires.append(ph.strip())
                morceaux.append(" " + CATALOGUE["cause_indeterminee"])
            else:
                morceaux.append(ph)
        texte = "".join(morceaux).strip()

    resultat = Resultat(texte=texte.strip(), anomalies=anomalies, retires=retires)
    resultat.invariant_respecte = respecte_invariant(resultat.texte, brouillon, faits)

    if relecteur is not None:
        resultat = _passer_relecteur(resultat, brouillon, faits, relecteur)
    return resultat


def _couper_derniere_phrase(texte: str) -> str:
    """Garder les phrases terminees, jeter la derniere. Jamais completer.

    Passe par le meme decoupage que le reste : couper sur le dernier point
    trouve au hasard amputerait `41.0` en plein milieu.
    """
    bouts = phrases((texte or "").rstrip())
    if not bouts:
        return ""
    if bouts[-1].rstrip()[-1:] in FIN_DE_PHRASE:
        return "".join(bouts).strip()
    return "".join(bouts[:-1]).strip()


def vocabulaire_autorise(brouillon: str, faits: Faits) -> set[str]:
    autorise = set(_mots(brouillon))
    autorise |= faits.mots()
    for phrase in CATALOGUE.values():
        autorise |= set(_mots(phrase))
    return autorise


def respecte_invariant(texte: str, brouillon: str, faits: Faits) -> bool:
    """Tout mot du resultat vient du brouillon, des faits ou du catalogue."""
    autorise = vocabulaire_autorise(brouillon, faits)
    return all(m in autorise for m in _mots(texte))


def _passer_relecteur(base: Resultat, brouillon: str, faits: Faits,
                      relecteur: Callable[[str, Faits], str]) -> Resultat:
    """Second passage par un modele, sous la meme regle. Rejet si triche."""
    try:
        propose = relecteur(base.texte, faits)
    except Exception as exc:  # noqa: BLE001
        base.anomalies.append(Anomalie(
            "relecteur_en_panne", f"{type(exc).__name__}", grave=False))
        return base
    if not propose or not propose.strip():
        base.relecteur_rejete = True
        return base
    if not respecte_invariant(propose, brouillon, faits):
        base.anomalies.append(Anomalie(
            "relecteur_rejete",
            "la proposition du relecteur introduit des mots absents des sources"))
        base.relecteur_rejete = True
        return base
    verifie = corriger(propose, faits, couper_tronquee=True,
                       retirer_graves=True, relecteur=None)
    if len(verifie.anomalies) > len(base.anomalies):
        base.relecteur_rejete = True
        base.anomalies.append(Anomalie(
            "relecteur_rejete",
            "la proposition du relecteur cree plus d'anomalies qu'elle n'en corrige",
            grave=False))
        return base
    verifie.relecteur_rejete = False
    verifie.retires = base.retires + verifie.retires
    return verifie
