"""Écrire, relire et nommer les originaux, sur disque, sans jamais croire le client.

Ce module est la seule frontière entre un flux d'octets reçu et un fichier posé
sur le volume. Tout ce qui vient de l'appelant — nom, extension, type annoncé,
longueur déclarée — est traité comme une allégation. Ce qui fait foi est ce qui
a été réellement écrit : les octets, leur nombre, leur empreinte et leur
signature.

Le chemin physique n'emprunte RIEN à l'utilisateur : il se déduit de trois
identifiants engendrés par le serveur. C'est ce qui rend inutile toute défense
contre « ../ », un chemin absolu ou un lien symbolique — il n'existe aucun
endroit où l'un d'eux pourrait entrer. La vérification de confinement en fin de
course n'est pas là par méfiance envers l'appelant, mais contre une erreur de
refactoring à venir.

L'ADR 0003 impose l'original intact : rien n'est converti, recompressé ni
réécrit. Le `ObjectStore` du paquet `contracts` décrit l'interface asynchrone
d'un stockage objet ; l'implémentation locale ci-dessous en est la variante
disque, la seule que ce déploiement utilise.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from metreo_domain.errors import DomainError

#: Lu par morceaux : un fichier de 25 Mio ne doit jamais tenir en mémoire.
TAILLE_MORCEAU = 256 * 1024

#: De quoi reconnaître une signature. Les conteneurs ZIP en demandent plus.
OCTETS_DE_TETE = 8

#: Ce que l'application accepte de recevoir, et sous quel nom elle le range.
#:
#: Volontairement court. Chaque type ouvert ici est un type que le produit
#: devra savoir relire, sauvegarder et restaurer pendant des années ; en ouvrir
#: un « au cas où » est une dette, pas un service.
EXTENSIONS: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "text/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}

TYPES_ACCEPTES = frozenset(EXTENSIONS)

#: Les signatures que l'on sait lire, dans l'ordre où elles se distinguent.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)

#: Les signatures que l'on refuse en les NOMMANT, plutôt que de rendre
#: « type inconnu » : un exécutable renommé en PDF est une tentative, pas une
#: maladresse, et le refus doit le dire.
_SIGNATURES_REFUSEES: tuple[tuple[bytes, str], ...] = (
    (b"MZ", "un exécutable Windows"),
    (b"\x7fELF", "un exécutable Linux"),
    (b"\xca\xfe\xba\xbe", "un exécutable macOS"),
    (b"\xcf\xfa\xed\xfe", "un exécutable macOS"),
    (b"#!", "un script"),
    (b"\x1f\x8b", "une archive gzip"),
    (b"BZh", "une archive bzip2"),
    (b"\xfd7zXZ", "une archive xz"),
    (b"7z\xbc\xaf\x27\x1c", "une archive 7-Zip"),
    (b"Rar!", "une archive RAR"),
)

_ZIP = b"PK\x03\x04"

#: Reconnaître un OOXML sans le décompresser : on ne lit que le SOMMAIRE de
#: l'archive (le central directory), jamais son contenu. Une bombe de
#: décompression n'a donc rien à gonfler.
_MARQUEURS_OOXML: tuple[tuple[str, str], ...] = (
    (
        "word/document.xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ("xl/workbook.xml", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
)

#: Un début de fichier qui ressemble à du HTML est refusé même s'il est
#: parfaitement valide en CSV : servi un jour par erreur dans l'origine de
#: l'application, il y exécuterait ses propres scripts.
_DEBUTS_HTML = (b"<!doctype", b"<html", b"<head", b"<script", b"<svg", b"<?xml")


class ContenuRefuse(DomainError):
    """Le contenu reçu n'est pas ce qu'il prétend être, ou n'est pas accepté.

    Le `code` est porté par l'INSTANCE et non par la classe : un seul refus
    couvre plusieurs motifs, et chacun doit rester distinguable par l'appelant
    sans qu'il ait à lire le message destiné à l'utilisateur.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TropVolumineux(DomainError):
    code = "file_too_large"

    def __init__(self, plafond: int) -> None:
        super().__init__(f"Fichier trop volumineux (maximum {plafond} octets).", limit=plafond)


@dataclass(frozen=True, slots=True)
class OriginalStocke:
    """Ce que le serveur CONSTATE, une fois le fichier écrit."""

    storage_key: str
    sha256: str
    byte_size: int
    #: Le type déduit des octets. C'est lui qui est conservé et re-servi.
    media_type: str
    #: Le type qu'annonçait le client. Conservé à part, jamais utilisé pour
    #: décider : il ne sert qu'à expliquer un refus et à nourrir l'audit.
    declared_media_type: str | None


