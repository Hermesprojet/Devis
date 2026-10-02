"""Rendre un plan DXF en une image affichable, sans logiciel externe.

Ce module produit un SVG et **rien d'autre** : il ne mesure pas, il n'écrit
pas en base, il ne parle à aucun service. Son unique raison d'être est qu'un
utilisateur puisse VOIR le plan qu'il vient de déposer, dans Metreo, sans
installer AutoCAD.

**Pourquoi un SVG et non une image matricielle.** Un plan d'exécution est du
trait fin sur grand format. Mesuré sur un plan réel : rendu en SVG, 4,49 Mo
bruts mais **0,41 Mo compressés** — onze fois moins — et le zoom reste net à
tous les niveaux. Une image matricielle lisible au même grossissement se
compterait en dizaines de mégaoctets et il faudrait la refaire à chaque
niveau de zoom.

**Pourquoi hors de la requête HTTP.** Mesuré sur deux plans réels de 7,4 et
11,2 Mo : **7,3 s et 8,8 s** du fichier au SVG, pour 10 189 et 33 574 tracés.
Une requête HTTP ne tient pas huit secondes, et un plan plus lourd en
prendrait davantage. Le rendu appartient donc au travail hors requête, et son
résultat est posé comme artefact dérivé ; l'API ne fait plus que le servir.

**Ce qui n'est pas garanti ici.** Le délai maximal n'est PAS appliqué par ce
module : interrompre une boucle de rendu en cours demanderait de l'armer dans
le processus appelant. C'est la règle de l'ADR 0007 — un processus par
fichier, borné par son appelant — et `scripts/lire_un_plan.py` l'arme. Ce
module borne ce qu'il peut borner avant de commencer : le nombre d'entités.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Au-delà, on ne rend pas : on refuse en le disant.
#:
#: La borne porte sur les entités de l'espace modèle. **Elle ne protège pas
#: de l'amplification, et il faut le dire** : mesuré, 10 189 entités
#: produisent 33 574 tracés — une référence de bloc en vaut des centaines — et
#: surtout **une SEULE entité de hachure produit 17,94 Mo de SVG en 10,8 s**.
#: Compter les entités, ou même les tracés, ne borne donc rien ; c'est ce que
#: la politique de hachure ci-dessous corrige à la racine.
#:
#: 200 000 reste une borne de garde : elle arrête un fichier manifestement
#: hors d'échelle avant de commencer, et laisse passer vingt fois le plus gros
#: des plans mesurés.
PLAFOND_ENTITES = 200_000

#: Au-delà, le SVG produit est jeté plutôt que posé.
#:
#: Mesuré : les deux plans réels rendent 4,49 et 4,66 Mo. Vingt-quatre mébi
#: octets laissent donc cinq fois la marge, et au-delà aucun navigateur
#: n'affiche utilement le résultat — il le rastérise à chaque changement
#: d'échelle.
#:
#: Cette borne arrive APRÈS la sérialisation : elle ne protège pas la mémoire,
#: elle protège le service et le navigateur. La mémoire, elle, ne se protège
#: que depuis l'extérieur (voir plus bas).
PLAFOND_OCTETS_SVG = 24 * 1024 * 1024

#: Ce qu'un SVG servi à un navigateur ne doit pas contenir.
#:
#: Vérifié sur les deux plans réels : le rendu d'ezdxf 1.4.4 n'en produit
#: aucun — pas même une balise `<text>`, les textes du plan étant tracés comme
#: des chemins. Le contrôle reste, parce qu'il ne coûte rien et qu'il survivra
#: à une version d'ezdxf qui changerait d'avis : un SVG est un document, et un
#: document peut porter du script.
MOTIFS_INTERDITS: tuple[str, ...] = (
    "<script",
    "<foreignobject",
    "<!entity",
    "<iframe",
    "javascript:",
    "xlink:href",
    "<image",
)

#: Un gestionnaire d'événement, cherché en motif et non en sous-chaîne.
#:
#: Une première version cherchait la sous-chaîne « on » précédée d'une espace.
#: C'était une faute : elle refuse un SVG légitime dès qu'un attribut ou un
#: nom de style commence par ces deux lettres, et un refus à tort est ici pire
#: qu'une absence de contrôle — l'utilisateur ne verrait plus son plan sans
#: comprendre pourquoi. Le motif exige donc la forme complète d'un attribut.
MOTIF_GESTIONNAIRE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)

#: Le dossier des artefacts dérivés d'un plan, sur le volume.
DOSSIER_RENDUS = "rendus-de-plan"


#: Les hachures sont tracées en CONTOUR et non en remplissage.
#:
#: C'est la seule borne qui attaque l'amplification à sa racine, et elle a été
#: choisie sur mesure, pas par principe :
#:
#: =========================== ============== ============ ========
#: cas                         remplissage    contour       perte
#: =========================== ============== ============ ========
#: une hachure de 60 m de côté 17,94 Mo        0,00 Mo       tout
#: un plan d'exécution réel     4,49 Mo        4,38 Mo       2,4 %
#: =========================== ============== ============ ========
#:
#: Autrement dit : le motif d'une hachure peut à lui seul peser quatre fois
#: tout un plan, et les vrais plans ne perdent presque rien à n'afficher que
#: ses contours. La durée suit : 10,8 s contre 0,0 s sur la hachure seule.
#:
#: **Ce que cela coûte, et il faut le savoir** : un plan qui distingue deux
#: matériaux par deux MOTIFS de hachure différents ne les distinguera plus à
#: l'écran — les deux auront le même contour. Le fichier, lui, garde tout : la
#: distinction reste lisible en téléchargeant l'original. Si ce cas se
#: présente, `HatchPolicy.SHOW_APPROXIMATE_PATTERN` est l'intermédiaire à
#: éprouver.
#:
#: Construite paresseusement : `HatchPolicy` vient d'ezdxf, qui est un extra
#: optionnel, et ce module doit s'importer sans lui.
_CONFIGURATION_CACHE: object | None = None


def _configuration() -> object:
    global _CONFIGURATION_CACHE
    if _CONFIGURATION_CACHE is None:
        from ezdxf.addons.drawing.config import Configuration, HatchPolicy

        _CONFIGURATION_CACHE = Configuration(hatch_policy=HatchPolicy.SHOW_OUTLINE)
    return _CONFIGURATION_CACHE


@dataclass(frozen=True)
class RenduRefuse(Exception):
    """Le rendu n'a pas eu lieu, et on dit pourquoi en un code stable."""

    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - lisibilité d'un journal
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class Rendu:
    """Un SVG prêt à servir, et le cadre qui lui donne son sens.

    `cadre` est l'étendue du dessin dans ses propres unités. Il est conservé
    parce que c'est LUI qui permet de situer une cotation sur l'image : une
    boîte normalisée dans [0,1] n'a de sens que rapportée au même cadre que
    celui qui a servi au rendu. Les deux doivent donc venir du même passage.
    """

    svg: str
    #: xmin, ymin, xmax, ymax, en unités de dessin.
    cadre: tuple[float, float, float, float]
    entites_rendues: int


