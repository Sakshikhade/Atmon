-- Policies the family app needs beyond the first read path:
-- flag and family-added events, notes, share grants, consents, retention.

drop policy if exists sessions_update_member on app.sessions;
create policy sessions_update_member on app.sessions
  for update to authenticated
  using (app.is_member(household_id, 'view'))
  with check (app.is_member(household_id, 'view'));

drop policy if exists events_insert_family on app.events;
create policy events_insert_family on app.events
  for insert to authenticated
  with check (
    source = 'family'
    and confidence_band is null
    and exists (
      select 1
      from app.sessions s
      where s.id = session_id
        and s.child_id = child_id
        and app.is_member(s.household_id, 'view')
    )
  );

drop policy if exists events_update_family on app.events;
create policy events_update_family on app.events
  for update to authenticated
  using (
    exists (
      select 1
      from app.sessions s
      where s.id = events.session_id
        and app.is_member(s.household_id, 'view')
    )
  )
  with check (
    exists (
      select 1
      from app.sessions s
      where s.id = session_id
        and app.is_member(s.household_id, 'view')
    )
  );

drop policy if exists notes_select_member on app.notes;
create policy notes_select_member on app.notes
  for select to authenticated
  using (
    (
      event_id is not null
      and exists (
        select 1
        from app.events e
        join app.sessions s on s.id = e.session_id
        where e.id = notes.event_id
          and app.is_member(s.household_id, 'view')
      )
    )
    or (
      session_id is not null
      and exists (
        select 1
        from app.sessions s
        where s.id = notes.session_id
          and app.is_member(s.household_id, 'view')
      )
    )
  );

drop policy if exists notes_insert_family on app.notes;
create policy notes_insert_family on app.notes
  for insert to authenticated
  with check (
    author_id = (select auth.uid())
    and (
      (
        event_id is not null
        and exists (
          select 1
          from app.events e
          join app.sessions s on s.id = e.session_id
          where e.id = event_id
            and app.is_member(s.household_id, 'view')
        )
      )
      or (
        session_id is not null
        and exists (
          select 1
          from app.sessions s
          where s.id = session_id
            and app.is_member(s.household_id, 'view')
        )
      )
    )
  );

drop policy if exists share_grants_select_member on app.share_grants;
create policy share_grants_select_member on app.share_grants
  for select to authenticated
  using (
    app.is_member(household_id, 'view')
    or clinician_id = (select auth.uid())
  );

drop policy if exists share_grants_insert_sharer on app.share_grants;
create policy share_grants_insert_sharer on app.share_grants
  for insert to authenticated
  with check (
    app.is_member(household_id, 'share')
    and created_by = (select auth.uid())
    and clinician_id is null
  );

drop policy if exists share_grants_update_sharer on app.share_grants;
create policy share_grants_update_sharer on app.share_grants
  for update to authenticated
  using (app.is_member(household_id, 'share'))
  with check (app.is_member(household_id, 'share'));

drop policy if exists share_grant_items_select_member on app.share_grant_items;
create policy share_grant_items_select_member on app.share_grant_items
  for select to authenticated
  using (
    exists (
      select 1
      from app.share_grants g
      where g.id = share_grant_items.grant_id
        and (
          app.is_member(g.household_id, 'view')
          or g.clinician_id = (select auth.uid())
        )
    )
  );

drop policy if exists share_grant_items_insert_sharer on app.share_grant_items;
create policy share_grant_items_insert_sharer on app.share_grant_items
  for insert to authenticated
  with check (
    exists (
      select 1
      from app.share_grants g
      where g.id = grant_id
        and app.is_member(g.household_id, 'share')
        and g.created_by = (select auth.uid())
    )
  );

drop policy if exists access_log_select_member on app.access_log;
create policy access_log_select_member on app.access_log
  for select to authenticated
  using (
    exists (
      select 1
      from app.share_grants g
      where g.id = access_log.grant_id
        and app.is_member(g.household_id, 'view')
    )
  );

drop policy if exists capture_requests_select_member on app.capture_requests;
create policy capture_requests_select_member on app.capture_requests
  for select to authenticated
  using (
    exists (
      select 1
      from app.share_grants g
      where g.id = capture_requests.grant_id
        and app.is_member(g.household_id, 'view')
    )
  );

drop policy if exists capture_requests_update_family on app.capture_requests;
create policy capture_requests_update_family on app.capture_requests
  for update to authenticated
  using (
    exists (
      select 1
      from app.share_grants g
      where g.id = capture_requests.grant_id
        and app.is_member(g.household_id, 'view')
    )
  )
  with check (
    exists (
      select 1
      from app.share_grants g
      where g.id = grant_id
        and app.is_member(g.household_id, 'view')
    )
  );

drop policy if exists consents_select_member on app.consents;
create policy consents_select_member on app.consents
  for select to authenticated
  using (app.is_member(household_id, 'view'));

drop policy if exists consents_insert_self on app.consents;
create policy consents_insert_self on app.consents
  for insert to authenticated
  with check (
    user_id = (select auth.uid())
    and app.is_member(household_id, 'view')
  );

drop policy if exists retention_select_member on app.retention_policies;
create policy retention_select_member on app.retention_policies
  for select to authenticated
  using (app.is_member(household_id, 'view'));

drop policy if exists retention_insert_admin on app.retention_policies;
create policy retention_insert_admin on app.retention_policies
  for insert to authenticated
  with check (app.is_member(household_id, 'admin'));

drop policy if exists retention_update_admin on app.retention_policies;
create policy retention_update_admin on app.retention_policies
  for update to authenticated
  using (app.is_member(household_id, 'admin'))
  with check (app.is_member(household_id, 'admin'));
