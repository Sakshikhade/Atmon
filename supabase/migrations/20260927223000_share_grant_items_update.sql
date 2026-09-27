-- Sharing the same clip again is an update, not a fresh insert. Without an
-- update policy Postgres rejects the row that is already on the grant.

drop policy if exists share_grant_items_update_sharer on app.share_grant_items;
create policy share_grant_items_update_sharer on app.share_grant_items
  for update to authenticated
  using (
    exists (
      select 1
      from app.share_grants g
      where g.id = share_grant_items.grant_id
        and app.is_member(g.household_id, 'share')
        and g.created_by = (select auth.uid())
    )
  )
  with check (
    exists (
      select 1
      from app.share_grants g
      where g.id = share_grant_items.grant_id
        and app.is_member(g.household_id, 'share')
        and g.created_by = (select auth.uid())
    )
  );
