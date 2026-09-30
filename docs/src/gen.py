"""Generate design diagrams (SVG files for Markdown + inline fragments for HTML)."""
import hashlib, html, os, re, sys

OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.makedirs(f"{OUT}/images", exist_ok=True)

FONT = "'IBM Plex Sans JP','Noto Sans JP','Hiragino Sans',sans-serif"
MONO = "'IBM Plex Mono',ui-monospace,monospace"

# Standalone SVG style (used when the SVG is viewed as an image file)
SVG_STYLE = f"""
<style>
svg{{--bg:#F4F7F8;--sf:#FFFFFF;--ink:#15212B;--mu:#5C6B75;--ln:#C9D4DA;--ac:#00818A;--acs:#E0F1F2;--led:#3D7BFF;--ok:#15803D}}
@media (prefers-color-scheme:dark){{svg{{--bg:#0E1519;--sf:#152026;--ink:#E2EAEE;--mu:#8FA0AA;--ln:#2E3E47;--ac:#3CBFC7;--acs:#16353A;--led:#7AA2FF;--ok:#4ADE80}}}}
</style>"""

# Shared class rules (standalone SVG and HTML page both use these, via tokens)
CLASS_CSS = f"""
.d-bg{{fill:var(--bg)}}
.d-zone{{fill:none;stroke:var(--ln);stroke-width:1.2;stroke-dasharray:5 4}}
.d-box{{fill:var(--sf);stroke:var(--ln);stroke-width:1.2}}
.d-box2{{fill:var(--acs);stroke:var(--ac);stroke-width:1.2}}
.d-t{{fill:var(--ink);font-family:{FONT};font-size:13px}}
.d-h{{fill:var(--ink);font-family:{FONT};font-size:14px;font-weight:600}}
.d-z{{fill:var(--mu);font-family:{FONT};font-size:12px;font-weight:600;letter-spacing:.06em}}
.d-s{{fill:var(--mu);font-family:{FONT};font-size:11.5px}}
.d-m{{fill:var(--mu);font-family:{MONO};font-size:11.5px}}
.d-a{{fill:var(--ac);font-family:{FONT};font-size:11.5px;font-weight:600}}
.d-ar{{stroke:var(--ac);stroke-width:1.6;fill:none}}
.d-arm{{stroke:var(--mu);stroke-width:1.3;fill:none;stroke-dasharray:4 3}}
.d-head{{fill:var(--ac)}}
.d-headm{{fill:var(--mu)}}
.d-tag{{fill:var(--sf);stroke:var(--ac);stroke-width:2}}
.d-led-on{{fill:var(--led)}}
.d-led-off{{fill:var(--ln)}}
"""

DEFS = """<defs>
<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="d-head" d="M0,0L10,5L0,10z"/></marker>
<marker id="am" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="d-headm" d="M0,0L10,5L0,10z"/></marker>
</defs>"""


def box(x, y, w, h, title=None, lines=(), cls="d-box", mono=False, r=8):
    s = f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>'
    ty = y + 22
    if title:
        s += f'<text class="d-h" x="{x+12}" y="{ty}">{title}</text>'
        ty += 22
    for ln in lines:
        c = "d-m" if mono else "d-t"
        s += f'<text class="{c}" x="{x+14}" y="{ty}">{ln}</text>'
        ty += 19
    return s


def zone(x, y, w, h, label):
    return (f'<rect class="d-zone" x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>'
            f'<text class="d-z" x="{x+14}" y="{y+22}">{label}</text>')


def arrow(pts, cls="d-ar", both=False, marker="ah"):
    d = "M" + " L".join(f"{x},{y}" for x, y in pts)
    st = f' marker-start="url(#{marker})"' if both else ""
    return f'<path class="{cls}" d="{d}" marker-end="url(#{marker})"{st}/>'


def text(x, y, s, cls="d-s", anchor="start"):
    return f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}">{s}</text>'


