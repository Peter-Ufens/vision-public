# vision-public (œil PC)

Outil **personnel** pour lire l’écran d’un PC (grille, OCR, description locale), sous Windows et, en partie, sous Linux X11.  
Réglé pour **un poste** (souvent deux écrans, Tesseract, modèles locaux).  
Ce n’est **pas** un produit clé en main.

## Notice (labo personnel)

Ce dépôt est un **labo personnel** de Peter UFENS, dans un cadre d’**autodéveloppement** et de **montée en compétence** en IA. Ce n’est **pas** un produit commercial de l’auteur.

Les copies, forks ou versions modifiées par des tiers **ne sont pas affiliées** à l’auteur et n’engagent pas sa responsabilité.

(Voir aussi le fichier `NOTICE`.)

## Qu’est-ce que c’est

L’**œil** du labo : capturer / lire / décrire ce qui est affiché.  
Les **mains** (souris, clavier) et les **caméras** sont d’autres chantiers, hors de cette vitrine.

## Ce que ce dépôt ne fait pas

- Pas de caméra / banque de visages
- Pas d’actuation souris-clavier dans les modules œil
- Pas de daemon 24/7 : une session a une durée (`--duree`)

## Installation

**Python 3.11 ou 3.12** (testés : 3.11 sous Windows, 3.12 sous Linux). Python 3.13 marche aussi, mais RapidOCR (OCR du HUD et de la veille, optionnel) ne s’installe pas encore en 3.13 : les outils passent alors à Tesseract seul, et le disent.

**Tesseract** (OCR), avec l’anglais **et le français** : les outils lisent `fra+eng` par défaut.

- Windows : installeur UB Mannheim, dans `C:\Program Files\Tesseract-OCR` (chemin utilisé par défaut), en cochant « French ».
- Debian / Ubuntu : `sudo apt install tesseract-ocr tesseract-ocr-fra python3-venv libgl1 libglib2.0-0`

Windows (PowerShell, dans le dossier du dépôt ; sans activation, donc sans souci de politique d’exécution). `py -0` liste les versions installées : remplacer `-3.12` par la vôtre (par exemple `-3.11`).

```powershell
py -3.12 -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r requirements-public.txt
.\.venv\Scripts\python.exe scripts\smoke_lire_cases.py
```

Linux :

```bash
python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements-public.txt
```

Optionnel, non installé par `requirements-public.txt` : Ollama, Moondream ou CLIP **en local** (l’image d’écran ne quitte pas la machine sans accord explicite).

## Systèmes

| Système | État |
|---|---|
| Windows 10/11 | complet : capture, grille, OCR, fenêtres, onglets (UI Automation), curseur |
| Linux, session X11 (« Xorg ») | capture, grille, OCR, veille et suivi marchent ; la liste des fenêtres et la position du curseur ne sont pas encore lues |
| Linux, session Wayland | **pas encore pris en charge** (capture par le portail système à faire) : choisir une session « Xorg » à l’écran de connexion |
| macOS | non testé |

## Démarrage rapide

```text
python scripts/smoke_lire_cases.py
python scripts/lire_cases.py 1:A1 --avec-texte
python scripts/instant_screen.py --depth standard
python scripts/veille_active.py --duree 30
```

Tout est en **lecture seule** : aucun clic, aucune touche, aucune image gardée par défaut.

## Licence

**Apache License 2.0** · voir `LICENSE` et `NOTICE`.  
(Licence des nouvelles vitrines du labo depuis le 2026-10-18.)

## Licences des briques tierces (indicatives)

À vérifier au tip des paquets / sites officiels avant réutilisation commerciale lourde. Ce dépôt ne redistribue **pas** les poids des modèles (téléchargement local éventuel hors vitrine).

| Brique | Rôle ici | Licence typique (indicatif) |
|---|---|---|
| **Tesseract OCR** | lecture de texte à l’écran | Apache-2.0 (binaire / moteur) |
| **OpenCV** (`opencv-python`) | traitement d’image | Apache-2.0 |
| **mss** | capture d’écran | MIT |
| **numpy** | calcul sur les images | BSD-3-Clause |
| **pytesseract** | pont Python vers Tesseract | Apache-2.0 |
| **pyzmq** | événements locaux | BSD-3-Clause (libzmq : MPL-2.0) |
| **psutil** | nom des processus | BSD-3-Clause |
| **pywin32** (Windows) | fenêtres Windows | PSF-2.0 |
| **comtypes** (Windows) | accessibilité (UI Automation) | MIT |
| **RapidOCR** (`rapidocr_onnxruntime`, optionnel) | OCR du HUD et de la veille | Apache-2.0 |
| **ONNX Runtime** (tiré par RapidOCR) | moteur des modèles OCR | MIT |
| **Pillow** | images | MIT-CMU (licence Pillow, déclarée par le paquet installé) |
| **CLIP** (via dépendances HF) | similarité visuelle optionnelle | selon paquet (souvent MIT) |
| **Moondream 3 (preview)** | description d’écran optionnelle | Business Source License 1.1 + Additional Use Grant (fiche du modèle) : usage perso et recherche libres ; lire les conditions avant tout service tiers |
| **Ollama** (outil local optionnel) | modèles locaux | selon le projet Ollama / le modèle choisi |

## Vie privée

Cette vitrine est produite par une **liste blanche** explicite (export contrôlé).  
Pas de captures, pas de sessions, pas de secrets.

## Auteur

Peter UFENS · labo perso / autodéveloppement IA.  
Écrit avec l’aide d’outils d’assistance IA (pas co-auteurs du copyright).
