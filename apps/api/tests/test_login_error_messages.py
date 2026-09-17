"""Chaque refus de connexion arrive-t-il à l'écran en phrase, ou en repli ?

**Ce fichier ne teste pas le client web.** Il vérifie que les deux moitiés du
parcours de connexion parlent de la même chose : les codes que l'API peut
déposer dans `?login_error=…`, et les phrases que `apps/web/src/lib/i18n.ts`
sait leur associer.

Sans ce contrôle, l'écart se creuse en silence. Il s'était déjà creusé deux
fois : une traduction nommée `unverified_email` pour un code qui s'appelle
`email_not_verified` — donc jamais affichée —, et neuf codes sans aucune
traduction, dont `account_disabled`, qui envoyait « La connexion a échoué. » à
quelqu'un dont le compte attendait simplement d'être réactivé.

Le repli générique reste utile — il empêche un code technique de s'afficher
brut. Mais il doit rester un filet, pas la réponse habituelle.
"""

from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parents[3]
SOURCES_API = RACINE / "apps" / "api" / "src" / "metreo_api"
DICTIONNAIRE = RACINE / "apps" / "web" / "src" / "lib" / "i18n.ts"

#: Ce que l'API construit pour refuser une connexion, sous ses deux formes :
#: l'exception du service, et le paramètre que le routeur met dans l'URL de
#: retour. Les deux portent des chaînes littérales — aucun code n'est calculé.
MOTIFS = (
    re.compile(r'OidcError\(\s*"([a-z_]+)"'),
    re.compile(r'login_error="([a-z_]+)"'),
)


def codes_emis_par_l_api() -> set[str]:
    codes: set[str] = set()
    for fichier in SOURCES_API.rglob("*.py"):
        texte = fichier.read_text(encoding="utf-8")
        for motif in MOTIFS:
            codes.update(motif.findall(texte))
    return codes


def codes_traduits() -> set[str]:
    texte = DICTIONNAIRE.read_text(encoding="utf-8")
    return set(re.findall(r"'login\.error\.([a-z_]+)'", texte)) - {"generic"}


def test_the_extraction_actually_finds_the_login_error_codes() -> None:
    """Un extracteur qui ne trouve plus rien ferait passer le test suivant.

    Deux codes connus servent de témoin : s'ils disparaissent du résultat,
    c'est l'extraction qui est cassée, pas le produit.
    """
    codes = codes_emis_par_l_api()
    assert {"no_membership", "unknown_user"} <= codes
    assert len(codes) >= 15, f"extraction suspecte : {sorted(codes)}"


def test_every_refusal_the_api_can_emit_reaches_the_screen_as_a_sentence() -> None:
    sans_phrase = sorted(codes_emis_par_l_api() - codes_traduits())
    assert not sans_phrase, (
        "Ces refus s'afficheraient en « La connexion a échoué. », sans dire quoi "
        f"faire : {sans_phrase}. Ajoutez-leur une clé « login.error.<code> » dans "
        "apps/web/src/lib/i18n.ts."
    )


def test_no_translation_waits_for_a_code_that_no_longer_exists() -> None:
    """Une phrase sans code est une phrase que personne ne verra jamais.

    C'est ce qui était arrivé à `unverified_email` : écrite, relue, traduite —
    et morte, parce que l'API dit `email_not_verified`.
    """
    orphelines = sorted(codes_traduits() - codes_emis_par_l_api())
    assert not orphelines, (
        f"Ces traductions ne correspondent à aucun code émis : {orphelines}. "
        "Soit le code a été renommé, soit la phrase est morte."
    )
