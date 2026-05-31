import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  return Response.json(await sql`
    SELECT e.id as event_id, e.event_name, e.event_date as start_date, e.season,
           r.distance_m, MIN(r.time_seconds) as best_time
    FROM results r
    JOIN events e ON e.id = r.event_id
    WHERE r.skater_id = ${Number(id)} AND r.time_seconds IS NOT NULL
      AND e.track_type = 'short' AND r.is_relay = false
      AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
    GROUP BY e.id, e.event_name, e.event_date, e.season, r.distance_m
    ORDER BY
      SPLIT_PART(e.event_date,'/',3)::INTEGER,
      SPLIT_PART(e.event_date,'/',1)::INTEGER,
      SPLIT_PART(e.event_date,'/',2)::INTEGER,
      e.id, r.distance_m
  `)
}
