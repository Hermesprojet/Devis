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
) -> bytes:
    """Rend les octets du PDF. Deux appels identiques rendent les mêmes octets."""
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
    hauteur_premiere = moteur.A4[1] - 330.0 - 180.0
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
            y = _entete_complet(page, numero, emis_le, valid_until, organisation, client, projet)
        else:
            y = _entete_court(page, numero, organisation)
        y = _tableau(page, y, colonnes, tranche, index == 0)
        if index == len(tranches) - 1:
            y = _totaux(page, y, totaux, devise)
            _conditions_et_signatures(page, y, terms, client, organisation)
        pages.append(page)

    for numero_page, page in enumerate(pages, start=1):
        _pied(page, numero, numero_page, len(pages))

    return moteur.assembler(
        pages,
        titre=f"Devis {numero}",
        auteur=str(organisation.get("legal_name") or organisation.get("name") or "Metreo"),
        date_pdf=emis_le.strftime("D:%Y%m%d%H%M%S"),
    )


def _entete_complet(
    page: moteur.Page,
    numero: str,
    emis_le: datetime,
    valid_until: date,
    organisation: dict[str, Any],
    client: dict[str, Any],
    projet: dict[str, Any],
) -> float:
    gauche = moteur.MARGE
    droite = moteur.A4[0] - moteur.MARGE
    y = moteur.A4[1] - moteur.MARGE

    page.texte(
        gauche, y - 14, str(organisation.get("name") or ""), police=moteur.HELVETICA_GRAS, taille=16
    )
    y -= 30
    for ligne in (
        organisation.get("legal_name") or "",
        (
            f"N° d'entreprise : {organisation['company_number']}"
            if organisation.get("company_number")
            else ""
        ),
    ):
        if ligne:
            page.texte(gauche, y, str(ligne), taille=9)
            y -= 11

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
    page.texte(gauche, y, str(client.get("name") or ""), police=moteur.HELVETICA_GRAS, taille=11)
    y -= 13
    if client.get("company_number"):
        page.texte(gauche, y, f"N° d'entreprise : {client['company_number']}", taille=9)
        y -= 11
    if client.get("contact_name"):
        page.texte(gauche, y, f"À l'attention de {client['contact_name']}", taille=9)
        y -= 11
    for ligne in _adresse(client, "billing_address"):
        page.texte(gauche, y, ligne, taille=9)
        y -= 11
    contacts = " — ".join(
        part
        for part in ((client.get("email") or "").strip(), (client.get("phone") or "").strip())
        if part
    )
    if contacts:
        page.texte(gauche, y, contacts, taille=9)
        y -= 11

    y -= 8
    page.texte(gauche, y, "CHANTIER", police=moteur.HELVETICA_GRAS, taille=8)
    y -= 13
    page.texte(
        gauche,
        y,
        f"{projet.get('reference', '')} — {projet.get('name', '')}",
        police=moteur.HELVETICA_GRAS,
        taille=10,
    )
    y -= 12
    for ligne in _adresse(projet, "address"):
        page.texte(gauche, y, ligne, taille=9)
        y -= 11
    if projet.get("client_reference"):
        page.texte(gauche, y, f"Référence client : {projet['client_reference']}", taille=9)
        y -= 11
    page.texte(
        gauche,
        y,
        f"Étude : {projet.get('estimate_name', '')} — version {projet.get('version_number', '')}",
        taille=9,
    )
    return y - 18


def _entete_court(page: moteur.Page, numero: str, organisation: dict[str, Any]) -> float:
    y = moteur.A4[1] - moteur.MARGE
    page.texte(
        moteur.MARGE,
        y - 10,
        str(organisation.get("name") or ""),
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


def _conditions_et_signatures(
    page: moteur.Page,
    y: float,
    terms: str | None,
    client: dict[str, Any],
    organisation: dict[str, Any],
) -> None:
    gauche = moteur.MARGE
    droite = moteur.A4[0] - moteur.MARGE
    y -= 14
    if terms:
        page.texte(gauche, y, "CONDITIONS", police=moteur.HELVETICA_GRAS, taille=8)
        y -= 12
        for paragraphe in terms.splitlines():
            for morceau in _replier(paragraphe, 105):
                page.texte(gauche, y, morceau, taille=8.5)
                y -= 10

    bas = max(y - 24, moteur.MARGE + 66)
    milieu = (gauche + droite) / 2
    page.texte(gauche, bas, "Bon pour accord — le client", police=moteur.HELVETICA_GRAS, taille=8)
    page.texte(gauche, bas - 11, f"{client.get('name', '')}", taille=8)
    page.texte(gauche, bas - 22, "Date et signature :", taille=8)
    page.ligne(gauche, bas - 44, milieu - 24, bas - 44)

    page.texte(milieu + 12, bas, "Pour l'entreprise", police=moteur.HELVETICA_GRAS, taille=8)
    page.texte(
        milieu + 12,
        bas - 11,
        str(organisation.get("legal_name") or organisation.get("name") or ""),
        taille=8,
    )
    page.texte(milieu + 12, bas - 22, "Date et signature :", taille=8)
    page.ligne(milieu + 12, bas - 44, droite, bas - 44)


def _replier(texte: str, largeur: int) -> list[str]:
    """Replie un paragraphe sur plusieurs lignes, sans couper les mots."""
    if not texte.strip():
        return [""]
    lignes: list[str] = []
    courante = ""
    for mot in texte.split():
        if courante and len(courante) + 1 + len(mot) > largeur:
            lignes.append(courante)
            courante = mot
        else:
            courante = f"{courante} {mot}".strip()
    if courante:
        lignes.append(courante)
    return lignes


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
