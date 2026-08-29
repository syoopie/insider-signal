/**
 * Form 4 transaction codes.
 *
 * Only `P` is ever scored — the rest are stored for context, and spelling out
 * what they mean is the difference between a table a reader can interpret and
 * one they have to look up elsewhere.
 *
 * Client-safe by design: this is display metadata, so it must not live in a
 * query module (importing a value from one pulls `lib/db.ts` into the browser).
 */
export const TRANSACTION_CODES: Record<string, { label: string; note: string }> = {
  P: { label: "Purchase", note: "Open-market buy with the insider's own money. The only code scored." },
  S: { label: "Sale", note: "Open-market sale." },
  A: { label: "Award", note: "Grant of stock or RSUs. No cash changes hands, so it carries no signal." },
  M: { label: "Option exercise", note: "Derivative exercised or converted." },
  F: { label: "Tax withholding", note: "Shares withheld by the issuer to cover tax on vesting." },
  D: { label: "Disposition", note: "Shares returned to the issuer." },
  G: { label: "Gift", note: "Shares given away or received as a gift." },
  X: { label: "In-the-money exercise", note: "Exercise of an in-the-money derivative." },
  C: { label: "Conversion", note: "Conversion of a derivative security." },
  J: { label: "Other", note: "Other acquisition or disposition; the filer explains it in a footnote." },
};
