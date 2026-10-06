"use strict";
// NFC備品管理 Web UI — plain JS, no build step. Talks to /api/v1 on the same origin.

const API = "/api/v1";
const state = { admin: null, view: "dashboard", itemActive: "true", itemLoan: "", itemQuery: "", userQuery: "", userFilter: "true",
                loanFilter: { active: false, from: "", to: "", user_q: "", item_q: "" } };
const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");

// ------------------------------------------------------------------ helpers
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const fmt = new Intl.DateTimeFormat("ja-JP", { timeZone: "Asia/Tokyo", month: "numeric", day: "numeric",
                                                hour: "2-digit", minute: "2-digit" });
const fmtFull = new Intl.DateTimeFormat("ja-JP", { timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit",
                                                    day: "2-digit", hour: "2-digit", minute: "2-digit" });
function when(iso, full = false) { return iso ? (full ? fmtFull : fmt).format(new Date(iso)) : "—"; }
// Secondary line under a name: non-empty parts joined with " · " (asset numbers in mono).
function meta(...parts) {
  const html = parts.filter(Boolean).join('<span class="sep">·</span>');
  return html ? `<div class="sub">${html}</div>` : "";
}
function assetNo(no) { return no && on("items", "asset_no") ? `<span class="uid">${esc(no)}</span>` : ""; }

// Site-customizable attribute fields (設定 → 項目名): label and whether the field is used.
const FIELD_KEYS = { users: ["department", "team"], items: ["asset_no", "category", "location"] };
const FIELD_DEFAULTS = { department: "部署", team: "チーム", asset_no: "管理番号", category: "カテゴリ", location: "保管場所" };
state.fields = Object.fromEntries(Object.entries(FIELD_KEYS).map(([kind, keys]) =>
  [kind, Object.fromEntries(keys.map(k => [k, { label: FIELD_DEFAULTS[k], enabled: true }]))]));  // until loaded
async function loadFields() { state.fields = await api("/fields"); }
function label(kind, key) { return state.fields[kind][key].label; }
function on(kind, key) { return state.fields[kind][key].enabled; }
// `text` only when the field is in use (for meta() lines and table cells).
function ifOn(kind, key, text) { return on(kind, key) ? text : ""; }
function th(kind, key) { return ifOn(kind, key, `<th>${esc(label(kind, key))}</th>`); }
function td(kind, key, value) { return ifOn(kind, key, `<td>${esc(value)}</td>`); }
// Form inputs for the fields in use; `extra` adds attributes per key (maxlength, placeholder).
function fieldInputs(kind, obj, extra = {}) {
  return FIELD_KEYS[kind].filter(k => on(kind, k)).map(k => `<label class="field"><span>${esc(label(kind, k))}</span>
    <input name="${k}" value="${esc(obj[k])}" ${extra[k] || ""}></label>`).join("");
}
function fieldValues(kind, f) {
  return Object.fromEntries(FIELD_KEYS[kind].filter(k => on(kind, k)).map(k => [k, f.get(k)]));
}
function searchHint(kind) {
  return ["名前", ...FIELD_KEYS[kind].filter(k => on(kind, k)).map(k => label(kind, k))].join("・") + "で検索";
}
function since(iso) {
  // clamp: a browser clock slightly behind the board would otherwise show "-1分" right after checkout
  const min = Math.max(0, Math.floor((Date.now() - new Date(iso)) / 60000));
  if (min < 60) return `${min}分`;
  const h = Math.floor(min / 60);
  return h < 24 ? `${h}時間${min % 60}分` : `${Math.floor(h / 24)}日${h % 24}時間`;
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401 && state.admin && path !== "/auth/login") {
    setAdmin(null);
  }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    const msg = data?.detail?.message || data?.detail || `エラー（${res.status}）`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function toast(text) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 4000);
}

function badge(status) {
  return { available: '<span class="badge b-ok">在庫あり</span>',
           on_loan: '<span class="badge b-ac">貸出中</span>',
           inactive: '<span class="badge b-mu">無効</span>' }[status] || "";
}
const REASON = { return: "返却", transfer: "切替", admin: "管理者が返却" };

// ------------------------------------------------------------------ dialog
const dlg = $("#dialog");
let dialogSubmit = null;
let dialogClose = null;

function openDialog({ title, body, ok = "保存", onSubmit, onClose, hideCancel = false }) {
  $("#dialog-title").textContent = title;
  $("#dialog-body").innerHTML = body;
  $("#dialog-ok").textContent = ok;
  $("#dialog-cancel").hidden = hideCancel;
  $("#dialog-error").hidden = true;
  dialogSubmit = onSubmit;
  dialogClose = onClose || null;
  if (!dlg.open) dlg.showModal();  // may replace the contents of an open dialog
  $("#dialog-body input:not([readonly]), #dialog-body textarea")?.focus();
}
function closeDialog() {
  dlg.close();
}
dlg.addEventListener("close", () => { dialogClose?.(); dialogClose = null; });
$("#dialog-cancel").addEventListener("click", closeDialog);
$("#dialog-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!dialogSubmit) return closeDialog();
  const btn = $("#dialog-ok");
  btn.disabled = true;
  try {
    const keepOpen = await dialogSubmit(new FormData(e.target));
    if (!keepOpen) closeDialog();
  } catch (err) {
    $("#dialog-error").textContent = err.message;
    $("#dialog-error").hidden = false;
  } finally {
    btn.disabled = false;
  }
});

