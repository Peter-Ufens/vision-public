"""Couche UIA (R-S1b) : identite du document et localisation des controles.

Repond a la seconde moitie de la question de l'auteur. `screen_context` dit
"quelle fenetre, quel processus" ; ce module dit "quel document" et "ou est
le controle".

Pourquoi UIA et pas les pixels : un site d'emploi, chat-ia.example et Outlook web
sont le MEME `msedge.exe`. Seule l'URL de l'onglet les distingue, et seule
UIA rend un bbox de controle exact. Chiffres du spike R-S0 (31/08) :
lecture d'URL 0,21 ms, controle nomme 0,14 ms, contre 30 ms pour une seule
capture d'ecran avant tout OCR. Note de spike interne (non publiee).

Regles de conception imposees par le spike, a ne pas reintroduire a l'envers :
  1. L'omnibox est le premier `Edit` de la fenetre. Chromium n'expose PAS de
     ControlType ToolBar : la chercher est une impasse (constat spike).
  2. Une page peut exposer PLUSIEURS `Document` (page + iframes). On itere.
  3. Un bbox hors ecran visible ou d'aire nulle est REJETE, jamais rendu.
  4. `FindFirst` sans resultat rend un pointeur COM NULL qui n'est pas None
     cote Python et qui leve au premier acces. On teste `if not elem`.
  5. Requetes ciblees uniquement : `FindFirst` / `FindAll` natifs avec
     condition de type. Jamais de parcours Python de l'arbre entier.
  6. Abstention : controle absent -> None. On n'invente jamais un bbox.

Modele d'usage : OUTIL A LA DEMANDE (decision I7 du 31/08). Aucune
boucle, aucun thread, aucun daemon. L'objet UIA est cree au premier appel et
garde en cache pour le process (l'init coute ~75 ms, les appels suivants
sont a 0,2 ms).

Non thread-safe : COM est initialise sur le thread appelant. Appeler depuis
le meme thread, ou re-initialiser COM soi-meme.

Local only. Aucun reseau, aucune ecriture disque.
"""

from __future__ import annotations

import time
import urllib.parse
from dataclasses import asdict, dataclass
from typing import Any, Iterable

__all__ = [
    "ControlHit",
    "DocumentInfo",
    "collect",
    "element_from_hwnd",
    "find_control",
    "is_available",
    "iter_documents",
    "normalise_url",
    "probe",
    "read_document",
    "unavailable_reason",
]

# Plafond d'enumeration par document et par role. Garde-fou : au-dela, on
# prefere abstenir plutot que de payer un parcours qui derape.
MAX_CONTROLS_PER_ROLE = 400

# Roles cherches par defaut pour une cible d'action.
DEFAULT_ROLES = ("Button", "Hyperlink")

_ROLES: dict[str, str] = {
    "Button": "UIA_ButtonControlTypeId",
    "Hyperlink": "UIA_HyperlinkControlTypeId",
    "Edit": "UIA_EditControlTypeId",
    "CheckBox": "UIA_CheckBoxControlTypeId",
    "RadioButton": "UIA_RadioButtonControlTypeId",
    "ComboBox": "UIA_ComboBoxControlTypeId",
    "ListItem": "UIA_ListItemControlTypeId",
    "MenuItem": "UIA_MenuItemControlTypeId",
    "Text": "UIA_TextControlTypeId",
    "Document": "UIA_DocumentControlTypeId",
    # Ajoutes en R-S8. `TabItem` etait DEJA appele par chrome_zones sans
    # figurer ici : la requete rendait une liste vide, sans erreur, et le
    # code en concluait "barre d'onglets verticale repliee". Une explication
    # inventee, propagee jusqu'au north star. Voir `collect`.
    "TabItem": "UIA_TabItemControlTypeId",
    "TreeItem": "UIA_TreeItemControlTypeId",
    "SplitButton": "UIA_SplitButtonControlTypeId",
    "Slider": "UIA_SliderControlTypeId",
    "ToolBar": "UIA_ToolBarControlTypeId",
}

