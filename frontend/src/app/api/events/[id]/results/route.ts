import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const { searchParams } = request.nextUrl
  const division = searchParams.get("division")
  const distance_m = searchParams.get("distance_m")
  const round_type = searchParams.get("round_type")

  const args: (string | number | boolean | null)[] = [Number(id)]
  let q = `
    SELECT r.id, r.skater_id, r.skater_name, c.canonical_name as club_name,
           r.division, r.distance_m, r.round_type, r.heat, r.rank,
           r.time_seconds, r.time_text, r.status, r.bib, r.race_number, r.points
    FROM results r
    LEFT JOIN clubs c ON c.id = r.club_id
    WHERE r.event_id = $1 AND r.is_relay = false
  `
  if (division)   { args.push(division);          q += ` AND r.division = $${args.length}` }
  if (distance_m) { args.push(Number(distance_m)); q += ` AND r.distance_m = $${args.length}` }
  if (round_type) { args.push(round_type);         q += ` AND r.round_type = $${args.length}` }
  q += " ORDER BY r.division, r.distance_m, r.round_type, r.heat NULLS LAST, r.rank NULLS LAST, r.time_seconds NULLS LAST"

  return Response.json(await sql.unsafe(q, args))
}
