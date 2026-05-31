import { sql } from "@/lib/db"
import type { NextRequest } from "next/server"

export const dynamic = "force-dynamic"

export async function GET(request: NextRequest) {
  const idsParam = request.nextUrl.searchParams.get("ids") ?? ""
  const ids = idsParam.split(",").map(Number).filter(n => !isNaN(n) && n > 0)
  if (ids.length === 0) return Response.json([])

  const skaters = await sql<{ id: number; full_name: string }[]>`
    SELECT id, full_name FROM skaters WHERE id = ANY(${ids})
  `

  const seasonBests = await sql<{ skater_id: number; season: string; distance_m: number; best_time: number; event_name: string }[]>`
    WITH bests AS (
      SELECT r.skater_id, e.season, r.distance_m, MIN(r.time_seconds) as best_time
      FROM results r JOIN events e ON e.id = r.event_id
      WHERE r.skater_id = ANY(${ids})
        AND r.time_seconds IS NOT NULL AND e.track_type = 'short' AND r.is_relay = false
        AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
      GROUP BY r.skater_id, e.season, r.distance_m
    )
    SELECT b.skater_id, b.season, b.distance_m, b.best_time,
           (SELECT e2.event_name FROM events e2
            JOIN results r2 ON r2.event_id = e2.id
            WHERE r2.skater_id = b.skater_id AND r2.distance_m = b.distance_m
              AND e2.season = b.season AND e2.track_type = 'short'
              AND r2.time_seconds = b.best_time
            LIMIT 1) as event_name
    FROM bests b
    ORDER BY b.season, b.distance_m
  `

  const eventBests = await sql<{ skater_id: number; event_id: number; event_name: string; start_date: string; season: string; distance_m: number; best_time: number }[]>`
    SELECT r.skater_id, e.id as event_id, e.event_name,
           e.event_date as start_date, e.season,
           r.distance_m, MIN(r.time_seconds) as best_time
    FROM results r JOIN events e ON e.id = r.event_id
    WHERE r.skater_id = ANY(${ids})
      AND r.time_seconds IS NOT NULL AND e.track_type = 'short' AND r.is_relay = false
      AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
    GROUP BY r.skater_id, e.id, e.event_name, e.event_date, e.season, r.distance_m
    ORDER BY
      SPLIT_PART(e.event_date,'/',3)::INTEGER,
      SPLIT_PART(e.event_date,'/',1)::INTEGER,
      SPLIT_PART(e.event_date,'/',2)::INTEGER,
      e.id, r.distance_m
  `

  return Response.json(skaters.map(sk => ({
    skater_id: sk.id,
    skater_name: sk.full_name,
    season_bests: seasonBests.filter(r => r.skater_id === sk.id),
    event_bests:  eventBests.filter(r => r.skater_id === sk.id),
  })))
}