def nom_original_sur(nom: str | None) -> str:
    """Un nom de fichier réduit à un nom de fichier.

    `../../secret.pdf`, `C:\\dossier\\x.pdf` et `/etc/passwd` désignent tous
    ici le seul segment final, débarrassé de ses séparateurs. Le résultat n'est
    JAMAIS utilisé pour construire un chemin — il est purement descriptif — mais
    une donnée qui ressemble à un chemin finit toujours par être employée comme
    tel par quelqu'un.
    """
    brut = (nom or "").strip()
    for separateur in ("\\", "/"):
        brut = brut.rsplit(separateur, 1)[-1]
    brut = brut.replace("\x00", "").strip().strip(".")
    # Les caractères de contrôle sortiraient d'un en-tête ou d'une ligne de
    # journal ; le reste est laissé tel quel, accents compris.
    brut = "".join(caractere for caractere in brut if caractere.isprintable())
    brut = brut[:255].strip()
    return brut or "document"


def _refuser_html(tete: bytes) -> None:
    debut = tete.lstrip()[:16].lower()
    if any(debut.startswith(marque) for marque in _DEBUTS_HTML):
        raise ContenuRefuse(
            "html_refused",
            "Un contenu HTML ou XML n'est pas accepté comme document de chantier.",
        )


def _type_ooxml(chemin: Path) -> str | None:
    try:
        with zipfile.ZipFile(chemin) as archive:
            noms = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    for marqueur, media_type in _MARQUEURS_OOXML:
        if marqueur in noms:
            return media_type
    return None


def _ressemble_a_du_texte(chemin: Path, taille: int) -> bool:
    """Un CSV n'a pas de signature : on constate qu'il est du texte lisible."""
    with chemin.open("rb") as fichier:
        echantillon = fichier.read(min(taille, 64 * 1024))
    if b"\x00" in echantillon:
        return False
    for encodage in ("utf-8", "cp1252"):
        try:
            echantillon.decode(encodage)
        except UnicodeDecodeError:
            continue
        return True
    return False


def detecter_type(chemin: Path, taille: int) -> str:
    """Le type RÉEL, lu dans les octets. L'extension n'est jamais consultée.

    Lève `ContenuRefuse` plutôt que de rendre un type par défaut : un contenu
    que l'on ne sait pas nommer n'a pas sa place sur le volume.
    """
    with chemin.open("rb") as fichier:
        tete = fichier.read(max(OCTETS_DE_TETE, 16))

    for signature, quoi in _SIGNATURES_REFUSEES:
        if tete.startswith(signature):
            raise ContenuRefuse(
                "unsupported_content",
                f"Le fichier est {quoi}, quel que soit son nom ou son extension.",
            )

    for signature, media_type in _SIGNATURES:
        if tete.startswith(signature):
            return media_type

    if tete.startswith(_ZIP):
        ooxml = _type_ooxml(chemin)
        if ooxml is None:
            raise ContenuRefuse(
                "unsupported_content",
                "Une archive qui n'est ni un .docx ni un .xlsx n'est pas acceptée.",
            )
        return ooxml

    _refuser_html(tete)

    if _ressemble_a_du_texte(chemin, taille):
        return "text/csv"

    raise ContenuRefuse(
        "unsupported_content",
        "Type de fichier non reconnu. Formats acceptés : PDF, PNG, JPEG, CSV, XLSX, DOCX.",
    )


