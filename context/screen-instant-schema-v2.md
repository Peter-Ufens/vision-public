# Schema `ScreenInstant` v2 (R-S2c, 2026-08-31)

**Producteur :** `src/screen_content.py` → `get_instant_screen(depth=…)`
**CLI :** `scripts/instant_screen.py --depth minimal|standard|rich`
**Remplace :** le schéma v1 (non publié), **sans le casser** (voir § 6)

Répond à la demande de l'auteur du 31/08 : `--profile film` donne l'application, le site et un titre approximatif ; il veut **tout ce qui est visible**, structuré : onglets, favoris, titre exact, recommandations, sous-titres, barre des tâches, heure, couleurs.

---

## 1. La décision qui structure ce lot

**La structure vient d'UIA, pas de l'OCR.** C'est un écart assumé à la lettre du brief, qui demandait « OCR chrome → tabs[] + bookmarks[] ».

Mesures faites sur la fenêtre Brave de l'utilisateur pendant ce lot :

| Voie | Résultat | Coût |
|---|---|---|
| **UIA** | 12 onglets avec leur titre **complet** (100 caractères pour le premier) | **58 ms** |
| **UIA** | 419 liens de page avec nom accessible et bbox | 59 ms |
| OCR de la bande chrome | le libellé **tronqué** affiché dans l'onglet, fragmenté | ~300 ms |

L'OCR donnerait ici un résultat à la fois **plus lent et moins bon**. Il garde une place, mais une seule : lire ce qui n'existe **que** en pixels, c'est-à-dire les sous-titres gravés dans une vidéo. C'est le seul endroit où ce module l'appelle.

Il reste en **repli** si l'arbre d'accessibilité ne répond pas : le champ porte alors `source: "ocr-repli"`.

**Chaque champ porte sa provenance.** Un consommateur doit pouvoir savoir si une valeur a été lue dans l'arbre (`uia`), devinée sur des pixels (`ocr`), ou prise à l'horloge système.

---

## 2. Profondeurs

| `--depth` | Contenu ajouté | Coût mesuré |
|---|---|---|
| `minimal` | Le socle R-S2a : application, site, titre de fenêtre, captures, détection de noir | ~60 ms |
| `standard` | + URL complète, titre nettoyé, onglets, favoris, couleurs par région | **~42 ms à chaud** |
| `rich` | + liens et recommandations, sous-titres incrustés, barre des tâches, heure | **~2,0 s** |

`standard` mesuré **plus rapide que `minimal`** dans un cas : `minimal` payait l'initialisation UIA à froid (196 ms) que `standard` trouvait déjà chaude. Les deux coûtent en réalité quelques dizaines de millisecondes une fois le process lancé.

La profondeur est **orthogonale au profil** : le profil dit *pourquoi* on regarde (film, agent, full), la profondeur dit *jusqu'où* on descend.

---

## 3. Nouveautés v2, par section

### 3.1 En-tête

```json
{ "v": 2, "phase": "R-S2c", "depth": "rich", "t_wall_ms": …, "elapsed_ms": 1986.39 }
```

### 3.2 `screens[].rich`

Présent dès `standard`. Absent en `minimal`.

```json
{
  "depth": "rich",
  "sources": { "tabs": "uia", "colors": "pixels", "subtitles": "ocr" },
  "page": { "title": "…", "suffixes_retires": ["Brave", "YouTube"], "source": "window.title" },
  "document": { "url": "https://…", "url_raw": "…", "host": "…",
                "scheme_suppose": true, "source": "uia" },
  "video": { "title": "…", "source": "window.title" },
  "browser": { },
  "overlay_text": { },
  "subtitles": ["…"],
  "colors": { "chrome": { }, "client": { } }
}
```

**`page.title`** vient du **titre de fenêtre**, pas de l'OCR ni du libellé d'onglet. Mesuré : le titre de fenêtre fait 100 caractères là où l'onglet n'en montre qu'une vingtaine. Windows ne le tronque pas. C'est donc la meilleure source pour le titre exact d'une vidéo, et elle coûte zéro.

`suffixes_retires` liste ce qui a été enlevé (`Brave`, `YouTube`…). Le séparateur n'est pas toujours le même caractère : Brave utilise un tiret cadratin, Chrome un tiret simple. Le nettoyage travaille sur la **classe de caractères**, pas sur une liste écrite à la main : un piège rencontré sur ce poste.