// ------------------------------------------------------------------ auth
function setAdmin(name) {
  state.admin = name;
  document.body.classList.toggle("admin", !!name);
  $("#who").textContent = name ? `${name}（管理者）` : "";
  $("#login-btn").hidden = !!name;
  $("#logout-btn").hidden = !name;
  if (!name && ["unknown", "settings"].includes(state.view)) location.hash = "#/dashboard";
  render();
}

$("#login-btn").addEventListener("click", () => openDialog({
  title: "管理者ログイン", ok: "ログイン",
  body: `<label class="field"><span>ユーザー名</span><input name="username" value="admin" autocomplete="username" required></label>
         <label class="field"><span>パスワード</span><input name="password" type="password" autocomplete="current-password" required></label>`,
  onSubmit: async (f) => {
    const r = await api("/auth/login", { method: "POST", body: { username: f.get("username"), password: f.get("password") } });
    setAdmin(r.username);
    toast("ログインしました");
  },
}));
$("#logout-btn").addEventListener("click", async () => {
  await api("/auth/logout", { method: "POST" });
  setAdmin(null);
  toast("ログアウトしました");
});

// ------------------------------------------------------------------ tag reader (registration mode)
function tagField(value = "") {
  return `<div class="field"><span>タグUID</span>
    <div class="row"><input name="tag_uid" value="${esc(value)}" placeholder="例 04A1B2C3D4E5F6" required autocomplete="off" class="uid">
    <button type="button" class="btn" id="read-tag">タグを読み取る</button></div>
    <p class="hint" id="read-hint">［タグを読み取る］を押してから、端末にタグをタッチしてください（60秒間）。</p></div>`;
}

function wireTagReader() {
  let sessionId = null, timer = null;
  const btn = $("#read-tag"), hint = $("#read-hint"), input = $("#dialog-body input[name=tag_uid]");
  const stop = (cancel) => {
    clearInterval(timer);
    timer = null;
    if (cancel && sessionId) api(`/registration-sessions/${sessionId}`, { method: "DELETE" }).catch(() => {});
    sessionId = null;
    btn.textContent = "タグを読み取る";
  };
  btn.addEventListener("click", async () => {
    if (timer) { stop(true); hint.textContent = "読み取りを中止しました。"; return; }
    hint.className = "hint";
    try {
      const s = await api("/registration-sessions", { method: "POST" });
      sessionId = s.id;
      btn.textContent = "中止";
      const tick = async () => {
        const left = Math.max(0, Math.round(s.expires_at - Date.now() / 1000));
        hint.textContent = `端末にタグをタッチしてください…（残り${left}秒）`;
        const cur = await api(`/registration-sessions/${sessionId}`).catch(() => null);
        if (!cur || cur.status === "waiting") return;
        stop(false);
        if (cur.status === "captured") {
          input.value = cur.uid;
          if (cur.existing) {
            hint.className = "hint warn";
            hint.textContent = `このタグはすでに${cur.existing.kind === "user" ? "ユーザー" : "備品"}「${cur.existing.name}」に登録されています。`;
          } else {
            hint.textContent = "タグを読み取りました。";
          }
        } else {
          hint.textContent = cur.status === "expired" ? "時間切れです。もう一度押してください。" : "読み取りは中止されました。";
        }
      };
      timer = setInterval(tick, 1000);
      tick();
    } catch (err) {
      hint.textContent = err.message;
    }
  });
  return () => stop(true);  // cancel when the dialog closes
}

// ------------------------------------------------------------------ forms
function userForm(u = {}) {
  const isNew = !u.id;
  openDialog({
    title: isNew ? "ユーザーを追加" : "ユーザーを編集",
    body: `${tagField(u.tag_uid)}
      <label class="field"><span>名前</span><input name="name" value="${esc(u.name)}" required maxlength="100"></label>
      ${fieldInputs("users", u)}
      <label class="field"><span>メモ</span><textarea name="note" rows="2">${esc(u.note)}</textarea></label>
      ${isNew ? "" : `<label class="check"><input type="checkbox" name="active" ${u.active ? "checked" : ""}> 有効（外すと貸出できなくなります）</label>`}`,
    onSubmit: async (f) => {
      const body = { tag_uid: f.get("tag_uid"), name: f.get("name"), ...fieldValues("users", f), note: f.get("note") };
      if (!isNew) body.active = f.get("active") === "on";
      await api(isNew ? "/users" : `/users/${u.id}`, { method: isNew ? "POST" : "PATCH", body });
      toast(isNew ? "ユーザーを追加しました" : "ユーザーを更新しました");
      render();
    },
    onClose: null,
  });
  dialogClose = wireTagReader();
}

