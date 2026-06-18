// Static leads viewer — reads ./data/leads.json and renders a filterable,
// paginated, category-partitioned table with a 🆕 badge for newly crawled rows.

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
const state = { data: null, filtered: [], page: 1, cat: "", city: "", contact: "", onlyNew: false, q: "" };

function esc(s) {
  return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

async function load() {
  try {
    const r = await fetch("./data/leads.json", { cache: "no-store" });
    state.data = await r.json();
  } catch (e) {
    document.getElementById("stats").textContent = "无法加载 data/leads.json";
    return;
  }
  renderStats();
  renderChips();
  renderCities();
  bindControls();
  applyFilters();
}

function renderStats() {
  const d = state.data;
  const cov = d.total ? Math.round((d.with_contact / d.total) * 100) : 0;
  document.getElementById("stats").innerHTML =
    `<span>线索总数 <b>${d.total.toLocaleString()}</b></span>` +
    `<span>有联系方式 <b>${d.with_contact.toLocaleString()}</b>（${cov}%）</span>` +
    `<span class="new">本期新增 🆕 <b>${d.new_count.toLocaleString()}</b></span>` +
    `<span>数据更新 ${d.generated_at}</span>` +
    `<span class="muted">（first_seen ≥ ${d.new_since} 记为新增）</span>`;
}

function renderChips() {
  const wrap = document.getElementById("catChips");
  const cats = state.data.categories.slice().sort((a, b) => b.count - a.count);
  const chips = [{ key: "", count: state.data.total, new: state.data.new_count, label: "全部" }].concat(
    cats.map((c) => ({ key: c.key, count: c.count, new: c.new, label: CAT_LABEL[c.key] || c.key }))
  );
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
      renderChips();
      applyFilters();
    })
  );
}

function renderCities() {
  const sel = document.getElementById("city");
  state.data.cities.forEach((c) => {
    const o = document.createElement("option");
    o.value = c;
    o.textContent = c;
    sel.appendChild(o);
  });
}

function bindControls() {
  document.getElementById("q").addEventListener("input", (e) => { state.q = e.target.value.toLowerCase(); state.page = 1; applyFilters(); });
  document.getElementById("city").addEventListener("change", (e) => { state.city = e.target.value; state.page = 1; applyFilters(); });
  document.getElementById("contact").addEventListener("change", (e) => { state.contact = e.target.value; state.page = 1; applyFilters(); });
  document.getElementById("onlyNew").addEventListener("change", (e) => { state.onlyNew = e.target.checked; state.page = 1; applyFilters(); });
}

function applyFilters() {
  const q = state.q;
  state.filtered = state.data.leads.filter((l) => {
    if (state.cat && l.category !== state.cat) return false;
    if (state.city && l.city !== state.city) return false;
    if (state.onlyNew && !l.is_new) return false;
    if (state.contact === "yes" && !(l.email || l.phone)) return false;
    if (state.contact === "no" && (l.email || l.phone)) return false;
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