def tag(cx, cy):
    return (f'<circle class="d-tag" cx="{cx}" cy="{cy}" r="11"/>'
            f'<text class="d-a" x="{cx}" y="{cy+4}" text-anchor="middle" style="font-size:8px">NFC</text>')


# Patterns: '#' = full brightness(7), '+' = dim(3), '.' = off
PATTERNS = [
    ("IDLE", "待機", "カードのタッチ待ち", "青", "常時・ゆっくり明滅",
     ["+++++++++++++", "+...........+", "+...#####...+", "+...#...#...+", "+...#...#...+", "+...#####...+", "+...........+", "+++++++++++++"]),
    ("USER", "ユーザー認識", "10秒以内に備品をタッチ", "青", "最大10秒・下段バーが減る",
     [".....###.....", ".....###.....", "......#......", "....#####....", "......#......", ".....#.#.....", ".............", "#########++++"]),
    ("CHECKOUT", "貸出", "棚から出ていく矢印", "緑", "2秒",
     ["#............", "#.......#....", "#.......##...", "#.#########..", "#.#########..", "#.......##...", "#.......#....", "#............"]),
    ("RETURN", "返却", "棚へ戻る矢印", "緑", "2秒",
     ["#............", "#...#........", "#..##........", "#.#########..", "#.#########..", "#..##........", "#...#........", "#............"]),
    ("TRANSFER", "貸出者切替", "前の人を返却し新しい人へ貸出", "緑", "2秒",
     [".........#...", "..#########..", ".........#...", ".............", ".............", "...#.........", "..#########..", "...#........."]),
    ("ERROR", "エラー", "順番違い・無効化されたタグ", "赤", "2秒",
     ["...#.....#...", "....#...#....", ".....#.#.....", "......#......", ".....#.#.....", "....#...#....", "...#.....#...", "............."]),
    ("UNKNOWN", "未登録タグ", "Web画面の未登録一覧に追加", "黄", "2秒",
     [".....###.....", "....#...#....", "........#....", ".......#.....", "......#......", "......#......", ".............", "......#......"]),
    ("CAPTURED", "登録用に読取", "Web画面の登録フォームへUIDを送信", "水色", "2秒",
     [".............", "......#......", "......#......", "...#######...", "......#......", "......#......", ".............", "............."]),
    ("OFFLINE", "サーバー未接続", "管理機能と通信できない", "赤（点滅）", "3秒",
     [".##.......##.", ".#....#....#.", "......#......", "......#......", "......#......", ".............", ".#....#....#.", ".##.......##."]),
]
for p in PATTERNS:
    assert len(p[5]) == 8 and all(len(r) == 13 for r in p[5]), p[0]


def matrix(x, y, rows, pitch, rad, cls_on="d-led-on", cls_off="d-led-off"):
    s = ""
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            cx, cy = x + i * pitch + pitch / 2, y + j * pitch + pitch / 2
            if ch == "#":
                s += f'<circle class="{cls_on}" cx="{cx}" cy="{cy}" r="{rad}"/>'
            elif ch == "+":
                s += f'<circle class="{cls_on}" cx="{cx}" cy="{cy}" r="{rad}" opacity=".38"/>'
            else:
                s += f'<circle class="{cls_off}" cx="{cx}" cy="{cy}" r="{rad*0.72}"/>'
    return s