# Ce sur quoi un humain clique. Sert d'inventaire par defaut (R-S8).
# `Edit` y figure : une zone de saisie se clique avant d'etre remplie.
# `Text` et `Document` n'y figurent pas : on les lit, on ne les actionne pas.
ROLES_ACTIONNABLES: tuple[str, ...] = (
    "Button", "Hyperlink", "MenuItem", "ListItem", "TabItem", "TreeItem",
    "CheckBox", "RadioButton", "ComboBox", "SplitButton", "Slider", "Edit",
)

_uia_cache: dict[str, Any] = {"iuia": None, "mod": None, "reason": "", "tried": False}


# --------------------------------------------------------------------------- #
# Initialisation paresseuse
# --------------------------------------------------------------------------- #

def _uia() -> tuple[Any, Any]:
    """(IUIAutomation, module genere) ou (None, None). Init une seule fois."""
    if _uia_cache["tried"]:
        return _uia_cache["iuia"], _uia_cache["mod"]
    _uia_cache["tried"] = True
    try:
        from comtypes.client import CreateObject, GetModule

        GetModule("UIAutomationCore.dll")
        import comtypes.gen.UIAutomationClient as mod  # noqa: N813

        _uia_cache["iuia"] = CreateObject(mod.CUIAutomation, interface=mod.IUIAutomation)
        _uia_cache["mod"] = mod
    except Exception as exc:  # noqa: BLE001
        _uia_cache["reason"] = f"{type(exc).__name__}: {exc}"
    return _uia_cache["iuia"], _uia_cache["mod"]


def is_available() -> bool:
    iuia, _ = _uia()
    return iuia is not None


def unavailable_reason() -> str:
    _uia()
    return str(_uia_cache["reason"] or "")


# --------------------------------------------------------------------------- #
# Primitives (regle 4 : `if not elem`, jamais `is None`)
# --------------------------------------------------------------------------- #

def _ok(elem: Any) -> bool:
    """Un pointeur COM NULL n'est pas None mais est faux. Voir regle 4."""
    if elem is None:
        return False
    try:
        return bool(elem)
    except Exception:  # noqa: BLE001
        return True


def _name(elem: Any) -> str:
    try:
        return str(elem.CurrentName or "")
    except Exception:  # noqa: BLE001
        return ""


def _class(elem: Any) -> str:
    try:
        return str(elem.CurrentClassName or "")
    except Exception:  # noqa: BLE001
        return ""


def _bbox(elem: Any) -> tuple[int, int, int, int] | None:
    try:
        r = elem.CurrentBoundingRectangle
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:  # noqa: BLE001
        return None


def _offscreen(elem: Any) -> bool:
    try:
        return bool(elem.CurrentIsOffscreen)
    except Exception:  # noqa: BLE001
        return False


def _type_cond(iuia: Any, mod: Any, role: str) -> Any | None:
    attr = _ROLES.get(role)
    if not attr or not hasattr(mod, attr):
        return None
    return iuia.CreatePropertyCondition(mod.UIA_ControlTypePropertyId, getattr(mod, attr))


def _find_first(elem: Any, mod: Any, cond: Any) -> Any:
    return elem.FindFirst(mod.TreeScope_Descendants, cond)


def element_from_hwnd(hwnd: int) -> Any:
    iuia, _ = _uia()
    if iuia is None or not hwnd:
        return None
    try:
        elem = iuia.ElementFromHandle(hwnd)
    except Exception:  # noqa: BLE001
        return None
    return elem if _ok(elem) else None


# --------------------------------------------------------------------------- #
# Geometrie (regle 3)
# --------------------------------------------------------------------------- #