def _verifier_innocuite(svg: str) -> None:
    bas = svg.lower()
    for motif in MOTIFS_INTERDITS:
        if motif in bas:
            raise RenduRefuse(
                "rendu_non_servable",
                "Le rendu produit contient une construction qu'un navigateur "
                "pourrait exécuter. Il est jeté plutôt que servi.",
            )
    if MOTIF_GESTIONNAIRE.search(svg) is not None:
        raise RenduRefuse(
            "rendu_non_servable",
            "Le rendu produit porte un gestionnaire d'événement. Il est jeté plutôt que servi.",
        )


#: Le plus grand côté de l'image, en pixels, tel que le SVG l'annonce.
#:
#: Ce n'est PAS une résolution de rendu — un SVG n'en a pas : c'est la taille
#: que le navigateur réserve avant tout zoom, et le rapport de forme reste
#: celui du dessin. Deux mille pixels remplissent un écran ordinaire sans
#: réserver une surface absurde.
COTE_AFFICHEE = 2000


def _dimensions_affichables(svg: str) -> str:
    """Remplace les dimensions physiques du SVG par une taille en pixels.

    **Les deux erreurs à éviter sont opposées, et j'ai fait la seconde avant
    de faire la bonne.**

    ezdxf dimensionne la page sur l'étendue du dessin, en millimètres. Sur un
    plan réel cela donne `width="383696mm"` : le navigateur réserverait quatre
    cents mètres de largeur avant la moindre mise en page.

    Les RETIRER, en se disant que le `viewBox` porte déjà toute la géométrie,
    produit l'autre défaut : un SVG sans dimension intrinsèque, chargé dans
    une balise `<img>` dont la largeur et la hauteur valent `auto`, n'a plus
    de taille du tout. Mesuré dans un navigateur : l'image est bien dans la
    page, avec sa source, et elle est **invisible** — un parcours de bout en
    bout l'a refusée.

    La bonne réponse est donc de les REMPLACER : une taille en pixels, dans le
    rapport de forme du `viewBox`, que la feuille de style peut ensuite
    agrandir ou réduire. Le `viewBox` n'est pas touché : c'est lui qui fait
    tomber au bon endroit un surlignage posé en coordonnées [0,1].
    """
    cadre = re.search(r'<svg\b[^>]*\sviewBox="([^"]+)"', svg)
    largeur, hauteur = COTE_AFFICHEE, COTE_AFFICHEE
    if cadre is not None:
        morceaux = cadre.group(1).replace(",", " ").split()
        if len(morceaux) == 4:
            try:
                etendue_x, etendue_y = float(morceaux[2]), float(morceaux[3])
            except ValueError:
                etendue_x = etendue_y = 0.0
            if etendue_x > 0 and etendue_y > 0:
                if etendue_x >= etendue_y:
                    largeur = COTE_AFFICHEE
                    hauteur = max(1, round(COTE_AFFICHEE * etendue_y / etendue_x))
                else:
                    hauteur = COTE_AFFICHEE
                    largeur = max(1, round(COTE_AFFICHEE * etendue_x / etendue_y))

    remplace, nombre = re.subn(
        r'(<svg\b[^>]*?)\s+width="[^"]*"\s+height="[^"]*"',
        f'\\1 width="{largeur}" height="{hauteur}"',
        svg,
        count=1,
    )
    if nombre:
        return remplace
    # Une version d'ezdxf qui n'écrirait plus ces attributs : on les ajoute,
    # plutôt que de servir une image sans taille.
    return re.sub(
        r"(<svg\b)",
        f'\\1 width="{largeur}" height="{hauteur}"',
        svg,
        count=1,
    )