**`document.url`** est l'URL complète. **`scheme_suppose: true`** signale que `https://` a été ajouté : Chromium masque `https://` quand l'omnibox n'a pas le focus, mais affiche `http://` en clair pour un site non sécurisé (c'est un avertissement, il ne l'élide jamais). L'absence de schéma **veut donc dire https**. La déduction est solide, le drapeau la signale quand même : un consommateur qui a besoin de certitude doit pouvoir distinguer ce qui a été lu de ce qui a été déduit.

### 3.3 `rich.browser`

```json
{
  "available": true,
  "omnibox_bbox": [2969, 228, 3905, 256],
  "content_top": 291,
  "tabs": [ { "title": "…", "bbox": […], "source": "uia" } ],
  "tabs_reason": "aucun TabItem expose : barre d'onglets masquee ou verticale repliee",
  "bookmarks": [ { "title": "…", "bbox": […], "source": "uia" } ],
  "links_count": 6,
  "links_reason": "439 liens dans l'arbre, aucun visible dans le viewport",
  "recommendations": [ { "title": "…", "channel": "…", "views": 46000,
                         "duration_s": 725, "bbox": […], "source": "uia" } ],
  "sidebar_detected": true,
  "chrome_errors": 0, "page_errors": 0
}
```

**Les frontières ne sont pas des constantes.** `content_top` est déduit de contrôles réels : bas des `TabItem`, bas du bbox de l'omnibox, bas de la rangée de favoris. La barre de favoris est identifiée comme la rangée de boutons la plus fournie dans les 70 px sous l'omnibox.

**`tabs_reason` et `links_reason` distinguent trois causes différentes de « zéro ».** Sans cela, une liste vide ne veut rien dire :

| Constat mesuré sur ce poste | Cause réelle |
|---|---|
| 0 `TabItem`, mais 49 boutons présents | Brave en **onglets verticaux repliés** : il n'expose aucun `TabItem`. La même fenêtre en onglets horizontaux en exposait 12. |
| 6 liens visibles sur **439** dans l'arbre | UIA marque hors écran ce qui est **défilé hors du viewport**. 428 liens l'étaient. C'est correct, pas une panne. |
| 0 partout, `errors` > 0 | Arbre indisponible pendant l'appel. |

**`recommendations`** : la colonne de droite est repérée **géométriquement** (liens nommés, visibles, dans le tiers droit sous le contenu). Rien de spécifique à un site dans la détection. Seule l'**interprétation** des noms est propre à YouTube, dont les noms accessibles encodent titre, chaîne, vues et durée dans une seule chaîne. Les champs non trouvés sont **absents**, jamais devinés : sur une interface dont la langue changerait, on rendrait un titre seul plutôt qu'une structure inventée.

### 3.4 `rich.overlay_text` et `rich.subtitles`

```json
{ "available": true, "lines": [ { "text": "…", "bbox": […] } ],
  "elapsed_ms": 876.66, "variant": "psm6-x3",
  "region_bbox": […], "words": 42, "source": "ocr" }
```

Le seul contenu que l'arbre d'accessibilité ne peut pas donner. Lu sur la **bande basse de la zone client** (30 %), là où vivent les sous-titres gravés.

**Réglage `psm6-x3`**, issu du bake-off R-S2b, qui divisait le taux d'erreur par 4,5 sur du texte d'interface. Ce réglage est **local à cette fonction** : `ocr_screen` et `screen_content` gardent le leur tant qu'il n'a pas été confirmé sur des captures réelles (un document interne non publié § 5). On ne généralise pas un réglage validé sur du synthétique.

Si la région est noire (flux protégé), l'OCR **s'abstient** avec sa raison plutôt que de rendre du bruit.

### 3.5 `system` et `taskbar` (niveau racine)

```json
"system": { "time": "15:26:32", "date": "2026-08-31", "weekday": "Monday",
            "iso": "…", "tz": "…", "epoch_ms": …, "source": "horloge systeme" },
"taskbar": { "available": true, "bars": [
    { "class": "Shell_TrayWnd", "monitor_index": 1, "bbox": […],
      "apps": [ { "name": "…", "bbox": […], "source": "uia" } ],
      "apps_count": 32, "system_buttons_count": 10,
      "tray_text": [ { "text": "…", "bbox": […] } ], "errors": 0 } ] }
```

