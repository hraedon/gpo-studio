// Presentation only: never use this label as request or stored policy identity.
export function policyDisplayLabel(policy) {
  for (const field of ["display_name", "qualified_id", "policy_id", "id"]) {
    const value = policy?.[field];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "Unnamed policy";
}
