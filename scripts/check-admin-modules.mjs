import fs from "node:fs/promises";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath, pathToFileURL } from "node:url";

const adminRoot = path.resolve("static/admin");
const assetVersion = "20260728-console-4";
const indexSource = await fs.readFile(
  path.join(adminRoot, "index.html"),
  "utf8",
);
const versionPayload = JSON.parse(
  await fs.readFile(path.join(adminRoot, "version.json"), "utf8"),
);
if (versionPayload.version !== assetVersion) {
  throw new Error("static/admin/version.json does not match the module version.");
}
const adminEntrySource = await fs.readFile(
  path.join(adminRoot, "admin.js"),
  "utf8",
);
if (!adminEntrySource.includes(`const ASSET_VERSION = "${assetVersion}";`)) {
  throw new Error("static/admin/admin.js does not match the module version.");
}
for (const asset of [
  "styles.css",
  "boot.js",
  "admin.js",
  "api.js",
  "ui.js",
]) {
  if (!indexSource.includes(`/admin/${asset}?v=${assetVersion}`)) {
    throw new Error(`static/admin/index.html has a stale ${asset} version.`);
  }
}
const entrySpecifiers = [
  "admin.js",
  `api.js?v=${assetVersion}`,
  `ui.js?v=${assetVersion}`,
  `pages/dashboard.js?v=${assetVersion}`,
  `pages/articles.js?v=${assetVersion}`,
  `pages/editor.js?v=${assetVersion}`,
  `pages/categories.js?v=${assetVersion}`,
  `pages/todos.js?v=${assetVersion}`,
  `pages/analytics.js?v=${assetVersion}`,
];
const moduleCache = new Map();

function checkedPath(moduleUrl) {
  const filePath = fileURLToPath(moduleUrl);
  const relative = path.relative(adminRoot, filePath);
  if (
    relative.startsWith("..")
    || path.isAbsolute(relative)
    || path.extname(filePath) !== ".js"
  ) {
    throw new Error(`Admin module import escapes static/admin: ${moduleUrl.href}`);
  }
  return filePath;
}

async function loadModule(moduleUrl) {
  const key = moduleUrl.href;
  if (moduleCache.has(key)) return moduleCache.get(key);

  const source = await fs.readFile(checkedPath(moduleUrl), "utf8");
  const module = new vm.SourceTextModule(source, { identifier: key });
  moduleCache.set(key, module);
  await module.link((specifier, referencingModule) => {
    const resolved = new URL(specifier, referencingModule.identifier);
    return loadModule(resolved);
  });
  return module;
}

const aggregatorUrl = pathToFileURL(path.join(adminRoot, "__module_check__.mjs"));
const aggregatorSource = entrySpecifiers
  .map((file) => `import ${JSON.stringify(`./${file}`)};`)
  .join("\n");
const aggregator = new vm.SourceTextModule(aggregatorSource, {
  identifier: aggregatorUrl.href,
});

await aggregator.link((specifier, referencingModule) => {
  return loadModule(new URL(specifier, referencingModule.identifier));
});

if (aggregator.status !== "linked") {
  throw new Error(`Admin module graph did not link: ${aggregator.status}`);
}
if (moduleCache.size !== entrySpecifiers.length) {
  throw new Error(
    `Expected ${entrySpecifiers.length} admin modules, linked ${moduleCache.size}.`,
  );
}

const adminUrl = pathToFileURL(path.join(adminRoot, "admin.js"));
const adminSource = await fs.readFile(fileURLToPath(adminUrl), "utf8");
const dynamicRoutePattern = /import\((["'])([^"']+)\1\)\s*\.then\(\(module\)\s*=>\s*module\.([A-Za-z_$][A-Za-z0-9_$]*)\)/g;
let dynamicRouteCount = 0;
for (const match of adminSource.matchAll(dynamicRoutePattern)) {
  dynamicRouteCount += 1;
  const moduleUrl = new URL(match[2], adminUrl);
  const module = await loadModule(moduleUrl);
  const exportName = match[3];
  if (!Reflect.ownKeys(module.namespace).includes(exportName)) {
    throw new Error(`${moduleUrl.href} does not export ${exportName}.`);
  }
}
if (dynamicRouteCount !== 7) {
  throw new Error(`Expected 7 lazy admin routes, found ${dynamicRouteCount}.`);
}

console.log(`Linked ${moduleCache.size} admin ESM modules.`);
