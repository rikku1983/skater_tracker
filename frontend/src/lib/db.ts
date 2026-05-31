import postgres from "postgres"

const DATABASE_URL = process.env.DATABASE_URL!

export const sql = postgres(DATABASE_URL, {
  ssl: "require",
  max: 10,
  idle_timeout: 20,
  prepare: false, // required for Supabase transaction pooler (port 6543)
})