function itemForm(i = {}) {
  const isNew = !i.id;
  openDialog({
    title: isNew ? "備品を追加" : "備品を編集",
    body: `${tagField(i.tag_uid)}
      <label class="field"><span>名前</span><input name="name" value="${esc(i.name)}" required maxlength="100"></label>
      ${fieldInputs("items", i, { asset_no: 'maxlength="64"' })}
      <label class="field"><span>メモ</span><textarea name="note" rows="2">${esc(i.note)}</textarea></label>
      ${isNew ? "" : `<label class="check"><input type="checkbox" name="active" ${i.active ? "checked" : ""}> 有効（外すと貸出できなくなります）</label>`}`,
    onSubmit: async (f) => {
      const body = { tag_uid: f.get("tag_uid"), name: f.get("name"), ...fieldValues("items", f), note: f.get("note") };
      if (!isNew) body.active = f.get("active") === "on";
      await api(isNew ? "/items" : `/items/${i.id}`, { method: isNew ? "POST" : "PATCH", body });
      toast(isNew ? "備品を追加しました" : "備品を更新しました");
      render();
    },
  });
  dialogClose = wireTagReader();
}

async function closeLoan(loanId, label) {
  if (!confirm(`${label} を返却済みにしますか？`)) return;
  try {
    await api(`/loans/${loanId}/close`, { method: "POST" });
    toast("返却済みにしました");
    render();
  } catch (err) { toast(err.message); }
}

