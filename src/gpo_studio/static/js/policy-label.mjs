// Presentation only: never use this label as request or stored policy identity.
export function policyDisplayLabel(policy) {
  for (const field of ["display_name", "qualified_id", "policy_id", "id"]) {
    const value = policy?.[field];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "Unnamed policy";
}

// Presentation only. Real templates can define an empty item label (InetRes.adml
// NeedEdgeBrowserChoice_None), which rendered as a blank option. Show the
// item's id rather than inventing a meaning for it.
export function enumItemLabel(item) {
  const value = item?.display_name;
  if (typeof value === "string" && value.trim()) return value;
  return `(no label: ${item?.id ?? ""})`;
}
