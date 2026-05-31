import { getDb } from "@/lib/db"

export function GET() {
  const db = getDb()
  const rows = db.prepare(`
    SELECT c.id, c.canonical_name, c.abbreviation, c.city,
           c.state_province as state,
           COUNT(DISTINCT r.skater_id) as skater_count
    FROM clubs c
    LEFT JOIN results r ON r.club_id = c.id
    GROUP BY c.id
    ORDER BY skater_count DESC, c.canonical_name
  `).all()
  return Response.json(rows)
}
