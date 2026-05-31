import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export function GET(request: NextRequest) {
  const db = getDb()
  const { searchParams } = request.nextUrl
  const season = searchParams.get("season")
  const track_type = searchParams.get("track_type") ?? "short"

  let sql = `
    SELECT e.id, e.event_name, e.season,
           e.event_date as start_date, e.end_date,
           e.venue, e.city, e.state, e.track_type,
           COUNT(r.id) as result_count
    FROM events e
    LEFT JOIN results r ON r.event_id = e.id
    WHERE e.track_type = ?
  `
  const args: (string | null)[] = [track_type]
  if (season) { sql += " AND e.season = ?"; args.push(season) }
  sql += ` GROUP BY e.id ORDER BY
    CAST(substr(e.event_date,-4) AS INTEGER) DESC,
    CAST(substr(e.event_date,1,instr(e.event_date,'/')-1) AS INTEGER) DESC,
    CAST(substr(e.event_date,instr(e.event_date,'/')+1) AS INTEGER) DESC,
    e.id DESC`

  return Response.json(db.prepare(sql).all(...args))
}
