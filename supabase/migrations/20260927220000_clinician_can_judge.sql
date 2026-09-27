-- A clinician can judge an event that a grant lists, or any event for a child
-- when the grant covers everything. The check runs as the table owner so it
-- does not re-enter the event and session policies.

create or replace function private.clinician_covers_event(p_event_id uuid)
returns boolean
language sql
stable
security definer
set search_path = app, pg_temp
as $$
  select exists (
    select 1
    from app.share_grant_items i
    join app.share_grants g on g.id = i.grant_id
    where i.event_id = p_event_id
      and g.clinician_id = (select auth.uid())
      and g.status = 'active'
      and g.expires_at > now()
  )
  or exists (
    select 1
    from app.events e
    join app.share_grants g on g.child_id = e.child_id
    where e.id = p_event_id
      and g.scope = 'all'
      and g.clinician_id = (select auth.uid())
      and g.status = 'active'
      and g.expires_at > now()
  );
$$;

revoke all on function private.clinician_covers_event(uuid) from public;
revoke all on function private.clinician_covers_event(uuid) from anon;
grant execute on function private.clinician_covers_event(uuid) to authenticated;

drop policy if exists event_verifications_select_clinician on app.event_verifications;
create policy event_verifications_select_clinician on app.event_verifications
  for select to authenticated
  using (private.clinician_covers_event(event_id));

drop policy if exists event_verifications_insert_clinician on app.event_verifications;
create policy event_verifications_insert_clinician on app.event_verifications
  for insert to authenticated
  with check (
    actor_id = (select auth.uid())
    and actor_kind = 'clinician'
    and private.clinician_covers_event(event_id)
  );

drop policy if exists notes_select_clinician on app.notes;
create policy notes_select_clinician on app.notes
  for select to authenticated
  using (
    event_id is not null
    and private.clinician_covers_event(event_id)
  );

drop policy if exists notes_insert_clinician on app.notes;
create policy notes_insert_clinician on app.notes
  for insert to authenticated
  with check (
    author_id = (select auth.uid())
    and to_family = true
    and event_id is not null
    and private.clinician_covers_event(event_id)
  );

drop policy if exists event_antecedents_insert_clinician on app.event_antecedents;
create policy event_antecedents_insert_clinician on app.event_antecedents
  for insert to authenticated
  with check (
    clinician_id = (select auth.uid())
    and private.clinician_covers_event(event_id)
  );
