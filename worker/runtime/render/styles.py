"""风格注册表与内置视觉稿（S3 风格层）。

**问题**（S2 结束时遗留）：:class:`RenderSpec` 的三个风格字段
（``style_id`` / ``art_style`` / ``image_set_id``）**没有任何渲染器消费**
——它们只是带着默认值躺在 spec 里，切换后渲出的画面完全相同，属「看起来
生效、实际无效」的误导（本仓最痛恨的静默失效）。

**本模块把它变成真实数据**：

- ``style_id``（版式风格）→ 决定**内置 HTML 视觉稿**：
  - ``ink_text`` 纸墨文字版：能力集 ``set()`` —— 零素材依赖（A 版，
    也是降级路径的落点）；
  - ``illustration`` 插画版：能力集 ``{"image"}`` —— 需要每幕配图（B 版）。
- ``art_style`` 正交于版式：只影响配图的**画面提示词**（IllustrateScenes
  已消费），不改布局、不需要在这里出现。
- ``image_set_id`` 是配图产物集的 id（尚未有表承载，S6/选型后落）。

**能力声明的意义**：渲染前看 ``style.capabilities`` 就知道这个风格**需要
什么输入**。缺输入时走降级（A 版作 B 版的降级路径），而不是把坏输入喂进
渲染器——降级发生在 handler（它看得到 scenes），不在 provider。

**内置视觉稿**：由共享骨架 + 每风格「画面函数」拼接而成（``file://`` 页面
不能 import 外部 JS，所以必须是单文件自包含 HTML）。产物是 ``style_id`` 的
纯函数 → 缓存到系统临时目录即可复用，同 t 同画面（逐帧契约）。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import NamedTuple

#: 风格能力标签：``image`` = 需要每幕配图
CAP_IMAGE = "image"

#: 内置视觉稿缓存目录（产物是 style_id 的纯函数，可安全复用）
_CACHE_DIR = os.path.join(tempfile.gettempdir(), "stepwork_styles")

#: 仓库打包字体根目录（resources/fonts，见其 README 的打包规则）
_FONTS_DIR = Path(__file__).resolve().parents[3] / "resources" / "fonts"

#: 已知字体的家族名登记（文件名无法推断时才需要）。键 = 文件名；值 =
#: (css family, weight)。同族不同字重以 weight 区分，@font-face 按需取用。
_FONT_META: dict[str, tuple[str, int]] = {
    "AlibabaPuHuiTi-2-55-Regular.ttf": ("Alibaba PuHuiTi", 400),
    "AlibabaPuHuiTi-2-65-Medium.ttf": ("Alibaba PuHuiTi", 500),
    "AlibabaPuHuiTi-2-85-Bold.ttf": ("Alibaba PuHuiTi", 700),
}


class StyleDef(NamedTuple):
    """一个版式风格的声明：能力 + 画面渲染所需的两段代码。"""

    id: str
    label: str
    #: 该风格需要什么输入（``{"image"}`` / ``set()``）
    capabilities: frozenset[str]
    #: 该风格的 CSS（放进 <style>）
    css: str
    #: 该风格的页面骨架（放进 <body>）
    body: str
    #: 该风格的画面函数 ``window._paint(one, t, k)``（与共享骨架同 <script>）
    paint_js: str

    @property
    def needs_image(self) -> bool:
        """该风格是否要求每幕有配图。"""
        return CAP_IMAGE in self.capabilities


# --------------------------------------------------------------------------
# 每风格的画面代码
# --------------------------------------------------------------------------
_PAPER_CSS = """
html, body { width: 1080px; height: 1920px; overflow: hidden; }
body {
  margin: 0;
  background:
    radial-gradient(1200px 1200px at 50% 42%, rgba(120,100,70,.10), rgba(0,0,0,0) 70%),
    #f2ecdd;
  color: #26221a;
  font-family: "Kaiti SC", "STKaiti", "KaiTi", "Noto Serif CJK SC", serif;
  position: relative;
}
/* 纸纹（细噪点，零外部资源） */
body::before {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: repeating-linear-gradient(0deg, rgba(0,0,0,.012) 0 2px, transparent 2px 6px);
}
#txt {
  position: absolute; left: 110px; right: 110px; top: 640px;
  text-align: center; line-height: 1.5; font-size: 72px;
  letter-spacing: .06em; font-weight: 700;
}
#txt em { font-style: normal; color: #b3382c; border-bottom: 6px solid rgba(179,56,44,.55); }
#seq {
  position: absolute; left: 90px; top: 150px; font-size: 40px; color: #8a7d5f;
  letter-spacing: .35em;
}
#seal {
  position: absolute; right: 130px; top: 180px; width: 150px; height: 150px;
  border: 8px solid #b3382c; border-radius: 18px; box-sizing: border-box;
  display: flex; align-items: center; justify-content: center;
  color: #b3382c; font-size: 72px; opacity: .9;
  box-shadow: inset 0 0 0 4px #f2ecdd, inset 0 0 0 6px #b3382c;
}
#mark {
  position: absolute; left: 90px; bottom: 140px; font-size: 40px;
  color: #9a8d6f; letter-spacing: .2em;
}
"""

_PAPER_BODY = """
<div id="seq">第 1 幕 / 1</div>
<div id="seal">言</div>
<div id="txt"></div>
<div id="mark">STEPWORK · ink_text</div>
"""

_PAPER_PAINT = r"""
function escHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function wrapCjk(s, n) {
  var t = String(s || "").replace(/\s+/g, " ");
  var out = [];
  for (var i = 0; i < t.length; i += n) out.push(t.slice(i, i + n));
  return out.slice(0, 3);  // 单幕至多 3 行，超出即截断（幕字数是可控的）
}
window._paint = function (one, t, k) {
  var seq = document.getElementById("seq");
  var idx = (window.__sceneIndex != null) ? (window.__sceneIndex + 1) : 1;
  seq.textContent = "第 " + idx + " 幕";
  if (one) {
    var box = document.getElementById("txt");
    var hi = one.highlight;
    var text = one.text || "";
    var html = "";
    wrapCjk(text, 11).forEach(function (line) {
      if (hi && line.indexOf(hi) >= 0) {
        html += escHtml(line.slice(0, line.indexOf(hi))) + "<em>" + escHtml(hi)
          + "</em>" + escHtml(line.slice(line.indexOf(hi) + hi.length)) + "<br>";
      } else {
        html += escHtml(line) + "<br>";
      }
    });
    box.innerHTML = html || "&nbsp;";
    // 幕内淡入（前 20% 渐显），配图/字幕不突变
    var a = k < 0.2 ? (0.3 + 0.7 * (k / 0.2)) : 1;
    box.style.opacity = a;
  }
  // 印章随 t 微浮 + 微转：保证每一帧画面确实不同（逐帧契约可复现 ≠ 静态）
  var seal = document.getElementById("seal");
  seal.style.transform = "translate3d(0," + (Math.sin(t * 1.3) * 8).toFixed(2)
    + "px,0) rotate(" + (Math.sin(t * 0.7) * 3).toFixed(2) + "deg)";
};
"""


_ILLUS_CSS = """
html, body { width: 1080px; height: 1920px; overflow: hidden; }
body {
  margin: 0; background: #14151a;
  /* 打包字体优先（resources/fonts/alibaba-puhuiti，@font-face 注入）；
     未打包时回退系统栈，保证任何机器都能渲 */
  font-family: "Alibaba PuHuiTi", "Noto Sans CJK SC", "Microsoft YaHei",
    "PingFang SC", sans-serif;
  color: #fff; position: relative;
}
#bg { position: absolute; inset: 0; overflow: hidden; background: #14151a; }
#bg img {
  width: 100%; height: 100%; object-fit: cover; display: block;
  transform-origin: center;
}
#cap {
  position: absolute; left: 0; right: 0; bottom: 0;
  padding: 220px 96px 150px 96px; box-sizing: border-box;
  background: linear-gradient(transparent, rgba(0,0,0,.72) 55%);
  font-size: 62px; line-height: 1.5; text-align: center; letter-spacing: .04em;
}
#cap em { font-style: normal; color: #FFD54F; }
#seq {
  position: absolute; left: 96px; top: 90px; font-size: 36px;
  color: rgba(255,255,255,.75); letter-spacing: .3em;
}
#mark {
  position: absolute; left: 96px; bottom: 60px; font-size: 32px;
  color: rgba(255,255,255,.55); letter-spacing: .25em;
}
"""

_ILLUS_BODY = """
<div id="bg"><img id="bgimg" alt="" src=""></div>
<div id="seq">01 / 01</div>
<div id="cap"></div>
<div id="mark">STEPWORK · illustration</div>
"""

_ILLUS_PAINT = r"""
function escHtml(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
window._paint = function (one, t, k) {
  var bg = document.getElementById("bg");
  var img = document.getElementById("bgimg");
  var cap = document.getElementById("cap");
  if (one) {
    // 慢速 ken-burns：图在幕内缓慢放大，画面动起来且不至于割裂
    var z = 1.04 + 0.05 * k;
    var tx = "scale(" + z.toFixed(4) + ") translate3d(0," + (k * 20).toFixed(1) + "px,0)";
    img.style.transform = tx;
    if (one.imageUri && img.getAttribute("src") !== one.imageUri) {
      img.setAttribute("src", one.imageUri);
      bg.style.display = "block";
    } else if (!one.imageUri) {
      bg.style.display = "none";  // 没有图就不显示图区（理论不该发生，见降级）
    }
    var hi = one.highlight, text = one.text || "", html = "";
    if (hi && text.indexOf(hi) >= 0) {
      html = escHtml(text.slice(0, text.indexOf(hi))) + "<em>" + escHtml(hi)
        + "</em>" + escHtml(text.slice(text.indexOf(hi) + hi.length));
    } else {
      html = escHtml(text);
    }
    cap.innerHTML = html || "&nbsp;";
    var a = k < 0.2 ? (k / 0.2) : 1;
    cap.style.opacity = a;
  }
  var seq = document.getElementById("seq");
  var cur = window.__sceneIndex != null ? window.__sceneIndex + 1 : 1;
  var total = DUR.length;
  seq.textContent = String(cur).padStart(2, "0")
    + " / " + String(total).padStart(2, "0");
};
"""


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------
STYLES: dict[str, StyleDef] = {
    "ink_text": StyleDef(
        id="ink_text",
        label="纸墨文字",
        capabilities=frozenset(),
        css=_PAPER_CSS,
        body=_PAPER_BODY,
        paint_js=_PAPER_PAINT,
    ),
    "illustration": StyleDef(
        id="illustration",
        label="插画",
        capabilities=frozenset({CAP_IMAGE}),
        css=_ILLUS_CSS,
        body=_ILLUS_BODY,
        paint_js=_ILLUS_PAINT,
    ),
}

#: 缺输入时的降级落点（A 版纸墨文字，零素材依赖）
DEFAULT_FALLBACK_STYLE = "ink_text"

#: 风格版本戳 —— 改 CSS/JS/字体注入后 bump，缓存文件名随之变化，避免旧文件复活
_STYLE_VERSION = "2"


# --------------------------------------------------------------------------
# 文档构建与物化
# --------------------------------------------------------------------------
_SHARED_JS = r"""
var SCENES = (window.SCENES && window.SCENES.length) ? window.SCENES : null;
var DUR = SCENES
  ? SCENES.map(function (s) { return s.durationSec || 0; })
  : ((window.SCENE_DURATIONS && window.SCENE_DURATIONS.length)
      ? window.SCENE_DURATIONS
      : [0]);
var TOTAL = DUR.reduce(function (a, b) { return a + b; }, 0);

function sceneAt(t) {
  var acc = 0;
  for (var i = 0; i < DUR.length; i++) {
    acc += DUR[i];
    if (t < acc) {
      return { i: i, k: DUR[i] > 0 ? (t - (acc - DUR[i])) / DUR[i] : 0 };
    }
  }
  return { i: Math.max(0, DUR.length - 1), k: 1 };
}

window.__setTime = function (t) {
  if (t < 0) t = 0;
  var s = sceneAt(t);
  window.__sceneIndex = s.i;
  var one = SCENES ? (SCENES[s.i] || null) : null;
  window._paint(one, t, s.k);
};

// 各幕首句在画面上的实际出现秒：抽帧目检撞切句瞬间取空字幕时据此前移取样。
// 无前摇的风格 = startSec；有入场动画的正式风格应返回更晚的秒。
window.__getSentBorn = function (i) {
  if (!SCENES || i < 0 || i >= SCENES.length) return null;
  return SCENES[i].startSec || 0;
};

window.__setTime(0);
"""


def bundled_fonts() -> list[dict[str, object]]:
    """扫描 ``resources/fonts`` 打包字体（按家族/字重排序，稳定输出）。

    Returns:
        [{path, family, weight, url}]；目录不存在/无字体 → 空列表。
    """
    fonts: list[dict[str, object]] = []
    if not _FONTS_DIR.is_dir():
        return fonts
    for path in sorted(_FONTS_DIR.rglob("*")):
        if path.suffix.lower() not in (".ttf", ".otf", ".woff2"):
            continue
        name = path.name
        family, weight = _FONT_META.get(name, (path.stem, 400))
        fonts.append(
            {
                "path": path,
                "family": family,
                "weight": weight,
                "url": path.resolve().as_uri(),
            }
        )
    return fonts


def font_face_css() -> str:
    """生成打包字体的 ``@font-face`` CSS（无字体 → 空串）。

    视觉稿是 ``file://`` 页面，字体路径必须用绝对 ``file://`` URI，
    渲染器已带 ``--allow-file-access-from-files`` 才能加载。
    """
    rules = []
    for font in bundled_fonts():
        rules.append(
            "@font-face {\n"
            f"  font-family: '{font['family']}';\n"
            f"  src: url('{font['url']}') format('truetype');\n"
            f"  font-weight: {font['weight']};\n"
            "}"
        )
    return "\n".join(rules)


def build_style_document(style: StyleDef) -> str:
    """把共享骨架与风格的画面代码拼成**单文件自包含** HTML。"""
    return (
        "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
        f"<title>STEPWORK · {style.label}</title>\n"
        "<style>\n"
        f"{font_face_css()}\n"
        f"{style.css}\n"
        "</style>\n</head>\n<body>\n"
        f"{style.body}\n"
        "<script>\n"
        f"{style.paint_js}\n"
        f"{_SHARED_JS}\n"
        "</script>\n</body>\n</html>\n"
    )


def style_document(style_id: str) -> Path:
    """返回某版式风格内置视觉稿的**本地路径**（无则物化到缓存目录）。

    产物是 ``style_id`` 的纯函数：同风格永远同内容 → 文件已存在就直接复用。
    原子写入：半截 HTML 会被下一次渲染覆盖，不会污染复用缓存。

    Raises:
        KeyError: 未知风格（handler 层转 INVALID_ARGUMENT）。
    """
    style = resolve_style(style_id)
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = os.path.join(_CACHE_DIR, f"{style.id}-v{_STYLE_VERSION}.html")
    if os.path.isfile(path):
        return Path(path)
    html = build_style_document(style)
    tmp = f"{path}.part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, path)
    return Path(path)


def resolve_style(style_id: str) -> StyleDef:
    """按 id 取风格；未知即 ``KeyError``（handler 转 INVALID_ARGUMENT）。"""
    try:
        return STYLES[style_id]
    except KeyError as exc:
        known = ", ".join(sorted(STYLES))
        raise KeyError(f"unknown style {style_id!r}; known styles: {known}") from exc


def list_styles() -> list[dict[str, object]]:
    """供前端/CLI 展示的风格清单（id / label / 能力）。"""
    return [
        {
            "id": s.id,
            "label": s.label,
            "capabilities": sorted(s.capabilities),
            "needsImage": s.needs_image,
        }
        for s in STYLES.values()
    ]
