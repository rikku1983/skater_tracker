import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const db = getDb()

  const rows = db.prepare(`
    SELECT r.id, r.skater_id, r.skater_name, c.canonical_name as club_name,
           r.division, r.distance_m, r.round_type, r.heat, r.rank,
           r.time_seconds, r.time_text, r.status, r.bib, r.race_number, r.points,
           e.event_name, e.season, e.event_date as start_date, e.id as event_id
    FROM results r
    JOIN events e ON e.id = r.event_id
    LEFT JOIN clubs c ON c.id = r.club_id
    WHERE r.skater_id = ? AND e.track_type = 'short' AND r.is_relay = 0
    ORDER BY
      CAST(substr(e.event_date,-4) AS INTEGER) DESC,
      CAST(substr(e.event_date,1,instr(e.event_date,'/')-1) AS INTEGER) DESC,
      CAST(substr(e.event_date,instr(e.event_date,'/')+1) AS INTEGER) DESC,
      e.id DESC, r.distance_m, r.round_type
  `).all(Number(id))

  return Response.json(rows)
}
