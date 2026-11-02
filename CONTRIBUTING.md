# 協作指南(給不熟 Git 的人)

這份文件教你怎麼把最新的專案拿下來、改完再放回 GitHub,**不需要記任何指令**,用圖形介面 GitHub Desktop 就可以。

## 先記住這四件事

1. **`main` 分支就是正式版。** 推上 `main` 之後,約一分鐘內手機網頁(GitHub Pages)就會自動更新,所有人立刻看到。所以**不要直接改 `main`**,改用「新分支 + Pull Request」,讓專案擁有者看過再合併。
2. **真正的測試項目 Excel 絕對不要放進專案。** 這個專案是公開的,任何人都看得到。範例檔 `procedures.sample.xlsx` 可以放,你自己的 `procedures.xlsx` 已經被 `.gitignore` 擋住,**不要改檔名繞過去**。
3. **開始工作前,先拉最新的;做完才推。** 這樣可以避免你的版本和別人的打架。
4. **不確定就先停,問擁有者。** Git 幾乎所有操作都能復原,但「不小心推上去的機密」例外(見最後一節)。

---

## 一、只想使用(不改程式)

- **最簡單:** 到專案首頁按綠色 **Code → Download ZIP**,解壓縮就能用。要更新時再下載一次。
- **手機:** 直接開網頁:https://davis-tsai.github.io/voice-test-procedure/ ,不用下載任何東西。
- 桌面版怎麼執行、怎麼放自己的 Excel,請看 [README.md](README.md)。

## 二、第一次設定(只做一次)

1. **有 GitHub 帳號**,並請專案擁有者把你加為協作者(擁有者:Settings → Collaborators → Add people)。你會收到 Email,按接受邀請。
2. **安裝 GitHub Desktop**:https://desktop.github.com/ ,用 GitHub 帳號登入。
3. **取得專案:** GitHub Desktop → File → **Clone repository** → 選 `Davis-Tsai/voice-test-procedure` → 選一個放的資料夾 → Clone。
4. **(只有要執行桌面版才需要)** 安裝 Python 3.11,在專案資料夾開終端機,執行:

   ```bash
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   ```

   之後用 `.venv\Scripts\python.exe main.py` 執行。只改網頁的人不需要這一步。

## 三、每次工作的流程

| 步驟 | 在 GitHub Desktop 怎麼做 | 為什麼 |
|---|---|---|
| 1. 拉最新 | 上方 **Fetch origin**,如果變成 **Pull origin** 就再按一次 | 先拿到別人的更新 |
| 2. 開新分支 | 左上 **Current Branch → New Branch**,名稱例如 `小明-調整字體` | 不動正式版 |
| 3. 修改檔案 | 用你習慣的編輯器改專案資料夾裡的檔案 | |
| 4. 本機測試 | 見下一節 | 確認沒壞再送 |
| 5. 提交 | 左下填一句**簡短說明**(例如「步驟字體再縮小一點」)→ **Commit to 分支名稱** | 把這批修改存成一個紀錄 |
| 6. 推上 GitHub | 按 **Publish branch**(之後是 **Push origin**) | 放到網路上 |
| 7. 發出 Pull Request | 按 **Create Pull Request**,瀏覽器會打開,填說明後送出 | 請擁有者檢查 |
| 8. 等合併 | 擁有者按 **Merge** 後,網頁約一分鐘自動更新 | |
| 9. 收尾 | 切回 `main`,按 **Pull origin**,把舊分支刪掉(Branch → Delete) | 準備下一次 |

**提醒:** 一個分支只做一件事,Pull Request 比較容易審。

## 四、本機測試

- **手機網頁版(改了 `web/` 底下的檔案):** 在專案資料夾開終端機執行

  ```bash
  python -m http.server 8765 --directory web
  ```

  再用瀏覽器開 http://localhost:8765 。(瀏覽器會快取檔案,改了沒變化時按 Ctrl+F5 強制重新整理。)
- **桌面版(改了 `.py`):** `.venv\Scripts\python.exe main.py`。

## 五、哪些檔案是做什麼的

| 位置 | 內容 |
|---|---|
| `web/` | 手機/瀏覽器網頁版(`app.js` 流程、`logic.js` 比對與朗讀內容、`index.html`、`style.css`) |
| `main.py`、`ui.py`、`speech_io.py`、`procedures.py`、`settings.py` | 桌面版 |
| `procedures.sample.xlsx`、`web/sample.xlsx`、`web/sample.json` | **範例**資料(可以改,但不能放真實資料) |
| `.github/workflows/pages.yml` | 自動發布網頁的設定,**不要隨便改** |

網頁版和桌面版的功能要**保持一致**:改了其中一邊的行為(例如比對規則、朗讀方式),請想一下另一邊要不要一起改。

## 六、常見問題

- **推送被拒絕(rejected):** 先 **Pull origin**,再推一次。
- **出現「衝突 conflict」:** 代表你和別人改了同一行。不要硬解,把畫面截圖給擁有者。
- **想放棄還沒提交的修改:** 在檔案上按右鍵 → **Discard changes**(這個無法復原,確認再按)。
- **改錯了想回到上一版:** 在 History 裡對那一筆按右鍵 → **Revert changes in commit**。
- **不小心把真實的 Excel 或密碼 commit / 推上去了:** **立刻告訴擁有者,不要自己只是刪掉檔案**。因為專案是公開的,歷史紀錄裡還是看得到,需要另外處理(清除歷史、更換密碼)。

## 七、給專案擁有者

- **`main` 已經設定保護(已設定):** 必須經過 Pull Request,且需要 **1 位核准**才能合併;禁止強制推送和刪除分支;提出 PR 後如果又有新的修改,之前的核准會作廢,要重新核准。協作者不可能直接動到正式版。
  - 擁有者(管理員)不受這些規則限制,可以直接推,也可以自己發的 PR 直接合併。
  - 注意:任何有寫入權限的協作者都能核准別人的 PR。如果想只有擁有者能核准,需要另外設定 CODEOWNERS 或 rulesets。
  - 想調整或拿掉:Settings → Branches → 找到 `main` 的規則。
- **邀請協作者:** Settings → Collaborators → Add people,對方要到 Email 按接受邀請。
- **審查重點:** 有沒有夾帶 `.xlsx` 真實資料、有沒有改到 `.github/workflows/`、網頁版和桌面版行為是否一致。
- 合併後到 **Actions** 分頁確認 "Deploy web to GitHub Pages" 是綠色勾勾。
