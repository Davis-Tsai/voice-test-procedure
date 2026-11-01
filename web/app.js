// 語音查詢測試步驟(網頁版):瀏覽器語音辨識 → 比對 Excel 項目 → 瀏覽器語音朗讀。
// __BUILD__ 會在 GitHub Actions 發布時換成這次的 commit 編號,讓瀏覽器每次發布都重新下載新檔案,不會吃到舊快取
import * as L from "./logic.js?v=__BUILD__";

const BUILD = "__BUILD__";

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 診斷紀錄:顯示在「資料與設定 → 診斷紀錄」,手機上沒有開發者工具時用來查問題
const T0 = Date.now();
const dbgLines = [];
function dbg(msg) {
  dbgLines.push(`${((Date.now() - T0) / 1000).toFixed(1)}s ${msg}`);
  if (dbgLines.length > 60) dbgLines.shift();
  const el = document.getElementById("debug");
  if (el) el.textContent = dbgLines.join("\n");
}

document.getElementById("version").textContent = `版本:${BUILD.startsWith("__") ? "本機開發版" : BUILD}`;

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const synth = window.speechSynthesis;
const KEY = { procs: "vtp.procs.v1", source: "vtp.source.v1", rate: "vtp.rate.v1", mode: "vtp.mode.v1", greet: "vtp.greet.v1" };

const state = {
  procs: [],
  last: null,
  active: false,      // 已按「開始」,正在聆聽/處理的連續流程
  handling: false,    // 單次打字查詢處理中
  runId: 0,
  finCurrent: null,
  pending: null,      // 朗讀時被打斷的打字查詢
  awaitingAnswer: false, // 正在問「請問是…嗎?」等是/不是
  listAll: false,     // 「清單」按鈕:顯示全部項目
  greetOn: true,      // 是否念開場說明(設定裡可關閉)
  greeted: false,     // 這次開啟網頁後是否已念過開場說明,只念第一次
  mode: "auto",       // "auto" 常開監聽 | "ptt" 按住說話
  ptt: null,          // 按住說話進行中的 {rec, startedAt, ...}
  prevEnded: null,    // 最近一次辨識(不論哪種模式)結束的 Promise,新的辨識要等它
  lastEndAt: 0,       // 最近一次辨識結束的時間
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

// ───────────── 打字查詢清單 ─────────────

/** 依輸入框內容(或「清單」按鈕)顯示符合的項目。點項目 = 查詢那一項並朗讀。 */
function renderResults() {
  const box = $("results");
  const main = document.querySelector("main");
  const q = $("typed").value.trim();
  $("listBtn").classList.toggle("on", state.listAll);
  if (!q && !state.listAll) {
    box.hidden = true;
    main.classList.remove("searching");
    return;
  }
  let hits = q ? L.search(q, state.procs) : state.procs.map((proc) => ({ proc }));
  let header;
  if (!q) header = `全部 ${hits.length} 項,點一下就朗讀`;
  else if (hits.length) header = `找到 ${hits.length} 項,點一下就朗讀`;
  else {
    hits = L.rank(q, state.procs); // 沒有包含這些字的項目:列出聽起來相近的
    header = hits.length ? `沒有包含「${q}」的項目,以下是相近的` : `找不到「${q}」`;
  }
  const head = Object.assign(document.createElement("li"), { className: "resHead", textContent: header });
  const items = hits.map(({ proc }) => {
    const li = document.createElement("li");
    const b = Object.assign(document.createElement("button"), { type: "button", className: "resItem" });
    b.append(
      Object.assign(document.createElement("span"), { className: "rid", textContent: proc.id }),
      Object.assign(document.createElement("span"), { className: "rname", textContent: proc.name }),
    );
    if (proc.aliases?.length) {
      b.append(Object.assign(document.createElement("span"), { className: "ralias", textContent: `別名:${proc.aliases.join("、")}` }));
    }
    b.addEventListener("click", () => {
      $("typed").value = "";
      $("typed").blur(); // 收起手機鍵盤
      state.listAll = false;
      renderResults();
      submit([proc.id]); // 用編號查詢,等同你說出完整編號
    });
    li.append(b);
    return li;
  });
  box.replaceChildren(head, ...items);
  box.hidden = false;
  main.classList.add("searching");
  main.scrollTop = 0;
}

function showConfirm(question) {
  $("confirmQ").textContent = question;
  $("confirm").hidden = false;
}
const hideConfirm = () => { $("confirm").hidden = true; };

function updateButtons() {
  const b = $("micBtn");
  if (state.mode === "ptt") {
    b.textContent = isPhone ? (state.ptt ? "停止" : "開始") : "按住說話";
    b.classList.toggle("on", !!state.ptt);
  } else {
    b.textContent = state.active ? "停止" : "開始";
    b.classList.toggle("on", state.active);
  }
}

function idleStatus() {
  if (state.mode === "ptt") setStatus(isPhone ? "按「開始」後說話" : "按住下方按鈕說話", "idle");
  else setStatus("尚未開始", "idle");
  if (!state.last) {
    $("title").textContent = state.mode === "ptt"
      ? (isPhone ? "按下「開始」,然後說出測試項目名稱或編號" : "按住下方按鈕,說出測試項目名稱或編號")
      : "按下「開始」,然後說出測試項目名稱或編號";
  }
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
  if (typeof renderResults === "function") renderResults();
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

/** iPhone 只允許「使用者點擊的當下」開始第一段朗讀,事後(例如辨識完成後)才念會沒聲音。
 *  所以在點擊當下先送一段無聲的朗讀,把朗讀功能解鎖。 */
function unlockSpeech() {
  if (!synth) return;
  const u = new SpeechSynthesisUtterance("。");
  u.volume = 0;
  u.lang = "zh-TW";
  synth.speak(u);
  dbg("解鎖朗讀(點擊當下送出無聲朗讀)");
}

function utter(text, lang) {
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    const v = pickVoice(lang);
    if (v) { u.voice = v; u.lang = v.lang; } else u.lang = lang === "zh" ? "zh-TW" : "en-US";
    u.rate = state.rate;
    let done = false;
    let started = false;
    const fin = () => { if (!done) { done = true; clearTimeout(timer); clearTimeout(warn); resolve(); } };
    const timer = setTimeout(fin, Math.max(4000, text.length * 500)); // 有些瀏覽器不會觸發 onend
    const warn = setTimeout(() => {
      if (started || done) return;
      dbg(`朗讀 2.5 秒內沒有開始:${text.slice(0, 12)}`);
      showNotice("手機沒有發出朗讀聲:請確認沒有開靜音(側邊撥片)、音量有開,然後再按一次「開始」。");
    }, 2500);
    u.onstart = () => { started = true; };
    u.onend = fin;
    u.onerror = (e) => { dbg(`朗讀 error:${e.error || ""}`); fin(); };
    state.finCurrent = fin;
    synth.speak(u);
  });
}