# ---------------------------------------------------------------- overview
def overview():
    W, H = 1000, 660
    b = [f'<rect class="d-bg" x="0" y="0" width="{W}" height="{H}"/>']
    # Phase 1 header
    b.append(text(20, 26, "PHASE 1　UNO Q 1台で全機能を動かす", "d-z"))
    # Field
    b.append(zone(20, 40, 250, 390, "設置場所（貸出カウンター）"))
    b.append(box(40, 78, 110, 58, "ユーザー", ["社員証に貼付"]))
    b.append(tag(128, 95))
    b.append(box(40, 176, 110, 58, "備品", ["PC・工具など"]))
    b.append(tag(128, 193))
    b.append(box(178, 120, 76, 72, "RC-S380", [], cls="d-box2"))
    b.append(text(216, 172, "NFCリーダー", "d-s", "middle"))
    b.append(arrow([(150, 112), (176, 140)]))
    b.append(arrow([(150, 200), (176, 176)]))
    b.append(text(158, 108, "①", "d-a"))
    b.append(text(158, 222, "②", "d-a"))
    b.append(box(166, 268, 94, 66, "USB-C ハブ", ["給電(PD)付き"]))
    b.append(arrow([(216, 192), (216, 266)], "d-arm", marker="am"))
    b.append(text(222, 236, "USB", "d-s"))
    b.append(box(40, 268, 110, 66, "NTAG215", ["UIDだけを使用"], cls="d-box"))
    b.append(text(40, 368, "① ユーザー → ② 備品 の順にタッチ", "d-t"))
    b.append(text(40, 390, "同じ操作で貸出・返却・切替を判定", "d-s"))
    b.append(text(40, 410, "（判定は管理機能が行う）", "d-s"))

    # UNO Q
    b.append(zone(300, 40, 450, 390, "ARDUINO UNO Q"))
    b.append(text(318, 84, "MPU（Linux）", "d-s"))
    b.append(box(318, 94, 190, 150, "Edge（App コンテナ）",
                 ["touch_fsm", "mgmt_client", "display"], mono=True))
    b.append(text(332, 204, "2段階タッチの状態管理", "d-s"))
    b.append(text(332, 221, "管理機能の呼出し・LED指示", "d-s"))
    b.append(box(318, 262, 136, 48, "nfc-agent", [], cls="d-box2"))
    b.append(text(332, 300, "別コンテナ・USB読取", "d-s"))
    b.append(arrow([(373, 260), (373, 246)]))
    b.append(text(380, 256, "UID（HTTP）", "d-a"))
    b.append(box(540, 94, 192, 212, "Management（管理機能）",
                 ["REST API / WebSocket", "業務ルール（貸出判定）", "DB（SQLite）", "Web UI（静的ファイル）"], cls="d-box2"))
    b.append(text(554, 214, "FastAPI 単体アプリ", "d-s"))
    b.append(text(554, 231, "→ Phase 2 でそのまま移設", "d-a"))
    b.append(arrow([(508, 160), (538, 160)]))
    b.append(text(524, 150, "HTTP", "d-a", "middle"))
    # MCU
    b.append(box(318, 322, 414, 94, None))
    b.append(text(332, 344, "MCU（STM32 / sketch.ino）", "d-h"))
    b.append(text(332, 366, "Bridge.provide(\"show\", …)", "d-m"))
    b.append(text(332, 386, "LED Matrix 8×13 + RGB LED", "d-s"))
    b.append(matrix(600, 332, PATTERNS[2][5], 9, 3.1))
    b.append(arrow([(470, 244), (470, 320)]))
    b.append(text(478, 316, "Bridge（RPC）", "d-a"))
    b.append(arrow([(260, 300), (316, 290)], "d-arm", marker="am"))

    # Browsers
    b.append(zone(780, 40, 200, 390, "利用者・管理者の端末"))
    b.append(box(796, 80, 168, 132, "Web 管理画面", ["ダッシュボード", "備品・ユーザー", "貸出履歴", "未登録タグ"]))
    b.append(arrow([(734, 140), (794, 140)], both=True))
    b.append(text(764, 128, "LAN", "d-a", "middle"))
    b.append(box(796, 236, 168, 96, "アクセス権", ["閲覧：誰でも", "編集：管理者ログイン"]))
    b.append(text(796, 366, "PC・スマホのブラウザ", "d-s"))
    b.append(text(796, 386, "http://&lt;UNO Q&gt;:8000", "d-m"))

    # Phase 2
    b.append(text(20, 470, "PHASE 2　管理機能を別PCへ移す", "d-z"))
    b.append(zone(20, 484, 960, 160, "接続先 MGMT_URL を変えるだけで移行"))
    for k in (2, 1, 0):
        b.append(box(44 + k * 6, 520 - k * 6, 250, 96, None))
    b.append(text(58, 542, "UNO Q（端末・複数台可）", "d-h"))
    b.append(text(58, 566, "nfc-agent + Edge + LED", "d-t"))
    b.append(text(58, 586, "MGMT_URL=http://mgmt-pc:8000", "d-m"))
    b.append(arrow([(298, 568), (428, 568)]))
    b.append(text(363, 558, "HTTP + 端末トークン", "d-a", "middle"))
    b.append(box(430, 520, 290, 96, "管理PC（Docker）",
                 ["Management：Phase 1 と同じコード", "DB：SQLite → PostgreSQL（任意）"], cls="d-box2"))
    b.append(arrow([(722, 568), (794, 568)], both=True))
    b.append(box(796, 530, 168, 76, "Web 管理画面", ["URL が管理PCに変わる"]))

    body = f'{DEFS}{"".join(b)}'
    return W, H, body


