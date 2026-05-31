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

  if (!distance_m) {
    return new Response("distance_m is required", { status: 400 })
  }

  // Step 1: leaderboard grouped by skater only (no club join — avoids duplicate rows)
  const args: (string | number | boolean | null)[] = [Number(distance_m)]
  let q = `
    SELECT s.id as skater_id, s.full_name as skater_name,
           MIN(r.time_seconds) as best_time,
           COUNT(r.id) as num_races
    FROM results r
    JOIN events e ON e.id = r.event_id
    JOIN skaters s ON s.id = r.skater_id
    WHERE r.distance_m = $1
      AND r.time_seconds IS NOT NULL AND e.track_type = 'short'
      AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
      AND r.skater_id IS NOT NULL
  `
  if (season)     { args.push(season);            q += ` AND e.season = $${args.length}` }
  if (gender)     { args.push(gender);            q += ` AND s.gender = $${args.length}` }
  if (division)   { args.push(division);           q += ` AND r.division = $${args.length}` }
  if (birth_year) { args.push(Number(birth_year)); q += ` AND s.birth_year = $${args.length}` }
  q += ` GROUP BY s.id, s.full_name ORDER BY best_time ASC LIMIT 50`

  const rows = await sql.unsafe<{ skater_id: number; skater_name: string; best_time: number; num_races: number }[]>(q, args)
  if (rows.length === 0) return Response.json([])

  // Step 2: most common club per skater
  const ids = rows.map(r => r.skater_id)
  const clubRows = await sql<{ skater_id: number; club_name: string }[]>`
    SELECT DISTINCT ON (r.skater_id) r.skater_id, c.canonical_name as club_name
    FROM results r
    JOIN clubs c ON c.id = r.club_id
    JOIN events e ON e.id = r.event_id
    WHERE r.club_id IS NOT NULL AND r.skater_id = ANY(${ids})
    ORDER BY r.skater_id,
      SPLIT_PART(e.event_date,'/',3)::INTEGER DESC,
      SPLIT_PART(e.event_date,'/',1)::INTEGER DESC,
      SPLIT_PART(e.event_date,'/',2)::INTEGER DESC,
      e.id DESC
  `
  const clubMap = new Map(clubRows.map(r => [r.skater_id, r.club_name]))

  return Response.json(rows.map((r, i) => ({
    rank: i + 1,
    skater_id: r.skater_id,
    skater_name: r.skater_name,
    club_name: clubMap.get(r.skater_id) ?? null,
    best_time: r.best_time,
    num_races: r.num_races,
  })))
}