def _monitor_boxes(monitors: Iterable[Any] | None) -> list[tuple[int, int, int, int]]:
    """Rectangles des ecrans physiques.

    Import paresseux de screen_context pour eviter un cycle a l'import :
    screen_context importe screen_uia (aussi paresseusement) dans
    get_screen_snapshot. Chacun reste utilisable seul.
    """
    if monitors is not None:
        return [tuple(m.bbox) for m in monitors]  # type: ignore[misc]
    try:
        from screen_context import list_monitors

        return [tuple(m.bbox) for m in list_monitors()]  # type: ignore[misc]
    except Exception:  # noqa: BLE001
        return []


def _on_screen(bbox: tuple[int, int, int, int], boxes: list[tuple[int, int, int, int]]) -> bool:
    if bbox[2] - bbox[0] <= 0 or bbox[3] - bbox[1] <= 0:
        return False
    if not boxes:
        return True  # aucune reference : on ne peut pas rejeter, on ne ment pas non plus
    for b in boxes:
        ox = max(0, min(bbox[2], b[2]) - max(bbox[0], b[0]))
        oy = max(0, min(bbox[3], b[3]) - max(bbox[1], b[1]))
        if ox * oy > 0:
            return True
    return False


# --------------------------------------------------------------------------- #
# Document / URL
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class DocumentInfo:
    url_raw: str
    host: str
    path: str
    scheme: str | None
    control_class: str
    via: str  # "omnibox-class" (sur) ou "value-heuristic" (moins sur)
    url_normalized: str = ""
    scheme_suppose: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalise_url(url_raw: str, scheme: str | None) -> tuple[str, bool]:
    """(url complete, schema suppose).

    Chromium masque `https://` quand l'omnibox n'a pas le focus, mais affiche
    `http://` en clair pour un site non securise (c'est un avertissement, il
    ne l'elide jamais). L'absence de schema est donc un signal, pas un vide :
    elle veut dire https.

    On rend quand meme le drapeau `suppose` a vrai dans ce cas. Un
    consommateur qui a besoin de certitude doit pouvoir distinguer ce qui a
    ete lu de ce qui a ete deduit, meme quand la deduction est solide.
    """
    u = (url_raw or "").strip()
    if not u:
        return ("", False)
    if scheme or "://" in u:
        return (u, False)
    return (f"https://{u}", True)


def _looks_like_url(value: str) -> bool:
    """Filtre anti-confusion.

    Le premier `Edit` de la fenetre EST l'omnibox sur Chromium, mais si le
    navigateur n'en expose pas, on tomberait sur un champ de la page. On
    n'accepte donc que ce qui a la forme d'une adresse. Une recherche tapee
    dans l'omnibox ("carte grise en ligne") est rejetee, et c'est voulu :
    mieux vaut s'abstenir que d'annoncer un faux site.
    """
    v = value.strip()
    if len(v) < 4 or " " in v:
        return False
    if "://" in v:
        return True
    head = v.split("/", 1)[0]
    return "." in head and not head.startswith(".") and not head.endswith(".")