**L'heure vient de l'horloge Python**, exacte par construction. Elle n'est **pas** lue par OCR sur la barre des tâches : ce serait plus lent, faillible, et sans aucun gain. Le texte du widget (météo, ville) est rendu à côté dans `tray_text`, lu par UIA : également sans OCR.

**Les applications sont triées par une classe de la plateforme**, pas par une liste de noms : Windows 11 expose les boutons d'application avec `Taskbar.TaskListButtonAutomationPeer`, les boutons système avec `ToggleButton`. Même principe que le filtre d'overlays de R-S2a.

Les **deux** barres d'un poste multi-écran sont rendues : `Shell_TrayWnd` sur l'écran principal, `Shell_SecondaryTrayWnd` sur les autres, chacune rattachée à son écran.

### 3.5bis `cursor` (niveau racine, R-S4a)

Présent seulement avec `--with-cursor` ou `--cursor-region N`. **Il n'y a qu'un curseur**, il est donc à la racine et non par écran.

```json
"cursor": {
  "available": true, "reason": "", "source": "win32",
  "x": 2675, "y": 1226,
  "visible": true,
  "monitor_index": 2,
  "window": { "hwnd": 0, "class_name": "…", "pid": 0, "exe_name": "…" },
  "region": { "bbox": [2595, 1146, 2755, 1262], "size": 160,
              "width": 160, "height": 116, "clamped": true,
              "monitor_index": 2, "source": "autour du curseur" },
  "capture": { "width": 160, "height": 116, "mean_luma": 222.09,
               "looks_black": false, "elapsed_ms": 7.95 },
  "elapsed_ms": 0.1346
}
```

