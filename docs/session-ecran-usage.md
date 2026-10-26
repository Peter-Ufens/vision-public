# Session écran : l'œil du labo sur l'ordinateur

**Statut :** livré R-S10, enrichi R-S13 (06/09/2026) · `scripts/session_screen.py`
**Ce que ça fait :** voir ce qui se passe sur le PC **d'un point A à un point B**, sur une durée.

> **Le cliché reste.** `screen_api` (contrat 1.1) ne bouge pas : les consommateurs et l'outil à la demande continuent de l'appeler. La session **s'ajoute** à côté.
> **Écran seulement.** Les caméras sont un **autre** projet ; rien ici ne les allume ni ne les prépare.

---

## 1. Lancer une session

```bash
# Dix minutes, cadence 1 Hz (le défaut)
python scripts/session_screen.py --duree 10m

# Trente minutes de film : une observation toutes les 5 s, ça suffit
python scripts/session_screen.py --duree 30m --profil film

# Quatre-vingt-dix secondes très serrées, pour voir un détail dans le temps
python scripts/session_screen.py --duree 90s --profil debug-avatar
```

**`--duree` est obligatoire.** Il n'existe pas de session infinie, pas de mode « jusqu'à nouvel ordre », pas de service qui démarre tout seul. La commande est le GO, sa fin est l'arrêt, et rien ne survit à la fermeture du terminal. C'est la décision I7, et le smoke vérifie qu'on ne peut pas la contourner.

**`Ctrl-C` arrête proprement** : le pied de session est écrit quand même.

---

## 2. Les profils

| Profil | Cadence | Coût CPU mesuré | Pour quoi |
|---|---|---:|---|
| `film` | 1 observation / 5 s | ~0,3 % | une séance ne bouge pas vite |
| `activite` *(défaut)* | 1 / s | **1,3 %** | fenêtres, apps déplacées, onglets |
| `debug-avatar` | 4 / s | **8,3 %** | voir un détail qui bouge vite |

`--cadence 2.5` force une cadence en secondes et prime sur le profil.

**Un tick ne capture aucun pixel** : ni image, ni OCR, ni modèle, ni VRAM. Il lit les écrans, les fenêtres et le contexte de la fenêtre active : 17 ms mesurés sur le poste de l'utilisateur.

---

## 3. Le cas qui motive tout ça : conscience de l'environnement PC

**Une fenêtre `cmd.exe` qui apparaît deux secondes puis se ferme n'est sur aucune photo.** Entre deux appels à la demande, elle est déjà partie. C'est exactement le genre de chose que l'utilisateur veut voir : une console qui surgit, un installeur qui s'ouvre, une fenêtre qui apparaît puis disparaît pendant qu'il regarde ailleurs.

Une session à 1 Hz la voit apparaître **et** la voit disparaître :

```jsonc
{"type":"session.diff","t_s":41.0,"changements":[
  {"quoi":"fenetre.apparue","exe":"cmd.exe","classe":"ConsoleWindowClass",
   "bbox":[820,410,1740,930],"ecran":1}]}

{"type":"session.diff","t_s":43.0,"changements":[
  {"quoi":"fenetre.disparue","exe":"cmd.exe","classe":"ConsoleWindowClass"}]}
```

Deux lignes, deux secondes d'écart : la fenêtre a vécu deux secondes, et on sait quand.

### Ce que la session sait voir

| Changement | Exemple |
|---|---|
| `fenetre.apparue` | une console, un installeur, une pop-up |
| `fenetre.disparue` | et sa durée de vie, par différence des horodatages |
| `fenetre.deplacee` | une app déplacée, redimensionnée |
| `fenetre.change_ecran` | passée sur l'autre moniteur |
| `fenetre.premier_plan` | l'attention a changé d'application |
| `document.change` | changement de page ou d'onglet |
| `ecrans.change` | un écran branché ou débranché |

---

## 3bis. Lire ce qui est écrit dans la fenêtre (R-S13)

Depuis R-S13, la session ne se contente plus de dire **où** : quand une fenêtre de type console apparaît, elle **saisit ses pixels immédiatement** et les lit ensuite.

**Pourquoi déclenché et pas périodique.** Dans vos sessions réelles, les fenêtres éphémères ont vécu **1,0 s**, **2,0 s** et **0,0 s**. Un échantillon riche toutes les 60 secondes les aurait toutes manquées. Une horloge plus rapide ne résout rien : seul un déclenchement sur l'événement le résout.

