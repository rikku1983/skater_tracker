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
      SELECT club_id FROM results WHERE skater_id = s.id AND club_id IS NOT NULL
      GROUP BY club_id ORDER BY COUNT(*) DESC LIMIT 1
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
