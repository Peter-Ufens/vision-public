"""Condenser une trace de session en digest, puis en faits (R-S17).

POURQUOI CE MODULE EXISTE
-------------------------

Une trace reelle de 30 min pese 34 401 octets, soit environ 8 600 jetons,
contre 8 192 de contexte Ollama par defaut. Elle ne rentre pas. Ce n'est
pas une optimisation qu'on pourrait remettre a plus tard : sans digest, il
n'y a pas de controle du tout.

OU PART LE POIDS
----------------

Mesure sur cette meme trace :

  session.keyframe   30 lignes   30 222 o   88 %
  session.diff       13 lignes    3 528 o   10 %
  session.end + start                561 o    2 %

Les images-cles font a elles seules 88 % du fichier, et elles repetent
l'etat complet meme quand rien n'a change. Les jeter ramene la trace a
environ 1 000 jetons.

CE QU'ON GARDE QUAND MEME DE LA PREMIERE IMAGE-CLE
---------------------------------------------------

Les jeter TOUTES supprimerait l'ancrage : une trace purement
differentielle ne sait plus dire ce qu'il y avait a l'ecran au depart. On
garde donc de la PREMIERE image-cle un resume compact (les moniteurs, la
liste des executables, l'application active) et on jette les trente
autres. L'etat initial coute quelques dizaines de jetons ; l'etat repete
en coutait huit mille.

CE QUI NE PART JAMAIS AU MODELE
--------------------------------

**Le texte lu a l'ecran.** R-S14 l'a rendu concret : au niveau `texte`, la
boite de dialogue Executer a livre des chemins de fichiers personnels
tires de son historique. Une trace locale et gitignoree est une chose ;
l'envoyer a un modele en est une autre. Il faut `avec_texte=True`, en
connaissance de cause.

LES CODES PLUTOT QUE LES PHRASES
---------------------------------

Une ligne de contenu porte une raison en francais : "zone noire : rien a
lire. Flux protege (DRM), ecran eteint ou scene tres sombre, indiscernables
sur ce seul signal."

Mesure R-S15 : quatre modeles sur cinq recopiaient cette phrase dans un
champ qui demandait une cause CERTAINE. Ils n'inventaient pas, ils
manquaient la semantique du champ. Le digest envoie donc un CODE stable,
`zone_noire_indiscernable`, et pas la phrase. On retire la tentation au
lieu de la punir. C'est la parade 2 du plan pont, section 2bis.

CE MODULE NE FAIT PAS D'APPEL RESEAU
-------------------------------------

Il lit un fichier et rend des dictionnaires. Le pont vers le duo vit
ailleurs, dans un script, et il est optionnel. Une session tourne sans lui.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA_DIGEST = 1

# Codes stables, envoyes a la place des phrases francaises de la trace.
# L'ordre compte : le premier motif trouve gagne.
CODES_RAISON: tuple[tuple[str, str], ...] = (
    ("zone noire", "zone_noire_indiscernable"),
    ("aucun texte reconnu", "aucun_texte_reconnu"),
    ("OCR indisponible", "ocr_indisponible"),
    ("Tesseract KO", "ocr_en_panne"),
    ("image liberee", "image_liberee_avant_lecture"),
    ("lecture impossible", "lecture_impossible"),
)
CODE_INCONNU = "raison_non_codee"

# Marqueurs lexicaux fournis par Vision au correcteur generique.
#
# Ils servent a deux choses : reperer une contradiction (les faits disent
# que rien n'etait lisible, la phrase affirme le contraire) et reperer une
# cause promue (les faits disent indetermine, la phrase tranche).
#
# Ils restent volontairement en MOTS SIMPLES. Un marqueur en plusieurs mots
# ("on peut lire") echouerait des que le modele tourne la phrase autrement,
# et la detection de negation qui l'accompagne travaille sur les quelques
# caracteres qui precedent le marqueur.
MARQUEURS_LISIBLE: tuple[str, ...] = ("lisible", "lisibles", "dechiffrable",
                                      "dechiffrables")
MARQUEURS_CAUSE_NOIRE: tuple[str, ...] = (
    "drm", "widevine", "protege", "protegee", "netflix", "prime",
    "eteint", "eteinte", "veille",
)

# Ce qu'on ne recopie jamais tel quel depuis la trace.
CLES_INTERDITES = ("texte",)


def _code_raison(raison: str) -> str:
    r = (raison or "").lower()
    if not r.strip():
        return ""
    for motif, code in CODES_RAISON:
        if motif.lower() in r:
            return code
    return CODE_INCONNU


@dataclass
class Digest:
    """Une session reduite a ce qui se raconte."""

    session_id: str = ""
    profil: str = ""
    cadence_s: float | None = None
    duree_prevue_s: float | None = None
    duree_reelle_s: float | None = None
    niveau_contenu: str | None = None
    riche_cadence_s: float | None = None
    debut: dict[str, Any] = field(default_factory=dict)
    evenements: list[dict[str, Any]] = field(default_factory=list)
    contenus: list[dict[str, Any]] = field(default_factory=list)
    decors: list[dict[str, Any]] = field(default_factory=list)
    decor_bilan: dict[str, Any] = field(default_factory=dict)
    decor_moments: list[dict[str, Any]] = field(default_factory=list)
    pied: dict[str, Any] = field(default_factory=dict)
    trous: list[str] = field(default_factory=list)
    lignes_lues: int = 0
    images_cles_jetees: int = 0
    avec_texte: bool = False

    def to_dict(self) -> dict[str, Any]:
        # Les noms de champs sont EXPLICITES, avec leur unite et leur sens.
        # Mesure R-S17 : avec la cle brute `cadence_s`, le modele a ecrit
        # "une cadence de 5,0 images par seconde" la ou la trace disait une
        # observation toutes les 5 secondes. Il avait inverse le rapport.
        # Aucun correcteur deterministe ne peut rattraper cela : le nombre
        # est juste, c'est le sens qui est faux. On retire donc l'ambiguite
        # a la source, comme on l'a fait pour les codes de raison.
        return {
            "v": SCHEMA_DIGEST,
            "session_id": self.session_id,
            "profil": self.profil,
            "secondes_entre_deux_observations": self.cadence_s,
            "duree_prevue_secondes": self.duree_prevue_s,
            "duree_reelle_secondes": self.duree_reelle_s,
            "niveau_contenu": self.niveau_contenu,
            "secondes_entre_deux_echantillons_decor": self.riche_cadence_s,
            "debut": self.debut,
            "evenements": self.evenements,
            "contenus": self.contenus,
            "decor": self.decor_bilan,
            "decor_moments": self.decor_moments,
            "pied": self.pied,
            "trous": self.trous,
            "lignes_lues": self.lignes_lues,
            "images_cles_jetees": self.images_cles_jetees,
        }

    def texte_pour_modele(self) -> str:
        """Le digest tel qu'il part au modele. Compact, sans cle inutile."""
        d = self.to_dict()
        for cle in ("lignes_lues", "images_cles_jetees", "v"):
            d.pop(cle, None)
        return json.dumps(d, ensure_ascii=False, separators=(",", ":"))

    def exes(self) -> list[str]:
        """Tous les executables vus, sans doublon, dans l'ordre d'apparition."""
        out: list[str] = []
        for source in (self.debut.get("fenetres") or [],
                       [e.get("exe") for e in self.evenements],
                       [c.get("exe") for c in self.contenus]):
            for exe in source:
                if exe and exe not in out:
                    out.append(exe)
        return out


