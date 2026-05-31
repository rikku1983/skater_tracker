import { sql } from "@/lib/db"
import { notFound } from "next/navigation"
import Link from "next/link"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatTime } from "@/lib/utils"
import { SkaterCharts } from "./SkaterCharts"
import { SkaterResultsTable } from "./SkaterResultsTable"

export const dynamic = "force-dynamic"

export default async function SkaterPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const sid = Number(id)

  const skaterRows = await sql`
    SELECT s.id, s.full_name, s.first_name, s.last_name, s.gender, s.birth_year,
           c.canonical_name as primary_club, c.id as club_id,
           COUNT(DISTINCT r.event_id) as num_events,
           COUNT(r.id) as num_results
    FROM skaters s
    LEFT JOIN results r ON r.skater_id = s.id
    LEFT JOIN clubs c ON c.id = (
      SELECT club_id FROM results WHERE skater_id = s.id AND club_id IS NOT NULL
      GROUP BY club_id ORDER BY COUNT(*) DESC LIMIT 1
    )
    WHERE s.id = ${sid}
    GROUP BY s.id, c.canonical_name, c.id
  `
  if (!skaterRows[0]) notFound()
  const skater = skaterRows[0] as { id: number; full_name: string; gender: string | null; birth_year: number | null; primary_club: string | null; club_id: number | null; num_events: number; num_results: number }

  const [seasonBests, personalBests, results, eventBests] = await Promise.all([
    sql<{ season: string; distance_m: number; best_time: number }[]>`
      WITH bests AS (
        SELECT r.skater_id, e.season, r.distance_m, MIN(r.time_seconds) as best_time
        FROM results r JOIN events e ON e.id = r.event_id
        WHERE r.skater_id = ${sid} AND r.time_seconds IS NOT NULL
          AND e.track_type = 'short' AND r.is_relay = false
          AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
        GROUP BY r.skater_id, e.season, r.distance_m
      )
      SELECT b.season, b.distance_m, b.best_time FROM bests b ORDER BY b.season, b.distance_m
    `,
    sql<{ distance_m: number; best_time: number; season: string }[]>`
      SELECT r.distance_m, MIN(r.time_seconds) as best_time, e.season
      FROM results r JOIN events e ON e.id = r.event_id
      WHERE r.skater_id = ${sid} AND r.time_seconds IS NOT NULL
        AND e.track_type = 'short' AND r.is_relay = false
        AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
      GROUP BY r.distance_m, e.season
      ORDER BY r.distance_m
    `,
    sql`
      SELECT r.id, r.skater_name, c.canonical_name as club_name,
             r.division, r.distance_m, r.round_type, r.heat, r.rank,
             r.time_seconds, r.time_text, r.status, r.points,
             e.event_name, e.season, e.event_date as start_date, e.id as event_id
      FROM results r
      JOIN events e ON e.id = r.event_id
      LEFT JOIN clubs c ON c.id = r.club_id
      WHERE r.skater_id = ${sid} AND e.track_type = 'short' AND r.is_relay = false
      ORDER BY
        SPLIT_PART(e.event_date,'/',3)::INTEGER DESC,
        SPLIT_PART(e.event_date,'/',1)::INTEGER DESC,
        SPLIT_PART(e.event_date,'/',2)::INTEGER DESC,
        e.id DESC, r.distance_m, r.round_type
    `,
    sql<{ event_id: number; event_name: string; start_date: string | null; season: string; distance_m: number; best_time: number }[]>`
      SELECT e.id as event_id, e.event_name, e.event_date as start_date, e.season,
             r.distance_m, MIN(r.time_seconds) as best_time
      FROM results r JOIN events e ON e.id = r.event_id
      WHERE r.skater_id = ${sid} AND r.time_seconds IS NOT NULL
        AND e.track_type = 'short' AND r.is_relay = false
        AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
      GROUP BY e.id, e.event_name, e.event_date, e.season, r.distance_m
      ORDER BY
        SPLIT_PART(e.event_date,'/',3)::INTEGER,
        SPLIT_PART(e.event_date,'/',1)::INTEGER,
        SPLIT_PART(e.event_date,'/',2)::INTEGER,
        e.id, r.distance_m
    `,
  ])

  // Build chart data
  const chartData: Record<number, { x: string; y: number; label?: string }[]> = {}
  const eventChartData: Record<number, { x: string; y: number; label?: string }[]> = {}
  const allDistances = [...new Set([...seasonBests.map(r => r.distance_m), ...eventBests.map(r => r.distance_m)])].sort((a, b) => a - b)

  for (const dist of allDistances) {
    const byDist = seasonBests.filter(r => r.distance_m === dist)
    if (byDist.length > 0) chartData[dist] = byDist.map(r => ({ x: r.season, y: Number(r.best_time) }))
    const byDistEvent = eventBests.filter(r => r.distance_m === dist)
    if (byDistEvent.length > 0) eventChartData[dist] = byDistEvent.map(r => ({ x: r.start_date ?? r.season, y: Number(r.best_time), label: r.event_name }))
  }

  // Personal bests: best time per distance (min across all seasons)
  const pbMap = new Map<number, { best_time: number; season: string }>()
  for (const r of personalBests) {
    const existing = pbMap.get(r.distance_m)
    if (!existing || Number(r.best_time) < existing.best_time) {
      pbMap.set(r.distance_m, { best_time: Number(r.best_time), season: r.season })
    }
  }
  const pbs = [...pbMap.entries()].map(([distance_m, v]) => ({ distance_m, ...v })).sort((a, b) => a.distance_m - b.distance_m)

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-4">
        <div>
          <h1 className="text-3xl font-bold">{skater.full_name}</h1>
          <div className="flex flex-wrap gap-2 mt-2">
            {skater.gender && <Badge variant="secondary">{skater.gender}</Badge>}
            {skater.birth_year && <Badge variant="outline">Born {skater.birth_year}</Badge>}
            {skater.primary_club && (
              <Badge variant="outline">
                {skater.club_id
                  ? <Link href={`/clubs/${skater.club_id}`} className="hover:underline">{skater.primary_club}</Link>
                  : skater.primary_club}
              </Badge>
            )}
            <Badge variant="outline">{Number(skater.num_events)} events · {Number(skater.num_results)} results</Badge>
          </div>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <Card>
          <CardHeader><CardTitle className="text-base">Personal Bests</CardTitle></CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 border-b">
                <tr>
                  <th className="text-left px-4 py-2 font-medium">Distance</th>
                  <th className="text-right px-4 py-2 font-medium">Best Time</th>
                  <th className="text-right px-4 py-2 font-medium">Season</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {pbs.map(b => (
                  <tr key={b.distance_m}>
                    <td className="px-4 py-2">{b.distance_m}m</td>
                    <td className="px-4 py-2 text-right font-mono font-semibold">{formatTime(b.best_time)}</td>
                    <td className="px-4 py-2 text-right text-muted-foreground">{b.season}</td>
                  </tr>
                ))}
                {pbs.length === 0 && (
                  <tr><td colSpan={3} className="px-4 py-4 text-center text-muted-foreground">No timed results</td></tr>
                )}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <SkaterCharts chartData={chartData} eventChartData={eventChartData} />
      </div>

      <SkaterResultsTable results={results as Record<string, unknown>[]} />
    </div>
  )
}