// ------------------------------------------------------------------ views
const views = {
  async dashboard() {
    const s = await api("/summary");
    const items = await api("/items?status=on_loan");
    items.sort((a, b) => a.loan.started_at.localeCompare(b.loan.started_at));
    return `
      <div class="head"><h1>ダッシュボード</h1></div>
      <div class="cards">
        <div class="card ok"><div class="n">${s.available}</div><div class="l">在庫あり</div></div>
        <div class="card ac"><div class="n">${s.on_loan}</div><div class="l">貸出中</div></div>
        <div class="card"><div class="n">${s.users_active}</div><div class="l">ユーザー</div></div>
        ${state.admin ? `<a class="card wn" href="#/unknown" style="text-decoration:none;color:inherit"><div class="n">${s.unknown_tags}</div><div class="l">未登録タグ</div></a>` : ""}
      </div>
      <div class="grid2">
        <section class="panel"><h2>貸出中の備品</h2>
          ${items.length ? `<div class="table-wrap"><table class="loans">
            <thead><tr><th>備品</th><th>借りている人</th><th class="num">経過</th></tr></thead>
            <tbody>${items.map(i => `<tr>
              <td>${esc(i.name)}${meta(assetNo(i.asset_no), ifOn("items", "location", esc(i.location)))}</td>
              <td>${esc(i.loan.user_name)}${meta(ifOn("users", "team", esc(i.loan.user_team)))}</td>
              <td class="num">${since(i.loan.started_at)}${meta(`${when(i.loan.started_at)}〜`)}</td></tr>`).join("")}</tbody></table></div>`
            : `<div class="empty">貸出中の備品はありません</div>`}
        </section>
        <section class="panel"><h2>最近の操作</h2>
          ${s.recent.length ? `<ul class="feed">${s.recent.slice(0, 10).map(e => `<li><time>${when(e.at)}</time>
            ${e.type === "checkout" ? '<span class="badge b-ac">貸出</span>' : '<span class="badge b-ok">返却</span>'}
            <div class="what">${esc(e.item_name)}${meta(esc(e.user_name), ifOn("users", "team", esc(e.user_team)), e.by_admin && "管理者が操作")}</div>
            ${assetNo(e.item_asset_no)}</li>`).join("")}</ul>`
            : `<div class="empty">まだ操作はありません</div>`}
        </section>
      </div>`;
  },

  async items() {
    const qs = new URLSearchParams({ q: state.itemQuery, loan: state.itemLoan });
    if (state.itemActive) qs.set("active", state.itemActive);
    const items = await api(`/items?${qs}`);
    const chip = (axis, v, label) => `<button class="chip ${state[axis] === v ? "on" : ""}" data-filter="${axis}:${v}">${label}</button>`;
    return `
      <div class="head"><h1>備品</h1><button class="btn primary admin-only" id="add-item">＋ 備品を追加</button></div>
      <div class="toolbar">
        <input type="search" id="item-q" placeholder="${esc(searchHint("items"))}" value="${esc(state.itemQuery)}">
        <div class="chips" role="group" aria-label="有効・無効">${chip("itemActive", "true", "有効")}${chip("itemActive", "false", "無効")}${chip("itemActive", "", "すべて")}</div>
        <div class="chips" role="group" aria-label="貸出状況">${chip("itemLoan", "available", "在庫あり")}${chip("itemLoan", "on_loan", "貸出中")}${chip("itemLoan", "", "すべて")}</div>
      </div>
      <section class="panel">
        ${items.length ? `<div class="table-wrap"><table>
          <thead><tr><th>状態</th><th>備品</th>${FIELD_KEYS.items.map(k => th("items", k)).join("")}<th>借りている人</th><th class="admin-only"></th></tr></thead>
          <tbody>${items.map(i => `<tr>
            <td>${badge(i.status)}</td>
            <td>${esc(i.name)}<div class="sub uid">${esc(i.tag_uid)}</div></td>
            ${FIELD_KEYS.items.map(k => td("items", k, i[k])).join("")}
            <td>${i.loan ? `${esc(i.loan.user_name)}<div class="sub">${when(i.loan.started_at)} から</div>` : ""}</td>
            <td class="actions admin-only">
              ${i.loan ? `<button class="btn small" data-close="${i.loan.loan_id}" data-label="${esc(i.name)}">返却にする</button>` : ""}
              <button class="btn small" data-edit-item="${i.id}">編集</button></td></tr>`).join("")}</tbody></table></div>`
          : `<div class="empty">該当する備品はありません</div>`}
      </section>`;
  },

  async users() {
    const qs = new URLSearchParams({ q: state.userQuery });
    if (state.userFilter) qs.set("active", state.userFilter);
    const users = await api(`/users?${qs}`);
    const chip = (v, label) => `<button class="chip ${state.userFilter === v ? "on" : ""}" data-user-filter="${v}">${label}</button>`;
    return `
      <div class="head"><h1>ユーザー</h1><button class="btn primary admin-only" id="add-user">＋ ユーザーを追加</button></div>
      <div class="toolbar">
        <input type="search" id="user-q" placeholder="${esc(searchHint("users"))}" value="${esc(state.userQuery)}">
        <div class="chips">${chip("true", "有効")}${chip("false", "無効")}${chip("", "すべて")}</div>
      </div>
      <section class="panel">
        ${users.length ? `<div class="table-wrap"><table>
          <thead><tr><th>名前</th>${FIELD_KEYS.users.map(k => th("users", k)).join("")}<th>借りている備品</th><th>状態</th><th class="admin-only"></th></tr></thead>
          <tbody>${users.map(u => `<tr>
            <td>${esc(u.name)}<div class="sub uid">${esc(u.tag_uid)}</div></td>
            ${FIELD_KEYS.users.map(k => td("users", k, u[k])).join("")}
            <td>${u.loans.length ? u.loans.map(l => `${esc(l.item_name)} <span class="sub">${when(l.started_at)}〜</span>`).join("<br>") : '<span class="sub">なし</span>'}</td>
            <td>${u.active ? '<span class="badge b-ok">有効</span>' : '<span class="badge b-mu">無効</span>'}</td>
            <td class="actions admin-only"><button class="btn small" data-edit-user="${u.id}">編集</button></td></tr>`).join("")}</tbody></table></div>`
          : `<div class="empty">該当するユーザーはいません</div>`}
      </section>`;
  },

  async loans() {
    const f = state.loanFilter;
    const qs = new URLSearchParams();
    if (f.active) qs.set("active", "true");
    for (const k of ["from", "to", "user_q", "item_q"]) if (f[k]) qs.set(k, f[k]);
    const [data, users, items] = await Promise.all([api(`/loans?${qs}`), api("/users"), api("/items")]);
    // Suggestions for the search boxes: names plus the values of the fields in use, labelled by field.
    const suggest = (kind, list) => {
      const seen = new Set();
      return [["name", "名前"], ...FIELD_KEYS[kind].filter(k => on(kind, k)).map(k => [k, label(kind, k)])]
        .flatMap(([k, lbl]) => list.map(x => [x[k], lbl]))
        .filter(([v]) => v && !seen.has(v) && seen.add(v))
        .map(([v, lbl]) => `<option value="${esc(v)}" label="${esc(lbl)}"></option>`).join("");
    };
    return `
      <div class="head"><h1>貸出履歴</h1><a class="btn" href="${API}/loans.csv?${qs}" download>CSVで保存</a></div>
      <form class="toolbar" id="loan-filter">
        ${periodPicker(f.from, f.to)}
        <input type="search" name="user_q" id="loan-user-q" list="loan-users" value="${esc(f.user_q)}"
               placeholder="ユーザー（${esc(searchHint("users").replace(/で検索$/, ""))}）">
        <datalist id="loan-users">${suggest("users", users)}</datalist>
        <input type="search" name="item_q" id="loan-item-q" list="loan-items" value="${esc(f.item_q)}"
               placeholder="備品（${esc(searchHint("items").replace(/で検索$/, ""))}）">
        <datalist id="loan-items">${suggest("items", items)}</datalist>
        <label class="check"><input type="checkbox" name="active" ${f.active ? "checked" : ""}> 貸出中のみ</label>
      </form>
      <section class="panel">
        ${data.loans.length ? `<div class="table-wrap"><table>
          <thead><tr><th>備品</th><th>ユーザー</th><th>貸出</th><th>返却</th><th>理由</th><th class="admin-only"></th></tr></thead>
          <tbody>${data.loans.map(l => `<tr>
            <td>${esc(l.item_name)}${meta(assetNo(l.item_asset_no))}</td>
            <td>${esc(l.user_name)}${meta(ifOn("users", "team", esc(l.user_team)))}</td>
            <td>${when(l.started_at, true)}</td>
            <td>${l.ended_at ? when(l.ended_at, true) : '<span class="badge b-ac">貸出中</span>'}</td>
            <td>${esc(REASON[l.end_reason] || "")}</td>
            <td class="actions admin-only">${l.ended_at ? "" : `<button class="btn small" data-close="${l.id}" data-label="${esc(l.item_name)}">返却にする</button>`}</td>
          </tr>`).join("")}</tbody></table></div>
          ${data.total > data.loans.length ? `<div class="empty">新しい順に${data.loans.length}件を表示（全${data.total}件）。すべては CSV で確認できます</div>` : ""}`
          : `<div class="empty">該当する履歴はありません</div>`}
      </section>`;
  },

  async unknown() {
    const tags = await api("/unknown-tags");
    return `
      <div class="head"><h1>未登録タグ</h1></div>
      <p class="hint">端末でタッチされた、まだ登録されていないタグです。ユーザーか備品として登録できます。</p>
      <section class="panel">
        ${tags.length ? `<div class="table-wrap"><table>
          <thead><tr><th>タグUID</th><th>初回</th><th>最終</th><th>端末</th><th></th></tr></thead>
          <tbody>${tags.map(t => `<tr><td class="uid">${esc(t.uid)}</td>
            <td>${when(t.first_seen_at)}</td><td>${when(t.last_seen_at)}</td><td>${esc(t.device_id)}</td>
            <td class="actions"><button class="btn small" data-reg-user="${esc(t.uid)}">ユーザーとして登録</button>
              <button class="btn small" data-reg-item="${esc(t.uid)}">備品として登録</button>
              <button class="btn small danger" data-del-tag="${esc(t.uid)}">削除</button></td></tr>`).join("")}</tbody></table></div>`
          : `<div class="empty">未登録タグはありません</div>`}
      </section>`;
  },

  async settings() {
    const devices = await api("/devices");
    return `
      <div class="head"><h1>設定</h1></div>
      <section class="panel"><h2>端末</h2>
        <div class="table-wrap"><table>
          <thead><tr><th>端末ID</th><th>名前</th><th>最終通信</th><th>リーダー</th><th></th></tr></thead>
          <tbody>${devices.map(d => {
            const online = d.last_seen_at && Date.now() - new Date(d.last_seen_at) < 3 * 60000;
            const reader = d.reader_ok === true ? '<span class="badge b-ok">接続</span>' : d.reader_ok === false ? '<span class="badge b-ng">未接続</span>' : '<span class="badge b-mu">不明</span>';
            return `<tr><td class="uid">${esc(d.id)}</td><td>${esc(d.name)}</td>
              <td>${d.last_seen_at ? `${when(d.last_seen_at)} ${online ? '<span class="badge b-ok">オンライン</span>' : '<span class="badge b-mu">オフライン</span>'}` : "—"}</td>
              <td>${reader}</td>
              <td class="actions"><button class="btn small" data-token="${esc(d.id)}">トークン再発行</button>
                <button class="btn small danger" data-del-device="${esc(d.id)}">削除</button></td></tr>`;
          }).join("")}</tbody></table></div>
        <div style="padding:12px 16px"><button class="btn" id="add-device">＋ 端末を追加</button></div>
      </section>
      <section class="panel"><h2>項目名</h2>
        <p class="hint" style="padding:12px 16px 0">ユーザーと備品の項目名を、運用に合わせて変えられます（例：部署 → 課、チーム → 社員種別）。
          「有効」のチェックを外した項目は、一覧や入力画面に表示されなくなります（入力済みの値は消えません）。</p>
        <div style="padding:12px 16px;display:flex;gap:10px;flex-wrap:wrap"><button class="btn" id="edit-fields">項目名を変更</button></div>
        <div class="table-wrap"><table>
          <thead><tr><th>対象</th><th>項目</th><th>項目名</th><th>有効</th></tr></thead>
          <tbody>${Object.entries(FIELD_KEYS).flatMap(([kind, keys]) => keys.map((k, n) => `<tr>
            <td>${kind === "users" ? "ユーザー" : "備品"}</td><td>項目${n + 1}</td><td>${esc(label(kind, k))}</td>
            <td>${on(kind, k) ? '<span class="badge b-ok">有効</span>' : '<span class="badge b-mu">無効</span>'}</td></tr>`)).join("")}</tbody></table></div>
      </section>
      <section class="panel"><h2>管理者</h2>
        <div style="padding:14px 16px;display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn" id="change-pw">パスワードを変更</button>
          <a class="btn" href="${API}/backup">DB のバックアップをダウンロード</a>
        </div>
        <p class="hint" style="padding:0 16px 14px">バックアップは毎日自動でも保存されます（7世代）。</p>
      </section>`;
  },
};

