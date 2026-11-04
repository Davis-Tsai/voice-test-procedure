"""讀取 Excel 對照表,並把語音辨識出來的文字比對到測試項目。"""
import re
import unicodedata
from dataclasses import dataclass, field

from openpyxl import load_workbook
from rapidfuzz import fuzz
from zhconv import convert

ACCEPT = 85    # 達到此分數且明顯領先時,直接朗讀
SUGGEST = 60   # 達到此分數但不夠確定時,先問使用者「是不是這個?」
MARGIN = 5     # 第一名要領先第二名多少分才算「明顯領先」

_CN_DIGITS = str.maketrans({
    "零": "0", "〇": "0", "一": "1", "二": "2", "兩": "2", "两": "2", "三": "3",
    "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9",
})

_HEADERS = {
    "id": ("編號", "项目编号", "項目編號", "编号", "id", "no", "no."),
    "name": ("名稱", "項目名稱", "项目名称", "名称", "name"),
    "aliases": ("別名", "别名", "alias", "aliases"),
    "steps": ("步驟", "步骤", "steps", "step"),
}
# 「.」「:」後面緊接數字是小數或時間(3.3V、1:30),不是步驟編號,不能去掉
_STEP_PREFIX = re.compile(r"^\s*(?:第?\s*\d+\s*(?:[步、\)）]|[\.:：](?!\d))|[①-⑳])\s*")


@dataclass
class Procedure:
    id: str
    name: str
    aliases: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    id_key: str = ""
    name_keys: list = field(default_factory=list)


def normalize(text):
    """統一成可比對的形式:全形半形、大小寫、簡繁、中文數字、去掉空白與標點。"""
    text = unicodedata.normalize("NFKC", str(text)).lower()
    text = convert(text, "zh-cn").translate(_CN_DIGITS)
    return re.sub(r"[^0-9a-z一-鿿]", "", text)


def _cell(value):
    return "" if value is None else str(value).strip()