**Capturer vite, lire tranquillement.** Mesuré sur ce poste :

| Étape | Coût | Dans le chemin critique ? |
|---|---:|---|
| Détection (le tick) | ~18 ms | : |
| **Capture des pixels** | **20 à 36 ms** | **oui** |
| Lecture OCR (console ~900×500) | ~380 ms | non, différée |

La lecture se fait dans le temps mort entre deux ticks, et la file est **vidée avant la fin de session** : une fenêtre saisie n'est jamais oubliée.

> **Le premier coup était le plus lent.** Sans précaution, la toute première capture coûtait **367 ms** (chargement de cv2) au lieu de 25 : et c'est justement elle qui peut tomber sur une fenêtre d'une seconde. La session préchauffe donc au démarrage, où personne n'attend.

### Tester avec un `cmd` éclair

Pendant qu'une session tourne :

```
Win+R  →  cmd /c "echo BONJOUR VISION & timeout /t 2"  →  Entrée
```

La fenêtre vit deux secondes. La trace portera :

```jsonc
{"type":"session.diff","t_s":8.0,"changements":[
  {"quoi":"fenetre.apparue","exe":"cmd.exe","classe":"ConsoleWindowClass", ...}]}

{"type":"session.contenu","t_s":8.0,"quoi":"contenu.fenetre","exe":"cmd.exe",
 "lisible":true,"mots":3,"lignes":1,"capture_ms":22.4,"ocr_ms":381.0}
```

Au niveau `texte`, une clé `texte` s'ajoute avec les lignes lues.

### Les niveaux de contenu

| Niveau | Ce qui est écrit | Quand l'utiliser |
|---|---|---|
| `none` | rien : session strictement légère, aucun pixel | endurance, mesure |
| **`resume`** *(défaut)* | comptes seulement : mots, lignes, luminance, lisible ou non. **Pas le texte.** | usage courant |
| `texte` | les lignes lues | quand vous voulez savoir **ce qui était écrit** |

```bash
python scripts/session_screen.py --duree 10m                     # resume
python scripts/session_screen.py --duree 10m --contenu texte     # avec le texte
python scripts/session_screen.py --duree 10m --contenu none      # strictement légère
python scripts/session_screen.py --duree 10m --contenu-classes ConsoleWindowClass,#32770
```

**`texte` enregistre ce qui était écrit à l'écran.** Réservez-le aux sessions où vous en avez besoin, et considérez une rétention plus courte (48 h) que les 7 jours par défaut : `--retention-jours 2`.

---

## 3ter. Ce qui n'est pas lisible : streaming, DRM, jeux

**Un flux vidéo protégé sort noir à la capture.** Prime Video, Netflix : Widevine plus accélération matérielle. Ce n'est pas une panne, et ce n'est pas à contourner.

**Mesuré sur votre poste**, fenêtre Edge/Prime : luminance **0,3**, écart-type 3,7 → détectée noire. La trace dit alors :

```jsonc
{"type":"session.contenu","exe":"msedge.exe","lisible":false,
 "raison":"zone noire : rien a lire. Flux protege (DRM), ecran eteint ou scene tres sombre -- indiscernables sur ce seul signal.",
 "identite":{"pid":53096,"exe":"msedge.exe","classe":"Chrome_WidgetWin_1","demarre_depuis_s":611.1}}
```

**Aucune scène n'est jamais devinée.** Trois causes donnent la même image noire : flux protégé, écran éteint, scène très sombre : et la trace refuse de trancher entre elles sur ce seul signal.

### Le repli du technicien au téléphone

Quand l'écran ne montre rien de lisible, on peut toujours dire **quelle application tourne**. C'est ce que fait le champ `identite` : PID, exécutable, classe de fenêtre, durée depuis le démarrage du processus : la même information qu'un **Gestionnaire des tâches** ou qu'un `Get-Process`, prise à la source Windows.

```powershell
# L'équivalent manuel, si vous voulez vérifier
Get-Process msedge | Select-Object Id, ProcessName, StartTime
```

Un technicien qui ne voit pas votre écran vous dit quand même quelle application est ouverte. La session fait pareil.

### Les jeux : mesuré pendant Spider-Man 2

