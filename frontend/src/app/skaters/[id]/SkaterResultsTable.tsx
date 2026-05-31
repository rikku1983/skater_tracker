"use client"

import { useState, useMemo } from "react"
import Link from "next/link"
import { formatTime } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"

interface Row {
  id: number
  event_id: number
  event_name: string
  season: string
  start_date: string | null
  division: string | null
  distance_m: number | null
  round_type: string | null
  heat: string | null
  rank: number | null
  time_seconds: number | null
  time_text: string | null
  status: string | null
  points: number | null
}

export function SkaterResultsTable({ results }: { results: Record<string, unknown>[] }) {
  const [filterSeason, setFilterSeason] = useState("")
  const [filterDist, setFilterDist] = useState("")

  const rows = results as unknown as Row[]

  const seasons = useMemo(() => [...new Set(rows.map(r => r.season))].sort().reverse(), [rows])
  const distances = useMemo(() => [...new Set(rows.map(r => r.distance_m).filter(Boolean))].sort((a, b) => (a ?? 0) - (b ?? 0)), [rows])

  const filtered = useMemo(() => rows.filter(r =>
    (!filterSeason || r.season === filterSeason) &&
    (!filterDist || String(r.distance_m) === filterDist)
  ), [rows, filterSeason, filterDist])

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">All Results</h2>
        <div className="flex gap-2">
          <select value={filterSeason} onChange={e => setFilterSeason(e.target.value)}
            className="border rounded-md px-3 py-1.5 text-sm bg-background">
            <option value="">All seasons</option>
            {seasons.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <select value={filterDist} onChange={e => setFilterDist(e.target.value)}
            className="border rounded-md px-3 py-1.5 text-sm bg-background">
            <option value="">All distances</option>
            {distances.map(d => <option key={d} value={String(d)}>{d}m</option>)}
          </select>
        </div>
      </div>

      <div className="border rounded-lg overflow-x-auto">
        <table className="w-full text-sm min-w-[520px]">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="text-left px-3 py-2 font-medium hidden sm:table-cell">Season</th>
              <th className="text-left px-3 py-2 font-medium">Event</th>
              <th className="text-right px-3 py-2 font-medium">Dist</th>
              <th className="text-left px-3 py-2 font-medium">Round</th>
              <th className="text-left px-3 py-2 font-medium hidden sm:table-cell">Heat</th>
              <th className="text-right px-3 py-2 font-medium">Rank</th>
              <th className="text-right px-3 py-2 font-medium">Time</th>
              <th className="text-left px-3 py-2 font-medium hidden sm:table-cell">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {filtered.map(r => (
              <tr key={r.id} className="hover:bg-muted/30 transition-colors">
                <td className="px-3 py-1.5 text-muted-foreground whitespace-nowrap hidden sm:table-cell">{r.season}</td>
                <td className="px-3 py-1.5">
                  <Link href={`/events/${r.event_id}`} className="hover:underline">{r.event_name}</Link>
                  <div className="text-xs text-muted-foreground sm:hidden">{r.season}</div>
                </td>
                <td className="px-3 py-1.5 text-right">{r.distance_m ? `${r.distance_m}m` : "—"}</td>
                <td className="px-3 py-1.5 text-muted-foreground">{r.round_type ?? "—"}</td>
                <td className="px-3 py-1.5 text-muted-foreground hidden sm:table-cell">{r.heat ?? "—"}</td>
                <td className="px-3 py-1.5 text-right">{r.rank ?? "—"}</td>
                <td className="px-3 py-1.5 text-right font-mono">
                  {r.time_seconds != null ? formatTime(r.time_seconds) : (r.time_text ?? "—")}
                </td>
                <td className="px-3 py-1.5 hidden sm:table-cell">
                  {r.status ? <Badge variant="destructive" className="text-xs">{r.status}</Badge> : null}
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={8} className="px-3 py-8 text-center text-muted-foreground">No results</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="text-xs text-muted-foreground">{filtered.length.toLocaleString()} of {rows.length.toLocaleString()} results</div>
    </div>
  )
}
