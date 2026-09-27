-- The phone recorder inserts its own session, the 2s segment index, and
-- detector events. Family-added events stay on events_insert_family.

drop policy if exists sessions_insert_recorder on app.sessions;
create policy sessions_insert_recorder on app.sessions
  for insert to authenticated
  with check (
    recorded_by = (select auth.uid())
    and app.is_member(household_id, 'record')
  );

drop policy if exists events_insert_detector on app.events;
create policy events_insert_detector on app.events
  for insert to authenticated
  with check (
    source = 'system'
    and confidence_band is not null
    and exists (
      select 1
      from app.sessions s
      where s.id = session_id
        and s.child_id = child_id
        and s.recorded_by = (select auth.uid())
        and app.is_member(s.household_id, 'record')
    )
  );

drop policy if exists session_segments_select_member on app.session_segments;
create policy session_segments_select_member on app.session_segments
  for select to authenticated
  using (
    exists (
      select 1
      from app.sessions s
      where s.id = session_segments.session_id
        and app.is_member(s.household_id, 'view')
    )
  );

drop policy if exists session_segments_insert_recorder on app.session_segments;
create policy session_segments_insert_recorder on app.session_segments
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