// ------------------------------------------------------------------ period picker (貸出履歴)
// A calendar popover in the app's own style; the native date picker can't be themed.
// Dates are "YYYY-MM-DD" in JST, the same as the API's from / to.
const ymd = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit" });
const today = () => ymd.format(new Date());
const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const dayStr = (dt) => dt.toISOString().slice(0, 10);
const addDays = (s, n) => { const dt = parseDay(s); dt.setUTCDate(dt.getUTCDate() + n); return dayStr(dt); };
const shortDay = (s) => { const [y, m, d] = s.split("-").map(Number); return `${y === +today().slice(0, 4) ? "" : y + "/"}${m}/${d}`; };
const cal = { open: false, mode: "day", month: "", from: "", to: "" };  // to === "" while picking the second day

function periodLabel(from, to) {
  if (!from && !to) return "すべての期間";
  if (from === to) return shortDay(from);
  return `${from ? shortDay(from) : ""} 〜 ${to ? shortDay(to) : ""}`;
}
function periodPresets() {
  const t = today(), dow = parseDay(t).getUTCDay(), [y, m] = t.split("-").map(Number);
  const prev = new Date(Date.UTC(y, m - 2, 1));
  return [["今日", t, t], ["今週", addDays(t, -dow), t], ["今月", t.slice(0, 8) + "01", t],
          ["先月", dayStr(prev), addDays(t.slice(0, 8) + "01", -1)], ["過去30日", addDays(t, -29), t]];
}
function periodPicker(from, to) {
  return `<div class="period" id="period">
    <input type="hidden" name="from" value="${esc(from)}"><input type="hidden" name="to" value="${esc(to)}">
    <button type="button" class="chip period-btn ${from || to ? "on" : ""}" id="period-btn" aria-haspopup="dialog" aria-expanded="false">
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2" y="3" width="12" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      ${esc(periodLabel(from, to))}</button>
    ${from || to ? `<button type="button" class="period-clear" data-period-clear aria-label="期間をクリア">×</button>` : ""}
    <div class="period-pop" id="period-pop" role="dialog" aria-label="期間を選択" hidden></div>
  </div>`;
}
function calendarHtml() {
  const t = today(), [y, m] = cal.month.split("-").map(Number);
  const first = new Date(Date.UTC(y, m - 1, 1)), days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const lo = cal.from, hi = cal.to || cal.from;
  const cells = Array.from({ length: first.getUTCDay() }, () => `<span></span>`);
  for (let d = 1; d <= days; d++) {
    const s = `${cal.month}-${String(d).padStart(2, "0")}`, dow = (first.getUTCDay() + d - 1) % 7;
    const cls = [s === t && "today", s > t && "future", lo && s >= lo && s <= hi && "in",
                 s === lo && "start", s === hi && "end", dow === 0 && "sun", dow === 6 && "sat"].filter(Boolean).join(" ");
    cells.push(`<button type="button" class="${cls}" data-day="${s}">${d}</button>`);
  }
  if (cal.mode === "month") return monthsHtml(y);
  if (cal.mode === "year") return yearsHtml(y);
  const preset = ([lbl, a, b]) => `<button type="button" class="chip ${a === cal.from && b === cal.to ? "on" : ""}" data-range="${a},${b}">${lbl}</button>`;
  return `<div class="chips">${periodPresets().map(preset).join("")}</div>
    <div class="cal-head">
      <button type="button" class="btn small ghost" data-month="-1" aria-label="前の月">‹</button>
      <button type="button" class="cal-title" data-mode="month" aria-label="年と月を選ぶ">${y}年${m}月 ▾</button>
      <button type="button" class="btn small ghost" data-month="1" aria-label="次の月">›</button></div>
    <div class="cal-grid">${["日", "月", "火", "水", "木", "金", "土"].map(w => `<span class="wd">${w}</span>`).join("")}${cells.join("")}</div>
    <p class="hint">${cal.from && !cal.to ? "終了日を選んでください（同じ日をもう一度押すとその日だけ）" : "開始日を選んでください"}</p>`;
}
// Year view: 12 years per page, ‹ › moves by 12; picking a year goes to the month view.
function yearsHtml(y) {
  const ty = +today().slice(0, 4), start = y - 7;  // the shown year sits in the middle of the page
  const years = Array.from({ length: 12 }, (_, i) => start + i).map(yy => {
    const cls = [yy === y && "start end", yy === ty && "today", yy > ty && "future"];
    return `<button type="button" class="${cls.filter(Boolean).join(" ")}" data-pick-year="${yy}">${yy}</button>`;
  }).join("");
  return `<div class="cal-head">
      <button type="button" class="btn small ghost" data-month="-144" aria-label="前の12年">‹</button>
      <span class="cal-title">${start}〜${start + 11}年</span>
      <button type="button" class="btn small ghost" data-month="144" aria-label="次の12年">›</button></div>
    <div class="cal-months">${years}</div>
    <p class="hint">年を選んでください</p>`;
}
// Month view: ‹ › moves by a year, picking a month goes back to the day view.
function monthsHtml(y) {
  const [ty, tm] = today().split("-").map(Number), [, sm] = cal.month.split("-").map(Number);
  const months = Array.from({ length: 12 }, (_, i) => i + 1).map(mm => {
    const cls = [mm === sm && "start end", y === ty && mm === tm && "today", (y > ty || (y === ty && mm > tm)) && "future"];
    return `<button type="button" class="${cls.filter(Boolean).join(" ")}" data-pick-month="${y}-${String(mm).padStart(2, "0")}">${mm}月</button>`;
  }).join("");
  return `<div class="cal-head">
      <button type="button" class="btn small ghost" data-month="-12" aria-label="前の年">‹</button>
      <button type="button" class="cal-title" data-mode="year" aria-label="年を選ぶ">${y}年 ▾</button>
      <button type="button" class="btn small ghost" data-month="12" aria-label="次の年">›</button></div>
    <div class="cal-months">${months}</div>
    <p class="hint">月を選んでください</p>`;
}
function drawCalendar() { $("#period-pop").innerHTML = calendarHtml(); }
function openCalendar() {
  const f = state.loanFilter;
  Object.assign(cal, { open: true, mode: "day", from: f.from, to: f.to, month: (f.to || f.from || today()).slice(0, 7) });
  drawCalendar();
  $("#period-pop").hidden = false;
  $("#period-btn").setAttribute("aria-expanded", "true");
}
function closeCalendar() {
  cal.open = false;
  const pop = $("#period-pop");
  if (pop) { pop.hidden = true; $("#period-btn").setAttribute("aria-expanded", "false"); }
}
function applyPeriod(from, to) {
  closeCalendar();
  Object.assign(state.loanFilter, { from, to });
  render();
}
function shiftMonth(n) {
  const [y, m] = cal.month.split("-").map(Number);
  cal.month = dayStr(new Date(Date.UTC(y, m - 1 + n, 1))).slice(0, 7);
  drawCalendar();
}
function pickDay(s) {
  if (!cal.from || cal.to) { Object.assign(cal, { from: s, to: "" }); drawCalendar(); return; }
  applyPeriod(s < cal.from ? s : cal.from, s < cal.from ? cal.from : s);
}
// composedPath: a day button is already replaced by the redraw when the click reaches the document.
document.addEventListener("click", (e) => { if (cal.open && !e.composedPath().some(el => el.id === "period")) closeCalendar(); });
document.addEventListener("keydown", (e) => {
  if (cal.open && e.key === "Escape") { closeCalendar(); $("#period-btn")?.focus(); }
});