# ---------------------------------------------------------------- LED patterns
def led_sheet():
    pitch, rad = 16, 5.4
    bw, bh = 13 * pitch + 24, 8 * pitch + 24
    gap, lab = 26, 62
    cols = 3
    W = cols * bw + (cols - 1) * gap + 40
    rows = (len(PATTERNS) + cols - 1) // cols
    H = rows * (bh + lab) + (rows - 1) * 18 + 40
    b = [f'<rect class="d-bg" x="0" y="0" width="{W}" height="{H}"/>']
    for n, (code, name, desc, rgb, dur, rows_) in enumerate(PATTERNS):
        cx, cy = 20 + (n % cols) * (bw + gap), 20 + (n // cols) * (bh + lab + 18)
        b.append(f'<rect x="{cx}" y="{cy}" width="{bw}" height="{bh}" rx="10" fill="#0B1322"/>')
        b.append(matrix(cx + 12, cy + 12, rows_, pitch, rad, "d-led-lit", "d-led-dark"))
        b.append(text(cx, cy + bh + 22, f'{name}', "d-h"))
        b.append(text(cx + bw, cy + bh + 22, code, "d-m", "end"))
        b.append(text(cx, cy + bh + 41, desc, "d-s"))
        b.append(text(cx, cy + bh + 58, f"RGB LED：{rgb}／{dur}", "d-s"))
    return W, H, "".join(b)


LED_CSS = ".d-led-lit{fill:#6FA0FF}.d-led-dark{fill:#1A2740}"


def standalone(W, H, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'role="img" aria-label="{title}"><title>{title}</title>{SVG_STYLE}'
            f'<style>{CLASS_CSS}{LED_CSS}</style>{body}</svg>\n')


def inline(W, H, body, title):
    # Several figures share one page: give each its own marker ids so arrowheads never
    # depend on another (possibly hidden) SVG's <defs>.
    uid = hashlib.md5(title.encode()).hexdigest()[:8]
    body = body.replace('id="ah"', f'id="ah-{uid}"').replace("url(#ah)", f"url(#ah-{uid})")
    body = body.replace('id="am"', f'id="am-{uid}"').replace("url(#am)", f"url(#am-{uid})")
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{title}" '
            f'style="width:100%;height:auto;min-width:{int(W*0.72)}px">{body}</svg>')


# ---------------------------------------------------------------- developer guide
def db(x, y, w, h, title, sub=""):
    """Database cylinder."""
    ry = 8
    body = (f'<path class="d-box2" d="M{x},{y+ry} L{x},{y+h-ry} A{w/2},{ry} 0 0 0 {x+w},{y+h-ry} '
            f'L{x+w},{y+ry}"/><ellipse class="d-box2" cx="{x+w/2}" cy="{y+ry}" rx="{w/2}" ry="{ry}"/>')
    body += text(x + w / 2, y + ry + 24, title, "d-h", "middle")
    if sub:
        body += text(x + w / 2, y + ry + 42, sub, "d-s", "middle")
    return body


