"""Une cotation de plan n'a ni page ni plage de caractères.

Le socle documentaire a été conçu pour du texte. Une citation y porte une
page, une plage de caractères et une boîte englobante, toutes obligatoires.
C'est exactement ce dont un plan de dessin ne dispose pas : une cotation de
DXF est désignée par sa **feuille**, son **calque** et son **handle
d'objet** — trois colonnes qui existent déjà, et qui sont nullables.

**La voie refusée, et pourquoi.** L'autre solution était d'écrire `page = 1`
et une plage de caractères factice pour chaque cotation de plan. Elle ne
coûtait aucune migration. Elle écrirait en base une provenance **fausse**,
indiscernable après coup d'une vraie citation de texte : plus rien ne
distinguerait un plan d'un document dont on aurait perdu le texte. Et elle
rendrait inécrivable le seul test qui vérifie qu'une citation est
résoluble — rouvrir la citation et retrouver ce qui est cité —, parce qu'il
n'y a rien à l'adresse « page 1, caractères 0 à 1 » d'un fichier de dessin.

**Ce qui reste interdit.** La nullabilité ouvre la porte à la citation ancrée
sur RIEN, qui serait pire que les deux autres : une provenance vide qui passe
pour une provenance. `ck_source_citation_ancrage` l'exige — page ET plage, ou
un handle d'objet. Les deux ancrages peuvent coexister : un plan PDF a une
page rendue et des objets vectoriels.

**Les trois nouvelles contraintes sont écrites uniquement en IS NULL /
IS NOT NULL.** Ce n'est pas un style, c'est la condition pour qu'elles
mordent : une contrainte CHECK est satisfaite quand son expression vaut NULL,
sur SQLite comme sur PostgreSQL. Une seule comparaison arithmétique dans l'une
de ces conditions la rendrait nulle, donc satisfaite, sur la valeur même
qu'elle vise.

**Les quatre contraintes de valeur existantes ne sont pas touchées.** Pour la
même raison : `page >= 1` laisse désormais passer une page absente, et
continue de refuser `page = 0`. La tolérance au NULL ouvrait cependant un
trou — la boîte englobante à moitié écrite, `x0` posé et `x1` absent, qui
rendait NULL et passait —, et `ck_source_citation_bbox_complete` le ferme.

**Trois contraintes ne voulaient pas dire ce qu'elles disaient.** Découvert en
analysant un plan réel, qui a été refusé par la base : `Amount` stocke un
décimal en TEXTE sur SQLite — c'est écrit dans sa docstring, SQLite n'ayant pas
de type décimal — et une comparaison entre une colonne de texte et un littéral
entier se fait donc sur les CHAÎNES. Mesuré :

    confidence VARCHAR(64) CHECK (confidence <= 1)
      '0.9000000000'   accepté       ('0' < '1' en tête de chaîne)
      '1.0000000000'   REFUSÉ        ('1.0000000000' > '1', plus longue)
      '1'              accepté

Le défaut est étroit et c'est ce qui l'a caché : `Amount` quantise toujours à
dix décimales, donc deux valeurs comparées entre elles ont la même longueur et
l'ordre lexicographique coïncide avec l'ordre numérique. Seule la comparaison
au littéral `0` ou `1` trahit — et une confiance de 1 est exactement ce qu'une
citation CAO porte, puisqu'un handle désigne UN objet sans ambiguïté. Une boîte
englobante touchant le bord droit du dessin (`x1 = 1`) tombait de la même
façon.

Pire : PostgreSQL, lui, l'accepte. La contrainte était donc **divergente entre
les deux moteurs**, ce que le type `Amount` existe précisément pour empêcher.

Les trois conditions concernées — `ck_source_citation_bbox`,
`ck_source_citation_confidence` et `ck_extraction_proposal_confidence`, les
seules du schéma qui comparent une colonne `Amount` à un littéral entier —
sont réécrites avec un `CAST(... AS NUMERIC)` explicite. Vérifié : avec le
`CAST`, `1.0000000000` passe, et `1.0000000001`, `-0.1` et `2` sont refusés.

**Quatre étapes de pipeline pour un plan.** `page_render`,
`vector_geometry`, `cad_read` et `measurement`. Aucune des onze étapes
déclarées ne leur convenait, et une liste `IN` n'est pas extensible : il faut
la refaire. C'est le seul endroit du dépôt où une contrainte CHECK existante
est modifiée dans un `upgrade`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

import metreo_api.db

revision: str = "d8e9fa010203"
down_revision: str | None = "c7d8e9fa0102"
branch_labels: str | None = None
depends_on: str | None = None


#: Les quinze étapes, dans l'ordre du pipeline. Ce libellé doit rester
#: identique, caractère pour caractère, à celui de `models.py` et à
#: `DOCUMENT_PIPELINE_STEPS`.
ETAPES: tuple[str, ...] = (
    "receive_security",
    "detection",
    "native_text",
    "ocr",
    "tables",
    "segmentation",
    "classification",
    "structured_extraction",
    "indexing",
    "consistency",
    "human_review",
    "page_render",
    "vector_geometry",
    "cad_read",
    "measurement",
)

#: Les quatre qu'un plan ajoute. Le `downgrade` refuse de les effacer si une
#: seule a réellement tourné.
ETAPES_DE_PLAN: tuple[str, ...] = ("page_render", "vector_geometry", "cad_read", "measurement")

#: Les trois contraintes posées sur `source_citations`, et le contrôle
#: préalable les relit telles quelles : elles ne valent jamais NULL, donc
#: `WHERE NOT (condition)` est sûr.
CONTRAINTES_CITATION: tuple[tuple[str, str], ...] = (
    (
        "ck_source_citation_ancrage",
        "((char_start IS NULL AND char_end IS NULL) "
        "OR (char_start IS NOT NULL AND char_end IS NOT NULL)) "
        "AND ((page IS NOT NULL AND char_start IS NOT NULL) OR object_id IS NOT NULL)",
    ),
    (
        "ck_source_citation_bbox_complete",
        "(x0 IS NULL AND y0 IS NULL AND x1 IS NULL AND y1 IS NULL) "
        "OR (x0 IS NOT NULL AND y0 IS NOT NULL AND x1 IS NOT NULL AND y1 IS NOT NULL)",
    ),
    (
        "ck_source_citation_reperes_cao_nonempty",
        "(sheet IS NULL OR length(trim(sheet)) > 0) "
        "AND (layer IS NULL OR length(trim(layer)) > 0) "
        "AND (object_id IS NULL OR length(trim(object_id)) > 0)",
    ),
)

#: Les conditions qui comparaient un décimal à un littéral entier, avant et
#: après. Le `CAST` est ce qui les rend vraies sur les deux moteurs.
CONDITION_BBOX_AVANT = (
    "x0 >= 0 AND x0 <= 1 AND y0 >= 0 AND y0 <= 1 AND "
    "x1 >= 0 AND x1 <= 1 AND y1 >= 0 AND y1 <= 1 AND "
    "x0 < x1 AND y0 < y1"
)
CONDITION_BBOX_APRES = (
    "CAST(x0 AS NUMERIC) >= 0 AND CAST(x0 AS NUMERIC) <= 1 AND "
    "CAST(y0 AS NUMERIC) >= 0 AND CAST(y0 AS NUMERIC) <= 1 AND "
    "CAST(x1 AS NUMERIC) >= 0 AND CAST(x1 AS NUMERIC) <= 1 AND "
    "CAST(y1 AS NUMERIC) >= 0 AND CAST(y1 AS NUMERIC) <= 1 AND "
    "CAST(x0 AS NUMERIC) < CAST(x1 AS NUMERIC) AND "
    "CAST(y0 AS NUMERIC) < CAST(y1 AS NUMERIC)"
)
CONDITION_CONFIANCE_AVANT = "confidence >= 0 AND confidence <= 1"
CONDITION_CONFIANCE_APRES = (
    "CAST(confidence AS NUMERIC) >= 0 AND CAST(confidence AS NUMERIC) <= 1"
)

#: Les sept colonnes qui deviennent nullables, avec leur type explicite.
#: `alter_column` sans `existing_type` est refusé par Alembic.
COLONNES_DE_TEXTE: tuple[tuple[str, object], ...] = (
    ("page", sa.Integer()),
    ("char_start", sa.Integer()),
    ("char_end", sa.Integer()),
    ("x0", metreo_api.db.Amount(precision=28, scale=10)),
    ("y0", metreo_api.db.Amount(precision=28, scale=10)),
    ("x1", metreo_api.db.Amount(precision=28, scale=10)),
    ("y1", metreo_api.db.Amount(precision=28, scale=10)),
)


def _condition_des_etapes(etapes: tuple[str, ...]) -> str:
    return "step IN ('" + "','".join(etapes) + "')"


def _table_source_citations(*, nullable: bool, nouvelles_contraintes: bool) -> sa.Table:
    """La table telle qu'elle est À CET INSTANT de la migration.

    Décrite ici plutôt que réfléchie : SQLite ne modifie pas une contrainte,
    Alembic refait donc la table à partir de cette définition. Les index en
    font partie — une définition qui les tait les EFFACE, ce qui a été mesuré
    sur la révision 20260831_0002 : `alembic check` réclamait ensuite leur
    recréation.

    `nullable` et `nouvelles_contraintes` décrivent l'état AVANT l'opération
    du lot, pas après : le `downgrade` doit décrire la table telle que
    l'`upgrade` l'a laissée.
    """
    metadonnees = sa.MetaData()
    contraintes: list[sa.SchemaItem] = [
        sa.CheckConstraint("page >= 1", name="ck_source_citation_page"),
        sa.CheckConstraint("char_start >= 0", name="ck_source_citation_char_start"),
        sa.CheckConstraint("char_end > char_start", name="ck_source_citation_char_range"),
        sa.CheckConstraint(
            CONDITION_BBOX_APRES if nouvelles_contraintes else CONDITION_BBOX_AVANT,
            name="ck_source_citation_bbox",
        ),
        sa.CheckConstraint(
            CONDITION_CONFIANCE_APRES if nouvelles_contraintes else CONDITION_CONFIANCE_AVANT,
            name="ck_source_citation_confidence",
        ),
        sa.CheckConstraint(
            "length(trim(extractor)) > 0",
            name="ck_source_citation_extractor_nonempty",
        ),
    ]
    if nouvelles_contraintes:
        contraintes += [sa.CheckConstraint(c, name=n) for n, c in CONTRAINTES_CITATION]
    return sa.Table(
        "source_citations",
        metadonnees,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("page", sa.Integer(), nullable=nullable),
        sa.Column("char_start", sa.Integer(), nullable=nullable),
        sa.Column("char_end", sa.Integer(), nullable=nullable),
        sa.Column("x0", metreo_api.db.Amount(precision=28, scale=10), nullable=nullable),
        sa.Column("y0", metreo_api.db.Amount(precision=28, scale=10), nullable=nullable),
        sa.Column("x1", metreo_api.db.Amount(precision=28, scale=10), nullable=nullable),
        sa.Column("y1", metreo_api.db.Amount(precision=28, scale=10), nullable=nullable),
        sa.Column("sheet", sa.String(length=120), nullable=True),
        sa.Column("layer", sa.String(length=120), nullable=True),
        sa.Column("object_id", sa.String(length=120), nullable=True),
        sa.Column("extractor", sa.String(length=120), nullable=False),
        sa.Column("confidence", metreo_api.db.Amount(precision=28, scale=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *contraintes,
        sa.ForeignKeyConstraint(
            ["organization_id", "revision_id"],
            ["document_revisions.organization_id", "document_revisions.id"],
            name="fk_source_citations_org_revision",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "revision_id",
            "id",
            name="uq_source_citation_org_revision_id",
        ),
        sa.Index("ix_source_citations_organization_id", "organization_id"),
        sa.Index("ix_source_citations_org_revision", "organization_id", "revision_id"),
    )


def _table_extraction_proposals(*, cast: bool) -> sa.Table:
    """La table des propositions, recopiée pour sa seule contrainte de confiance."""
    metadonnees = sa.MetaData()
    return sa.Table(
        "extraction_proposals",
        metadonnees,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("citation_id", sa.String(length=36), nullable=False),
        sa.Column("schema_name", sa.String(length=120), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("confidence", metreo_api.db.Amount(precision=28, scale=10), nullable=False),
        sa.Column("pipeline_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            CONDITION_CONFIANCE_APRES if cast else CONDITION_CONFIANCE_AVANT,
            name="ck_extraction_proposal_confidence",
        ),
        sa.CheckConstraint(
            "status IN ('proposed','accepted','corrected','rejected')",
            name="ck_extraction_proposal_status",
        ),
        sa.CheckConstraint(
            "length(trim(schema_name)) > 0 AND length(trim(schema_version)) > 0 "
            "AND length(trim(pipeline_version)) > 0 "
            "AND length(trim(prompt_version)) > 0 "
            "AND length(trim(model_version)) > 0",
            name="ck_extraction_proposal_versions_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "revision_id", "citation_id"],
            [
                "source_citations.organization_id",
                "source_citations.revision_id",
                "source_citations.id",
            ],
            name="fk_extraction_proposals_org_revision_citation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "id", name="uq_extraction_proposal_org_id"),
        sa.Index("ix_extraction_proposals_organization_id", "organization_id"),
        sa.Index("ix_extraction_proposals_org_revision", "organization_id", "revision_id"),
    )


def _table_document_step_runs(etapes: tuple[str, ...]) -> sa.Table:
    """Idem, et `ck_document_step_run_step` DOIT y figurer nommée.

    `batch_op.drop_constraint(nom, type_="check")` ne trouve que ce que la
    définition déclare : une contrainte absente d'ici est introuvable là.
    """
    metadonnees = sa.MetaData()
    return sa.Table(
        "document_step_runs",
        metadonnees,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("revision_id", sa.String(length=36), nullable=False),
        sa.Column("step", sa.String(length=40), nullable=False),
        sa.Column("pipeline_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("model_version", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_summary", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(_condition_des_etapes(etapes), name="ck_document_step_run_step"),
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded','failed')",
            name="ck_document_step_run_status",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_document_step_run_attempt"),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_document_step_run_duration",
        ),
        sa.CheckConstraint(
            "status != 'failed' OR (error_code IS NOT NULL AND length(trim(error_code)) > 0)",
            name="ck_document_step_run_failure_code",
        ),
        sa.CheckConstraint(
            "length(trim(pipeline_version)) > 0 AND length(trim(prompt_version)) > 0 "
            "AND length(trim(model_version)) > 0",
            name="ck_document_step_run_versions_nonempty",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "revision_id"],
            ["document_revisions.organization_id", "document_revisions.id"],
            name="fk_document_step_runs_org_revision",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "revision_id",
            "step",
            "pipeline_version",
            "prompt_version",
            "model_version",
            name="uq_document_step_run_idempotence",
        ),
        sa.Index("ix_document_step_runs_organization_id", "organization_id"),
        sa.Index("ix_document_step_runs_org_revision", "organization_id", "revision_id"),
    )


def _lignes_qui_satisfont(condition: str, table: str) -> list[str]:
    """Les identifiants des lignes où `condition` est VRAIE."""
    lien = op.get_bind()
    rangees = lien.execute(
        sa.text(f"SELECT id FROM {table} WHERE {condition}")  # noqa: S608
    ).scalars()
    return [str(identifiant) for identifiant in rangees]


def _lignes_qui_violent(condition: str, table: str) -> list[str]:
    """Les identifiants des lignes où `condition` est FAUSSE.

    Sûr malgré les NULL parce que les conditions passées ici sont bâties en
    IS NULL / IS NOT NULL et ne valent donc jamais NULL.
    """
    return _lignes_qui_satisfont(f"NOT ({condition})", table)


def _nommer(identifiants: list[str]) -> str:
    vus = ", ".join(identifiants[:10])
    reste = f" (et {len(identifiants) - 10} autres)" if len(identifiants) > 10 else ""
    return f"{vus}{reste}"


def upgrade() -> None:
    # 1. Contrôle préalable. En pratique aucune ligne existante ne peut violer
    #    ces conditions, les sept colonnes étant encore NOT NULL ; le contrôle
    #    garde contre une base qui aurait vécu autrement.
    fautives: list[str] = []
    for nom, condition in CONTRAINTES_CITATION:
        identifiants = _lignes_qui_violent(condition, "source_citations")
        if identifiants:
            fautives.append(f"{nom} : {_nommer(identifiants)}")
    if fautives:
        raise RuntimeError(
            "Des citations existantes violent les contraintes que cette "
            "migration installe :\n  " + "\n  ".join(fautives) + "\n"
            "Corriger ces lignes avant de rejouer la migration. Remplacer une "
            "provenance revient à un humain, pas à une migration : une adresse "
            "choisie par le code serait indiscernable d'une adresse constatée."
        )

    # 2. `recreate="auto"` et non `"always"` : sous PostgreSQL une
    #    reconstruction détruirait `source_citations`, que
    #    `extraction_proposals` référence en CASCADE par une clé composite.
    #    Sous SQLite, un `alter_column` suffit déjà à déclencher la
    #    reconstruction, et le corps du lot n'est pas vide — le no-op
    #    silencieux mesuré sur la révision 20260831_0004 n'est pas en cause.
    with op.batch_alter_table(
        "source_citations",
        copy_from=_table_source_citations(nullable=False, nouvelles_contraintes=False),
        recreate="auto",
    ) as lot:
        for colonne, type_existant in COLONNES_DE_TEXTE:
            lot.alter_column(colonne, existing_type=type_existant, nullable=True)
        for nom, condition in CONTRAINTES_CITATION:
            lot.create_check_constraint(nom, condition)
        # Les deux conditions qui comparaient un texte à un entier. Voir
        # l'en-tête : sans le `CAST`, une confiance de 1 et une boîte touchant
        # le bord du dessin sont refusées sur SQLite et acceptées sur
        # PostgreSQL.
        lot.drop_constraint("ck_source_citation_bbox", type_="check")
        lot.create_check_constraint("ck_source_citation_bbox", CONDITION_BBOX_APRES)
        lot.drop_constraint("ck_source_citation_confidence", type_="check")
        lot.create_check_constraint("ck_source_citation_confidence", CONDITION_CONFIANCE_APRES)

    with op.batch_alter_table(
        "extraction_proposals",
        copy_from=_table_extraction_proposals(cast=False),
        recreate="auto",
    ) as lot:
        lot.drop_constraint("ck_extraction_proposal_confidence", type_="check")
        lot.create_check_constraint(
            "ck_extraction_proposal_confidence", CONDITION_CONFIANCE_APRES
        )

    # 3. Une liste `IN` ne se complète pas : on la refait.
    with op.batch_alter_table(
        "document_step_runs",
        copy_from=_table_document_step_runs(ETAPES[:11]),
        recreate="auto",
    ) as lot:
        lot.drop_constraint("ck_document_step_run_step", type_="check")
        lot.create_check_constraint("ck_document_step_run_step", _condition_des_etapes(ETAPES))


def downgrade() -> None:
    """Le retour en arrière REFUSE plutôt qu'il n'invente, deux fois.

    Une migration ne choisit jamais une valeur métier — le dépôt l'interdit
    et un test le vérifie. Les deux refus ci-dessous en découlent : il n'y a
    aucune valeur honnête à écrire à la place de ce qui serait effacé.
    """
    etapes_jouees = _lignes_qui_satisfont(
        _condition_des_etapes(ETAPES_DE_PLAN), "document_step_runs"
    )
    if etapes_jouees:
        raise RuntimeError(
            "Des étapes de lecture de plan ont réellement tourné :\n  "
            + _nommer(etapes_jouees)
            + "\nRevenir à onze étapes effacerait leur état d'exécution, donc "
            "la trace de ce qui a été lu et de ce qui a échoué. Supprimer ces "
            "lignes est une décision humaine."
        )

    citations_cao = _lignes_qui_satisfont(
        "page IS NULL OR char_start IS NULL OR char_end IS NULL OR x0 IS NULL",
        "source_citations",
    )
    if citations_cao:
        raise RuntimeError(
            "Des citations sont ancrées sur un objet de plan, sans page ni "
            "plage de caractères :\n  " + _nommer(citations_cao) + "\n"
            "Reposer NOT NULL demanderait d'inventer une page et une plage "
            "pour chacune. Une migration ne choisit pas cela à la place d'un "
            "humain : la provenance inventée serait indiscernable d'une "
            "provenance constatée."
        )

    with op.batch_alter_table(
        "document_step_runs",
        copy_from=_table_document_step_runs(ETAPES),
        recreate="auto",
    ) as lot:
        lot.drop_constraint("ck_document_step_run_step", type_="check")
        lot.create_check_constraint(
            "ck_document_step_run_step", _condition_des_etapes(ETAPES[:11])
        )

    with op.batch_alter_table(
        "extraction_proposals",
        copy_from=_table_extraction_proposals(cast=True),
        recreate="auto",
    ) as lot:
        lot.drop_constraint("ck_extraction_proposal_confidence", type_="check")
        lot.create_check_constraint(
            "ck_extraction_proposal_confidence", CONDITION_CONFIANCE_AVANT
        )

    with op.batch_alter_table(
        "source_citations",
        copy_from=_table_source_citations(nullable=True, nouvelles_contraintes=True),
        recreate="auto",
    ) as lot:
        lot.drop_constraint("ck_source_citation_confidence", type_="check")
        lot.create_check_constraint("ck_source_citation_confidence", CONDITION_CONFIANCE_AVANT)
        lot.drop_constraint("ck_source_citation_bbox", type_="check")
        lot.create_check_constraint("ck_source_citation_bbox", CONDITION_BBOX_AVANT)
        for nom, _ in reversed(CONTRAINTES_CITATION):
            lot.drop_constraint(nom, type_="check")
        for colonne, type_existant in COLONNES_DE_TEXTE:
            lot.alter_column(colonne, existing_type=type_existant, nullable=False)
