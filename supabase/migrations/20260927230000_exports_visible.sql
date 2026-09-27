alter table app.exports
  add column if not exists created_at timestamptz not null default now();

alter table app.exports enable row level security;

drop policy if exists exports_select_clinician on app.exports;
create policy exports_select_clinician on app.exports
  for select to authenticated
  using (clinician_id = (select auth.uid()));

drop policy if exists exports_select_member on app.exports;
create policy exports_select_member on app.exports
  for select to authenticated
  using (
    exists (
      select 1
      from app.share_grants g
      where g.id = exports.grant_id
        and app.is_member(g.household_id, 'view')
    )
  );
