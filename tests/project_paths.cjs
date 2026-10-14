const fs = require("node:fs");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const readLayout = filename => JSON.parse(fs.readFileSync(path.join(projectRoot, filename), "utf8")
  .replace(/^\uFEFF/, ""));
const webuiLayout = readLayout("webui-layout.json");
const sourceLayout = readLayout("source-layout.json");

function webuiAssetPath(relative) {
  return path.join(projectRoot, webuiLayout.assets[relative]);
}

function extensionAssetPath(relative) {
  return path.join(projectRoot, sourceLayout.resource_roots.extension, relative);
}

function scriptSourcePath(relative) {
  const moduleName = relative.replace(/\.py$/, "").replaceAll("/", ".");
  return path.join(projectRoot, sourceLayout.module_files[moduleName]);
}

module.exports = { webuiAssetPath, extensionAssetPath, scriptSourcePath };
