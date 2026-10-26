# Flux LLM et agent de correction

**Statut :** livré R-S16 (2026-09-09) · `src/llm_stream/`
**Ce que c'est :** un composant autonome qui fait défiler du texte généré au fil de l'eau, puis le relit contre des faits et refuse ce qui n'y est pas.

> **Ce n'est pas branché sur Vision.** Une session écran tourne exactement pareil, Ollama éteint ou allumé. Le câblage session est réservé à R-S17. Le smoke vérifie que `session_screen.py` ne connaît pas ce paquet.

---

## 1. Démarrer

Le script de démonstration reste dans le dépôt privé. Le paquet s'utilise directement :

```python
from llm_stream import journal_duo
```

Sans serveur Ollama, le client le dit et rend la main proprement. **Aucun téléchargement n'est jamais tenté.**

---

## 2. Les deux moitiés, et pourquoi elles sont séparées

| Moitié | Rôle |
|---|---|
| `llm_stream.client` | parler à Ollama en flux, sans bloquer, en pouvant couper au milieu d'une phrase |
| `llm_stream.correction` | relire ce flux contre des faits, sans jamais rien ajouter |
| `llm_stream.duo` | mettre les deux bout à bout et rendre un journal |

On peut corriger un texte **sans avoir streamé** (`corriger(texte, faits)`), et streamer sans corriger. C'est ce qui rend le composant réutilisable ailleurs.

---

## 3. La règle qui tient tout

**Le correcteur ne peut que retirer ou neutraliser. Il n'ajoute jamais.**

Un correcteur qui réécrit librement serait un second générateur, donc une seconde source d'invention, et personne ne vérifierait le vérificateur. Celui-ci travaille par soustraction. Les seules phrases qu'il ait le droit d'écrire sont trois formules d'abstention, dans un catalogue fixe :

```
"La cause n'est pas determinee par les faits."
"(fin de phrase coupee)"
"(passage retire : absent des faits)"
```

**L'invariant se vérifie mot à mot** : tout mot du texte corrigé vient du brouillon, des faits, ou de ce catalogue. Le smoke le contrôle sur cinq brouillons différents.

### Ce que l'agent attrape

| Anomalie | Ce que c'est | Ce qu'il fait |
|---|---|---|
| `phrase_tronquee` | le flux s'arrête au milieu d'un mot | coupe à la dernière phrase complète, **ne complète jamais** |
| `fait_absent` | un exécutable ou un identifiant absent des sources | retire la phrase |
| `contradiction` | les faits disent `lisible=False`, la phrase affirme le contraire | retire la phrase |
| `cause_promue` | une cause déclarée indéterminée est affirmée | remplace par l'abstention |
| `repetition` | le même mot en boucle, panne de flux classique | replie |
| `chiffre_non_source` | un nombre qui ne vient d'aucun fait | **signale sans retirer** |

Le dernier est volontairement doux. Un résumé qui dit « elle a vécu 2 s » à partir de 41 s et 43 s fait une soustraction légitime, et le module accepte les **écarts entre faits** pour cette raison. Un nombre vraiment inventé est signalé, pas supprimé : un calcul juste ne doit pas disparaître en silence.

### La leçon de R-S15, codée

Quatre modèles sur cinq recopiaient la phrase « Flux protégé (DRM), écran éteint ou scène très sombre, indiscernables sur ce seul signal » dans un champ qui demandait une cause **certaine**. Ils n'inventaient pas, ils manquaient la sémantique du champ. Un appelant y aurait lu une cause établie avec une confiance de 1,0.

Un fait marqué `indetermine: true` interdit désormais toute phrase qui l'affirme :

```json
{"cle": "cause_zone_noire", "valeur": null, "indetermine": true,
 "marqueurs": ["drm", "zone noire", "flux protege"]}
```

> Brouillon : « La zone est noire, c'est du DRM. »
> Corrigé : « La zone est noire. La cause n'est pas determinee par les faits. »

---

## 4. Tenir la charge quand d'autres choses tournent

L'auteur a prévu de faire tourner ce composant à côté d'une session Vision, d'une écoute Whisper, d'une voix TTS, et plus tard d'une couche de fusion multimodale. Quatre choses cassent en premier quand les charges se superposent, et chacune a sa parade **mesurée**.

| Risque | Parade | Mesure |
|---|---|---|
| L'appelant bloque | le flux tourne dans un fil, on lit une file | premier mot à **78 ms** au lieu d'attendre 374 ms |
| La mémoire enfle | file **bornée** ; le producteur attend, la mémoire non | 200 morceaux, **0 perdu**, attente producteur mesurée |
| On ne peut pas s'arrêter | `stop()` coupe en pleine phrase | le fil meurt **6 ms** après |
| Ollama se tait sans fermer | chien de garde sur le silence **entre** deux morceaux | déclenche à `silence_max_s` |

