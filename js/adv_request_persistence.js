import {
  ADV_REQUEST_SERIALIZED_WIDGET_ORDER,
  ADV_REQUEST_WIDGET_SCHEMA_PROPERTY,
  ADV_REQUEST_WIDGET_SCHEMA_VERSION,
  normalizeAdvRequestWidgetValues,
} from "./adv_request_contract.js";

function widget(node, name) {
  return node?.widgets?.find((candidate) => candidate?.name === name);
}

function setWidgetValuesByName(node, values) {
  ADV_REQUEST_SERIALIZED_WIDGET_ORDER.forEach((name, index) => {
    const found = widget(node, name);
    if (found) found.value = values[index];
  });
}

function markCurrentSchema(node) {
  node.properties = {
    ...(node.properties || {}),
    [ADV_REQUEST_WIDGET_SCHEMA_PROPERTY]: ADV_REQUEST_WIDGET_SCHEMA_VERSION,
  };
}

export function normalizedAdvRequestNodeWidgetValues(node) {
  return normalizeAdvRequestWidgetValues(
    ADV_REQUEST_SERIALIZED_WIDGET_ORDER.map((name) => widget(node, name)?.value),
  );
}

export function normalizeAdvRequestNodeWidgets(node) {
  const values = normalizedAdvRequestNodeWidgetValues(node);
  setWidgetValuesByName(node, values);
  markCurrentSchema(node);
  return values;
}

export function serializeAdvRequestNode(node, nodeData) {
  if (!nodeData || typeof nodeData !== "object") return;
  nodeData.widgets_values = normalizeAdvRequestNodeWidgets(node);
  nodeData.properties = {
    ...(nodeData.properties || {}),
    [ADV_REQUEST_WIDGET_SCHEMA_PROPERTY]: ADV_REQUEST_WIDGET_SCHEMA_VERSION,
  };
}

export function restoreAdvRequestNodeWidgets(node, nodeData) {
  if (!nodeData || typeof nodeData !== "object") {
    normalizeAdvRequestNodeWidgets(node);
    return;
  }
  const properties = nodeData.properties && typeof nodeData.properties === "object"
    ? nodeData.properties
    : {};
  const currentSchema = properties[ADV_REQUEST_WIDGET_SCHEMA_PROPERTY] === ADV_REQUEST_WIDGET_SCHEMA_VERSION
    && Array.isArray(nodeData.widgets_values)
    && nodeData.widgets_values.length === ADV_REQUEST_SERIALIZED_WIDGET_ORDER.length;
  const values = currentSchema
    ? normalizeAdvRequestWidgetValues(nodeData.widgets_values)
    : normalizeAdvRequestWidgetValues();
  setWidgetValuesByName(node, values);
  markCurrentSchema(node);
}

export function disableWidgetSerialization(found) {
  if (!found) return;
  found.serialize = false;
  found.options = { ...(found.options || {}), serialize: false };
}
