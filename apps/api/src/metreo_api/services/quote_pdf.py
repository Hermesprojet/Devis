"""La mise en page du devis : ce que le papier montre, et dans quel ordre.

Tout vient des INSTANTANÉS du devis émis, jamais des tables vivantes. C'est la
règle qui rend le document stable : modifier la fiche client demain ne change
pas ce qu'un client a reçu hier.

Ce que la page porte, de haut en bas : l'identité légale de l'entreprise, le
bloc destinataire, le cartouche du devis (numéro, dates), le chantier, le
tableau des postes, les totaux, les conditions, puis les deux zones de
signature. Rien d'autre — et surtout aucune mention légale inventée : les
conditions sont saisies par l'entreprise et imprimées telles quelles.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from . import pdf as moteur
from .images import ImageRefusee, lire_png

GRIS_ENTETE = 0.88
GRIS_TOTAL = 0.94

#: Colonnes du tableau client : (titre, largeur en points, aligné à droite).
COLONNES = (
    ("Poste", 52.0, False),
    ("Désignation", 214.0, False),
    ("Unité", 38.0, False),
    ("Quantité", 62.0, True),
    ("P.U. HT", 66.0, True),
    ("Total HT", 68.0, True),
)
#: Les trois colonnes réservées à l'entreprise, jamais imprimées par défaut.
COLONNES_INTERNES = (
    ("Déboursé", 60.0, True),
    ("Revient", 60.0, True),
    ("Marge", 56.0, True),
)

INTERLIGNE = 13.0
TAILLE_LIGNE = 8.5
TAILLE_CONDITIONS = 8.5

#: La boîte réservée au logo, en points. Le logo s'y inscrit en gardant ses
#: proportions : un logo horizontal touche les bords gauche et droit, un logo
#: carré touche le haut et le bas. Il ne DÉBORDE jamais — c'est la boîte, et
#: non l'image, qui décide de la place prise, et c'est ce qui garantit que
#: l'identité posée à côté reste lisible quelle que soit la forme du fichier.
LOGO_LARGEUR_MAXIMALE = 108.0
LOGO_HAUTEUR_MAXIMALE = 46.0
#: L'espace entre le logo et le texte d'identité.
LOGO_ECART = 14.0
#: Le nom sous lequel le logo est déclaré dans les ressources du document.
LOGO_NOM = "Im1"


def _boite_du_logo(largeur_px: int, hauteur_px: int) -> tuple[float, float]:
    """Les dimensions imprimées d'un logo, proportions gardées.

    On inscrit dans la boîte plutôt qu'on ne remplit : déformer le logo d'une
    entreprise pour qu'il tienne exactement dans un rectangle est la seule
    chose qu'un document commercial ne doit jamais faire.
    """
    if largeur_px <= 0 or hauteur_px <= 0:
        return (0.0, 0.0)
    facteur = min(
        LOGO_LARGEUR_MAXIMALE / largeur_px,
        LOGO_HAUTEUR_MAXIMALE / hauteur_px,
    )
    return (largeur_px * facteur, hauteur_px * facteur)


def _date_fr(valeur: date | datetime | str) -> str:
    """Une date au format belge. Une chaîne illisible est rendue telle quelle.

    Un instantané relu d'une base porte des chaînes ISO ; un devis qu'on vient
    d'émettre porte des objets. Les deux passent ici.
    """
    if isinstance(valeur, str):
        try:
            return date.fromisoformat(valeur[:10]).strftime("%d/%m/%Y")
        except ValueError:
            return valeur
    return valeur.strftime("%d/%m/%Y")


def _adresse(bloc: dict[str, Any], cle_rue: str) -> list[str]:
    lignes = []
    rue = (bloc.get(cle_rue) or "").strip()
    if rue:
        lignes.append(rue)
    ville = " ".join(
        part
        for part in ((bloc.get("postal_code") or "").strip(), (bloc.get("city") or "").strip())
        if part
    )
    if ville:
        lignes.append(ville)
    pays = (bloc.get("country_code") or "").strip()
    if pays:
        lignes.append(pays)
    return lignes


def composer_le_devis(
    *,
    numero: str,
    emis_le: datetime,
    valid_until: date,
    organisation: dict[str, Any],
    client: dict[str, Any],
    projet: dict[str, Any],
    document: dict[str, Any],
    terms: str | None,
    include_internal_costs: bool,
    logo: bytes | None = None,
) -> bytes:
    """Rend les octets du PDF. Deux appels identiques rendent les mêmes octets.

    `logo` porte les OCTETS du logo tel qu'il était à l'émission, jamais une
    clé de stockage : ce module ne lit aucun fichier et ne connaît aucun
    chemin. Un logo illisible ne fait pas échouer l'émission — le devis part
    sans lui, avec une identité qui reste complète.
    """
    image = _image_du_logo(logo)
    colonnes = list(COLONNES) + (list(COLONNES_INTERNES) if include_internal_costs else [])
    largeur_utile = moteur.A4[0] - 2 * moteur.MARGE
    total_colonnes = sum(largeur for _, largeur, _ in colonnes)
    echelle = largeur_utile / total_colonnes if total_colonnes > largeur_utile else 1.0
    colonnes = [(titre, largeur * echelle, droite) for titre, largeur, droite in colonnes]

    lignes = document.get("lines") or []
    totaux = document.get("totals") or {}
    devise = totaux.get("currency") or projet.get("currency") or "EUR"

    # Combien de lignes tiennent après l'en-tête complet, puis sur une page
    # suivante qui n'a que le cartouche.
    # 380 et non 330 : l'identité de l'émetteur porte maintenant son adresse et
    # ses coordonnées, soit quatre lignes de plus, et le bloc DESTINATAIRE
    # descend d'autant. Réserver trop peu ferait dessiner les dernières lignes
    # du tableau sous la marge — invisibles, sans que rien ne le signale.
    hauteur_premiere = moteur.A4[1] - 380.0 - 180.0
    hauteur_suivante = moteur.A4[1] - 110.0 - 180.0
    par_page_premiere = max(1, int(hauteur_premiere / INTERLIGNE))
    par_page_suivante = max(1, int(hauteur_suivante / INTERLIGNE))

    tranches: list[list[dict[str, Any]]] = []
    reste = list(lignes)
    tranches.append(reste[:par_page_premiere])
    reste = reste[par_page_premiere:]
    while reste:
        tranches.append(reste[:par_page_suivante])
        reste = reste[par_page_suivante:]

    pages: list[moteur.Page] = []
    for index, tranche in enumerate(tranches):
        page = moteur.Page()
        if index == 0:
            y = _entete_complet(
                page, numero, emis_le, valid_until, organisation, client, projet, image
            )
        else:
            y = _entete_court(page, numero, organisation)
        y = _tableau(page, y, colonnes, tranche, index == 0)
        pages.append(page)
        if index < len(tranches) - 1:
            continue

        y = _totaux(page, y, totaux, devise)
        # Les conditions passent à la page suivante plutôt que sous le pied.
        #
        # Le champ accepte 4 000 caractères, soit une quarantaine de lignes ;
        # les dessiner à la suite des totaux les faisait sortir de la page —
        # tracées à une ordonnée négative, donc invisibles. Un devis dont les
        # conditions disparaissent en silence est pire qu'un devis sans
        # conditions : l'entreprise croit les avoir envoyées.
        conditions = _lignes_de_conditions(terms)
        if y - _hauteur_du_pied(conditions) < moteur.MARGE:
            page = moteur.Page()
            y = _entete_court(page, numero, organisation)
            pages.append(page)
        _conditions_et_signatures(page, y, conditions, client, organisation)

    for numero_page, page in enumerate(pages, start=1):
        _pied(page, numero, numero_page, len(pages))

    return moteur.assembler(
        pages,
        titre=f"Devis {numero}",
        auteur=str(organisation.get("legal_name") or organisation.get("name") or "Metreo"),
        date_pdf=emis_le.strftime("D:%Y%m%d%H%M%S"),
        images=[image] if image is not None else [],
    )


def _image_du_logo(logo: bytes | None) -> moteur.ImagePdf | None:
    """Le logo décodé, ou `None` — jamais une exception.

    Un logo devenu illisible entre son dépôt et l'émission — fichier tronqué
    par une panne de volume, format qu'une version ultérieure refuserait — ne
    doit pas empêcher un devis de partir. L'entreprise garde son identité
    écrite ; elle perd une image. Refuser d'émettre pour cela transformerait un
    incident cosmétique en blocage commercial.
    """
    if not logo:
        return None
    try:
        decodee = lire_png(logo)
    except ImageRefusee:
        return None
    return moteur.ImagePdf(
        nom=LOGO_NOM,
        largeur=decodee.largeur,
        hauteur=decodee.hauteur,
        espace=decodee.espace,
        couleur=decodee.couleur,
        alpha=decodee.alpha,
    )


def lignes_d_adresse_emetteur(organisation: dict[str, Any]) -> list[str]:
    """L'adresse postale de l'émetteur, dans l'ordre où on l'écrit.

    Publique, et c'est le sujet : la page publique du devis affiche la même
    adresse que le PDF imprime. Deux constructions séparées divergeraient au
    premier champ ajouté, et le client lirait une adresse à l'écran et une
    autre sur le papier — pour le même devis.
    """
    postal = _adresse(organisation, "address")
    complement = (organisation.get("address_complement") or "").strip()
    if not postal:
        return [complement] if complement else []
    # Le complément suit la rue et précède la localité : c'est l'ordre postal.
    return [postal[0], *([complement] if complement else []), *postal[1:]]


def _lignes_d_identite(organisation: dict[str, Any]) -> list[str]:
    """L'émetteur sous le nom : raison sociale, numéro, adresse, contacts.

    Chaque ligne n'apparaît que si elle porte quelque chose. Un devis qui
    imprimerait « Téléphone : » suivi d'un blanc dit au client que l'entreprise
    n'a pas fini de se configurer, ce qui n'est pas ce qu'un devis doit dire.

    Tout est lu dans l'INSTANTANÉ, avec `.get` : un devis émis avant que ces
    champs n'existent n'en porte aucun, et se réimprime sans eux plutôt que de
    lever une erreur au téléchargement.
    """
    lignes: list[str] = []
    if organisation.get("legal_name"):
        lignes.append(str(organisation["legal_name"]))
    if organisation.get("company_number"):
        lignes.append(f"N° d'entreprise : {organisation['company_number']}")
    lignes.extend(lignes_d_adresse_emetteur(organisation))
    contacts = " — ".join(
        part
        for part in (
            (organisation.get("phone") or "").strip(),
            (organisation.get("email") or "").strip(),
        )
        if part
    )
    if contacts:
        lignes.append(contacts)
    if organisation.get("website"):
        lignes.append(str(organisation["website"]))
    return lignes


def _entete_complet(
    page: moteur.Page,
    numero: str,
    emis_le: datetime,
    valid_until: date,
    organisation: dict[str, Any],
    client: dict[str, Any],
    projet: dict[str, Any],
    image: moteur.ImagePdf | None = None,
) -> float:
    gauche = moteur.MARGE
    droite = moteur.A4[0] - moteur.MARGE
    haut = moteur.A4[1] - moteur.MARGE
    #: L'identité de l'entreprise partage sa bande avec le cartouche du devis,
    #: posé à `droite - 210`. Elle ne dispose donc pas de la pleine largeur —
    #: et une raison sociale d'intercommunale la dépasse largement.
    #: Le logo, quand il existe, lui en prend encore.
    largeur_logo, hauteur_logo = (
        _boite_du_logo(image.largeur, image.hauteur) if image is not None else (0.0, 0.0)
    )
    decalage = (largeur_logo + LOGO_ECART) if largeur_logo else 0.0
    identite_x = gauche + decalage
    largeur_identite = droite - 210 - identite_x - 12
    #: Les blocs DESTINATAIRE et CHANTIER, eux, passent SOUS le cartouche.
    largeur_pleine = droite - gauche

    if image is not None and largeur_logo:
        # Le coin SUPÉRIEUR du logo s'aligne sur celui du texte : un PDF place
        # une image par son coin inférieur, d'où la soustraction.
        page.image(LOGO_NOM, gauche, haut - hauteur_logo, largeur_logo, hauteur_logo)

    y = page.paragraphe(
        identite_x,
        haut - 14,
        str(organisation.get("name") or ""),
        largeur=largeur_identite,
        police=moteur.HELVETICA_GRAS,
        # Un logo prend de la largeur : le nom passe à 13 points pour continuer
        # à tenir en une ou deux lignes plutôt que quatre.
        taille=13 if largeur_logo else 16,
        interligne=15 if largeur_logo else 18,
    )
    y -= 10
    for ligne in _lignes_d_identite(organisation):
        y = page.paragraphe(identite_x, y, ligne, largeur=largeur_identite, interligne=11)
    # Le bloc suivant ne doit pas remonter au-dessus du logo, même quand
    # l'identité est plus courte que lui.
    y = min(y, haut - hauteur_logo)

    haut_cartouche = moteur.A4[1] - moteur.MARGE - 6
    page.texte(droite - 210, haut_cartouche - 8, "DEVIS", police=moteur.HELVETICA_GRAS, taille=20)
    page.texte(
        droite - 210, haut_cartouche - 26, f"N° {numero}", police=moteur.HELVETICA_GRAS, taille=11
    )
    page.texte(droite - 210, haut_cartouche - 40, f"Émis le {_date_fr(emis_le)}", taille=9)
    page.texte(
        droite - 210, haut_cartouche - 52, f"Valable jusqu'au {_date_fr(valid_until)}", taille=9
    )

    y = min(y, haut_cartouche - 70) - 14
    page.texte(gauche, y, "DESTINATAIRE", police=moteur.HELVETICA_GRAS, taille=8)
    y -= 13
    y = page.paragraphe(
        gauche,
        y,
        str(client.get("name") or ""),
        largeur=largeur_pleine,
        police=moteur.HELVETICA_GRAS,
        taille=11,
        interligne=13,
    )
    if client.get("company_number"):
        y = page.paragraphe(
            gauche,
            y,
            f"N° d'entreprise : {client['company_number']}",
            largeur=largeur_pleine,
            interligne=11,
        )
    if client.get("contact_name"):
        y = page.paragraphe(
            gauche,
            y,
            f"À l'attention de {client['contact_name']}",
            largeur=largeur_pleine,
            interligne=11,
        )
    for ligne in _adresse(client, "billing_address"):
        y = page.paragraphe(gauche, y, ligne, largeur=largeur_pleine, interligne=11)
    contacts = " — ".join(
        part
        for part in ((client.get("email") or "").strip(), (client.get("phone") or "").strip())
        if part
    )
    if contacts:
        y = page.paragraphe(gauche, y, contacts, largeur=largeur_pleine, interligne=11)

    y -= 8
    page.texte(gauche, y, "CHANTIER", police=moteur.HELVETICA_GRAS, taille=8)
    y -= 13
    y = page.paragraphe(
        gauche,
        y,
        f"{projet.get('reference', '')} — {projet.get('name', '')}",
        largeur=largeur_pleine,
        police=moteur.HELVETICA_GRAS,
        taille=10,
        interligne=12,
    )
    for ligne in _adresse(projet, "address"):
        y = page.paragraphe(gauche, y, ligne, largeur=largeur_pleine, interligne=11)
    if projet.get("client_reference"):
        y = page.paragraphe(
            gauche,
            y,
            f"Référence client : {projet['client_reference']}",
            largeur=largeur_pleine,
            interligne=11,
        )
    y = page.paragraphe(
        gauche,
        y,
        f"Étude : {projet.get('estimate_name', '')} — version {projet.get('version_number', '')}",
        largeur=largeur_pleine,
        interligne=11,
    )
    return y - 7


def _entete_court(page: moteur.Page, numero: str, organisation: dict[str, Any]) -> float:
    y = moteur.A4[1] - moteur.MARGE
    #: Le nom partage sa ligne avec « Devis N° … », posé à droite : on tronque
    #: ici plutôt que de replier, car la ligne doit rester unique.
    page.texte(
        moteur.MARGE,
        y - 10,
        moteur.tronquer(
            str(organisation.get("name") or ""), moteur.A4[0] - 2 * moteur.MARGE - 140, 11
        ),
        police=moteur.HELVETICA_GRAS,
        taille=11,
    )
    page.texte_a_droite(
        moteur.A4[0] - moteur.MARGE, y - 10, f"Devis {numero}", police=moteur.COURIER, taille=10
    )
    page.ligne(moteur.MARGE, y - 18, moteur.A4[0] - moteur.MARGE, y - 18)
    return y - 40


def _tableau(
    page: moteur.Page,
    y: float,
    colonnes: list[tuple[str, float, bool]],
    lignes: list[dict[str, Any]],
    premiere: bool,
) -> float:
    gauche = moteur.MARGE
    page.pave(gauche, y - 4, sum(largeur for _, largeur, _ in colonnes), 15, GRIS_ENTETE)
    x = gauche
    for titre, largeur, a_droite in colonnes:
        if a_droite:
            page.texte_a_droite(x + largeur - 4, y, titre, police=moteur.HELVETICA_GRAS, taille=8)
        else:
            page.texte(x + 3, y, titre, police=moteur.HELVETICA_GRAS, taille=8)
        x += largeur
    y -= 16
    del premiere

    for ligne in lignes:
        x = gauche
        section = ligne.get("kind") == "section"
        police = moteur.HELVETICA_GRAS if section else moteur.HELVETICA
        for cle, (_, largeur, a_droite) in zip(
            (
                "position",
                "designation",
                "unit",
                "quantity",
                "unit_price_ht",
                "selling_price_ht",
                "direct_cost",
                "cost_price",
                "margin_amount",
            ),
            colonnes,
            strict=False,
        ):
            valeur = str(ligne.get(cle, "") or "")
            if section and cle not in ("position", "designation"):
                valeur = ""
            if a_droite:
                page.texte_a_droite(x + largeur - 4, y, valeur, taille=TAILLE_LIGNE)
            else:
                page.texte(
                    x + 3,
                    y,
                    moteur.tronquer(valeur, largeur - 6, TAILLE_LIGNE),
                    police=police,
                    taille=TAILLE_LIGNE,
                )
            x += largeur
        if not ligne.get("included_in_total", True):
            page.texte(gauche + 3, y - 7, "non compté au total", taille=6.5)
            y -= 6
        y -= INTERLIGNE
    page.ligne(gauche, y + 8, gauche + sum(largeur for _, largeur, _ in colonnes), y + 8)
    return y


def _totaux(page: moteur.Page, y: float, totaux: dict[str, Any], devise: str) -> float:
    droite = moteur.A4[0] - moteur.MARGE
    gauche_bloc = droite - 240
    y -= 10

    def ligne_total(libelle: str, montant: str, *, gras: bool = False) -> None:
        nonlocal y
        page.texte(
            gauche_bloc,
            y,
            libelle,
            police=moteur.HELVETICA_GRAS if gras else moteur.HELVETICA,
            taille=9.5 if gras else 9,
        )
        page.texte_a_droite(
            droite,
            y,
            f"{montant} {devise}",
            police=moteur.COURIER_GRAS if gras else moteur.COURIER,
            taille=9.5 if gras else 9,
        )
        y -= 14

    ligne_total("Total HT", str(totaux.get("total_ht", "") or "0"))
    for taxe in totaux.get("taxes", []) or []:
        ligne_total(str(taxe.get("label", "Taxe")), str(taxe.get("amount", "") or "0"))
    page.pave(gauche_bloc - 6, y - 4, droite - gauche_bloc + 6, 17, GRIS_TOTAL)
    ligne_total("TOTAL À PAYER TTC", str(totaux.get("total_ttc", "") or "0"), gras=True)
    return y


#: Ce qu'occupent les deux cadres de signature, sous les conditions.
HAUTEUR_SIGNATURES = 90.0


def _lignes_de_conditions(terms: str | None) -> list[str]:
    """Les conditions, déjà repliées : on doit les MESURER avant de les poser."""
    if not terms:
        return []
    largeur = moteur.A4[0] - 2 * moteur.MARGE
    lignes: list[str] = []
    for paragraphe in terms.splitlines():
        lignes += moteur.replier(paragraphe, largeur, TAILLE_CONDITIONS) or [""]
    return lignes


def _hauteur_du_pied(conditions: list[str]) -> float:
    """Ce que réclament les conditions et les signatures, ensemble."""
    hauteur = 14.0 + HAUTEUR_SIGNATURES
    if conditions:
        hauteur += 12.0 + 10.0 * len(conditions)
    return hauteur


def _conditions_et_signatures(
    page: moteur.Page,
    y: float,
    conditions: list[str],
    client: dict[str, Any],
    organisation: dict[str, Any],
) -> None:
    gauche = moteur.MARGE
    droite = moteur.A4[0] - moteur.MARGE
    y -= 14
    if conditions:
        page.texte(gauche, y, "CONDITIONS", police=moteur.HELVETICA_GRAS, taille=8)
        y -= 12
        for morceau in conditions:
            page.texte(gauche, y, morceau, taille=TAILLE_CONDITIONS)
            y -= 10

    bas = max(y - 24, moteur.MARGE + 66)
    milieu = (gauche + droite) / 2
    #: Deux cadres côte à côte : chaque nom tient dans SA moitié. Posé
    #: entier, un nom long recouvrait le cadre voisin — les deux signatures
    #: se chevauchaient sur le papier.
    colonne = milieu - 24 - gauche
    page.texte(gauche, bas, "Bon pour accord — le client", police=moteur.HELVETICA_GRAS, taille=8)
    page.texte(
        gauche, bas - 11, moteur.tronquer(str(client.get("name") or ""), colonne, 8), taille=8
    )
    page.texte(gauche, bas - 22, "Date et signature :", taille=8)
    page.ligne(gauche, bas - 44, milieu - 24, bas - 44)

    page.texte(milieu + 12, bas, "Pour l'entreprise", police=moteur.HELVETICA_GRAS, taille=8)
    page.texte(
        milieu + 12,
        bas - 11,
        moteur.tronquer(
            str(organisation.get("legal_name") or organisation.get("name") or ""),
            droite - milieu - 12,
            8,
        ),
        taille=8,
    )
    page.texte(milieu + 12, bas - 22, "Date et signature :", taille=8)
    page.ligne(milieu + 12, bas - 44, droite, bas - 44)


def _pied(page: moteur.Page, numero: str, page_courante: int, total: int) -> None:
    y = moteur.MARGE - 18
    page.ligne(moteur.MARGE, y + 12, moteur.A4[0] - moteur.MARGE, y + 12)
    page.texte(moteur.MARGE, y, f"Devis {numero}", taille=7.5)
    page.texte_a_droite(
        moteur.A4[0] - moteur.MARGE,
        y,
        f"Page {page_courante} / {total}",
        police=moteur.COURIER,
        taille=7.5,
    )
