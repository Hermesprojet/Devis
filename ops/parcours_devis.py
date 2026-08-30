"""Produit un devis complet par HTTP, et vérifie ce qui est imprimé.

Ce module est le cœur métier de la répétition de préproduction : il part
d'une organisation VIDE — celle que l'amorçage vient de créer, sans jeu de
démonstration — et va jusqu'au devis gelé, au CSV et à l'aperçu imprimable.

Il est séparé du script de répétition pour deux raisons : il s'éprouve seul
contre une API locale, et il se relit sans traverser du bash.

    python ops/parcours_devis.py --base http://localhost:8080/api/v1 --jeton "$JETON"
    python ops/parcours_devis.py --base ... --jeton ... --verifier-seulement

`--verifier-seulement` relit un devis déjà gelé et contrôle qu'il n'a pas
bougé : c'est ce qu'on lance après un redémarrage et après une restauration.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any

# Deux taux : un devis à un seul taux ne dirait rien de la TVA par base
# taxable, qui est l'endroit où l'arithmétique du document se joue.
LIGNES = [
    {
        "position": "01.10",
        "designation": "Déblai en terrain meuble",
        "unite": "m3",
        "quantite": "1250.5",
        "prix": "18.4567",
    },
    {
        "position": "01.20",
        "designation": "Remblai compacté",
        "unite": "m3",
        "quantite": "870.25",
        "prix": "22.9133",
    },
    {
        "position": "02.10",
        "designation": "Béton de propreté",
        "unite": "m3",
        "quantite": "45.75",
        "prix": "142.8891",
    },
    {
        "position": "02.20",
        "designation": "Coffrage de semelle",
        "unite": "m2",
        "quantite": "312.4",
        "prix": "37.6612",
    },
    {
        "position": "03.10",
        "designation": "Évacuation de terres",
        "unite": "t",
        "quantite": "615.8",
        "prix": "14.2038",
    },
]


class EchecParcours(RuntimeError):
    pass


class Client:
    def __init__(self, base: str, jeton: str) -> None:
        self.base = base.rstrip("/")
        self.jeton = jeton

    def _appel(self, methode: str, chemin: str, corps: Any = None, brut: bool = False):
        url = f"{self.base}{chemin}"
        donnees = json.dumps(corps).encode() if corps is not None else None
        requete = urllib.request.Request(url, data=donnees, method=methode)
        requete.add_header("Authorization", f"Bearer {self.jeton}")
        if donnees is not None:
            requete.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(requete, timeout=60) as reponse:
                charge = reponse.read().decode()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:600]
            raise EchecParcours(f"{methode} {chemin} → {exc.code}\n    {detail}") from exc
        except urllib.error.URLError as exc:
            raise EchecParcours(f"{methode} {chemin} → injoignable : {exc.reason}") from exc
        return charge if brut else (json.loads(charge) if charge else None)

    def get(self, chemin: str, brut: bool = False):
        return self._appel("GET", chemin, brut=brut)

    def post(self, chemin: str, corps: Any = None):
        return self._appel("POST", chemin, corps)


def construire(client: Client, suffixe: str = "") -> dict[str, str]:
    """Du néant au devis gelé.

    `suffixe` distingue les noms d'une exécution à l'autre : bibliothèque et
    projet portent un nom unique par organisation, et une seconde exécution
    sur la même base heurterait sinon un 409.
    """
    etapes: dict[str, str] = {}
    marque = f" {suffixe}".rstrip()

    bibliotheque = client.post(
        "/price-books", {"name": f"Bibliothèque de répétition{marque}", "currency": "EUR"}
    )
    etapes["price_book_id"] = bibliotheque["id"]

    # Le libellé passe en paramètre de requête, pas dans le corps.
    version_prix = client.post(f"/price-books/{bibliotheque['id']}/versions?label=repetition")
    etapes["price_book_version_id"] = version_prix["id"]

    prix_ids = []
    for index, ligne in enumerate(LIGNES, start=1):
        prix = client.post(
            f"/price-books/versions/{version_prix['id']}/items",
            {
                "code": f"REP-{suffixe}-{index:03d}" if suffixe else f"REP-{index:03d}",
                "label": ligne["designation"],
                "unit_code": ligne["unite"],
                "unit_price": ligne["prix"],
                "resource_kind": "material",
                "currency": "EUR",
            },
        )
        prix_ids.append(prix["id"])

    projet = client.post(
        "/projects",
        {
            "reference": f"REP-{suffixe or '2026-001'}",
            "name": f"Chantier de répétition{marque}",
        },
    )
    etapes["project_id"] = projet["id"]

    bordereau = client.post(f"/projects/{projet['id']}/boqs", {"name": "Bordereau de répétition"})
    etapes["boq_id"] = bordereau["id"]

    for ligne, prix_id in zip(LIGNES, prix_ids, strict=True):
        client.post(
            f"/boqs/{bordereau['id']}/items",
            {
                "position": ligne["position"],
                "designation": ligne["designation"],
                "unit_code": ligne["unite"],
                "quantity": ligne["quantite"],
                "kind": "item",
                "price_item_id": prix_id,
            },
        )

    estimation = client.post(
        "/estimates",
        {
            "project_id": projet["id"],
            "boq_id": bordereau["id"],
            "price_book_version_id": version_prix["id"],
            "name": "Étude de répétition",
        },
    )
    etapes["estimate_id"] = estimation["id"]

    version = client.post(f"/estimates/{estimation['id']}/versions", {"label": "v1"})
    etapes["version_id"] = version["id"]

    # Calcul avant gel : geler sans avoir calculé masquerait un poste sans prix.
    client.get(f"/estimates/{estimation['id']}/versions/{version['id']}/computation")

    gel = client.post(
        f"/estimates/{estimation['id']}/versions/{version['id']}/freeze",
        {"confirm": True, "label": "Devis de répétition"},
    )
    etapes["snapshot_sha256"] = gel.get("snapshot_sha256", "")
    return etapes


def _quantifier(valeur: str) -> Decimal:
    return Decimal(str(valeur))


def verifier(
    client: Client, estimate_id: str, version_id: str, *, exiger_tva: bool = False
) -> dict[str, Any]:
    """Les quatre identités du document, sur les trois sorties.

    C'est le contrôle qui distingue « le service répond » de « le devis est
    juste ». Il est rejoué après redémarrage et après restauration : les mêmes
    nombres doivent sortir, sinon la persistance n'a pas tenu.
    """
    base = f"/estimates/{estimate_id}/versions/{version_id}"
    calcul = client.get(f"{base}/computation")["result"]

    total_ht = _quantifier(calcul["total_selling_price_ht"])
    total_ttc = _quantifier(calcul["total_ttc"])

    # 1. le total HT est la somme des lignes IMPRIMÉES et incluses
    somme_lignes = sum(
        (
            _quantifier(ligne["price"]["selling_price_ht"])
            for ligne in calcul["lines"]
            if ligne.get("included_in_total") and ligne.get("price")
        ),
        Decimal(0),
    )
    if somme_lignes != total_ht:
        raise EchecParcours(
            f"le total HT ({total_ht}) n'est pas la somme des lignes imprimées ({somme_lignes})"
        )

    # 2. le TTC est le HT imprimé plus les TVA imprimées
    taxes = calcul.get("taxes") or []
    somme_tva = sum((_quantifier(t["amount"]) for t in taxes), Decimal(0))
    if exiger_tva and somme_tva == 0:
        # Sans TVA, l'identité « TTC = HT + TVA » se vérifie toute seule et ne
        # prouve rien. Une organisation amorcée n'a AUCUN taux — aucune route
        # n'en crée — d'où ce contrôle explicite plutôt qu'un succès trompeur.
        raise EchecParcours(
            "aucune TVA sur ce devis : le contrôle du TTC ne prouverait rien "
            f"({len(taxes)} taux appliqués)"
        )
    if total_ht + somme_tva != total_ttc:
        raise EchecParcours(f"TTC ({total_ttc}) ≠ HT ({total_ht}) + TVA ({somme_tva})")

    # 3. la liste des versions affiche le total DU DOCUMENT
    versions = client.get(f"/estimates/{estimate_id}/versions")
    ligne_version = next((v for v in versions if v["id"] == version_id), None)
    if ligne_version is None:
        raise EchecParcours("la version a disparu de la liste")
    if ligne_version.get("status") != "frozen":
        raise EchecParcours(f"la version n'est pas gelée : {ligne_version.get('status')}")
    affiche = ligne_version.get("total_selling_price_ht_display")
    if affiche is None or _quantifier(affiche) != total_ht:
        raise EchecParcours(
            f"la liste affiche « {affiche} » là où le document imprime « {total_ht} »"
        )

    # 4. les deux documents réellement remis portent ces mêmes nombres
    csv = client.get(f"{base}/export.csv", brut=True)
    html = client.get(f"{base}/quote.html", brut=True)
    for nom, contenu in (("CSV", csv), ("aperçu HTML", html)):
        if str(total_ht) not in contenu:
            raise EchecParcours(f"le total HT {total_ht} n'apparaît pas dans le {nom}")
        if str(total_ttc) not in contenu:
            raise EchecParcours(f"le total TTC {total_ttc} n'apparaît pas dans le {nom}")

    return {
        "estimate_id": estimate_id,
        "version_id": version_id,
        "total_ht": str(total_ht),
        "total_ttc": str(total_ttc),
        "lignes": len(calcul["lines"]),
        "snapshot_sha256": ligne_version.get("snapshot_sha256"),
        "octets_csv": len(csv),
        "octets_html": len(html),
    }


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description="Parcours de devis pour la répétition")
    parseur.add_argument(
        "--base", required=True, help="Racine de l'API, ex. http://localhost:8080/api/v1"
    )
    parseur.add_argument("--jeton", required=True)
    parseur.add_argument("--sortie", help="Fichier JSON où écrire le constat")
    parseur.add_argument(
        "--verifier-seulement",
        action="store_true",
        help="Relire un devis déjà gelé, sans rien créer",
    )
    parseur.add_argument("--estimation", help="Avec --verifier-seulement")
    parseur.add_argument("--version", help="Avec --verifier-seulement")
    parseur.add_argument("--suffixe", default="", help="Distingue les noms entre exécutions")
    parseur.add_argument(
        "--exiger-tva",
        action="store_true",
        help="Échoue si le devis ne porte aucune TVA — le contrôle du TTC serait sinon vide",
    )
    arguments = parseur.parse_args(argv)

    client = Client(arguments.base, arguments.jeton)
    try:
        if arguments.verifier_seulement:
            if not (arguments.estimation and arguments.version):
                print("--verifier-seulement exige --estimation et --version", file=sys.stderr)
                return 2
            constat = verifier(
                client, arguments.estimation, arguments.version, exiger_tva=arguments.exiger_tva
            )
        else:
            etapes = construire(client, suffixe=arguments.suffixe)
            constat = verifier(
                client, etapes["estimate_id"], etapes["version_id"], exiger_tva=arguments.exiger_tva
            )
            constat["price_book_version_id"] = etapes["price_book_version_id"]
            constat["project_id"] = etapes["project_id"]
    except EchecParcours as exc:
        print(f"parcours en échec : {exc}", file=sys.stderr)
        return 1

    print(json.dumps(constat, indent=2, ensure_ascii=False))
    if arguments.sortie:
        from pathlib import Path

        Path(arguments.sortie).parent.mkdir(parents=True, exist_ok=True)
        Path(arguments.sortie).write_text(
            json.dumps(constat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
