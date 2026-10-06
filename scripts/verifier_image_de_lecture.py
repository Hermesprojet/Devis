"""Éprouve, DANS l'image construite, que la lecture d'un plan fonctionne.

    python3 scripts/verifier_image_de_lecture.py

Ce contrôle existe à cause d'un défaut précis : `infra/api.Dockerfile`
installait `apps/api[postgres]` SANS l'extra `plans`. L'image démarrait,
répondait à tous ses points de santé, servait l'écran « Lire le plan » — et
tombait sur un `ModuleNotFoundError` au premier plan déposé. Aucun des
contrôles existants ne pouvait le voir : ils éprouvent le code, pas l'image.

Vérifier que `ezdxf` s'importe ne suffirait pas davantage. On lit donc un
VRAI fichier, on compare la cote à une valeur connue d'avance, on rend le
dessin, et on vérifie qu'un fichier d'un type non lu est refusé par un code
nommé plutôt que par une exception quelconque.

**Le PDF a le même problème et il est éprouvé de la même façon.** `pypdfium2`
est un second extra, `pdf`, installé dans l'image par le même Dockerfile :
l'oublier reproduirait exactement le défaut d'origine, un écran qui s'affiche
et un `ModuleNotFoundError` au premier fichier déposé. La section 5 lit donc
un PDF fabriqué sur place, compare la position d'un texte à une valeur connue
— c'est l'inversion du repère vertical qui est en jeu — et produit un aperçu
PNG.

Ce script ne touche ni base, ni réseau, ni volume de stockage : il écrit ses
fixtures dans un répertoire temporaire, les relit, et les jette. Il tourne
donc à l'identique dans l'image, dans la CI et sur un poste.
"""

from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

# La fabrique de fixtures est partagée avec les tests du dépôt : une seconde
# copie des octets d'un PDF aurait divergé de celle que les tests éprouvent.
# Elle vit à côté de ce script, qui est monté avec son dossier.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fabriquer_pdf_de_test

#: La cote posée dans le plan d'essai, en millimètres. Connue d'avance : c'est
#: toute la différence entre « la lecture a rendu quelque chose » et « la
#: lecture a rendu la bonne valeur ».
COTE_ATTENDUE = Decimal("5000")

#: Tolérance de comparaison. La cote est recalculée par ezdxf à partir des
#: points de définition : un flottant peut rendre 4999.999999999999.
TOLERANCE = Decimal("0.001")

#: Au-dessus, la boîte du texte de référence n'est pas dans le quart haut de
#: l'écran — donc l'axe vertical n'a pas été inversé. Calculé : le texte est à
#: 80 pt du bord bas d'une page de 100 pt, sa hampe monte à 88,4 pt, donc
#: `y1 = 1 − 79,8/100 ≈ 0,202`. Sans inversion il vaudrait 0,884.
Y_ECRAN_MAXIMUM = 0.25

#: La boîte d'ENCRE du texte de référence, relevée au pixel sur un rendu, pour
#: chacune des quatre rotations d'affichage. Ces nombres ne viennent pas d'un
#: raisonnement sur les conventions du format : la page a été rendue en PNG et
#: les pixels sombres ont été comptés. Déduire une convention de rotation a une
#: chance sur huit d'être juste, et se tromper ne casse rien — le surlignage
#: tombe ailleurs, et personne ne s'en aperçoit.
ENCRE_PAR_ROTATION: dict[int, tuple[float, float, float, float]] = {
    0: (0.102, 0.115, 0.228, 0.195),
    90: (0.800, 0.102, 0.880, 0.228),
    180: (0.767, 0.800, 0.895, 0.880),
    270: (0.115, 0.770, 0.195, 0.895),
}

_echecs: list[str] = []
_controles = 0


def exiger(condition: bool, intitule: str, constat: str) -> None:
    """Enregistre un contrôle, et ne s'arrête pas au premier échec.

    Rendre la main sur le premier défaut cacherait les suivants ; un
    diagnostic complet vaut mieux qu'un diagnostic rapide.
    """
    global _controles
    _controles += 1
    if condition:
        print(f"  ok   {intitule} — {constat}")
    else:
        print(f"  NON  {intitule} — {constat}")
        _echecs.append(f"{intitule} : {constat}")


