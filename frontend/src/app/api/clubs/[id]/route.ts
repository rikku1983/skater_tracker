import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const { id } = await params
  const rows = await sql`
    SELECT c.id, c.canonical_name, c.abbreviation, c.city,
           c.state_province as state, c.aliases as alt_names,
           COUNT(DISTINCT r.skater_id) as skater_count
    FROM clubs c
    LEFT JOIN results r ON r.club_id = c.id
    WHERE c.id = ${Number(id)}
    GROUP BY c.id
  `
  if (!rows[0]) return new Response("Not found", { status: 404 })
  return Response.json(rows[0])
}