def dev_envs():
    W, H = 960, 420
    pw = 288
    b = [f'<rect class="d-bg" x="0" y="0" width="{W}" height="{H}"/>']
    # 1: unit tests
    x0 = 20
    b.append(text(x0, 26, "① 単体テスト", "d-z"))
    b.append(zone(x0, 40, pw, 360, "UNO Q か Docker のある PC"))
    b.append(box(x0 + 16, 76, pw - 32, 150, "一時コンテナ", ["python-apps-base + pytest"], cls="d-box2"))
    b.append(box(x0 + 32, 134, pw - 64, 76, "pytest", ["test_management.py", "test_edge.py"], mono=True))
    b.append(arrow([(x0 + pw / 2, 210), (x0 + pw / 2, 248)]))
    b.append(db(x0 + 60, 250, pw - 120, 64, "SQLite（一時）", "テストごとに作って捨てる"))
    b.append(text(x0 + 16, 344, "sh tests/run.sh", "d-m"))
    b.append(text(x0 + 16, 364, "Edge は偽の API・LED で検証", "d-s"))
    b.append(text(x0 + 16, 382, "起動中の App には影響しない", "d-s"))
    # 2: real app + fake reader
    x0 = 336
    b.append(text(x0, 26, "② 実機の App ＋ 疑似リーダー", "d-z"))
    b.append(zone(x0, 40, pw, 360, "UNO Q"))
    b.append(box(x0 + 16, 76, 120, 70, "疑似リーダー", [":8100"], cls="d-box2", mono=True))
    b.append(arrow([(x0 + pw - 16, 111), (x0 + 138, 111)]))
    b.append(text(x0 + 150, 102, "curl /inject", "d-a"))
    b.append(box(x0 + 16, 168, pw - 32, 90, "App コンテナ", ["Edge ＋ Management", "app start で起動  :8000"], cls="d-box2"))
    b.append(arrow([(x0 + 76, 146), (x0 + 76, 166)]))
    b.append(text(x0 + 84, 161, "UID", "d-a"))
    b.append(db(x0 + 16, 300, 130, 60, "data/nfc.db", "本番と同じ"))
    b.append(box(x0 + 162, 300, pw - 178, 60, "MCU", ["LED 表示"]))
    b.append(arrow([(x0 + 81, 258), (x0 + 81, 298)]))
    b.append(arrow([(x0 + 217, 258), (x0 + 217, 298)]))
    b.append(text(x0 + 16, 386, "リーダーなしでタッチと LED を確認", "d-s"))
    # 3: management only
    x0 = 652
    b.append(text(x0, 26, "③ Management 単体", "d-z"))
    b.append(zone(x0, 40, pw, 360, "PC か UNO Q（Docker）"))
    b.append(box(x0 + 16, 76, pw - 32, 80, "management コンテナ", ["deploy/management  :8000"], cls="d-box2"))
    b.append(db(x0 + 16, 176, 130, 60, "SQLite", "data/nfc.db"))
    b.append(arrow([(x0 + 81, 156), (x0 + 81, 174)]))
    b.append(box(x0 + 162, 176, 90, 50, "ブラウザ", ["Web UI"]))
    b.append(arrow([(x0 + 207, 176), (x0 + 207, 158)]))
    b.append(box(x0 + 162, 242, pw - 178, 54, "curl", ["端末の代わり"]))
    b.append(arrow([(x0 + 264, 242), (x0 + 264, 158)]))
    b.append(text(x0 + 16, 344, "Web UI の変更：ブラウザを再読込", "d-s"))
    b.append(text(x0 + 16, 364, "Python の変更：コンテナを再起動", "d-s"))
    b.append(text(x0 + 16, 382, "Edge・LED・リーダーは動かない", "d-s"))
    return W, H, f'{DEFS}{"".join(b)}'


