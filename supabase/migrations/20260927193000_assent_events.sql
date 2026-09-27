-- Pause and resume on the phone are written as assent events for that session.

alter table app.assent_events enable row level security;

drop policy if exists assent_events_select_member on app.assent_events;
create policy assent_events_select_member on app.assent_events
  for select to authenticated
  using (
    exists (
      select 1
      from app.sessions s
      where s.id = assent_events.session_id
        and app.is_member(s.household_id, 'view')
    )
  );

drop policy if exists assent_events_insert_recorder on app.assent_events;
create policy assent_events_insert_recorder on app.assent_events
  for insert to authenticated
  with check (
    exists (
      select 1
      from app.sessions s
      where s.id = session_id
        and s.recorded_by = (select auth.uid())
        and app.is_member(s.household_id, 'record')
    )
  );
