"""Regime riche PERIODIQUE : le decor des ecrans dans le temps (R-S14).

CE QUE CE MODULE AJOUTE, ET POURQUOI IL FALLAIT L'AJOUTER
----------------------------------------------------------

Le tick structurel dit OU sont les fenetres ; le regime declenche (R-S13) dit
ce qui est ECRIT dans une fenetre qui vient d'apparaitre. Ni l'un ni l'autre
ne dit ce que montrent les ecrans quand RIEN ne bouge.

Mesure R-S12 sur la session de 30 min de l'utilisateur : 24 changements structurels
seulement. Pendant un film, aucune fenetre ne bouge -- et c'est precisement
la que la trace structurelle est la plus vide alors qu'il se passe le plus de
choses. Ce module echantillonne donc le decor a cadence lente.

LE COUT N'EST PAS CELUI QUE LE PLAN AVAIT ANNONCE
--------------------------------------------------

Le plan R-S12 chiffrait un tick riche a **1584 ms** et en deduisait 2,7 % de
ticks structurels perdus. Ce chiffre etait celui de `get_vision_snapshot`
(depth=rich, tous ecrans), dont l'essentiel part dans UIA : arbre du
navigateur, inventaire des controles, barre des taches.

Mesure R-S14, en assemblant directement les primitives :

  capture plein ecran 2560x1440      33 ms
  capture plein ecran 1920x1080      22 ms
  colors_of (teinte, luminance)     1,5 a 3,8 ms
  densite_contours (y a-t-il du texte)  0,8 a 6 ms
  ---------------------------------------------
  les DEUX ecrans, echantillon complet   ~61 ms

Soit **26 fois moins** que la valeur du plan. Un echantillon par minute coute
0,1 % d'un coeur : le regime periodique ne sacrifie aucun tick structurel, et
l'arbitrage "un seul processus sequentiel" du plan (S 5.3) se paie beaucoup
moins cher que prevu.

CE QU'ON ECRIT : DES DIFFERENCES
---------------------------------

Meme regle que la structure. Un echantillon brut pese ~250 o ; ecrire les 30
echantillons d'une session de 30 min alors que rien n'a change serait le
defaut deja identifie sur les images-cles. On ecrit donc un ANCRAGE par ecran
au premier echantillon, puis uniquement ce qui a bouge.

LES SEUILS, MESURES ET NON DEVINES (inconnu K5 du plan)
--------------------------------------------------------

36 echantillons sur 30 s, deux ecrans, poste de l'utilisateur :

  |d luminance|   mediane 0,00   p90 0,20   max 32,10 (changement reel)
  |d dominante|   0 sur 35 echantillons -- elle ne bouge jamais par bruit
  |d densite|     mediane 0,00001   max 0,00089 sur l'ecran reste immobile

Le plancher de bruit de BitBlt sur un ecran immobile est **nul** : deux
captures successives rendent des statistiques identiques. Les seuils peuvent
donc etre bas sans produire de bavardage. SEUIL_LUMA vaut 10 fois le p90 du
bruit, SEUIL_DENSITE environ 2 fois le maximum observe a l'arret.

VIE PRIVEE -- CE REGIME REGARDE TOUT L'ECRAN
---------------------------------------------

Le declenche capture une console qui vient d'apparaitre ; celui-ci capture
**tout l'ecran, periodiquement**. Ce n'est pas la meme promesse, et il est
donc **opt-in** (`--riche`), jamais actif par defaut.

Au niveau `resume`, **aucun mot n'est lu** : la presence de texte est deduite
d'une densite de contours (1 ms) et non d'un OCR (1000 ms). Lire le bandeau
demande `--contenu texte`, explicitement.

Aucune image n'atteint le disque. La frame plein ecran est liberee des que
les statistiques sont calculees ; seule la bande basse survit, et seulement
si une lecture est demandee.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

# Un echantillon par minute : assez lent pour ne rien couter, assez rapide
# pour qu'une seance de 30 min garde trente points de repere.
CADENCE_DEFAUT_S = 60.0

# Seuils mesures (voir le docstring). Un ecran immobile rend 0,00 ; ces
# valeurs laissent donc une marge large avant d'ecrire quoi que ce soit.
SEUIL_LUMA = 2.0
SEUIL_DENSITE = 0.002

# Part basse de l'ecran ou vivent sous-titres et barre des taches.
PART_BANDE = 0.30

# La file des bandes en attente de lecture reste minuscule : une bande de
# 2560x433 pese ~3 Mo en memoire. Deux ecrans, une lecture de retard.
FILE_MAX = 4


@dataclass
class EchantillonDecor:
    """Ce qu'un ecran montre a un instant. La frame ne touche jamais le disque."""

    ecran: int
    taille: list[int]
    capture_ms: float
    luma: float
    ecart_luma: float
    noir: bool
    theme: str
    dominante: list[int]
    densite_texte: float
    texte_present: bool
    t_s: float
    origine_bande: tuple[int, int] = (0, 0)
    bande: Any = field(default=None, repr=False, compare=False)

    def liberer(self) -> None:
        self.bande = None

    def ancrage(self) -> dict[str, Any]:
        """L'etat complet de cet ecran : ecrit une fois, puis on diffe."""
        return {
            "quoi": "decor.initial",
            "ecran": self.ecran,
            "taille": self.taille,
            "capture_ms": self.capture_ms,
            "luminance": self.luma,
            "theme": self.theme,
            "dominante": self.dominante,
            "noir": self.noir,
            "densite_texte": round(self.densite_texte, 5),
            "texte_present": self.texte_present,
        }


