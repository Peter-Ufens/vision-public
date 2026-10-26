"""Parler a Ollama en flux, sans bloquer l'appelant (R-S16).

POURQUOI DU FLUX ET PAS UN APPEL QUI REND TOUT A LA FIN
--------------------------------------------------------

Un appel bloquant de 25 s rend un texte parfait 25 s trop tard. Pour du
temps reel ecrit, ce qui compte n'est pas la duree totale mais le **delai
avant le premier mot** : c'est lui qui decide si une interface parait
vivante ou figee. Le flux le rend mesurable, et ce module le mesure.

CE QUE CE MODULE PREVOIT POUR LA SUITE
---------------------------------------

L'auteur a annonce que ce composant serait repris ailleurs : voix (Whisper en
entree, TTS en sortie), puis une couche de fusion multimodale, pendant qu'une
session Vision tourne. Quatre choses cassent en premier quand plusieurs
charges se superposent, et chacune a ici sa parade :

  1. **L'appelant bloque.** Le flux tourne dans un fil, l'appelant lit une
     file. Une boucle audio n'attend jamais le LLM.

  2. **La memoire enfle.** La file est BORNEE. Quand le lecteur prend du
     retard, c'est le producteur qui attend, pas la memoire qui monte. On
     ne jette jamais un jeton de texte : un mot perdu au milieu d'une
     phrase serait indetectable en aval.

  3. **On ne peut pas s'arreter.** `stop()` coupe au milieu d'une phrase.
     C'est la condition du "barge-in" : quand l'utilisateur reparle, la generation
     en cours doit mourir tout de suite, pas finir son paragraphe.

  4. **Ollama se tait sans fermer.** Un flux qui stagne bloquerait pour
     toujours. Un chien de garde surveille le delai ENTRE deux morceaux,
     separement du delai total.

Cinquieme point, qui n'est pas dans ce fichier mais qui le contraint : deux
appels simultanes sur deux modeles differents font permuter les poids en
VRAM et ecroulent les deux. Les appels sont donc **serialises par modele**,
et `keep_alive` evite au modele d'etre decharge entre deux tours.

RESEAU
------

`127.0.0.1` uniquement, et l'hote n'est pas configurable par une chaine
libre : un digest de session ne doit pas pouvoir partir ailleurs par une
faute de configuration. Aucun `ollama pull` n'est jamais declenche.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

# Recommandation mesuree en R-S15 : 23/25 en 728 ms, seul modele a s'abstenir
# sur une zone noire, 6,6 Go. Voir PLAN-PONT-LLM-CONTROLE-VISION.
MODELE_DEFAUT = "qwen3.5:9b-gpu"

# System MINIMAL. Surtout pas une persona : mesure R-S15, le meme modele
# passe de 24/25 a 0/25 quand on lui donne un system de compagnon.
SYSTEM_MINIMAL = (
    "Tu ecris un journal factuel en francais. Tu n'affirmes que ce que les "
    "faits fournis contiennent. Quand un fait est absent ou indetermine, tu "
    "le dis au lieu de le deviner. Phrases courtes."
)

PORT_DEFAUT = 11434
HOTE = "127.0.0.1"  # non configurable : voir le docstring

# Bornes de la file. 256 morceaux, c'est plusieurs phrases d'avance : de quoi
# absorber une hesitation du lecteur sans laisser la memoire filer.
FILE_MAX = 256

# Le chien de garde ne surveille pas la duree totale mais le silence entre
# deux morceaux. Un modele lent reste acceptable ; un modele muet, non.
SILENCE_MAX_S = 30.0
ATTENTE_TOTALE_MAX_S = 600.0

# Un modele decharge se recharge en plusieurs secondes. On demande a Ollama
# de le garder en VRAM entre deux tours d'une meme conversation.
KEEP_ALIVE = "5m"

# Serialisation par modele : deux flux sur deux modeles differents feraient
# permuter les poids en VRAM. Un verrou par tag, cree a la demande.
_verrous: dict[str, threading.Lock] = {}
_verrou_registre = threading.Lock()


def _verrou_modele(tag: str) -> threading.Lock:
    with _verrou_registre:
        return _verrous.setdefault(tag, threading.Lock())


class OllamaIndisponible(RuntimeError):
    """Le serveur ne repond pas. Echec PROPRE, jamais silencieux.

    Volontairement une exception et non un `None` : un appelant qui oublie
    de tester un `None` croirait avoir recu une reponse vide. Vision, elle,
    ne leve pas cette exception, parce qu'elle n'appelle pas ce module.
    """


class StreamBloque(TimeoutError):
    """Le flux s'est tu sans se fermer. Voir SILENCE_MAX_S."""


@dataclass
class Morceau:
    """Un fragment de texte tel qu'il arrive. Pas forcement un mot entier."""

    texte: str
    index: int
    t_ms: float


