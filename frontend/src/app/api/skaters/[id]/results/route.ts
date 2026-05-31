import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  return Response.json(await sql`
    SELECT r.id, r.skater_id, r.skater_name, c.canonical_name as club_name,
           r.division, r.distance_m, r.round_type, r.heat, r.rank,
           r.time_seconds, r.time_text, r.status, r.bib, r.race_number, r.points,
           e.event_name, e.season, e.event_date as start_date, e.id as event_id
    FROM results r
    JOIN events e ON e.id = r.event_id
    LEFT JOIN clubs c ON c.id = r.club_id
    WHERE r.skater_id = ${Number(id)} AND e.track_type = 'short' AND r.is_relay = false
    ORDER BY
      SPLIT_PART(e.event_date,'/',3)::INTEGER DESC,
      SPLIT_PART(e.event_date,'/',1)::INTEGER DESC,
      SPLIT_PART(e.event_date,'/',2)::INTEGER DESC,
      e.id DESC, r.distance_m, r.round_type
  `)
}
