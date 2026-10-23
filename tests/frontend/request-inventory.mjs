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
    namespaces = new Set(),
    fetchNames = new Set(["fetch"]);
  const rule = {
    meta: { schema: [] },
    create(context) {
      const source = context.sourceCode;
      return {
        ImportDeclaration(node) {
          if (!node.source.value.endsWith("api.mjs")) return;
          for (const spec of node.specifiers) {
            if (spec.imported?.name === "api") apiNames.add(spec.local.name);
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
            if (node?.type === "Identifier")
              return apiNames.has(node.name)
                ? "api"
                : fetchNames.has(node.name)
                  ? "fetch"
                  : null;
            if (node?.type !== "MemberExpression") return null;
            if (
              !node.computed &&
              namespaces.has(node.object.name) &&
              node.property.name === "api"
            )
              return "api";
            if (
              ["window", "globalThis", "self"].includes(node.object.name) &&
              (node.property.name === "fetch" ||
                node.property.value === "fetch")
            )
              return "fetch";
            return null;
          }
          for (let pass = 0; pass <= aliases.length; pass++)
            for (const node of aliases) {
              const kind = channel(node.init);
              if (kind)
                (kind === "api" ? apiNames : fetchNames).add(node.id.name);
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