// ------------------------------------------------------------------ events inside views
view.addEventListener("click", async (e) => {
  const t = e.target.closest("button, [data-filter]");
  if (!t) return;
  const d = t.dataset;
  try {
    if (t.id === "add-item") itemForm();
    else if (t.id === "add-user") userForm();
    else if (d.filter) { const [axis, v] = d.filter.split(":"); state[axis] = v; render(); }
    else if (d.userFilter !== undefined) { state.userFilter = d.userFilter; render(); }
    else if (t.id === "period-btn") cal.open ? closeCalendar() : openCalendar();
    else if (d.periodClear !== undefined) applyPeriod("", "");
    else if (d.range) applyPeriod(...d.range.split(","));
    else if (d.month) shiftMonth(+d.month);
    else if (d.mode) { cal.mode = d.mode; drawCalendar(); }
    else if (d.pickYear) { Object.assign(cal, { mode: "month", month: d.pickYear + cal.month.slice(4) }); drawCalendar(); }
    else if (d.pickMonth) { Object.assign(cal, { mode: "day", month: d.pickMonth }); drawCalendar(); }
    else if (d.day) pickDay(d.day);
    else if (d.editItem) itemForm((await api("/items")).find(i => i.id === +d.editItem));
    else if (d.editUser) userForm((await api("/users")).find(u => u.id === +d.editUser));
    else if (d.close) closeLoan(d.close, d.label);
    else if (d.regUser) userForm({ tag_uid: d.regUser });
    else if (d.regItem) itemForm({ tag_uid: d.regItem });
    else if (d.delTag) { await api(`/unknown-tags/${d.delTag}`, { method: "DELETE" }); render(); }
    else if (t.id === "add-device" || d.token) deviceForm(d.token);
    else if (d.delDevice) {
      if (confirm(`端末 ${d.delDevice} を削除しますか？ この端末からは操作できなくなります。`)) {
        await api(`/devices/${d.delDevice}`, { method: "DELETE" });
        render();
      }
    }
    else if (t.id === "change-pw") passwordForm();
    else if (t.id === "edit-fields") fieldsForm();
  } catch (err) { toast(err.message); }
});