**On n'abandonne jamais un jeton de texte.** Quand le lecteur traîne, c'est le producteur qui patiente. Un mot perdu au milieu d'une phrase serait indétectable en aval, alors qu'une attente se mesure (`attente_file_ms`).

### Sous charge réelle

Mesuré sur le poste de l'utilisateur, flux LLM pendant que l'œil Vision échantillonne les deux écrans :

| Situation | Premier mot (médiane) |
|---|---:|
| LLM seul | **49 ms** |
| LLM pendant l'échantillonnage du décor | **71 ms** |

Écart : **+22 ms**. L'œil, lui, passe de 61 ms à 72 ms par tour. Les deux se gênent un peu, aucun ne s'écroule.

### Deux flux en même temps

Les appels sont **sérialisés par modèle**, parce que deux modèles différents en parallèle font permuter les poids en VRAM et écroulent les deux. Mesuré, deux flux simultanés sur le même tag :

```
A : premier mot 50 ms,  total 215 ms
B : premier mot 253 ms, total 428 ms     (B a attendu son tour)
les deux textes sont complets
```

`keep_alive` demande à Ollama de garder le modèle en VRAM entre deux tours, pour éviter les 4,3 s de rechargement à froid.

---

## 5. Le câblage, non négociable

Trois réglages viennent de R-S15 et sont les valeurs par défaut :

| Réglage | Sans lui |
|---|---|
| `think: false` | le budget part dans le raisonnement, le texte reste vide |
| system **minimal**, jamais une persona | le même modèle passe de 24/25 à 0/25 |
| `num_predict` suffisant (700) | la réponse est coupée avant d'exister |

Modèle par défaut : **`qwen3.5:9b-gpu`**, recommandé en R-S15 (seul à s'abstenir sur une zone noire, et le plus rapide).

**Réseau :** `127.0.0.1` uniquement, et l'hôte n'est pas un paramètre. Un digest de session ne doit pas pouvoir partir ailleurs par une faute de configuration.

---

## 6. L'API

```python
from llm_stream import StreamLLM, Faits, corriger, journal_duo

faits = Faits.depuis([
    {"cle": "exe", "valeur": "cmd.exe"},
    {"cle": "lisible", "valeur": False, "marqueurs": ["lisible"]},
    {"cle": "cause", "valeur": None, "indetermine": True, "marqueurs": ["drm"]},
])

# temps réel
flux = StreamLLM().demarrer("Résume ces faits.")
for morceau in flux.morceaux():
    afficher(morceau.texte)          # ou l'envoyer à une voix
    if utilisateur_reparle():
        flux.stop()                  # coupe tout de suite

# relecture
resultat = corriger(flux.texte(), faits)
resultat.texte          # ce qui a survécu
resultat.anomalies      # ce qui a été retiré, et pourquoi
resultat.invariant_respecte

# les deux d'un coup
journal = journal_duo(prompt, faits, sur_morceau=afficher)
journal["brouillon"]    # ce que le modèle a vraiment écrit
journal["corrige"]      # ce qui tient dans les faits
```

**Le journal garde les deux.** Écraser le brouillon par le corrigé perdrait la trace de ce que le modèle avait produit. C'est le même raisonnement qu'une trace de session : le constat brut vit à côté de l'interprétation.

Un relecteur LLM peut se brancher (`corriger(..., relecteur=...)`). Sa sortie **repasse** par la couche déterministe et elle est **rejetée** si elle introduit un mot absent des sources. Un modèle ne peut pas faire rentrer par la fenêtre ce que la règle a sorti par la porte.

---

## 7. Réemploi

Le cœur **n'importe rien de Vision**. Les faits entrent par une liste de dictionnaires, avec leurs propres marqueurs lexicaux. Le smoke vérifie cette indépendance en lisant l'arbre syntaxique : un import de `session_contenu`, `screen_*` ou `cv2` fait échouer le contrôle.

| Usage | Ce qui change | Ce qui ne change pas |
|---|---|---|
| **Vision (R-S17)** | les faits viennent d'un digest de session | le client, le correcteur, l'invariant |
| **Application** | les faits viennent de l'application | idem |
| **Couche de fusion** | plusieurs sources fusionnées | idem |
| **Voix** | `sur_morceau` alimente un TTS ; `stop()` fait le barge-in | idem |

Ce qu'il faudra ajouter le jour où la voix arrive, et qui n'est **pas** dans ce lot : un découpage en phrases pour le TTS (parler dès la première phrase complète plutôt qu'à la fin), et un arbitrage de priorité si plusieurs composants veulent le GPU en même temps.

---

## 8. Vérifier

```bash
python scripts/smoke_llm_stream.py     # 54 contrôles, sans Ollama
```

Il vérifie notamment que le correcteur **n'ajoute jamais** un mot, qu'une cause indéterminée n'est jamais promue, qu'un relecteur menteur est rejeté, qu'aucun morceau n'est perdu sous contre-pression, que le composant ne lance aucun processus externe, ne parle qu'à `/api/chat` et `/api/version`, et que `session_screen.py` ne l'appelle pas.
