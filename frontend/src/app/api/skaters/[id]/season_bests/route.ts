import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const db = getDb()
  const sid = Number(id)

  const rows = db.prepare(`
    WITH bests AS (
      SELECT r.skater_id, e.season, r.distance_m, MIN(r.time_seconds) as best_time
      FROM results r
      JOIN events e ON e.id = r.event_id
      WHERE r.skater_id = ? AND r.time_seconds IS NOT NULL
        AND e.track_type = 'short'
        AND r.is_relay = 0
        AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
      GROUP BY r.skater_id, e.season, r.distance_m
    )
    SELECT b.season, b.distance_m, b.best_time,
           (SELECT e2.event_name FROM events e2
            JOIN results r2 ON r2.event_id=e2.id
            WHERE r2.skater_id=b.skater_id AND r2.distance_m=b.distance_m
              AND e2.season=b.season AND e2.track_type='short'
              AND r2.time_seconds = b.best_time
            LIMIT 1) as event_name
    FROM bests b
    ORDER BY b.season, b.distance_m
  `).all(sid)

  return Response.json(rows)
}