| Champ | Sens |
|---|---|
| `x`, `y` | Coordonnées écran absolues, process DPI per-monitor |
| `visible` | **Le champ qui décide si la position vaut quelque chose** (voir ci-dessous) |
| `monitor_index` | Écran portant le point, par **appartenance à un rectangle**. `null` est une réponse valide. |
| `window` | Fenêtre de plus haut niveau **sous le pointeur** : répond à « il pointe sur quoi », pas seulement « il pointe où » |
| `region` | Carré de `size` px centré sur le pointeur, **découpé** aux bornes de son écran |
| `clamped` | Vrai quand le découpage a réduit la région (curseur près d'un bord) |
| `capture` | Présente seulement si `region` l'est. Mêmes champs qu'une capture ordinaire. |

**`visible` n'est pas un détail.** Windows conserve une position de curseur même quand le pointeur est **caché** : lecteur vidéo en plein écran, jeu, saisie tactile. La position est alors celle du dernier déplacement et **ne dit rien de l'attention**. Un consommateur qui l'ignorerait conclurait que l'utilisateur regarde un coin d'écran qu'il ne regarde pas. Quand `visible` est faux, `hidden_reason` dit pourquoi.

**`monitor_index: null` est une réponse, pas une panne.** Sur ce poste, la zone `x > 2560` et `y < 182` appartient au bureau virtuel mais à **aucun écran physique** : les deux écrans ne sont alignés ni en x ni en y. Un index plausible et faux serait pire qu'une absence.

**Le découpage ne déplace pas la région.** Faire glisser le carré pour lui garder sa taille près d'un bord éloignerait son centre du curseur, donc de ce que l'utilisateur regarde. On préfère une région plus petite mais réellement centrée, et on le signale par `clamped`.

**Lecture seule, par construction.** Ce bloc lit la position. Il ne la déplace pas, ne clique pas, n'envoie aucune entrée : aucun `SetCursorPos`, `mouse_event` ni `SendInput` n'existe dans le module. L'actuation n'est pas dans ce dépôt (ADR-0007).

**Pas de suivi en arrière-plan.** Conformément à la décision I7, le curseur est lu **à l'appel**, comme le reste du snapshot. Entre deux appels, rien ne tourne : ni boucle, ni thread, ni daemon.

Latences mesurées : `GetCursorPos` 0,0007 ms · `GetCursorInfo` 0,0008 ms · fenêtre sous le pointeur 0,0027 ms · bloc complet dans le snapshot **0,13 à 0,19 ms**. Le budget du lot était de 5 ms.

### 3.6 `rich.colors`

```json
{ "chrome": { "mean_rgb": [235.6, 235.2, 236.0], "luma": 235.3,
              "dominant_rgb": [224, 224, 224], "theme": "clair" },
  "client": { "mean_rgb": [40.1, 41.1, 41.0], "luma": 40.8,
              "dominant_rgb": [32, 32, 32], "theme": "sombre" } }
```

Mesuré sur la page de l'utilisateur : bandeau clair à 235, zone vidéo sombre à 41. Le champ `theme` est un simple seuil sur la luminance, utile pour adapter un affichage ou choisir un prétraitement OCR.

---

## 4. Robustesse : l'arbre d'un navigateur vivant est volatile

Trois incidents rencontrés pendant ce lot, tous sur la même fenêtre :

1. **12 onglets puis 0** à une minute d'intervalle, sans que l'utilisateur change de page.
2. **419 liens puis 0**, puis 439 de nouveau.
3. **`COMError: pointeur non valide`** en pleine itération, quand une autre application a repris la main.

Conséquences inscrites dans le code :

- **Jamais de racine UIA en cache entre deux appels.** Un élément obtenu par `ElementFromHandle` devient invalide dès que l'arbre est reconstruit.
- **Chaque accès à un élément est protégé individuellement**, pas seulement l'appel global. `screen_uia.collect` rend ce qu'il a pu collecter et **compte les pannes** (`errors`), au lieu de tout perdre.
- **Chaque bloc est indépendant** : si la barre des tâches échoue, les onglets restent.
- **Un bloc absent porte sa raison.** Jamais de silence.

---

## 5. Vie privée

Ce schéma décrit la structure la plus intrusive du projet à ce jour. Le retour contient :

- l'URL complète et le titre de chaque page,
- **tous les onglets ouverts** avec leur titre complet,
- **tous les favoris**, dossiers compris,
- le texte incrusté visible à l'écran,
- la liste des applications de la barre des tâches,
- le texte du widget système (ville, météo).

Règles :

- Destiné à l'écran de l'utilisateur. **Ne jamais coller une sortie réelle** dans un fichier commité, un ticket ou une preuve.
- `--save` écrit dans `captures/`, gitignoré.
- Le smoke n'affiche que des **longueurs et des formes**, jamais le contenu.
- Aucun réseau : tout est local.

---

## 6. Compatibilité v1

| Point | Statut |
|---|---|
| Champs v1 (`monitors`, `screens[]`, `regions`, `captures`, `ocr`, `vlm`, `notes`) | **Inchangés** |
| `v` | passe de `1` à `2` |
| `phase` | passe de `"R-S2a"` à `"R-S2c"` |
| `depth`, `screens[].rich`, `system`, `taskbar` | **Ajouts** |
| `cursor` | **Ajout R-S4a**, présent seulement sur demande explicite |
| `minimal` | équivaut au comportement v1 (aucune section `rich`) |

Un consommateur v1 qui ignore les champs inconnus continue de fonctionner sans modification.

---

## 7. Limites connues au 31/08

| Limite | Détail |
|---|---|
| **Scénario YouTube non joué** | Les onglets, favoris, recommandations, sous-titres et couleurs ont été validés sur une **page réelle**, mais **pas** sur une page YouTube. Le parseur de noms accessibles YouTube est testé sur des chaînes synthétiques écrites à la main. |
| **Onglets non vérifiés en conditions nominales** | Brave était en onglets verticaux repliés pendant les tests finaux : 0 `TabItem`. Le chemin a été mesuré plus tôt dans la session (12 onglets, titres complets) mais pas rejoué en bout de chaîne. |
| Recommandations hors YouTube | Le titre est rendu brut, sans chaîne ni vues. Normal : rien à parser. |
| Bande de sous-titres | Bas de la zone client à 30 %, heuristique fixe, non ajustée par lecteur vidéo. |
| Onglets verticaux | Aucun repli implémenté pour lire une barre d'onglets verticale. Signalé par `tabs_reason`, pas contourné. |

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

