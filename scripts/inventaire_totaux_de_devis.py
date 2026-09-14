"""Inventaire, EN LECTURE SEULE, des devis émis dont l'instantané n'a pas de totaux.

    python scripts/inventaire_totaux_de_devis.py [--json]

Pourquoi cet outil existe
-------------------------

`totaux_du_document` lisait les totaux sous une clé ``"totals"`` que
``EstimateResult.to_dict()`` ne produit pas. Tout devis émis avant la
correction porte donc, dans son ``document_snapshot``, des totaux vides — et
les trois surfaces qui les relisent (le PDF figé, le tableau des devis, la
page publique du client) affichent ``0 EUR``.

Ce script DIT lesquels. Il ne répare rien, n'écrit rien, ne supprime rien, et
c'est délibéré :

* un devis émis est **immuable**. Son numéro a été communiqué, son PDF a une
  empreinte, un client l'a peut-être déjà accepté. Réécrire son instantané
  ferait mentir l'empreinte et effacerait la trace de ce qui a été transmis ;
* corriger en base produirait un document qui ne correspond plus à celui que
  le client détient, sans que personne ne l'apprenne.

La seule sortie honnête est donc une **réémission**, décrite en fin de
rapport, qui laisse l'original en place.

Ce que « démonstration » veut dire ici
---------------------------------------

Aucune colonne ne marque un devis comme fictif. Le jeu de démonstration, lui,
pose ``is_demo_data=True`` sur les PRIX qu'il crée. Une organisation est donc
*présumée* de démonstration quand elle possède au moins un prix ainsi marqué
et aucun prix qui ne le soit pas. C'est une présomption, elle est affichée
comme telle, et un doute est signalé plutôt que tranché.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api", "src"))

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from metreo_api.db import get_engine
from metreo_api.models import IssuedQuote, Organization, PriceItem


def _totaux_absents(instantane: dict[str, Any] | None) -> str | None:
    """Rend le motif si les totaux sont inexploitables, sinon ``None``."""
    if not instantane:
        return "instantané de document absent"
    totaux = instantane.get("totals")
    if totaux is None:
        return "aucune clé « totals »"
    if not isinstance(totaux, dict):
        return f"« totals » n'est pas un objet ({type(totaux).__name__})"
    manquants = [
        champ for champ in ("total_ht", "total_ttc") if not str(totaux.get(champ) or "").strip()
    ]
    if manquants:
        return "champs vides : " + ", ".join(manquants)
    return None


def _presomption(session: Session, organization_id: str) -> str:
    """« démonstration », « réel » ou « indéterminé » — jamais deviné en silence."""
    marques = session.execute(
        select(
            func.count(PriceItem.id).filter(PriceItem.is_demo_data.is_(True)),
            func.count(PriceItem.id).filter(PriceItem.is_demo_data.is_(False)),
        ).where(PriceItem.organization_id == organization_id)
    ).one()
    demo, reels = int(marques[0] or 0), int(marques[1] or 0)
    if demo and not reels:
        return "démonstration"
    if reels and not demo:
        return "réel"
    if not demo and not reels:
        return "indéterminé (aucun prix)"
    return f"indéterminé ({demo} prix de démonstration, {reels} non marqués)"


def inventorier(session: Session) -> list[dict[str, Any]]:
    lignes: list[dict[str, Any]] = []
    organisations = {o.id: o.name for o in session.execute(select(Organization)).scalars().all()}
    presomptions = {oid: _presomption(session, oid) for oid in organisations}

    for devis in session.execute(select(IssuedQuote).order_by(IssuedQuote.issued_at)).scalars():
        motif = _totaux_absents(devis.document_snapshot)
        if motif is None:
            continue
        lignes.append(
            {
                "organisation": organisations.get(devis.organization_id, "(inconnue)"),
                "organization_id": devis.organization_id,
                "nature": presomptions.get(devis.organization_id, "indéterminé"),
                "numero": devis.number,
                "issued_quote_id": devis.id,
                "emis_le": devis.issued_at.isoformat() if devis.issued_at else None,
                "estimate_id": devis.estimate_id,
                "estimate_version_id": devis.estimate_version_id,
                "pdf_sha256": devis.pdf_sha256,
                "motif": motif,
            }
        )
    return lignes


REMEDE = """
Comment corriger un document déjà remis, sans rien effacer
-----------------------------------------------------------

Le mécanisme existe déjà, il n'y a rien à inventer :

  1. Ouvrir l'estimation concernée et créer une NOUVELLE VERSION
     (« Créer une nouvelle version » sur l'écran de chiffrage). La version
     gelée d'origine n'est pas touchée.
  2. Geler cette nouvelle version.
  3. L'émettre. Elle reçoit son PROPRE numéro, sa propre date et sa propre
     empreinte, et porte cette fois les montants justes.
  4. Transmettre le nouveau document au client, en lui disant lequel il
     remplace. Si un lien de partage pointait l'ancien, le révoquer.

Ce que cela préserve, et pourquoi c'est le point :

  * l'ancien devis reste en base, avec son numéro, son PDF et son empreinte ;
  * sa chronologie commerciale — transmis, consulté, accepté, refusé — reste
    lisible ;
  * le journal d'audit garde la trace des deux émissions.

Ce qu'il ne faut PAS faire : modifier `document_snapshot` en base. Le PDF
figé, lui, ne changerait pas ; l'empreinte cesserait de correspondre à ce
qu'elle scelle, et le client détiendrait un document que le système ne
reconnaîtrait plus.
"""


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parseur.add_argument("--json", action="store_true", help="Sortie JSON, pour un traitement")
    arguments = parseur.parse_args(argv)

    with Session(get_engine()) as session:
        lignes = inventorier(session)

    if arguments.json:
        print(json.dumps(lignes, indent=2, ensure_ascii=False))
        return 0

    if not lignes:
        print("Aucun devis émis ne porte de totaux absents ou vides.")
        print("Rien à réémettre.")
        return 0

    par_nature: dict[str, int] = {}
    print(f"{len(lignes)} devis émis portent des totaux inexploitables.\n")
    largeur = max(len(str(ligne["numero"])) for ligne in lignes)
    for ligne in lignes:
        par_nature[ligne["nature"]] = par_nature.get(ligne["nature"], 0) + 1
        print(
            f"  {ligne['numero']!s:<{largeur}}  {ligne['nature']:<28}"
            f"  {ligne['organisation']}\n"
            f"  {'':<{largeur}}  {ligne['motif']}"
        )
    print("\nRépartition :")
    for nature, compte in sorted(par_nature.items()):
        print(f"  {nature:<40} {compte}")
    print(REMEDE)
    # Sortie 0 : c'est un inventaire, pas une porte. Un code d'échec ferait de
    # ce constat un blocage de chaîne, alors qu'il appelle une décision humaine.
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
