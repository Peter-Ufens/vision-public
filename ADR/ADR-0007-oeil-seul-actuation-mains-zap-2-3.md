# ADR-0007 : Vision = œil seul · actuation = Mains · phases 2 et 3 zappées

- **Statut :** Accepté
- **Date :** 2026-10-18
- **Décideurs :** Peter UFENS (dictée) · proposition et relecture par les outils d'audit du labo

## Contexte

Le plan du 15/10 prévoyait un terrain VM (phase 2) et du smart awareness (phase 3) pour prouver « je vois » **et** cliquer.  
L'auteur décide (18/10) que **souris / clavier / clic** appartiennent à un projet **Mains** séparé, pas à Vision.  
Sinon, une erreur de clic ne dit plus si c’est l’outil Mains ou un bricolage d’agente (`SetCursorPos`, Hyper-V mouse…).

## Décision

1. **vision-public** = œil PC uniquement (grille, overlay, capture, OCR, session, `lire_cases`, digest…).
2. **Aucune actuation** dans les modules œil (`SetCursorPos`, `mouse_event`, `keybd_event`, `SendInput`, …) : smokes + cette ADR.
3. **Phases 2 (VM `LV-*`) et 3 (smart awareness)** = **zappées** pour ce projet (preuves bonus 17/10 conservées dans les retours).
4. Les micro-tests de la main sont des scripts **jetables** du dépôt privé, **pas** le produit Vision.
5. Les Mains utiliseront Vision en **lecture** (cases, position) pour ne pas agir à l’aveugle (sens inverse, 18/10).

## Conséquences

- (+) Frontière produit claire avant repo public
- (+) Tests de sécurité Vision : scénarios clic/VM reportés au projet Mains ou N/A, pas de faux vert
- (−) Pas de banc VM clic-par-clic dans ce repo
- Suite : tests de sécurité → nettoyage → ouverture publique (nom neutre) · caméras = **autre** projet

## Liens

Décision tracée dans le dépôt **privé** (notes / retours d’audit hors vitrine).  
Cette ADR suffit pour la vitrine publique.

## Addendum 2026-10-26 (vitrine)

La veille active (`scripts/veille_active.py`) est une lecture seule bornée par `--duree` : elle signale, elle n'obéit jamais à un texte vu à l'écran. Ce n'est pas le daemon de la phase 3 zappée. Toute action (clic, touche) est un projet séparé, absent de ce dépôt.
