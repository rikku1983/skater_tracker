import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const { searchParams } = request.nextUrl
  const section_type = searchParams.get("section_type")
  const division = searchParams.get("division")
  const db = getDb()

  let sql = `
    SELECT cl.id, cl.skater_id, cl.skater_name, c.canonical_name as club_name,
           cl.division, cl.section_type, cl.rank, cl.points,
           cl.distance_m, cl.best_time_seconds as time_seconds, cl.bib
    FROM classification cl
    LEFT JOIN clubs c ON c.id = cl.club_id
    WHERE cl.event_id = ?
  `
  const args: (string | number)[] = [Number(id)]
  if (section_type) { sql += " AND cl.section_type = ?"; args.push(section_type) }
  if (division) { sql += " AND cl.division = ?"; args.push(division) }
  sql += " ORDER BY cl.division, cl.section_type, cl.rank NULLS LAST"

  return Response.json(db.prepare(sql).all(...args))
}
