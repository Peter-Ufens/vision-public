# Oeil par trames, et Computer Use

vision-public regarde l'ecran par **photos successives** (grab). Entre deux photos il y a un trou. Ce n'est pas une camera video, ni un flux GPU continu.

Le suiveur `scripts/suivi_dual_ecran.py` enchaine ces photos sur **les deux ecrans**, mesure des cases actives, et **ne fait pas d'OCR a chaque trame**. L'OCR d'interface (`scripts/ocr_hud.py`) ne part que sur un petit rectangle, quand on le demande.

## vision-public et Computer Use ne sont pas le meme outil

| | vision-public | Computer Use (outil d'un agent IA) |
|---|---|---|
| Ou | ce depot, `grab_region` | outil de l'agent, hors `src/` |
| Ecrans | les ecrans listes (1 et 2) | souvent le **moniteur principal seulement** |
| Action | l'oeil ne clique pas | peut injecter des entrees |
| Mot « capture » | une trame figee | souvent une capture de son sidecar |

Si un retour parle d'un cadre « prise en main », il doit dire **Computer Use** ou **vision-public**, pas les deux sous le meme nom.

## Pas d'action

Ce depot ne clique pas et n'envoie aucune touche (ADR-0007). Une eventuelle « main » est un projet separe, qui n'est pas dans ce depot.

## Rythme mesure (26/10, deux ecrans, processeur charge a 99 %)

- Les deux ecrans en parallele : environ **24 paires par seconde**, 34 ms la paire (l'un apres l'autre : 14, 59 ms).
- Lire une petite ligne de texte d'interface (jauge, coordonnees, horloge) avec RapidOCR : **10 a 35 ms**. Tesseract n'y lisait aucun chiffre.

Pas de clic dans `src/` (ADR-0007).
