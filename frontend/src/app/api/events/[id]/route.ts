import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const db = getDb()

  const row = db.prepare(`
    SELECT e.id, e.event_name, e.season,
           e.event_date as start_date, e.end_date,
           e.venue, e.city, e.state, e.track_type, e.pdf_format,
           COUNT(r.id) as result_count
    FROM events e
    LEFT JOIN results r ON r.event_id = e.id
    WHERE e.id = ?
    GROUP BY e.id
  `).get(Number(id))

  if (!row) return new Response("Not found", { status: 404 })
  return Response.json(row)
}
