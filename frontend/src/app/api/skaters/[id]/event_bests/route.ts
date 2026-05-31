import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const db = getDb()

  const rows = db.prepare(`
    SELECT e.id as event_id, e.event_name, e.event_date as start_date, e.season,
           r.distance_m, MIN(r.time_seconds) as best_time
    FROM results r
    JOIN events e ON e.id = r.event_id
    WHERE r.skater_id = ? AND r.time_seconds IS NOT NULL
      AND e.track_type = 'short'
      AND r.is_relay = 0
      AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
    GROUP BY e.id, r.distance_m
    ORDER BY
      CAST(substr(e.event_date,-4) AS INTEGER),
      CAST(substr(e.event_date,1,instr(e.event_date,'/')-1) AS INTEGER),
      CAST(substr(e.event_date,instr(e.event_date,'/')+1) AS INTEGER),
      e.id, r.distance_m
  `).all(Number(id))

  return Response.json(rows)
}
