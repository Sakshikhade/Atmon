-- A clinician accepts invites addressed to their sign-in email, then reads
-- only the events those grants cover and writes a judgement beside the family's.

create or replace function app.accept_invites(display_name text)
returns integer
language plpgsql
security definer
set search_path = app, pg_temp
as $$
declare
  uid uuid := (select auth.uid());
  email text := (select auth.jwt() ->> 'email');
  picked app.clinician_role;
  attached integer;
begin
  if uid is null or email is null then
    raise exception 'Sign in first';
  end if;

  insert into app.profiles (user_id, kind, display_name)
  values (uid, 'clinician', coalesce(nullif(btrim(display_name), ''), split_part(email, '@', 1)))
  on conflict (user_id) do nothing;

  select g.clinician_role into picked
  from app.share_grants g
  where g.clinician_id is null
    and g.status = 'pending'
    and g.invite_email = email
    and g.expires_at > now()
  limit 1;

  if picked is not null then
    insert into app.clinician_profiles (user_id, role)
    values (uid, picked)
    on conflict (user_id) do nothing;
  end if;

  update app.share_grants g
  set clinician_id = uid,
      status = 'active'
  where g.clinician_id is null
    and g.status = 'pending'
    and g.invite_email = email
    and g.expires_at > now();

  get diagnostics attached = row_count;
  return attached;
end;
$$;

revoke all on function app.accept_invites(text) from public;
grant execute on function app.accept_invites(text) to authenticated;

drop policy if exists share_grants_select_invitee on app.share_grants;
create policy share_grants_select_invitee on app.share_grants
  for select to authenticated
  using (
    clinician_id is null
    and status = 'pending'
    and invite_email = (select auth.jwt() ->> 'email')
  );

drop policy if exists children_select_clinician on app.children;
create policy children_select_clinician on app.children
  for select to authenticated
  using (
    exists (
      select 1
      from app.share_grants g
      where g.child_id = children.id
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
  );

drop policy if exists sessions_select_clinician on app.sessions;
create policy sessions_select_clinician on app.sessions
  for select to authenticated
  using (
    exists (
      select 1
      from app.events e
      where e.session_id = sessions.id
        and (
          exists (
            select 1
            from app.share_grant_items i
            join app.share_grants g on g.id = i.grant_id
            where i.event_id = e.id
              and g.clinician_id = (select auth.uid())
              and g.status = 'active'
              and g.expires_at > now()
          )
          or exists (
            select 1
            from app.share_grants g
            where g.child_id = sessions.child_id
              and g.scope = 'all'
              and g.clinician_id = (select auth.uid())
              and g.status = 'active'
              and g.expires_at > now()
          )
        )
    )
  );

drop policy if exists events_select_clinician on app.events;
create policy events_select_clinician on app.events
  for select to authenticated
  using (
    exists (
      select 1
      from app.share_grant_items i
      join app.share_grants g on g.id = i.grant_id
      where i.event_id = events.id
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
    or exists (
      select 1
      from app.share_grants g
      where g.child_id = events.child_id
        and g.scope = 'all'
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
  );

drop policy if exists notes_select_clinician on app.notes;
create policy notes_select_clinician on app.notes
  for select to authenticated
  using (
    event_id is not null
    and exists (
      select 1
      from app.share_grant_items i
      join app.share_grants g on g.id = i.grant_id
      where i.event_id = notes.event_id
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
  );

drop policy if exists notes_insert_clinician on app.notes;
create policy notes_insert_clinician on app.notes
  for insert to authenticated
  with check (
    author_id = (select auth.uid())
    and to_family = true
    and event_id is not null
    and exists (
      select 1
      from app.share_grant_items i
      join app.share_grants g on g.id = i.grant_id
      where i.event_id = notes.event_id
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
  );

drop policy if exists event_verifications_select_clinician on app.event_verifications;
create policy event_verifications_select_clinician on app.event_verifications
  for select to authenticated
  using (
    exists (
      select 1
      from app.share_grant_items i
      join app.share_grants g on g.id = i.grant_id
      where i.event_id = event_verifications.event_id
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
  );

drop policy if exists event_verifications_insert_clinician on app.event_verifications;
create policy event_verifications_insert_clinician on app.event_verifications
  for insert to authenticated
  with check (
    actor_id = (select auth.uid())
    and actor_kind = 'clinician'
    and exists (
      select 1
      from app.share_grant_items i
      join app.share_grants g on g.id = i.grant_id
      where i.event_id = event_verifications.event_id
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
  );

alter table app.event_antecedents enable row level security;

drop policy if exists event_antecedents_select_clinician on app.event_antecedents;
create policy event_antecedents_select_clinician on app.event_antecedents
  for select to authenticated
  using (clinician_id = (select auth.uid()));

drop policy if exists event_antecedents_insert_clinician on app.event_antecedents;
create policy event_antecedents_insert_clinician on app.event_antecedents
  for insert to authenticated
  with check (
    clinician_id = (select auth.uid())
    and exists (
      select 1
      from app.share_grant_items i
      join app.share_grants g on g.id = i.grant_id
      where i.event_id = event_antecedents.event_id
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
  );

drop policy if exists capture_requests_select_clinician on app.capture_requests;
create policy capture_requests_select_clinician on app.capture_requests
  for select to authenticated
  using (clinician_id = (select auth.uid()));

drop policy if exists capture_requests_insert_clinician on app.capture_requests;
create policy capture_requests_insert_clinician on app.capture_requests
  for insert to authenticated
  with check (
    clinician_id = (select auth.uid())
    and exists (
      select 1
      from app.share_grants g
      where g.id = grant_id
        and g.clinician_id = (select auth.uid())
        and g.status = 'active'
        and g.expires_at > now()
    )
  );

alter table app.clinician_profiles enable row level security;

drop policy if exists clinician_profiles_select_self on app.clinician_profiles;
create policy clinician_profiles_select_self on app.clinician_profiles
  for select to authenticated
  using (user_id = (select auth.uid()));

notify pgrst, 'reload schema';
