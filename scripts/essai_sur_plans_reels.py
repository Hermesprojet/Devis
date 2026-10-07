#!/usr/bin/env python3
"""L'essai sur plans réels, contre une pile LOCALE et isolée (`ops/essai_local.sh`).

    python scripts/essai_sur_plans_reels.py preparer --base URL --email ADMIN
    python scripts/essai_sur_plans_reels.py deposer  --base URL --email ADMIN --projet REF --fichier PLAN
    python scripts/essai_sur_plans_reels.py textes   --base URL --email ADMIN --document ID --revision ID --motif REGEX
    python scripts/essai_sur_plans_reels.py tuile    --base URL --email ADMIN --document ID --revision ID --x X --y Y --sortie Z.png
    python scripts/essai_sur_plans_reels.py pixel    --tuile Z.png --px PX --py PY
    python scripts/essai_sur_plans_reels.py dxf      --base URL --email ADMIN --document ID --revision ID [--fichier PLAN.dxf]
    python scripts/essai_sur_plans_reels.py rapport  --base URL --email ADMIN --plan PLAN_D_ESSAI.json --resultats DOSSIER

**Ce que ce script est.** L'instrument de mesure de l'essai, pas l'opérateur.
Il prépare un chantier FICTIF, dépose un plan, aide à repérer et à pointer,
lit ce que Metreo a mesuré, le compare à des références que l'opérateur a
écrites lui-même, et rédige le compte rendu. Les gestes de l'essai — déclarer
l'échelle, pointer, trancher, reprendre, chiffrer, émettre — passent par
l'interface (`apps/web/essai/parcours-reel.spec.ts`) : c'est elle qu'on éprouve.

**Ce qu'il ne fait jamais.** Il ne parle qu'à l'adresse qu'on lui donne, et
refuse toute adresse qui n'est pas la boucle locale : un plan client ne part
vers aucun serveur. Il n'écrit rien dans le dépôt : ses sorties vont dans le
dossier qu'on lui désigne, et il refuse un dossier situé dans le dépôt. Il ne
calcule AUCUNE quantité à la place de Metreo : les écarts qu'il rapporte sont
des différences entre ce que Metreo affiche et ce que l'opérateur attendait.

**Ce que ses chiffres ne sont pas.** Une garantie de précision. Cinq pointages
d'une même cote disent la dispersion de CES cinq pointages, sur CE plan, par
CET opérateur. Rien de plus — et le compte rendu le dit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

RACINE = Path(__file__).resolve().parents[1]

#: Ce qui marque, partout où il apparaît, ce qui n'est pas réel. Un prix fictif
#: qui ne le dirait pas finirait, tôt ou tard, recopié dans un vrai devis.
MARQUE_FICTIVE = "FICTIF — essai sur plans réels, sans valeur commerciale"

#: Les types d'entité dont le lecteur DXF de Metreo tire des mesures. Tout le
#: reste est lu, compté et rendu visible, mais ne devient pas une quantité.
TYPES_MESURES_PAR_LE_LECTEUR = frozenset({"DIMENSION"})

#: Les familles de cotation DXF, par la valeur basse de `dimtype`.
FAMILLES_DXF = {
    0: "linéaire",
    1: "alignée",
    2: "angulaire (2 lignes)",
    3: "diamètre",
    4: "rayon",
    5: "angulaire (3 points)",
    6: "ordonnée",
}


class EchecDEssai(RuntimeError):
    """Une étape de l'essai n'a pas abouti ; le message dit laquelle et pourquoi."""


# ---------------------------------------------------------------------------
# Garde-fous : la boucle locale, et rien hors du dossier privé
# ---------------------------------------------------------------------------


def exiger_la_boucle_locale(base: str) -> str:
    """Refuse toute API qui ne serait pas sur cette machine.

    Le plan d'un client est confidentiel. Une faute de frappe dans `--base`
    ne doit pas suffire à l'envoyer ailleurs.
    """
    hote = urlparse(base).hostname or ""
    if hote not in {"127.0.0.1", "localhost", "::1"}:
        raise EchecDEssai(
            f"« {base} » n'est pas une adresse locale. Cet essai ne parle qu'à une pile "
            "montée sur cette machine (ops/essai_local.sh) : un plan client ne part pas."
        )
    return base.rstrip("/")


def exiger_hors_du_depot(chemin: Path) -> Path:
    """Refuse d'écrire un résultat — tuile, PDF, compte rendu — dans le dépôt."""
    absolu = chemin.resolve()
    if absolu == RACINE or RACINE in absolu.parents:
        raise EchecDEssai(
            f"« {absolu} » est dans le dépôt. Les sorties de l'essai portent des extraits "
            "de plans clients : écrivez-les dans le dossier privé de l'essai."
        )
    return absolu