def fabriquer_le_plan(dossier: Path) -> Path:
    """Un mur coté de 5 000 mm, en R2000 pour que `$INSUNITS` soit exporté.

    R12 n'écrit pas `$INSUNITS`, et un plan sans unité n'est pas mesurable :
    le plan d'essai doit donc être au moins R2000, sans quoi ce contrôle
    prouverait le contraire de ce qu'il veut prouver.
    """
    import ezdxf

    document = ezdxf.new("R2000", setup=False)
    document.header["$INSUNITS"] = 4  # millimètres
    document.layers.add("MURS")
    document.layers.add("COTATIONS")
    espace = document.modelspace()
    espace.add_line((0, 0), (5000, 0), dxfattribs={"layer": "MURS"})
    espace.add_line((0, 0), (0, 2500), dxfattribs={"layer": "MURS"})
    cotation = espace.add_linear_dim(
        base=(0, -800), p1=(0, 0), p2=(5000, 0), dxfattribs={"layer": "COTATIONS"}
    )
    cotation.render()

    chemin = dossier / "mur_cote.dxf"
    document.saveas(chemin)
    return chemin


def fabriquer_un_type_non_lu(dossier: Path) -> Path:
    """Un CSV : un type que Metreo accepte au dépôt et ne lit PAS comme un plan.

    Ce fichier portait un PDF jusqu'à ce que le PDF devienne lisible. Le
    contrôle, lui, garde tout son sens : la frontière doit se dire par un code
    nommé, et non par une exception quelconque.
    """
    chemin = dossier / "bordereau.csv"
    chemin.write_bytes(b"code;designation;unite;pu\nA1;Beton;m3;120.00\n")
    return chemin


def controler_les_bibliotheques() -> bool:
    """Le minimum : sans elles, rien d'autre n'a de sens."""
    print("1. Les bibliothèques de lecture sont dans l'image")
    try:
        import ezdxf
    except ModuleNotFoundError as erreur:
        exiger(False, "ezdxf importable", f"absent de l'image — {erreur}")
        return False
    exiger(True, "ezdxf importable", f"version {ezdxf.__version__}")

    try:
        import PIL
    except ModuleNotFoundError as erreur:
        # Pillow est une dépendance DURE du module de dessin d'ezdxf :
        # `ezdxf/addons/drawing/frontend.py` fait `import PIL.Image` sans
        # garde. Son absence ne se voit qu'au rendu.
        exiger(False, "Pillow importable", f"absent de l'image — {erreur}")
        return False
    exiger(True, "Pillow importable", f"version {PIL.__version__}")
    return True


def controler_la_lecture(plan: Path) -> None:
    """La cote lue est-elle la cote posée ?"""
    from metreo_api.services.lecture_dxf import lire

    print("2. La lecture d'un DXF rend la cote attendue")
    constat = lire(plan, situer=True)

    exiger(not constat.refuse, "le plan n'est pas refusé", f"refuse={constat.refuse}")
    exiger(
        constat.unite_source == "mm",
        "l'unité du document est lue",
        f"unite_source={constat.unite_source!r}, $INSUNITS={constat.insunits}",
    )
    exiger(
        len(constat.cotations) == 1,
        "une cotation et une seule est récoltée",
        f"{len(constat.cotations)} cotation(s)",
    )
    if not constat.cotations:
        return

    cotation = constat.cotations[0]
    valeur = cotation.valeur
    exiger(
        valeur is not None and abs(valeur - COTE_ATTENDUE) < TOLERANCE,
        "la valeur lue est celle qui a été posée",
        f"{valeur} mm, attendu {COTE_ATTENDUE} mm",
    )
    exiger(
        cotation.fiabilite == "mesurable",
        "la cotation est exploitable",
        f"fiabilite={cotation.fiabilite!r}, anomalies={[a.code for a in cotation.anomalies]}",
    )
    exiger(
        cotation.famille == "lineaire",
        "la famille est conservée",
        f"famille={cotation.famille!r}",
    )
    exiger(
        bool(cotation.object_ref),
        "la cotation porte son handle, donc sa citation est ancrable",
        f"object_ref={cotation.object_ref!r}, calque={cotation.calque!r}",
    )
    cadre = cotation.cadre
    exiger(
        cadre is not None
        and all(0.0 <= c <= 1.0 for c in (cadre.x0, cadre.y0, cadre.x1, cadre.y1)),
        "la cotation est située dans l'image, en coordonnées normalisées",
        f"cadre={cadre}",
    )


