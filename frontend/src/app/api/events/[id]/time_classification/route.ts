import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const db = getDb()

  const rows = db.prepare(`
    SELECT tc.id, tc.skater_id, tc.skater_name, c.canonical_name as club_name,
           tc.division, tc.distance_m, tc.best_time_seconds as time_seconds, tc.rank, tc.bib
    FROM time_classification tc
    LEFT JOIN clubs c ON c.id = tc.club_id
    WHERE tc.event_id = ?
    ORDER BY tc.division, tc.distance_m, tc.rank NULLS LAST, tc.best_time_seconds NULLS LAST
  `).all(Number(id))

  return Response.json(rows)
}