let searchTimer;
view.addEventListener("input", (e) => {
  const id = e.target.id;
  if (!["item-q", "user-q", "loan-user-q", "loan-item-q"].includes(id)) return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    if (id === "item-q") state.itemQuery = e.target.value;
    else if (id === "user-q") state.userQuery = e.target.value;
    else readLoanFilter(e.target.form);
    render({ keepFocus: id });
  }, 250);
});
view.addEventListener("change", (e) => {
  const form = e.target.closest("#loan-filter");
  if (!form || e.target.type === "search") return;  // search boxes are handled on input
  readLoanFilter(form);
  render();
});
function readLoanFilter(form) {
  const f = new FormData(form);
  state.loanFilter = { active: f.get("active") === "on", from: f.get("from"), to: f.get("to"),
                       user_q: f.get("user_q").trim(), item_q: f.get("item_q").trim() };
}

function deviceForm(existingId) {
  openDialog({
    title: existingId ? "トークンを再発行" : "端末を追加",
    ok: "発行",
    body: existingId
      ? `<p>端末 <b class="uid">${esc(existingId)}</b> の新しいトークンを発行します。古いトークンは使えなくなります。</p><input type="hidden" name="id" value="${esc(existingId)}">`
      : `<label class="field"><span>端末ID</span><input name="id" placeholder="例 unoq-2" pattern="[A-Za-z0-9_.\\-]+" required></label>
         <label class="field"><span>名前</span><input name="name" placeholder="例 2階 倉庫"></label>`,
    onSubmit: async (f) => {
      const r = await api("/devices", { method: "POST", body: { id: f.get("id"), name: f.get("name") || "" } });
      openDialog({
        title: "端末トークン", ok: "閉じる", hideCancel: true,
        body: `<p>このトークンは<b>今しか表示されません</b>。端末の <code>data/app.env</code> に次の2行を書いてください。</p>
               <div class="secret">DEVICE_ID=${esc(r.id)}<br>DEVICE_TOKEN=${esc(r.token)}</div>`,
        onSubmit: null,
      });
      render();
      return true;  // the token dialog replaced this one
    },
  });
}