| Question | Réponse |
|---|---|
| La capture fonctionne-t-elle pendant un jeu ? | ✅ **oui** : 1922×1112 en 25 à 89 ms, `noir=false` |
| Voit-on la scène changer ? | ✅ luminance 57,9 → 88,9 entre deux échantillons |
| Lit-on le texte affiché ? | ✅ 0, 4 puis 40 mots selon le moment (HUD, dialogues) |
| Comprend-on « je fais une filature » ? | ❌ **non** : c'est une intention de jeu, pas une propriété d'une image |

**Nuance à connaître :** ces mesures ont été prises sur un jeu en mode **fenêtré**. Le plein écran **exclusif** peut se comporter autrement (défaut `BitBlt` documenté dans un document interne non publié) : non mesuré à ce jour.

---

## 3quater. Le décor des écrans dans le temps (R-S14)

Le déclenché répond à « une console a surgi ». Il ne répond pas à « que montraient mes écrans pendant ces trente minutes ? » : pendant un film, **aucune fenêtre ne bouge**, et la trace structurelle est au plus vide au moment où il se passe le plus de choses.

Le régime **riche périodique** échantillonne donc le décor de chaque écran à cadence lente.

```bash
python scripts/session_screen.py --duree 30m --riche            # un échantillon / minute
python scripts/session_screen.py --duree 30m --riche 30         # toutes les 30 s
python scripts/session_screen.py --duree 30m --riche --riche-ecrans 2   # écran 2 seulement
```

**`--riche` est opt-in, et ce n'est pas un oubli.** Le déclenché capture une console qui vient d'apparaître ; celui-ci capture **tout l'écran, en boucle**. Ce n'est pas la même promesse, donc ce n'est pas le même défaut.

### Ce qu'il coûte : 26 fois moins que prévu

Le plan R-S12 chiffrait un tick riche à **1584 ms** et en déduisait que 2,7 % des ticks structurels seraient perdus. Ce chiffre était celui de `get_vision_snapshot(depth=rich)`, dont l'essentiel part dans UIA : l'arbre du navigateur, l'inventaire des contrôles : et non dans les pixels.

Mesuré en assemblant directement les primitives :

| Étape | Coût |
|---|---:|
| Capture plein écran 2560×1440 | **33 ms** |
| Capture plein écran 1920×1080 | **22 ms** |
| Teinte, luminance, thème | 1,5 à 3,8 ms |
| Densité de contours (« y a-t-il du texte ? ») | 0,8 à 6 ms |
| **Les deux écrans, échantillon complet** | **~61 ms** |

Un échantillon par minute coûte donc **0,1 % d'un cœur**. Mesuré sur une session réelle : `ticks_sautes: 0`, coût de tick p50 inchangé à 19,6 ms.

### Ce qu'il écrit : des différences, comme le reste

```jsonc
// une ancre par écran, au premier échantillon
{"type":"session.decor","t_s":0.2,"quoi":"decor.initial","ecran":1,
 "luminance":21.1,"theme":"sombre","dominante":[0,0,0],"noir":false,
 "densite_texte":0.029,"texte_present":true}

// ensuite, seulement ce qui a bougé
{"type":"session.decor","t_s":60.2,"ecran":2,"changements":[
  {"quoi":"decor.luminance","de":179.2,"vers":171.5}]}
```

Un décor immobile n'écrit **rien du tout**. Les signaux suivis : `decor.luminance`, `decor.theme`, `decor.dominante`, `decor.noir`, `decor.texte_present`, `decor.densite_texte`.

**Les seuils sont mesurés, pas choisis.** Sur 36 échantillons, deux écrans : le plancher de bruit d'un écran immobile est **nul** (|Δ luminance| médiane 0,00, p90 0,20). Le seuil est fixé à 2,0, soit dix fois ce bruit.

### Lire la bande basse : `--contenu texte` seulement

Au niveau `resume` **aucun mot n'est lu** : la présence de texte est déduite d'une densité de contours (1 ms) au lieu d'un OCR (1000 ms). C'est un choix de coût **et** de vie privée.

```bash
python scripts/session_screen.py --duree 30m --riche --contenu texte
```

La bande basse (30 % de la hauteur **du moniteur**) est alors lue **en différé**, et la ligne n'est écrite **que si la lecture a changé**.

