// 純邏輯(不碰畫面與語音):Excel 欄位解析、語音文字比對、朗讀內容切分。
// 對應桌面版的 procedures.py 與 speech_io.py 裡的 segment()。

export const ACCEPT = 85;   // 達到此分數且明顯領先時,直接朗讀
export const SUGGEST = 60;  // 達到此分數但不夠確定時,先問「是不是這個?」
export const MARGIN = 5;    // 第一名要領先第二名多少分才算「明顯領先」

const CN_DIGITS = { 零: "0", 〇: "0", 一: "1", 二: "2", 兩: "2", 两: "2", 三: "3", 四: "4", 五: "5", 六: "6", 七: "7", 八: "8", 九: "9" };

/** 統一成可比對的形式:全形半形、大小寫、中文數字,去掉空白與標點。 */
export function normalize(text) {
  const t = String(text).normalize("NFKC").toLowerCase().replace(/[零〇一二兩两三四五六七八九]/g, (c) => CN_DIGITS[c]);
  return t.replace(/[^0-9a-z一-鿿]/g, "");
}

// ───────────── Excel 欄位解析 ─────────────

const HEADERS = {
  id: ["編號", "項目編號", "项目编号", "编号", "id", "no", "no."],
  name: ["名稱", "項目名稱", "项目名称", "名称", "name"],
  aliases: ["別名", "别名", "alias", "aliases"],
  steps: ["步驟", "步骤", "steps", "step"],
};
// 「.」「:」後面緊接數字是小數或時間(3.3V、1:30),不是步驟編號,不能去掉
const STEP_PREFIX = /^\s*(?:第?\s*\d+\s*(?:[步、)）]|[.:：](?!\d))|[①-⑳])\s*/;

/** rows: 二維陣列,第一列是欄位名稱。回傳 [{id,name,aliases,steps,idKey,nameKeys}] */
export function parseRows(rows) {
  if (!rows || !rows.length) throw new Error("檔案是空的");
  const cell = (v) => (v === null || v === undefined ? "" : String(v).trim());
  const col = {};
  rows[0].forEach((h, i) => {
    const head = cell(h).toLowerCase();
    for (const [key, names] of Object.entries(HEADERS)) if (names.includes(head)) col[key] = i;
  });
  const missing = ["id", "name", "steps"].filter((k) => !(k in col));
  if (missing.length) throw new Error(`第一列缺少欄位:${missing.join("、")}。需要「編號、名稱、別名(選填)、步驟」`);

  const procs = [];
  for (const row of rows.slice(1)) {
    const get = (k) => (k in col ? cell(row[col[k]]) : "");
    const id = get("id");
    const name = get("name");
    if (!id && !name) continue;
    const aliases = get("aliases").split(/[;；,，、|\n]/).map((a) => a.trim()).filter(Boolean);
    const steps = get("steps").split(/\r?\n/).map((s) => s.replace(STEP_PREFIX, "").trim()).filter(Boolean);
    procs.push({
      id, name, aliases, steps,
      idKey: normalize(id),
      nameKeys: [name, ...aliases].map(normalize).filter(Boolean),
    });
  }
  return procs;
}

/** 從存檔的 JSON 還原(只存 id/name/aliases/steps,比對用的欄位重算)。 */
export function hydrate(list) {
  return list.map((p) => ({
    ...p,
    idKey: normalize(p.id),
    nameKeys: [p.name, ...(p.aliases || [])].map(normalize).filter(Boolean),
  }));
}

// ───────────── 模糊比對(等同 rapidfuzz 的 ratio / partial_ratio)─────────────

function lcsLength(a, b) {
  if (!a.length || !b.length) return 0;
  let prev = new Array(b.length + 1).fill(0);
  for (let i = 1; i <= a.length; i++) {
    const cur = new Array(b.length + 1).fill(0);
    for (let j = 1; j <= b.length; j++) {
      cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
    }
    prev = cur;
  }
  return prev[b.length];
}

export function ratio(a, b) {
  const total = a.length + b.length;
  return total ? (200 * lcsLength(a, b)) / total : 0;
}

export function partialRatio(a, b) {
  const [s, l] = a.length <= b.length ? [a, b] : [b, a];
  if (!s.length) return 0;
  let best = 0;
  for (let i = 0; i + s.length <= l.length; i++) best = Math.max(best, ratio(s, l.slice(i, i + s.length)));
  // 頭尾不足整段長度的視窗也要比(例如較長字串的結尾剛好是較短字串的開頭)
  for (let k = 1; k < s.length && k < l.length; k++) {
    best = Math.max(best, ratio(s, l.slice(0, k)), ratio(s, l.slice(l.length - k)));
  }
  return best;
}

function escapeRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function score(query, p) {
  let best = 0;
  if (p.idKey) {
    if (query === p.idKey) return 100;
    // 句子裡含有編號,但前面不能是英數字、後面不能緊接數字(避免 tc1 吃掉 tc10)
    const m = new RegExp(`(^|[^0-9a-z])${escapeRe(p.idKey)}(?![0-9])`).test(query);
    if (m) return 98;
    if (p.idKey.length >= 4) {
      const r = ratio(query, p.idKey);
      best = r >= 75 ? Math.min(80, r) : 0;
    }
  }
  for (const key of p.nameKeys) {
    if (query === key) return 100;
    let s;
    if (key.length >= 2 && query.includes(key)) s = 96;
    else if (query.length >= 2 && key.includes(query)) s = 88;
    else {
      const partial = Math.min(query.length, key.length) >= 3 ? 0.95 * partialRatio(query, key) : 0;
      s = Math.min(94, Math.max(ratio(query, key), partial));
    }
    best = Math.max(best, s);
  }
  return best;
}

/** 回傳 [{score, proc}],由高到低,只保留達到建議門檻的前幾名。 */
export function rank(text, procs, limit = 3) {
  const q = normalize(text);
  if (!q) return [];
  return procs
    .map((proc) => ({ score: score(q, proc), proc }))
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .filter((r) => r.score >= SUGGEST);
}

/**
 * 關鍵字搜尋(打字查詢清單用):輸入幾個字(可用空白分成多個關鍵字),列出編號、名稱、別名含有這些字的所有項目。
 * 每個關鍵字都要找得到;編號開頭 > 編號內含 > 名稱 > 別名,同分維持原本順序。
 * 回傳 [{proc, score}]。
 */
export function search(query, procs) {
  const tokens = String(query).trim().split(/\s+/).map(normalize).filter(Boolean);
  if (!tokens.length) return [];
  const hits = [];
  procs.forEach((proc, order) => {
    const name = normalize(proc.name);
    const aliases = (proc.aliases || []).map(normalize);
    let score = 0;
    for (const t of tokens) {
      let s = 0;
      if (proc.idKey.startsWith(t)) s = 4;
      else if (proc.idKey.includes(t)) s = 3;
      else if (name.includes(t)) s = 2;
      else if (aliases.some((a) => a.includes(t))) s = 1;
      if (!s) return; // 有一個關鍵字找不到,這項就不算
      score += s;
    }
    hits.push({ proc, score, order });
  });
  return hits.sort((a, b) => b.score - a.score || a.order - b.order).map(({ proc, score }) => ({ proc, score }));
}

/**
 * 語音「查詢」指令:句首是 查詢/搜尋/找/列出(或 search/find/list)才算,後面接關鍵字。
 *  {type:"search", keywords}  顯示含有關鍵字的項目清單
 *  {type:"all"}               顯示全部項目
 *  {type:"close"}             收起清單
 *  null                       不是查詢指令
 * 只認句首,所以「檢查電源」裡的「查」不會被誤判。
 */
export function parseQuery(text) {
  const t = String(text).trim().replace(/^[\s,。.,!?!?、]+|[\s,。.,!?!?、]+$/g, "");
  if (/^(關閉|关闭|收起|隱藏|隐藏)(項目)?(清單|清单|列表)?$/.test(t) || /^(close|hide)(\s+list)?$/i.test(t)) return { type: "close" };
  if (/^(全部|所有|全部項目|所有項目|全部项目|所有项目|項目清單|项目清单|項目列表|项目列表|清單|清单|列表)$/.test(t)) return { type: "all" };
  const m = t.match(/^(?:查詢|查询|搜尋|搜寻|搜索|尋找|寻找|查找|列出|找|search|find|list)\s*(.*)$/i);
  if (!m) return null;
  const kw = m[1].replace(/^[\s,。.,!?!?、::]+|[\s,。.,!?!?、::]+$/g, "");
  if (/^(全部|所有)(項目|项目)?$/.test(kw) || /^all$/i.test(kw)) return { type: "all" };
  return { type: "search", keywords: kw };
}

export function isConfident(ranked) {
  if (!ranked.length || ranked[0].score < ACCEPT) return false;
  return ranked.length === 1 || ranked[0].score - ranked[1].score >= MARGIN;
}

const REPEAT = ["重複", "重复", "重來", "重来", "再一次", "再說一次", "再说一次", "再念一次", "再唸一次", "repeat", "again"].map(normalize);
const QUIT = ["結束", "结束", "離開", "离开", "退出", "quit", "exit"].map(normalize);

export function command(text) {
  const q = normalize(text);
  if (REPEAT.some((w) => q.includes(w))) return "repeat";
  if (QUIT.some((w) => q.includes(w))) return "quit";
  return null;
}

