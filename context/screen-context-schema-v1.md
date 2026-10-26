# Schema `screen.context` v1 (R-S1b, 2026-08-31)

**Type d'event :** `screen.context` · **Source :** `vision.screen` · **Contrat :** events v1 (ADR-0005)
**Producteur :** `src/screen_context.py` (`get_screen_snapshot`) · **Transport :** JSONL local + PUB ZMQ `tcp://127.0.0.1:5556`
**Consommateurs prevus :** des agents ou scripts locaux qui lisent l'ecran avant d'agir

Ce document decrit le **payload**. L'enveloppe est celle de tous les events du projet, definie dans `src/events_v1.py` et cannonisee par ADR-0005. **Il n'existe pas de second schema d'events.**

---

## 1. Enveloppe (rappel, non specifique a l'ecran)

```json
{
  "v": 1,
  "id": "evt_...",
  "t_capture_ns": 0,
  "t_emit_ns": 0,
  "source": "vision.screen",
  "type": "screen.context",
  "conf": 0.95,
  "ttl_ms": 5000,
  "payload": { }
}
```

| Champ | Note ecran |
|---|---|
| `source` | Toujours `vision.screen`, meme si le bus a ete cree pour la webcam |
| `conf` | Derive de `payload.confidence` : high 0,95 · medium 0,80 · low 0,45 · unknown 0,0 |
| `ttl_ms` | **5000**, contre 300 a 500 ms pour les events webcam. Un contexte ecran reste vrai tant que l'utilisateur ne change ni de fenetre ni de page ; un visage detecte se perime en quelques centaines de millisecondes. |
| `t_capture_ns` / `t_emit_ns` | `time.perf_counter_ns()`. Utiles pour des ecarts, **pas** pour dater une preuve : voir `payload.t_wall_ms`. |

---

## 2. Payload

```json
{
  "t_wall_ms": 1756630000000,
  "phase": "R-S1b",
  "monitor_index": 1,
  "process": {
    "exe_name": "msedge.exe",
    "exe_path": "C:\\Program Files (x86)\\...\\msedge.exe",
    "pid": 1234,
    "category": "browser",
    "label": "Microsoft Edge",
    "known": true,
    "document_source": "url"
  },
  "document": {
    "url_raw": "emploi.example.org/offres/recherche/detail/...",
    "host": "emploi.example.org",
    "path": "/offres/recherche/detail/...",
    "scheme": null,
    "control_class": "OmniboxViewViews",
    "via": "omnibox-class"
  },
  "window": {
    "hwnd": 330470,
    "title": "Offre d'emploi ...",
    "rect": [0, 0, 2560, 1440],
    "rect_visible": [7, 0, 2553, 1440],
    "is_foreground": true
  },
  "target_requested": "Postuler",
  "target": {
    "name": "Postuler",
    "role": "Button",
    "bbox": [1204, 812, 1338, 852],
    "in_window": true,
    "method": "exact",
    "control_class": "btn btn-primary"
  },
  "confidence": "high",
  "unknown": false
}
```

### 2.1 Champs de tete

| Champ | Type | Sens |
|---|---|---|
| `t_wall_ms` | int | **Horloge murale** en millisecondes epoch. C'est le seul champ qui permet de relier un event a une capture de preuve ou a une ligne de log. `t_capture_ns` ne le permet pas. |
| `phase` | str | Phase du plan ayant produit l'event. Sert a reperer les events d'une version anterieure dans un journal. |
| `monitor_index` | int \| null | Index 1-based du moniteur portant le plus grand morceau de la fenetre, par **intersection d'aires**. `null` si la fenetre n'est sur aucun ecran. |

### 2.2 `process` : niveau 1 de l'identite

Toujours present si une fenetre a ete resolue. Vient de Win32 et est **deterministe**.

| Champ | Type | Sens |
|---|---|---|
| `exe_name` | str | Nom de l'executable. **La cle d'identification.** Jamais le titre de fenetre, qui change avec la langue, la version et le contenu. |
| `exe_path` | str | Chemin complet, ou chaine vide si le processus est protege (acces refuse). |
| `pid` | int | |
| `category` | str | `browser`, `mail`, `editor`, `terminal`, `office`, `pdf`, `files`, `settings`, `chat`, `media`, `agent`, `unknown` |
| `label` | str \| null | Nom lisible, ou `null` si l'executable est inconnu de la table |
| `known` | bool | L'executable figure dans `context/screen-app-categories.json` |
| `document_source` | str \| null | `"url"`, `"title"` ou `null`. Voir 2.3. |

