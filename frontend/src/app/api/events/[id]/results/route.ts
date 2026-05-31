import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const { searchParams } = request.nextUrl
  const division = searchParams.get("division")
  const distance_m = searchParams.get("distance_m")
  const round_type = searchParams.get("round_type")
  const db = getDb()

  let sql = `
    SELECT r.id, r.skater_id, r.skater_name, c.canonical_name as club_name,
           r.division, r.distance_m, r.round_type, r.heat, r.rank,
           r.time_seconds, r.time_text, r.status, r.bib, r.race_number, r.points
    FROM results r
    LEFT JOIN clubs c ON c.id = r.club_id
    WHERE r.event_id = ? AND r.is_relay = 0
  `
  const args: (string | number)[] = [Number(id)]
  if (division) { sql += " AND r.division = ?"; args.push(division) }
  if (distance_m) { sql += " AND r.distance_m = ?"; args.push(Number(distance_m)) }
  if (round_type) { sql += " AND r.round_type = ?"; args.push(round_type) }
  sql += " ORDER BY r.division, r.distance_m, r.round_type, r.heat, r.rank NULLS LAST, r.time_seconds NULLS LAST"

  return Response.json(db.prepare(sql).all(...args))
}
