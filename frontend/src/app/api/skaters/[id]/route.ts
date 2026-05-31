import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const sid = Number(id)

  const skaterRows = await sql`
    SELECT s.id, s.full_name, s.first_name, s.last_name, s.gender, s.birth_year,
           c.canonical_name as primary_club, c.id as club_id,
           COUNT(DISTINCT r.event_id) as num_events,
           COUNT(r.id) as num_results
    FROM skaters s
    LEFT JOIN results r ON r.skater_id = s.id
    LEFT JOIN clubs c ON c.id = (
      SELECT r2.club_id FROM results r2
      JOIN events e2 ON e2.id = r2.event_id
      WHERE r2.skater_id = s.id AND r2.club_id IS NOT NULL
      ORDER BY SPLIT_PART(e2.event_date,'/',3)::INTEGER DESC,
               SPLIT_PART(e2.event_date,'/',1)::INTEGER DESC,
               SPLIT_PART(e2.event_date,'/',2)::INTEGER DESC,
               e2.id DESC
      LIMIT 1
    )
    WHERE s.id = ${sid}
    GROUP BY s.id, c.canonical_name, c.id
  `
  if (!skaterRows[0]) return new Response("Not found", { status: 404 })
  const skater = skaterRows[0]

  const aliases = await sql<{ skater_name: string }[]>`
    SELECT DISTINCT skater_name FROM results WHERE skater_id = ${sid} AND skater_name != ${skater.full_name as string}
    UNION
    SELECT DISTINCT skater_name FROM classification WHERE skater_id = ${sid} AND skater_name != ${skater.full_name as string}
    ORDER BY skater_name
  `
  return Response.json({ ...skater, aliases: aliases.map(a => a.skater_name) })
}