def fabriquer_le_plan_a_blocs(dossier: Path) -> Path:
    """Un plan dont les cotes vivent dans des BLOCS, comme un plan réel.

    Trois insertions du même bloc coté de 5 000 mm : une à l'identique, une à
    l'échelle 2, et une par un bloc intermédiaire. C'est la forme qu'un
    dessinateur produit, et c'est celle qui rendait zéro avant le parcours des
    blocs.
    """
    import ezdxf

    document = ezdxf.new("R2000", setup=False)
    document.header["$INSUNITS"] = 4
    document.layers.add("COTATIONS")
    bloc = document.blocks.new("FACADE_COTEE")
    cotation = bloc.add_linear_dim(
        base=(0, -800), p1=(0, 0), p2=(5000, 0), dxfattribs={"layer": "COTATIONS"}
    )
    cotation.render()
    enveloppe = document.blocks.new("ETAGE")
    enveloppe.add_blockref("FACADE_COTEE", (1000, 2000))

    espace = document.modelspace()
    espace.add_line((0, 0), (5000, 0))
    espace.add_blockref("FACADE_COTEE", (0, 0))
    espace.add_blockref("FACADE_COTEE", (0, 40000), dxfattribs={"xscale": 2, "yscale": 2})
    espace.add_blockref("ETAGE", (20000, 0))

    chemin = dossier / "plan_a_blocs.dxf"
    document.saveas(chemin)
    return chemin


def controler_les_cotes_en_blocs(plan: Path) -> None:
    """Les cotes des blocs sont-elles lues, à la bonne valeur, sans doublon ?

    Le défaut que ce contrôle ferme ne se voit pas dans l'image seulement : il
    se voyait partout. `modelspace.query("DIMENSION")` ne lit que le premier
    niveau, et un plan dont les cotes vivent dans des blocs rendait **zéro
    cotation** — pas une erreur, pas une anomalie : zéro, en silence.
    """
    from metreo_api.services.lecture_dxf import lire

    print("3. Les cotes contenues dans des blocs sont lues, et comptées une fois")
    constat = lire(plan, situer=True)

    exiger(not constat.refuse, "le plan à blocs n'est pas refusé", f"refuse={constat.refuse}")
    exiger(
        len(constat.cotations) == 3,
        "une cotation par INSERTION, ni par définition ni en double",
        f"{len(constat.cotations)} cotation(s), attendu 3 — "
        "(0 signifierait que les blocs ne sont pas parcourus, "
        "4 ou plus que `*Model_Space` est compté deux fois)",
    )
    if len(constat.cotations) != 3:
        return

    valeurs = sorted(str(c.valeur) for c in constat.cotations)
    exiger(
        valeurs == ["10000.0", "5000.0", "5000.0"],
        "l'insertion à l'échelle 2 double la longueur mesurée",
        f"valeurs lues : {valeurs}",
    )

    references = {c.object_ref for c in constat.cotations}
    exiger(
        len(references) == 3,
        "chaque instance se désigne séparément, donc la base en garde trois",
        f"désignations : {sorted(references)}",
    )
    exiger(
        any("/" in reference for reference in references),
        "une cotation de bloc porte son chemin d'insertion",
        f"désignations : {sorted(references)}",
    )

    positions = {
        (round(c.cadre.x0, 6), round(c.cadre.y0, 6))
        for c in constat.cotations
        if c.cadre is not None
    }
    exiger(
        len(positions) == 3,
        "les trois cotations sont situées à trois endroits distincts",
        f"{len(positions)} position(s) distincte(s)",
    )
    exiger(
        all(c.calque == "COTATIONS" for c in constat.cotations),
        "le calque traverse le bloc",
        f"calques : {sorted({c.calque for c in constat.cotations})}",
    )


def controler_le_rendu(plan: Path) -> None:
    """Le dessin arrive-t-il jusqu'à une image affichable ?"""
    from metreo_api.services.rendu_de_plan import rendre

    print("4. Le rendu produit un SVG affichable")
    rendu = rendre(plan)

    exiger(
        rendu.svg.lstrip().startswith("<svg") or "<svg" in rendu.svg[:512],
        "le rendu est bien un SVG",
        f"{len(rendu.svg)} octets, {rendu.entites_rendues} entités",
    )
    exiger("viewBox" in rendu.svg, "le SVG garde son viewBox", "viewBox présent")
    exiger(
        'width="' in rendu.svg and 'height="' in rendu.svg,
        "le SVG porte une taille en pixels",
        # Sans elle, une image sans taille intrinsèque dans une balise `img`
        # à `auto` n'a AUCUNE taille : le plan est dans la page, et invisible.
        "width et height présents",
    )
    exiger(
        rendu.entites_rendues > 0,
        "des entités ont réellement été dessinées",
        f"{rendu.entites_rendues} entités",
    )


