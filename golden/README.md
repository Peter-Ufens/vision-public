# Golden set : jeu de régression local (R-S2b)

**Ce dossier est volontairement vide sous git.** Seuls ce README et
`ocr-samples/.gitkeep` sont suivis. **Aucune capture, aucun JSON de vérité ne
doit être commité.**

## Pourquoi

Le golden set est fait de captures de l'écran de l'utilisateur : titres d'onglets,
objets de courriels, offres d'emploi, adresses visitées. Ce sont des données
personnelles, et parfois celles de tiers (noms de recruteurs, expéditeurs).

Le dépôt est privé, mais privé n'est pas local. Ces images restent ici.

Règles `.gitignore` en place (vérifiées) :

```
golden/*
!golden/README.md
!golden/ocr-samples/
golden/ocr-samples/*
!golden/ocr-samples/.gitkeep
```

Vérification, qui ne doit jamais lister autre chose que ces deux fichiers :

```bash
git add --dry-run golden/
```

## À quoi ça sert

Deux usages, tous deux des mesures de **non-régression** :

1. **Bake-off OCR** (`scripts/bakeoff_ocr.py`) : comparer des moteurs et des
   réglages sur du texte réellement affiché, pas sur du synthétique. Les
   chiffres actuels viennent d'un corpus synthétique et **doivent être
   reconfirmés ici** avant d'être inscrits dans le code.
2. **Régression R-S3** : rejouer les mêmes images après chaque changement,
   pour vérifier qu'une optimisation de latence n'a rien cassé.

## Comment remplir `ocr-samples/`

Chaque échantillon est une paire de fichiers portant le même nom.

**1. L'image.** Le plus simple est de laisser `instant_screen.py` la produire,
puis de la déplacer :

```bash
python scripts/instant_screen.py --monitor 1 --save captures/
```

Choisir le PNG voulu dans `captures/` (les régions `chrome` sont les plus
utiles pour l'OCR) et le copier dans `golden/ocr-samples/` sous un nom parlant.

> **Le nom de l'échantillon finit dans un rapport.** Les métriques sont
> publiées par `nom d'échantillon`, et ces rapports vont dans `preuves/` (local,
> hors git). Un nom comme `film-youtube-01` révèle quel site était ouvert.
> Préférer des noms neutres : `film-01`, `film-02`, `ui-01`. Le contenu
> reste local ; seul le nom voyage.

**2. La vérité terrain**, un `.json` du même nom, où l'utilisateur écrit ce qui est
**réellement lisible** sur l'image :

```json
{
  "expected_lines": [
    "Mickey 17 (2025) - Lecture",
    "Boite de reception (3)"
  ],
  "notes": "bandeau Brave, theme sombre, 3 onglets"
}
```

Deux points qui comptent :

- Écrire ce qui est **visible**, pas ce qu'on sait être là. Si un titre est
  tronqué par la largeur de l'onglet, écrire la version tronquée.
- N'inclure que ce qui est nécessaire à la mesure. Inutile de transcrire une
  boîte de réception entière : deux ou trois lignes représentatives suffisent,
  et c'est autant de données personnelles en moins sur le disque.
- **Mots courts, un mot par token.** Le rappel exige une correspondance exacte
  mot à mot. Un token de 30 caractères (phrase ou URL collée) fait échouer tout
  le mot pour une seule faute. Préférer des libellés de 4-15 caractères :
  `"Postuler"`, `"Boite"`, `"Lecture"`. Les URL se lisent par UIA, pas par OCR.

## Scénarios utiles

| ID | Scénario | Ce qu'il éprouve |
|---|---|---|
| `film-*` | Brave ou Edge, vidéo en pause, bandeau visible | Titres d'onglets, thème sombre |
| `ui-*` | Bandeau avec boutons et libellés d'interface | Petit texte, le cas le plus dur |
| `emploi-*` | un site d'emploi, offre avec bouton Postuler | Cible d'action des agents |
| `edge-*` | chat-ia.example, page d'export | Scénario d'export |
| `drm-*` | Netflix ou Prime en lecture | Doit être **noir** : vérifie que l'abstention fonctionne |

`emploi-*` et `edge-*` servent les agents qui agissent. `film-*` et `ui-*` servent
la vision générale, qui est la priorité de l'auteur. Les deux comptent.

## Lancer la mesure

Deux outils, deux questions différentes.

**Choisir un réglage** (compare des moteurs et des variantes entre eux) :

```bash
python scripts/bakeoff_ocr.py --report
```

**Détecter une régression** (rejoue la configuration de production contre des
seuils, et tranche la question du réglage ×3) :

```bash
python scripts/replay_golden.py --report
```

Codes de sortie du rejeu : `0` si le golden est vide **ou** si les seuils
sont tenus, `1` si une régression est mesurée. `--soft` force le `0`.

**Golden vide n'est pas une erreur.** Le harnais fonctionne, il attend des
échantillons. C'est l'état normal tant que rien n'a été déposé ici.

### Le réglage ×3 ne sera appliqué que sur preuve

Le bake-off R-S2b recommandait `tess-psm6-x3` (taux d'erreur divisé par 4,5),
mais sur un corpus **synthétique**. La production ne changera de réglage que
si **au moins deux échantillons réels** le confirment, et que la candidate
tient les seuils de un document interne non publié § 3. Tant que ce n'est
pas le cas, `replay_golden.py` répond `appliquer : NON` avec sa raison.

C'est trois ou quatre captures qui débloquent ce point.

## Scénarios de validation

```bash
python scripts/run_validation_scenarios.py
python scripts/run_validation_scenarios.py --v8    # page SANS bouton Postuler
python scripts/run_validation_scenarios.py --v1    # page AVEC bouton Postuler
```

Le runner observe l'écran tel qu'il est et rend `pass`, `fail`, `skip` ou
`unknown`. Il ne bascule aucune fenêtre et ne clique nulle part.

**`skip` et `unknown` ne sont pas des succès.** Sur une page d'offre d'emploi,
trouver un bouton « Postuler » est un succès (V1) ou un faux positif grave
(V8) selon que la page en comporte un : sans `--v1` ou `--v8`, le runner
s'abstient de conclure.

Le rapport atterrit dans `preuves/ocr-bakeoff-<date>.txt` (local, hors git) et ne
contient **que des métriques et des noms d'échantillons**. Ni le texte lu, ni
la vérité terrain n'y figurent.
