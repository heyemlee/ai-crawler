// Static leads viewer — reads ./data/leads.json and renders a filterable,
// paginated table partitioned by WEEK (one weekly crawl = one period), then by
// category, with a 🆕 badge for newly crawled rows.

const CAT_LABEL = {
  PUBLIC_WORKS: "公共工程",
  COMMERCIAL_TI: "商业改造",
  GC_SUBCONTRACT: "总包/分包",
  RESIDENTIAL_REMODEL: "住宅翻新",
  HOSPITALITY_REMODEL: "酒店翻新",
  RESTAURANT_RETAIL: "餐饮/零售",
  OFFICE_LAB: "办公/实验室",
  OTHER: "其他",
};

const PAGE_SIZE = 50;
const state = { data: null, base: [], filtered: [], page: 1, week: "", cat: "", city: "", contact: "", onlyNew: false, q: "" };

function esc(s) {
  return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function hasContact(l) { return !!(l.email || l.phone); }

async function load() {
  try {
    const r = await fetch("./data/leads.json", { cache: "no-store" });
    state.data = await r.json();
  } catch (e) {
    document.getElementById("stats").textContent = "无法加载 data/leads.json";
    return;
  }
  const weeks = state.data.weeks || [];
  state.week = weeks.length ? weeks[0].key : ""; // default: the latest period
  renderWeeks();
  renderCities();
  bindControls();
  refreshBase();
}

// Recompute the week-scoped base set, then re-render everything that depends on it.
function refreshBase() {
  state.base = state.week ? state.data.leads.filter((l) => l.week === state.week) : state.data.leads;
  renderStats();
  renderChips();
  applyFilters();
}

function renderWeeks() {
  const sel = document.getElementById("week");
  const weeks = state.data.weeks || [];
  sel.innerHTML =
    `<option value="">全部周期 · ${state.data.total.toLocaleString()} 条</option>` +
    weeks
      .map(
        (w, i) =>
          `<option value="${esc(w.key)}" ${w.key === state.week ? "selected" : ""}>` +
          `${esc(w.label)} · ${w.count.toLocaleString()} 条${i === 0 ? "（最新）" : ""}</option>`
      )
      .join("");
}

function renderCities() {
  const sel = document.getElementById("city");
  state.data.cities.forEach((c) => {
    const o = document.createElement("option");
    o.value = c; o.textContent = c;
    sel.appendChild(o);
  });
}

function renderStats() {
  const base = state.base;
  const withC = base.filter(hasContact).length;
  const cov = base.length ? Math.round((withC / base.length) * 100) : 0;
  const newC = base.filter((l) => l.is_new).length;
  const wk = state.week ? (state.data.weeks.find((w) => w.key === state.week) || {}).label : "全部周期";
  document.getElementById("stats").innerHTML =
    `<span>周期 <b>${esc(wk || state.week)}</b></span>` +
    `<span>线索 <b>${base.length.toLocaleString()}</b></span>` +
    `<span>有联系方式 <b>${withC.toLocaleString()}</b>（${cov}%）</span>` +
    `<span class="new">新增 🆕 <b>${newC.toLocaleString()}</b></span>` +
    `<span class="muted">数据更新 ${state.data.generated_at}</span>`;
}

function renderChips() {
  const counts = {};
  state.base.forEach((l) => {
    const c = counts[l.category] || (counts[l.category] = { count: 0, new: 0 });
    c.count++;
    if (l.is_new) c.new++;
  });
  const arr = Object.keys(counts)
    .map((k) => ({ key: k, count: counts[k].count, new: counts[k].new, label: CAT_LABEL[k] || k }))
    .sort((a, b) => b.count - a.count);
  const all = { key: "", count: state.base.length, new: state.base.filter((l) => l.is_new).length, label: "全部" };
  const chips = [all].concat(arr);

  const wrap = document.getElementById("catChips");
  wrap.innerHTML = chips
    .map(
      (c) =>
        `<span class="chip ${c.key === state.cat ? "active" : ""}" data-cat="${esc(c.key)}">` +
        `${esc(c.label)}<span class="n">${c.count}</span>` +
        (c.new ? `<span class="nb">+${c.new}</span>` : "") +
        `</span>`
    )
    .join("");
  wrap.querySelectorAll(".chip").forEach((el) =>
    el.addEventListener("click", () => {
      state.cat = el.dataset.cat;
      state.page = 1;
      wrap.querySelectorAll(".chip").forEach((c) => c.classList.toggle("active", c.dataset.cat === state.cat));
      applyFilters();
    })
  );
}

function bindControls() {
  document.getElementById("week").addEventListener("change", (e) => {
    state.week = e.target.value;
    state.cat = "";
    state.page = 1;
    refreshBase();
  });
  document.getElementById("q").addEventListener("input", (e) => { state.q = e.target.value.toLowerCase(); state.page = 1; applyFilters(); });
  document.getElementById("city").addEventListener("change", (e) => { state.city = e.target.value; state.page = 1; applyFilters(); });
  document.getElementById("contact").addEventListener("change", (e) => { state.contact = e.target.value; state.page = 1; applyFilters(); });
  document.getElementById("onlyNew").addEventListener("change", (e) => { state.onlyNew = e.target.checked; state.page = 1; applyFilters(); });
}

function applyFilters() {
  const q = state.q;
  state.filtered = state.base.filter((l) => {
    if (state.cat && l.category !== state.cat) return false;
    if (state.city && l.city !== state.city) return false;
    if (state.onlyNew && !l.is_new) return false;
    if (state.contact === "yes" && !hasContact(l)) return false;
    if (state.contact === "no" && hasContact(l)) return false;
    if (q && !(`${l.company} ${l.address} ${l.desc} ${l.email}`.toLowerCase().includes(q))) return false;
    return true;
  });
  render();
}

function render() {
  const total = state.filtered.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  state.page = Math.min(state.page, pages);
  const start = (state.page - 1) * PAGE_SIZE;
  const rows = state.filtered.slice(start, start + PAGE_SIZE);
  const wrap = document.getElementById("tableWrap");

  if (!total) {
    wrap.innerHTML = `<div class="empty">没有匹配的线索</div>`;
    document.getElementById("pager").innerHTML = "";
    return;
  }

  wrap.innerHTML =
    `<table><thead><tr>` +
    `<th></th><th>公司</th><th>分类</th><th>城市</th><th>邮箱</th><th>电话</th><th>项目描述</th><th>日期</th><th>来源</th>` +
    `</tr></thead><tbody>` +
    rows.map(rowHtml).join("") +
    `</tbody></table>`;

  document.getElementById("pager").innerHTML =
    `<button ${state.page <= 1 ? "disabled" : ""} id="prev">← 上一页</button>` +
    `<span>第 ${state.page} / ${pages} 页 · 共 ${total.toLocaleString()} 条</span>` +
    `<button ${state.page >= pages ? "disabled" : ""} id="next">下一页 →</button>`;
  const prev = document.getElementById("prev"), next = document.getElementById("next");
  if (prev) prev.onclick = () => { state.page--; render(); window.scrollTo(0, 0); };
  if (next) next.onclick = () => { state.page++; render(); window.scrollTo(0, 0); };

  wrap.querySelectorAll("[data-copy]").forEach((el) =>
    el.addEventListener("click", (e) => {
      e.preventDefault();
      navigator.clipboard.writeText(el.dataset.copy);
      const t = el.textContent; el.textContent = "已复制 ✓";
      setTimeout(() => { el.textContent = t; }, 1000);
    })
  );
}

function rowHtml(l) {
  const company = l.company ? esc(l.company) : `<span class="muted">(无公司名)</span>`;
  const email = l.email ? `<span class="contact"><a data-copy="${esc(l.email)}" title="点击复制">${esc(l.email)}</a></span>` : `<span class="muted">—</span>`;
  const phone = l.phone ? `<span class="contact"><a data-copy="${esc(l.phone)}" title="点击复制">${esc(l.phone)}</a></span>` : `<span class="muted">—</span>`;
  const src = l.url ? `<a class="src" href="${esc(l.url)}" target="_blank" rel="noopener">查看</a>` : `<span class="muted">${esc(l.source)}</span>`;
  return (
    `<tr class="${l.is_new ? "new" : ""}">` +
    `<td>${l.is_new ? '<span class="badge">🆕</span>' : ""}</td>` +
    `<td>${company}</td>` +
    `<td><span class="cat">${esc(CAT_LABEL[l.category] || l.category)}</span></td>` +
    `<td>${esc(l.city) || '<span class="muted">—</span>'}</td>` +
    `<td>${email}</td>` +
    `<td>${phone}</td>` +
    `<td class="desc" title="${esc(l.desc)}">${esc(l.desc)}</td>` +
    `<td class="muted">${esc(l.date)}</td>` +
    `<td>${src}</td>` +
    `</tr>`
  );
}

load();