### 2.3 `document` : niveau 2 de l'identite

**C'est la raison d'etre de R-S1b.** un site d'emploi, chat-ia.example et Outlook web sont le **meme `msedge.exe`** : le niveau processus ne peut pas les distinguer. `null` quand aucune adresse n'a pu etre lue.

| Champ | Type | Sens |
|---|---|---|
| `url_raw` | str | La valeur exacte rendue par UIA, sans retouche |
| `host` | str | Domaine. **C'est le champ sur lequel un consommateur doit brancher sa logique**, pas `url_raw`. |
| `path` | str | Chemin |
| `scheme` | str \| null | `null` est frequent et normal : Chromium **elide `https://`** quand l'omnibox n'a pas le focus (mesure au spike : 38 caracteres, aucun `://`). On ne fabrique pas le schema absent. |
| `control_class` | str | Classe du controle lu (`OmniboxViewViews`, `BraveOmniboxViewViews`...) |
| `via` | str | `"omnibox-class"` = la classe du controle contient "Omnibox", signal sur. `"value-heuristic"` = l'application est declaree navigateur et la valeur a une forme d'adresse, signal plus faible. |

**Pourquoi `document_source` compte.** Trois cas differents produisent `document: null`, et les confondre serait une faute :

| `document_source` | `document` | Sens reel | `unknown` |
|---|---|---|---|
| `"url"` | `null` | On attendait une adresse et on ne l'a pas eue. **Trou d'information.** | `true` |
| `"title"` | `null` | Application sans adresse. Le titre renseigne, faiblement. | `false` |
| `null` | `null` | L'application n'a pas de document (terminal). **Rien a lire, et on le sait.** | `false` |

### 2.4 `window`

| Champ | Type | Sens |
|---|---|---|
| `hwnd` | int | Handle. Valable pour la session en cours seulement. |
| `title` | str | Titre. Indicatif : ne jamais s'en servir pour identifier l'application. |
| `rect` | [x, y, x2, y2] | `GetWindowRect`, qui **inclut la bordure de redimensionnement invisible** du DWM (7 px de chaque cote en pratique) |
| `rect_visible` | [x, y, x2, y2] | Bords reellement peints (`DWMWA_EXTENDED_FRAME_BOUNDS`). **C'est ce rectangle qui correspond a ce que l'utilisateur voit**, donc celui a utiliser pour tout raisonnement "au pixel pres". |
| `is_foreground` | bool | |

### 2.5 `target` : la cible d'action

`null` par defaut. Rempli seulement si `target_requested` etait pose **et** qu'un controle a ete localise et valide.

| Champ | Type | Sens |
|---|---|---|
| `name` | str | Nom accessible reellement porte par le controle (peut differer du libelle demande) |
| `role` | str | `Button`, `Hyperlink`, ... |
| `bbox` | [x, y, x2, y2] | Coordonnees ecran absolues. **Garanties : aire non nulle, intersecte un ecran physique, controle non marque hors ecran.** |
| `in_window` | bool | Le bbox est entierement contenu dans `window.rect_visible` |
| `method` | str | `"exact"` (nom identique, casse ignoree) ou `"substring"` (repli) |
| `control_class` | str | Dans le contenu web Chromium, ce sont les **classes CSS du DOM**. Signal utile pour R-S2 et R-Sem. |

> **Regle non negociable.** `target_requested` pose et `target` a `null` veut dire **le controle n'a pas ete trouve**. Ce n'est pas une erreur : c'est une abstention. Aucun bbox n'est jamais devine, approche ou interpole. C'est la propriete qui rend ce contrat utilisable par un agent qui clique, et elle sert directement le jalon **G3** (100 % d'abstention correcte).

### 2.6 `confidence` et `unknown`

| `confidence` | `conf` | Quand |
|---|---|---|
| `high` | 0,95 | Adresse lue via `omnibox-class`, **ou** cible demandee et trouvee (deux sources concordantes) |
| `medium` | 0,80 | Adresse lue par heuristique, **ou** application sans document attendu |
| `low` | 0,45 | Identite par titre seul |
| `unknown` | 0,0 | UIA indisponible, **ou** adresse attendue et non lue |