def processes():
    W, H = 960, 410
    b = [f'<rect class="d-bg" x="0" y="0" width="{W}" height="{H}"/>']
    b.append(zone(20, 40, 680, 350, "UNO Q：Linux（MPU）"))
    b.append(box(36, 76, 464, 298, "App コンテナ（unoq-nfc-lending-main-1）", [], cls="d-box2"))
    b.append(text(48, 118, "python main.py ─ 1 プロセス・複数スレッド", "d-m"))
    rows = (130, 214, 298)
    for y, (t, sub) in zip(rows, [("management", "uvicorn  :8000"), ("スレッドプール", "API の処理"), ("backup", "毎日の DB 保存")]):
        b.append(box(52, y, 200, 56, t, [sub]))
    for y, (t, sub) in zip(rows, [("メイン", "Edge（touch_fsm）"), ("nfc-reader", "/events を受信"), ("heartbeat", "60秒ごとに報告")]):
        b.append(box(288, y, 196, 56, t, [sub]))
    b.append(arrow([(286, 158), (254, 158)]))
    b.append(text(270, 150, "HTTP", "d-a", "middle"))
    b.append(arrow([(386, 214), (386, 188)]))
    b.append(text(394, 206, "キュー", "d-a"))
    b.append(box(520, 120, 164, 56, "arduino-router", ["ボード標準の中継"]))
    b.append(arrow([(486, 148), (518, 148)]))
    b.append(box(520, 214, 164, 70, "nfc-agent コンテナ", ["python agent.py", ":8100"]))
    b.append(arrow([(518, 252), (486, 252)]))
    b.append(text(520, 330, "ブラウザ → :8000", "d-s"))
    b.append(text(520, 350, "Edge → management も HTTP", "d-s"))
    b.append(zone(716, 40, 228, 150, "UNO Q：MCU"))
    b.append(box(732, 120, 196, 56, "sketch", ["LED Matrix・RGB LED"]))
    b.append(arrow([(684, 148), (730, 148)]))
    b.append(text(707, 140, "Bridge", "d-a", "middle"))
    b.append(zone(716, 196, 228, 100, "USB 機器"))
    b.append(box(732, 227, 196, 54, "RC-S380", ["NFC リーダー"]))
    b.append(arrow([(730, 254), (686, 254)]))
    return W, H, f'{DEFS}{"".join(b)}'


def prod_envs():
    W, H = 960, 450
    b = [f'<rect class="d-bg" x="0" y="0" width="{W}" height="{H}"/>']
    # Phase 1
    b.append(text(20, 26, "PHASE 1　UNO Q 1台", "d-z"))
    b.append(zone(20, 40, 440, 360, "Arduino UNO Q"))
    b.append(box(36, 76, 100, 50, "RC-S380", []))
    b.append(text(86, 118, "USB", "d-s", "middle"))
    b.append(arrow([(136, 101), (158, 101)]))
    b.append(box(160, 76, 284, 62, "nfc-agent コンテナ", ["docker compose・USB を許可"]))
    b.append(box(36, 160, 408, 180, "App コンテナ（arduino-app-cli）", [], cls="d-box2"))
    b.append(box(52, 196, 172, 62, "Management", ["スレッド  :8000"]))
    b.append(box(256, 196, 172, 62, "Edge", ["メインスレッド"]))
    b.append(arrow([(254, 227), (226, 227)]))
    b.append(db(58, 272, 160, 58, "data/nfc.db", "backups/・app.env"))
    b.append(arrow([(138, 258), (138, 270)]))
    b.append(arrow([(338, 138), (338, 194)]))
    b.append(text(346, 172, "UID（HTTP）", "d-a"))
    b.append(box(36, 352, 408, 40, "MCU（sketch）", []))
    b.append(text(430, 377, "LED Matrix・RGB LED", "d-s", "end"))
    b.append(arrow([(338, 258), (338, 350)]))
    b.append(text(346, 306, "Bridge", "d-a"))
    b.append(text(20, 424, "ブラウザ → http://&lt;UNO Q&gt;:8000", "d-m"))
    # Phase 2
    b.append(text(490, 26, "PHASE 2　管理機能を管理PCへ", "d-z"))
    b.append(zone(490, 40, 450, 196, "Arduino UNO Q（端末・複数台可）"))
    b.append(box(506, 76, 130, 56, "nfc-agent", ["USB 読取"]))
    b.append(arrow([(636, 104), (650, 104)]))
    b.append(box(652, 76, 272, 76, "App コンテナ", ["Edge のみ", "RUN_MANAGEMENT=false"], cls="d-box2"))
    b.append(box(506, 150, 130, 56, "MCU", ["LED 表示"]))
    b.append(arrow([(700, 152), (700, 178), (638, 178)]))
    b.append(text(652, 206, "data/app.env に", "d-s"))
    b.append(text(652, 223, "MGMT_URL・DEVICE_TOKEN", "d-m"))
    b.append(zone(490, 262, 450, 150, "管理PC（Docker）"))
    b.append(db(506, 296, 190, 64, "SQLite / PostgreSQL", "data/nfc.db か DATABASE_URL"))
    b.append(box(720, 298, 204, 62, "management コンテナ", ["deploy/management :8000"], cls="d-box2"))
    b.append(arrow([(718, 329), (698, 329)]))
    b.append(arrow([(840, 152), (840, 296)]))
    b.append(text(832, 252, "HTTP＋端末トークン", "d-a", "end"))
    b.append(text(490, 436, "ブラウザ → http://&lt;管理PC&gt;:8000", "d-m"))
    return W, H, f'{DEFS}{"".join(b)}'


