const API = "";
const STORAGE_KEY = "distinct-square-operations-context";
const context = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");

const ids = {
  facility: "current-facility",
  resident: "current-resident",
  requester: "current-requester",
  boss: "current-boss",
  order: "current-order",
};

function requestKey() {
  return globalThis.crypto?.randomUUID?.() || `ui-${Date.now()}-${Math.random()}`;
}

function saveContext() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(context));
}

function setContext(key, value) {
  context[key] = value;
  saveContext();
  syncContext();
}

function syncContext() {
  for (const [key, elementId] of Object.entries(ids)) {
    const element = document.getElementById(elementId);
    element.textContent = context[key] || "Not set";
    element.title = context[key] || "";
  }
  document.querySelectorAll(".order-id-input").forEach((input) => {
    if (context.order) input.value = context.order;
  });
  document.getElementById("task-order-id").value = context.order || "";
  document.getElementById("resident-facility-id").value = context.facility || "";
  document.getElementById("user-facility-id").value = context.facility || "";
  document.getElementById("order-resident-id").value = context.resident || "";
  document.getElementById("order-requester-id").value = context.requester || "";
  document.getElementById("approval-boss-id").value = context.boss || "";
}

function showToast(message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  document.getElementById("toast-region").append(toast);
  window.setTimeout(() => toast.remove(), 5200);
}

function formValues(formData) {
  const values = {};
  formData.forEach((value, key) => { values[key] = value; });
  return values;
}

function errorMessage(data, raw, statusCode) {
  const detail = data && typeof data === "object" ? data.detail : null;
  if (Array.isArray(detail)) {
    return detail
      .map((issue) => `${issue.loc?.slice(-1)[0] || "Request"}: ${issue.msg || "is invalid"}`)
      .join(" ");
  }
  if (typeof detail === "string") return detail;
  return raw || `Request failed with status ${statusCode}`;
}

async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["Idempotency-Key"] = requestKey();
  }
  const response = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const raw = await response.text();
  let data = null;
  try { data = raw ? JSON.parse(raw) : null; } catch { data = raw; }
  if (!response.ok) {
    throw new Error(errorMessage(data, raw, response.status));
  }
  return data;
}

function money(value) {
  if (value === undefined || value === null) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(Number(value));
}

function titleCase(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function setOrderSummary(order) {
  if (!order) return;
  setContext("order", order.id);
  document.querySelector("#order-summary .helper-text").textContent = `${order.resident_name} · ${order.shipping_address}`;
  const pill = document.querySelector("#order-summary .status-pill");
  pill.className = `status-pill ${order.status}`;
  pill.textContent = titleCase(order.status);
  document.getElementById("summary-items").textContent = `${order.items.length} line item${order.items.length === 1 ? "" : "s"}`;
  document.getElementById("summary-subtotal").textContent = money(order.item_subtotal);
  document.getElementById("summary-total").textContent = money(order.total);
  const itemList = document.getElementById("summary-item-list");
  itemList.replaceChildren();
  if (!order.items.length) {
    itemList.append(createCell("Add items to see the draft take shape here.", "li"));
    itemList.firstChild.className = "empty-line-items";
    return;
  }
  order.items.forEach((item) => {
    const line = document.createElement("li");
    const description = document.createElement("span");
    const name = createCell(item.product_name, "span");
    name.className = "line-item-name";
    const detail = createCell(`${item.sku} · ${item.size} · ${item.quantity} × ${money(item.unit_price)}`, "span");
    detail.className = "line-item-detail";
    description.append(name, detail);
    line.append(description, createCell(money(item.subtotal)));
    itemList.append(line);
  });
}

function disableDuring(form, disabled) {
  form.querySelectorAll("button, input, select, textarea").forEach((element) => { element.disabled = disabled; });
}

async function handleForm(form, action) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    disableDuring(form, true);
    try {
      await action(data);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      disableDuring(form, false);
    }
  });
}

async function refreshHealth() {
  const target = document.getElementById("connection-status");
  try {
    const health = await api("/health");
    target.className = "connection online";
    target.innerHTML = `<span class="status-dot"></span> API ${health.status}`;
  } catch {
    target.className = "connection offline";
    target.innerHTML = "<span class=\"status-dot\"></span> API unavailable";
  }
}

