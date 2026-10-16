"""Email-anchored website-provider proofs and session provenance.

Revision ID: e2f3a4b5c6d7
Revises: d04fdae36884
"""

from alembic import op
import sqlalchemy as sa

revision = "e2f3a4b5c6d7"
down_revision = "d04fdae36884"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_channel_identity_owner", "channel_identity", ["id", "app_user_id"])
    op.create_table(
        "external_login_identity",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("application_id", sa.Text(), nullable=False),
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("app_user_id", sa.BigInteger(), sa.ForeignKey("app_user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("channel_identity_id", sa.BigInteger(), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "application_id", "namespace", "subject", name="uq_external_login_principal"),
        sa.ForeignKeyConstraint(["channel_identity_id", "app_user_id"], ["channel_identity.id", "channel_identity.app_user_id"],
                                name="fk_external_login_anchor_owner", deferrable=True, initially="DEFERRED"),
        sa.CheckConstraint("provider IN ('microsoft', 'wechat_website', 'qq')", name="ck_external_login_provider"),
        sa.CheckConstraint("length(application_id) BETWEEN 1 AND 512 AND length(namespace) BETWEEN 1 AND 1024 AND length(subject) BETWEEN 1 AND 1024", name="ck_external_login_principal_bounds"),
    )
    op.create_index("ix_external_login_user", "external_login_identity", ["app_user_id"])
    op.create_index("ix_external_login_anchor", "external_login_identity", ["channel_identity_id"])
    op.create_table(
        "external_login_flow",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("state_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("csrf_token_hash", sa.Text(), nullable=False),
        sa.Column("requester_hash", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("application_id", sa.Text(), nullable=False),
        sa.Column("configuration_id", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False, server_default="created"),
        sa.Column("material_ciphertext", sa.Text()),
        sa.Column("namespace", sa.Text()),
        sa.Column("subject", sa.Text()),
        sa.Column("failure_code", sa.Text()),
        sa.Column("completed_session_hash", sa.Text()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("provider IN ('microsoft', 'wechat_website', 'qq')", name="ck_external_flow_provider"),
        sa.CheckConstraint("phase IN ('created', 'exchanging', 'email_required', 'completed', 'failed', 'cancelled')", name="ck_external_flow_phase"),
    )
    op.create_index("ix_external_flow_expires", "external_login_flow", ["expires_at"])
    op.create_index("ix_external_flow_requester_created", "external_login_flow", ["requester_hash", "created_at"])
    op.add_column("web_auth_challenge", sa.Column("external_login_flow_id", sa.BigInteger()))
    op.create_foreign_key("fk_web_auth_challenge_external_flow", "web_auth_challenge", "external_login_flow",
                          ["external_login_flow_id"], ["id"], ondelete="CASCADE")
    op.add_column("web_session", sa.Column("external_login_identity_id", sa.BigInteger()))
    op.create_foreign_key("fk_web_session_external_login_identity", "web_session", "external_login_identity",
                          ["external_login_identity_id"], ["id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_web_session_external_provenance", "web_session",
        "(external_login_identity_id IS NULL AND login_channel IN ('web', 'email', 'telegram', 'wechat')) OR "
        "(external_login_identity_id IS NOT NULL AND login_channel IN ('microsoft', 'wechat_website', 'qq') AND channel_identity_id IS NOT NULL)")
    op.create_index("ix_web_session_external_identity", "web_session", ["external_login_identity_id"])


def downgrade() -> None:
    op.drop_index("ix_web_session_external_identity", table_name="web_session")
    op.drop_constraint("ck_web_session_external_provenance", "web_session", type_="check")
    op.drop_constraint("fk_web_session_external_login_identity", "web_session", type_="foreignkey")
    op.drop_column("web_session", "external_login_identity_id")
    op.drop_constraint("fk_web_auth_challenge_external_flow", "web_auth_challenge", type_="foreignkey")
    op.drop_column("web_auth_challenge", "external_login_flow_id")
    op.drop_table("external_login_flow")
    op.drop_table("external_login_identity")
    op.drop_constraint("uq_channel_identity_owner", "channel_identity", type_="unique")
