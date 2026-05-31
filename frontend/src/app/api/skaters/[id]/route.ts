import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const db = getDb()
  const sid = Number(id)

  const skater = db.prepare(`
    SELECT s.id, s.full_name, s.first_name, s.last_name, s.gender, s.birth_year,
           c.canonical_name as primary_club, c.id as club_id,
           COUNT(DISTINCT r.event_id) as num_events,
           COUNT(r.id) as num_results
    FROM skaters s
    LEFT JOIN results r ON r.skater_id = s.id
    LEFT JOIN clubs c ON c.id = (
      SELECT club_id FROM results WHERE skater_id=s.id AND club_id IS NOT NULL
      GROUP BY club_id ORDER BY COUNT(*) DESC LIMIT 1
    )
    WHERE s.id = ?
    GROUP BY s.id
  `).get(sid) as Record<string, unknown> | undefined

  if (!skater) return new Response("Not found", { status: 404 })

  const aliases = db.prepare(`
    SELECT DISTINCT skater_name FROM results WHERE skater_id=? AND skater_name != ?
    UNION
    SELECT DISTINCT skater_name FROM classification WHERE skater_id=? AND skater_name != ?
    ORDER BY skater_name
  `).all(sid, skater.full_name as string, sid, skater.full_name as string) as { skater_name: string }[]

  return Response.json({ ...skater, aliases: aliases.map(a => a.skater_name) })
}