function fieldsForm() {
  const rows = (kind) => FIELD_KEYS[kind].map((k, n) => `<div class="field"><span>項目${n + 1}</span>
    <div class="row"><input name="${kind}.${k}" value="${esc(label(kind, k))}" maxlength="20" placeholder="${esc(FIELD_DEFAULTS[k])}">
    <label class="check"><input type="checkbox" name="${kind}.${k}.enabled" ${on(kind, k) ? "checked" : ""}> 有効</label></div></div>`).join("");
  openDialog({
    title: "項目名を変更",
    body: `<h3>ユーザー</h3>${rows("users")}<h3>備品</h3>${rows("items")}
           <p class="hint">空欄にすると初期値（入力欄に薄く表示されている名前）に戻ります。</p>`,
    onSubmit: async (f) => {
      const body = {};
      for (const [kind, keys] of Object.entries(FIELD_KEYS)) {
        body[kind] = Object.fromEntries(keys.map(k => [k, { label: f.get(`${kind}.${k}`),
                                                             enabled: f.get(`${kind}.${k}.enabled`) === "on" }]));
      }
      state.fields = await api("/fields", { method: "PATCH", body });
      toast("項目名を変更しました");
      render();
    },
  });
}

function passwordForm() {
  openDialog({
    title: "パスワードを変更",
    body: `<label class="field"><span>現在のパスワード</span><input name="current" type="password" autocomplete="current-password" required></label>
           <label class="field"><span>新しいパスワード（8文字以上）</span><input name="new" type="password" minlength="8" autocomplete="new-password" required></label>`,
    onSubmit: async (f) => {
      await api("/auth/password", { method: "POST", body: { current: f.get("current"), new: f.get("new") } });
      toast("パスワードを変更しました");
    },
  });
}

// ------------------------------------------------------------------ routing & rendering
let renderSeq = 0;
async function render(opts = {}) {
  const name = (location.hash.replace(/^#\//, "") || "dashboard");
  state.view = views[name] ? name : "dashboard";
  for (const a of document.querySelectorAll("#nav a")) a.classList.toggle("active", a.dataset.view === state.view);
  const seq = ++renderSeq;
  try {
    const html = await views[state.view]();
    if (seq !== renderSeq) return;  // a newer render started
    view.innerHTML = html;
    if (opts.keepFocus) {
      const el = document.getElementById(opts.keepFocus);
      el?.focus();
      el?.setSelectionRange(el.value.length, el.value.length);
    }
  } catch (err) {
    if (seq === renderSeq) view.innerHTML = `<div class="panel"><div class="empty">読み込めませんでした：${esc(err.message)}</div></div>`;
  }
  if (state.admin) {
    api("/summary").then(s => {
      const c = $("#unknown-count");
      c.textContent = s.unknown_tags;
      c.hidden = !s.unknown_tags;
    }).catch(() => {});
  }
}
window.addEventListener("hashchange", () => render());

// ------------------------------------------------------------------ live updates
const MESSAGES = {
  checkout: e => `${e.user.name} さんが ${e.item.name} を借りました`,
  return: e => `${e.item.name} が返却されました${e.by_admin ? "（管理者）" : ""}`,
  transfer: e => `${e.item.name}：${e.previous_user?.name ?? ""} さん → ${e.user.name} さんに切り替えました`,
  unknown_tag: e => `未登録のタグがタッチされました（${e.uid}）`,
};
function connectLive() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${API}/ws/events`);
  ws.onopen = () => $("#live").classList.add("on");
  ws.onclose = () => { $("#live").classList.remove("on"); setTimeout(connectLive, 3000); };
  ws.onmessage = (m) => {
    const e = JSON.parse(m.data);
    if (e.type === "ping" || e.type === "captured") return;
    if (e.type === "fields_changed") { loadFields().then(() => { if (!dlg.open) render(); }).catch(() => {}); return; }
    if (MESSAGES[e.type]) toast(MESSAGES[e.type](e));
    if (!dlg.open && !cal.open) render();
  };
}

(async () => {
  try { await loadFields(); } catch (err) { toast(err.message); }
  try { setAdmin((await api("/auth/me")).username); } catch { setAdmin(null); }
  connectLive();
})();