def load_procedures(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = list(wb.active.iter_rows(values_only=True))
    finally:
        wb.close()  # 讀完就放開檔案,否則在 Windows 上程式執行期間 Excel 無法存檔
    if not rows:
        raise ValueError(f"{path} 是空的")

    col = {}
    for i, head in enumerate(rows[0]):
        head = _cell(head).lower()
        for key, names in _HEADERS.items():
            if head in names:
                col[key] = i
    missing = [k for k in ("id", "name", "steps") if k not in col]
    if missing:
        raise ValueError(f"第一列缺少欄位:{missing}。需要「編號、名稱、別名(選填)、步驟」")

    procs = []
    for row in rows[1:]:
        get = lambda k: _cell(row[col[k]]) if k in col and col[k] < len(row) else ""
        pid, name = get("id"), get("name")
        if not pid and not name:
            continue
        aliases = [a.strip() for a in re.split(r"[;；,，、|\n]", get("aliases")) if a.strip()]
        steps = [_STEP_PREFIX.sub("", s).strip() for s in get("steps").splitlines()]
        p = Procedure(pid, name, aliases, [s for s in steps if s])
        p.id_key = normalize(pid)
        p.name_keys = [k for k in (normalize(x) for x in [name, *aliases]) if k]
        procs.append(p)
    return procs


def _score(query, p):
    best = 0
    if p.id_key:
        if query == p.id_key:
            return 100
        # 句子裡含有編號,但後面不能緊接著數字(避免 tc1 吃掉 tc10)
        if re.search(rf"(?<![0-9a-z]){re.escape(p.id_key)}(?![0-9])", query):
            return 98
        if len(p.id_key) >= 4:  # 編號聽錯一個字時,只給「建議」等級的分數
            r = fuzz.ratio(query, p.id_key)
            best = min(80, r) if r >= 75 else 0
    for key in p.name_keys:
        if query == key:
            return 100
        if len(key) >= 2 and key in query:
            s = 96
        elif len(query) >= 2 and query in key:
            s = 88
        else:
            s = min(94, max(fuzz.ratio(query, key),
                            0.95 * fuzz.partial_ratio(query, key) if min(len(query), len(key)) >= 3 else 0))
        best = max(best, s)
    return best


def rank(text, procs, limit=3):
    """回傳 [(分數, Procedure), ...],由高到低,只保留達到建議門檻的。"""
    q = normalize(text)
    if not q:
        return []
    scored = sorted(((_score(q, p), p) for p in procs), key=lambda x: -x[0])
    return [(s, p) for s, p in scored[:limit] if s >= SUGGEST]


def is_confident(ranked):
    if not ranked or ranked[0][0] < ACCEPT:
        return False
    return len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= MARGIN


_REPEAT = [normalize(w) for w in ("重複", "重來", "再一次", "再說一次", "再念一次", "再唸一次", "repeat", "again")]
_QUIT = [normalize(w) for w in ("結束", "離開", "退出", "quit", "exit")]


def command(text):
    q = normalize(text)
    if any(w in q for w in _REPEAT):
        return "repeat"
    if any(w in q for w in _QUIT):
        return "quit"
    return None


def yes_no(text):
    """回傳 True / False / None(聽不出來)。"""
    q = normalize(text)
    if q in ("no", "nope", "nah") or re.search("不|否|没有", q):
        return False
    if q in ("yes", "yeah", "yep", "ok", "okay", "sure") or re.search("没错|对|是|好|要|确定|嗯", q):
        return True
    if "错" in q:
        return False
    return None


# ───────────── 關鍵字搜尋與語音查詢指令(與網頁版 logic.js 一致)─────────────

def search(query, procs):
    """輸入幾個字(可用空白分成多個關鍵字),列出編號、名稱、別名含有這些字的所有項目。
    每個關鍵字都要找得到;編號開頭 > 編號內含 > 名稱 > 別名,同分維持原本順序。回傳 [(分數, Procedure)]。"""
    tokens = [t for t in (normalize(x) for x in str(query).split()) if t]
    if not tokens:
        return []
    hits = []
    for order, p in enumerate(procs):
        name = normalize(p.name)
        aliases = [normalize(a) for a in p.aliases]
        score = 0
        for t in tokens:
            if p.id_key.startswith(t):
                s = 4
            elif t in p.id_key:
                s = 3
            elif t in name:
                s = 2
            elif any(t in a for a in aliases):
                s = 1
            else:
                score = None
                break
            score += s
        if score is not None:
            hits.append((score, order, p))
    hits.sort(key=lambda h: (-h[0], h[1]))
    return [(score, p) for score, _, p in hits]


_PUNCT = " \t,。.,!?!?、::"
_QUERY_PREFIX = re.compile(r"^(?:查詢|查询|搜尋|搜寻|搜索|尋找|寻找|查找|列出|找|search|find|list)\s*(.*)$", re.I)


def parse_query(text):
    """語音「查詢」指令:句首是 查詢/搜尋/找/列出(或 search/find/list)才算,後面接關鍵字。
    回傳 ("search", 關鍵字) / ("all", None) / ("close", None) / None。只認句首,所以「檢查電源」不會被誤判。"""
    t = str(text).strip(_PUNCT)
    if re.fullmatch(r"(?:關閉|关闭|收起|隱藏|隐藏)(?:項目)?(?:清單|清单|列表)?|(?:close|hide)(?:\s+list)?", t, re.I):
        return ("close", None)
    if re.fullmatch(r"全部|所有|全部項目|所有項目|全部项目|所有项目|項目清單|项目清单|項目列表|项目列表|清單|清单|列表", t):
        return ("all", None)
    m = _QUERY_PREFIX.match(t)
    if not m:
        return None
    kw = m.group(1).strip(_PUNCT)
    if re.fullmatch(r"(?:全部|所有)(?:項目|项目)?|all", kw, re.I):
        return ("all", None)
    return ("search", kw)
