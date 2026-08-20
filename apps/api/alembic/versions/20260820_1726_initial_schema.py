"""Initial schema — Phase 1.

Organisations and permissions, region packs, projects, price library with its
staged CSV imports, sub-details, bill of quantities, estimates with freezable
versions, and the append-only audit journal.

Revision ID: d88792b38c2d
Revises: 
Create Date: 2026-08-20 17:26:42.408810+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import metreo_api.db

revision: str = 'd88792b38c2d'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('organizations',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('legal_name', sa.String(length=200), nullable=True),
    sa.Column('company_number', sa.String(length=50), nullable=True),
    sa.Column('country_code', sa.String(length=2), nullable=False),
    sa.Column('region_code', sa.String(length=10), nullable=False),
    sa.Column('locale', sa.String(length=10), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('timezone', sa.String(length=50), nullable=False),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('region_profiles',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('country_code', sa.String(length=2), nullable=False),
    sa.Column('code', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('version', sa.String(length=20), nullable=False),
    sa.Column('effective_from', sa.Date(), nullable=True),
    sa.Column('default_locale', sa.String(length=10), nullable=False),
    sa.Column('locales', sa.JSON(), nullable=False),
    sa.Column('default_currency', sa.String(length=3), nullable=False),
    sa.Column('terminology', sa.JSON(), nullable=False),
    sa.Column('rules', sa.JSON(), nullable=False),
    sa.Column('sources', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('disclaimer', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code', 'version', name='uq_region_code_version')
    )
    with op.batch_alter_table('region_profiles', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_region_profiles_country_code'), ['country_code'], unique=False)

    op.create_table('users',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=200), nullable=False),
    sa.Column('locale', sa.String(length=10), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('audit_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('sequence', sa.Integer(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.Column('actor_user_id', sa.String(length=36), nullable=True),
    sa.Column('actor_email', sa.String(length=255), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('object_type', sa.String(length=60), nullable=False),
    sa.Column('object_id', sa.String(length=36), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('previous_hash', sa.String(length=64), nullable=True),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'sequence', name='uq_audit_org_sequence')
    )
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_events_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index('ix_audit_org_occurred', ['organization_id', 'occurred_at'], unique=False)

    op.create_table('memberships',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.String(length=40), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'organization_id', name='uq_membership_user_org')
    )
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memberships_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_memberships_user_id'), ['user_id'], unique=False)

    op.create_table('organization_settings',
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('rounding_scale', sa.Integer(), nullable=False),
    sa.Column('rounding_mode', sa.String(length=20), nullable=False),
    sa.Column('unit_price_scale', sa.Integer(), nullable=False),
    sa.Column('site_overheads_rate', metreo_api.db.Amount(precision=28, scale=10), nullable=False),
    sa.Column('site_overheads_base', sa.String(length=30), nullable=False),
    sa.Column('general_overheads_rate', metreo_api.db.Amount(precision=28, scale=10), nullable=False),
    sa.Column('general_overheads_base', sa.String(length=30), nullable=False),
    sa.Column('contingency_rate', metreo_api.db.Amount(precision=28, scale=10), nullable=False),
    sa.Column('contingency_base', sa.String(length=30), nullable=False),
    sa.Column('margin_rate', metreo_api.db.Amount(precision=28, scale=10), nullable=False),
    sa.Column('margin_method', sa.String(length=20), nullable=False),
    sa.Column('missing_price_policy', sa.String(length=10), nullable=False),
    sa.Column('quote_number_pattern', sa.String(length=60), nullable=False),
    sa.Column('show_internal_costs_in_client_pdf', sa.Boolean(), nullable=False),
    sa.Column('ai_enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('organization_id')
    )
    op.create_table('price_books',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'name', name='uq_pricebook_org_name')
    )
    with op.batch_alter_table('price_books', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_price_books_organization_id'), ['organization_id'], unique=False)

    op.create_table('projects',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('reference', sa.String(length=60), nullable=False),
    sa.Column('client_reference', sa.String(length=120), nullable=True),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('client_name', sa.String(length=200), nullable=True),
    sa.Column('address', sa.String(length=255), nullable=True),
    sa.Column('postal_code', sa.String(length=20), nullable=True),
    sa.Column('city', sa.String(length=120), nullable=True),
    sa.Column('country_code', sa.String(length=2), nullable=False),
    sa.Column('region_code', sa.String(length=10), nullable=False),
    sa.Column('latitude', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('longitude', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('market_type', sa.String(length=60), nullable=True),
    sa.Column('work_categories', sa.JSON(), nullable=False),
    sa.Column('submission_deadline', sa.DateTime(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('locale', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'reference', name='uq_project_org_reference')
    )
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.create_index('ix_projects_org_status', ['organization_id', 'status'], unique=False)
        batch_op.create_index(batch_op.f('ix_projects_organization_id'), ['organization_id'], unique=False)

    op.create_table('tax_rates',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('code', sa.String(length=30), nullable=False),
    sa.Column('label', sa.String(length=120), nullable=False),
    sa.Column('rate', metreo_api.db.Amount(precision=28, scale=10), nullable=False),
    sa.Column('applies_from', sa.Date(), nullable=True),
    sa.Column('applies_to', sa.Date(), nullable=True),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('source', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'code', 'applies_from', name='uq_tax_org_code_from')
    )
    with op.batch_alter_table('tax_rates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tax_rates_organization_id'), ['organization_id'], unique=False)

    op.create_table('bills_of_quantities',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('bills_of_quantities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_bills_of_quantities_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_bills_of_quantities_project_id'), ['project_id'], unique=False)

    op.create_table('price_book_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('price_book_id', sa.String(length=36), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=160), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('published_at', sa.DateTime(), nullable=True),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['price_book_id'], ['price_books.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('price_book_id', 'version_number', name='uq_pbv_book_number')
    )
    with op.batch_alter_table('price_book_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_price_book_versions_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_price_book_versions_price_book_id'), ['price_book_id'], unique=False)

    op.create_table('composite_prices',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('price_book_version_id', sa.String(length=36), nullable=False),
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('unit_code', sa.String(length=12), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('is_demo_data', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['price_book_version_id'], ['price_book_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('price_book_version_id', 'code', name='uq_composite_version_code')
    )
    with op.batch_alter_table('composite_prices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_composite_prices_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_composite_prices_price_book_version_id'), ['price_book_version_id'], unique=False)

    op.create_table('estimates',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('boq_id', sa.String(length=36), nullable=False),
    sa.Column('price_book_version_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['boq_id'], ['bills_of_quantities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['price_book_version_id'], ['price_book_versions.id'], ),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('estimates', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_estimates_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_estimates_project_id'), ['project_id'], unique=False)

    op.create_table('import_batches',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('price_book_version_id', sa.String(length=36), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('strategy', sa.String(length=20), nullable=False),
    sa.Column('row_count', sa.Integer(), nullable=False),
    sa.Column('valid_count', sa.Integer(), nullable=False),
    sa.Column('error_count', sa.Integer(), nullable=False),
    sa.Column('duplicate_count', sa.Integer(), nullable=False),
    sa.Column('column_mapping', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('committed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['price_book_version_id'], ['price_book_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('import_batches', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_batches_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_import_batches_price_book_version_id'), ['price_book_version_id'], unique=False)

    op.create_table('price_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('price_book_version_id', sa.String(length=36), nullable=False),
    sa.Column('code', sa.String(length=60), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('family', sa.String(length=60), nullable=True),
    sa.Column('resource_kind', sa.String(length=30), nullable=False),
    sa.Column('unit_code', sa.String(length=12), nullable=False),
    sa.Column('unit_price', metreo_api.db.Amount(precision=28, scale=10), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('supplier_name', sa.String(length=200), nullable=True),
    sa.Column('region_code', sa.String(length=10), nullable=True),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('min_quantity', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('lead_time_days', sa.Integer(), nullable=True),
    sa.Column('source', sa.String(length=255), nullable=True),
    sa.Column('conditions', sa.Text(), nullable=True),
    sa.Column('indexation', sa.String(length=120), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('confidence', sa.String(length=20), nullable=False),
    sa.Column('is_demo_data', sa.Boolean(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['price_book_version_id'], ['price_book_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('price_book_version_id', 'code', name='uq_priceitem_version_code')
    )
    with op.batch_alter_table('price_items', schema=None) as batch_op:
        batch_op.create_index('ix_price_items_org_family', ['organization_id', 'family'], unique=False)
        batch_op.create_index(batch_op.f('ix_price_items_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_price_items_price_book_version_id'), ['price_book_version_id'], unique=False)

    op.create_table('boq_items',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('boq_id', sa.String(length=36), nullable=False),
    sa.Column('parent_id', sa.String(length=36), nullable=True),
    sa.Column('sort_index', sa.Integer(), nullable=False),
    sa.Column('position', sa.String(length=40), nullable=False),
    sa.Column('code', sa.String(length=60), nullable=True),
    sa.Column('designation', sa.Text(), nullable=False),
    sa.Column('unit_code', sa.String(length=12), nullable=False),
    sa.Column('quantity', metreo_api.db.Amount(precision=28, scale=10), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('formula', sa.Text(), nullable=True),
    sa.Column('client_quantity', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('price_item_id', sa.String(length=36), nullable=True),
    sa.Column('composite_price_id', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("kind IN ('section','item','option','variant','provisional')", name='ck_boq_item_kind'),
    sa.CheckConstraint("status IN ('proposed','verified','approved','rejected')", name='ck_boq_item_status'),
    sa.ForeignKeyConstraint(['boq_id'], ['bills_of_quantities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['composite_price_id'], ['composite_prices.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_id'], ['boq_items.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['price_item_id'], ['price_items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('boq_id', 'position', name='uq_boqitem_boq_position')
    )
    with op.batch_alter_table('boq_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_boq_items_boq_id'), ['boq_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_boq_items_organization_id'), ['organization_id'], unique=False)

    op.create_table('composite_components',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('composite_price_id', sa.String(length=36), nullable=False),
    sa.Column('sort_index', sa.Integer(), nullable=False),
    sa.Column('component_type', sa.String(length=20), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('resource_kind', sa.String(length=30), nullable=False),
    sa.Column('price_item_id', sa.String(length=36), nullable=True),
    sa.Column('consumption', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('resource_unit_code', sa.String(length=12), nullable=True),
    sa.Column('unit_price', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('loss_ratio', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('convert_boq_quantity', sa.Boolean(), nullable=False),
    sa.Column('density_value', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('density_source', sa.String(length=255), nullable=True),
    sa.Column('output_rate', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('hourly_rate', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('crew_size', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('payload_value', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('payload_unit_code', sa.String(length=12), nullable=True),
    sa.Column('cost_per_rotation', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('round_up', sa.Boolean(), nullable=False),
    sa.Column('distance_km', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('rate_per_km', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('lump_sum_amount', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("component_type IN ('consumption','output_rate','rotation','lump_sum')", name='ck_composite_component_type'),
    sa.ForeignKeyConstraint(['composite_price_id'], ['composite_prices.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['price_item_id'], ['price_items.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('composite_components', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_composite_components_composite_price_id'), ['composite_price_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_composite_components_organization_id'), ['organization_id'], unique=False)

    op.create_table('estimate_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('estimate_id', sa.String(length=36), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('price_book_version_id', sa.String(length=36), nullable=False),
    sa.Column('markup', sa.JSON(), nullable=False),
    sa.Column('taxes', sa.JSON(), nullable=False),
    sa.Column('rounding', sa.JSON(), nullable=False),
    sa.Column('missing_price_policy', sa.String(length=10), nullable=False),
    sa.Column('snapshot', sa.JSON(), nullable=True),
    sa.Column('snapshot_sha256', sa.String(length=64), nullable=True),
    sa.Column('total_selling_price_ht', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('total_ttc', metreo_api.db.Amount(precision=28, scale=10), nullable=True),
    sa.Column('frozen_at', sa.DateTime(), nullable=True),
    sa.Column('frozen_by', sa.String(length=36), nullable=True),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status IN ('draft','frozen','superseded')", name='ck_estimate_version_status'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['estimate_id'], ['estimates.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['frozen_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['price_book_version_id'], ['price_book_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('estimate_id', 'version_number', name='uq_estimateversion_number')
    )
    with op.batch_alter_table('estimate_versions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_estimate_versions_estimate_id'), ['estimate_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_estimate_versions_organization_id'), ['organization_id'], unique=False)

    op.create_table('import_batch_rows',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('batch_id', sa.String(length=36), nullable=False),
    sa.Column('line_number', sa.Integer(), nullable=False),
    sa.Column('raw', sa.JSON(), nullable=False),
    sa.Column('normalized', sa.JSON(), nullable=True),
    sa.Column('is_valid', sa.Boolean(), nullable=False),
    sa.Column('is_duplicate', sa.Boolean(), nullable=False),
    sa.Column('errors', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['batch_id'], ['import_batches.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('import_batch_rows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_import_batch_rows_batch_id'), ['batch_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('import_batch_rows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_import_batch_rows_batch_id'))

    op.drop_table('import_batch_rows')
    with op.batch_alter_table('estimate_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_estimate_versions_organization_id'))
        batch_op.drop_index(batch_op.f('ix_estimate_versions_estimate_id'))

    op.drop_table('estimate_versions')
    with op.batch_alter_table('composite_components', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_composite_components_organization_id'))
        batch_op.drop_index(batch_op.f('ix_composite_components_composite_price_id'))

    op.drop_table('composite_components')
    with op.batch_alter_table('boq_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_boq_items_organization_id'))
        batch_op.drop_index(batch_op.f('ix_boq_items_boq_id'))

    op.drop_table('boq_items')
    with op.batch_alter_table('price_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_price_items_price_book_version_id'))
        batch_op.drop_index(batch_op.f('ix_price_items_organization_id'))
        batch_op.drop_index('ix_price_items_org_family')

    op.drop_table('price_items')
    with op.batch_alter_table('import_batches', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_import_batches_price_book_version_id'))
        batch_op.drop_index(batch_op.f('ix_import_batches_organization_id'))

    op.drop_table('import_batches')
    with op.batch_alter_table('estimates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_estimates_project_id'))
        batch_op.drop_index(batch_op.f('ix_estimates_organization_id'))

    op.drop_table('estimates')
    with op.batch_alter_table('composite_prices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_composite_prices_price_book_version_id'))
        batch_op.drop_index(batch_op.f('ix_composite_prices_organization_id'))

    op.drop_table('composite_prices')
    with op.batch_alter_table('price_book_versions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_price_book_versions_price_book_id'))
        batch_op.drop_index(batch_op.f('ix_price_book_versions_organization_id'))

    op.drop_table('price_book_versions')
    with op.batch_alter_table('bills_of_quantities', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_bills_of_quantities_project_id'))
        batch_op.drop_index(batch_op.f('ix_bills_of_quantities_organization_id'))

    op.drop_table('bills_of_quantities')
    with op.batch_alter_table('tax_rates', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_tax_rates_organization_id'))

    op.drop_table('tax_rates')
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_projects_organization_id'))
        batch_op.drop_index('ix_projects_org_status')

    op.drop_table('projects')
    with op.batch_alter_table('price_books', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_price_books_organization_id'))

    op.drop_table('price_books')
    op.drop_table('organization_settings')
    with op.batch_alter_table('memberships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memberships_user_id'))
        batch_op.drop_index(batch_op.f('ix_memberships_organization_id'))

    op.drop_table('memberships')
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index('ix_audit_org_occurred')
        batch_op.drop_index(batch_op.f('ix_audit_events_organization_id'))

    op.drop_table('audit_events')
    op.drop_table('users')
    with op.batch_alter_table('region_profiles', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_region_profiles_country_code'))

    op.drop_table('region_profiles')
    op.drop_table('organizations')
