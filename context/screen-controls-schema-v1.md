# Schéma : inventaire des contrôles (`v: 1`)

**Produit par :** `screen_api.list_controls()` (R-S8) · contrat **1.1**
**Question à laquelle il répond :** « **que puis-je cliquer ici ?** »

Complément de `get_context_snapshot(target=…)`, qui répond à la question inverse : « où est le bouton que je nomme déjà ? ».

---

## Point d'entrée

```python
from screen_api import list_controls

inv = list_controls()                       # fenêtre au premier plan
inv = list_controls(hwnd=123456)            # une fenêtre précise, même derrière
inv = list_controls(roles=["Button"])       # plus rapide
inv = list_controls(scope="document")       # la page seulement, sans le chrome
```

---

## Retour

```jsonc
{
  "v": 1,
  "available": true,
  "reason": "",
  "hwnd": 123456,
  "exe_name": "explorer.exe",
  "monitor_index": 1,
  "scope": "window",              // "window" | "document"
  "has_document": false,
  "roles": ["Button", "Hyperlink", "..."],
  "truncated": false,             // vrai si `limit` a coupé la liste
  "errors": 0,
  "controls": [
    {
      "name": "Nouveau dossier",  // contenu d'écran : voir Vie privée
      "role": "Button",
      "bbox": [120, 340, 260, 372],   // écran ABSOLU, x0 y0 x1 y1
      "class": "...",
      "in_document": false,
      "occluded": false
    }
  ],
  "counts": {
    "total": 108,
    "par_role": {"Button": 28, "TreeItem": 19, "SplitButton": 10, "...": 0},
    "dans_document": 0,
    "occultes": 108,
    "occultation_inconnue": 0
  },
  "timings_ms": {"arbre": 215.0, "occultation": 0.9, "total": 215.9}
}
```

---

## Les trois champs qu'un agent qui clique ne doit pas ignorer

### `occluded`

| Valeur | Sens |
|---|---|
| `false` | un clic au **centre** de ce contrôle atteindrait cette fenêtre |
| `true` | il atteindrait une **autre** fenêtre : le contrôle est recouvert |
| `null` | le gestionnaire de fenêtres n'a pas répondu : **on ne sait pas, et on ne tranche pas** |

Déterminé en interrogeant le gestionnaire de fenêtres (`WindowFromPoint`), pas en comparant des rectangles et des ordres Z. La différence n'est pas théorique : une fenêtre recouverte **conjointement** par deux autres passe pour visible au test rectangle par rectangle, et pour recouverte ici : ce qui est la bonne réponse.

**Limite assumée :** c'est un test du **centre**. Un contrôle recouvert sur un bord mais dégagé en son centre est rendu `occluded: false`, ce qui est la bonne réponse pour un clic, et une réponse incomplète pour un rendu visuel.

### `in_document`

`false` = le contrôle appartient au **chrome de l'application** (onglet, barre d'outils, barre de favoris).

**Conséquence directe :** il ne sera **pas** retrouvé par `get_context_snapshot(target=…)`, qui cherche dans les documents quand la fenêtre en a. Un onglet apparaît donc dans l'inventaire sans être adressable par son nom via `target`. Le champ existe pour que l'appelant le sache au lieu de le découvrir.

### `available` / `reason`

`available: false` n'est jamais une exception : arbre d'accessibilité indisponible, fenêtre fermée, aucun document en `scope="document"`. La raison est écrite. **Un champ absent est absent ; aucun bbox n'est deviné.**

---

## Rôles interrogés

Douze rôles actionnables par défaut : ce sur quoi un humain clique :

```
Button · Hyperlink · MenuItem · ListItem · TabItem · TreeItem
CheckBox · RadioButton · ComboBox · SplitButton · Slider · Edit
```

`Edit` y figure : une zone de saisie se clique avant d'être remplie.
`Text` et `Document` n'y figurent **pas** : on les lit, on ne les actionne pas.

La liste se lit à l'exécution :

```python
from screen_api import describe_contract
describe_contract()["roles_actionnables"]
```

**Un rôle inconnu lève `ValueError`.** C'est une erreur de programmation, pas une condition d'environnement : et rendre une liste vide serait indiscernable de « cette application n'expose aucun contrôle de ce type ». C'est exactement ce qui s'est produit avant R-S8 : `TabItem` n'était pas enregistré, la requête rendait un vide silencieux, et le code en concluait une barre d'onglets repliée qui n'existait pas.

---

## Coûts mesurés (poste de mesure, 03/09)

| Fenêtre | Contrôles | Arbre | Occultation |
|---|---:|---:|---:|
| Navigateur (film) | 1 | 27 ms | 0,1 ms |
| Navigateur (11 onglets) | 50 | 77 ms | 0,3 ms |
| Éditeur | 65 | 80 ms | 0,5 ms |
| App bureau d'assistant IA | 100 | 172 ms | 1,0 ms |
| Explorateur Windows | 108 | 215 ms | 0,9 ms |

Les douze rôles sont interrogés en **une seule traversée** de l'arbre (condition OR). Mesure sur l'explorateur : 973 ms en douze requêtes successives contre 116 ms en une : **8,4 fois moins**. La traversée dominait le coût, pas la lecture des propriétés.

Restreindre `roles` accélère encore.

---

## Vie privée

**Les noms de contrôles sont du contenu d'écran** : libellés de boutons, titres d'onglets, noms de fichiers. Ils sont rendus parce que c'est l'objet de la fonction.

Ils n'ont rien à faire dans un fichier de preuves commité. Les rapports du dépôt ne contiennent que des **comptes, des longueurs et des durées** : jamais un nom lu à l'écran.