def patterns_h():
    """C header for the sketch: one 8x13 grayscale frame (0..7) per pattern, same art as the figure."""
    level = {"#": 7, "+": 3, ".": 0}
    out = ["// Generated by docs/src/gen.py from PATTERNS - edit there, then run gen.py.",
           "#pragma once", "#include <stdint.h>", "",
           "enum Pattern : uint8_t {"]
    out += [f"  PAT_{p[0]} = {n}," for n, p in enumerate(PATTERNS)]
    out += [f"  PAT_COUNT = {len(PATTERNS)}", "};", "",
            f"const uint8_t PATTERN_FRAMES[{len(PATTERNS)}][104] = {{"]
    for code, name, *_rest, rows in PATTERNS:
        out.append(f"  // {code}（{name}）")
        out.append("  {")
        for r in rows:
            out.append("    " + ",".join(str(level[c]) for c in r) + ",")
        out.append("  },")
    out.append("};")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- Markdown -> HTML (developer guide)
def _inline(t: str) -> str:
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    # Links to repo files/anchors do not resolve on the published page: keep only the text.
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    return t


def md_to_html(md: str, figures: dict) -> tuple[str, str, list]:
    """Tiny converter for the subset used in developer-guide.md. Returns (title, body, nav)."""
    lines = md.splitlines()
    title, out, nav, para, i = "", [], [], [], 0
    by_file = {fname: key for key, (_, _, fname) in FIGURES.items()}
    open_section = False

    def flush():
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if ln.startswith("# "):
            title = ln[2:].strip()
        elif ln.startswith("## "):
            flush()
            if open_section:
                out.append("</section>")
            m = re.match(r"## (\d+)\. (.+)", ln)
            no, head = (m.group(1), m.group(2)) if m else ("", ln[3:])
            nav.append((no, head))
            out.append(f'<section id="s{no}"><h2><span class="no">{int(no):02d}</span>{_inline(head)}</h2>')
            open_section = True
        elif ln.startswith("### "):
            flush()
            out.append(f"<h3>{_inline(ln[4:])}</h3>")
        elif ln.startswith("```"):
            flush()
            lang, body = ln[3:].strip(), []
            i += 1
            while not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            code = html.escape("\n".join(body), quote=False)
            out.append(f'<pre class="mmd">\n{code}\n</pre>' if lang == "mermaid" else f'<pre class="code">{code}</pre>')
        elif ln.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            i -= 1
            head, body = rows[0], rows[2:]
            out.append('<div class="tbl"><table><thead><tr>' + "".join(f"<th>{_inline(c)}</th>" for c in head)
                       + "</tr></thead><tbody>" + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
                       + "</tbody></table></div>")
        elif ln.startswith("- "):
            flush()
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            i -= 1
            out.append("<ul>" + "".join(items) + "</ul>")
        elif m := re.match(r"!\[([^\]]*)\]\(images/([^)]+)\)", ln):
            flush()
            svg = figures.get(by_file.get(m.group(2), ""), "")
            out.append(f'<figure class="fig" style="margin:0">{svg}<figcaption>{html.escape(m.group(1))}</figcaption></figure>')
        elif ln.strip() in ("", "---"):
            flush()
        else:
            para.append(ln.strip())
        i += 1
    flush()
    if open_section:
        out.append("</section>")
    return title, "\n".join(out), nav


