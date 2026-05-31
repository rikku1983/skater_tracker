import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl
  const season     = searchParams.get("season")
  const distance_m = searchParams.get("distance_m")
  const gender     = searchParams.get("gender")
  const division   = searchParams.get("division")
  const birth_year = searchParams.get("birth_year")

  if (!season || !distance_m) {
    return new Response("season and distance_m are required", { status: 400 })
  }

  const args: (string | number | boolean | null)[] = [season, Number(distance_m)]
  let q = `
    SELECT s.id as skater_id, s.full_name as skater_name,
           c.canonical_name as club_name,
           MIN(r.time_seconds) as best_time,
           COUNT(r.id) as num_races,
           e.season
    FROM results r
    JOIN events e ON e.id = r.event_id
    JOIN skaters s ON s.id = r.skater_id
    LEFT JOIN clubs c ON c.id = r.club_id
    WHERE e.season = $1 AND r.distance_m = $2
      AND r.time_seconds IS NOT NULL AND e.track_type = 'short'
      AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
      AND r.skater_id IS NOT NULL
  `
  if (gender)     { args.push(gender);           q += ` AND s.gender = $${args.length}` }
  if (division)   { args.push(division);          q += ` AND r.division = $${args.length}` }
  if (birth_year) { args.push(Number(birth_year)); q += ` AND s.birth_year = $${args.length}` }
  q += ` GROUP BY s.id, s.full_name, c.canonical_name, e.season ORDER BY best_time ASC LIMIT 50`

  const rows = (await sql.unsafe<Record<string, unknown>[]>(q, args)).map((r, i) => ({
    ...r, rank: i + 1,
  }))
  return Response.json(rows)
}
