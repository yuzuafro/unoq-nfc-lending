"use strict";
// NFC備品管理 Web UI — plain JS, no build step. Talks to /api/v1 on the same origin.

const API = "/api/v1";
const state = { admin: null, view: "dashboard", itemFilter: "", itemQuery: "", userQuery: "",
                loanFilter: { active: false, from: "", to: "", user_id: "", item_id: "" } };
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
function assetNo(no) { return no ? `<span class="uid">${esc(no)}</span>` : ""; }
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
      <label class="field"><span>部署</span><input name="department" value="${esc(u.department)}"></label>
      <label class="field"><span>チーム</span><input name="team" value="${esc(u.team)}"></label>
      <label class="field"><span>メモ</span><textarea name="note" rows="2">${esc(u.note)}</textarea></label>
      ${isNew ? "" : `<label class="check"><input type="checkbox" name="active" ${u.active ? "checked" : ""}> 有効（外すと貸出できなくなります）</label>`}`,
    onSubmit: async (f) => {
      const body = { tag_uid: f.get("tag_uid"), name: f.get("name"), department: f.get("department"),
                     team: f.get("team"), note: f.get("note") };
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
      <label class="field"><span>管理番号</span><input name="asset_no" value="${esc(i.asset_no)}" maxlength="64" placeholder="例 PC-0001"></label>
      <label class="field"><span>カテゴリ</span><input name="category" value="${esc(i.category)}" placeholder="例 PC、工具"></label>
      <label class="field"><span>保管場所</span><input name="location" value="${esc(i.location)}"></label>
      <label class="field"><span>メモ</span><textarea name="note" rows="2">${esc(i.note)}</textarea></label>
      ${isNew ? "" : `<label class="check"><input type="checkbox" name="active" ${i.active ? "checked" : ""}> 有効（外すと貸出できなくなります）</label>`}`,
    onSubmit: async (f) => {
      const body = { tag_uid: f.get("tag_uid"), name: f.get("name"), asset_no: f.get("asset_no"),
                     category: f.get("category"),
                     location: f.get("location"), note: f.get("note") };
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
              <td>${esc(i.name)}${meta(assetNo(i.asset_no), esc(i.location))}</td>
              <td>${esc(i.loan.user_name)}${meta(esc(i.loan.user_team))}</td>
              <td class="num">${since(i.loan.started_at)}${meta(`${when(i.loan.started_at)}〜`)}</td></tr>`).join("")}</tbody></table></div>`
            : `<div class="empty">貸出中の備品はありません</div>`}
        </section>
        <section class="panel"><h2>最近の操作</h2>
          ${s.recent.length ? `<ul class="feed">${s.recent.slice(0, 10).map(e => `<li><time>${when(e.at)}</time>
            ${e.type === "checkout" ? '<span class="badge b-ac">貸出</span>' : '<span class="badge b-ok">返却</span>'}
            <div class="what">${esc(e.item_name)}${meta(esc(e.user_name), esc(e.user_team), e.by_admin && "管理者が操作")}</div>
            ${assetNo(e.item_asset_no)}</li>`).join("")}</ul>`
            : `<div class="empty">まだ操作はありません</div>`}
        </section>
      </div>`;
  },

  async items() {
    const qs = new URLSearchParams({ q: state.itemQuery, status: state.itemFilter });
    const items = await api(`/items?${qs}`);
    const chip = (v, label) => `<button class="chip ${state.itemFilter === v ? "on" : ""}" data-filter="${v}">${label}</button>`;
    return `
      <div class="head"><h1>備品</h1><button class="btn primary admin-only" id="add-item">＋ 備品を追加</button></div>
      <div class="toolbar">
        <input type="search" id="item-q" placeholder="名前・管理番号・カテゴリ・保管場所で検索" value="${esc(state.itemQuery)}">
        <div class="chips">${chip("", "すべて")}${chip("available", "在庫あり")}${chip("on_loan", "貸出中")}${chip("inactive", "無効")}</div>
      </div>
      <section class="panel">
        ${items.length ? `<div class="table-wrap"><table>
          <thead><tr><th>状態</th><th>備品</th><th>管理番号</th><th>カテゴリ</th><th>保管場所</th><th>借りている人</th><th class="admin-only"></th></tr></thead>
          <tbody>${items.map(i => `<tr>
            <td>${badge(i.status)}</td>
            <td>${esc(i.name)}<div class="sub uid">${esc(i.tag_uid)}</div></td>
            <td>${esc(i.asset_no)}</td><td>${esc(i.category)}</td><td>${esc(i.location)}</td>
            <td>${i.loan ? `${esc(i.loan.user_name)}<div class="sub">${when(i.loan.started_at)} から</div>` : ""}</td>
            <td class="actions admin-only">
              ${i.loan ? `<button class="btn small" data-close="${i.loan.loan_id}" data-label="${esc(i.name)}">返却にする</button>` : ""}
              <button class="btn small" data-edit-item="${i.id}">編集</button></td></tr>`).join("")}</tbody></table></div>`
          : `<div class="empty">該当する備品はありません</div>`}
      </section>`;
  },

  async users() {
    const users = await api(`/users?${new URLSearchParams({ q: state.userQuery })}`);
    return `
      <div class="head"><h1>ユーザー</h1><button class="btn primary admin-only" id="add-user">＋ ユーザーを追加</button></div>
      <div class="toolbar"><input type="search" id="user-q" placeholder="名前・部署・チームで検索" value="${esc(state.userQuery)}"></div>
      <section class="panel">
        ${users.length ? `<div class="table-wrap"><table>
          <thead><tr><th>名前</th><th>部署</th><th>チーム</th><th>借りている備品</th><th>状態</th><th class="admin-only"></th></tr></thead>
          <tbody>${users.map(u => `<tr>
            <td>${esc(u.name)}<div class="sub uid">${esc(u.tag_uid)}</div></td>
            <td>${esc(u.department)}</td><td>${esc(u.team)}</td>
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
    for (const k of ["from", "to", "user_id", "item_id"]) if (f[k]) qs.set(k, f[k]);
    const [data, users, items] = await Promise.all([api(`/loans?${qs}`), api("/users"), api("/items")]);
    const opt = (list, sel) => list.map(x => `<option value="${x.id}" ${String(x.id) === String(sel) ? "selected" : ""}>${esc(x.name)}</option>`).join("");
    return `
      <div class="head"><h1>貸出履歴</h1><a class="btn" href="${API}/loans.csv?${qs}" download>CSVで保存</a></div>
      <form class="toolbar" id="loan-filter">
        <input type="date" name="from" value="${esc(f.from)}" aria-label="開始日"> 〜
        <input type="date" name="to" value="${esc(f.to)}" aria-label="終了日">
        <select name="user_id"><option value="">すべてのユーザー</option>${opt(users, f.user_id)}</select>
        <select name="item_id"><option value="">すべての備品</option>${opt(items, f.item_id)}</select>
        <label class="check"><input type="checkbox" name="active" ${f.active ? "checked" : ""}> 貸出中のみ</label>
      </form>
      <section class="panel">
        ${data.loans.length ? `<div class="table-wrap"><table>
          <thead><tr><th>備品</th><th>ユーザー</th><th>貸出</th><th>返却</th><th>理由</th><th class="admin-only"></th></tr></thead>
          <tbody>${data.loans.map(l => `<tr>
            <td>${esc(l.item_name)}${meta(assetNo(l.item_asset_no))}</td>
            <td>${esc(l.user_name)}${meta(esc(l.user_team))}</td>
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
      <section class="panel"><h2>管理者</h2>
        <div style="padding:14px 16px;display:flex;gap:10px;flex-wrap:wrap">
          <button class="btn" id="change-pw">パスワードを変更</button>
          <a class="btn" href="${API}/backup">DB のバックアップをダウンロード</a>
        </div>
        <p class="hint" style="padding:0 16px 14px">バックアップは毎日自動でも保存されます（7世代）。</p>
      </section>`;
  },
};

// ------------------------------------------------------------------ events inside views
view.addEventListener("click", async (e) => {
  const t = e.target.closest("button, [data-filter]");
  if (!t) return;
  const d = t.dataset;
  try {
    if (t.id === "add-item") itemForm();
    else if (t.id === "add-user") userForm();
    else if (d.filter !== undefined) { state.itemFilter = d.filter; render(); }
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
  } catch (err) { toast(err.message); }
});

let searchTimer;
view.addEventListener("input", (e) => {
  if (e.target.id === "item-q" || e.target.id === "user-q") {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      if (e.target.id === "item-q") state.itemQuery = e.target.value; else state.userQuery = e.target.value;
      render({ keepFocus: e.target.id });
    }, 250);
  }
});
view.addEventListener("change", (e) => {
  const form = e.target.closest("#loan-filter");
  if (!form) return;
  const f = new FormData(form);
  state.loanFilter = { active: f.get("active") === "on", from: f.get("from"), to: f.get("to"),
                       user_id: f.get("user_id"), item_id: f.get("item_id") };
  render();
});

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
    if (MESSAGES[e.type]) toast(MESSAGES[e.type](e));
    if (!dlg.open) render();
  };
}

(async () => {
  try { setAdmin((await api("/auth/me")).username); } catch { setAdmin(null); }
  connectLive();
})();