@dataclass
class Mesures:
    """Ce qu'on saura dire du flux une fois qu'il sera fini."""

    ttft_ms: float | None = None          # delai avant le premier morceau
    total_ms: float | None = None
    morceaux: int = 0
    caracteres: int = 0
    done_reason: str | None = None
    eval_count: int | None = None
    arret_demande: bool = False
    attente_file_ms: float = 0.0          # temps ou le producteur a attendu

    def resume(self) -> dict[str, Any]:
        return {
            "ttft_ms": round(self.ttft_ms, 1) if self.ttft_ms is not None else None,
            "total_ms": round(self.total_ms, 1) if self.total_ms is not None else None,
            "morceaux": self.morceaux,
            "caracteres": self.caracteres,
            "done_reason": self.done_reason,
            "eval_count": self.eval_count,
            "arret_demande": self.arret_demande,
            "attente_file_ms": round(self.attente_file_ms, 1),
        }


class Flux:
    """Un flux en cours. Se lit morceau par morceau, s'arrete a la demande.

    L'objet est cree par `StreamLLM.demarrer` et vit dans son propre fil.
    L'appelant ne fait qu'iterer et, s'il le veut, appeler `stop()`.
    """

    _FIN = object()

    def __init__(self, source: Callable[[threading.Event], Iterator[dict[str, Any]]],
                 *, file_max: int = FILE_MAX, silence_max_s: float = SILENCE_MAX_S,
                 verrou: threading.Lock | None = None) -> None:
        self._file: queue.Queue[Any] = queue.Queue(maxsize=file_max)
        self._arret = threading.Event()
        self._source = source
        self._silence_max_s = silence_max_s
        self._verrou = verrou
        self._erreur: BaseException | None = None
        self._parts: list[str] = []
        self.mesures = Mesures()
        self._t0 = time.perf_counter()
        self._fil = threading.Thread(target=self._produire, name="llm-stream",
                                     daemon=True)
        self._fil.start()

    # -- cote producteur --------------------------------------------------- #

    def _produire(self) -> None:
        pris = False
        try:
            if self._verrou is not None:
                # Attendre son tour plutot que de faire permuter les poids
                # en VRAM avec un autre flux.
                while not self._verrou.acquire(timeout=0.2):
                    if self._arret.is_set():
                        return
                pris = True
            index = 0
            for brut in self._source(self._arret):
                if self._arret.is_set():
                    break
                texte = self._texte_de(brut)
                if texte:
                    if self.mesures.ttft_ms is None:
                        self.mesures.ttft_ms = (time.perf_counter() - self._t0) * 1000.0
                    m = Morceau(texte=texte, index=index,
                                t_ms=(time.perf_counter() - self._t0) * 1000.0)
                    index += 1
                    self._deposer(m)
                if brut.get("done"):
                    self.mesures.done_reason = brut.get("done_reason")
                    self.mesures.eval_count = brut.get("eval_count")
        except BaseException as exc:  # noqa: BLE001
            self._erreur = exc
        finally:
            if pris and self._verrou is not None:
                self._verrou.release()
            self.mesures.total_ms = (time.perf_counter() - self._t0) * 1000.0
            self.mesures.arret_demande = self._arret.is_set()
            # Toujours refermer la file, meme en cas de panne : un lecteur
            # qui attend une fin qui ne vient pas est pire qu'une erreur.
            while True:
                try:
                    self._file.put(self._FIN, timeout=0.2)
                    break
                except queue.Full:
                    if self._arret.is_set():
                        break
                    try:  # faire de la place : le lecteur est parti
                        self._file.get_nowait()
                    except queue.Empty:
                        pass

    def _deposer(self, m: Morceau) -> None:
        """Poser un morceau, en attendant si le lecteur est en retard.

        On ATTEND, on ne jette pas. Un jeton de texte perdu au milieu d'une
        phrase serait invisible en aval, alors qu'un producteur qui patiente
        se mesure (`attente_file_ms`).
        """
        debut = time.perf_counter()
        while not self._arret.is_set():
            try:
                self._file.put(m, timeout=0.2)
                self.mesures.attente_file_ms += (time.perf_counter() - debut) * 1000.0
                return
            except queue.Full:
                continue

    @staticmethod
    def _texte_de(brut: dict[str, Any]) -> str:
        msg = brut.get("message") or {}
        # `thinking` est ignore VOLONTAIREMENT : avec think=false il est vide,
        # et s'il ne l'est pas, ce n'est pas du texte destine au lecteur.
        return str(msg.get("content") or brut.get("response") or "")

    # -- cote lecteur ------------------------------------------------------ #

    def morceaux(self) -> Iterator[Morceau]:
        """Itere les morceaux jusqu'a la fin du flux. Releve une panne."""
        debut = time.perf_counter()
        while True:
            try:
                item = self._file.get(timeout=self._silence_max_s)
            except queue.Empty:
                self.stop()
                raise StreamBloque(
                    f"aucun morceau depuis {self._silence_max_s:g} s"
                ) from None
            if item is Flux._FIN:
                break
            if time.perf_counter() - debut > ATTENTE_TOTALE_MAX_S:
                self.stop()
                raise StreamBloque("duree totale de lecture depassee")
            self.mesures.morceaux += 1
            self.mesures.caracteres += len(item.texte)
            self._parts.append(item.texte)
            yield item
        if self._erreur is not None:
            raise self._erreur

    def texte(self) -> str:
        """Ce qui a ete lu jusqu'ici. Partiel si le flux a ete arrete."""
        return "".join(self._parts)

    def stop(self) -> None:
        """Couper la generation en cours, meme au milieu d'une phrase.

        C'est le "barge-in" : quand quelqu'un reparle, ce qui est en train
        d'etre ecrit n'a plus d'interet. On ne le termine pas poliment.
        """
        self._arret.set()

    def attendre(self, timeout: float = 5.0) -> bool:
        self._fil.join(timeout)
        return not self._fil.is_alive()

    @property
    def arrete(self) -> bool:
        return self._arret.is_set()


