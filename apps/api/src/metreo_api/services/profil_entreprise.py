"""Le profil de l'entreprise émettrice : ce qu'il faut, et ce qu'on en fait.

Deux questions vivent ici, et une seule réponse à chacune.

**Que faut-il pour émettre ?** Le nom, l'adresse, le code postal, la ville et
le pays. C'est le minimum d'un courrier : de quoi identifier l'émetteur et lui
répondre. Le numéro d'entreprise, l'e-mail et le téléphone restent facultatifs
— tant qu'aucune règle juridique **sourcée** ne les impose, les exiger serait
une opinion déguisée en contrainte, et elle bloquerait une entreprise
étrangère ou une personne physique. La même sévérité que pour la fiche client,
et pour la même raison : voir `issuance.client_suffisant`.

**Où vit le logo ?** Sur le volume, jamais dans l'arbre Git ni dans la base.
La base n'en garde que ce que le serveur a MESURÉ sur les octets : empreinte,
taille, type réel, dimensions. Le remplacement écrit un fichier neuf sous un
identifiant neuf, puis retire l'ancien seulement une fois la base validée —
l'ordre est ce qui empêche qu'un échec laisse une ligne pointant vers un
fichier disparu.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import Organization, new_id, utcnow
from ..transactions import achever, compenser
from .document_storage import StockageLocal
from .images import ImageDecodee, verifier_un_logo

#: Le dossier du volume où vivent les logos, à côté de `documents` et `devis`.
DOSSIER = "logos"

#: Ce qu'il faut pour qu'un devis puisse partir. Ordre d'affichage à l'écran.
CHAMPS_REQUIS: tuple[str, ...] = ("name", "address", "postal_code", "city", "country_code")

#: Ce que l'écran propose de renseigner, au-delà du minimum.
CHAMPS_MODIFIABLES: tuple[str, ...] = (
    "name",
    "legal_name",
    "company_number",
    "address",
    "address_complement",
    "postal_code",
    "city",
    "country_code",
    "email",
    "phone",
    "website",
)


def emetteur_suffisant(organization: Organization) -> list[str]:
    """Ce qui manque à l'entreprise pour qu'un devis puisse porter son nom.

    Rend la liste des champs, dans l'ordre où l'écran les présente. Une liste
    vide vaut « on peut émettre ». On rend des NOMS DE CHAMP plutôt qu'une
    phrase : l'écran sait alors quel encadré surligner, ce qu'une phrase ne
    permet pas.
    """
    return [
        champ for champ in CHAMPS_REQUIS if not (getattr(organization, champ, None) or "").strip()
    ]


def verrouiller_l_organisation(session: Session, organization: Organization) -> None:
    """Sérialise les écritures qui touchent le logo d'une organisation.

    PostgreSQL pose un `SELECT ... FOR UPDATE` : la seconde requête attend la
    validation de la première, et relit donc la clé que celle-ci a écrite.
    SQLite ne connaît pas le verrou de ligne et sérialise déjà les écritures au
    niveau de la base ; l'appel y est sans effet, ce qui est correct.
    """
    session.refresh(organization, with_for_update=True)


def logo_present(organization: Organization) -> bool:
    """Un logo est là quand ses métadonnées sont là — la base l'impose toutes ou aucune."""
    return bool(organization.logo_storage_key)


def instantane_logo(organization: Organization) -> dict[str, Any] | None:
    """Ce qu'un devis émis retiendra du logo. `None` s'il n'y en a pas."""
    if not logo_present(organization):
        return None
    return {
        "sha256": organization.logo_sha256,
        "byte_size": organization.logo_byte_size,
        "media_type": organization.logo_media_type,
        "width": organization.logo_width,
        "height": organization.logo_height,
    }


def poser_le_logo(
    session: Session,
    *,
    organization: Organization,
    contenu: bytes,
    stockage: StockageLocal,
) -> ImageDecodee:
    """Valide les octets, les pose, puis décrit le logo dans la base.

    L'ordre des gestes est le sujet de cette fonction :

    1. **valider d'abord** — un fichier refusé n'atteint jamais le volume, donc
       aucun résidu à nettoyer ; c'est plus sûr qu'écrire puis effacer ;
    2. **écrire ensuite**, sous un identifiant NEUF : l'ancien fichier reste
       intact tant que la transaction n'est pas validée, et un lecteur
       concurrent continue de le servir ;
    3. **compenser** l'écriture — si la transaction échoue, les octets neufs
       repartent, et la ligne d'avant n'a jamais bougé ;
    4. **enregistrer** les métadonnées mesurées ;
    5. **planifier le retrait de l'ancien** pour APRÈS la validation. Le
       retirer maintenant détruirait le logo courant si la transaction
       échouait ensuite.
    """
    image = verifier_un_logo(contenu)
    # Verrouiller la ligne AVANT de lire l'ancienne clé.
    #
    # Sans cela, deux poses simultanées lisent la même ancienne clé O, écrivent
    # chacune leur fichier A et B, et retirent toutes deux O. La base ne retient
    # qu'une des deux clés ; l'autre fichier n'est plus désigné par personne, et
    # aucun remplacement futur ne le retirera jamais. Mesuré : un double-clic
    # sur « Enregistrer » suffit à laisser un orphelin définitif.
    verrouiller_l_organisation(session, organization)
    ancienne_cle = organization.logo_storage_key

    stocke = stockage.ecrire_octets(
        organization_id=organization.id,
        dossier=DOSSIER,
        identifiant=new_id(),
        extension=".png",
        contenu=contenu,
        media_type=image_media_type(image),
    )
    compenser(
        session,
        lambda: stockage.supprimer(stocke.storage_key),
        f"retirer le logo neuf de l'organisation {organization.id}",
    )

    organization.logo_storage_key = stocke.storage_key
    organization.logo_sha256 = stocke.sha256
    organization.logo_byte_size = stocke.byte_size
    organization.logo_media_type = stocke.media_type
    organization.logo_width = image.largeur
    organization.logo_height = image.hauteur
    organization.logo_updated_at = utcnow()
    session.flush()

    if ancienne_cle and ancienne_cle != stocke.storage_key:
        achever(
            session,
            lambda: stockage.supprimer(ancienne_cle),
            f"retirer l'ancien logo de l'organisation {organization.id}",
        )
    return image


def retirer_le_logo(
    session: Session, *, organization: Organization, stockage: StockageLocal
) -> bool:
    """Efface le logo. Rend False s'il n'y en avait pas — ce n'est pas une erreur.

    Les octets ne partent qu'APRÈS la validation, pour la même raison que
    ci-dessus : une transaction qui échouerait après coup laisserait une ligne
    décrivant un fichier qui n'existe plus.
    """
    verrouiller_l_organisation(session, organization)
    cle = organization.logo_storage_key
    if not cle:
        return False
    organization.logo_storage_key = None
    organization.logo_sha256 = None
    organization.logo_byte_size = None
    organization.logo_media_type = None
    organization.logo_width = None
    organization.logo_height = None
    organization.logo_updated_at = utcnow()
    session.flush()
    achever(
        session,
        lambda: stockage.supprimer(cle),
        f"retirer le logo effacé de l'organisation {organization.id}",
    )
    return True


def image_media_type(image: ImageDecodee) -> str:
    """Le type ÉTABLI sur les octets. Un seul format est accepté aujourd'hui."""
    del image  # la validation n'accepte que du PNG ; le paramètre documente l'intention
    return "image/png"
