# Lire une case de la grille (V1.1)

« Que vois-tu en `2:C4` ? » se dit maintenant en une commande. La grille est la même que celle de l'overlay (`scripts/grille.py`) : on peut lire avec la grille affichée ou non.

```powershell
cd <dossier du projet>
python scripts/lire_cases.py --grille                    # taille de la grille de chaque écran
python scripts/lire_cases.py 1:A1                        # couleurs, lumière, formes
python scripts/lire_cases.py 1:A1-1:D2 --avec-texte      # coin haut-gauche + texte (OCR)
python scripts/lire_cases.py 1:F8-1:K9 --avec-texte      # bande du bas (sous-titres hors DRM)
python scripts/lire_cases.py 2:C4 --decrire              # + une phrase d'un modèle local (à vérifier)
python scripts/lire_cases.py 2:A1-2:P3 --rapport-mesure  # phrases écrites par le code, sans modèle
python scripts/lire_cases.py 2:A1-2:P3 --rapport-mesure --decrire   # les deux côte à côte
```

**`--rapport-mesure` (V1.1c)** : le code écrit des phrases à partir des mesures, avec le gabarit `context/vision-rapport-mesure.md` (modifiable sans toucher au code). Aucun modèle, aucun appel réseau, même écran = même rapport. Il lit le texte tout seul et ne nomme une scène que si un **mot-repère** est lu dans un libellé court (pas une citation, pas une longue phrase).

**Pourquoi ce n'est pas un `AGENTS.md`** :
- Un `AGENTS.md` est un mode d'emploi pour un agent IA : c'est le modèle qui le lit et l'interprète.
- Sans modèle, un `AGENTS.md` ne fait rien.
- Le gabarit du rapport-mesure est une recette suivie par du code : toujours la même sortie, zéro invention.
- Il est plus pauvre en scènes : il ne connaît que ses mots-repères.
- Il peut aussi mal conclure, et se tait quand il doute (« scène non affirmée »).

- **Case** `1:B7`, **sous-case** `1:B7.5` (pavé de téléphone, 5 = centre), **plage** `1:F8-1:K9` (un seul écran par plage).
- **Mesuré** : couleurs nommées, lumière, contraste, formes sommaires, texte. **À vérifier** : la phrase de `--decrire` (`qwen3.5:9b-gpu` dans Ollama, déjà installé ; environ 1 s, 25 s au premier appel). `--moteur moondream` reprend l'œil de juillet (Moondream3 local, environ 40 s). Le tag Ollama `moondream:latest` (1B) est non fiable : `decrire_ollama` le refuse sans appel réseau, sauf `VISION_OLLAMA_MODELE_NON_FIABLE=1`.
- **Texte** : chaque morceau est rangé dans la case où il **commence** ; `[1:F9-I9]` veut dire qu'il couvre ces cases. Deux fenêtres côte à côte ne sont plus collées en une seule ligne (corrigé V1.1b). Une phrase du modèle coupée par la limite de longueur est signalée « COUPEE ».
- **Refus clairs** : `1:L16` n'existe pas sur une grille 16×9 (lignes 1 à 9) ; l'outil le dit et propose la bande du bas. Code de sortie 2.
- **Zone noire** (film protégé, écran éteint) : texte et description s'abstiennent, on ne devine pas.
- **Rien n'est enregistré** : aucune image écrite, aucun téléchargement, aucune action sur l'écran. `--json` pour un programme.