> **Ce n'est pas une bande de sous-titres, et R-S18 l'a montré en vrai.** Pendant une séance de 30 minutes avec une vidéo dans une fenêtre de moitié d'écran, les 63 lectures de l'écran 1 ont rendu **121 à 187 mots** chacune : le titre de la vidéo, le nombre de vues, les commentaires. Autrement dit l'interface du site, pas la scène.
>
> La raison est géométrique, pas une affaire d'OCR. La bande couvre les pixels 1008 à 1440 du **moniteur** ; dans une fenêtre de navigateur, la zone vidéo (et donc un sous-titre) se trouve bien plus haut, et à côté se trouvent d'autres fenêtres.
>
> **Ce que la bande donne réellement :** « il y a du texte en bas de cet écran, et il a changé ». C'est utile, et c'est moins que ce que le mot « sous-titres » laissait entendre.
>
> Même effet sur la luminance : moyennée sur tout le moniteur, celle de l'écran 1 n'a varié que de **63,8 à 74,6** en trente minutes de vidéo. Le signal de la fenêtre est noyé dans le reste du bureau.
>
> **Viser la scène demanderait de localiser la zone vidéo dans la fenêtre**, ce qu'aucun module ne sait faire aujourd'hui. C'est nommé au backlog, pas promis ici.

> **Pourquoi une ressemblance et pas une égalité.** Ma première version comparait les lectures à l'identique. La mesure l'a refusée : sur une image dont la luminance et la densité n'avaient pas bougé : donc un écran réellement immobile : l'OCR rendait des lectures **différentes** (ressemblance 0,80 à 1,00). Une égalité stricte aurait écrit une ligne pour du bruit de reconnaissance. Le seuil de 0,70 sépare ce bruit des vrais changements, mesurés entre 0,00 et 0,50.

**Attention au coût de ce niveau :** la bande d'un 2560×1440 se lit en 750 à 1400 ms. Sur une session à 1 Hz, cela fait sauter des ticks : le pied de session les **compte** (`ticks_sautes`, mesuré à 3 sur une session de 25 s à cadence riche 8 s). Un trou annoncé n'est pas un trou.

---

## 3quinquies. Rattraper une fenêtre lue trop tôt : le cas `Boo`

Lors du test C1, `BONJOUR VISION` et `Test un deux trois` ont été lus ; **`Boo !!!` ne l'a pas été** : la trace ne portait que la ligne du `timeout`.

**Le contrôle a mis l'OCR hors de cause.** Sur une image de synthèse, `Boo !!!` est lu parfaitement, seul (2 mots) comme à côté de la ligne de timeout (12 mots). La ligne n'était donc **pas dans l'image capturée** : une seule capture ne peut pas rattraper ce qu'elle n'a pas saisi.

Depuis R-S14, une fenêtre déclenchée est **capturée une seconde fois au tick suivant**, si elle existe encore. Les deux lectures sont réunies en **un seul** événement :

```jsonc
{"type":"session.contenu","exe":"cmd.exe","prises":2,"mots":10,
 "texte":["Boo !!!","Attendre 1 secondes, appuyez sur une touche pour continuer..."]}
```

Le rapprochement des lignes se fait sur une clé normalisée : l'OCR rend `continuer...` une fois et `continuer. |` la suivante, et ces deux-là sont la même ligne : c'est la variante la plus longue qui est gardée. `--relecture 0` désactive.

**Ce que cela ne garantit pas :** si la fenêtre disparaît avant le tick suivant, il n'y a pas de seconde chance. Le pied compte alors `relectures_perdues`.

---

## 4. Ce que la trace contient

Un fichier par session : `data/sessions/session-<date>-<heure>.jsonl`.

| Ligne | Quand | Contenu |
|---|---|---|
| `session.start` | au début | profil, cadence, durée prévue |
| `session.keyframe` | toutes les 60 s | l'**état complet** |
| `session.diff` | quand quelque chose bouge | seulement ce qui a changé |
| `session.end` | à la fin, **toujours** | durée réelle, ticks, changements, raison d'arrêt |

**Pourquoi des différences et pas des états.** Une session de 30 min à 1 Hz fait 1 800 observations, dont ~21 % seulement voient un changement (mesuré). Écrire 1 800 états complets serait illisible et gros pour rien.

**Pourquoi quand même une image-clé par minute.** Une trace purement différentielle est irrattrapable si on la rejoint en cours de route. Avec une ancre par minute, une session de 30 min reste lisible depuis n'importe lequel de ses trente points.

