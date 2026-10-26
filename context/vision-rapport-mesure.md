# Gabarit du rapport-mesure (V1.1c)

> Lu par **du code** (`src/screen_cells.py`, fonction `rapport_mesure`), **pas par un modèle**. Le code remplit les `{trous}` avec les mesures de `lire_cases` (couleurs, lumière, formes, texte lu par l'OCR, cases). Rien n'est deviné : si une mesure manque, la phrase n'est pas dite.
>
> **Différence avec un `AGENTS.md`** : un `AGENTS.md` est un mode d'emploi écrit pour un agent IA, qui le lit et l'interprète. Sans modèle, il ne fait rien. Ce fichier-ci est une **recette** que le code applique toujours de la même façon : même écran, même rapport, zéro invention. On peut le modifier sans toucher au code (changer une phrase, ajouter un mot-repère).

## Phrases

Une ligne par phrase. Ne pas renommer la colonne `clé` : le code s'en sert.

| clé | phrase |
|---|---|
| zone | Zone {cible} : écran {ecran}, {cases} case(s), {largeur} x {hauteur} pixels. |
| noire | La zone est noire (lumière {lum}/255). Rien à lire ni à décrire. Trois causes possibles : film protégé, écran éteint ou image vraiment noire ; on ne tranche pas. |
| couleurs | Couleurs mesurées : {couleurs}. |
| fond_clair | Fond clair presque uni ({blanc} % de blanc). |
| fond_sombre | Fond sombre presque uni ({noir} % de noir). |
| fond_mixte | Mélange de parties sombres et de parties claires. |
| touches | Touches de couleur : {touches}. |
| lumiere | Lumière moyenne {lum}/255, contraste {contraste_mot} ({contraste}). |
| formes | Formes : {niveau} ({details} % de contours), {rectangles} rectangle(s) net(s), {autres} autre(s) contour(s). |
| disposition | Par colonnes : {disposition}. |
| texte_non_demande | Texte non lu (ajouter --avec-texte). |
| texte_indispo | Texte non lu : {raison}. |
| texte_aucun | Aucun texte lisible. |
| texte_intro | Texte lu par l'OCR ({n} morceau(x) sûr(s)) : |
| texte_ligne | en {case} : « {texte} » |
| texte_reste | et {k} autre(s) morceau(x), voir --json. |
| repere | Mot-repère lu en {case} : « {mot} », donc cette zone montre {scene}. |
| repere_cite | « {mot} » lu en {case}, mais entre guillemets : c'est une citation, pas forcément l'application. Scène non affirmée. |
| repere_phrase | « {mot} » lu en {case}, mais dans une phrase de {n} mots : peut-être un texte qui en parle (chat, document). Scène non affirmée. |
| sans_repere | Aucun mot-repère connu : la scène n'est pas nommée (--decrire peut en proposer une, à vérifier). |
| fin | Ces phrases viennent toutes des mesures : rien n'est deviné. |

## Mots-repères

Si l'OCR lit le mot de gauche (sans tenir compte des majuscules ni des accents), le rapport dit la phrase de droite. **Règle : la phrase de droite ne nomme jamais un logiciel absent du mot de gauche.** « point de contrôle » ne suffit pas à dire « Hyper-V » ; « Gestionnaire Hyper-V » suffit.

**Un mot vu ne suffit pas toujours** (essai du 17/10 : un message de chat qui citait « Gestionnaire Hyper-V » a fait croire à la fenêtre Hyper-V). Le code n'affirme la scène que si le mot est dans un **libellé court** (8 mots au plus) et **pas entre guillemets**. Sinon il dit le mot et écrit « scène non affirmée ».

| mot lu par l'OCR | la zone montre |
|---|---|
| Gestionnaire Hyper-V | le Gestionnaire Hyper-V |
| Ordinateurs virtuels | une liste de machines virtuelles |
| point de contrôle | un panneau de points de contrôle |
| Cet ordinateur | l'explorateur de fichiers de Windows |
| Corbeille | le bureau de Windows (icône Corbeille) |
| Edit View | la barre de menu d'un éditeur (File, Edit, View) |
| prêt à jouer | le lanceur d'un jeu |
