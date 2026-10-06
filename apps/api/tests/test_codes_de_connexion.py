"""Tout code de refus qui atteint l'écran de connexion porte une phrase.

Ce test existe à cause d'un défaut qui a coûté une mise en ligne : le front
cherchait la clé `login.error.unverified_email` tandis que l'API émettait le
code `email_not_verified`. Les deux ne se sont jamais rencontrés. Et comme la
page retombe **silencieusement** sur un message générique quand la clé manque,
rien ne pouvait le montrer : le propriétaire a buté sur l'écran de connexion
sans jamais savoir pourquoi.

Ce repli générique est une bonne chose — il interdit d'afficher un code brut à
un utilisateur. Mais il rend la dérive invisible. Elle doit donc être attrapée
ici, et nulle part ailleurs.

Les deux sens sont vérifiés : aucun code sans phrase, aucune phrase sans code.
Le second sens compte autant : une phrase orpheline est une phrase que
personne ne lira jamais, et c'était exactement la forme du défaut.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[3]
SOURCES_API = (
    RACINE / "apps/api/src/metreo_api/services/oidc.py",
    RACINE / "apps/api/src/metreo_api/routers/oidc_login.py",
)
DICTIONNAIRE = RACINE / "apps/web/src/lib/i18n.ts"

#: Les codes qui n'atteignent JAMAIS l'écran par `login_error`, et pourquoi.
#: Chacun arrive à l'utilisateur par un autre chemin, qui porte déjà son
#: propre message — la liste est donc une exemption motivée, pas une
#: dérogation de confort. Y ajouter un code demande d'écrire sa raison.
HORS_DU_RETOUR: dict[str, str] = {
    "oidc_disabled": (
        "levé par la route de départ, avant toute redirection : l'écran "
        "n'affiche même pas le bouton quand OIDC n'est pas configuré"
    ),
    "invalid_login_code": (
        "levé par l'échange du code à usage unique, appelé en fond par la "
        "page : il revient en détail HTTP, avec son propre message"
    ),
    "organization_required": (
        "n'est pas un refus mais une question : la page affiche la liste des "
        "organisations et attend un choix"
    ),
}

#: Les clés qui ne correspondent à aucun code de l'API, et pourquoi.
SANS_CODE_D_API: dict[str, str] = {
    "generic": "le repli, affiché quand le code reçu n'a pas de phrase à lui",
    "invalid_request": "posé par le routeur quand le fournisseur revient sans code ni erreur",
    "provider_refused": "posé par le routeur quand le fournisseur revient sur une erreur",
}


def _codes_de_l_api() -> set[str]:
    """Les codes que l'API peut produire, lus dans l'arbre syntaxique.

    Une recherche textuelle rendrait aussi les codes cités dans les
    commentaires et les docstrings ; l'arbre ne rend que ce qui est
    réellement construit.
    """
    codes: set[str] = set()
    for source in SOURCES_API:
        arbre = ast.parse(source.read_text(encoding="utf-8"))
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Call):
                continue
            nom = getattr(noeud.func, "id", None) or getattr(noeud.func, "attr", None)
            if nom == "OidcError" and noeud.args:
                premier = noeud.args[0]
                if isinstance(premier, ast.Constant) and isinstance(premier.value, str):
                    codes.add(premier.value)
            for argument in noeud.keywords:
                if argument.arg == "login_error" and isinstance(argument.value, ast.Constant):
                    valeur = argument.value.value
                    if isinstance(valeur, str):
                        codes.add(valeur)
        # Les refus qui ne passent pas par `OidcError` : une `HTTPException`
        # porte son code dans un `detail={"code": "...", "message": "..."}`.
        # Dans ces deux fichiers, une clé « code » n'apparaît nulle part
        # ailleurs — la recherche est donc sûre, et elle évite d'oublier trois
        # codes qui atteignent bel et bien l'utilisateur.
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Dict):
                continue
            for cle, valeur in zip(noeud.keys, noeud.values, strict=False):
                if (
                    isinstance(cle, ast.Constant)
                    and cle.value == "code"
                    and isinstance(valeur, ast.Constant)
                    and isinstance(valeur.value, str)
                ):
                    codes.add(valeur.value)
    return codes


def _cles_du_dictionnaire() -> set[str]:
    texte = DICTIONNAIRE.read_text(encoding="utf-8")
    return set(re.findall(r"'login\.error\.([a-z_]+)'", texte))


def _avis_de_l_api() -> set[str]:
    """Les codes d'AVIS que l'API peut poser dans l'URL de retour.

    Un avis accompagne une connexion RÉUSSIE — il n'y en a qu'un aujourd'hui.
    Il souffre pourtant exactement de la même dérive que les refus : la clé
    manque, la phrase ne s'affiche pas, et personne ne le voit. Pire, en fait :
    un refus muet se remarque parce que l'utilisateur est bloqué, un avis muet
    ne se remarque pas du tout.
    """
    codes: set[str] = set()
    for source in SOURCES_API:
        arbre = ast.parse(source.read_text(encoding="utf-8"))
        # Un avis est posé par AFFECTATION dans le dictionnaire de paramètres
        # du retour — `parametres["login_notice"] = "…"` — et non par un
        # argument nommé comme les refus. On cherche donc la valeur affectée.
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Assign) or len(noeud.targets) != 1:
                continue
            cible = noeud.targets[0]
            if (
                isinstance(cible, ast.Subscript)
                and isinstance(cible.slice, ast.Constant)
                and cible.slice.value == "login_notice"
                and isinstance(noeud.value, ast.Constant)
                and isinstance(noeud.value.value, str)
            ):
                codes.add(noeud.value.value)
    return codes


def _cles_d_avis_du_dictionnaire() -> set[str]:
    texte = DICTIONNAIRE.read_text(encoding="utf-8")
    return set(re.findall(r"'login\.notice\.([a-z_]+)'", texte))


def test_every_refusal_code_that_reaches_the_screen_has_a_sentence() -> None:
    codes = _codes_de_l_api()
    cles = _cles_du_dictionnaire()

    assert codes, "aucun code de refus trouvé : l'extraction est cassée, pas le produit"

    attendus = {code for code in codes if code not in HORS_DU_RETOUR}
    manquants = sorted(attendus - cles)

    assert not manquants, (
        "ces codes de refus peuvent atteindre l'écran de connexion sans phrase "
        f"à afficher, donc sous un message générique : {manquants}. "
        "Ajouter la clé `login.error.<code>` dans apps/web/src/lib/i18n.ts, ou "
        "déclarer le code dans HORS_DU_RETOUR en écrivant par quel autre "
        "chemin l'utilisateur en est informé."
    )


def test_no_sentence_is_written_for_a_code_that_no_longer_exists() -> None:
    """Une phrase orpheline ne s'affiche jamais, et personne ne le voit.

    C'est la forme exacte du défaut d'origine : `unverified_email` existait
    dans le dictionnaire, l'API émettait `email_not_verified`, et la phrase
    attendait un code qui n'arrivait pas.
    """
    codes = _codes_de_l_api()
    cles = _cles_du_dictionnaire()

    orphelines = sorted(cles - codes - set(SANS_CODE_D_API))

    assert not orphelines, (
        f"ces phrases n'ont aucun code qui les déclenche : {orphelines}. "
        "Soit le code a été renommé côté API et la phrase est restée en "
        "arrière, soit la phrase est un repli qu'il faut déclarer dans "
        "SANS_CODE_D_API avec sa raison d'être."
    )


def test_the_two_declared_exemption_lists_stay_honest() -> None:
    """Une exemption qui ne correspond plus à rien se retire.

    Sans ce contrôle, les deux listes ci-dessus deviendraient le tapis sous
    lequel on glisse les codes gênants : un code supprimé de l'API y
    resterait pour toujours, et le test continuerait à passer en couvrant de
    moins en moins de choses.
    """
    codes = _codes_de_l_api()
    cles = _cles_du_dictionnaire()

    exemptions_perimees = sorted(set(HORS_DU_RETOUR) - codes)
    assert not exemptions_perimees, (
        f"ces codes exemptés n'existent plus dans l'API : {exemptions_perimees}"
    )

    replis_perimes = sorted(set(SANS_CODE_D_API) - cles)
    assert not replis_perimes, (
        f"ces replis déclarés n'existent plus dans le dictionnaire : {replis_perimes}"
    )


def test_every_notice_the_api_can_post_has_a_sentence() -> None:
    """Un avis sans phrase ne s'affiche pas, et personne ne s'en aperçoit.

    La page se TAIT sur un avis qu'elle ne sait pas traduire, et c'est le bon
    repli : la personne est entrée, et une phrase générique sur un écran qui
    fonctionne n'apprendrait rien tout en inquiétant.

    Mais ce silence est exactement ce qui rend la dérive invisible. Un refus
    muet se remarque — l'utilisateur est bloqué et le dit. Un avis muet ne se
    remarque jamais. Il doit donc être attrapé ici, et nulle part ailleurs.
    """
    codes = _avis_de_l_api()
    cles = _cles_d_avis_du_dictionnaire()

    assert codes, "aucun code d'avis trouvé : l'extraction est cassée, pas le produit"

    manquants = sorted(codes - cles)
    assert not manquants, (
        f"ces avis n'ont aucune phrase à afficher, donc ne s'afficheront pas : "
        f"{manquants}. Ajouter la clé `login.notice.<code>` dans "
        "apps/web/src/lib/i18n.ts."
    )

    orphelines = sorted(cles - codes)
    assert not orphelines, f"ces phrases d'avis n'ont aucun code qui les déclenche : {orphelines}"


def test_a_notice_is_never_also_a_refusal() -> None:
    """Les deux vocabulaires ne se recouvrent pas, et c'est voulu.

    Un même code qui serait à la fois un refus et un avis s'afficherait tantôt
    en rouge avec « role=alert », tantôt en bleu avec « role=status », selon le
    paramètre qui l'a porté. L'utilisateur ne saurait pas s'il est entré.
    """
    communs = _avis_de_l_api() & _codes_de_l_api()
    assert not communs, f"ces codes sont à la fois un refus et un avis : {sorted(communs)}"