def controler_le_refus_dun_type_non_lu(fichier: Path) -> None:
    """Un type que la lecture ne traite pas doit être refusé en le disant."""
    from metreo_api.services.document_storage import detecter_type
    from metreo_api.services.lecture_de_plan import PlanNonLisible, verifier_que_cest_un_plan

    print("5. Un type non lu est refusé par un code nommé")
    type_reel = detecter_type(fichier, fichier.stat().st_size)
    exiger(
        type_reel not in ("image/vnd.dxf", "application/pdf"),
        "le type est lu dans les octets, pas dans l'extension",
        f"type réel={type_reel!r}",
    )

    revision = SimpleNamespace(media_type=type_reel)
    try:
        verifier_que_cest_un_plan(revision)  # type: ignore[arg-type]
    except PlanNonLisible as refus:
        exiger(
            refus.code == "type_non_lisible",
            "le refus porte un code stable",
            f"code={refus.code!r}",
        )
    else:
        exiger(False, "le fichier est refusé", "la lecture l'a ACCEPTÉ — défaut grave")


def controler_la_lecture_du_pdf(dossier: Path) -> None:
    """Le PDF : les bibliothèques, la position d'un texte, et l'aperçu.

    La position est le cœur du contrôle, et pas la présence du texte. Un PDF a
    son origine en bas à gauche, un écran en haut à gauche : un lecteur qui
    oublie l'inversion ne plante pas, il surligne le bas du plan quand la cote
    est en haut. Cela ne se voit qu'en comparant à une valeur connue.

    La fixture vient de `fabriquer_pdf_de_test.py`, qui n'a besoin d'aucune
    bibliothèque : elle est donc fabriquée même quand `pypdfium2` manque, ce
    qui permet de dire laquelle des deux choses a échoué.
    """
    print("6. L'image sait lire un PDF et en produire un aperçu")

    try:
        import pypdfium2
    except ModuleNotFoundError as erreur:
        # La répétition exacte du défaut d'origine, pour l'autre extra :
        # l'image s'affiche, et le premier PDF déposé tombe.
        exiger(
            False,
            "pypdfium2 importable",
            f"absent de l'image — {erreur}. L'extra `pdf` n'a pas été installé.",
        )
        return
    exiger(
        True,
        "pypdfium2 importable",
        f"version {version('pypdfium2')} / {pypdfium2.__name__}",
    )

    from metreo_api.services.lecture_pdf import lire
    from metreo_api.services.rendu_pdf import rendre, rendre_une_zone

    chemin = dossier / "reference.pdf"
    chemin.write_bytes(fabriquer_pdf_de_test.une_page_avec_texte())

    constat = lire(chemin)
    exiger(not constat.refuse, "le PDF n'est pas refusé", f"refuse={constat.refuse}")
    exiger(
        constat.pages == 1 and constat.dimensions == [(200.0, 100.0)],
        "les dimensions de page sont lues en points PostScript, sans conversion",
        f"pages={constat.pages}, dimensions={constat.dimensions}",
    )
    exiger(
        len(constat.fragments) == 1,
        "un fragment de texte et un seul est récolté",
        f"{len(constat.fragments)} fragment(s)",
    )
    if not constat.fragments:
        return

    fragment = constat.fragments[0]
    exiger(
        fragment.texte == fabriquer_pdf_de_test.TEXTE_REFERENCE,
        "le texte est rendu tel qu'il est écrit",
        f"texte={fragment.texte!r}, attendu {fabriquer_pdf_de_test.TEXTE_REFERENCE!r}",
    )
    exiger(fragment.page == 1, "la page est comptée depuis 1", f"page={fragment.page}")

    # Le texte est posé à 80 pt du bord BAS d'une page de 100 pt : dans le
    # repère de l'écran, sa boîte doit donc tomber dans le quart HAUT.
    cadre = fragment.cadre
    exiger(
        cadre is not None and fragment.position == "exacte",
        "le fragment est situé exactement, sans rognage",
        f"position={fragment.position!r}",
    )
    if cadre is None:
        return
    exiger(
        cadre.y1 < Y_ECRAN_MAXIMUM,
        "l'axe vertical est inversé entre le PDF et l'écran",
        f"y0={cadre.y0:.4f}, y1={cadre.y1:.4f} — attendu y1 < {Y_ECRAN_MAXIMUM} "
        "(sans inversion, y1 vaudrait environ 0,88)",
    )
    exiger(
        0.0 <= cadre.x0 < cadre.x1 <= 1.0 and cadre.x0 < 0.2,
        "l'axe horizontal n'est PAS inversé",
        f"x0={cadre.x0:.4f}, x1={cadre.x1:.4f}",
    )

    # L'extraction du texte et le rendu de l'aperçu sont deux bibliothèques
    # différentes derrière la même : la première échouerait sur un PDFium
    # amputé, la seconde sur un Pillow absent.
    apercu = rendre(chemin)
    exiger(
        apercu.png[:8] == b"\x89PNG\r\n\x1a\n",
        "l'aperçu est bien un PNG",
        f"{len(apercu.png)} octets, {apercu.largeur}×{apercu.hauteur} px",
    )
    exiger(
        apercu.largeur == 200 and apercu.hauteur == 100,
        "une page plus petite que la cote affichée est rendue à sa taille",
        f"{apercu.largeur}×{apercu.hauteur} px pour une page de 200×100 pt",
    )

    # Et la couture : un cadre normalisé posé sur l'aperçu tombe dans l'image.
    gauche = round(cadre.x0 * apercu.largeur)
    haut = round(cadre.y0 * apercu.hauteur)
    exiger(
        0 <= gauche < apercu.largeur and 0 <= haut < apercu.hauteur,
        "le cadre du texte se pose directement sur l'aperçu, sans conversion",
        f"coin haut gauche à ({gauche}, {haut}) px dans une image de "
        f"{apercu.largeur}×{apercu.hauteur}",
    )

    # Une page tournée : `get_size()` rend la taille AFFICHÉE tandis que les
    # rectangles de texte restent dans le repère non tourné. Les valeurs
    # attendues viennent d'une boîte d'encre relevée au pixel sur un rendu.
    for rotation, encre in ENCRE_PAR_ROTATION.items():
        tournee = dossier / f"tournee{rotation}.pdf"
        tournee.write_bytes(fabriquer_pdf_de_test.page_tournee(rotation))
        lu = lire(tournee)
        if not lu.fragments or lu.fragments[0].cadre is None:
            exiger(False, f"/Rotate {rotation} : le texte est situé", "aucun cadre")
            continue
        boite = lu.fragments[0].cadre
        obtenu = (boite.x0, boite.y0, boite.x1, boite.y1)
        ecart = max(abs(a - b) for a, b in zip(obtenu, encre, strict=True))
        exiger(
            ecart < 0.02,
            f"/Rotate {rotation} : la position suit la rotation de la page",
            f"écart maximal {ecart:.4f} avec l'encre relevée au pixel "
            f"{tuple(round(v, 3) for v in encre)}",
        )

    # Une page dont le coin bas gauche n'est pas (0,0) : le texte y est au même
    # endroit RELATIF, donc le cadre doit être le même.
    decalee = dossier / "decalee.pdf"
    decalee.write_bytes(fabriquer_pdf_de_test.page_avec_boite_decalee())
    lu = lire(decalee)
    autre = lu.fragments[0].cadre if lu.fragments else None
    exiger(
        autre is not None
        and abs(autre.x0 - cadre.x0) < 1e-6
        and abs(autre.y0 - cadre.y0) < 1e-6
        and autre.y1 > autre.y0,
        "une page dont la boîte ne commence pas à (0,0) se lit pareil",
        f"cadre={autre}"
        + (
            ""
            if autre is None
            else f" contre {cadre} — une boîte plate serait refusée en base"
        ),
    )

    # La tuile de détail : sans elle, « confirmer ou corriger » demanderait
    # d'ouvrir le PDF hors de Metreo. Mesuré sur les plans réels : à 2 000 px
    # de grand côté, un texte fait 3,5 à 4,4 pixels de haut.
    tuile = rendre_une_zone(
        chemin, page=1, zone=(cadre.x0, cadre.y0, cadre.x1, cadre.y1)
    )
    exiger(
        tuile.png[:8] == b"\x89PNG\r\n\x1a\n"
        and tuile.hauteur_du_texte_px is not None
        and tuile.hauteur_du_texte_px > 4.0,
        "une tuile de détail agrandit le texte assez pour le relire",
        f"{tuile.largeur}×{tuile.hauteur} px, texte "
        f"{tuile.hauteur_du_texte_px:.0f} px, {len(tuile.png)} octets",
    )

    # Un PDF chiffré : le refus doit NOMMER le chiffrement, et Metreo ne doit
    # jamais demander de mot de passe.
    protege = dossier / "protege.pdf"
    protege.write_bytes(fabriquer_pdf_de_test.chiffre())
    refus = lire(protege)
    motif = refus.motif_du_refus
    exiger(
        refus.refuse and motif is not None and motif.code == "pdf_chiffre",
        "un PDF chiffré est refusé en nommant le chiffrement",
        f"refuse={refus.refuse}, code={motif.code if motif else None!r}",
    )
    exiger(
        not refus.fragments,
        "rien du contenu chiffré ne ressort",
        f"{len(refus.fragments)} fragment(s)",
    )