class StreamLLM:
    """Client de flux. Un objet par usage, reutilisable pour plusieurs tours."""

    def __init__(self, modele: str = MODELE_DEFAUT, *, system: str = SYSTEM_MINIMAL,
                 port: int = PORT_DEFAUT, num_predict: int = 700,
                 temperature: float = 0.0, think: bool = False,
                 transport: Callable[..., Iterator[dict[str, Any]]] | None = None,
                 serialiser: bool = True) -> None:
        self.modele = modele
        self.system = system
        self.port = int(port)
        self.num_predict = int(num_predict)
        self.temperature = float(temperature)
        # R-S15 : sans think=false, le budget part dans le raisonnement et
        # `content` reste vide. Ce n'est pas un detail de reglage.
        self.think = bool(think)
        self._transport = transport
        self._serialiser = serialiser

    # -- transport --------------------------------------------------------- #

    def _url(self, chemin: str) -> str:
        return f"http://{HOTE}:{self.port}{chemin}"

    def disponible(self) -> tuple[bool, str]:
        """(serveur joignable, version ou nom de l'erreur). Ne leve pas."""
        if self._transport is not None:
            return True, "transport-injecte"
        try:
            d = json.loads(urllib.request.urlopen(self._url("/api/version"),
                                                  timeout=5).read())
            return True, str(d.get("version", "?"))
        except Exception as exc:  # noqa: BLE001
            return False, type(exc).__name__

    def _corps(self, prompt: str, images: list[str] | None) -> dict[str, Any]:
        user: dict[str, Any] = {"role": "user", "content": prompt}
        if images:
            user["images"] = images
        messages = ([{"role": "system", "content": self.system}] if self.system
                    else []) + [user]
        return {
            "model": self.modele,
            "messages": messages,
            "stream": True,
            "think": self.think,
            "keep_alive": KEEP_ALIVE,
            "options": {"num_predict": self.num_predict,
                        "temperature": self.temperature},
        }

    def _http(self, corps: dict[str, Any], arret: threading.Event
              ) -> Iterator[dict[str, Any]]:
        req = urllib.request.Request(
            self._url("/api/chat"), data=json.dumps(corps).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            reponse = urllib.request.urlopen(req, timeout=SILENCE_MAX_S)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            raise OllamaIndisponible(f"HTTP {exc.code} : {detail}") from exc
        except Exception as exc:  # noqa: BLE001
            raise OllamaIndisponible(
                f"serveur injoignable sur {HOTE}:{self.port} ({type(exc).__name__}). "
                "Aucun telechargement n'est tente."
            ) from exc
        try:
            for ligne in reponse:
                if arret.is_set():
                    break  # le `finally` ferme la reponse, donc la generation
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    yield json.loads(ligne)
                except json.JSONDecodeError:
                    continue  # ligne partielle : le flux reprendra
        finally:
            try:
                reponse.close()
            except Exception:  # noqa: BLE001
                pass

    # -- API --------------------------------------------------------------- #

    def demarrer(self, prompt: str, *, images: list[str] | None = None,
                 file_max: int = FILE_MAX,
                 silence_max_s: float = SILENCE_MAX_S) -> Flux:
        """Lancer un flux. Rend tout de suite, la generation tourne derriere."""
        corps = self._corps(prompt, images)
        transport = self._transport

        def source(arret: threading.Event) -> Iterator[dict[str, Any]]:
            if transport is not None:
                yield from transport(corps, arret)
            else:
                yield from self._http(corps, arret)

        verrou = _verrou_modele(self.modele) if self._serialiser else None
        return Flux(source, file_max=file_max, silence_max_s=silence_max_s,
                    verrou=verrou)

    def texte_complet(self, prompt: str, **kw: Any) -> tuple[str, Mesures]:
        """Confort : derouler le flux jusqu'au bout. Bloque, evidemment."""
        flux = self.demarrer(prompt, **kw)
        for _ in flux.morceaux():
            pass
        return flux.texte(), flux.mesures