# --------------------------------------------------------------------------- #
# Digestion
# --------------------------------------------------------------------------- #

def _resumer_keyframe(etat: dict[str, Any]) -> dict[str, Any]:
    """L'ancrage : ce qu'il y avait a l'ecran au depart, en quelques mots."""
    fenetres = etat.get("fenetres") or {}
    exes: list[str] = []
    for w in fenetres.values():
        exe = w.get("exe")
        if exe and exe not in exes:
            exes.append(exe)
    actif = etat.get("actif") or {}
    return {
        "moniteurs": etat.get("moniteurs") or [],
        "fenetres_ouvertes": len(fenetres),
        "fenetres": exes,
        "actif_exe": actif.get("exe"),
        # L'hote est deja une empreinte de 8 caracteres dans la trace ; on
        # se contente de dire s'il y en avait un, pas lequel.
        "document_present": bool(actif.get("hote")),
    }


def resumer_decors(decors: list[dict[str, Any]], max_moments: int = 20
                   ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replier les echantillons de decor en un bilan par ecran.

    POURQUOI CE REPLI EXISTE, ET CE QU'IL A COUTE DE L'APPRENDRE.

    Mesure R-S18, session reelle de 30 min avec `--riche 20` : le digest
    pesait 12 598 o, dont **11 839 pour la seule section `decors`**, soit
    84 %. C'est exactement la pathologie que le digest avait ete construit
    pour corriger : un type de ligne qui ecrase tous les autres. Les
    images-cles faisaient 88 % de la trace brute ; les echantillons de
    decor faisaient 84 % du digest cense la remplacer.

    Le repli garde ce dont un lecteur a besoin (combien d'echantillons, la
    plage de luminance parcourue, quels changements et combien de fois) et
    jette la suite pas a pas. Deux choses survivent toujours, parce
    qu'elles ne se resument pas :

      - **tout passage au noir**, meme un seul : c'est le signal DRM ;
      - les plus grands sauts de luminance, jusqu'a `max_moments`.
    """
    par_ecran: dict[str, Any] = {}
    moments: list[dict[str, Any]] = []
    sauts: list[tuple[float, dict[str, Any]]] = []

    for d in decors:
        ecran = str(d.get("ecran"))
        e = par_ecran.setdefault(ecran, {
            "echantillons": 0, "changements": {}, "passages_au_noir": 0,
            "lectures_texte": 0})
        if d.get("quoi") == "decor.initial":
            e["echantillons"] += 1
            for cle in ("luminance", "theme", "noir", "texte_present"):
                if d.get(cle) is not None:
                    e.setdefault(f"{cle}_au_depart", d[cle])
            _borner(e, "luminance", d.get("luminance"))
        elif d.get("quoi") == "decor.texte":
            e["lectures_texte"] += 1
            _borner(e, "mots", d.get("mots"))
        else:
            e["echantillons"] += 1
            for c in d.get("changements") or []:
                quoi = c.get("quoi")
                e["changements"][quoi] = e["changements"].get(quoi, 0) + 1
                if quoi == "decor.noir":
                    e["passages_au_noir"] += 1
                    # Un passage au noir ne se resume pas : il se garde.
                    moments.append({"t_s": d.get("t_s"), "ecran": d.get("ecran"),
                                    "quoi": quoi, "vers": c.get("vers")})
                elif quoi == "decor.luminance":
                    try:
                        saut = abs(float(c.get("vers")) - float(c.get("de")))
                    except (TypeError, ValueError):
                        continue
                    _borner(e, "luminance", c.get("vers"))
                    _borner(e, "luminance", c.get("de"))
                    sauts.append((saut, {"t_s": d.get("t_s"), "ecran": d.get("ecran"),
                                         "quoi": quoi, "de": c.get("de"),
                                         "vers": c.get("vers")}))

    reste = max(0, max_moments - len(moments))
    sauts.sort(key=lambda x: -x[0])
    moments.extend(m for _, m in sauts[:reste])
    moments.sort(key=lambda m: m.get("t_s") or 0.0)
    return par_ecran, moments


def _borner(e: dict[str, Any], nom: str, valeur: Any) -> None:
    if valeur is None:
        return
    try:
        v = float(valeur)
    except (TypeError, ValueError):
        return
    cle_min, cle_max = f"{nom}_min", f"{nom}_max"
    e[cle_min] = v if cle_min not in e else min(e[cle_min], v)
    e[cle_max] = v if cle_max not in e else max(e[cle_max], v)


def digerer(lignes: Iterable[dict[str, Any]], *, avec_texte: bool = False,
            max_evenements: int = 400) -> Digest:
    """Condenser une trace deja parsee. Ne leve pas sur une ligne bizarre."""
    dig = Digest(avec_texte=avec_texte)
    premiere_keyframe = True

    for ligne in lignes:
        if not isinstance(ligne, dict):
            continue
        dig.lignes_lues += 1
        type_ = ligne.get("type")

        if type_ == "session.start":
            dig.session_id = str(ligne.get("session_id") or "")
            dig.profil = str(ligne.get("profil") or "")
            dig.cadence_s = ligne.get("cadence_s")
            dig.duree_prevue_s = ligne.get("duree_prevue_s")
            dig.niveau_contenu = ligne.get("contenu")
            dig.riche_cadence_s = ligne.get("riche_cadence_s")

        elif type_ == "session.keyframe":
            if premiere_keyframe:
                dig.debut = _resumer_keyframe(ligne.get("etat") or {})
                premiere_keyframe = False
            else:
                # Les 29 autres repetent l'etat complet : 88 % du fichier
                # pour une information deja portee par les differences.
                dig.images_cles_jetees += 1

        elif type_ == "session.diff":
            for c in ligne.get("changements") or []:
                if len(dig.evenements) >= max_evenements:
                    break
                dig.evenements.append(_compacter_changement(ligne.get("t_s"), c))

        elif type_ == "session.contenu":
            dig.contenus.append(_compacter_contenu(ligne, avec_texte))

        elif type_ == "session.decor":
            entree = _compacter_decor(ligne, avec_texte)
            if entree is not None:
                dig.decors.append(entree)

        elif type_ == "session.erreur":
            dig.trous.append(f"erreur a t={ligne.get('t_s')} : {ligne.get('erreur')}")

        elif type_ == "session.end":
            dig.duree_reelle_s = ligne.get("duree_reelle_s")
            dig.pied = _compacter_pied(ligne)

    dig.decor_bilan, dig.decor_moments = resumer_decors(dig.decors)
    dig.trous.extend(_trous_du_pied(dig.pied))
    if not dig.pied:
        dig.trous.append("trace sans pied de session : elle a ete coupee, "
                         "ce qui suit peut etre incomplet")
    return dig


def digerer_fichier(chemin: str | Path, **kw: Any) -> Digest:
    """Lire un JSONL de session et le condenser."""
    p = Path(chemin)
    lignes = []
    for brut in p.read_text(encoding="utf-8").splitlines():
        brut = brut.strip()
        if not brut:
            continue
        try:
            lignes.append(json.loads(brut))
        except json.JSONDecodeError:
            # Une derniere ligne tronquee arrive si la session a ete tuee.
            # On la saute et le pied manquant sera signale comme un trou.
            continue
    return digerer(lignes, **kw)


def _compacter_changement(t_s: Any, c: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"t_s": t_s, "quoi": c.get("quoi")}
    for cle in ("exe", "classe", "ecran", "de", "vers"):
        if c.get(cle) is not None:
            out[cle] = c[cle]
    # La bbox n'apprend rien a un modele de texte et coute quatre nombres.
    return out


def _compacter_contenu(ligne: dict[str, Any], avec_texte: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "t_s": ligne.get("t_s"),
        "exe": ligne.get("exe"),
        "classe": ligne.get("classe"),
        "lisible": bool(ligne.get("lisible")),
        "mots": ligne.get("mots"),
    }
    if ligne.get("luminance") is not None:
        out["luminance"] = ligne.get("luminance")
    code = _code_raison(str(ligne.get("raison") or ""))
    if code:
        out["raison_code"] = code
    ident = ligne.get("identite") or {}
    if ident:
        out["identite"] = {k: ident.get(k) for k in ("exe", "pid", "demarre_depuis_s")
                           if ident.get(k) is not None}
    if ligne.get("prises"):
        out["prises"] = ligne["prises"]
    if avec_texte and ligne.get("texte"):
        out["texte"] = ligne["texte"]
    return out


def _compacter_decor(ligne: dict[str, Any], avec_texte: bool) -> dict[str, Any] | None:
    quoi = ligne.get("quoi")
    if quoi == "decor.initial":
        return {"t_s": ligne.get("t_s"), "quoi": quoi, "ecran": ligne.get("ecran"),
                "luminance": ligne.get("luminance"), "theme": ligne.get("theme"),
                "noir": ligne.get("noir"), "texte_present": ligne.get("texte_present")}
    if quoi == "decor.texte":
        out = {"t_s": ligne.get("t_s"), "quoi": quoi, "ecran": ligne.get("ecran"),
               "lisible": ligne.get("lisible"), "mots": ligne.get("mots")}
        if avec_texte and ligne.get("texte"):
            out["texte"] = ligne["texte"]
        return out
    if ligne.get("changements"):
        # On garde `de` et `vers`, pas seulement le nom du changement : le
        # bilan en a besoin pour mesurer l'amplitude des sauts de luminance
        # et retenir les plus grands. Cette liste ne part plus au modele
        # depuis R-S18, elle nourrit le repli.
        return {"t_s": ligne.get("t_s"), "ecran": ligne.get("ecran"),
                "changements": [{"quoi": c.get("quoi"), "de": c.get("de"),
                                 "vers": c.get("vers")}
                                for c in ligne["changements"]]}
    return None


CLES_PIED = ("ticks", "changements", "images_cles", "erreurs_tick", "raison_arret",
             "cout_tick_p50_ms", "contenu", "captures_armees", "lectures_faites",
             "lectures_en_echec", "captures_abandonnees", "captures_non_lues",
             "relectures_faites", "relectures_perdues", "riche_cadence_s",
             "riches_echantillons", "decor_changements", "ticks_attendus",
             "ticks_sautes", "duree_reelle_s", "duree_prevue_s")


def _compacter_pied(ligne: dict[str, Any]) -> dict[str, Any]:
    # `fichier` porte un chemin absolu, donc un nom d'utilisateur. Il n'a
    # rien a faire dans ce qui part au modele.
    return {k: ligne[k] for k in CLES_PIED if ligne.get(k) is not None}


def _trous_du_pied(pied: dict[str, Any]) -> list[str]:
    """Ce que la session declare elle-meme avoir manque.

    Une session honnete compte ses propres trous (R-S14). Les remonter au
    premier plan evite qu'un modele lise une trace amputee comme une
    session calme.
    """
    out: list[str] = []
    if not pied:
        return out
    sautes = pied.get("ticks_sautes") or 0
    if sautes:
        out.append(f"{sautes} tick(s) structurel(s) saute(s) sur "
                   f"{pied.get('ticks_attendus')} attendus")
    for cle, mot in (("captures_non_lues", "capture(s) jamais lue(s)"),
                     ("captures_abandonnees", "capture(s) abandonnee(s), file pleine"),
                     ("lectures_en_echec", "lecture(s) en echec"),
                     ("relectures_perdues", "relecture(s) perdue(s), fenetre disparue"),
                     ("erreurs_tick", "erreur(s) de tick")):
        n = pied.get(cle) or 0
        if n:
            out.append(f"{n} {mot}")
    raison = pied.get("raison_arret")
    if raison and raison != "duree_atteinte":
        out.append(f"arret anticipe : {raison}")
    return out


# --------------------------------------------------------------------------- #
# Faits
# --------------------------------------------------------------------------- #

def faits_depuis_digest(dig: Digest) -> list[dict[str, Any]]:
    """Traduire un digest en faits pour `llm_stream.Faits.depuis`.

    Deux principes tiennent cette fonction :

    1. **Un booleen faux n'est emis que s'il est faux SANS AMBIGUITE.** Si
       une session a des captures lisibles et d'autres non, on n'emet pas
       `lisible: False` : une phrase disant "certaines fenetres etaient
       lisibles" serait vraie, et le correcteur la retirerait a tort.

    2. **Une cause indiscernable est emise comme indeterminee**, avec ses
       marqueurs. C'est la lecon R-S15 : sans cela, un modele affirme
       "DRM" avec une confiance de 1,0 la ou la trace refuse de trancher.
    """
    faits: list[dict[str, Any]] = []

    def ajoute(cle: str, valeur: Any, **kw: Any) -> None:
        if valeur is not None:
            faits.append({"cle": cle, "valeur": valeur, **kw})

    # Noms explicites : voir le commentaire de `Digest.to_dict`. Une cle
    # `cadence_s` s'est fait lire "5 images par seconde" au lieu de "une
    # observation toutes les 5 secondes".
    ajoute("session_id", dig.session_id)
    ajoute("profil", dig.profil)
    ajoute("secondes_entre_deux_observations", dig.cadence_s)
    ajoute("duree_prevue_secondes", dig.duree_prevue_s)
    ajoute("duree_reelle_secondes", dig.duree_reelle_s)
    ajoute("niveau_contenu", dig.niveau_contenu)
    ajoute("secondes_entre_deux_echantillons_decor", dig.riche_cadence_s)

    if dig.debut:
        ajoute("moniteurs", dig.debut.get("moniteurs"))
        ajoute("fenetres_au_depart", dig.debut.get("fenetres_ouvertes"))
        ajoute("application_active_au_depart", dig.debut.get("actif_exe"))

    exes = dig.exes()
    if exes:
        ajoute("executables_vus", ", ".join(exes))

    for quoi in ("fenetre.apparue", "fenetre.disparue", "fenetre.deplacee",
                 "fenetre.premier_plan", "fenetre.change_ecran", "document.change",
                 "ecrans.change"):
        n = sum(1 for e in dig.evenements if e.get("quoi") == quoi)
        if n:
            ajoute(quoi.replace(".", "_"), n)

    for e in dig.evenements:
        if e.get("quoi") == "fenetre.apparue" and e.get("exe"):
            ajoute(f"apparition_{e['exe']}_a_la_seconde", e.get("t_s"))

    # TOUS les horodatages du digest deviennent un fait, pas seulement ceux
    # des apparitions.
    #
    # Mesure R-S18 : le modele a ecrit "disparu a 1535 secondes", ce qui etait
    # EXACT, et le correcteur l'a signale comme non source. La liste de faits
    # etait plus etroite que le digest que le modele avait sous les yeux : il
    # citait une vraie donnee, invisible au verificateur.
    #
    # Un faux positif coute aussi cher qu'un raté : il apprend a ignorer les
    # alertes. Ce qu'on montre au modele doit etre verifiable.
    instants = [e.get("t_s") for e in dig.evenements if e.get("t_s") is not None]
    instants += [m.get("t_s") for m in dig.decor_moments if m.get("t_s") is not None]
    instants += [c.get("t_s") for c in dig.contenus if c.get("t_s") is not None]
    if instants:
        ajoute("secondes_des_evenements",
               ", ".join(str(t) for t in sorted(set(instants))))

    if dig.contenus:
        lisibles = [c for c in dig.contenus if c.get("lisible")]
        illisibles = [c for c in dig.contenus if not c.get("lisible")]
        ajoute("captures_lues", len(dig.contenus))
        ajoute("captures_lisibles", len(lisibles))
        ajoute("captures_illisibles", len(illisibles))
        mots = sum(int(c.get("mots") or 0) for c in lisibles)
        if mots:
            ajoute("mots_lus_au_total", mots)

        # Le booleen n'est emis que si TOUTES les captures etaient illisibles.
        if illisibles and not lisibles:
            faits.append({"cle": "contenu_lisible", "valeur": False,
                          "marqueurs": list(MARQUEURS_LISIBLE)})

        codes = {c.get("raison_code") for c in illisibles if c.get("raison_code")}
        for code in sorted(c for c in codes if c):
            ajoute(f"raison_{code}",
                   sum(1 for c in illisibles if c.get("raison_code") == code))

        if "zone_noire_indiscernable" in codes:
            # Le fait central de ce lot. La luminance est un fait ; la cause
            # n'en est pas un, et la trace le dit explicitement.
            faits.append({
                "cle": "cause_zone_noire",
                "valeur": None,
                "indetermine": True,
                "marqueurs": list(MARQUEURS_CAUSE_NOIRE),
            })
            lum = [c.get("luminance") for c in illisibles
                   if c.get("raison_code") == "zone_noire_indiscernable"
                   and c.get("luminance") is not None]
            if lum:
                ajoute("luminance_zone_noire", min(lum))

        for c in illisibles:
            ident = c.get("identite") or {}
            if ident.get("exe"):
                ajoute(f"processus_identifie_{ident['exe']}", ident.get("pid"))

    if dig.decor_bilan:
        ajoute("ecrans_observes", ", ".join(sorted(dig.decor_bilan)))
        for ecran, bilan in sorted(dig.decor_bilan.items()):
            ajoute(f"ecran{ecran}_echantillons_decor", bilan.get("echantillons"))
            ajoute(f"ecran{ecran}_luminance_min", bilan.get("luminance_min"))
            ajoute(f"ecran{ecran}_luminance_max", bilan.get("luminance_max"))
            if bilan.get("lectures_texte"):
                ajoute(f"ecran{ecran}_lectures_de_texte", bilan["lectures_texte"])
                ajoute(f"ecran{ecran}_mots_lus_max", bilan.get("mots_max"))
            for quoi, n in sorted((bilan.get("changements") or {}).items()):
                ajoute(f"ecran{ecran}_{quoi.replace('.', '_')}", n)
            if bilan.get("passages_au_noir"):
                ajoute(f"ecran{ecran}_passages_au_noir", bilan["passages_au_noir"])

    for cle, valeur in (dig.pied or {}).items():
        if cle in ("duree_reelle_s", "duree_prevue_s"):
            continue  # deja donnes plus haut, sous un nom explicite
        if cle in ("raison_arret", "contenu"):
            ajoute(f"pied_{cle}", valeur)
        elif isinstance(valeur, (int, float)):
            ajoute(f"pied_{cle}", valeur)

    if dig.trous:
        ajoute("trous_declares", " ; ".join(dig.trous))

    return faits


def fabriquer_prompt(dig: Digest) -> str:
    """La consigne envoyee avec le digest. Volontairement seche."""
    return (
        "Tu ecris le journal d'une session d'observation d'ecran, en francais.\n"
        "Trois a six phrases. Que des faits presents dans le digest.\n"
        "Si une cause est marquee indeterminee ou codee "
        "'zone_noire_indiscernable', dis que la cause ne peut pas etre "
        "etablie ; ne nomme aucune explication.\n"
        "Si des trous sont declares, dis-le.\n\n"
        "Digest de session :\n" + dig.texte_pour_modele()
    )
