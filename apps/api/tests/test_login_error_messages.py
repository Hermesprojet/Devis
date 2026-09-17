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
#: retour.
#:
#: Limite assumée : seuls les codes écrits en toutes lettres sont vus. Un code
#: passé par une variable échapperait à cette lecture — d'où le témoin plus
#: bas, qui refuse une extraction devenue muette.
MOTIFS = (
    re.compile(r'OidcError\(\s*"([a-z0-9_]+)"'),
    re.compile(r'login_error="([a-z0-9_]+)"'),
)

#: Le dictionnaire français, et lui seul.
DEBUT_DICTIONNAIRE_FR = "const fr: Dictionary = {"


def codes_emis_par_l_api() -> set[str]:
    codes: set[str] = set()
    for fichier in SOURCES_API.rglob("*.py"):
        texte = fichier.read_text(encoding="utf-8")
        for motif in MOTIFS:
            codes.update(motif.findall(texte))
    return codes


def dictionnaire_francais() -> str:
    """Le corps du dictionnaire `fr`, commentaires retirés.

    Lire le fichier entier ferait exactement l'erreur que ce fichier combat.
    Une entrée mise en commentaire resterait « trouvée », et une clé déplacée
    dans le dictionnaire `nl` — vide aujourd'hui, rempli en phase 5 — le
    serait aussi. Dans les deux cas `translate` rend la clé, l'écran affiche
    le repli générique, et un test qui lit le fichier entier reste vert.
    """
    texte = DICTIONNAIRE.read_text(encoding="utf-8")
    debut = texte.index(DEBUT_DICTIONNAIRE_FR)
    # La première accolade fermante en début de ligne clôt l'objet littéral.
    fin = texte.index("\n}\n", debut)
    corps = texte[debut:fin]
    # `^\s*//` et non `//` : « http:// » vit à l'intérieur de certaines
    # valeurs, et ne commente rien.
    return re.sub(r"^\s*//.*$", "", corps, flags=re.MULTILINE)


def codes_traduits() -> set[str]:
    motif = r"'login\.error\.([a-z0-9_]+)'"
    return set(re.findall(motif, dictionnaire_francais())) - {"generic"}


def test_the_extraction_actually_finds_the_login_error_codes() -> None:
    """Un extracteur qui ne trouve plus rien ferait passer les tests suivants.

    Des codes connus servent de témoin des deux côtés : s'ils disparaissent du
    résultat, c'est l'extraction qui est cassée, pas le produit.
    """
    codes = codes_emis_par_l_api()
    assert {"no_membership", "unknown_user"} <= codes
    assert len(codes) >= 15, f"extraction côté API suspecte : {sorted(codes)}"

    traduits = codes_traduits()
    assert {"no_membership", "unknown_user"} <= traduits
    assert len(traduits) >= 15, f"extraction côté dictionnaire suspecte : {sorted(traduits)}"


def test_a_commented_out_translation_does_not_count_as_present() -> None:
    """La complaisance que ce fichier doit refuser, éprouvée sur du vrai texte.

    Commenter une entrée la laisse dans le fichier mais la retire du
    dictionnaire : `translate` rend la clé, l'écran affiche le repli. Une
    lecture naïve du fichier la compterait présente.
    """
    corps = dictionnaire_francais()
    assert "'login.error.unknown_user'" in corps

    commente = re.sub(
        r"^(\s*)('login\.error\.unknown_user')",
        r"\1// \2",
        corps,
        flags=re.MULTILINE,
    )
    assert commente != corps, "la mutation n'a rien changé : le témoin ne prouve rien"
    restant = re.sub(r"^\s*//.*$", "", commente, flags=re.MULTILINE)
    assert "'login.error.unknown_user'" not in restant


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
