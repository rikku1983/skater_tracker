import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl
  const q = searchParams.get("q")?.trim() ?? ""
  const gender = searchParams.get("gender")
  const limit = Math.min(Number(searchParams.get("limit") ?? "50"), 200)

  const args: (string | number | boolean | null)[] = []
  let query = `
    SELECT s.id, s.full_name, s.first_name, s.last_name, s.gender, s.birth_year,
           COUNT(r.id) as num_results,
           COUNT(DISTINCT r.event_id) as num_events
    FROM skaters s
    LEFT JOIN results r ON r.skater_id = s.id
    WHERE 1=1
  `
  if (q)      { args.push(`%${q}%`); query += ` AND s.full_name ILIKE $${args.length}` }
  if (gender) { args.push(gender);   query += ` AND s.gender = $${args.length}` }
  args.push(limit)
  query += ` GROUP BY s.id ORDER BY num_results DESC LIMIT $${args.length}`

  const skaters = await sql.unsafe<{ id: number; full_name: string; first_name: string | null; last_name: string | null; gender: string | null; birth_year: number | null; num_results: number; num_events: number }[]>(query, args)
  if (skaters.length === 0) return Response.json([])

  const ids = skaters.map(s => s.id)
  const clubRows = await sql<{ skater_id: number; primary_club: string }[]>`
    SELECT DISTINCT ON (r.skater_id) r.skater_id, c.canonical_name as primary_club
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
  const clubMap = new Map(clubRows.map(r => [r.skater_id, r.primary_club]))
  return Response.json(skaters.map(s => ({ ...s, primary_club: clubMap.get(s.id) ?? null })))
}