def build_guide(md_path: str, design_tpl: str, figures: dict) -> str:
    """Wrap the converted guide in the design doc's page chrome (styles, nav, Mermaid script)."""
    title, body, nav = md_to_html(open(md_path).read(), figures)
    first_section = body.index("<section")
    lead, body = body[:first_section], body[first_section:]
    head = design_tpl[:design_tpl.index('<div class="wrap">')]
    head = re.sub(r"<title>.*?</title>", f"<title>{html.escape(title)}</title>", head)
    tail = design_tpl[design_tpl.index('<script src="https://cdn.jsdelivr'):]
    nav_html = "".join(f'<li><a href="#s{no}"><span>{no}</span>{_inline(h)}</a></li>' for no, h in nav)
    lead = lead.replace("<p>", '<p class="lead">', 1)
    return (f'{head}<div class="wrap">\n<nav aria-label="目次"><ol>{nav_html}</ol></nav>\n<main>\n'
            f'<header><div class="eyebrow">Developer Guide · NFC備品管理</div><h1>{html.escape(title)}</h1>{lead}</header>\n'
            f'{body}\n</main>\n</div>\n{tail}')


FIGURES = {  # placeholder in a template -> (builder, title, SVG file name)
    "<!--OVERVIEW-->": (overview, "システム構成イメージ", "system-overview.svg"),
    "<!--LEDS-->": (led_sheet, "LED Matrix 表示パターン", "led-patterns.svg"),
    "<!--DEV-->": (dev_envs, "開発時の構成", "dev-environments.svg"),
    "<!--PROD-->": (prod_envs, "本番の構成", "prod-environments.svg"),
    "<!--PROC-->": (processes, "プロセスとスレッドの構成", "processes.svg"),
}
OUTPUT = {"template.html": "design.html"}


def main(templates):
    built = {}
    for key, (build, title, fname) in FIGURES.items():
        fig = build()
        built[key] = inline(*fig, title)
        open(f"{OUT}/images/{fname}", "w").write(standalone(*fig, title))
    open(os.path.join(os.path.dirname(OUT), "sketch", "patterns.h"), "w").write(patterns_h())
    here = os.path.dirname(os.path.abspath(__file__))
    for path in templates:
        tpl = open(path).read().replace("/*DIAGRAM_CSS*/", CLASS_CSS + LED_CSS)
        for key, svg in built.items():
            tpl = tpl.replace(key, svg)
        open(os.path.join(here, OUTPUT[os.path.basename(path)]), "w").write(tpl)
    # The developer guide's HTML is generated from its Markdown (single source).
    design_tpl = open(os.path.join(here, "template.html")).read().replace("/*DIAGRAM_CSS*/", CLASS_CSS + LED_CSS)
    guide = build_guide(os.path.join(OUT, "developer-guide.md"), design_tpl, built)
    open(os.path.join(here, "developer-guide.html"), "w").write(guide)
    print("ok")


if __name__ == "__main__":
    main(sys.argv[1:])
