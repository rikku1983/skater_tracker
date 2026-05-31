import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const { searchParams } = request.nextUrl
  const section_type = searchParams.get("section_type")
  const division = searchParams.get("division")

  const args: (string | number | boolean | null)[] = [Number(id)]
  let q = `
    SELECT cl.id, cl.skater_id, cl.skater_name, c.canonical_name as club_name,
           cl.division, cl.section_type, cl.rank, cl.points,
           cl.distance_m, cl.best_time_seconds as time_seconds, cl.bib
    FROM classification cl
    LEFT JOIN clubs c ON c.id = cl.club_id
    WHERE cl.event_id = $1
  `
  if (section_type) { args.push(section_type); q += ` AND cl.section_type = $${args.length}` }
  if (division)     { args.push(division);      q += ` AND cl.division = $${args.length}` }
  q += " ORDER BY cl.division, cl.section_type, cl.rank NULLS LAST"

  return Response.json(await sql.unsafe(q, args))
}