function createCell(text, tag = "span") {
  const cell = document.createElement(tag);
  cell.textContent = text;
  return cell;
}

async function refreshCatalog() {
  const target = document.getElementById("catalog-table");
  const count = document.getElementById("catalog-count");
  target.className = "catalog-table empty-state";
  target.textContent = "Loading catalog…";
  try {
    const products = await api("/products");
    count.textContent = `${products.length} item${products.length === 1 ? "" : "s"}`;
    target.replaceChildren();
    target.className = "catalog-table";
    if (!products.length) {
      target.classList.add("empty-state");
      target.textContent = "No products are in the catalog yet.";
      return;
    }
    const header = document.createElement("div");
    header.className = "catalog-row header";
    ["Product", "Size", "Price", "Available", "Stock"].forEach((label) => header.append(createCell(label)));
    target.append(header);
    const inventory = await Promise.all(products.map(async (product) => {
      try { return await api(`/products/${encodeURIComponent(product.sku)}/inventory`); }
      catch { return { available_quantity: 0 }; }
    }));
    products.forEach((product, index) => {
      const row = document.createElement("div");
      row.className = "catalog-row";
      const name = document.createElement("div");
      name.append(createCell(product.name, "strong"), createCell(product.sku, "small"));
      row.append(name, createCell(product.size), createCell(money(product.unit_price)));
      const active = createCell(product.is_active ? "Active" : "Inactive");
      active.className = product.is_active ? "active-label" : "inactive-label";
      row.append(active, createCell(String(inventory[index].available_quantity)));
      target.append(row);
    });
  } catch (error) {
    target.className = "catalog-table empty-state";
    target.textContent = error.message;
    showToast(error.message, "error");
  }
}

async function loadOrder(orderId) {
  const order = await api(`/orders/${encodeURIComponent(orderId)}`);
  setOrderSummary(order);
  return order;
}

function nextStatus(status) {
  return { pending: "picking", picking: "labeling", labeling: "packing", packing: "ready_to_ship" }[status];
}

async function loadTasks() {
  const orderId = document.getElementById("task-order-id").value.trim() || context.order;
  if (!orderId) throw new Error("Create or load an order before loading fulfillment tasks");
  const target = document.getElementById("task-list");
  target.className = "task-grid";
  target.replaceChildren();
  const tasks = await api(`/orders/${encodeURIComponent(orderId)}/fulfillment-tasks`);
  if (!tasks.length) {
    target.className = "task-grid empty-state";
    target.textContent = "No tasks yet. The order needs Boss approval and reservable inventory.";
    return;
  }
  tasks.forEach((task, index) => {
    const card = document.createElement("article");
    card.className = "task-card";
    const header = document.createElement("header");
    const heading = createCell(`Task ${index + 1}`, "h3");
    const pill = createCell(titleCase(task.status));
    pill.className = `status-pill ${task.status}`;
    header.append(heading, pill);
    const description = createCell("Reserved items ready for warehouse work.", "p");
    const list = document.createElement("ul");
    list.className = "task-items";
    task.items.forEach((item) => list.append(createCell(`${item.quantity} × ${item.product_name} (${item.sku})`, "li")));
    card.append(header, description, list);
    const next = nextStatus(task.status);
    if (next) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `Mark ${titleCase(next)}`;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await api(`/fulfillment-tasks/${task.id}/advance`, { method: "POST", body: { next_status: next } });
          showToast(`Task moved to ${titleCase(next)}.`);
          await loadTasks();
          await loadOrder(orderId);
        } catch (error) {
          showToast(error.message, "error");
          button.disabled = false;
        }
      });
      card.append(button);
    } else {
      card.append(createCell(task.status === "cancelled" ? "This task was stopped by an approved cancellation." : "This task has reached its terminal V0 state.", "p"));
    }
    target.append(card);
  });
}

