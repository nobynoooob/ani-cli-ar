-- Watch Together: Supabase Realtime Broadcast/Presence authorization policies
--
-- Supabase enables "Realtime Authorization" by default. It gates Broadcast and
-- Presence relay on RLS policies for the `realtime.messages` table (in the
-- `realtime` schema). Without these policies, channel subscribe/join works but
-- messages are silently dropped on the server, so hosts/guests never sync.
--
-- The client connects with the ANON key (no user session), so the `anon` role
-- is what needs access. Rooms are identified by a 6-digit code (the code is
-- the auth), so wide-open public policies are intentional.
--
-- Apply this file in the Supabase dashboard: SQL Editor -> New query -> Run.
-- Realtime reloads the policy cache on each new WebSocket connection, so
-- existing clients must reconnect (restart the app) after applying.

drop policy if exists "anon can receive watch_together" on "realtime"."messages";
drop policy if exists "anon can send watch_together" on "realtime"."messages";

create policy "anon can receive watch_together"
on "realtime"."messages"
for select
to anon
using ( true );

create policy "anon can send watch_together"
on "realtime"."messages"
for insert
to anon
with check ( true );