def controler_les_fixtures(dossier: Path) -> None:
    """Les trois fichiers commités, relus par l'image elle-même.

    Ils couvrent ce que le plan fabriqué ne couvre pas : un plan sans unité,
    et un fichier cassé. Un lecteur qui tombe sur un fichier tronqué au lieu
    de le refuser en le nommant est un lecteur qu'on ne peut pas exposer à
    des fichiers venus de l'extérieur.
    """
    from metreo_api.services.lecture_dxf import lire

    print(f"7. Les fixtures commitées se relisent dans l'image ({dossier})")

    nominal = dossier / "mur_simple.dxf"
    if nominal.exists():
        constat = lire(nominal)
        exiger(
            not constat.refuse and constat.unite_source == "mm",
            "mur_simple.dxf : lecture nominale",
            f"refuse={constat.refuse}, unite={constat.unite_source!r}, "
            f"{len(constat.cotations)} cotation(s)",
        )
    else:
        exiger(False, "mur_simple.dxf présent", f"introuvable dans {dossier}")

    sans_unites = dossier / "sans_unites.dxf"
    if sans_unites.exists():
        constat = lire(sans_unites)
        codes = [a.code for a in constat.anomalies]
        exiger(
            constat.unite_source is None and bool(codes),
            "sans_unites.dxf : l'unité absente est signalée, pas supposée",
            f"unite={constat.unite_source!r}, anomalies={codes}",
        )
    else:
        exiger(False, "sans_unites.dxf présent", f"introuvable dans {dossier}")

    tronque = dossier / "tronque.dxf"
    if tronque.exists():
        constat = lire(tronque)
        motif = constat.motif_du_refus
        exiger(
            constat.refuse and motif is not None,
            "tronque.dxf : le fichier incomplet est refusé en nommant la cause",
            f"refuse={constat.refuse}, motif={motif.code if motif else None!r}",
        )
    else:
        exiger(False, "tronque.dxf présent", f"introuvable dans {dossier}")


