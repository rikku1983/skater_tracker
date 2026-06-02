"use client"

import { useState, useEffect, useMemo } from "react"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { X } from "lucide-react"
import { formatTime } from "@/lib/utils"
import type { SkaterRow, CompareData } from "@/lib/types"
import { CompareCharts } from "./CompareCharts"

interface SelectedSkater { id: number; name: string; birth_year: number | null }

function displayName(s: SelectedSkater, allSelected: SelectedSkater[]): string {
  const dupes = allSelected.filter(x => x.name === s.name)
  return dupes.length > 1 && s.birth_year ? `${s.name} (b. ${s.birth_year})` : s.name
}

export default function ComparePage() {
  const [query, setQuery] = useState("")
  const [suggestions, setSuggestions] = useState<SkaterRow[]>([])
  const [selected, setSelected] = useState<SelectedSkater[]>([])
  const [compareData, setCompareData] = useState<CompareData[]>([])
  const [chartMode, setChartMode] = useState<"season" | "event">("season")

  useEffect(() => {
    if (!query.trim()) { setSuggestions([]); return }
    const t = setTimeout(() => {
      fetch(`/api/skaters?q=${encodeURIComponent(query)}&limit=8`).then(r => r.json()).then(setSuggestions)
    }, 200)
    return () => clearTimeout(t)
  }, [query])

  useEffect(() => {
    if (selected.length === 0) { setCompareData([]); return }
    fetch(`/api/compare?ids=${selected.map(s => s.id).join(",")}`).then(r => r.json()).then(setCompareData)
  }, [selected])

  function addSkater(s: SkaterRow) {
    if (selected.find(x => x.id === s.id)) return
    setSelected(prev => [...prev, { id: s.id, name: s.full_name, birth_year: s.birth_year }])
    setQuery("")
    setSuggestions([])
  }

  function removeSkater(id: number) {
    setSelected(prev => prev.filter(s => s.id !== id))
  }

  // Map skater_id → disambiguated display name (adds birth year when names collide)
  const nameMap = useMemo<Record<number, string>>(() => {
    const map: Record<number, string> = {}
    selected.forEach(s => { map[s.id] = displayName(s, selected) })
    return map
  }, [selected])

  // Stable count across both modes so order doesn't shift when toggling
  function distCount(dist: number) {
    return compareData.reduce((s, d) =>
      s + d.season_bests.filter(b => b.distance_m === dist).length
        + d.event_bests.filter(b => b.distance_m === dist).length, 0)
  }
  const distances = [...new Set(compareData.flatMap(d =>
    (chartMode === "season" ? d.season_bests : d.event_bests).map(b => b.distance_m)
  ))].sort((a, b) => {
    if (a === 500) return -1
    if (b === 500) return 1
    return distCount(b) - distCount(a)
  })

  return (
    <div className="space-y-6">
      <div
        className="relative rounded-2xl overflow-hidden"
        style={{ backgroundImage: "url('/compare-skaters.png')", backgroundSize: "cover", backgroundPosition: "center" }}
      >
        <div className="absolute inset-0 bg-black/25" />
        <div className="relative px-8 py-12" style={{ textShadow: "0 1px 6px rgba(0,0,0,0.7)" }}>
          <h1 className="text-4xl font-bold text-white mb-2">Compare Skaters</h1>
          <p className="text-white/80 text-lg">Side-by-side performance across seasons</p>
        </div>
      </div>

      <div className="space-y-3">
        <div className="relative">
          <Input
            placeholder="Search skater to add…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            className="max-w-xs"
          />
          {suggestions.length > 0 && (
            <div className="absolute z-10 mt-1 w-72 border rounded-md bg-background shadow-lg">
              {suggestions.map(s => (
                <button
                  key={s.id}
                  onClick={() => addSkater(s)}
                  className="w-full text-left px-3 py-2 text-sm hover:bg-muted/50 first:rounded-t-md last:rounded-b-md"
                >
                  <span className="font-medium">{s.full_name}</span>
                  {s.birth_year && <span className="text-muted-foreground ml-1">b. {s.birth_year}</span>}
                  <span className="text-muted-foreground ml-2">{s.primary_club ?? ""}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          {selected.map(s => (
            <Badge key={s.id} variant="secondary" className="text-sm gap-1 pr-1">
              <span>{nameMap[s.id]}</span>
              <button onClick={() => removeSkater(s.id)} className="ml-1 hover:text-destructive">
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
      </div>

      {selected.length > 0 && (
        <div className="flex gap-2">
          <Button variant={chartMode === "season" ? "default" : "outline"} size="sm" onClick={() => setChartMode("season")}>
            Season Best
          </Button>
          <Button variant={chartMode === "event" ? "default" : "outline"} size="sm" onClick={() => setChartMode("event")}>
            Event Best (by date)
          </Button>
        </div>
      )}

      {compareData.length > 0 && (
        <CompareCharts data={compareData} distances={distances} mode={chartMode} nameMap={nameMap} />
      )}

      {compareData.length > 0 && chartMode === "season" && (
        <SeasonBestsTable data={compareData} nameMap={nameMap} />
      )}
    </div>
  )
}

const COLORS = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed", "#db2777"]

function SeasonBestsTable({ data, nameMap }: { data: CompareData[]; nameMap: Record<number, string> }) {
  const allSeasons = [...new Set(data.flatMap(d => d.season_bests.map(b => b.season)))].sort()
  const allDists = [...new Set(data.flatMap(d => d.season_bests.map(b => b.distance_m)))].sort((a, b) => a - b)

  return (
    <div className="space-y-4">
      {allDists.map(dist => {
        const seasons = [...new Set(data.flatMap(d => d.season_bests.filter(b => b.distance_m === dist).map(b => b.season)))].sort()
        return (
          <div key={dist}>
            <h3 className="font-semibold mb-2">{dist}m — Season Bests</h3>
            <div className="border rounded-lg overflow-auto">
              <table className="text-sm">
                <thead className="bg-muted/50 border-b">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">Season</th>
                    {data.map((d, i) => (
                      <th key={d.skater_id} className="text-right px-3 py-2 font-medium" style={{ color: COLORS[i % COLORS.length] }}>
                        {nameMap[d.skater_id] ?? d.skater_name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {seasons.map(s => (
                    <tr key={s} className="hover:bg-muted/30">
                      <td className="px-3 py-1.5 text-muted-foreground">{s}</td>
                      {data.map(d => {
                        const b = d.season_bests.find(x => x.distance_m === dist && x.season === s)
                        return (
                          <td key={d.skater_id} className="px-3 py-1.5 text-right font-mono">
                            {b ? formatTime(b.best_time) : "—"}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}
    </div>
  )
}
