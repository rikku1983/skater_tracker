import { Suspense } from "react"
import { SkatersSearch } from "./SkatersSearch"

export const dynamic = "force-dynamic"

export default function SkatersPage() {
  return (
    <div className="space-y-4">
      <div
        className="relative rounded-2xl overflow-hidden"
        style={{ backgroundImage: "url('/skaters.png')", backgroundSize: "cover", backgroundPosition: "center" }}
      >
        <div className="absolute inset-0 bg-black/25" />
        <div className="relative px-8 py-12" style={{ textShadow: "0 1px 6px rgba(0,0,0,0.7)" }}>
          <h1 className="text-4xl font-bold text-white mb-2">Skaters</h1>
          <p className="text-white/80 text-lg">Search and explore skater profiles</p>
        </div>
      </div>
      <Suspense fallback={<div className="text-sm text-muted-foreground">Loading…</div>}>
        <SkatersSearch />
      </Suspense>
    </div>
  )
}
