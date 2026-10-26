# Faire lire une session par un LLM local

**Statut :** livré R-S17 (2026-09-10). Le chemin complet existe : trace, digest, faits, journal contrôlé.
**Modules :** `src/session_digest.py` · `src/llm_stream/` · **CLI** `scripts/controle_session.py`
**Duo :** `docs/llm-stream-correction.md`

> **Vision ne dépend de rien.** Une session tourne exactement pareil avec Ollama éteint. Ce qui suit décrit un **lecteur** optionnel de traces, jamais un prérequis.

---

## 0. Contrôler une session, en une commande

```bash
python scripts/controle_session.py                      # la session la plus récente
python scripts/controle_session.py --digest-seul        # sans aucun modèle
python scripts/controle_session.py --faits              # voir les faits extraits
```

Le texte défile au fil de l'eau, puis la relecture s'affiche avec ses anomalies. Le journal part dans `data/journaux/`, **à côté** de la trace, jamais dedans : une trace est un constat d'instruments, l'interprétation se range ailleurs.

**Sans Ollama, la commande rend quand même le digest et les faits, et sort avec le code 0.** Digérer une trace n'a jamais demandé de modèle.

### Ce que ça donne, mesuré

| Trace | Brut | Digest | Facteur |
|---|---:|---:|---:|
| 30 min `film` (session réelle) | ~8 600 jetons | **671 jetons** | **12,8 fois** |
| 2 min avec contenu lu | ~3 100 jetons | 1 143 jetons | 2,7 fois |

Les images-clés faisaient **88 %** du fichier. Le digest les jette toutes sauf la première, dont il garde un résumé compact : sans cet ancrage, une trace purement différentielle ne sait plus dire ce qu'il y avait à l'écran au départ.

---

## 1. Le bake-off

Le script du bake-off reste dans le dépôt privé ; les chiffres de ce document en viennent. **Aucun `ollama pull` n'a été lancé** : seuls les modèles déjà installés ont été mesurés.

---

## 2. Les trois réglages qui décident de tout

Le smoke initial concluait que plusieurs modèles étaient inutilisables. La mesure dit que c'est **l'appel** qui l'était.

| Réglage | Sans lui |
|---|---|
| **`think: false`** | le budget de génération part dans le raisonnement, `content` reste vide |
| **`system` minimal fourni par l'appelant** | le `SYSTEM` du Modelfile s'applique : pour certains tags locaux, c'est une persona de compagnon |
| **`num_predict` ≥ 700** | `done_reason: "length"` : la réponse est coupée avant d'exister |
| `temperature: 0.0` | un contrôleur qui varie d'un appel à l'autre ne contrôle rien |

Vérifier qu'un tag ne porte pas de persona : `ollama show <tag> --modelfile`, puis lire la ligne `SYSTEM`.

---

## 3. Pourquoi la réponse est en JSON

Un verdict en prose demanderait un juge : humain, ou un second LLM. **Un LLM qui note un LLM ne se mesure pas.** Le contrôleur rend donc un objet à clés fixes :

```jsonc
{"resume": "...", "flash_console": false, "zone_illisible": true,
 "cause_certaine": null, "trace_complete": true, "confiance": 0.9}
```

Chaque clé se compare à une vérité connue, et c'est de toute façon ce dont le code appelant aurait besoin.

**`cause_certaine` est la clé qui compte.** Une zone noire a trois causes indiscernables : flux protégé, écran éteint, scène très sombre. Un contrôleur qui répond « DRM » paraît plus utile et il est plus dangereux. La bonne réponse est `null`, et le bake-off la note comme telle.

---

## 4. Ce qui partira au modèle : et ce qui ne partira jamais

Une trace réelle de 30 min pèse ~34 Ko, soit ~8 600 jetons : **elle ne tient pas** dans le contexte Ollama par défaut (8 192). Le pont envoie donc un **digest**, jamais la trace brute.

| Ce qui part | Ce qui ne part **jamais** par défaut |
|---|---|
| profil, cadence, durée | **le texte lu à l'écran** |
| les changements de fenêtre | les lignes de sous-titres |
| `lisible`, `mots`, `raison`, `identite` | le contenu des captures |
| les changements de décor | les images |
| tous les compteurs du pied | |

R-S14 a rendu ce point concret : au niveau `texte`, la boîte **Exécuter** a livré des chemins de fichiers personnels. Une trace locale est une chose ; l'envoyer à un modèle en est une autre. Il faut `--avec-texte`, en connaissance de cause, et le smoke vérifie que sans ce drapeau rien ne passe.

### Deux ambiguïtés retirées à la source

Le correcteur ne rattrape pas tout, alors on enlève ce qui piège, au lieu de le punir ensuite.

| Problème | Ce que le digest envoie |
|---|---|
| La phrase « Flux protégé (DRM), écran éteint ou scène très sombre » se faisait recopier dans un champ « cause certaine » (R-S15, 4 modèles sur 5) | le code `zone_noire_indiscernable`, jamais la phrase |
| La clé `cadence_s` valant 5.0 s'est fait lire « 5 images par seconde » alors que c'était une observation toutes les 5 secondes | la clé `secondes_entre_deux_observations` |

Le second cas mérite d'être connu : **aucun correcteur déterministe ne peut l'attraper.** Le nombre est juste, c'est le sens qui est faux. Le seul remède est de nommer les choses correctement avant de les envoyer.

---

## 5. Vérifier

```bash
python scripts/smoke_session_digest.py     # 45 contrôles, sans Ollama
python scripts/smoke_llm_stream.py         # 56 contrôles, sans Ollama
```

Le smoke du digest vérifie notamment qu'aucun texte lu ne part par défaut, qu'une zone noire produit un fait **indéterminé** et pas une cause, qu'une trace tronquée ou vide ne fait pas planter la lecture, que le journal va dans un fichier séparé, et que **`session_screen.py` ne connaît ni le digest ni le duo**.

Ce que le script garantit : aucun téléchargement, aucun appel réseau hors `127.0.0.1`, aucune image écrite sur disque, et un rapport qui ne contient que des scores et des latences.