def read_document(
    hwnd: int,
    *,
    expect_browser: bool = False,
    root: Any = None,
) -> DocumentInfo | None:
    """URL de l'onglet actif, ou None (abstention).

    Regle 1 : premier `Edit` descendant de la fenetre. Chromium n'expose
    aucune ToolBar, et l'omnibox precede le document dans l'ordre de l'arbre,
    donc un FindFirst natif le ramene en premier.

    Constat spike : Chromium rend l'URL SANS schema quand l'omnibox n'a pas
    le focus (mesure : 38 caracteres, aucun "://"). On ne fabrique pas le
    schema manquant, on le rend a null.

    Piege ferme ici : dans une application quelconque, le premier `Edit` est
    souvent la zone de saisie, pas une barre d'adresse. Observe sur Notepad,
    dont l'`Edit` porte le texte du document : si l'utilisateur y tape "example.com",
    une lecture naive annoncerait un site visite. Deux garde-fous :

      - signal sur : la classe du controle contient "Omnibox"
        (`OmniboxViewViews` sur Edge/Chrome, `BraveOmniboxViewViews` sur
        Brave). C'est une sous-chaine, pas une egalite : les forks Chromium
        prefixent leur nom.
      - signal faible : `expect_browser=True`, pose par l'appelant a partir
        de la table des categories d'applications, plus la forme d'URL.

    Sans l'un des deux, on rend None. Firefox n'expose pas cette classe et
    n'a pas ete teste : il tombera donc sur le signal faible ou s'abstiendra.
    """
    iuia, mod = _uia()
    if iuia is None:
        return None
    if root is None:
        root = element_from_hwnd(hwnd)
    if not _ok(root):
        return None

    cond = _type_cond(iuia, mod, "Edit")
    if cond is None:
        return None
    try:
        edit = _find_first(root, mod, cond)
    except Exception:  # noqa: BLE001
        return None
    if not _ok(edit):
        return None

    try:
        pattern = edit.GetCurrentPattern(mod.UIA_ValuePatternId)
        if not pattern:
            return None
        value = str(pattern.QueryInterface(mod.IUIAutomationValuePattern).CurrentValue or "")
    except Exception:  # noqa: BLE001
        return None

    value = value.strip()
    if not _looks_like_url(value):
        return None

    cls = _class(edit)
    if "omnibox" in cls.casefold():
        via = "omnibox-class"
    elif expect_browser:
        via = "value-heuristic"
    else:
        return None  # ni classe reconnue ni app declaree navigateur : abstention

    has_scheme = "://" in value
    parsed = urllib.parse.urlparse(value if has_scheme else "//" + value)
    scheme = parsed.scheme if has_scheme else None
    complete, suppose = normalise_url(value, scheme)
    return DocumentInfo(
        url_raw=value,
        host=parsed.netloc,
        path=parsed.path,
        scheme=scheme,
        control_class=cls,
        via=via,
        url_normalized=complete,
        scheme_suppose=suppose,
    )


def iter_documents(hwnd: int, root: Any = None) -> list[Any]:
    """Tous les `Document` de la fenetre (regle 2 : page + iframes)."""
    iuia, mod = _uia()
    if iuia is None:
        return []
    if root is None:
        root = element_from_hwnd(hwnd)
    if not _ok(root):
        return []
    cond = _type_cond(iuia, mod, "Document")
    if cond is None:
        return []
    out: list[Any] = []
    try:
        arr = root.FindAll(mod.TreeScope_Descendants, cond)
        for i in range(min(arr.Length, 32)):
            el = arr.GetElement(i)
            if _ok(el):
                out.append(el)
    except Exception:  # noqa: BLE001
        return out
    return out


# --------------------------------------------------------------------------- #
# Controles
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ControlHit:
    name: str
    role: str
    bbox: tuple[int, int, int, int]
    in_window: bool
    method: str
    control_class: str

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bbox"] = list(self.bbox)
        return d


def _accept(
    elem: Any,
    boxes: list[tuple[int, int, int, int]],
    window_rect: tuple[int, int, int, int] | None,
) -> tuple[tuple[int, int, int, int], bool] | None:
    """Valide un candidat. Regle 3 : hors ecran ou aire nulle -> rejet."""
    if not _ok(elem) or _offscreen(elem):
        return None
    bbox = _bbox(elem)
    if bbox is None or not _on_screen(bbox, boxes):
        return None
    in_window = True
    if window_rect:
        in_window = (
            window_rect[0] <= bbox[0] and window_rect[1] <= bbox[1]
            and bbox[2] <= window_rect[2] and bbox[3] <= window_rect[3]
        )
    return bbox, in_window


