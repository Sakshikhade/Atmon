-- Family recorder can delete their own takes (session + child rows).

drop policy if exists sessions_delete_recorder on app.sessions;
create policy sessions_delete_recorder on app.sessions
  for delete to authenticated
  using (
    recorded_by = (select auth.uid())
    and app.is_member(household_id, 'record')
  );

drop policy if exists events_delete_recorder on app.events;
create policy events_delete_recorder on app.events
  for delete to authenticated
  using (
    exists (
      select 1
      from app.sessions s
      where s.id = events.session_id
        and s.recorded_by = (select auth.uid())
        and app.is_member(s.household_id, 'record')
    )
  );

drop policy if exists session_segments_delete_recorder on app.session_segments;
create policy session_segments_delete_recorder on app.session_segments
  for delete to authenticated
  using (
    exists (
      select 1
      from app.sessions s
      where s.id = session_segments.session_id
        and s.recorded_by = (select auth.uid())
        and app.is_member(s.household_id, 'record')
    )
  );

drop policy if exists assent_events_delete_recorder on app.assent_events;
create policy assent_events_delete_recorder on app.assent_events
  for delete to authenticated
  using (
    exists (
      select 1
      from app.sessions s
      where s.id = assent_events.session_id
        and s.recorded_by = (select auth.uid())
        and app.is_member(s.household_id, 'record')
    )
  );

-- ml_jobs.session_id is ON DELETE NO ACTION; clear jobs before/with session delete.
drop policy if exists ml_jobs_delete_recorder on app.ml_jobs;
create policy ml_jobs_delete_recorder on app.ml_jobs
  for delete to authenticated
  using (
    exists (
      select 1
      from app.sessions s
      where s.id = ml_jobs.session_id
        and s.recorded_by = (select auth.uid())
        and app.is_member(s.household_id, 'record')
    )
  );