/** paragraphs: 字串陣列,每個字串是一段;onPara(n) 在第 n 段開始念時呼叫。 */
async function speak(paragraphs, onPara) {
  if (!synth) return;
  const token = ++state.speakToken;
  synth.cancel();
  // 辨識剛結束時,iPhone 的聲音輸出還沒從「收音」切回「播放」,馬上念會沒聲音
  const sinceEnd = Date.now() - state.lastEndAt;
  if (isIOS && sinceEnd < 600) await sleep(600 - sinceEnd);
  if (token !== state.speakToken) return;
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
  if (state.mode === "ptt") setStatus(isPhone ? "按「開始」後回答" : "按住下方按鈕回答", "listen");
  else setStatus("聆聽中…", "listen");
  return new Promise((resolve) => {
    let finished = false;
    const finish = (v) => {
      if (finished) return;
      finished = true;
      state.typedResolve = null;
      state.rec = null;
      resolve(v);
    };
    state.typedResolve = (t, alts = [t]) => {
      try { state.rec?.abort(); } catch { /* 已結束 */ }
      finish({ text: t, alts });
    };
    if (state.pending) {  // 朗讀時被打斷的輸入(打字或按住說話),這段就是下一次輸入
      const { text, alts } = state.pending;
      state.pending = null;
      return state.typedResolve(text, alts);
    }
    if (!SR || state.mode === "ptt") return; // 按住說話模式:由按鈕送結果進來

    const r = new SR();
    state.rec = r;
    let markEnded;
    const ended = new Promise((res) => { markEnded = res; });
    r.lang = "zh-TW";
    r.maxAlternatives = 3;
    r.interimResults = true; // 結束時若沒有「最終」結果(iPhone 常見),就用最後一個暫時結果
    r.continuous = false;
    let alts = [];
    let error = null;
    let errorMessage = "";
    const t0 = Date.now();
    r.onstart = () => dbg("常開 辨識 start");
    r.onaudiostart = () => {
      dbg("常開 audiostart(麥克風開始收音)");
      if (state.active && state.mode === "auto") setStatus("聆聽中…請說話", "listen");
    };
    r.onspeechstart = () => dbg("常開 speechstart(偵測到說話)");
    r.onresult = (e) => {
      const last = e.results[e.results.length - 1];
      alts = [...last].map((a) => a.transcript.trim()).filter(Boolean);
      dbg(`常開 result:${alts.join(" | ")}`);
    };
    r.onerror = (e) => {
      error = e.error;
      errorMessage = e.message || "";
      dbg(`常開 error:${e.error}${e.message ? `(${e.message})` : ""}`);
    };
    r.onend = () => {
      const ms = Date.now() - t0;
      state.lastEndAt = Date.now();
      markEnded();
      dbg(`常開 end(${ms} 毫秒,${alts.length ? "有結果" : "沒結果"})`);
      if (error === "not-allowed" || error === "service-not-allowed") finish({ fatal: describeError(error) });
      else if (error === "audio-capture" && /muted/i.test(errorMessage)) finish({ transient: describeError(error, errorMessage), ms });
      else if (error === "audio-capture") finish({ fatal: describeError(error, errorMessage) });
      else if (error === "network") finish({ network: true });
      else finish({ text: alts[0] || "", alts, ms });
    };
    // 上一次辨識(不論哪種模式)還沒完全結束就開始新的,iPhone 會把新的一次直接靜音:先等它結束
    const prev = state.prevEnded;
    state.prevEnded = ended;
    (async () => {
      if (prev) await Promise.race([prev, sleep(1500)]);
      if (isIOS) await sleep(300);
      if (finished) { markEnded(); return; } // 等待期間已被取消或有打字輸入
      try { r.start(); } catch (err) {
        dbg(`常開 start() 丟出例外:${err.name} ${err.message}`);
        markEnded();
        finish({ needTap: true });
      }
    })();
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
      setHeard(`聽到:${heard}`);
      showConfirm(`請問是 ${proc.id} ${proc.name} 嗎?`); // 也可以直接點下面的「是 / 不是」
      await say([`請問是 ${L.spokenId(proc.id)}。${proc.name} 嗎?請回答是或不是。`]);
      let ans = null;
      for (let i = 0; i < 2; i++) {
        ans = L.yesNo(await listenText((t) => L.yesNo(t) !== null));
        if (ans !== null) break;
      }
      if (ans) { chosen = proc; break; }
    }
    state.awaitingAnswer = false;
    hideConfirm();
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
  if (state.greetOn && !state.greeted) {
    // 開場說明只在開啟網頁後的第一次念,而且要在使用者點擊的當下就開始念,iPhone 才允許發聲
    state.greeted = true;
    const greeting = speak(["系統已就緒。請說出測試項目名稱或編號。說「重複」可以重新朗讀,說「結束」離開。"]);
    setStatus("朗讀中", "speak");
    await greeting;
  } else {
    unlockSpeech(); // 不念說明時,也要在點擊當下解鎖朗讀,之後的結果才念得出聲音
  }

  let fails = 0; // 連續「立刻結束、沒有聽到任何東西」的次數,避免默默空轉
  while (state.active) {
    await ensureOnline();
    if (!state.active) break;
    // iPhone 的 Safari 朗讀完立刻開麥克風,常常會被判定為「靜音」,等一下再開
    if (isIOS) await sleep(900);
    if (!state.active) break;
    const r = await getInput();
    if (!state.active) break;

    if (r.transient || (!r.fatal && !r.network && !r.needTap && !r.alts?.length && r.ms < 1500)) {
      fails++;
      dbg(`連續快速失敗 ${fails} 次`);
      if (fails >= 3) {
        showNotice(r.transient || "麥克風一直立刻結束、沒有收到聲音。請看「診斷紀錄」;也可以改用下方輸入框,點進去按鍵盤上的麥克風來聽寫。");
        break;
      }
      await sleep(1500);
      continue;
    }
    fails = 0;

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
  hideConfirm();
  updateButtons();
}

/** 打字或按住說話送來的輸入,統一在這裡分流。alts: 辨識候選(打字就是一個字串)。 */
async function submit(alts) {
  const text = alts[0];
  if (!text) return;
  if (state.typedResolve) return state.typedResolve(text, alts); // 流程正在等輸入
  if (state.active || state.awaitingAnswer) {                    // 正在朗讀/等回答:打斷它,下一次聆聽直接用這段
    state.pending = { text, alts };
    stopSpeaking();
    return;
  }
  const run = ++state.runId;                                     // 單次查詢(念到一半再查詢會打斷舊的)
  state.handling = true;
  stopSpeaking();
  try {
    const heard = pickAlt(alts, (t) => L.isConfident(L.rank(t, state.procs)) || L.command(t));
    setHeard(`${state.mode === "ptt" ? "聽到" : "輸入"}:${heard}`);
    await handle(heard);
  } finally {
    if (run === state.runId) {
      state.handling = false;
      if (!state.active) idleStatus();
    }
  }
}

// ───────────── 按住說話 ─────────────

const MIN_HOLD_MS = 350;

// 手機(iPhone、Android)一律用「點一下開始,說完停頓自動結束,再點一次可提早結束」:
// iPhone 的 Safari 用 continuous 模式容易失敗(被靜音、沒偵測到說話),
// Android 的 Chrome 用 continuous 模式會重複吐出累積的文字。電腦才用按住說話。
const autoEnd = () => isPhone;

async function pttStart() {
  if (state.mode !== "ptt") return;
  if (state.ptt) { // 點一下模式進行中,再按一次 = 說完了
    if (state.ptt.tapMode) {
      dbg("再按一次,提早結束收音");
      setStatus("辨識中…", "think");
      try { state.ptt.rec.stop(); } catch { /* 已結束 */ }
    }
    return;
  }
  showNotice("");
  if (!SR) { showNotice("這個瀏覽器不支援語音辨識,請用下方輸入框打字。"); return; }
  if (!navigator.onLine) {
    setStatus("沒有網路", "error");
    speak(["網路沒有連線,請檢查網路。"]);
    return;
  }
  stopSpeaking(); // 按住時先停止朗讀,方便插話
  keepAwake();
  const r = new SR();
  r.lang = "zh-TW";
  r.maxAlternatives = 3;
  r.interimResults = true; // 放開時若還沒有「最終」結果(iPhone 常見),就用最後一個暫時結果
  r.continuous = !autoEnd(); // 電腦:按住期間即使停頓也持續收音,放開才結束
  const press = { rec: r, startedAt: Date.now(), error: null, errorMessage: "", tooShort: false, released: false, tapMode: autoEnd(), results: [] };
  press.ended = new Promise((resolve) => { press.resolveEnded = resolve; });
  r.onstart = () => dbg("辨識 start");
  r.onaudiostart = () => {
    dbg("辨識 audiostart(麥克風開始收音)");
    if (state.ptt !== press) return;
    if (press.tapMode) setStatus("請說話…(說完停頓,或按「停止」)", "listen");
    else if (!press.released) setStatus("請說話…(說完放開)", "listen"); // 等麥克風真的開了再開口,前面的字才不會被吃掉
  };
  r.onspeechstart = () => dbg("辨識 speechstart(偵測到說話)");
  r.onnomatch = () => dbg("辨識 nomatch");
  r.onresult = (e) => {
    press.results = [...e.results];
    dbg(`辨識 result:${[...e.results].map((x) => x[0].transcript).join(" | ")}`);
  };
  r.onerror = (e) => {
    press.error = e.error;
    press.errorMessage = e.message || "";
    dbg(`辨識 error:${e.error}${e.message ? `(${e.message})` : ""}`);
  };
  r.onend = () => {
    dbg(`辨識 end(${press.released ? "已放開" : "還沒放開就結束了"})`);
    state.lastEndAt = Date.now();
    press.resolveEnded();
    pttFinished(press);
  };
  state.ptt = press;
  updateButtons();
  $("micBtn").classList.add("on");
  setStatus("準備中…", "think");
  dbg(`pttStart(continuous=${r.continuous})`);
  // 連續啟動太快,iPhone 會把新的一次直接靜音:先等上一次完全結束
  const prev = state.prevEnded;
  state.prevEnded = press.ended;
  if (prev) await Promise.race([prev, sleep(1500)]);
  if (isIOS) await sleep(300);
  if (state.ptt !== press) return; // 等待期間已被取消(切換模式等)
  if (press.released && !press.tapMode) { // 等待期間已經放開(而且不是點一下):不用再開始
    state.ptt = null;
    press.resolveEnded();
    return;
  }
  try { r.start(); } catch (err) {
    dbg(`start() 丟出例外:${err.name} ${err.message}`);
    state.ptt = null;
    $("micBtn").classList.remove("on");
    showNotice("無法開始收音,請再按一次。");
  }
}

function pttEnd() {
  if (autoEnd()) return; // 手機:點一下開始、再點一下結束,放開手指不算結束
  const press = state.ptt;
  if (!press || press.released) return;
  press.released = true;
  $("micBtn").classList.remove("on");
  const held = Date.now() - press.startedAt;
  dbg(`放開(按住 ${held} 毫秒)`);
  if (held < MIN_HOLD_MS) {
    if (autoEnd()) { // 點一下:改成「點一下說話」,說完停頓就會自動結束
      press.tapMode = true;
      $("micBtn").classList.add("on");
      setStatus("請說話…(說完停頓,或按「停止」)", "listen");
      return;
    }
    press.tooShort = true;
    try { press.rec.abort(); } catch { /* 已結束 */ }
    setStatus("按太短了,請按住說話", "error");
    return;
  }
  setStatus("辨識中…", "think");
  try { press.rec.stop(); } catch { /* 已結束 */ }
}

function describeError(code, message = "") {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "沒有麥克風或語音辨識權限。iPhone 請到 設定 > 一般 > 鍵盤 開啟「聽寫」,並在 Safari 允許使用麥克風。";
    case "audio-capture":
      return /muted/i.test(message)
        ? "麥克風被靜音或被其他功能佔用(例如 Siri、通話、聽寫、另一個正在錄音的網頁)。請先關閉那些功能,等幾秒再試;還是不行就重新整理網頁(或關掉分頁重開)。"
        : "找不到麥克風。";
    case "network": return "辨識服務連不上,請檢查網路後再試。";
    case "no-speech": return "沒有聽到說話聲,請靠近麥克風再試一次。";
    case "language-not-supported": return "這個瀏覽器不支援中文語音辨識。";
    default: return `辨識出錯(${code})。`;
  }
}

function pttFinished(press) {
  if (state.ptt === press) state.ptt = null;
  $("micBtn").classList.remove("on");
  updateButtons();
  if (press.tooShort) return;
  if (press.error && press.error !== "aborted") {
    showNotice(describeError(press.error, press.errorMessage));
    return idleStatus();
  }
  const alts = collectAlts(press.results);
  if (!alts.length) {
    const noSpeech = /no speech/i.test(press.errorMessage || "") || press.error === "no-speech";
    showNotice(noSpeech
      ? "沒有偵測到說話聲。請按住後等畫面出現「請說話」再開口,並靠近麥克風、說大聲一點。"
      : press.released
        ? "沒有收到辨識結果,請再按住說話。"
        : "收音自己提早結束了,沒有收到辨識結果。請再按住說話,並看「診斷紀錄」。");
    setStatus("沒聽到聲音,請再按住說話", "error");
    return;
  }
  submit(alts);
}

/** continuous 模式可能有好幾段結果:只有一段就用它的候選,多段就把各段第一名接起來。 */
function collectAlts(results) {
  const segs = results.map((r) => [...r].map((a) => a.transcript.trim()).filter(Boolean)).filter((a) => a.length);
  if (!segs.length) return [];
  if (segs.length === 1) return segs[0];
  return [segs.map((a) => a[0]).join("")];
}

async function keepAwake() {
  try { state.wake = await navigator.wakeLock?.request("screen"); } catch { /* 不支援就算了 */ }
}

// ───────────── 事件 ─────────────

$("micBtn").addEventListener("click", () => {
  if (state.mode === "ptt") {
    // 手機:點一下開始、再點一下結束。click 才會被當成「使用者操作」,朗讀與收音都需要這個
    if (isPhone) {
      dbg(state.ptt ? "click(結束)" : "click(開始)");
      if (!state.ptt) unlockSpeech();
      pttStart();
    }
    return; // 其他裝置用下面的 pointer 事件(按住說話)
  }
  if (state.active) stopSession();
  else session();
});
$("stopSpeakBtn").addEventListener("click", stopSpeaking);
$("yesBtn").addEventListener("click", () => submit(["是"]));
$("noBtn").addEventListener("click", () => submit(["不是"]));

// 按住說話:按下開始、放開結束(用 pointer capture,手指滑出按鈕也會收到放開事件)
const mic = $("micBtn");
mic.addEventListener("pointerdown", (e) => {
  if (state.mode !== "ptt" || isPhone) return;
  e.preventDefault();
  dbg(`pointerdown(${e.pointerType})`);
  try { mic.setPointerCapture(e.pointerId); } catch { /* 不支援就算了 */ }
  pttStart();
});
["pointerup", "pointercancel"].forEach((t) => mic.addEventListener(t, () => {
  if (state.mode !== "ptt" || isPhone) return;
  dbg(t);
  pttEnd();
}));
mic.addEventListener("contextmenu", (e) => state.mode === "ptt" && e.preventDefault());
// 電腦上也可以按住空白鍵說話(游標不在輸入框時)
addEventListener("keydown", (e) => {
  if (state.mode === "ptt" && e.code === "Space" && !e.repeat && !/INPUT|SELECT|TEXTAREA|BUTTON/.test(e.target.tagName)) {
    e.preventDefault();
    pttStart();
  }
});
addEventListener("keyup", (e) => {
  if (state.mode === "ptt" && e.code === "Space") pttEnd();
});

$("typedForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("typed").value.trim();
  $("typed").value = "";
  state.listAll = false;
  renderResults();
  if (text) submit([text]);
});
$("typed").addEventListener("input", renderResults);
$("listBtn").addEventListener("click", () => {
  state.listAll = !state.listAll;
  renderResults();
});