`unknown: true` veut dire : **il y avait quelque chose a savoir et nous ne l'avons pas su.** Un terminal sans document ne met pas `unknown`, parce qu'il n'y avait rien a lire.

Un consommateur qui agit doit traiter `unknown: true` comme un refus d'agir. Confondre "rien vu" et "pas pu regarder" est exactement le mode d'echec qui fait cliquer au mauvais endroit.

---

## 3. Regle d'emission

L'event n'est emis **que si le contexte a change** depuis le dernier appel du process appelant (`screen_context.context_signature`).

Signature = `exe_name` · `document.host` · `document.path` · `monitor_index` · `window.hwnd` · `target_requested` · presence de `target`.

**Hors signature, volontairement : le bbox de la cible.** Sinon un simple defilement de page reemettrait en boucle. La **presence** de la cible en fait partie : un bouton qui apparait est un changement, pas un detail.

A rediscuter en R-S5 si un consommateur a besoin du suivi de position.

Il n'y a **aucune boucle et aucun daemon** : la vision ecran est un outil a la demande (decision I7 du 31/08). C'est l'appelant qui decide quand regarder.

---

## 4. Vie privee

Le payload contient **l'URL complete et le titre de fenetre**. Ces donnees sont personnelles.

- Le journal `data/events/vision_events.jsonl` est **gitignore** et plafonne a 2000 lignes.
- Le PUB ZMQ est lie a `127.0.0.1` : rien ne sort de la machine.
- **Ne jamais** coller un payload reel dans un fichier commite, une capture de preuve ou un ticket. Les exemples de ce document sont fabriques.

---

## 5. Limites connues au 31/08

| Limite | Detail |
|---|---|
| **Non valide sur un site d'emploi** | Toutes les mesures ont ete prises sur Brave (meme moteur Chromium qu'Edge) sur une page sans bouton d'action. Le cas V1 (bouton Postuler) et le cas V8 (page sans bouton) restent a executer par l'utilisateur. |
| Firefox | Moteur different, lecture d'adresse non testee. Le module s'abstiendra plutot que de deviner. |
| Outlook client lourd | Non teste. Seul Outlook **web** est couvert par le raisonnement navigateur. |
| Applications Electron | Un arbre riche coute plus cher : 62 ms mesures sur un editeur Electron avec recherche de cible, contre 23 ms sur Brave. |
| `hwnd` non persistant | Ne pas conserver un `hwnd` d'une session a l'autre. |

---

## 6. Latences mesurees (31/08, poste de mesure)

| Appel | p50 | p95 |
|---|---:|---:|
| `get_screen_context()` (Win32 seul, sans UIA) | 0,70 ms | 0,84 ms |
| `get_screen_snapshot()` sans cible | 6,53 ms | 7,20 ms |
| `get_screen_snapshot(target=...)` cible trouvee | 24,90 ms | 26,53 ms |
| `get_screen_snapshot(target=...)` cible absente | 22,54 ms | 24,28 ms |
| Idem sur application Electron (editeur) | 62,30 ms | 62,94 ms |

Pour comparaison, une seule capture d'ecran 2560x1440 coute **30,33 ms** avant tout OCR (spike R-S0). Le contexte complet sans cible coute donc **moins qu'une capture**.

L'optimisation de ces chiffres est le sujet de **R-S4**, pas de R-S1b. Aucune n'a ete tentee ici au-dela d'eviter les recalculs inutiles.

---

## Point d'entrée public (R-S5)

Ce schéma est produit par le contrat public `src/screen_api.py`, figé en version **1.0** :

```python
from screen_api import get_vision_snapshot, get_context_snapshot, describe_contract
```

| Fonction | Rend | Schéma |
|---|---|---|
| `get_context_snapshot()` | contexte seul, ~7 ms, sans cv2 | `screen-context-schema-v1.md` (`v: 1`) |
| `get_vision_snapshot()` | vision complète, pixels compris | `screen-instant-schema-v2.md` (`v: 2`) |

`describe_contract()` rend les versions, les profondeurs et les garanties à l'exécution.