def find_control(
    hwnd: int,
    name: str,
    *,
    roles: Iterable[str] = DEFAULT_ROLES,
    window_rect: tuple[int, int, int, int] | None = None,
    monitors: Iterable[Any] | None = None,
    allow_substring: bool = True,
    root: Any = None,
    scopes: list[Any] | None = None,
) -> ControlHit | None:
    """Localise un controle par son nom. None = abstention assumee.

    Deux passes, toutes deux en requetes natives ciblees (regle 5) :
      1. Correspondance exacte du nom, insensible a la casse, par role.
      2. Repli par sous-chaine sur les controles DU document (ou de la
         fenetre s'il n'y a pas de document), plafonne.

    Aucune des deux ne parcourt l'arbre entier. La passe 2 enumere un
    sous-ensemble TYPE d'un sous-arbre, ce qui est autre chose.
    """
    iuia, mod = _uia()
    if iuia is None or not name:
        return None
    if root is None:
        root = element_from_hwnd(hwnd)
    if not _ok(root):
        return None

    boxes = _monitor_boxes(monitors)
    # Regle 2 : plusieurs documents possibles. Sans document (app native),
    # on cherche depuis la fenetre.
    if scopes is None:
        scopes = iter_documents(hwnd, root=root)
    scopes = scopes or [root]
    role_list = [r for r in roles if r in _ROLES]

    try:
        name_cond = iuia.CreatePropertyConditionEx(
            mod.UIA_NamePropertyId, name, mod.PropertyConditionFlags_IgnoreCase
        )
    except Exception:  # noqa: BLE001
        return None

    # Passe 1 : exact
    for scope in scopes:
        for role in role_list:
            tcond = _type_cond(iuia, mod, role)
            if tcond is None:
                continue
            try:
                elem = _find_first(scope, mod, iuia.CreateAndCondition(tcond, name_cond))
            except Exception:  # noqa: BLE001
                continue
            accepted = _accept(elem, boxes, window_rect)
            if accepted:
                bbox, in_window = accepted
                return ControlHit(
                    name=_name(elem),
                    role=role,
                    bbox=bbox,
                    in_window=in_window,
                    method="exact",
                    control_class=_class(elem),
                )

    if not allow_substring:
        return None

    # Passe 2 : sous-chaine, sur un sous-ensemble type et plafonne
    needle = name.casefold()
    for scope in scopes:
        for role in role_list:
            tcond = _type_cond(iuia, mod, role)
            if tcond is None:
                continue
            try:
                arr = scope.FindAll(mod.TreeScope_Descendants, tcond)
                total = min(arr.Length, MAX_CONTROLS_PER_ROLE)
            except Exception:  # noqa: BLE001
                continue
            for i in range(total):
                try:
                    el = arr.GetElement(i)
                except Exception:  # noqa: BLE001
                    continue
                label = _name(el)
                if not label or needle not in label.casefold():
                    continue
                accepted = _accept(el, boxes, window_rect)
                if accepted:
                    bbox, in_window = accepted
                    return ControlHit(
                        name=label,
                        role=role,
                        bbox=bbox,
                        in_window=in_window,
                        method="substring",
                        control_class=_class(el),
                    )
    return None


# --------------------------------------------------------------------------- #
# Sonde complete
# --------------------------------------------------------------------------- #