def rendre(chemin: str | Path, *, plafond_entites: int = PLAFOND_ENTITES) -> Rendu:
    """Rend l'espace modèle d'un DXF en SVG. Lève `RenduRefuse` sinon.

    L'ordre est le même que celui du lecteur, et pour la même raison : l'audit
    passe AVANT tout parcours, parce qu'un cycle de référence de bloc fait du
    parcours une récursion sans fin (code d'audit 104, voir `lecture_dxf`).
    """
    from ezdxf import bbox, recover
    from ezdxf.addons.drawing import Frontend, RenderContext, layout
    from ezdxf.addons.drawing.svg import SVGBackend

    from .lecture_dxf import CODE_CYCLE_DE_BLOCS

    try:
        document, auditeur = recover.readfile(str(chemin), errors="strict")
    except UnicodeDecodeError as erreur:
        raise RenduRefuse(
            "fichier_illisible",
            "Le fichier contient des données que le format ne permet pas de décoder.",
        ) from erreur
    except Exception as erreur:
        raise RenduRefuse(
            "fichier_invalide",
            "Le fichier n'a pas pu être ouvert comme un DXF.",
        ) from erreur

    if CODE_CYCLE_DE_BLOCS in {e.code for e in auditeur.errors}:
        raise RenduRefuse(
            "cycle_de_blocs",
            "Le fichier contient un cycle de référence de bloc : le rendre "
            "serait une récursion sans fin.",
        )

    modelspace = document.modelspace()
    nombre = sum(1 for _ in modelspace)
    if nombre > plafond_entites:
        raise RenduRefuse(
            "plan_trop_charge",
            f"Le plan porte {nombre} entités, au-delà du plafond de "
            f"{plafond_entites} posé pour le rendu.",
        )
    if nombre == 0:
        raise RenduRefuse(
            "plan_vide",
            "L'espace modèle du plan ne contient aucune entité : il n'y a rien à afficher.",
        )

    # Le cadre est calculé AVANT le rendu et rendu à l'appelant, pour que la
    # position d'une cotation et l'image soient rapportées au même repère.
    # Mesuré : 1,37 s sur un plan réel, et le cache rend les 663 boîtes de
    # cotation suivantes en 0,01 s.
    cache = bbox.Cache()
    etendue = bbox.extents(modelspace, cache=cache)
    if not etendue.has_data:
        raise RenduRefuse(
            "plan_sans_etendue",
            "Le plan ne porte aucune géométrie situable : rien ne peut être affiché.",
        )

    backend = SVGBackend()
    try:
        Frontend(RenderContext(document), backend, config=_configuration()).draw_layout(  # type: ignore[arg-type]
            modelspace, finalize=True
        )
    except RecursionError as erreur:
        # Ceinture et bretelles : l'audit a déjà écarté le cycle de blocs
        # déclaré, mais une imbrication profonde et licite reste possible.
        raise RenduRefuse(
            "rendu_trop_profond",
            "Le rendu a atteint la limite de récursion : le plan imbrique trop "
            "de blocs pour être affiché.",
        ) from erreur
    except MemoryError as erreur:
        # Cette garde est VRAIE mais étroite : un rendu qui épuise la mémoire
        # de la machine est tué par le noyau (signal 9) sans qu'aucune
        # exception ne soit levée — vérifié. Elle n'attrape qu'un échec
        # d'allocation que l'interpréteur a vu venir.
        raise RenduRefuse(
            "rendu_trop_lourd",
            "Le rendu a épuisé la mémoire disponible.",
        ) from erreur

    # Un plan dont TOUTES les entités sont sur un calque éteint est un cas
    # parfaitement ordinaire. L'audit ne le signale pas, le compte d'entités
    # n'est pas nul, et l'étendue en porte même une : `bbox.extents` compte
    # une entité invisible que le frontend, lui, saute. Le backend n'a alors
    # rien enregistré, et `get_string` levait « empty bounding box » — une
    # ValueError nue, donc un 500 au lieu d'un refus nommé. Vérifié par
    # exécution avant d'écrire ces six lignes.
    if not backend.records:
        raise RenduRefuse(
            "plan_sans_trace",
            "Rien n'est visible dans ce plan : ses entités sont sur des "
            "calques éteints ou gelés. Le fichier reste consultable et "
            "mesurable ; seule son image ne peut pas être produite.",
        )

    try:
        svg = backend.get_string(layout.Page(0, 0, layout.Units.mm, layout.Margins.all(0)))
    except ValueError as erreur:
        raise RenduRefuse(
            "plan_sans_trace",
            "Le rendu n'a produit aucun tracé situable.",
        ) from erreur

    if len(svg.encode("utf-8")) > PLAFOND_OCTETS_SVG:
        raise RenduRefuse(
            "rendu_trop_lourd",
            f"L'image produite dépasse {PLAFOND_OCTETS_SVG // (1024 * 1024)} Mio. "
            "Elle est jetée : aucun navigateur ne l'afficherait utilement.",
        )

    _verifier_innocuite(svg)
    return Rendu(
        svg=_dimensions_affichables(svg),
        cadre=(
            float(etendue.extmin.x),
            float(etendue.extmin.y),
            float(etendue.extmax.x),
            float(etendue.extmax.y),
        ),
        entites_rendues=nombre,
    )


def cle_du_rendu(organization_id: str, revision_id: str) -> str:
    """La clé de l'artefact de rendu, calculée et non stockée — exprès.

    La clé de l'ORIGINAL ne se recalcule jamais : son extension dépend du type
    réellement détecté dans les octets, et elle est écrite une seule fois en
    base. Ici c'est l'inverse : l'artefact est produit par Metreo, son
    extension est toujours `.svg`, et son identifiant est celui de la révision
    — immuable. La clé est donc déterministe par construction, et un nouveau
    rendu remplace le précédent au même endroit, atomiquement.

    Conséquence voulue : aucune table n'est nécessaire pour retrouver un
    rendu. Son absence se lit en demandant sa taille au volume.
    """
    return f"{DOSSIER_RENDUS}/{organization_id}/{revision_id}.svg"
