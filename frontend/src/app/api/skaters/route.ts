import { getDb } from "@/lib/db"
import type { NextRequest } from "next/server"

export function GET(request: NextRequest) {
  const db = getDb()
  const { searchParams } = request.nextUrl
  const q = searchParams.get("q")?.trim() ?? ""
  const gender = searchParams.get("gender")
  const limit = Math.min(Number(searchParams.get("limit") ?? "50"), 200)

  // Step 1: fast skater match (no correlated subquery)
  let sql = `
    SELECT s.id, s.full_name, s.first_name, s.last_name, s.gender, s.birth_year,
           COUNT(r.id) as num_results,
           COUNT(DISTINCT r.event_id) as num_events
    FROM skaters s
    LEFT JOIN results r ON r.skater_id = s.id
    WHERE 1=1
  `
  const args: (string | number)[] = []
  if (q) { sql += " AND s.full_name LIKE ?"; args.push(`%${q}%`) }
  if (gender) { sql += " AND s.gender = ?"; args.push(gender) }
  sql += " GROUP BY s.id ORDER BY num_results DESC LIMIT ?"
  args.push(limit)

  const skaters = db.prepare(sql).all(...args) as {
    id: number; full_name: string; first_name: string | null; last_name: string | null
    gender: string | null; birth_year: number | null; num_results: number; num_events: number
  }[]

  if (skaters.length === 0) return Response.json([])

  // Step 2: top club per matched skater (fast targeted join, not correlated)
  const ids = skaters.map(s => s.id)
  const ph = ids.map(() => "?").join(",")
  const clubRows = db.prepare(`
    SELECT skater_id, canonical_name as primary_club
    FROM (
      SELECT r.skater_id, c.canonical_name, COUNT(*) as cnt,
             ROW_NUMBER() OVER (PARTITION BY r.skater_id ORDER BY COUNT(*) DESC) as rn
      FROM results r JOIN clubs c ON c.id = r.club_id
      WHERE r.club_id IS NOT NULL AND r.skater_id IN (${ph})
      GROUP BY r.skater_id, r.club_id
    ) WHERE rn = 1
  `).all(...ids) as { skater_id: number; primary_club: string }[]

  const clubMap = new Map(clubRows.map(r => [r.skater_id, r.primary_club]))

  return Response.json(skaters.map(s => ({ ...s, primary_club: clubMap.get(s.id) ?? null })))
}
