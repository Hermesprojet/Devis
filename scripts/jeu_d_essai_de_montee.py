#!/usr/bin/env python3
"""Un chantier complet, créé SOUS L'ANCIENNE VERSION, puis relu après la montée.

    python3 scripts/jeu_d_essai_de_montee.py creer   --url <URL>
    python3 scripts/jeu_d_essai_de_montee.py relever --url <URL>
    python3 scripts/jeu_d_essai_de_montee.py verifier --url <URL>

**Ce que ce module ajoute à `epreuve_montee_depuis_preproduction.py`.**

Cette épreuve-là monte une base VIDE au schéma de la préproduction, puis y
applique les migrations du candidat. Elle prouve que le chemin existe. Elle ne
prouve rien de ce qui fait le prix d'un déploiement : qu'un devis gelé la
veille porte encore le même total le lendemain.

Une migration peut parfaitement s'appliquer sans erreur et perdre quelque
chose : un `ALTER` qui retype une colonne et tronque, une contrainte qui vide
une clé étrangère, un `server_default` qui réécrit une ligne, un déclencheur
qui refuse une écriture ultérieure. Sur une base vide, aucun de ces quatre
accidents ne se voit.

**Pourquoi trois sous-commandes, et pourquoi elles ne s'exécutent pas au même
endroit.**

- `creer` s'exécute avec les fichiers de l'ANCIENNE version — l'appelant pose
  son `PYTHONPATH` sur l'arbre de la préproduction. C'est ce qui rend les
  données « créées sous l'ancienne version » et non « écrites par le candidat
  dans un vieux schéma ». La différence n'est pas cosmétique : l'empreinte d'un
  devis gelé et les maillons de la chaîne d'audit sont calculés par le code qui
  écrit. Les faire calculer par le candidat rendrait la vérification
  tautologique.
- `relever` n'importe RIEN de `metreo_api` : il lit en SQL brut. C'est ce qui
  permet de le lancer avant ET après la montée et de comparer deux relevés. Un
  relevé passé par l'ORM comparerait ce que chaque version sait lire, pas ce
  que la base porte — et une colonne perdue par la migration serait invisible
  des deux côtés.
- `verifier` s'exécute avec les fichiers du CANDIDAT, parce que les questions
  posées sont exactement celles-là : le code neuf reconnaît-il encore la chaîne
  d'audit écrite par l'ancien, rend-il le même total pour un devis déjà gelé,
  et la chaîne se continue-t-elle ?

**Ce jeu d'essai est fictif.** L'entreprise, la rue, le client et les prix sont
inventés, comme ceux de `metreo_api.seed`. Aucun chiffre n'est un prix de
marché, et le taux de TVA porté ici est celui du jeu de démonstration, avec la
même réserve : il est à confirmer par un pack régional validé. Rien de ce
module ne doit tourner ailleurs que sur une base jetable.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

#: Le nom de l'organisation du jeu d'essai. Il sert de point d'entrée au relevé
#: et à toute vérification : on ne compte pas « les organisations », on retrouve
#: CELLE-CI. Une base de travail qui en porterait d'autres reste lisible.
ORGANISATION = "Entreprise d'essai de montée (fictive)"

#: L'administrateur du jeu. Une adresse en `.invalid` : le TLD réservé par la
#: RFC 2606, qui ne peut pas être enregistré et n'atteindra donc jamais
#: personne.
ADMINISTRATEUR = "admin@essai-de-montee.invalid"

#: Les trois lignes du bordereau. Des quantités à décimales et des prix à
#: quatre décimales : c'est là qu'une migration maladroite se voit, et pas sur
#: des entiers ronds.
LIGNES: tuple[dict[str, str], ...] = (
    {
        "position": "01.10",
        "designation": "Déblai en terrain meuble",
        "unite": "m3",
        "quantite": "1250.5",
        "code": "TER-DEB",
        "prix": "18.4567",
    },
    {
        "position": "01.20",
        "designation": "Remblai compacté",
        "unite": "m3",
        "quantite": "870.25",
        "code": "TER-REM",
        "prix": "22.9133",
    },
    {
        "position": "02.10",
        "designation": "Béton de propreté",
        "unite": "m3",
        "quantite": "45.75",
        "code": "BET-PRO",
        "prix": "142.8891",
    },
)

#: Les tables relevées, et les colonnes de chacune.
#:
#: Toutes existent AVANT comme APRÈS la montée : le relevé doit pouvoir se
#: faire des deux côtés avec la même requête, sans quoi il ne compare rien.
#: Les colonnes ajoutées par le candidat sont volontairement absentes — ce
#: qu'on vérifie est la CONSERVATION, pas l'arrivée.
#:
#: `ORDRE` est imposé parce qu'un `SELECT` sans `ORDER BY` n'a pas d'ordre
#: garanti, et que deux relevés dans deux ordres différents se compareraient
#: faux.
RELEVE: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "organizations",
        ("id", "name", "legal_name", "company_number", "region_code", "currency"),
        "id",
    ),
    ("users", ("id", "email", "full_name", "locale"), "email"),
    ("memberships", ("id", "user_id", "organization_id", "role", "is_active"), "id"),
    (
        "clients",
        ("id", "organization_id", "name", "company_number", "billing_address", "city", "status"),
        "id",
    ),
    (
        "projects",
        ("id", "organization_id", "reference", "name", "client_id", "region_code", "status"),
        "reference",
    ),
    ("bills_of_quantities", ("id", "organization_id", "project_id", "name", "revision"), "id"),
    (
        "boq_items",
        (
            "id",
            "organization_id",
            "boq_id",
            "position",
            "designation",
            "unit_code",
            "quantity",
            "status",
            "price_item_id",
        ),
        "position",
    ),
    (
        "price_items",
        ("id", "organization_id", "code", "label", "unit_code", "unit_price", "currency"),
        "code",
    ),
    (
        "estimates",
        ("id", "organization_id", "project_id", "boq_id", "price_book_version_id", "name"),
        "id",
    ),
    (
        "estimate_versions",
        (
            "id",
            "organization_id",
            "estimate_id",
            "version_number",
            "status",
            "snapshot_sha256",
            "total_selling_price_ht",
            "total_ttc",
            "document_total_ht",
            "document_total_ttc",
        ),
        "version_number",
    ),
    (
        "login_transactions",
        ("id", "state", "redirect_uri", "return_to", "user_id", "organization_id"),
        "state",
    ),
    (
        "audit_events",
        (
            "id",
            "organization_id",
            "sequence",
            "action",
            "object_type",
            "object_id",
            "previous_hash",
            "hash",
        ),
        "sequence",
    ),
)


# ---------------------------------------------------------------------------
#  creer — avec le code de l'ANCIENNE version
# ---------------------------------------------------------------------------


def creer(url: str) -> int:
    """Écrit le chantier d'essai. À lancer avec le `PYTHONPATH` de l'ancien arbre.

    Rien ici n'est importé au chargement du module : l'import se fait dans la
    fonction, parce que `relever` doit pouvoir tourner sans `metreo_api` du
    tout — c'est tout l'intérêt du relevé en SQL brut.
    """
    import os

    os.environ["METREO_DATABASE_URL"] = url

    from metreo_api.db import get_session_factory, reset_engine
    from metreo_api.models import (
        BillOfQuantities,
        BoqItem,
        Client,
        Estimate,
        EstimateVersion,
        LoginTransaction,
        Membership,
        Organization,
        OrganizationSettings,
        PriceBook,
        PriceBookVersion,
        PriceItem,
        Project,
        TaxRateRow,
        User,
    )
    from metreo_api.services import audit, estimating

    reset_engine()
    with get_session_factory()() as session:
        organisation = Organization(
            name=ORGANISATION,
            legal_name="Entreprise d'essai de montée SRL",
            company_number="BE0123.456.749",
            address="Rue de l'Épreuve 1",
            postal_code="5000",
            city="Namur",
            country_code="BE",
            region_code="BE-WAL",
            locale="fr-BE",
            currency="EUR",
            email="contact@essai-de-montee.invalid",
            phone="+32 81 00 00 00",
            website="https://essai-de-montee.invalid",
        )
        session.add(organisation)
        session.flush()

        session.add(
            OrganizationSettings(
                organization_id=organisation.id,
                rounding_scale=2,
                rounding_mode="half_up",
                unit_price_scale=2,
                site_overheads_rate=Decimal("0.06"),
                site_overheads_base="direct_cost",
                general_overheads_rate=Decimal("0.09"),
                general_overheads_base="direct_plus_site",
                contingency_rate=Decimal("0.03"),
                contingency_base="running_total",
                margin_rate=Decimal("0.12"),
                margin_method="on_cost",
                missing_price_policy="block",
            )
        )
        # Le taux est celui du jeu de démonstration, avec sa réserve : ce module
        # n'énonce aucune règle fiscale, il rejoue un cas de figure.
        session.add(
            TaxRateRow(
                organization_id=organisation.id,
                code="VAT-BE-21",
                label="TVA 21 %",
                rate=Decimal("0.21"),
                applies_from=date(1996, 1, 1),
                is_default=True,
                source="Taux à confirmer par le pack régional validé",
            )
        )

        administrateur = User(
            email=ADMINISTRATEUR,
            full_name="Administratrice d'essai",
            locale="fr-BE",
        )
        session.add(administrateur)
        session.flush()
        session.add(
            Membership(
                user_id=administrateur.id,
                organization_id=organisation.id,
                role="org_admin",
                is_active=True,
            )
        )

        client = Client(
            organization_id=organisation.id,
            name="Commune d'Essai",
            company_number="BE0987.654.321",
            billing_address="Place de la Montée 3",
            postal_code="5000",
            city="Namur",
            country_code="BE",
            contact_name="Service des travaux",
            email="travaux@essai-de-montee.invalid",
            status="active",
            created_by=administrateur.id,
        )
        session.add(client)
        session.flush()

        projet = Project(
            organization_id=organisation.id,
            reference="MONTEE-001",
            name="Chantier d'essai de montée",
            client_id=client.id,
            client_name=client.name,
            address="Chaussée de l'Épreuve 12",
            postal_code="5000",
            city="Namur",
            country_code="BE",
            region_code="BE-WAL",
            currency="EUR",
            locale="fr-BE",
            status="studying",
            created_by=administrateur.id,
        )
        session.add(projet)
        session.flush()

        livre = PriceBook(
            organization_id=organisation.id,
            name="Bibliothèque d'essai",
            currency="EUR",
            is_default=True,
        )
        session.add(livre)
        session.flush()
        version_du_livre = PriceBookVersion(
            organization_id=organisation.id,
            price_book_id=livre.id,
            version_number=1,
            label="Essai 2026.1",
            status="published",
            created_by=administrateur.id,
        )
        session.add(version_du_livre)
        session.flush()

        bordereau = BillOfQuantities(
            organization_id=organisation.id,
            project_id=projet.id,
            name="Métré d'essai",
            source="manual",
            revision=1,
        )
        session.add(bordereau)
        session.flush()

        for ligne in LIGNES:
            prix = PriceItem(
                organization_id=organisation.id,
                price_book_version_id=version_du_livre.id,
                code=ligne["code"],
                label=ligne["designation"],
                resource_kind="material",
                unit_code=ligne["unite"],
                unit_price=Decimal(ligne["prix"]),
                currency="EUR",
                source="Jeu d'essai fictif — ne pas utiliser comme prix de marché",
                is_demo_data=True,
            )
            session.add(prix)
            session.flush()
            session.add(
                BoqItem(
                    organization_id=organisation.id,
                    boq_id=bordereau.id,
                    kind="item",
                    position=ligne["position"],
                    designation=ligne["designation"],
                    unit_code=ligne["unite"],
                    quantity=Decimal(ligne["quantite"]),
                    status="approved",
                    price_item_id=prix.id,
                )
            )
        session.flush()

        etude = Estimate(
            organization_id=organisation.id,
            project_id=projet.id,
            boq_id=bordereau.id,
            price_book_version_id=version_du_livre.id,
            name="Étude d'essai",
            currency="EUR",
            created_by=administrateur.id,
        )
        session.add(etude)
        session.flush()
        version = EstimateVersion(
            organization_id=organisation.id,
            estimate_id=etude.id,
            version_number=1,
            label="Version gelée avant la montée",
            status="draft",
            price_book_version_id=version_du_livre.id,
            created_by=administrateur.id,
        )
        session.add(version)
        session.flush()

        # Le gel : c'est lui qui écrit l'instantané et son empreinte. C'est le
        # seul objet du jeu dont la valeur est CALCULÉE par l'ancien code, donc
        # le seul qui prouve quelque chose si elle survit.
        version, resultat = estimating.freeze_version(
            session,
            estimate=etude,
            version=version,
            actor_user_id=administrateur.id,
            label="Version gelée avant la montée",
        )

        # Une demande de connexion EN COURS au moment de la montée.
        #
        # Elle n'est pas décorative : `login_transactions` est la **seule** table
        # déjà peuplée à laquelle le candidat ajoute une colonne NOT NULL
        # (`reauthentication_requested`, révision `d1e2f3a40506`). Cette colonne
        # porte un `server_default`, et c'est précisément ce que cette ligne
        # éprouve : sans le défaut, la montée échouerait dès qu'une connexion est
        # ouverte — c'est-à-dire exactement le jour d'un déploiement en journée.
        # Sur une table vide, l'oubli ne se voit pas.
        session.add(
            LoginTransaction(
                state="essai-de-montee-etat-0123456789",
                nonce="essai-de-montee-nonce-0123456789",
                code_verifier="essai-de-montee-verificateur-0123456789",
                redirect_uri="https://essai-de-montee.invalid/auth/retour",
                return_to="/projets",
                expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=10),
                user_id=administrateur.id,
                organization_id=organisation.id,
            )
        )
        session.flush()

        # Trois maillons de chaîne d'audit, écrits par l'ANCIEN code. Ce sont
        # eux que le candidat devra encore reconnaître.
        for action, objet, identifiant, resume in (
            ("client.created", "client", client.id, "Fiche client créée"),
            ("project.created", "project", projet.id, "Chantier créé"),
            (
                "estimate_version.frozen",
                "estimate_version",
                version.id,
                "Version de devis gelée",
            ),
        ):
            audit.record(
                session,
                organization_id=organisation.id,
                action=action,
                object_type=objet,
                object_id=identifiant,
                summary=resume,
                actor_user_id=administrateur.id,
                actor_email=administrateur.email,
            )

        session.commit()

        print(f"organisation      {organisation.id}")
        print(f"administrateur    {administrateur.id}")
        print(f"client            {client.id}")
        print(f"chantier          {projet.reference}")
        print(f"bordereau         {len(LIGNES)} ligne(s)")
        print(f"devis gelé        total HT {version.document_total_ht}")
        print(f"                  total TTC {version.document_total_ttc}")
        print(f"                  empreinte {version.snapshot_sha256}")
        print(f"                  (moteur : {resultat.total_selling_price_ht.amount})")
    return 0


# ---------------------------------------------------------------------------
#  relever — en SQL brut, sans aucun modèle
# ---------------------------------------------------------------------------


def _texte(valeur: Any) -> Any:
    """Une valeur écrite d'une seule façon, quel que soit le moteur.

    `Decimal("10.00")` et `Decimal("10.0")` sont égaux et ne s'écrivent pas
    pareil ; SQLite rend parfois un `float` là où PostgreSQL rend un
    `Decimal` ; un `datetime` ne se sérialise pas en JSON. Le relevé normalise
    donc tout en texte, et compare du texte.

    Les décimales sont normalisées par `Decimal.normalize()` : sans cela,
    `1250.5` relevé « 1250.50 » d'un côté et « 1250.5000000000 » de l'autre
    ferait échouer la comparaison sur une différence d'écriture, et non de
    valeur. Ce qu'on vérifie est la VALEUR.
    """
    if valeur is None:
        return None
    if isinstance(valeur, bool):
        return valeur
    if isinstance(valeur, Decimal):
        return format(valeur.normalize(), "f")
    if isinstance(valeur, float):
        return format(Decimal(repr(valeur)).normalize(), "f")
    if isinstance(valeur, int):
        return valeur
    return str(valeur)


def relever(url: str) -> dict[str, Any]:
    """L'état des tables du jeu d'essai, lu en SQL brut.

    Portée à l'organisation du jeu : une base de travail qui porterait d'autres
    données reste lisible, et le relevé ne dépend pas de ce qui l'entoure.
    """
    from sqlalchemy import create_engine, text

    moteur = create_engine(url)
    empreinte: dict[str, Any] = {}
    try:
        with moteur.connect() as connexion:
            identifiant = connexion.execute(
                text("SELECT id FROM organizations WHERE name = :nom"),
                {"nom": ORGANISATION},
            ).scalar_one_or_none()
            if identifiant is None:
                raise SystemExit(
                    f"ÉCHEC — aucune organisation « {ORGANISATION} » : "
                    "le jeu d'essai n'a pas été créé sur cette base."
                )
            empreinte["organization_id"] = identifiant

            for table, colonnes, ordre in RELEVE:
                # Trois façons d'atteindre l'organisation du jeu, et trois
                # seulement : `organizations` EST l'organisation ; `users` n'en
                # porte pas, puisqu'un utilisateur peut appartenir à plusieurs,
                # et se retrouve donc par l'appartenance ; tout le reste porte
                # `organization_id`, qui est l'invariant du dépôt.
                if table == "organizations":
                    ou = "id = :org"
                elif table == "users":
                    ou = "id IN (SELECT user_id FROM memberships WHERE organization_id = :org)"
                else:
                    ou = "organization_id = :org"
                requete = f"SELECT {', '.join(colonnes)} FROM {table} WHERE {ou} ORDER BY {ordre}"
                lignes = connexion.execute(text(requete), {"org": identifiant}).mappings().all()
                empreinte[table] = [
                    {nom: _texte(ligne[nom]) for nom in colonnes} for ligne in lignes
                ]
    finally:
        moteur.dispose()
    return empreinte


# ---------------------------------------------------------------------------
#  verifier — avec le code du CANDIDAT
# ---------------------------------------------------------------------------


def verifier(url: str) -> int:
    """Ce que le code NEUF comprend des données écrites par l'ANCIEN.

    Le relevé en SQL brut prouve que les octets n'ont pas bougé. Il ne prouve
    pas qu'ils veulent encore dire quelque chose. Trois questions restent, et
    ce sont elles qui coûtent cher un jour de déploiement :

    1. **La chaîne d'audit tient-elle encore ?** Chaque maillon porte
       l'empreinte du précédent, calculée sur son contenu. Une migration qui
       aurait retypé, tronqué ou réordonné une colonne d'`audit_events` la
       romprait — et c'est le seul contrôle qui distingue « les lignes sont
       là » de « les lignes sont intactes ».
    2. **Le devis gelé vaut-il toujours le même montant ?** Son instantané est
       rejoué par le moteur du candidat. Si l'arithmétique a bougé d'un
       centime, un devis déjà remis à un client ne dit plus la même chose que
       le papier qu'il a reçu.
    3. **L'empreinte de cet instantané est-elle toujours la même ?** Elle est
       recalculée par le candidat et comparée à celle que l'ancien code avait
       écrite. C'est la question de la sérialisation canonique : deux moteurs
       qui écrivent `10.0` et `10.00` donnent deux empreintes, donc deux
       devis réputés différents pour le même document.

    Puis un quatrième geste, qui n'est pas une relecture : **écrire un nouveau
    maillon**. Une chaîne qu'on peut relire mais pas continuer serait une
    préproduction morte au premier clic.
    """
    import os

    os.environ["METREO_DATABASE_URL"] = url

    from sqlalchemy import text

    from metreo_api.db import get_session_factory, reset_engine
    from metreo_api.services import audit, estimating

    reset_engine()
    problemes: list[str] = []
    with get_session_factory()() as session:
        identifiant = session.execute(
            text("SELECT id FROM organizations WHERE name = :nom"),
            {"nom": ORGANISATION},
        ).scalar_one_or_none()
        if identifiant is None:
            print(f"ÉCHEC — aucune organisation « {ORGANISATION} »", file=sys.stderr)
            return 1
        organisation = str(identifiant)

        # 1. La chaîne d'audit.
        rapport = audit.verify_chain(session, organisation)
        if rapport.get("valid"):
            print(f"  chaîne d'audit        intègre, {rapport.get('checked', '?')} maillon(s)")
        else:
            problemes.append("la chaîne d'audit est rompue")
            print(
                "  chaîne d'audit        ROMPUE : "
                + json.dumps(rapport, ensure_ascii=False, default=str),
                file=sys.stderr,
            )

        # 2 et 3. Le devis gelé, rejoué par le moteur du candidat.
        gelees = (
            session.execute(
                text(
                    "SELECT id, snapshot, snapshot_sha256, document_total_ht, document_total_ttc "
                    "FROM estimate_versions "
                    "WHERE organization_id = :org AND status = 'frozen' "
                    "ORDER BY version_number"
                ),
                {"org": organisation},
            )
            .mappings()
            .all()
        )
        if not gelees:
            problemes.append("aucune version gelée : le jeu d'essai est incomplet")
        for version in gelees:
            instantane = version["snapshot"]
            if isinstance(instantane, str):
                instantane = json.loads(instantane)
            if not instantane:
                problemes.append(f"version {version['id']} : instantané perdu")
                continue

            rejoue = estimating.recompute_from_snapshot(instantane)
            ecrit_ht = Decimal(str(version["document_total_ht"]))
            ecrit_ttc = Decimal(str(version["document_total_ttc"]))
            arrondi = estimating.rounding_from_dict(instantane["rounding"])
            rendu = rejoue.to_dict(arrondi)
            rejoue_ht = Decimal(rendu["total_selling_price_ht"])
            rejoue_ttc = Decimal(rendu["total_ttc"])
            if rejoue_ht != ecrit_ht or rejoue_ttc != ecrit_ttc:
                problemes.append(
                    f"version {version['id']} : le moteur du candidat rend "
                    f"{rejoue_ht} / {rejoue_ttc} là où le document porte "
                    f"{ecrit_ht} / {ecrit_ttc}"
                )
            else:
                print(
                    f"  devis gelé            {_texte(ecrit_ht)} HT · "
                    f"{_texte(ecrit_ttc)} TTC, inchangés"
                )

            empreinte = estimating.snapshot_digest(instantane)
            if empreinte != version["snapshot_sha256"]:
                problemes.append(
                    f"version {version['id']} : empreinte recalculée {empreinte[:12]}… "
                    f"au lieu de {str(version['snapshot_sha256'])[:12]}…"
                )
            else:
                print(f"  empreinte             {empreinte[:16]}…, recalculée à l'identique")

        # 4. La chaîne se continue.
        avant = int(
            session.execute(
                text("SELECT COUNT(*) FROM audit_events WHERE organization_id = :org"),
                {"org": organisation},
            ).scalar_one()
        )
        audit.record(
            session,
            organization_id=organisation,
            action="migration.verified",
            object_type="organization",
            object_id=organisation,
            summary="Montée vérifiée : un maillon écrit par la version montée",
        )
        session.commit()
        suite = audit.verify_chain(session, organisation)
        apres = int(
            session.execute(
                text("SELECT COUNT(*) FROM audit_events WHERE organization_id = :org"),
                {"org": organisation},
            ).scalar_one()
        )
        if not suite.get("valid") or apres != avant + 1:
            problemes.append("la chaîne d'audit ne se continue pas après la montée")
        else:
            print(f"  chaîne continuée      {avant} → {apres} maillon(s), toujours intègre")

    if problemes:
        print("\nÉCHEC — la montée n'a pas conservé ce qu'elle devait :", file=sys.stderr)
        for probleme in problemes:
            print(f"  · {probleme}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
#  Comparaison de deux relevés
# ---------------------------------------------------------------------------


def comparer(avant: dict[str, Any], apres: dict[str, Any]) -> list[str]:
    """Les écarts entre deux relevés, en clair.

    Rend une liste vide quand rien n'a bougé. Chaque écart nomme la table, la
    ligne et la colonne : « il manque quelque chose » n'aide personne à
    dix heures du soir.
    """
    ecarts: list[str] = []
    if avant.get("organization_id") != apres.get("organization_id"):
        ecarts.append(
            "organizations : l'identifiant de l'organisation a changé "
            f"({avant.get('organization_id')} → {apres.get('organization_id')})"
        )
    for table, colonnes, _ in RELEVE:
        lignes_avant = avant.get(table, [])
        lignes_apres = apres.get(table, [])
        if len(lignes_avant) != len(lignes_apres):
            ecarts.append(
                f"{table} : {len(lignes_avant)} ligne(s) avant, {len(lignes_apres)} après"
            )
            continue
        for rang, (a, b) in enumerate(zip(lignes_avant, lignes_apres, strict=True)):
            for colonne in colonnes:
                if a.get(colonne) != b.get(colonne):
                    ecarts.append(
                        f"{table}[{rang}].{colonne} : {a.get(colonne)!r} → {b.get(colonne)!r}"
                    )
    return ecarts


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument(
        "geste",
        choices=("creer", "relever", "verifier"),
        help="creer (code ancien), relever (SQL brut), verifier (code candidat)",
    )
    analyseur.add_argument("--url", required=True, help="l'URL SQLAlchemy de la base jetable")
    analyseur.add_argument(
        "--sortie",
        default=None,
        help="pour « relever » : le fichier JSON où écrire l'empreinte (sinon la sortie standard)",
    )
    arguments = analyseur.parse_args()

    if arguments.geste == "creer":
        return creer(arguments.url)
    if arguments.geste == "verifier":
        return verifier(arguments.url)

    empreinte = relever(arguments.url)
    texte = json.dumps(empreinte, indent=2, ensure_ascii=False, sort_keys=True)
    if arguments.sortie:
        with open(arguments.sortie, "w", encoding="utf-8") as fichier:
            fichier.write(texte + "\n")
        total = sum(len(v) for v in empreinte.values() if isinstance(v, list))
        print(f"relevé écrit : {arguments.sortie} ({total} ligne(s))")
    else:
        print(texte)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