// 聆聽模式切換:常開監聽 / 按住說話
function setMode(mode, { save = true } = {}) {
  if (save) unlockSpeech(); // 使用者點了切換鈕:這是解鎖朗讀的好時機
  if (state.active) stopSession();
  if (state.ptt) {
    try { state.ptt.rec.abort(); } catch { /* 已結束 */ }
    state.ptt.tooShort = true;
    state.ptt = null;
  }
  state.mode = mode;
  if (save) localStorage.setItem(KEY.mode, mode);
  document.querySelectorAll('input[name="mode"]').forEach((r) => { r.checked = r.value === mode; });
  mic.classList.toggle("ptt", mode === "ptt");
  showNotice("");
  updateButtons();
  idleStatus();
}
document.querySelectorAll('input[name="mode"]').forEach((r) => r.addEventListener("change", () => r.checked && setMode(r.value)));

$("file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (f) await loadFile(f);
  e.target.value = "";
});
$("sampleBtn").addEventListener("click", async () => { await useSample(); setHeard("已改用範例資料"); });
$("clearBtn").addEventListener("click", async () => { await useSample(); setHeard("已清除載入的資料,改回範例資料"); });

$("greetOn").addEventListener("change", (e) => {
  state.greetOn = e.target.checked;
  localStorage.setItem(KEY.greet, e.target.checked ? "1" : "0");
});
$("rate").addEventListener("change", (e) => {
  state.rate = Number(e.target.value);
  localStorage.setItem(KEY.rate, e.target.value);
});
document.addEventListener("visibilitychange", () => {
  if (state.active && document.visibilityState === "visible") keepAwake();
});

// ───────────── 啟動 ─────────────

state.greetOn = localStorage.getItem(KEY.greet) !== "0";
$("greetOn").checked = state.greetOn;

const savedRate = localStorage.getItem(KEY.rate);
if (savedRate) { state.rate = Number(savedRate); $("rate").value = savedRate; }

if (!SR) showNotice("這個瀏覽器不支援語音辨識(建議用 Chrome、Edge 或 Safari)。仍可用下方輸入框打字查詢,並由語音朗讀。");
else if (!synth) showNotice("這個瀏覽器不支援語音朗讀。");

// 預設:iPhone/iPad 的 Safari 不允許自動開始聆聽,預設用按住說話;其他裝置預設常開監聽
const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const isPhone = isIOS || /Android/i.test(navigator.userAgent);
if (isPhone) {
  const label = document.querySelector('input[name="mode"][value="ptt"] + span');
  if (label) label.textContent = "點擊監聽";
  $("typedHint").hidden = false; // 手機鍵盤有聽寫:提示放在輸入框下面(可換行),輸入框裡的提示字保持簡短
}
setMode(localStorage.getItem(KEY.mode) || (isIOS ? "ptt" : "auto"), { save: false });

loadInitial().then(updateButtons);
