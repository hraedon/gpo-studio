import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const workspaceDirectory = mkdtempSync(join(tmpdir(), "gpo-studio-browser-"));
const database = join(workspaceDirectory, "workspace.db");

// Without a catalogue the Administrative Templates tab only ever renders its
// empty state, so the ADMX authoring surface was unreachable from browser
// tests. Seed a synthetic pack shaped like a real one: 2006/07 namespace, a
// policyNamespaces target, and presentation carried in the ADML
// presentationTable rather than inline.
const admxDirectory = join(workspaceDirectory, "PolicyDefinitions");
mkdirSync(admxDirectory, { recursive: true });
writeFileSync(
  join(admxDirectory, "synthetic.admx"),
  `<?xml version="1.0" encoding="utf-8"?>
<policyDefinitions xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <policyNamespaces>
    <target prefix="synthetic" namespace="Synthetic.Policies.Browser" />
  </policyNamespaces>
  <categories>
    <category name="SyntheticBrowserCategory" displayName="$(string.SyntheticBrowserCategory)" />
  </categories>
  <supportedOn>
    <definition name="Supported_Synthetic" displayName="$(string.Supported_Synthetic)" />
  </supportedOn>
  <policies>
    <policy name="SyntheticFeature" class="Machine" key="Software\\Policies\\Synthetic\\Browser"
            displayName="$(string.SyntheticFeature)" explainText="$(string.SyntheticFeature_Explain)"
            valueName="FeatureState" presentation="$(presentation.SyntheticFeature)">
      <parentCategory ref="SyntheticBrowserCategory" />
      <supportedOn ref="Supported_Synthetic" />
      <enabledValue><decimal value="1" /></enabledValue>
      <disabledValue><decimal value="0" /></disabledValue>
      <elements>
        <boolean id="SubOption" valueName="SubOption" />
      </elements>
    </policy>
    <policy name="SyntheticToggle" class="Machine" key="Software\\Policies\\Synthetic\\Browser"
            displayName="$(string.SyntheticToggle)" explainText="$(string.SyntheticToggle_Explain)"
            valueName="ToggleState" presentation="$(presentation.SyntheticToggle)">
      <parentCategory ref="SyntheticBrowserCategory" />
      <supportedOn ref="Supported_Synthetic" />
    </policy>
  </policies>
</policyDefinitions>
`,
);
writeFileSync(
  join(admxDirectory, "synthetic.adml"),
  `<?xml version="1.0" encoding="utf-8"?>
<policyDefinitionResources xmlns="http://schemas.microsoft.com/GroupPolicy/2006/07/PolicyDefinitions">
  <resources>
    <stringTable>
      <string id="SyntheticBrowserCategory">Synthetic Browser Category</string>
      <string id="Supported_Synthetic">Synthetic OS Support</string>
      <string id="SyntheticFeature">Synthetic feature with options</string>
      <string id="SyntheticFeature_Explain">A synthetic policy that has a child element.</string>
      <string id="SyntheticToggle">Synthetic toggle without options</string>
      <string id="SyntheticToggle_Explain">A synthetic policy with no elements at all.</string>
    </stringTable>
    <presentationTable>
      <presentation id="SyntheticFeature">
        <checkBox refId="SubOption">Enable the sub option</checkBox>
      </presentation>
      <presentation id="SyntheticToggle" />
    </presentationTable>
  </resources>
</policyDefinitionResources>
`,
);
const python = process.env.GPO_STUDIO_TEST_PYTHON;
const command = python || (process.env.CI ? "python" : "uv");
const args =
  python || process.env.CI
    ? ["-m", "gpo_studio", "run", "--port", "4173", "--database", database]
    : ["run", "gpo-studio", "run", "--port", "4173", "--database", database];

const server = spawn(command, args, {
  stdio: "inherit",
  env: {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    GPO_STUDIO_ADMX_DIR: admxDirectory,
  },
});

let cleaned = false;
function cleanup() {
  if (!cleaned) {
    cleaned = true;
    rmSync(workspaceDirectory, { recursive: true, force: true });
  }
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => server.kill(signal));
}

server.once("error", (error) => {
  cleanup();
  throw error;
});

server.once("exit", (code, signal) => {
  cleanup();
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 1);
});
