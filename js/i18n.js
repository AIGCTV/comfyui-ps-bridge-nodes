/** Read the same locale catalogs as the host; display strings never become values. */
let catalogs = {};
let readLocale = () => 'en';

export async function initLocalization(app, api) {
  catalogs = await api.getCustomNodesI18n();
  readLocale = () => app.extensionManager.setting.get('Comfy.Locale') || 'en';
}

export function t(message, values = {}) {
  const locale = readLocale();
  const text = catalogs[locale]?.psBridge?.[message] ?? catalogs.en?.psBridge?.[message] ?? message;
  return text.replace(/\{(\w+)\}/g, (match, key) => Object.hasOwn(values, key) ? String(values[key]) : match);
}

export function nodeName(type, fallback = type) {
  return catalogs[readLocale()]?.nodeDefs?.[type]?.display_name ?? catalogs.en?.nodeDefs?.[type]?.display_name ?? fallback;
}

/** Restore translated defaults, retaining user-renamed titles and port labels. */
export function localizeNode(node) {
  const type = node.comfyClass || node.type, base = catalogs.en?.nodeDefs?.[type];
  if (!base) return;
  const all = Object.values(catalogs).map(locale => locale.nodeDefs?.[type]).filter(Boolean);
  const translated = catalogs[readLocale()]?.nodeDefs?.[type] ?? base;
  if (all.some(def => def.display_name === node.title)) node.title = translated.display_name;
  for (const [index, port] of node.outputs.entries()) {
    if (!port.label || port.label === port.name || all.some(def => def.outputs?.[index]?.name === port.label))
      port.label = translated.outputs?.[index]?.name ?? base.outputs?.[index]?.name ?? port.name;
  }
}