def echantillonner(ecran: int, rect: tuple[int, int, int, int], t_s: float,
                   *, garder_bande: bool = False) -> EchantillonDecor | None:
    """Un echantillon de decor pour un ecran. ~22 a 40 ms. None si impossible.

    Ne leve pas : un moniteur debranche entre deux echantillons est une
    condition d'environnement, pas une panne.

    `garder_bande` conserve la bande basse en memoire pour une lecture
    DIFFEREE. Par defaut elle est liberee tout de suite avec le reste : au
    niveau `resume`, la densite de contours suffit a dire qu'il y a du texte,
    pour un milliseme du prix d'un OCR.
    """
    l, t, r, b = (int(v) for v in rect)
    if r <= l or b <= t:
        return None
    try:
        from screen_content import _capture
        from screen_rich import SEUIL_DENSITE_TEXTE, colors_of, densite_contours

        cap = _capture("ecran", (l, t, r, b), None, "periodique")
        couleurs = colors_of(cap.frame)
        hauteur = cap.frame.shape[0]
        haut_bande = int(hauteur * (1.0 - PART_BANDE))
        bande = cap.frame[haut_bande:, :]
        densite = densite_contours(bande)
    except Exception:  # noqa: BLE001
        return None

    return EchantillonDecor(
        ecran=ecran,
        taille=[cap.width, cap.height],
        capture_ms=round(cap.elapsed_ms, 1),
        luma=cap.mean_luma,
        ecart_luma=cap.std_luma,
        noir=cap.looks_black,
        theme=str(couleurs.get("theme", "")),
        dominante=list(couleurs.get("dominant_rgb", [])),
        densite_texte=float(densite),
        texte_present=bool(densite > SEUIL_DENSITE_TEXTE),
        t_s=round(t_s, 3),
        origine_bande=(l, t + haut_bande),
        # COPIE, pas une vue : une tranche numpy garde vivante la frame
        # entiere (25 Mo pour un 2560x1440). La copie de la bande en pese 3.
        bande=bande.copy() if garder_bande else None,
    )


def differences_decor(avant: EchantillonDecor | None,
                      apres: EchantillonDecor) -> list[dict[str, Any]]:
    """Ce qui a change sur cet ecran depuis l'echantillon precedent.

    Liste vide = le decor n'a pas bouge, et on n'ecrit rien du tout. C'est la
    regle deja appliquee a la structure : des differences, pas des etats.
    """
    if avant is None:
        return []
    out: list[dict[str, Any]] = []

    if abs(apres.luma - avant.luma) >= SEUIL_LUMA:
        out.append({"quoi": "decor.luminance", "de": avant.luma, "vers": apres.luma})
    if apres.theme != avant.theme:
        out.append({"quoi": "decor.theme", "de": avant.theme, "vers": apres.theme})
    if apres.dominante != avant.dominante:
        out.append({"quoi": "decor.dominante", "de": avant.dominante,
                    "vers": apres.dominante})
    if apres.noir != avant.noir:
        # Ne conclut PAS au DRM : flux protege, ecran eteint et scene tres
        # sombre donnent la meme image. On dit ce qu'on voit (R-S13).
        out.append({"quoi": "decor.noir", "de": avant.noir, "vers": apres.noir})
    if apres.texte_present != avant.texte_present:
        out.append({"quoi": "decor.texte_present", "de": avant.texte_present,
                    "vers": apres.texte_present})
    elif abs(apres.densite_texte - avant.densite_texte) >= SEUIL_DENSITE:
        out.append({"quoi": "decor.densite_texte",
                    "de": round(avant.densite_texte, 5),
                    "vers": round(apres.densite_texte, 5)})
    return out