def collect(
    root: Any,
    role: str,
    *,
    limite: int = 600,
) -> tuple[list[dict[str, Any]], int]:
    """Tous les controles d'un role, avec leur nom et leur bbox.

    Retourne (elements, erreurs). Chaque acces est protege individuellement,
    pas seulement l'appel global.

    Pourquoi ce soin : l'arbre d'accessibilite d'un navigateur vivant est
    VOLATILE. Mesure pendant ce lot, sur la meme fenetre Brave a une minute
    d'intervalle : 12 onglets puis 0, 419 liens puis 0, et un
    `COMError: pointeur non valide` en pleine iteration quand une autre
    application a repris la main. Un element mis en cache devient invalide
    des que l'arbre est reconstruit.

    Consequence : jamais de root en cache entre deux appels, et une panne en
    cours de route rend ce qui a ete collecte plutot que de tout perdre.
    """
    if role not in _ROLES:
        # Erreur de PROGRAMMATION, pas condition d'environnement : elle
        # remonte. Rendre une liste vide ici serait indiscernable de "cette
        # application n'expose aucun controle de ce type" -- c'est
        # exactement ce qui a fait inventer a chrome_zones une barre
        # d'onglets repliee qui n'existait pas (R-S7 puis R-S8).
        raise ValueError(
            f"role UIA inconnu : {role!r}. Roles connus : {sorted(_ROLES)}"
        )
    iuia, mod = _uia()
    if iuia is None or not _ok(root):
        return ([], 1)
    cond = _type_cond(iuia, mod, role)
    if cond is None:
        # Role connu, mais absent de cette version de comtypes : condition
        # d'environnement, comptee comme une erreur et non comme un vide.
        return ([], 1)
    try:
        arr = root.FindAll(mod.TreeScope_Descendants, cond)
        total = min(arr.Length, limite)
    except Exception:  # noqa: BLE001
        return ([], 1)

    out: list[dict[str, Any]] = []
    erreurs = 0
    for i in range(total):
        try:
            el = arr.GetElement(i)
            nom = _name(el)
            bbox = _bbox(el)
            if not nom or bbox is None:
                continue
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            out.append(
                {
                    "name": nom,
                    "bbox": list(bbox),
                    "role": role,
                    "class": _class(el),
                    "offscreen": _offscreen(el),
                }
            )
        except Exception:  # noqa: BLE001
            erreurs += 1
    return (out, erreurs)


