import { Suspense } from "react"
import { SkatersSearch } from "./SkatersSearch"

export default function SkatersPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Skaters</h1>
      <Suspense fallback={<div className="text-sm text-muted-foreground">Loading…</div>}>
        <SkatersSearch />
      </Suspense>
    </div>
  )
}
