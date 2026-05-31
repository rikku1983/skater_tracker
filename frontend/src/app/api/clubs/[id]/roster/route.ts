import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const { searchParams } = request.nextUrl
  const season = searchParams.get("season")
  const db = getDb()

  let sql = `
    SELECT s.id as skater_id, s.full_name, s.gender, s.birth_year,
           COUNT(r.id) as num_results,
           MIN(CASE WHEN r.distance_m=500 AND r.time_seconds IS NOT NULL
               AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
               THEN r.time_seconds END) as best_500
    FROM skaters s
    JOIN results r ON r.skater_id = s.id
    JOIN events e ON e.id = r.event_id
    WHERE r.club_id = ? AND e.track_type = 'short' AND r.is_relay = 0
  `
  const args: (string | number)[] = [Number(id)]
  if (season) { sql += " AND e.season = ?"; args.push(season) }
  sql += " GROUP BY s.id ORDER BY num_results DESC"

  return Response.json(db.prepare(sql).all(...args))
}
