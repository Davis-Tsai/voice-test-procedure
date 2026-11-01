// 語音查詢測試步驟(網頁版):瀏覽器語音辨識 → 比對 Excel 項目 → 瀏覽器語音朗讀。
import * as L from "./logic.js";

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const synth = window.speechSynthesis;
const KEY = { procs: "vtp.procs.v1", source: "vtp.source.v1", rate: "vtp.rate.v1" };

const state = {
  procs: [],
  last: null,
  active: false,      // 已按「開始」,正在聆聽/處理的連續流程
  handling: false,    // 單次打字查詢處理中
  runId: 0,
  finCurrent: null,
  pending: null,      // 朗讀時被打斷的打字查詢
  awaitingAnswer: false, // 正在問「請問是…嗎?」等是/不是
  rec: null,
  typedResolve: null, // 流程正在等輸入時,打字查詢送到這裡
  speakToken: 0,
  rate: 1,
  wake: null,
};

// ───────────── 畫面 ─────────────

function setStatus(text, kind = "idle") {
  const el = $("status");
  el.textContent = text;
  el.className = kind;
}
const setHeard = (text) => ($("heard").textContent = text);

function showNotice(text) {
  $("notice").hidden = !text;
  $("notice").textContent = text || "";
}

function showProc(p) {
  $("title").textContent = `${p.id}  ${p.name}`;
  const ol = $("steps");
  ol.replaceChildren(...p.steps.map((s) => Object.assign(document.createElement("li"), { textContent: s })));
}

/** 段號 n:1~N 是步驟,其他(開頭、結尾)清掉標示。 */
function highlight(n) {
  const items = [...$("steps").children];
  items.forEach((li, i) => li.classList.toggle("cur", i + 1 === n));
  if (items[n - 1]) items[n - 1].scrollIntoView({ block: "center", behavior: "smooth" });
}

function updateButtons() {
  const b = $("micBtn");
  b.textContent = state.active ? "停止" : "開始";
  b.classList.toggle("on", state.active);
}

// ───────────── 資料(Excel)─────────────

function saveProcs(procs, source) {
  const plain = procs.map(({ id, name, aliases, steps }) => ({ id, name, aliases, steps }));
  localStorage.setItem(KEY.procs, JSON.stringify(plain));
  localStorage.setItem(KEY.source, source);
}

function applyProcs(procs, source) {
  state.procs = procs;
  state.last = null;
  $("source").textContent = `目前資料:${source}(${procs.length} 項)`;
}

async function loadInitial() {
  const saved = localStorage.getItem(KEY.procs);
  if (saved) {
    try {
      applyProcs(L.hydrate(JSON.parse(saved)), localStorage.getItem(KEY.source) || "已載入的資料");
      return;
    } catch { /* 存檔壞了就改用範例 */ }
  }
  await useSample();
}

async function useSample() {
  const list = await (await fetch("sample.json")).json();
  localStorage.removeItem(KEY.procs);
  localStorage.removeItem(KEY.source);
  applyProcs(L.hydrate(list), "範例資料");
}

async function loadFile(file) {
  try {
    const wb = XLSX.read(await file.arrayBuffer(), { type: "array" });
    const rows = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, raw: false, defval: "" });
    const procs = L.parseRows(rows);
    if (!procs.length) throw new Error("沒有讀到任何項目");
    saveProcs(procs, file.name);
    applyProcs(procs, file.name);
    showNotice("");
    setHeard(`已載入 ${file.name}:${procs.length} 個項目`);
  } catch (e) {
    showNotice(`讀取失敗:${e.message}`);
  }
}

// ───────────── 朗讀 ─────────────

let voices = [];
const refreshVoices = () => (voices = synth ? synth.getVoices() : []);
if (synth) {
  refreshVoices();
  synth.onvoiceschanged = refreshVoices;
}

function pickVoice(lang) {
  const prefs = lang === "zh" ? ["zh-tw", "zh-hk", "zh-cn", "zh"] : ["en-us", "en-gb", "en"];
  const norm = (v) => v.lang.toLowerCase().replace("_", "-");
  for (const p of prefs) {
    const hit = voices.find((v) => norm(v).startsWith(p));
    if (hit) return hit;
  }
  return null;
}

