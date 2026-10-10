"""M4 (FR-X.1): tamper-evident audit log. Every row carries the definition/config version it acted under
and joins a per-tenant hash chain: hash = sha256(prev_hash | row), computed by a BEFORE INSERT trigger so
every writer (engine, API, anything later) is chained without code. `seq` orders the chain; it's
assigned under a per-tenant advisory lock because identity ids are handed out before the lock.
nova_app still can't update or delete audit rows (0001). Verify with `select * from audit_verify()`.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"

STATEMENTS = [
    """alter table audit_log
         add column seq bigint, add column definition_version int, add column config_version int,
         add column prev_hash text, add column hash text""",
    # the one definition of a row's digest: used by the trigger and by verification
    """create function audit_digest(prev text, a audit_log) returns text language sql immutable as $$
         select encode(sha256(convert_to(concat_ws('|', prev, a.seq, a.tenant_id, a.actor_type, a.actor_id,
           a.action, a.subject, coalesce(a.evidence::text, ''), coalesce(a.definition_version::text, ''),
           coalesce(a.config_version::text, ''),
           to_char(a.at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')), 'UTF8')), 'hex')
       $$""",
    """create function audit_chain() returns trigger language plpgsql as $$
       declare last audit_log;
       begin
         perform pg_advisory_xact_lock(hashtextextended('audit:' || new.tenant_id::text, 0));
         select * into last from audit_log where tenant_id = new.tenant_id order by seq desc limit 1;
         new.seq := coalesce(last.seq, 0) + 1;
         new.prev_hash := coalesce(last.hash, '');
         new.hash := audit_digest(new.prev_hash, new);
         return new;
       end $$""",
    # backfill the existing rows in id order, per tenant (the owner sees all rows only without FORCE)
    "alter table audit_log no force row level security",
    """do $$
       declare r audit_log; prev text; n bigint; t uuid;
       begin
         for t in select distinct tenant_id from audit_log loop
           prev := ''; n := 0;
           for r in select * from audit_log where tenant_id = t order by id loop
             n := n + 1; r.seq := n;
             update audit_log set seq = n, prev_hash = prev, hash = audit_digest(prev, r) where id = r.id;
             prev := audit_digest(prev, r);
           end loop;
         end loop;
       end $$""",
    "alter table audit_log force row level security",
    "alter table audit_log alter column seq set not null, alter column hash set not null",
    "alter table audit_log add constraint audit_log_chain unique (tenant_id, seq)",
    """create trigger audit_chain before insert on audit_log for each row execute function audit_chain()""",
    # first broken link for the current tenant (RLS scopes it), or no row when the chain holds
    """create function audit_verify() returns table (seq bigint, id bigint, reason text)
       language sql stable as $$
         select x.seq, x.id, x.reason from (
           select a.seq, a.id, a.tenant_id, case
               when a.prev_hash is distinct from coalesce(lag(a.hash) over w, '') then 'prev_hash mismatch'
               when a.hash <> audit_digest(a.prev_hash, a) then 'hash mismatch'
               when a.seq <> row_number() over w then 'gap in sequence'
             end as reason
           from audit_log a window w as (partition by a.tenant_id order by a.seq)
         ) x where x.reason is not null order by x.tenant_id, x.seq
       $$""",
]


def upgrade() -> None:
    for stmt in STATEMENTS:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("drop function audit_verify")
    op.execute("drop trigger audit_chain on audit_log")
    op.execute("drop function audit_chain")
    op.execute("drop function audit_digest")
    op.execute("alter table audit_log drop constraint audit_log_chain")
    op.execute(
        "alter table audit_log drop column seq, drop column definition_version, drop column config_version,"
        " drop column prev_hash, drop column hash"
    )
