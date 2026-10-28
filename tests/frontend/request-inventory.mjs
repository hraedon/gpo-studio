import { Linter } from "eslint";

// A bounded syntax check, not a dataflow/ownership proof. Resolve imported API
// aliases/namespaces, simple identifier aliases and direct/global fetch. Newly
// used browser network channels fail closed until this inventory understands
// them. It does not follow arbitrary wrappers, computed properties or runtime
// injection; review and the actual-page adapters cover those limits.
export function discoverRequests(text, file) {
  const result = [],
    calls = [],
    aliases = [],
    apiNames = new Set(),
    actionNames = new Set(),
    namespaces = new Set(),
    fetchNames = new Set(["fetch"]);
  const rule = {
    meta: { schema: [] },
    create(context) {
      const source = context.sourceCode;
      return {
        FunctionDeclaration(node) {
          if (file !== "api.mjs") return;
          if (node.id?.name === "api") apiNames.add("api");
          if (node.id?.name === "actionApi") actionNames.add("actionApi");
        },
        ImportDeclaration(node) {
          if (!/(^|\/)api\.mjs$/.test(node.source.value)) return;
          for (const spec of node.specifiers) {
            if (spec.imported?.name === "api") apiNames.add(spec.local.name);
            if (spec.imported?.name === "actionApi")
              actionNames.add(spec.local.name);
            if (spec.type === "ImportNamespaceSpecifier")
              namespaces.add(spec.local.name);
          }
        },
        VariableDeclarator(node) {
          if (node.id.type === "Identifier") aliases.push(node);
        },
        CallExpression(node) {
          calls.push({ node, ancestors: source.getAncestors(node) });
        },
        NewExpression(node) {
          const constructor =
            node.callee.name ||
            (node.callee.type === "MemberExpression" &&
            ["window", "globalThis", "self"].includes(node.callee.object.name)
              ? node.callee.property.name || node.callee.property.value
              : null);
          if (
            [
              "XMLHttpRequest",
              "WebSocket",
              "EventSource",
              "WebTransport",
            ].includes(constructor)
          )
            throw Error(
              `Unclassified network channel in ${file}: ${constructor}`,
            );
        },
        "Program:exit"() {
          function channel(node) {
            if (node?.type === "Identifier") {
              if (apiNames.has(node.name) && actionNames.has(node.name))
                throw Error(
                  `Ambiguous API frontdoor alias in ${file}: ${node.name}`,
                );
              return actionNames.has(node.name)
                ? "actionApi"
                : apiNames.has(node.name)
                  ? "api"
                  : fetchNames.has(node.name)
                    ? "fetch"
                    : null;
            }
            if (node?.type !== "MemberExpression") return null;
            const property = node.computed
              ? node.property.type === "Literal"
                ? node.property.value
                : null
              : node.property.name;
            if (namespaces.has(node.object.name)) {
              if (property === null)
                throw Error(`Unclassified computed API frontdoor in ${file}`);
              if (property === "api" || property === "actionApi")
                return property;
            }
            if (
              ["window", "globalThis", "self"].includes(node.object.name) &&
              property === "fetch"
            )
              return "fetch";
            return null;
          }
          for (let pass = 0; pass <= aliases.length; pass++)
            for (const node of aliases) {
              if (
                node.init?.type === "Identifier" &&
                namespaces.has(node.init.name)
              )
                namespaces.add(node.id.name);
              const kind = channel(node.init);
              if (kind)
                (kind === "actionApi"
                  ? actionNames
                  : kind === "api"
                    ? apiNames
                    : fetchNames
                ).add(node.id.name);
            }
          for (const { node, ancestors } of calls) {
            if (
              node.callee.type === "MemberExpression" &&
              (node.callee.property.name === "sendBeacon" ||
                node.callee.property.value === "sendBeacon")
            )
              throw Error(
                `Unclassified network channel in ${file}: sendBeacon`,
              );
            const kind = channel(node.callee);
            if (!kind) continue;
            const options = node.arguments[1],
              properties =
                options?.type === "ObjectExpression" ? options.properties : [];
            const method = properties.find(
              (p) => p.key?.name === "method" || p.key?.value === "method",
            );
            const verb = method
              ? method.value.type === "Literal"
                ? method.value.value
                : `dynamic:${source.getText(method.value)}`
              : options &&
                  (options.type !== "ObjectExpression" ||
                    properties.some((p) => p.type === "SpreadElement"))
                ? "dynamic-options"
                : "GET";
            const fn = ancestors.filter((p) => /Function/.test(p.type)).at(-1);
            let owner = fn?.id?.name;
            if (!owner && fn) {
              const parent = ancestors[ancestors.indexOf(fn) - 1];
              owner =
                parent?.type === "VariableDeclarator"
                  ? parent.id.name
                  : parent?.type === "AssignmentExpression"
                    ? source.getText(parent.left)
                    : "<callback>";
            }
            const comment = source.getCommentsBefore(node.callee).at(-1);
            const id = comment?.value
              .trim()
              .match(/^request: ([a-z0-9.-]+)$/)?.[1];
            result.push({
              id: id ?? null,
              file,
              owner: owner || "<module>",
              channel: kind,
              method: verb,
              path: source.getText(node.arguments[0]),
              offset: node.callee.range[0],
            });
          }
        },
      };
    },
  };
  const errors = new Linter().verify(
    text,
    [
      {
        files: ["**/*.mjs", "**/*.js"],
        languageOptions: { ecmaVersion: "latest", sourceType: "module" },
        plugins: { inventory: { rules: { requests: rule } } },
        rules: { "inventory/requests": "error" },
      },
    ],
    { filename: file },
  );
  if (errors.length) throw Error(JSON.stringify(errors));
  return result;
}

// Classification is semantic, not inferred from method: a POST preview must
// never resolve errors and an actual write must use the explicit frontdoor.
export function validateRequestPolicies(sites, records) {
  for (const site of sites) {
    const record = records.find((candidate) => candidate.id === site.id);
    if (!record) throw Error(`Unregistered request: ${site.id}`);
    if (record.class === "mutation") {
      if (site.channel !== "actionApi")
        throw Error(`Actual write must use actionApi: ${site.id}`);
    } else if (
      ["read", "preview", "artifact", "silent-monitor"].includes(record.class)
    ) {
      if (!["api", "fetch"].includes(site.channel))
        throw Error(`Read must remain pure: ${site.id}`);
    } else if (record.class === "transport") {
      const expected =
        site.id === "transport.api"
          ? ["api", "fetch"]
          : site.id === "transport.action"
            ? ["actionApi", "api"]
            : null;
      if (
        !expected ||
        site.file !== "api.mjs" ||
        site.owner !== expected[0] ||
        site.channel !== expected[1]
      )
        throw Error(`Invalid internal transport: ${site.id}`);
    } else throw Error(`Unclassified request effect: ${site.id}`);
  }
}
