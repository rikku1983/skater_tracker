"use client"

import React, { useState, useMemo } from "react"
import Link from "next/link"
import { formatTime } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"

interface Row {
  id: number
  skater_id: number | null
  skater_name: string
  club_name: string | null
  division: string | null
  distance_m: number | null
  round_type: string | null
  round_label: string | null
  heat: number | null
  rank: number | null
  time_seconds: number | null
  time_text: string | null
  status: string | null
  points: number | null
  race_number: number | null
}

function groupByRace(data: Row[]) {
  const groups: { key: number | null; label: string; rows: Row[] }[] = []
  let cur: (typeof groups)[0] | null = null
  for (const r of data) {
    if (!cur || cur.key !== r.race_number) {
      const label = r.round_label
        ? r.round_label
        : r.round_type && r.heat != null
          ? `${r.round_type} Heat ${r.heat}`
          : r.round_type ?? "Race"
      cur = { key: r.race_number, label, rows: [] }
      groups.push(cur)
    }
    cur.rows.push(r)
  }
  return groups
}

export function EventResultsTable({ rows }: { rows: Record<string, unknown>[] }) {
  const data = rows as unknown as Row[]
  const [filterDiv, setFilterDiv] = useState("")
  const [filterDist, setFilterDist] = useState("")
  const [filterRound, setFilterRound] = useState("")

  const divisions = useMemo(() => [...new Set(data.map(r => r.division).filter(Boolean))].sort() as string[], [data])
  const distances = useMemo(() => [...new Set(data.map(r => r.distance_m).filter(Boolean))].sort((a, b) => (a ?? 0) - (b ?? 0)) as number[], [data])
  const rounds = useMemo(() => [...new Set(data.map(r => r.round_type).filter(Boolean))].sort() as string[], [data])

  const filtered = useMemo(() => data.filter(r =>
    (!filterDiv || r.division === filterDiv) &&
    (!filterDist || String(r.distance_m) === filterDist) &&
    (!filterRound || r.round_type === filterRound)
  ), [data, filterDiv, filterDist, filterRound])

  const groups = useMemo(() => groupByRace(filtered), [filtered])

  return (
    <div className="space-y-3">
      <div className="flex gap-2 flex-wrap items-center">
        <select value={filterDiv} onChange={e => setFilterDiv(e.target.value)} className="border rounded-md px-3 py-1.5 text-sm bg-background">
          <option value="">All divisions</option>
          {divisions.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <select value={filterDist} onChange={e => setFilterDist(e.target.value)} className="border rounded-md px-3 py-1.5 text-sm bg-background">
          <option value="">All distances</option>
          {distances.map(d => <option key={d} value={String(d)}>{d}m</option>)}
        </select>
        <select value={filterRound} onChange={e => setFilterRound(e.target.value)} className="border rounded-md px-3 py-1.5 text-sm bg-background">
          <option value="">All rounds</option>
          {rounds.map(r => <option key={r} value={r}>{r}</option>)}
        </select>
        <span className="text-sm text-muted-foreground">{filtered.length.toLocaleString()} rows · {groups.length} races</span>
      </div>

      <div className="border rounded-lg overflow-auto">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 border-b">
            <tr>
              <th className="text-right px-3 py-2 font-medium">Rank</th>
              <th className="text-left px-3 py-2 font-medium">Name</th>
              <th className="text-left px-3 py-2 font-medium">Club</th>
              <th className="text-left px-3 py-2 font-medium">Division</th>
              <th className="text-right px-3 py-2 font-medium">Time</th>
              <th className="text-left px-3 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g, gi) => (
              <React.Fragment key={`grp-${gi}`}>
                <tr className="bg-muted/70 border-t">
                  <td colSpan={6} className="px-3 py-1.5 text-xs font-semibold text-muted-foreground tracking-wide uppercase">
                    {g.label}
                    {g.rows[0]?.distance_m ? ` · ${g.rows[0].distance_m}m` : ""}
                  </td>
                </tr>
                {g.rows.map(r => (
                  <tr key={r.id} className="hover:bg-muted/30 transition-colors border-t border-muted/30">
                    <td className="px-3 py-1.5 text-right text-muted-foreground w-12">{r.rank ?? "—"}</td>
                    <td className="px-3 py-1.5">
                      {r.skater_id
                        ? <Link href={`/skaters/${r.skater_id}`} className="hover:underline font-medium">{r.skater_name}</Link>
                        : r.skater_name}
                    </td>
                    <td className="px-3 py-1.5 text-muted-foreground">{r.club_name ?? "—"}</td>
                    <td className="px-3 py-1.5 text-muted-foreground">{r.division ?? "—"}</td>
                    <td className="px-3 py-1.5 text-right font-mono font-semibold">
                      {r.time_seconds != null ? formatTime(r.time_seconds) : (r.time_text ?? "—")}
                    </td>
                    <td className="px-3 py-1.5">
                      {r.status ? <Badge variant="destructive" className="text-xs">{r.status}</Badge> : null}
                    </td>
                  </tr>
                ))}
              </React.Fragment>
            ))}
            {filtered.length === 0 && (
              <tr><td colSpan={6} className="px-3 py-8 text-center text-muted-foreground">No results</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
