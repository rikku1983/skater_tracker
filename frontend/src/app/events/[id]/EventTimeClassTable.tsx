"use client"

import { useState, useMemo } from "react"
import Link from "next/link"
import { formatTime } from "@/lib/utils"

interface Row {
  id: number
  skater_id: number | null
  skater_name: string
  club_name: string | null
  division: string | null
  distance_m: number | null
  time_seconds: number | null
  rank: number | null
  bib: string | null
}

export function EventTimeClassTable({ rows }: { rows: Record<string, unknown>[] }) {
  const data = rows as unknown as Row[]
  const [filterDiv, setFilterDiv] = useState("")
  const [filterDist, setFilterDist] = useState("")

  const divisions = useMemo(() => [...new Set(data.map(r => r.division).filter(Boolean))].sort() as string[], [data])
  const distances = useMemo(() => [...new Set(data.map(r => r.distance_m).filter(Boolean))].sort((a, b) => (a ?? 0) - (b ?? 0)) as number[], [data])

  const filtered = useMemo(() => data.filter(r =>
    (!filterDiv || r.division === filterDiv) &&
    (!filterDist || String(r.distance_m) === filterDist)
  ), [data, filterDiv, filterDist])

  return (
    <div className="space-y-3">
      <div className="flex gap-2 flex-wrap">
        <select value={filterDiv} onChange={e => setFilterDiv(e.target.value)} className="border rounded-md px-3 py-1.5 text-sm bg-background">
          <option value="">All divisions</option>
          {divisions.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <select value={filterDist} onChange={e => setFilterDist(e.target.value)} className="border rounded-md px-3 py-1.5 text-sm bg-background">
          <option value="">All distances</option>
          {distances.map(d => <option key={d} value={String(d)}>{d}m</option>)}
        </select>
        <span className="text-sm text-muted-foreground self-center">{filtered.length.toLocaleString()} rows</span>
      </div>

      <div className="border rounded-lg overflow-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="text-right px-3 py-2 font-medium">Rank</th>
              <th className="text-left px-3 py-2 font-medium">Name</th>
              <th className="text-left px-3 py-2 font-medium">Club</th>
              <th className="text-left px-3 py-2 font-medium">Division</th>
              <th className="text-right px-3 py-2 font-medium">Dist</th>
              <th className="text-right px-3 py-2 font-medium">Best Time</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {filtered.map(r => (
              <tr key={r.id} className="hover:bg-muted/30 transition-colors">
                <td className="px-3 py-1.5 text-right text-muted-foreground">{r.rank ?? "—"}</td>
                <td className="px-3 py-1.5">
                  {r.skater_id
                    ? <Link href={`/skaters/${r.skater_id}`} className="hover:underline font-medium">{r.skater_name}</Link>
                    : r.skater_name}
                </td>
                <td className="px-3 py-1.5 text-muted-foreground">{r.club_name ?? "—"}</td>
                <td className="px-3 py-1.5 text-muted-foreground">{r.division ?? "—"}</td>
                <td className="px-3 py-1.5 text-right">{r.distance_m ? `${r.distance_m}m` : "—"}</td>
                <td className="px-3 py-1.5 text-right font-mono font-semibold">{r.time_seconds != null ? formatTime(r.time_seconds) : "—"}</td>
              </tr>
            ))}
            {filtered.length === 0 && <tr><td colSpan={6} className="px-3 py-8 text-center text-muted-foreground">No time classification data</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
