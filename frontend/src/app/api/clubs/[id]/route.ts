import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const db = getDb()

  const row = db.prepare(`
    SELECT c.id, c.canonical_name, c.abbreviation, c.city,
           c.state_province as state, c.aliases as alt_names,
           COUNT(DISTINCT r.skater_id) as skater_count
    FROM clubs c
    LEFT JOIN results r ON r.club_id = c.id
    WHERE c.id = ?
    GROUP BY c.id
  `).get(Number(id))

  if (!row) return new Response("Not found", { status: 404 })
  return Response.json(row)
}
