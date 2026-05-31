import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl
  const season = searchParams.get("season")
  const track_type = searchParams.get("track_type") ?? "short"

  const params: (string | number | boolean | null)[] = [track_type]
  let q = `
    SELECT e.id, e.event_name, e.season,
           e.event_date as start_date, e.end_date,
           e.venue, e.city, e.state, e.track_type,
           COUNT(r.id) as result_count
    FROM events e
    LEFT JOIN results r ON r.event_id = e.id
    WHERE e.track_type = $1
  `
  if (season) { params.push(season); q += ` AND e.season = $${params.length}` }
  q += ` GROUP BY e.id ORDER BY
    SPLIT_PART(e.event_date,'/',3)::INTEGER DESC,
    SPLIT_PART(e.event_date,'/',1)::INTEGER DESC,
    SPLIT_PART(e.event_date,'/',2)::INTEGER DESC,
    e.id DESC`

  return Response.json(await sql.unsafe(q, params))
}
