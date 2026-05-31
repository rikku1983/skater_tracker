import { getDb } from "@/lib/db"
import { notFound } from "next/navigation"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Badge } from "@/components/ui/badge"
import { EventResultsTable } from "./EventResultsTable"
import { EventClassificationTable } from "./EventClassificationTable"
import { EventTimeClassTable } from "./EventTimeClassTable"

export default async function EventDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params
  const db = getDb()
  const eid = Number(id)

  const event = db.prepare(`
    SELECT e.id, e.event_name, e.season, e.event_date as start_date, e.end_date,
           e.venue, e.city, e.state, e.track_type, e.pdf_format,
           COUNT(r.id) as result_count
    FROM events e
    LEFT JOIN results r ON r.event_id = e.id
    WHERE e.id = ?
    GROUP BY e.id
  `).get(eid) as { id: number; event_name: string; season: string; start_date: string | null; venue: string | null; city: string | null; state: string | null; result_count: number } | undefined

  if (!event) notFound()

  const results = db.prepare(`
    SELECT r.id, r.skater_id, r.skater_name, c.canonical_name as club_name,
           r.division, r.distance_m, r.round_type, r.round_label, r.heat, r.rank,
           r.time_seconds, r.time_text, r.status, r.bib, r.race_number, r.points
    FROM results r
    LEFT JOIN clubs c ON c.id = r.club_id
    WHERE r.event_id = ? AND r.is_relay = 0
    ORDER BY r.race_number NULLS LAST, r.heat NULLS LAST, r.rank NULLS LAST, r.time_seconds NULLS LAST
  `).all(eid) as Record<string, unknown>[]

  const classification = db.prepare(`
    SELECT cl.id, cl.skater_id, cl.skater_name, c.canonical_name as club_name,
           cl.division, cl.section_type, cl.rank, cl.points,
           cl.distance_m, cl.best_time_seconds as time_seconds, cl.bib
    FROM classification cl
    LEFT JOIN clubs c ON c.id = cl.club_id
    WHERE cl.event_id = ?
    ORDER BY cl.division,
             CASE cl.section_type WHEN 'overall' THEN 1 WHEN 'overall_dist' THEN 2 ELSE 3 END,
             cl.distance_m NULLS LAST,
             cl.rank NULLS LAST
  `).all(eid) as Record<string, unknown>[]

  const timeClass = db.prepare(`
    SELECT tc.id, tc.skater_id, tc.skater_name, c.canonical_name as club_name,
           tc.division, tc.distance_m, tc.best_time_seconds as time_seconds, tc.rank, tc.bib
    FROM time_classification tc
    LEFT JOIN clubs c ON c.id = tc.club_id
    WHERE tc.event_id = ?
    ORDER BY tc.division, tc.distance_m, tc.rank NULLS LAST, tc.best_time_seconds NULLS LAST
  `).all(eid) as Record<string, unknown>[]

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold mb-2">{event.event_name}</h1>
        <div className="flex flex-wrap gap-2">
          <Badge variant="secondary">{event.season}</Badge>
          {event.start_date && <Badge variant="outline">{event.start_date}</Badge>}
          {(event.city || event.venue) && (
            <Badge variant="outline">{[event.venue, event.city, event.state].filter(Boolean).join(", ")}</Badge>
          )}
          <Badge variant="outline">{event.result_count.toLocaleString()} results</Badge>
        </div>
      </div>

      <Tabs defaultValue="results">
        <TabsList>
          <TabsTrigger value="results">Results ({results.length.toLocaleString()})</TabsTrigger>
          <TabsTrigger value="classification">Classification ({classification.length.toLocaleString()})</TabsTrigger>
          <TabsTrigger value="time_class">Time Class ({timeClass.length.toLocaleString()})</TabsTrigger>
        </TabsList>
        <TabsContent value="results" className="mt-4">
          <EventResultsTable rows={results} />
        </TabsContent>
        <TabsContent value="classification" className="mt-4">
          <EventClassificationTable rows={classification} />
        </TabsContent>
        <TabsContent value="time_class" className="mt-4">
          <EventTimeClassTable rows={timeClass} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
