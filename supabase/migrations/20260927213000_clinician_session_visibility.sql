-- sessions_select_clinician used to read events, and events_select_member
-- reads sessions. Postgres stops that loop. This check runs as the table
-- owner, so it can see the grant without re-entering either policy.
-- A session is visible when it holds a shared event, or the grant covers all.

create schema if not exists private;

revoke all on schema private from public;
grant usage on schema private to authenticated;

create or replace function private.clinician_can_see_session(p_session_id uuid, p_child_id uuid)
returns boolean
language sql
stable
security definer
set search_path = app, pg_temp
as $$
  select
    exists (
      select 1
      from app.events e
      join app.share_grant_items i on i.event_id = e.id
      join app.share_grants g on g.id = i.grant_id
      where e.session_id = p_session_id
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
    or exists (
      select 1
      from app.share_grants g
      where g.child_id = p_child_id
        and g.scope = 'all'
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    );
$$;

revoke all on function private.clinician_can_see_session(uuid, uuid) from public;
revoke all on function private.clinician_can_see_session(uuid, uuid) from anon;
grant execute on function private.clinician_can_see_session(uuid, uuid) to authenticated;

drop policy if exists sessions_select_clinician on app.sessions;
create policy sessions_select_clinician on app.sessions
  for select to authenticated
  using (private.clinician_can_see_session(id, child_id));