def lire_bande(ech: EchantillonDecor, niveau: str) -> dict[str, Any] | None:
    """Lire le texte incruste dans la bande basse. ~900 a 1700 ms, DIFFERE.

    Rend None quand il n'y a rien a faire : niveau autre que `texte`, bande
    deja liberee, ou bande sans aucun contour -- inutile de payer un OCR pour
    apprendre qu'une zone unie ne contient pas de texte.

    Reglage `psm6-x3` de `read_overlay_text`, inchange : le golden reel de
    l'utilisateur avait tranche en R-S3b/R-S4b que l'agrandissement x3 valait son
    prix (rappel 0,867 contre 0,578 en x2). Ce lot ne le rediscute pas.
    """
    if niveau != "texte" or ech.bande is None or not ech.texte_present:
        return None
    try:
        from screen_rich import read_overlay_text

        res = read_overlay_text(ech.bande, ech.origine_bande)
    except Exception as exc:  # noqa: BLE001
        return {"quoi": "decor.texte", "ecran": ech.ecran, "vue_a_t_s": ech.t_s,
                "lisible": False, "raison": f"lecture impossible ({type(exc).__name__})"}

    lignes = [str(l.get("text", "")) for l in (res.get("lines") or [])]
    out: dict[str, Any] = {
        "quoi": "decor.texte",
        "ecran": ech.ecran,
        "vue_a_t_s": ech.t_s,
        "ocr_ms": round(res.get("elapsed_ms", 0.0), 1),
        "lisible": bool(res.get("available")),
        "mots": int(res.get("words", 0) or 0),
        "lignes": len(lignes),
    }
    if not res.get("available"):
        out["raison"] = res.get("reason") or "OCR indisponible"
        return out
    out["texte"] = lignes
    return out


# Deux lectures se ressemblant a plus de SEUIL_RESSEMBLANCE sont tenues pour
# la MEME. Valeur mesuree, pas choisie (voir `texte_a_change`).
SEUIL_RESSEMBLANCE = 0.7


def signature_texte(lignes: list[str] | None) -> tuple[str, ...]:
    """Signature normalisee d'une lecture, pour la comparer a la precedente.

    Sans comparaison, le regime periodique reecrit la meme barre des taches a
    chaque echantillon -- exactement le defaut reproche aux images-cles dans
    le plan R-S12 (86 % du fichier pour des donnees repetees).

    La normalisation absorbe la ponctuation et la casse, pas les mots.
    """
    return tuple(
        cle for cle in
        ("".join(c for c in str(l).lower() if c.isalnum()) for l in (lignes or []))
        if cle
    )


def texte_a_change(avant: tuple[str, ...] | None,
                   apres: tuple[str, ...],
                   seuil: float = SEUIL_RESSEMBLANCE) -> bool:
    """La lecture est-elle assez differente pour meriter une ligne de trace ?

    POURQUOI PAS UNE EGALITE STRICTE. C'etait ma premiere version, et la
    mesure l'a refusee. Sur une image dont la luminance et la densite de
    contours n'avaient pas bouge -- donc un ecran reellement immobile --
    l'OCR a rendu des lectures differentes :

      image stable    ressemblance 1,00 · 0,80 · 0,88
      image changee   ressemblance 0,50 · 0,33 · 0,40 · 0,28 · 0,07 · 0,00

    Une egalite stricte aurait donc ecrit une ligne pour du bruit de
    reconnaissance, sur deux echantillons stables sur trois. Le seuil de 0,7
    tombe dans l'ecart entre les deux populations, avec de la marge des deux
    cotes.

    Reserve honnete : trois transitions stables seulement. Si une session
    reelle montre du bavardage, c'est ce seuil qu'il faut remonter, et la
    mesure ci-dessus qu'il faut refaire avec plus d'echantillons.
    """
    if avant is None:
        return True
    a, b = set(avant), set(apres)
    if not a and not b:
        return False
    return (len(a & b) / max(1, len(a | b))) < seuil


def fusionner_lignes(*lectures: list[str]) -> list[str]:
    """Reunir plusieurs lectures d'une meme fenetre sans repeter les lignes.

    LE CAS `Boo` (C1, 06/09). Une console a affiche `Boo !!!` puis la
    ligne du `timeout` ; la trace n'a garde que le `timeout`. Le controle
    R-S14 a mis l'OCR hors de cause : sur une image de synthese, `Boo !!!`
    est lu parfaitement, seul (2 mots) comme a cote de la ligne de timeout
    (12 mots). La ligne n'etait donc pas DANS l'image capturee.

    Une seule capture ne peut pas rattraper cela : ce qui n'a pas ete saisi
    est perdu. Deux captures espacees d'un tick, dont on reunit les lignes,
    le peuvent -- et coutent 13 ms de plus.

    Le rapprochement se fait sur une cle normalisee (minuscules, seuls les
    caracteres alphanumeriques) : l'OCR rend "continuer..." une fois et
    "continuer. |" la fois suivante, et ces deux-la sont la meme ligne. On
    garde la variante la plus longue, qui est la lecture la plus complete.
    """
    ordre: list[str] = []
    par_cle: dict[str, str] = {}
    for lecture in lectures:
        for ligne in lecture or []:
            cle = "".join(c for c in str(ligne).lower() if c.isalnum())
            if not cle:
                continue
            if cle not in par_cle:
                par_cle[cle] = str(ligne)
                ordre.append(cle)
            elif len(str(ligne)) > len(par_cle[cle]):
                par_cle[cle] = str(ligne)
    return [par_cle[c] for c in ordre]


def cout_echantillon_ms(rect: tuple[int, int, int, int]) -> float:
    """Mesurer le cout d'un echantillon sur ce rectangle. Sert au smoke."""
    t0 = time.perf_counter()
    echantillonner(0, rect, 0.0)
    return round((time.perf_counter() - t0) * 1000.0, 1)