**Pourquoi le pied de session compte.** Sans lui, une trace coupée serait indiscernable d'une session qui n'a rien vu. Il dit ce qui s'est passé *et* pourquoi ça s'est arrêté : `duree_atteinte`, `interruption`, ou une panne nommée.

Ordre de grandeur mesuré : **1 Ko pour 12 secondes sans changement**. Une séance de 30 min reste très en dessous de 100 Ko.

---

## 5. Vie privée

| Règle | Contenu |
|---|---|
| **Aucun titre de fenêtre** | jamais enregistré, dans aucun mode |
| **Aucune image sur le disque** | une fenêtre capturée vit en mémoire le temps d'être lue, puis disparaît |
| Texte lu | seulement au niveau `--contenu texte`, jamais par défaut |
| **Hôtes réduits à une empreinte** | on voit qu'un site a **changé**, pas lequel. `--avec-hotes` les enregistre en clair, par choix explicite |
| **Écrans capturés en boucle** | **jamais par défaut** : `--riche` est opt-in. Sans lui, aucun écran n'est échantillonné : le smoke le vérifie |
| Sous-titres et texte d'écran | lus seulement au niveau `--contenu texte` ; à `resume`, la présence de texte vient d'une densité de contours, **aucun mot n'est reconnu** |
| **Local, gitignoré** | `data/sessions/` n'est jamais commité |
| **Rétention bornée** | 7 jours et 50 Mo par défaut, purge au démarrage (`--retention-jours`, `--taille-max-mo`) |

Une session voit plus longtemps qu'un cliché ; ses garde-fous sont donc plus stricts, pas égaux.

---

## 5bis. Éprouver la lecture : protocole de test

`BONJOUR VISION` ne prouve pas grand-chose : c'est court, en capitales, sans accent. Deux familles d'entrées plus dures.

### Mots aléatoires, depuis un terminal

Pendant qu'une session tourne avec `--contenu texte` :

```bash
python -c "import uuid,random,string; print(uuid.uuid4().hex[:8], ''.join(random.choices(string.ascii_letters,k=7)))"
```

Puis `Win+R` → `cmd /c "echo <les jetons tirés> & timeout /t 3"`. Un `timeout` de **3 s** plutôt que 1 : il laisse la place à la relecture du tick suivant.

### Extraits de notes : phrases anodines seulement

Afficher à l'écran une phrase tirée d'une note **publique** (un titre de section, une ligne technique), jamais une note personnelle.

> **Interdits, sans exception :** notes personnelles / dossiers protégés · identifiants · toute donnée personnelle recopiée dans le dépôt, dans un retour ou dans une trace commitée.  
> (Formulation neutre 18/10 : pas de chemins ni libellés de zone dans la vitrine.)

### Rappel mesuré (R-S14)

Six familles rendues en image de synthèse, relues par la même chaîne OCR que la session :

| Famille | Rappel |
|---|---:|
| Mots inventés (`Zorblak`, `brindzou`) | **1,00** |
| Français accentué (`Cadence recommandée`, `Résumé des événements`) | **1,00** |
| Extraits de notes (titres de section techniques) | **1,00** |
| Court, cas `Boo` (`Boo !!!`, `ok`, `42`) | 6/6 jetons réels |
| Chemins et commandes | 0,91 |
| UUID courts et hexadécimal | 0,75 |

**Rappel global : 43 jetons sur 45 (0,956).** Les deux échecs sont `0xDEADBEEF` et un chemin Windows complet : des jetons longs sans espace, où **une seule lettre fausse fait échouer le jeton entier**. C'est le piège déjà documenté sur les URL en R-S2b, pas une faiblesse nouvelle.

Ce qui est vérifié au passage : **les accents français passent** (10 jetons sur 10), et `Boo !!!` est lu sans difficulté dès qu'il est dans l'image.

---

## 6. Vérifier

```bash
python scripts/smoke_session_screen.py     # 57 contrôles, moins de 5 s
python scripts/smoke_session_contenu.py    # 59 contrôles du contenu déclenché
python scripts/smoke_session_decor.py      # 57 contrôles du riche périodique
```

Il vérifie notamment qu'on **ne peut pas** lancer une session sans durée, que le pied est écrit **même après un Ctrl-C**, qu'aucun titre ni URL ne se glisse dans la trace, et que ce lot ne touche ni à la caméra, ni au contrat photo, ni au plafond du journal d'events ponctuels.