def main() -> int:
    fixtures: Path | None = None
    arguments = sys.argv[1:]
    if arguments:
        if arguments[0] != "--fixtures" or len(arguments) != 2:
            print(__doc__)
            print("usage : verifier_image_de_lecture.py [--fixtures RÉPERTOIRE]")
            return 2
        fixtures = Path(arguments[1])

    print("Épreuve de l'image : lire un plan, refuser ce qui ne se lit pas")
    print(f"python {sys.version.split()[0]} — {sys.executable}")
    print()

    if not controler_les_bibliotheques():
        print()
        print("ÉCHEC : les bibliothèques de lecture ne sont pas dans cet environnement.")
        print("Cause la plus probable : l'extra `plans` n'a pas été installé.")
        return 1

    with tempfile.TemporaryDirectory(prefix="metreo-epreuve-") as brouillon:
        dossier = Path(brouillon)
        plan = fabriquer_le_plan(dossier)
        print(f"  plan d'essai : {plan.stat().st_size} octets")
        print()
        controler_la_lecture(plan)
        print()
        controler_les_cotes_en_blocs(fabriquer_le_plan_a_blocs(dossier))
        print()
        controler_le_rendu(plan)
        print()
        controler_le_refus_dun_type_non_lu(fabriquer_un_type_non_lu(dossier))
        print()
        controler_la_lecture_du_pdf(dossier)

    if fixtures is not None:
        print()
        controler_les_fixtures(fixtures)

    print()
    if _echecs:
        print(f"ÉCHEC : {len(_echecs)} contrôle(s) sur {_controles} n'ont pas passé.")
        for echec in _echecs:
            print(f"  - {echec}")
        return 1
    print(f"Les {_controles} contrôles passent : cette image sait lire un plan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
