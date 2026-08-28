import { neon } from "@neondatabase/serverless";

/**
 * Neon HTTP client. One round trip per query, no connection pool to manage,
 * which is what serverless functions want. Reads never mutate, so the plain
 * `neon()` tagged-template client is enough.
 *
 * The Python pipeline owns writes and the schema; this app only reads.
 */
const connectionString = process.env.DATABASE_URL;

if (!connectionString && process.env.NODE_ENV === "production") {
  // A missing URL at runtime in production is a deploy misconfiguration worth failing loudly on.
  throw new Error("DATABASE_URL is not set");
}

const client = connectionString ? neon(connectionString) : null;

/**
 * Postgres array parameters (for `= ANY($n::text[])`) are serialised to an array
 * literal by the driver, so a `string[]` is a legal single parameter.
 */
export type SqlParam = string | number | boolean | null | string[];

/**
 * Run a parameterised query. Returns `[]` (never throws) when the DB is
 * unreachable or unconfigured, so a page renders its empty state instead of a
 * 500. Callers that need to distinguish "no rows" from "query failed" should
 * use `queryOrThrow`.
 */
export async function query<T = Record<string, unknown>>(
  sql: string,
  params: SqlParam[] = [],
): Promise<T[]> {
  if (!client) return [];
  try {
    const rows = await client.query(sql, params);
    return rows as T[];
  } catch (err) {
    console.error("[db] query failed:", err);
    return [];
  }
}

export async function queryOrThrow<T = Record<string, unknown>>(
  sql: string,
  params: SqlParam[] = [],
): Promise<T[]> {
  if (!client) throw new Error("DATABASE_URL is not set");
  const rows = await client.query(sql, params);
  return rows as T[];
}

export async function queryOne<T = Record<string, unknown>>(
  sql: string,
  params: SqlParam[] = [],
): Promise<T | null> {
  const rows = await query<T>(sql, params);
  return rows[0] ?? null;
}