function bindForms() {
  handleForm(document.getElementById("facility-form"), async (data) => {
    const facility = await api("/facilities", { method: "POST", body: formValues(data) });
    setContext("facility", facility.id);
    showToast(`Saved ${facility.name}.`);
  });

  handleForm(document.getElementById("user-form"), async (data) => {
    const body = formValues(data);
    if (!body.facility_id) delete body.facility_id;
    const user = await api("/users", { method: "POST", body });
    if (user.role === "authorized_requester") setContext("requester", user.id);
    if (user.role === "boss") setContext("boss", user.id);
    showToast(`Saved ${user.full_name}.`);
  });

  handleForm(document.getElementById("resident-form"), async (data) => {
    const resident = await api("/residents", { method: "POST", body: formValues(data) });
    setContext("resident", resident.id);
    showToast(`Saved ${resident.full_name}.`);
  });

  handleForm(document.getElementById("product-form"), async (data) => {
    const body = formValues(data);
    body.is_active = data.get("is_active") === "on";
    const product = await api("/products", { method: "POST", body });
    document.getElementById("stock-sku").value = product.sku;
    showToast(`Added ${product.name} to the catalog.`);
    await refreshCatalog();
  });

  handleForm(document.getElementById("stock-form"), async (data) => {
    const sku = data.get("sku").trim();
    const inventory = await api(`/products/${encodeURIComponent(sku)}/inventory`, {
      method: "POST", body: { quantity: Number(data.get("quantity")) },
    });
    showToast(`Inventory updated: ${inventory.available_quantity} units available.`);
    await refreshCatalog();
  });

  handleForm(document.getElementById("order-form"), async (data) => {
    const order = await api("/orders", { method: "POST", body: formValues(data) });
    setOrderSummary(order);
    showToast("Draft order created.");
  });

  handleForm(document.getElementById("item-form"), async (data) => {
    const body = formValues(data);
    body.quantity = Number(body.quantity);
    const order = await api(`/orders/${encodeURIComponent(body.order_id)}/items`, { method: "POST", body: { product_sku: body.product_sku, quantity: body.quantity } });
    setOrderSummary(order);
    showToast("Item added to draft.");
  });

  handleForm(document.getElementById("confirm-form"), async (data) => {
    const body = formValues(data);
    const order = await api(`/orders/${encodeURIComponent(body.order_id)}/confirm`, { method: "POST", body: { shipping_cost: body.shipping_cost } });
    setOrderSummary(order);
    showToast("Order submitted for Boss approval.");
  });

  handleForm(document.getElementById("approval-form"), async (data) => {
    const body = formValues(data);
    body.allow_partial_fulfillment = data.get("allow_partial_fulfillment") === "on";
    const order = await api(`/orders/${encodeURIComponent(body.order_id)}/approve`, { method: "POST", body: { boss_id: body.boss_id, allow_partial_fulfillment: body.allow_partial_fulfillment } });
    setOrderSummary(order);
    showToast("Order approved. Inventory and task automation has run.");
    document.getElementById("task-order-id").value = order.id;
    await loadTasks();
  });

  document.getElementById("reject-order").addEventListener("click", async () => {
    const form = document.getElementById("approval-form");
    const orderId = form.elements.order_id.value.trim();
    const bossId = form.elements.boss_id.value.trim();
    if (!orderId || !bossId) { showToast("Enter the order ID and Boss ID before rejecting.", "error"); return; }
    if (!window.confirm("Reject this order?")) return;
    try {
      const order = await api(`/orders/${encodeURIComponent(orderId)}/reject`, { method: "POST", body: { boss_id: bossId } });
      setOrderSummary(order);
      showToast("Order rejected.");
    } catch (error) { showToast(error.message, "error"); }
  });

  handleForm(document.getElementById("order-lookup-form"), async (data) => {
    const order = await loadOrder(data.get("order_id").trim());
    showToast(`Loaded ${titleCase(order.status)} order.`);
  });

  document.getElementById("load-tasks").addEventListener("click", async () => {
    try { await loadTasks(); } catch (error) { showToast(error.message, "error"); }
  });
}

function bindNavigation() {
  document.querySelectorAll(".nav-link").forEach((link) => {
    link.addEventListener("click", () => {
      document.querySelectorAll(".nav-link").forEach((item) => item.classList.remove("active"));
      link.classList.add("active");
    });
  });
  document.getElementById("clear-workspace").addEventListener("click", () => {
    Object.keys(context).forEach((key) => delete context[key]);
    saveContext();
    syncContext();
    showToast("Saved workspace IDs cleared.", "info");
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  syncContext();
  bindForms();
  bindNavigation();
  await refreshHealth();
  await refreshCatalog();
  if (context.order) {
    try { await loadOrder(context.order); } catch { /* Stale browser state is harmless. */ }
  }
});