def ecrire_prive(chemin: Path, contenu: bytes | str) -> Path:
    """Écrit un fichier lisible par son seul propriétaire."""
    chemin = exiger_hors_du_depot(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if isinstance(contenu, str):
        chemin.write_text(contenu, encoding="utf-8")
    else:
        chemin.write_bytes(contenu)
    chemin.chmod(0o600)
    return chemin


# ---------------------------------------------------------------------------
# Le client HTTP
# ---------------------------------------------------------------------------


class Api:
    """Un porteur de jeton, connecté par la connexion de développement locale."""

    def __init__(self, base: str, email: str) -> None:
        self.base = exiger_la_boucle_locale(base)
        # Long délai : l'analyse d'un grand plan prend plusieurs secondes, et
        # la première tuile d'une page aussi.
        self.client = httpx.Client(timeout=600.0)
        reponse = self.client.post(f"{self.base}/auth/dev-login", json={"email": email})
        self._verifier(reponse, "POST /auth/dev-login")
        self.client.headers["Authorization"] = f"Bearer {reponse.json()['access_token']}"

    @staticmethod
    def _verifier(reponse: httpx.Response, quoi: str) -> None:
        if reponse.status_code >= 400:
            raise EchecDEssai(f"{quoi} → {reponse.status_code}\n    {reponse.text[:800]}")

    def get(self, chemin: str, **params: Any) -> Any:
        reponse = self.client.get(f"{self.base}{chemin}", params=params or None)
        self._verifier(reponse, f"GET {chemin}")
        return reponse.json()

    def brut(self, chemin: str, **params: Any) -> httpx.Response:
        reponse = self.client.get(f"{self.base}{chemin}", params=params or None)
        self._verifier(reponse, f"GET {chemin}")
        return reponse

    def post(self, chemin: str, corps: Any = None, **params: Any) -> Any:
        reponse = self.client.post(f"{self.base}{chemin}", json=corps, params=params or None)
        self._verifier(reponse, f"POST {chemin}")
        return reponse.json() if reponse.content else None

    def patch(self, chemin: str, corps: Any) -> Any:
        reponse = self.client.patch(f"{self.base}{chemin}", json=corps)
        self._verifier(reponse, f"PATCH {chemin}")
        return reponse.json()

    def deposer(self, chemin: str, fichier: Path) -> Any:
        with fichier.open("rb") as flux:
            reponse = self.client.post(
                f"{self.base}{chemin}",
                files={"file": (fichier.name, flux, "application/octet-stream")},
            )
        self._verifier(reponse, f"POST {chemin}")
        return reponse.json()


# ---------------------------------------------------------------------------
# preparer — le chantier FICTIF qui accueille les plans
# ---------------------------------------------------------------------------

#: L'émetteur et le client du devis d'essai. Fictifs, et ils le disent : un
#: devis émis pendant l'essai ne doit jamais pouvoir passer pour un vrai.
PROFIL_FICTIF = {
    "legal_name": f"Entreprise d'essai ({MARQUE_FICTIVE})",
    "address": "Rue de l'Essai 1 (adresse fictive)",
    "postal_code": "1000",
    "city": "Bruxelles",
    "country_code": "BE",
}
CLIENT_FICTIF = {
    "name": "Client fictif de l'essai",
    "billing_address": "Avenue Imaginaire 2 (adresse fictive)",
    "postal_code": "1040",
    "city": "Etterbeek",
    "notes": MARQUE_FICTIVE,
}

#: Les prix de l'essai. FICTIFS : ils ne disent rien du coût d'un ouvrage et
#: n'existent que pour que la chaîne mesure → bordereau → devis aille au bout.
PRIX_FICTIFS = (
    {
        "code": "ESSAI-ML",
        "label": "Poste au mètre linéaire (PRIX FICTIF)",
        "unit_code": "m",
        "unit_price": "10.00",
    },
    {
        "code": "ESSAI-M2",
        "label": "Poste au mètre carré (PRIX FICTIF)",
        "unit_code": "m2",
        "unit_price": "10.00",
    },
)


def _une_seule(elements: list[dict[str, Any]], **filtre: Any) -> dict[str, Any] | None:
    for element in elements:
        if all(element.get(cle) == valeur for cle, valeur in filtre.items()):
            return element
    return None


def preparer(api: Api, reference: str, nom: str) -> dict[str, str]:
    """Profil émetteur, client, bibliothèque de prix et chantier — tous fictifs.

    Idempotent : relancé, il retrouve ce qu'il a créé au lieu de le doubler.
    Aucun taux de taxe n'est créé : Metreo n'en invente aucun, et cet essai non
    plus. Le devis d'essai est donc émis sans TVA, ce que le compte rendu dit.
    """
    organisation = api.get("/organization")
    manquants = {cle: valeur for cle, valeur in PROFIL_FICTIF.items() if not organisation.get(cle)}
    if manquants:
        api.patch("/organization", manquants)

    clients = api.get("/clients")
    client = _une_seule(clients, name=CLIENT_FICTIF["name"]) or api.post("/clients", CLIENT_FICTIF)

    bibliotheques = api.get("/price-books")
    nom_bibliotheque = "Bibliothèque d'essai — PRIX FICTIFS"
    bibliotheque = _une_seule(bibliotheques, name=nom_bibliotheque) or api.post(
        "/price-books",
        {"name": nom_bibliotheque, "currency": "EUR", "description": MARQUE_FICTIVE},
    )
    versions = api.get(f"/price-books/{bibliotheque['id']}/versions")
    version = (
        versions[0]
        if versions
        else api.post(f"/price-books/{bibliotheque['id']}/versions", label="essai")
    )
    deja = {
        prix["code"] for prix in api.get(f"/price-books/versions/{version['id']}/items")["items"]
    }
    for prix in PRIX_FICTIFS:
        if prix["code"] not in deja:
            api.post(
                f"/price-books/versions/{version['id']}/items",
                {**prix, "resource_kind": "other", "currency": "EUR", "source": MARQUE_FICTIVE},
            )

    page = api.get("/projects", q=reference)
    projet = _une_seule(page["items"], reference=reference) or api.post(
        "/projects", {"reference": reference, "name": nom}
    )
    if projet.get("client_id") != client["id"]:
        projet = api.patch(f"/projects/{projet['id']}", {"client_id": client["id"]})
    bordereaux = api.get(f"/projects/{projet['id']}/boqs")
    bordereau = (
        bordereaux[0]
        if bordereaux
        else api.post(f"/projects/{projet['id']}/boqs", {"name": "Bordereau de l'essai"})
    )
    return {
        "project_id": projet["id"],
        "client_id": client["id"],
        "price_book_version_id": version["id"],
        "boq_id": bordereau["id"],
    }


# ---------------------------------------------------------------------------
# deposer — un plan, sa révision, et ce que Metreo en lit
# ---------------------------------------------------------------------------


def empreinte(fichier: Path) -> str:
    condensat = hashlib.sha256()
    with fichier.open("rb") as flux:
        for bloc in iter(lambda: flux.read(1 << 20), b""):
            condensat.update(bloc)
    return condensat.hexdigest()


def deposer(api: Api, projet_id: str, fichier: Path, titre: str) -> dict[str, Any]:
    document = api.post(f"/projects/{projet_id}/documents", {"title": titre})
    revision = api.deposer(f"/documents/{document['id']}/revisions", fichier)
    plan = api.post(f"/documents/{document['id']}/revisions/{revision['id']}/plan/analyse")
    return {
        "fichier": fichier.name,
        "sha256": empreinte(fichier),
        "octets": fichier.stat().st_size,
        "document_id": document["id"],
        "revision_id": revision["id"],
        "media_type": revision.get("media_type"),
        "plan": resume_du_plan(plan),
    }


def resume_du_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Ce que l'écran annoncerait : format, unité, pages, extraction, refus."""
    return {
        "format": plan.get("format"),
        "mesurable": plan.get("mesurable"),
        "refuse": plan.get("refuse"),
        "motif_du_refus": plan.get("motif_du_refus"),
        "unite_source": plan.get("unite_source"),
        "insunits": plan.get("insunits"),
        "version_dxf": plan.get("version_dxf"),
        "pages": plan.get("pages"),
        "dimensions_des_pages": plan.get("dimensions_des_pages"),
        "caracteres_extraits": plan.get("caracteres_extraits"),
        "fragments_lus": plan.get("fragments_lus"),
        "traces_vectoriels": plan.get("traces_vectoriels"),
        "probablement_scanne": plan.get("probablement_scanne"),
        "entites": plan.get("entites"),
        "anomalies": plan.get("anomalies"),
        "mesures_proposees": len(plan.get("mesures") or []),
    }


# ---------------------------------------------------------------------------
# Repérer et pointer : textes, tuiles, pixels
# ---------------------------------------------------------------------------


def _chemin_de_revision(document: str, revision: str) -> str:
    return f"/documents/{document}/revisions/{revision}"


def textes(
    api: Api, document: str, revision: str, page: int, motif: str | None
) -> list[dict[str, Any]]:
    filtre = re.compile(motif) if motif else None
    trouves: list[dict[str, Any]] = []
    depuis = 0
    while True:
        tranche = api.get(
            f"{_chemin_de_revision(document, revision)}/plan/textes", page=page, depuis=depuis
        )
        fragments = tranche.get("fragments") or []
        if not fragments:
            break
        for fragment in fragments:
            if filtre is None or filtre.search(fragment.get("texte", "")):
                trouves.append(fragment)
        depuis += len(fragments)
        if depuis >= int(tranche.get("total") or 0):
            break
    return trouves


@dataclass(frozen=True)
class Tuile:
    """Une zone rendue par Metreo, et de quoi convertir un pixel en point de plan."""

    page: int
    zone: tuple[float, float, float, float]
    pixels: tuple[int, int]
    page_en_points: tuple[float, float]

    @property
    def points_par_pixel(self) -> float:
        """La résolution du pointage dans cette image : ε = max(zone_pt / pixels).

        C'est la grandeur que l'écran transmet comme `resolution_du_pointage`,
        calculée de la même façon — sur l'image rendue, pas sur la zone demandée.
        """
        x0, y0, x1, y1 = self.zone
        largeur, hauteur = self.page_en_points
        return max((x1 - x0) * largeur / self.pixels[0], (y1 - y0) * hauteur / self.pixels[1])

    def vers_la_page(self, px: float, py: float) -> tuple[float, float]:
        """Le centre du pixel (px, py) — un clic tombe sur un pixel entier."""
        x0, y0, x1, y1 = self.zone
        return (
            x0 + (int(px) + 0.5) / self.pixels[0] * (x1 - x0),
            y0 + (int(py) + 0.5) / self.pixels[1] * (y1 - y0),
        )


def tuile(
    api: Api, document: str, revision: str, page: int, x: float, y: float, demi: float, sortie: Path
) -> Tuile:
    plan = api.get(f"{_chemin_de_revision(document, revision)}/plan")
    dimensions = (plan.get("dimensions_des_pages") or [])[page - 1]
    zone_demandee = (max(0.0, x - demi), max(0.0, y - demi), min(1.0, x + demi), min(1.0, y + demi))
    reponse = api.brut(
        f"{_chemin_de_revision(document, revision)}/plan/tuile",
        page=page,
        x0=zone_demandee[0],
        y0=zone_demandee[1],
        x1=zone_demandee[2],
        y1=zone_demandee[3],
    )
    zone = tuple(float(v) for v in reponse.headers["X-Metreo-Zone"].split(","))
    largeur, hauteur = (int(v) for v in reponse.headers["X-Metreo-Pixels"].split(","))
    rendue = Tuile(
        page=page,
        zone=(zone[0], zone[1], zone[2], zone[3]),
        pixels=(largeur, hauteur),
        page_en_points=(float(dimensions[0]), float(dimensions[1])),
    )
    ecrire_prive(sortie, reponse.content)
    ecrire_prive(
        sortie.with_suffix(".json"),
        json.dumps(
            {
                "page": page,
                "zone": list(rendue.zone),
                "pixels": list(rendue.pixels),
                "page_en_points": list(rendue.page_en_points),
                "points_par_pixel": rendue.points_par_pixel,
            },
            indent=2,
        ),
    )
    return rendue


def lire_tuile(png: Path) -> Tuile:
    meta = json.loads(png.with_suffix(".json").read_text(encoding="utf-8"))
    return Tuile(
        page=int(meta["page"]),
        zone=tuple(meta["zone"]),  # type: ignore[arg-type]
        pixels=tuple(meta["pixels"]),  # type: ignore[arg-type]
        page_en_points=tuple(meta["page_en_points"]),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# dxf — ce que le lecteur reconnaît, et ce qu'il ne reconnaît pas
# ---------------------------------------------------------------------------


def recensement_local_des_cotations(fichier: Path, profondeur_max: int = 8) -> dict[str, Any]:
    """Les cotations que le FICHIER porte, comptées sans passer par Metreo.

    C'est la référence indépendante du lecteur : ezdxf, ouvert ici, sur le
    fichier privé, sans rien envoyer. On compte les cotations atteignables
    depuis l'espace objet — directement ou à travers des blocs insérés, chaque
    insertion comptant pour une — et celles des mises en page papier, que le
    lecteur de Metreo ne lit pas.
    """
    import ezdxf  # dépendance de l'extra « plans », présente dans la pile locale

    document = ezdxf.readfile(str(fichier))
    par_famille: Counter[str] = Counter()
    par_conteneur: Counter[str] = Counter()
    sans_handle = 0

    def parcourir(entites: Any, conteneur: str, profondeur: int) -> None:
        nonlocal sans_handle
        for entite in entites:
            genre = entite.dxftype()
            if genre == "DIMENSION":
                par_famille[FAMILLES_DXF.get(entite.dimtype & 0x0F, "autre")] += 1
                par_conteneur[conteneur] += 1
                if not entite.dxf.get("handle"):
                    sans_handle += 1
            elif genre == "INSERT" and profondeur < profondeur_max:
                bloc = document.blocks.get(entite.dxf.name)
                if bloc is not None:
                    parcourir(bloc, f"bloc {entite.dxf.name}", profondeur + 1)

    parcourir(document.modelspace(), "espace objet", 0)
    papier: Counter[str] = Counter()
    for mise_en_page in document.layouts:
        if mise_en_page.name == "Model":
            continue
        for entite in mise_en_page:
            if entite.dxftype() == "DIMENSION":
                papier[mise_en_page.name] += 1
    return {
        "insunits": document.header.get("$INSUNITS"),
        "cotations_atteignables_depuis_l_espace_objet": sum(par_conteneur.values()),
        "par_famille": dict(par_famille.most_common()),
        "par_conteneur": dict(par_conteneur.most_common(12)),
        "sans_handle": sans_handle,
        "cotations_en_espace_papier": dict(papier),
    }


def constat_dxf(api: Api, document: str, revision: str, fichier: Path | None) -> dict[str, Any]:
    plan = api.get(f"{_chemin_de_revision(document, revision)}/plan")
    mesures = plan.get("mesures") or []
    entites: dict[str, int] = plan.get("entites") or {}
    non_mesurees = {
        genre: nombre
        for genre, nombre in sorted(entites.items(), key=lambda item: -item[1])
        if genre not in TYPES_MESURES_PAR_LE_LECTEUR
    }
    familles = Counter(str(m.get("famille")) for m in mesures)
    reserves = Counter(
        str(reserve.get("code")) for m in mesures for reserve in (m.get("reserves") or [])
    )
    imposes = [
        {
            "handle": m.get("object_ref"),
            "texte_impose": m.get("texte_impose"),
            "valeur_document": m.get("valeur_document"),
            "unite": m.get("unite_document"),
        }
        for m in mesures
        if m.get("texte_impose")
    ]
    constat: dict[str, Any] = {
        "unite_source": plan.get("unite_source"),
        "insunits": plan.get("insunits"),
        "version_dxf": plan.get("version_dxf"),
        "mesurable": plan.get("mesurable"),
        "refuse": plan.get("refuse"),
        "motif_du_refus": plan.get("motif_du_refus"),
        "anomalies": plan.get("anomalies"),
        "feuilles": plan.get("feuilles"),
        "entites": entites,
        "types_lus_sans_devenir_une_mesure": non_mesurees,
        "cotations_proposees": len(mesures),
        "cotations_proposees_par_famille": dict(familles.most_common()),
        "reserves_des_cotations": dict(reserves.most_common()),
        "cotations_a_texte_impose": imposes,
    }
    if fichier is not None:
        recensement = recensement_local_des_cotations(fichier)
        constat["recensement_local"] = recensement
        constat["cotations_du_fichier_non_proposees"] = recensement[
            "cotations_atteignables_depuis_l_espace_objet"
        ] - len(mesures)
    return constat


def cotation_dxf(api: Api, document: str, revision: str, handle: str) -> dict[str, Any] | None:
    """La cotation DXF portant ce handle, telle que Metreo l'a lue."""
    plan = api.get(f"{_chemin_de_revision(document, revision)}/plan")
    for mesure in plan.get("mesures") or []:
        if mesure.get("object_ref") == handle:
            return dict(mesure)
    return None


# ---------------------------------------------------------------------------
# rapport — attendu, mesuré, écart ; dispersion ; la quantité jusqu'au PDF
# ---------------------------------------------------------------------------


def _decimal(valeur: Any) -> Decimal:
    try:
        return Decimal(str(valeur))
    except (InvalidOperation, ValueError) as exc:
        raise EchecDEssai(f"« {valeur} » n'est pas un nombre décimal") from exc


@dataclass(frozen=True)
class Ecart:
    attendu: Decimal
    mesure: Decimal
    incertitude: Decimal

    @property
    def absolu(self) -> Decimal:
        return self.mesure - self.attendu

    @property
    def relatif(self) -> Decimal:
        return self.absolu / self.attendu

    @property
    def dans_le_plus_ou_moins(self) -> bool:
        return abs(self.absolu) <= self.incertitude

    @property
    def dans_deux_fois(self) -> bool:
        return abs(self.absolu) <= 2 * self.incertitude


def comparer(attendu: Any, mesure: dict[str, Any], unite_attendue: str) -> Ecart:
    """L'écart entre une référence et une mesure DANS LA MÊME UNITÉ.

    Une référence dans une autre unité que la mesure est refusée plutôt que
    convertie : une conversion écrite ici serait une seconde arithmétique, et
    c'est précisément ce que l'essai doit éviter d'introduire.
    """
    if mesure.get("unite") != unite_attendue:
        raise EchecDEssai(
            f"« {mesure.get('libelle')} » est en {mesure.get('unite')}, la référence en "
            f"{unite_attendue} : écrivez la référence dans l'unité de la mesure."
        )
    return Ecart(_decimal(attendu), _decimal(mesure["valeur"]), _decimal(mesure["incertitude"]))


def dispersion(valeurs: list[Decimal]) -> dict[str, str]:
    """Ce que cinq pointages disent d'eux-mêmes — et rien au-delà."""
    flottants = [float(v) for v in valeurs]
    return {
        "n": str(len(valeurs)),
        "min": str(min(valeurs)),
        "max": str(max(valeurs)),
        "etendue": str(max(valeurs) - min(valeurs)),
        "moyenne": f"{statistics.fmean(flottants):.4f}",
        "ecart_type": f"{statistics.stdev(flottants):.4f}" if len(valeurs) > 1 else "—",
    }


def en_belge_pour_le_pdf(texte: str) -> str:
    """« 12.3400 » → « 12,3400 », milliers groupés par l'espace insécable U+00A0.

    Une transcription, comme celle du PDF : ni arrondi, ni décimale choisie.
    Écrite ici et non importée, pour que le contrôle ne dépende pas du code
    qu'il contrôle (même raison que `ops/parcours_devis.py`).
    """
    signe = "-" if texte.startswith("-") else ""
    entiere, _, fraction = texte.lstrip("-").partition(".")
    groupes: list[str] = []
    while len(entiere) > 3:
        groupes.insert(0, entiere[-3:])
        entiere = entiere[:-3]
    groupes.insert(0, entiere)
    ecrite = " ".join(groupes)
    return f"{signe}{ecrite},{fraction}" if fraction else f"{signe}{ecrite}"


def texte_du_pdf(octets: bytes) -> str:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(octets)
    return "\n".join(document[i].get_textpage().get_text_range() for i in range(len(document)))


def rapport(api: Api, plan_d_essai: dict[str, Any], resultats: Path) -> dict[str, Any]:
    pdf = plan_d_essai["pdf"]
    lues = api.get(f"{_chemin_de_revision(pdf['document_id'], pdf['revision_id'])}/plan/mesures")
    par_libelle = {m["libelle"]: m for m in lues.get("mesures") or []}
    calibrations = lues.get("calibrations") or []

    lignes: list[dict[str, Any]] = []
    for reference in plan_d_essai.get("references", []):
        mesure = par_libelle.get(reference["libelle"])
        if mesure is None:
            lignes.append({**reference, "statut": "non mesurée"})
            continue
        ecart = comparer(reference["attendu"], mesure, reference["unite"])
        ligne = {
            **reference,
            "statut": "mesurée",
            "mesure": mesure["valeur"],
            "mesure_lisible": mesure.get("valeur_lisible"),
            "incertitude": mesure["incertitude"],
            "incertitude_lisible": mesure.get("incertitude_lisible"),
            "fiabilite": mesure.get("fiabilite"),
            "reserves": mesure.get("reserves"),
            "ecart_absolu": str(ecart.absolu),
            "ecart_relatif_pourcent": f"{ecart.relatif * 100:.3f}",
            "dans_le_plus_ou_moins": ecart.dans_le_plus_ou_moins,
            "dans_deux_fois_le_plus_ou_moins": ecart.dans_deux_fois,
        }
        handle = reference.get("dxf_handle")
        dxf = plan_d_essai.get("dxf")
        if handle and dxf:
            cotation = cotation_dxf(api, dxf["document_id"], dxf["revision_id"], handle)
            if cotation is None:
                ligne["dxf"] = {"handle": handle, "statut": "absente des cotations lues par Metreo"}
            else:
                ligne["dxf"] = {
                    "handle": handle,
                    "valeur": cotation.get("valeur_document"),
                    "unite": cotation.get("unite_document"),
                    "famille": cotation.get("famille"),
                    "texte_impose": cotation.get("texte_impose"),
                    "fiabilite": cotation.get("fiabilite"),
                }
                if cotation.get("unite_document") == mesure.get("unite"):
                    ligne["dxf"]["ecart_pdf_moins_dxf"] = str(
                        _decimal(mesure["valeur"]) - _decimal(cotation["valeur_document"])
                    )
        lignes.append(ligne)

    repetitions: dict[str, Any] | None = None
    regle = plan_d_essai.get("repetitions")
    if regle:
        tirages = [
            m for libelle, m in sorted(par_libelle.items()) if libelle.startswith(regle["prefixe"])
        ]
        valeurs = [_decimal(m["valeur"]) for m in tirages]
        repetitions = {
            "prefixe": regle["prefixe"],
            "attendu": regle["attendu"],
            "unite": regle["unite"],
            "valeurs": [str(v) for v in valeurs],
            "incertitudes": [m["incertitude"] for m in tirages],
            "ecarts": [str(v - _decimal(regle["attendu"])) for v in valeurs],
        }
        if len(valeurs) >= 2:
            repetitions["dispersion"] = dispersion(valeurs)

    chaine = verifier_la_chaine(api, plan_d_essai, resultats)
    constat = {
        "plan_d_essai": plan_d_essai.get("titre"),
        "calibrations": calibrations,
        "references": lignes,
        "repetitions": repetitions,
        "chaine": chaine,
    }
    ecrire_prive(resultats / "compte-rendu.json", json.dumps(constat, indent=2, ensure_ascii=False))
    ecrire_prive(resultats / "compte-rendu.md", en_markdown(plan_d_essai, constat))
    return constat


def verifier_la_chaine(
    api: Api, plan_d_essai: dict[str, Any], resultats: Path
) -> dict[str, Any] | None:
    """La quantité retenue, du bordereau au PDF du devis, à l'identique."""
    regle = plan_d_essai.get("reprise")
    if not regle:
        return None
    projet = plan_d_essai["project_id"]
    lignes = []
    for bordereau in api.get(f"/projects/{projet}/boqs"):
        lignes.extend(api.get(f"/boqs/{bordereau['id']}/items"))
    ligne = _une_seule(lignes, position=regle["position"])
    if ligne is None:
        return {"statut": f"aucune ligne « {regle['position']} » au bordereau"}
    chaine: dict[str, Any] = {
        "position": ligne["position"],
        "quantite": str(ligne["quantity"]),
        "quantite_lisible": ligne.get("quantity_lisible"),
        "unite": ligne.get("unit_code"),
        "source_proposal_id": ligne.get("source_proposal_id"),
    }
    devis = api.get(f"/projects/{projet}/issued-quotes")
    if not devis:
        chaine["devis"] = "aucun devis émis"
        return chaine
    emis = devis[0]
    octets = api.brut(f"/issued-quotes/{emis['id']}/document.pdf").content
    ecrire_prive(resultats / f"devis-{emis['number']}.pdf", octets)
    texte = texte_du_pdf(octets)
    attendue = en_belge_pour_le_pdf(_decimal(ligne["quantity"]).normalize().__format__("f"))
    stockee = en_belge_pour_le_pdf(str(ligne["quantity"]))
    chaine["devis"] = {
        "numero": emis["number"],
        "quantite_stockee_imprimee": stockee in texte,
        "quantite_sans_zeros_imprimee": attendue in texte,
        "ecriture_cherchee": stockee,
        "mention_fictive_presente": "FICTIF" in texte,
    }
    return chaine


def _cellule(valeur: Any) -> str:
    return "—" if valeur in (None, "") else str(valeur).replace("|", "/")


def _court(valeur: Any, decimales: int = 3) -> str:
    """Un écart, lisible : le compte rendu arrondit pour être lu, la mesure ne l'est pas."""
    if valeur in (None, ""):
        return "—"
    return f"{_decimal(valeur):+.{decimales}f}".replace(".", ",")


def en_markdown(plan_d_essai: dict[str, Any], constat: dict[str, Any]) -> str:
    """Le compte rendu court : une ligne par référence, puis la dispersion et la chaîne.

    Les écarts y sont arrondis à trois décimales pour être lus ; les valeurs
    complètes sont dans `compte-rendu.json`, à côté.
    """
    pdf = plan_d_essai["pdf"]
    lignes = [
        f"# Compte rendu — {plan_d_essai.get('titre', 'essai sur plan réel')}",
        "",
        f"Fichier : `{pdf.get('fichier')}`, page {pdf.get('page', 1)}.",
        "",
    ]
    for calibration in constat.get("calibrations") or []:
        lignes.append(
            f"Échelle déclarée : {calibration['distance_reelle']} {calibration['unite']} — "
            f"facteur rendu {calibration['facteur_lisible']}, résolution du pointage "
            f"{_court(calibration['resolution_du_pointage'], 4).lstrip('+')} point par pixel."
        )
    lignes += [
        "",
        "| Référence | Attendu | Mesuré | ± affiché | Écart absolu | Écart relatif | Dans le ± | DXF | PDF − DXF |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for ligne in constat["references"]:
        dxf = ligne.get("dxf") or {}
        dans = (
            "oui"
            if ligne.get("dans_le_plus_ou_moins")
            else ("à 2 u" if ligne.get("dans_deux_fois_le_plus_ou_moins") else "non")
        )
        lignes.append(
            "| "
            + " | ".join(
                _cellule(v)
                for v in (
                    ligne["libelle"],
                    f"{ligne['attendu']} {ligne['unite']}",
                    ligne.get("mesure_lisible") or ligne.get("mesure"),
                    ligne.get("incertitude_lisible") or ligne.get("incertitude"),
                    f"{_court(ligne.get('ecart_absolu'))} {ligne['unite']}"
                    if ligne.get("ecart_absolu")
                    else None,
                    f"{ligne['ecart_relatif_pourcent'].replace('.', ',')} %"
                    if ligne.get("ecart_relatif_pourcent")
                    else None,
                    dans if ligne.get("statut") == "mesurée" else None,
                    f"{_court(dxf.get('valeur')).lstrip('+')} {dxf.get('unite')}"
                    if dxf.get("valeur")
                    else None,
                    _court(dxf.get("ecart_pdf_moins_dxf"))
                    if dxf.get("ecart_pdf_moins_dxf")
                    else None,
                )
            )
            + " |"
        )
    lignes += ["", "Sources des références :", ""]
    lignes += [
        f"- {ligne['libelle']} : {ligne.get('source', '—')}" for ligne in constat["references"]
    ]

    repetitions = constat.get("repetitions")
    if repetitions and repetitions.get("dispersion"):
        d = repetitions["dispersion"]
        lignes += [
            "",
            f"## Répétition — {repetitions['n'] if 'n' in repetitions else d['n']} pointages",
            "",
            f"Attendu {repetitions['attendu']} {repetitions['unite']}.",
            "",
            "| Min | Max | Étendue | Moyenne | Écart-type |",
            "| --- | --- | --- | --- | --- |",
            f"| {_court(d['min']).lstrip('+')} | {_court(d['max']).lstrip('+')} | "
            f"{_court(d['etendue']).lstrip('+')} | {d['moyenne'].replace('.', ',')} | "
            f"{d['ecart_type'].replace('.', ',')} |",
            "",
            "Ces pointages disent la dispersion de ces pointages, sur ce plan. "
            "Ils ne fondent aucune garantie générale de précision.",
        ]
    chaine = constat.get("chaine")
    if chaine:
        lignes += [
            "",
            "## La quantité retenue, jusqu'au PDF",
            "",
            "```",
            json.dumps(chaine, indent=2, ensure_ascii=False),
            "```",
        ]
    return "\n".join(lignes) + "\n"


# ---------------------------------------------------------------------------
# Ligne de commande
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sous = parseur.add_subparsers(dest="commande", required=True)

    def avec_api(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--base", required=True, help="ex. http://127.0.0.1:8071/api/v1")
        p.add_argument("--email", required=True)
        return p

    def avec_revision(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--document", required=True)
        p.add_argument("--revision", required=True)
        return p

    p = avec_api(sous.add_parser("preparer", help="chantier, client, prix et profil FICTIFS"))
    p.add_argument("--projet", default="ESSAI-PLANS-REELS")
    p.add_argument("--nom", default="Essai sur plans réels (chantier fictif)")

    p = avec_api(sous.add_parser("deposer", help="déposer un plan et le lire"))
    p.add_argument("--projet", default="ESSAI-PLANS-REELS")
    p.add_argument("--fichier", required=True, type=Path)
    p.add_argument("--titre")

    p = avec_revision(avec_api(sous.add_parser("textes", help="les textes d'une page")))
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--motif")

    p = avec_revision(avec_api(sous.add_parser("tuile", help="rendre une zone, pour pointer")))
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--x", type=float, required=True)
    p.add_argument("--y", type=float, required=True)
    p.add_argument(
        "--demi", type=float, default=0.01, help="demi-côté de la zone, en fraction de page"
    )
    p.add_argument("--sortie", type=Path, required=True)

    p = sous.add_parser("pixel", help="un pixel d'une tuile, en point de page et en ε")
    p.add_argument("--tuile", type=Path, required=True)
    p.add_argument("--px", type=float, required=True)
    p.add_argument("--py", type=float, required=True)

    p = avec_revision(
        avec_api(sous.add_parser("dxf", help="ce que le lecteur DXF reconnaît ou non"))
    )
    p.add_argument("--fichier", type=Path, help="le DXF privé, pour un recensement indépendant")
    p.add_argument("--sortie", type=Path)

    p = avec_api(sous.add_parser("rapport", help="écarts, dispersion, chaîne jusqu'au PDF"))
    p.add_argument("--plan", type=Path, required=True, help="le plan d'essai (JSON privé)")
    p.add_argument("--resultats", type=Path, required=True)

    arguments = parseur.parse_args(argv)
    try:
        if arguments.commande == "pixel":
            rendue = lire_tuile(arguments.tuile)
            x, y = rendue.vers_la_page(arguments.px, arguments.py)
            print(json.dumps({"x": x, "y": y, "points_par_pixel": rendue.points_par_pixel}))
            return 0
        api = Api(arguments.base, arguments.email)
        if arguments.commande == "preparer":
            print(json.dumps(preparer(api, arguments.projet, arguments.nom), indent=2))
        elif arguments.commande == "deposer":
            ids = preparer(api, arguments.projet, "Essai sur plans réels (chantier fictif)")
            depot = deposer(
                api, ids["project_id"], arguments.fichier, arguments.titre or arguments.fichier.stem
            )
            print(json.dumps(depot, indent=2, ensure_ascii=False))
        elif arguments.commande == "textes":
            for fragment in textes(
                api, arguments.document, arguments.revision, arguments.page, arguments.motif
            ):
                print(json.dumps(fragment, ensure_ascii=False))
        elif arguments.commande == "tuile":
            rendue = tuile(
                api,
                arguments.document,
                arguments.revision,
                arguments.page,
                arguments.x,
                arguments.y,
                arguments.demi,
                arguments.sortie,
            )
            print(
                json.dumps(
                    {
                        "zone": rendue.zone,
                        "pixels": rendue.pixels,
                        "points_par_pixel": rendue.points_par_pixel,
                    }
                )
            )
        elif arguments.commande == "dxf":
            constat = constat_dxf(api, arguments.document, arguments.revision, arguments.fichier)
            texte = json.dumps(constat, indent=2, ensure_ascii=False)
            if arguments.sortie:
                ecrire_prive(arguments.sortie, texte)
            print(texte)
        elif arguments.commande == "rapport":
            plan_d_essai = json.loads(arguments.plan.read_text(encoding="utf-8"))
            exiger_hors_du_depot(arguments.resultats)
            constat = rapport(api, plan_d_essai, arguments.resultats)
            print(json.dumps(constat, indent=2, ensure_ascii=False))
    except EchecDEssai as echec:
        print(f"essai en échec : {echec}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