def _collect_multi(
    root: Any,
    roles: tuple[str, ...],
    limite: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Tous les roles demandes en UNE traversee. (elements, erreurs, tronque).

    Une condition OR au lieu de N requetes successives. Mesure sur
    l'explorateur Windows, 12 roles : 973 ms en boucle, 116 ms ici, soit
    **8,4 fois moins**. La traversee de l'arbre dominait, pas la lecture des
    proprietes -- interroger douze fois le meme arbre pour douze types etait
    le vrai cout.

    Repli sur la boucle par role si la condition OR n'est pas disponible :
    une version de comtypes sans `CreateOrConditionFromArray` doit degrader,
    pas echouer.
    """
    iuia, mod = _uia()
    if iuia is None or not _ok(root):
        return ([], 1, False)

    conds, connus = [], []
    for r in roles:
        c = _type_cond(iuia, mod, r)
        if c is not None:
            conds.append(c)
            connus.append(r)
    if not conds:
        return ([], len(roles), False)

    inverse = {
        getattr(mod, _ROLES[r]): r for r in connus if hasattr(mod, _ROLES[r])
    }
    try:
        cond = conds[0] if len(conds) == 1 else iuia.CreateOrConditionFromArray(conds)
        arr = root.FindAll(mod.TreeScope_Descendants, cond)
        total = arr.Length
    except Exception:  # noqa: BLE001
        out: list[dict[str, Any]] = []
        erreurs = 0
        for r in connus:
            els, err = collect(root, r, limite=limite)
            for e in els:
                e["role"] = r
            out += els
            erreurs += err
        return (out[:limite], erreurs, len(out) > limite)

    elements: list[dict[str, Any]] = []
    erreurs = 0
    for i in range(min(total, limite)):
        try:
            el = arr.GetElement(i)
            nom = _name(el)
            bbox = _bbox(el)
            if not nom or bbox is None:
                continue
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            elements.append(
                {
                    "name": nom,
                    "bbox": list(bbox),
                    "role": inverse.get(el.CurrentControlType, "?"),
                    "class": _class(el),
                    "offscreen": _offscreen(el),
                }
            )
        except Exception:  # noqa: BLE001
            erreurs += 1
    return (elements, erreurs, total > limite)


def inventory(
    hwnd: int,
    *,
    roles: Iterable[str] | None = None,
    scope: str = "window",
    limite: int = 400,
    monitors: Iterable[Any] | None = None,
    window_rect: tuple[int, int, int, int] | None = None,
    with_occlusion: bool = True,
) -> dict[str, Any]:
    """Ce qui est cliquable dans cette fenetre. Ne leve pas sur l'environnement.

    R-S7 a mesure que 302 controles actionnables sur 304 portent deja un nom
    dans l'arbre d'accessibilite. Il manquait de quoi les DEMANDER : cette
    fonction repond a "que puis-je cliquer ici ?", la ou `find_control`
    repond a "ou est le bouton que je nomme deja ?".

    `scope` :
      "window"    toute la fenetre, chrome du navigateur compris (defaut)
      "document"  les documents seulement, s'il y en a

    LE PERIMETRE COMPTE, et il differe de celui de `find_control`. Ce dernier
    cherche dans les DOCUMENTS quand la fenetre en a. Un onglet ou un bouton
    de barre d'outils apparait donc dans l'inventaire en `scope="window"`
    sans etre atteignable par `target=`. Chaque controle porte `in_document`
    pour que l'appelant le sache au lieu de le decouvrir.

    `occluded` : vrai si un clic au CENTRE du controle atteindrait une autre
    fenetre. Un controle partiellement recouvert dont le centre est degage
    est rendu non occulte, ce qui est la bonne reponse pour un clic.

    VIE PRIVEE : les noms de controles sont du contenu d'ecran. Ils sont
    rendus parce que c'est l'objet de la fonction ; ils n'ont rien a faire
    dans un fichier de preuves.
    """
    t0 = time.perf_counter()
    demandes = tuple(roles) if roles is not None else ROLES_ACTIONNABLES
    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "scope": scope,
        "roles": list(demandes),
        "controls": [],
        "counts": {},
        "errors": 0,
        "truncated": False,
        "timings_ms": {},
    }
    inconnus = [r for r in demandes if r not in _ROLES]
    if inconnus:
        raise ValueError(f"roles UIA inconnus : {inconnus}. Connus : {sorted(_ROLES)}")
    if scope not in ("window", "document"):
        raise ValueError(f"scope inconnu : {scope!r}. Attendu 'window' ou 'document'.")

    if not is_available():
        out["reason"] = unavailable_reason() or "UIA indisponible"
        return out
    root = element_from_hwnd(hwnd)
    if not _ok(root):
        out["reason"] = "ElementFromHandle a echoue sur cette fenetre"
        return out

    documents = iter_documents(hwnd, root=root)
    if scope == "document":
        perimetres = documents
        if not perimetres:
            out["available"] = True
            out["reason"] = "aucun document dans cette fenetre"
            return out
    else:
        perimetres = [root]
    out["has_document"] = bool(documents)

    boxes = _monitor_boxes(monitors)
    # Bbox des documents : sert a dire si un controle est atteignable par
    # `target=`, sans reparcourir l'arbre une seconde fois.
    doc_boxes: list[tuple[int, int, int, int]] = []
    for d in documents:
        bb = _bbox(d)
        if bb and bb[2] > bb[0] and bb[3] > bb[1]:
            doc_boxes.append(bb)

    controles: list[dict[str, Any]] = []
    for scope_elem in perimetres:
        elements, erreurs, tronque = _collect_multi(scope_elem, demandes, limite)
        out["errors"] += erreurs
        out["truncated"] = out["truncated"] or tronque
        for el in elements:
            bbox = tuple(el["bbox"])
            if el["offscreen"] or not _on_screen(bbox, boxes):
                continue
            if window_rect is not None and not _on_screen(bbox, [window_rect]):
                continue
            controles.append(
                {
                    "name": el["name"],
                    "role": el["role"],
                    "bbox": list(bbox),
                    "class": el["class"],
                    "in_document": any(
                        bbox[0] >= d[0] and bbox[1] >= d[1]
                        and bbox[2] <= d[2] and bbox[3] <= d[3]
                        for d in doc_boxes
                    ),
                }
            )
    out["timings_ms"]["arbre"] = round((time.perf_counter() - t0) * 1000.0, 1)

    if with_occlusion and controles:
        t1 = time.perf_counter()
        from screen_cursor import window_at_point

        for c in controles:
            b = c["bbox"]
            dessus = window_at_point((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
            # None = le gestionnaire n'a pas repondu : on ne sait pas, et on
            # ne tranche pas.
            c["occluded"] = None if dessus is None else (dessus != hwnd)
        out["timings_ms"]["occultation"] = round((time.perf_counter() - t1) * 1000.0, 1)

    controles.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))
    out["available"] = True
    out["controls"] = controles
    out["counts"] = {
        "total": len(controles),
        "par_role": {r: sum(1 for c in controles if c["role"] == r) for r in demandes},
        "dans_document": sum(1 for c in controles if c["in_document"]),
        "occultes": sum(1 for c in controles if c.get("occluded") is True),
        "occultation_inconnue": sum(1 for c in controles if c.get("occluded") is None),
    }
    out["timings_ms"]["total"] = round((time.perf_counter() - t0) * 1000.0, 1)
    return out


def probe(
    hwnd: int,
    *,
    target: str | None = None,
    window_rect: tuple[int, int, int, int] | None = None,
    monitors: Iterable[Any] | None = None,
    expect_browser: bool = False,
) -> dict[str, Any]:
    """Tout ce qu'UIA sait dire de cette fenetre, avec les chronos.

    Ne leve jamais : en cas d'echec, `available` est faux et les champs sont
    a None. Un appelant qui ne peut pas distinguer "rien vu" de "pas pu
    regarder" prendrait des decisions dangereuses.
    """
    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "document": None,
        "documents_count": None,  # None = non compte (voir probe), pas "aucun"
        "target": None,
        "target_requested": target,
        "timings_ms": {},
    }
    if not is_available():
        out["reason"] = unavailable_reason() or "UIA indisponible"
        return out
    out["available"] = True

    # Un seul ElementFromHandle et une seule enumeration de documents pour
    # tout le probe : les recalculer triplait le cout mesure.
    t0 = time.perf_counter()
    root = element_from_hwnd(hwnd)
    out["timings_ms"]["root"] = round((time.perf_counter() - t0) * 1000.0, 3)
    if not _ok(root):
        out["reason"] = "ElementFromHandle a echoue sur cette fenetre"
        return out

    t0 = time.perf_counter()
    doc = read_document(hwnd, expect_browser=expect_browser, root=root)
    out["timings_ms"]["document"] = round((time.perf_counter() - t0) * 1000.0, 3)
    out["document"] = doc.to_dict() if doc else None

    # Les documents ne servent qu'a delimiter la recherche d'une cible. Les
    # enumerer sans cible couterait ~8 ms mesures pour une donnee purement
    # indicative, sur l'appel le plus frequent. None veut donc dire "pas
    # compte", pas "aucun document" : la nuance compte pour l'appelant.
    if target:
        t0 = time.perf_counter()
        scopes = iter_documents(hwnd, root=root)
        out["timings_ms"]["documents"] = round((time.perf_counter() - t0) * 1000.0, 3)
        out["documents_count"] = len(scopes)

        t0 = time.perf_counter()
        hit = find_control(
            hwnd,
            target,
            window_rect=window_rect,
            monitors=monitors,
            root=root,
            scopes=scopes,
        )
        out["timings_ms"]["target"] = round((time.perf_counter() - t0) * 1000.0, 3)
        out["target"] = hit.to_dict() if hit else None
    return out