function utter(text, lang) {
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    const v = pickVoice(lang);
    if (v) { u.voice = v; u.lang = v.lang; } else u.lang = lang === "zh" ? "zh-TW" : "en-US";
    u.rate = state.rate;
    let done = false;
    const fin = () => { if (!done) { done = true; clearTimeout(timer); resolve(); } };
    const timer = setTimeout(fin, Math.max(4000, text.length * 500)); // 有些瀏覽器不會觸發 onend
    u.onend = fin;
    u.onerror = fin;
    state.finCurrent = fin;
    synth.speak(u);
  });
}

/** paragraphs: 字串陣列,每個字串是一段;onPara(n) 在第 n 段開始念時呼叫。 */
async function speak(paragraphs, onPara) {
  if (!synth) return;
  const token = ++state.speakToken;
  synth.cancel();
  $("stopSpeakBtn").disabled = false;
  try {
    for (let n = 0; n < paragraphs.length; n++) {
      if (token !== state.speakToken) return;
      onPara?.(n);
      for (const seg of L.segment(paragraphs[n])) {
        if (token !== state.speakToken) return;
        await utter(seg.text, seg.lang);
      }
      await sleep(550);
    }
  } finally {
    if (token === state.speakToken) $("stopSpeakBtn").disabled = true;
  }
}

function stopSpeaking() {
  state.speakToken++;
  synth?.cancel();
  state.finCurrent?.(); // 有些瀏覽器取消後不會通知,直接讓目前這句結束
  $("stopSpeakBtn").disabled = true;
}

async function say(paragraphs, onPara) {
  setStatus("朗讀中", "speak");
  await speak(paragraphs, onPara);
}

async function read(proc) {
  showProc(proc);
  await say(L.script(proc), highlight);
  highlight(0);
}

// ───────────── 聆聽(語音或打字)─────────────

/** 回傳 {text, alts} | {needTap} | {network} | {fatal}。語音辨識與打字查詢誰先到用誰。 */
function getInput() {
  setStatus("聆聽中…", "listen");
  return new Promise((resolve) => {
    let finished = false;
    const finish = (v) => {
      if (finished) return;
      finished = true;
      state.typedResolve = null;
      state.rec = null;
      resolve(v);
    };
    state.typedResolve = (t) => {
      try { state.rec?.abort(); } catch { /* 已結束 */ }
      finish({ text: t, alts: [t] });
    };
    if (state.pending) {  // 朗讀時被打字查詢打斷,這段文字就是下一次輸入
      const t = state.pending;
      state.pending = null;
      return state.typedResolve(t);
    }
    if (!SR) return;

    const r = new SR();
    state.rec = r;
    r.lang = "zh-TW";
    r.maxAlternatives = 3;
    r.interimResults = false;
    r.continuous = false;
    let alts = [];
    let error = null;
    r.onresult = (e) => { alts = [...e.results[0]].map((a) => a.transcript.trim()).filter(Boolean); };
    r.onerror = (e) => { error = e.error; };
    r.onend = () => {
      if (error === "not-allowed" || error === "service-not-allowed") finish({ fatal: "沒有麥克風權限。請在瀏覽器設定允許使用麥克風,或改用下方輸入框打字。" });
      else if (error === "audio-capture") finish({ fatal: "找不到麥克風。" });
      else if (error === "network") finish({ network: true });
      else finish({ text: alts[0] || "", alts });
    };
    try { r.start(); } catch { finish({ needTap: true }); }
  });
}

/** 辨識有多個候選時,挑第一個能對上項目(或指令)的。 */
function pickAlt(alts, accept) {
  return alts.find(accept) ?? alts[0] ?? "";
}

async function listenText(accept) {
  const r = await getInput();
  if (r.alts?.length) return pickAlt(r.alts, accept);
  return r.text ?? "";
}

// ───────────── 網路 ─────────────

async function ensureOnline() {
  if (navigator.onLine) return;
  setStatus("沒有網路,等待連線中…", "error");
  await speak(["網路沒有連線,請檢查網路。連線後會自動繼續。"]);
  while (!navigator.onLine && state.active) await new Promise((r) => addEventListener("online", r, { once: true }));
  if (state.active) await say(["網路已恢復。"]);
}

// ───────────── 主流程 ─────────────