/** true / false / null(聽不出來) */
export function yesNo(text) {
  const q = normalize(text);
  if (["no", "nope", "nah"].includes(q) || /不|否|沒有|没有/.test(q)) return false;
  if (["yes", "yeah", "yep", "ok", "okay", "sure"].includes(q) || /沒錯|没错|對|对|是|好|要|確定|确定|嗯/.test(q)) return true;
  if (/錯|错/.test(q)) return false;
  return null;
}

// ───────────── 朗讀內容 ─────────────

/** TC-023 → "T C 0 2 3",讓編號一個字一個字念清楚。 */
export function spokenId(pid) {
  return pid.replace(/-/g, " ").replace(/[A-Za-z0-9]/g, (c) => c + " ").replace(/ +/g, " ").trim();
}

/** 每個元素是一段。第 0 段是編號與名稱,第 1~N 段是各步驟(段號就是步驟號),最後一段是結尾。 */
export function script(p) {
  return [
    `${spokenId(p.id)}。${p.name}。共 ${p.steps.length} 個步驟。`,
    ...p.steps.map((s, i) => `第 ${i + 1} 步。${s}`),
    "以上。",
  ];
}

const EN_RUN = /[A-Za-z][A-Za-z0-9'_/-]*(?:\.[A-Za-z0-9]+)*(?: +[A-Za-z0-9][A-Za-z0-9'_/-]*(?:\.[A-Za-z0-9]+)*)*/g;

// 數字後面的單位用中文念(1 kHz → 1千赫茲),英文語音會把 kHz 念成一個個字母
const UNITS = {
  kHz: "千赫茲", MHz: "兆赫茲", GHz: "吉赫茲", Hz: "赫茲",
  mV: "毫伏特", V: "伏特", mA: "毫安培", A: "安培", W: "瓦特",
  dB: "分貝", "Ω": "歐姆", ms: "毫秒",
};
const UNIT_RE = new RegExp(
  `(\\d+(?:\\.\\d+)?)\\s*(${Object.keys(UNITS).sort((a, b) => b.length - a.length).map(escapeRe).join("|")})(?![A-Za-z])`,
  "g"
);
const DECIMAL = /\d+(?:\.\d+)+/g;
const isAlnumOrDot = (c) => c !== undefined && /[A-Za-z0-9.]/.test(c);
const isLetter = (c) => c !== undefined && /[A-Za-z]/.test(c);

function splitWords(text) {
  const out = [];
  let pos = 0;
  for (const m of text.matchAll(EN_RUN)) {
    out.push({ lang: "zh", text: text.slice(pos, m.index) });
    out.push({ lang: "en", text: m[0] });
    pos = m.index + m[0].length;
  }
  out.push({ lang: "zh", text: text.slice(pos) });
  return out;
}

// 一個片段開頭的標點(例如英文編號後面接的「。」)不能交給語音念,否則會被念成「句號」;
// 把它換成停頓(gap 秒):句號類停久一點,逗號類停短一點。片段中間的標點語音本來就只會停頓,不用處理。
const LEAD_PUNCT = /^[\s。，、；：！？…,.;:!?「」『』（）()《》【】]+/;
const gapOf = (p) => (/[。；：！？…;:!?.]/.test(p) ? 0.3 : /[，、,]/.test(p) ? 0.15 : 0);

/** 切成 [{lang, text, gap}]:英文走英文語音,其餘(中文與數字)走中文語音;小數與 IP 用中文念成「點」。 */
export function segment(input) {
  const text = input.replace(UNIT_RE, (m, num, unit, offset, whole) =>
    isLetter(whole[offset - 1]) ? m : num + UNITS[unit]
  );
  const parts = [];
  let pos = 0;
  for (const m of text.matchAll(DECIMAL)) {
    if (isAlnumOrDot(text[m.index - 1])) continue;
    parts.push(...splitWords(text.slice(pos, m.index)));
    parts.push({ lang: "zh", text: m[0].replace(/\./g, "點") });
    pos = m.index + m[0].length;
  }
  parts.push(...splitWords(text.slice(pos)));

  const merged = []; // 相鄰的同語言片段合併,念起來才連貫
  for (const { lang, text: t } of parts) {
    const last = merged[merged.length - 1];
    if (last && last.lang === lang) last.text += (lang === "en" && t.trim() ? " " : "") + t;
    else merged.push({ lang, text: t });
  }

  const out = [];
  let pending = 0;
  for (const { lang, text: t } of merged) {
    const lead = (t.match(LEAD_PUNCT) || [""])[0];
    const rest = t.slice(lead.length).trim();
    if (!/[\p{L}\p{N}]/u.test(rest)) { // 只剩標點:變成下一個片段前面的停頓
      pending += gapOf(lead + rest);
      continue;
    }
    out.push({ lang, text: rest, gap: pending + gapOf(lead) });
    pending = 0;
  }
  return out;
}
