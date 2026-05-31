import { getDb } from "@/lib/db"
import { notFound } from "next/navigation"
import Link from "next/link"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { formatTime } from "@/lib/utils"
import { SkaterCharts } from "./SkaterCharts"
import { SkaterResultsTable } from "./SkaterResultsTable"

const CHART_DISTANCES = [333, 500, 777, 1000, 1500]

export default async function SkaterPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const db = getDb()
  const sid = Number(id)

  const skater = db.prepare(`
    SELECT s.id, s.full_name, s.first_name, s.last_name, s.gender, s.birth_year,
           c.canonical_name as primary_club, c.id as club_id,
           COUNT(DISTINCT r.event_id) as num_events,
           COUNT(r.id) as num_results
    FROM skaters s
    LEFT JOIN results r ON r.skater_id = s.id
    LEFT JOIN clubs c ON c.id = (
      SELECT club_id FROM results WHERE skater_id=s.id AND club_id IS NOT NULL
      GROUP BY club_id ORDER BY COUNT(*) DESC LIMIT 1
    )
    WHERE s.id = ?
    GROUP BY s.id
  `).get(sid) as { id: number; full_name: string; gender: string | null; birth_year: number | null; primary_club: string | null; club_id: number | null; num_events: number; num_results: number } | undefined

  if (!skater) notFound()

  const seasonBests = db.prepare(`
    WITH bests AS (
      SELECT r.skater_id, e.season, r.distance_m, MIN(r.time_seconds) as best_time
      FROM results r
      JOIN events e ON e.id = r.event_id
      WHERE r.skater_id = ? AND r.time_seconds IS NOT NULL
        AND e.track_type = 'short' AND r.is_relay = 0
        AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
      GROUP BY r.skater_id, e.season, r.distance_m
    )
    SELECT b.season, b.distance_m, b.best_time
    FROM bests b
    ORDER BY b.season, b.distance_m
  `).all(sid) as { season: string; distance_m: number; best_time: number }[]

  const personalBests = db.prepare(`
    SELECT r.distance_m, MIN(r.time_seconds) as best_time, e.season
    FROM results r
    JOIN events e ON e.id = r.event_id
    WHERE r.skater_id = ? AND r.time_seconds IS NOT NULL
      AND e.track_type = 'short' AND r.is_relay = 0
      AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
    GROUP BY r.distance_m
    ORDER BY r.distance_m
  `).all(sid) as { distance_m: number; best_time: number; season: string }[]

  const results = db.prepare(`
    SELECT r.id, r.skater_name, c.canonical_name as club_name,
           r.division, r.distance_m, r.round_type, r.heat, r.rank,
           r.time_seconds, r.time_text, r.status, r.points,
           e.event_name, e.season, e.event_date as start_date, e.id as event_id
    FROM results r
    JOIN events e ON e.id = r.event_id
    LEFT JOIN clubs c ON c.id = r.club_id
    WHERE r.skater_id = ? AND e.track_type = 'short' AND r.is_relay = 0
    ORDER BY
      CAST(substr(e.event_date,-4) AS INTEGER) DESC,
      CAST(substr(e.event_date,1,instr(e.event_date,'/')-1) AS INTEGER) DESC,
      CAST(substr(e.event_date,instr(e.event_date,'/')+1) AS INTEGER) DESC,
      e.id DESC, r.distance_m, r.round_type
  `).all(sid) as Record<string, unknown>[]

  const eventBests = db.prepare(`
    SELECT e.id as event_id, e.event_name, e.event_date as start_date, e.season,
           r.distance_m, MIN(r.time_seconds) as best_time
    FROM results r
    JOIN events e ON e.id = r.event_id
    WHERE r.skater_id = ? AND r.time_seconds IS NOT NULL
      AND e.track_type = 'short' AND r.is_relay = 0
      AND COALESCE(r.status,'') NOT IN ('DNS','DNS+','DNF','DNF+','DQ','DQ+','FNT','no contest')
    GROUP BY e.id, r.distance_m
    ORDER BY
      CAST(substr(e.event_date,-4) AS INTEGER),
      CAST(substr(e.event_date,1,instr(e.event_date,'/')-1) AS INTEGER),
      CAST(substr(e.event_date,instr(e.event_date,'/')+1) AS INTEGER),
      e.id, r.distance_m
  `).all(sid) as { event_id: number; event_name: string; start_date: string | null; season: string; distance_m: number; best_time: number }[]

  // Build chart data per distance
  const chartData: Record<number, { x: string; y: number; label?: string }[]> = {}
  const eventChartData: Record<number, { x: string; y: number; label?: string }[]> = {}
  const allDistances = [...new Set([
    ...seasonBests.map(r => r.distance_m),
    ...eventBests.map(r => r.distance_m),
  ])].sort((a, b) => a - b)

  for (const dist of allDistances) {
    const byDist = seasonBests.filter(r => r.distance_m === dist)
    if (byDist.length > 0) {
      chartData[dist] = byDist.map(r => ({ x: r.season, y: r.best_time }))
    }
    const byDistEvent = eventBests.filter(r => r.distance_m === dist)
    if (byDistEvent.length > 0) {
      eventChartData[dist] = byDistEvent.map(r => ({
        x: r.start_date ?? r.season,
        y: r.best_time,
        label: r.event_name,
      }))
    }
  }

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
            <Badge variant="outline">{skater.num_events} events · {skater.num_results} results</Badge>
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
                {personalBests.map(b => (
                  <tr key={b.distance_m}>
                    <td className="px-4 py-2">{b.distance_m}m</td>
                    <td className="px-4 py-2 text-right font-mono font-semibold">{formatTime(b.best_time)}</td>
                    <td className="px-4 py-2 text-right text-muted-foreground">{b.season}</td>
                  </tr>
                ))}
                {personalBests.length === 0 && (
                  <tr><td colSpan={3} className="px-4 py-4 text-center text-muted-foreground">No timed results</td></tr>
                )}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <SkaterCharts chartData={chartData} eventChartData={eventChartData} />
      </div>

      <SkaterResultsTable results={results} />
    </div>
  )
}