async function handle(heard) {
  const procs = state.procs;
  const ranked = L.rank(heard, procs);
  const confident = L.isConfident(ranked);
  const cmd = confident ? null : L.command(heard);

  if (cmd === "quit") {
    await say(["好的,再見。"]);
    stopSession();
    return;
  }
  if (cmd === "repeat") {
    if (state.last) await read(state.last);
    else await say(["還沒有可以重複的內容,請先說出測試項目。"]);
    return;
  }

  let chosen = null;
  if (confident) chosen = ranked[0].proc;
  else {
    state.awaitingAnswer = true;
    for (const { proc } of ranked) { // 不太確定:逐一詢問最接近的幾個
      setHeard(`聽到:${heard}  →  請問是 ${proc.id} ${proc.name} 嗎?`);
      await say([`請問是 ${L.spokenId(proc.id)}。${proc.name} 嗎?請回答是或不是。`]);
      let ans = null;
      for (let i = 0; i < 2; i++) {
        ans = L.yesNo(await listenText((t) => L.yesNo(t) !== null));
        if (ans !== null) break;
      }
      if (ans) { chosen = proc; break; }
    }
    state.awaitingAnswer = false;
  }
  if (!chosen) {
    setHeard(`聽到:${heard}  (找不到對應的測試項目)`);
    await say(["找不到對應的測試項目,請再說一次。"]);
    return;
  }
  state.last = chosen;
  await read(chosen);
}

async function session() {
  state.active = true;
  updateButtons();
  keepAwake();
  showNotice("");
  // 開場白要在使用者點擊的當下就開始念,iPhone 才允許發聲
  const greeting = speak(["系統已就緒。請說出測試項目名稱或編號。說「重複」可以重新朗讀,說「結束」離開。"]);
  setStatus("朗讀中", "speak");
  await greeting;

  while (state.active) {
    await ensureOnline();
    if (!state.active) break;
    const r = await getInput();
    if (!state.active) break;

    if (r.fatal) { showNotice(r.fatal); break; }
    if (r.needTap) { showNotice("這個瀏覽器需要手動點一下才能開始聽,請再按一次「開始」。"); break; }
    if (r.network) {
      if (!navigator.onLine) continue;
      await say(["辨識服務暫時連不上,請再說一次。"]);
      continue;
    }
    const heard = pickAlt(r.alts ?? [], (t) => L.isConfident(L.rank(t, state.procs)) || L.command(t));
    if (!heard) continue;
    setHeard(`聽到:${heard}`);
    await handle(heard);
  }
  stopSession();
}

function stopSession() {
  state.active = false;
  try { state.rec?.abort(); } catch { /* 已結束 */ }
  state.typedResolve?.("");
  stopSpeaking();
  state.wake?.release?.().catch(() => {});
  state.wake = null;
  setStatus("已停止", "idle");
  updateButtons();
}

async function keepAwake() {
  try { state.wake = await navigator.wakeLock?.request("screen"); } catch { /* 不支援就算了 */ }
}

// ───────────── 事件 ─────────────

$("micBtn").addEventListener("click", () => {
  if (state.active) stopSession();
  else session();
});
$("stopSpeakBtn").addEventListener("click", stopSpeaking);

$("typedForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = $("typed").value.trim();
  $("typed").value = "";
  if (!text) return;
  if (state.typedResolve) return state.typedResolve(text);   // 連續流程正在等輸入
  if (state.active || state.awaitingAnswer) {                // 正在朗讀/等回答:打斷它,下一次聆聽直接用這段文字
    state.pending = text;
    stopSpeaking();
    return;
  }
  const run = ++state.runId;                                  // 沒按開始:單次查詢(念到一半再查詢會打斷舊的)
  state.handling = true;
  stopSpeaking();
  try {
    setHeard(`輸入:${text}`);
    await handle(text);
  } finally {
    if (run === state.runId) {
      state.handling = false;
      if (!state.active) setStatus("尚未開始", "idle");
    }
  }
});

$("file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (f) await loadFile(f);
  e.target.value = "";
});
$("sampleBtn").addEventListener("click", async () => { await useSample(); setHeard("已改用範例資料"); });
$("clearBtn").addEventListener("click", async () => { await useSample(); setHeard("已清除載入的資料,改回範例資料"); });

$("rate").addEventListener("change", (e) => {
  state.rate = Number(e.target.value);
  localStorage.setItem(KEY.rate, e.target.value);
});
document.addEventListener("visibilitychange", () => {
  if (state.active && document.visibilityState === "visible") keepAwake();
});

// ───────────── 啟動 ─────────────

const savedRate = localStorage.getItem(KEY.rate);
if (savedRate) { state.rate = Number(savedRate); $("rate").value = savedRate; }

if (!SR) showNotice("這個瀏覽器不支援語音辨識(建議用 Chrome、Edge 或 Safari)。仍可用下方輸入框打字查詢,並由語音朗讀。");
else if (!synth) showNotice("這個瀏覽器不支援語音朗讀。");

loadInitial().then(updateButtons);
