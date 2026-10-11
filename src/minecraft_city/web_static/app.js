"use strict";
const $ = (id) => document.getElementById(id);
const ns = "http://www.w3.org/2000/svg";
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has("token")) {
  sessionStorage.setItem("city-token", fragment.get("token"));
  history.replaceState(null, "", location.pathname);
}
const token = sessionStorage.getItem("city-token") || "";
let state,
  pending = null,
  busy = false,
  mode = "select",
  selection = {},
  view = [0, 0, 256, 256];
const objectURLs = new Map();
async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: "Bearer " + token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const data = await response.json();
    throw new Error(data.error || "処理に失敗しました");
  }
  return response;
}
async function json(path, body) {
  return (await api(path, body)).json();
}
async function picture(id, path, svg = false) {
  const url = URL.createObjectURL(await (await api(path)).blob());
  if (objectURLs.has(id)) URL.revokeObjectURL(objectURLs.get(id));
  objectURLs.set(id, url);
  if (svg) $(id).setAttribute("href", url);
  else $(id).src = url;
}
async function download(path, filename) {
  const url = URL.createObjectURL(await (await api(path)).blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
function message(text, who = "assistant") {
  const p = document.createElement("p");
  p.className = who;
  p.textContent = text;
  $("messages").append(p);
  while ($("messages").children.length > 15) $("messages").firstChild.remove();
  $("messages").scrollTop = $("messages").scrollHeight;
}
function buttons() {
  document.querySelectorAll("button,textarea,select,input").forEach((el) => {
    el.disabled = busy || !state;
  });
  $("refresh").disabled = busy;
  $("undo").disabled = busy || !state?.undo;
  $("approve").disabled = busy || !pending;
}
async function work(label, fn) {
  if (busy) return;
  busy = true;
  buttons();
  $("status").textContent = label;
  try {
    await fn();
    $("status").textContent = "";
  } catch (error) {
    $("status").textContent = error.message;
    message(error.message);
  } finally {
    busy = false;
    buttons();
  }
}
function invalidate() {
  pending = null;
  $("review").hidden = true;
  $("undo-confirm").hidden = true;
  if (state) paint();
}
function options(id, rows, empty) {
  const select = $(id),
    current = select.value;
  select.replaceChildren(new Option(empty, ""));
  rows.forEach((r) => select.add(new Option(r.name, r.id)));
  select.value = rows.some((r) => r.id === current) ? current : "";
}
function polygon(coords, attributes) {
  const node = document.createElementNS(ns, "polygon");
  node.setAttribute("points", coords.map((p) => p.join(",")).join(" "));
  Object.entries(attributes).forEach(([k, v]) => node.setAttribute(k, v));
  return node;
}
function paint() {
  if (!state) return;
  $("district-shapes").replaceChildren();
  $("building-shapes").replaceChildren();
  $("selection-shape").replaceChildren();
  state.districts
    .filter((d) => d.polygon.length)
    .forEach((d) => {
      const selected =
        selection.district === d.id ||
        (!selection.district && selection.ward === d.ward_id);
      $("district-shapes").append(
        polygon(d.polygon, {
          class: "district-hit" + (selected ? " selected" : ""),
          "data-district": d.id,
        }),
      );
    });
  state.buildings.forEach((b) =>
    $("building-shapes").append(
      polygon(b.polygon, {
        class:
          "building-hit" + (selection.building_id === b.id ? " selected" : ""),
        "data-building": b.id,
      }),
    ),
  );
  if (selection.bounds) {
    const [x, z, a, b] = selection.bounds;
    $("selection-shape").append(
      polygon(
        [
          [x, z],
          [a, z],
          [a, b],
          [x, b],
        ],
        { class: "selection-mark" },
      ),
    );
  }
  if (selection.point) {
    const circle = document.createElementNS(ns, "circle");
    circle.setAttribute("cx", selection.point[0]);
    circle.setAttribute("cy", selection.point[1]);
    circle.setAttribute("r", 3);
    circle.setAttribute("class", "selection-mark");
    $("selection-shape").append(circle);
  }
  if (pending)
    changeShapes().forEach((coords) =>
      $("selection-shape").append(polygon(coords, { class: "proposal-mark" })),
    );
  const labels = [];
  if (selection.ward)
    labels.push(state.wards.find((w) => w.id === selection.ward)?.name);
  if (selection.district)
    labels.push(state.districts.find((d) => d.id === selection.district)?.name);
  if (selection.building_id !== undefined) {
    const b = state.buildings.find((b) => b.id === selection.building_id);
    labels.push(`建物 ${b.id} / ${b.kind} / ${b.floors}階`);
  }
  if (selection.point)
    labels.push(`X ${selection.point[0]} / Z ${selection.point[1]}`);
  if (selection.bounds) labels.push("範囲 " + selection.bounds.join(", "));
  $("selected").textContent =
    labels.join(" › ") || "未選択：指示に都市・区・地区名を含めてください";
}
function viewBox() {
  $("map").setAttribute("viewBox", view.join(" "));
}
function zoom(factor, x = view[0] + view[2] / 2, z = view[1] + view[3] / 2) {
  const width = Math.min(state.size * 1.5, Math.max(24, view[2] * factor));
  const ratio = width / view[2];
  view = [x - (x - view[0]) * ratio, z - (z - view[1]) * ratio, width, width];
  viewBox();
}
async function refresh(reset = false) {
  const latest = await json("/api/state");
  if (!state || state.revision !== latest.revision) {
    selection = {};
    invalidate();
  }
  state = latest;
  $("city-name").textContent = state.city_name;
  $("city-stats").textContent =
    `${state.size} × ${state.size} blocks · ${state.buildings.length}棟 · ${state.wards.length}区 · 編集 ${state.history_depth}件`;
  options("ward", state.wards, "区を選択");
  options("district", state.districts, "地区を選択");
  $("ward").value = selection.ward || "";
  $("district").value = selection.district || "";
  $("map-image").setAttribute("width", state.size);
  $("map-image").setAttribute("height", state.size);
  if (reset) view = [0, 0, state.size, state.size];
  viewBox();
  paint();
  await picture("map-image", "/api/files/current/map.png", true);
}
$("ward").onchange = () => {
  selection = $("ward").value ? { ward: $("ward").value } : {};
  options(
    "district",
    state.districts.filter(
      (d) => !selection.ward || d.ward_id === selection.ward,
    ),
    "地区を選択",
  );
  $("district").value = "";
  invalidate();
  paint();
};
$("district").onchange = () => {
  selection = $("ward").value ? { ward: $("ward").value } : {};
  if ($("district").value) selection.district = $("district").value;
  invalidate();
  paint();
};
$("clear").onclick = () => {
  selection = {};
  $("ward").value = "";
  options("district", state.districts, "地区を選択");
  $("district").value = "";
  invalidate();
  paint();
};
document.querySelectorAll("[data-mode]").forEach(
  (b) =>
    (b.onclick = () => {
      mode = b.dataset.mode;
      document
        .querySelectorAll("[data-mode]")
        .forEach((el) => el.classList.toggle("active", el === b));
    }),
);
$("zoom-in").onclick = () => zoom(0.7);
$("zoom-out").onclick = () => zoom(1.4);
$("fit").onclick = () => {
  view = [0, 0, state.size, state.size];
  viewBox();
};
$("refresh").onclick = () => work("地図を更新中…", () => refresh());
$("chat").onsubmit = (e) => {
  e.preventDefault();
  work("配置と衝突を確認しています…", async () => {
    invalidate();
    const instruction = $("instruction").value.trim();
    message(instruction, "user");
    pending = await json("/api/plan", {
      revision: state.revision,
      instruction,
      selection,
      seed: Number($("seed").value),
    });
    const p = pending.plan,
      c = p.changes;
    $("review-summary").textContent =
      `追加 ${c.added_buildings.length}棟 / 外観変更 ${c.changed_buildings.length}棟 / 公園 ${c.added_sites.length}件。道路接続・衝突検査：合格。`;
    $("plan-details").textContent = JSON.stringify(
      {
        instruction: p.instruction,
        intent: p.intent,
        changes: p.changes,
        validation: p.validation,
        assumptions: p.assumptions,
        plan_id: p.plan_id,
      },
      null,
      2,
    );
    await picture("before", `/api/files/reviews/${pending.review}/before.png`);
    await picture("after", `/api/files/reviews/${pending.review}/after.png`);
    paint();
    $("review").hidden = false;
    message(
      "計画ができました。変更前後と補完条件を確認し、承認すると建築されます。",
    );
    $("review").scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
};
$("instruction").addEventListener("input", invalidate);
$("seed").addEventListener("input", invalidate);
$("cancel").onclick = () => {
  invalidate();
  message("計画を見送りました。都市は変更していません。");
};
$("approve").onclick = () =>
  work("承認済みの建築を保存しています…", async () => {
    await json("/api/apply", {
      revision: state.revision,
      review: pending.review,
      approval: pending.plan.plan_id,
    });
    invalidate();
    await refresh();
    message("建築を反映しました。新しいproject.jsonと.schemを保存できます。");
  });
$("undo").onclick = () => {
  $("undo-confirm").hidden = false;
};
$("undo-cancel").onclick = () => {
  $("undo-confirm").hidden = true;
};
$("undo-approve").onclick = () =>
  work("変更前の都市を復元しています…", async () => {
    await json("/api/undo", { revision: state.revision, approval: state.undo });
    invalidate();
    await refresh();
    message("編集前の都市を復元しました。");
  });
$("download-plan").onclick = () =>
  work("計画を保存…", () =>
    download(
      `/api/files/reviews/${pending.review}/plan.json`,
      "edit-plan.json",
    ),
  );
document
  .querySelectorAll("[data-download]")
  .forEach(
    (b) =>
      (b.onclick = () =>
        work("ファイルを準備…", () =>
          download(
            "/api/files/current/" + b.dataset.download,
            b.dataset.download,
          ),
        )),
  );
function changeShapes() {
  const c = pending.plan.changes;
  return [...c.added_buildings, ...c.changed_buildings.map((b) => b.after)]
    .map((b) =>
      b.footprint.length
        ? b.footprint
        : [
            [b.x, b.z],
            [b.x + b.width, b.z],
            [b.x + b.width, b.z + b.depth],
            [b.x, b.z + b.depth],
          ],
    )
    .concat(c.added_sites.map((s) => s.polygon));
}
$("focus-change").onclick = () => {
  const points = changeShapes().flat(),
    xs = points.map((p) => p[0]),
    zs = points.map((p) => p[1]);
  const x0 = Math.min(...xs),
    x1 = Math.max(...xs),
    z0 = Math.min(...zs),
    z1 = Math.max(...zs);
  const width = Math.max(64, x1 - x0 + 32, z1 - z0 + 32);
  view = [(x0 + x1 - width) / 2, (z0 + z1 - width) / 2, width, width];
  viewBox();
  $("map-wrap").scrollIntoView({ behavior: "smooth", block: "center" });
};
$("download-comparison").onclick = () =>
  work("比較画像を保存…", () =>
    download(
      `/api/files/reviews/${pending.review}/comparison.png`,
      "comparison.png",
    ),
  );
const pointers = new Map();
let gesture = null,
  lastPinch = null;
function coords(event) {
  const p = new DOMPoint(event.clientX, event.clientY).matrixTransform(
    $("map").getScreenCTM().inverse(),
  );
  return [p.x, p.y];
}
function bounded(p) {
  return p.map(
    (v) => Math.round(Math.min(state.size, Math.max(0, v)) * 10) / 10,
  );
}
$("map").onpointerdown = (e) => {
  if (busy || !state) return;
  e.preventDefault();
  pointers.set(e.pointerId, [e.clientX, e.clientY]);
  $("map").setPointerCapture(e.pointerId);
  if (pointers.size === 1)
    gesture = {
      start: coords(e),
      client: [e.clientX, e.clientY],
      view: [...view],
      target: e.target,
      moved: false,
      pinched: false,
    };
  else {
    gesture.pinched = true;
    lastPinch = null;
  }
};
$("map").onpointermove = (e) => {
  if (!pointers.has(e.pointerId) || !gesture) return;
  pointers.set(e.pointerId, [e.clientX, e.clientY]);
  if (pointers.size === 2) {
    const [a, b] = [...pointers.values()],
      distance = Math.hypot(a[0] - b[0], a[1] - b[1]);
    const center = coords({
      clientX: (a[0] + b[0]) / 2,
      clientY: (a[1] + b[1]) / 2,
    });
    if (lastPinch && distance > 0) zoom(lastPinch / distance, ...center);
    lastPinch = distance;
    return;
  }
  if (gesture.pinched) return;
  const dx = e.clientX - gesture.client[0],
    dy = e.clientY - gesture.client[1];
  if (Math.hypot(dx, dy) > 5) gesture.moved = true;
  if (mode === "rect") {
    const a = bounded(gesture.start),
      b = bounded(coords(e));
    selection = {
      ...selection,
      bounds: [
        Math.min(a[0], b[0]),
        Math.min(a[1], b[1]),
        Math.max(a[0], b[0]),
        Math.max(a[1], b[1]),
      ],
    };
    delete selection.point;
    delete selection.building_id;
    invalidate();
    paint();
  } else if (mode === "select") {
    const scale = gesture.view[2] / $("map").getBoundingClientRect().width;
    view = [
      gesture.view[0] - dx * scale,
      gesture.view[1] - dy * scale,
      gesture.view[2],
      gesture.view[3],
    ];
    viewBox();
  }
};
function endPointer(e) {
  if (!gesture || !pointers.has(e.pointerId)) return;
  if (!gesture.pinched && !gesture.moved) {
    if (mode === "point") {
      delete selection.bounds;
      delete selection.building_id;
      selection.point = bounded(coords(e));
    } else if (mode === "select") {
      const id = gesture.target.dataset.building,
        district = gesture.target.dataset.district;
      if (id !== undefined) {
        selection = { building_id: Number(id) };
        $("ward").value = "";
        $("district").value = "";
      } else if (district) {
        selection = { district };
        $("district").value = district;
        $("ward").value = "";
      }
    }
    invalidate();
    paint();
  }
  pointers.delete(e.pointerId);
  lastPinch = null;
  if (!pointers.size) gesture = null;
}
$("map").onpointerup = endPointer;
$("map").onpointercancel = () => {
  pointers.clear();
  gesture = null;
  lastPinch = null;
};
$("map").addEventListener(
  "wheel",
  (e) => {
    if (!state || busy) return;
    e.preventDefault();
    zoom(e.deltaY > 0 ? 1.1 : 0.9, ...coords(e));
  },
  { passive: false },
);
work("都市を読み込んでいます…", () => refresh(true));