class StockageLocal:
    """Le volume, vu comme un magasin d'objets adressés par clé.

    Une seule instance suffit ; elle ne porte aucun état mutable. La racine
    vient de `METREO_STORAGE_ROOT`, le même chemin que la sauvegarde archive et
    que la restauration rend.
    """

    def __init__(self, racine: str | os.PathLike[str]) -> None:
        self._racine = Path(racine)

    @property
    def racine(self) -> Path:
        return self._racine

    def _dossier(self, organization_id: str, document_id: str) -> Path:
        # Partitionné par organisation PUIS par document : une restauration
        # partielle, une purge RGPD ou un simple `du` se font par tenant sans
        # parcourir le volume entier.
        return self._racine / "documents" / organization_id / document_id

    def chemin(self, storage_key: str) -> Path:
        """Le chemin absolu d'une clé, confiné à la racine.

        `storage_key` sort de la base, où seul ce module l'a écrite. Le
        confinement est vérifié malgré tout : c'est la ligne qui survivra à un
        futur code qui, lui, accepterait une clé venue d'ailleurs.
        """
        candidat = (self._racine / storage_key).resolve()
        racine = self._racine.resolve()
        if not candidat.is_relative_to(racine):
            raise ContenuRefuse(
                "invalid_storage_key",
                "Clé de stockage hors de la racine.",
            )
        return candidat

    def ecrire(
        self,
        *,
        organization_id: str,
        document_id: str,
        revision_id: str,
        morceaux: Iterable[bytes],
        plafond: int,
        declared_media_type: str | None,
    ) -> OriginalStocke:
        """Écrit un flux, puis ne publie le fichier que s'il est acceptable.

        L'ordre compte, et il est le sujet de cette fonction :

        1. on écrit dans un temporaire du MÊME dossier — donc du même système
           de fichiers, sans quoi le renommage final ne serait pas atomique et
           se dégraderait en copie ;
        2. on compte et on hache PENDANT l'écriture, et on s'arrête à
           l'octet qui dépasse le plafond — pas après avoir tout absorbé ;
        3. on ne détecte le type qu'une fois le fichier complet, parce qu'un
           conteneur OOXML ne se reconnaît qu'à son sommaire, écrit à la fin ;
        4. on ne rend le fichier visible qu'ensuite, d'un `os.replace`, qui est
           atomique : aucun lecteur ne peut voir un fichier à moitié écrit.

        Un échec à n'importe quelle étape laisse le volume dans l'état où il
        était : le temporaire est supprimé, y compris si l'interruption vient
        du client au milieu du flux.
        """
        dossier = self._dossier(organization_id, document_id)
        dossier.mkdir(parents=True, exist_ok=True)

        descripteur, temporaire_nom = tempfile.mkstemp(
            dir=dossier, prefix=".depot-", suffix=".part"
        )
        temporaire = Path(temporaire_nom)
        empreinte = hashlib.sha256()
        ecrits = 0
        try:
            with os.fdopen(descripteur, "wb") as sortie:
                for morceau in morceaux:
                    if not morceau:
                        continue
                    ecrits += len(morceau)
                    if ecrits > plafond:
                        raise TropVolumineux(plafond)
                    empreinte.update(morceau)
                    sortie.write(morceau)
                sortie.flush()
                # Le devoir de persistance est ici : sans `fsync`, un
                # redémarrage brutal peut rendre un fichier référencé en base
                # mais vide sur le disque.
                os.fsync(sortie.fileno())

            if ecrits == 0:
                raise ContenuRefuse("empty_file", "Le fichier est vide.")

            media_type = detecter_type(temporaire, ecrits)
            if declared_media_type and declared_media_type.split(";")[0].strip() not in (
                media_type,
                "application/octet-stream",
            ):
                raise ContenuRefuse(
                    "media_type_mismatch",
                    f"Le contenu est un {media_type}, alors que « {declared_media_type} » "
                    "est annoncé.",
                )

            cle = f"documents/{organization_id}/{document_id}/{revision_id}{EXTENSIONS[media_type]}"
            destination = self.chemin(cle)
            if destination.exists():
                # Impossible avec un identifiant engendré, et c'est bien pour
                # cela qu'on le dit : si cela arrive un jour, il vaut mieux un
                # refus qu'un original écrasé.
                raise ContenuRefuse("storage_collision", "Cette clé de stockage existe déjà.")
            temporaire.chmod(0o640)
            os.replace(temporaire, destination)
        except BaseException:
            temporaire.unlink(missing_ok=True)
            raise

        return OriginalStocke(
            storage_key=cle,
            sha256=empreinte.hexdigest(),
            byte_size=ecrits,
            media_type=media_type,
            declared_media_type=declared_media_type,
        )

    def lire(self, storage_key: str) -> Iterator[bytes]:
        """Relit un original par morceaux, sans suivre de lien symbolique."""
        chemin = self.chemin(storage_key)
        descripteur = os.open(chemin, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descripteur, "rb") as fichier:
            while morceau := fichier.read(TAILLE_MORCEAU):
                yield morceau

    def taille(self, storage_key: str) -> int | None:
        """La taille RÉELLE sur le disque, ou `None` si le fichier a disparu."""
        try:
            return self.chemin(storage_key).stat().st_size
        except (OSError, ContenuRefuse):
            return None

    def supprimer(self, storage_key: str) -> None:
        """Réservé au nettoyage interne — aucune route ne l'expose."""
        try:
            self.chemin(storage_key).unlink(missing_ok=True)
        except ContenuRefuse:
            return
