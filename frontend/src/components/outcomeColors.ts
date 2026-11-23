/** The colour of each outcome, shared by the badges and the chart that draws it (#112). */
export const OUTCOME_COLORS: Record<string, string> = {
  won: "#27ae60", lost: "#c0392b", open: "#1565c0",
  expired: "#888", inconclusive: "#aaa",
};

/** An outcome's colour, or a neutral grey for one this list does not know. */
export function outcomeColor(outcome: string | null | undefined): string {
  return (outcome && OUTCOME_COLORS[outcome]) || "#aaa";
}
