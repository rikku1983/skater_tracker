import { getDb } from "@/lib/db"

export function GET() {
  const db = getDb()
  const rows = db.prepare(
    "SELECT DISTINCT season FROM events WHERE track_type='short' ORDER BY season DESC"
  ).all() as { season: string }[]
  return Response.json(rows.map(r => r.season))
}
