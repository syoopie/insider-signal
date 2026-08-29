/**
 * SIC code → sector.
 *
 * EDGAR gives every filer a four-digit SIC code and a specific description
 * ("Pharmaceutical Preparations", "State Commercial Banks"). Those are too fine
 * to group by — a few hundred distinct values across a couple of thousand
 * companies — so the first two digits are mapped to the standard SIC divisions,
 * which is the level at which "what are insiders buying into" is answerable.
 *
 * Ranges are the official SIC division boundaries.
 */
const DIVISIONS: { max: number; sector: string }[] = [
  { max: 9, sector: "Agriculture & Forestry" },
  { max: 14, sector: "Mining & Energy" },
  { max: 17, sector: "Construction" },
  { max: 19, sector: "Other" },
  { max: 39, sector: "Manufacturing" },
  { max: 49, sector: "Transport & Utilities" },
  { max: 51, sector: "Wholesale Trade" },
  { max: 59, sector: "Retail Trade" },
  { max: 67, sector: "Finance & Real Estate" },
  { max: 89, sector: "Services" },
  { max: 99, sector: "Public Administration" },
];

export const UNKNOWN_SECTOR = "Unclassified";

export function sectorForSic(sic: string | null | undefined): string {
  if (!sic) return UNKNOWN_SECTOR;
  const major = Number.parseInt(String(sic).trim().padStart(4, "0").slice(0, 2), 10);
  if (!Number.isFinite(major) || major <= 0) return UNKNOWN_SECTOR;
  return DIVISIONS.find((d) => major <= d.max)?.sector ?? UNKNOWN_SECTOR;
}
