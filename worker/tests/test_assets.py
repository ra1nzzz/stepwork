"""随包资源定位测试：源码运行 vs 冻结 exe（PyInstaller）。

锁死：``repo_root()`` 在冻结时返回 ``sys._MEIPASS``、源码运行返回仓库根，
且各调用点的资源路径都挂在它下面 —— 这是「侧车打包后 migrations / schemas /
字体还能找到」的唯一保证。这些路径此前各写一份 ``parents[N]``，冻结后
**静默指到不存在的解包相对位置**（migrations 丢了侧车直接起不来，字体丢了
只是字形悄悄变掉）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from worker.runtime import assets


def test_repo_root_source_mode_is_repo_root(monkeypatch: Any) -> None:
    """源码运行：仓库根 = ``worker/runtime/assets.py`` 的上两级。"""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    root = assets.repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "migrations").is_dir()
    assert (root / "schemas").is_dir()


def test_repo_root_frozen_mode_is_meipass(tmp_path: Path, monkeypatch: Any) -> None:
    """冻结：``sys._MEIPASS`` 说了算（且每次调用重判，不受导入时机影响）。"""
    monkeypatch.delenv("STEPWORK_ROOT", raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert assets.repo_root() == tmp_path
    assert assets.repo_path("resources", "fonts") == tmp_path / "resources" / "fonts"

    # 恢复源码模式 → 立刻回到仓库根（证明是「调用时判断」而非导入时定死）
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert (assets.repo_root() / "pyproject.toml").is_file()


def test_resource_call_sites_hang_off_repo_root() -> None:
    """四个资源调用点都必须挂在 ``repo_root()`` 下。

    这是**变更检测器**：新增一个 ``Path(__file__).resolve().parents[N]`` 形式的
    资源查找就会在冻结后失效，而这里会点名是哪个模块。
    """
    from worker.runtime import bootstrap
    from worker.runtime.commands import envelope
    from worker.runtime.providers.renderer import playwright
    from worker.runtime.render import styles

    root = assets.repo_root()
    assert bootstrap.migrations_dir() == root / "migrations"
    assert envelope._SCHEMA_PATH == root / "schemas" / "command-envelope.schema.json"
    assert styles._FONTS_DIR == root / "resources" / "fonts"
    assert playwright.DEFAULT_DOCUMENT == (
        root / "worker" / "runtime" / "render" / "assets" / "s1_probe.html"
    )


def _resource_call_sites() -> dict[str, Path]:
    """所有「运行期从仓库根读文件」的调用点（属性名 → 实际路径）。

    与 ``test_resource_call_sites_hang_off_repo_root`` 保存同一份枚举 —— 两处
    一起改，漏改会被其中一条点名。
    """
    from worker.runtime import bootstrap
    from worker.runtime.commands import envelope
    from worker.runtime.providers.renderer import playwright
    from worker.runtime.render import styles

    return {
        "bootstrap.migrations_dir": bootstrap.migrations_dir(),
        "envelope._SCHEMA_PATH": envelope._SCHEMA_PATH,
        "styles._FONTS_DIR": styles._FONTS_DIR,
        "playwright.DEFAULT_DOCUMENT": playwright.DEFAULT_DOCUMENT,
    }


def _covered_by_manifest(rel: str) -> bool:
    """仓库根相对路径是否被随包清单覆盖（整目录收集 → 目录下所有文件都算）。"""
    if rel in assets.BUNDLED_FILES:
        return True
    return any(rel == d or rel.startswith(d + "/") for d in assets.BUNDLED_DIRS)


def test_resource_call_sites_are_covered_by_bundle_manifest() -> None:
    """每个资源调用点都必须被随包清单覆盖。

    ``test_resource_call_sites_hang_off_repo_root`` 只保证「路径挂在仓库根下」
    —— 挂对了但**没打进 exe** 一样是空的，而且大多不会报错（字体丢了只是静默
    回落系统字体）。两条合起来才是「冻结后真的还找得到」。新增资源读取点却忘了
    登记清单，这里会点名是哪个属性。
    """
    root = assets.repo_root()
    uncovered = [
        name
        for name, path in _resource_call_sites().items()
        if not _covered_by_manifest(path.relative_to(root).as_posix())
    ]
    assert not uncovered, (
        f"这些资源读取点没进随包清单（追加到 assets.BUNDLED_DIRS/BUNDLED_FILES）："
        f"{uncovered}"
    )


def test_bundled_manifest_entries_exist() -> None:
    """清单里每条都真实存在，且目录/文件的归类没写反。

    写错一个字母时 PyInstaller 只**告警**不报错 —— 打包照过，资源照缺。
    """
    root = assets.repo_root()
    bad = [
        f"{rel}（应为目录）" for rel in assets.BUNDLED_DIRS if not (root / rel).is_dir()
    ] + [
        f"{rel}（应为文件）" for rel in assets.BUNDLED_FILES if not (root / rel).is_file()
    ]
    assert not bad, f"随包清单指向不存在的路径：{bad}"


def test_bundled_datas_preserves_repo_relative_layout() -> None:
    """不变式：资源在 exe 里必须保持它**在仓库里的相对位置**。

    这是 ``datas`` 唯一的正确性要求 —— ``repo_path("resources", "fonts")`` 在
    冻结态算出来是 ``_MEIPASS/resources/fonts``，所以打包落点必须同样是
    ``resources/fonts``。``dest`` 差一层（例如用 basename 把目录收成 ``fonts``）
    时 PyInstaller 不报错、运行期也不报错，只是字体静默扫不到。

    目录 / 文件的判定取自**磁盘实况**（``is_dir()``）而非我心里的清单，
    因为 PyInstaller 对两者的语义不同：目录是把**内容**放进 ``dest``，
    文件是把**文件本身**放进 ``dest``。
    """
    repo = assets.repo_root()
    wrong = []
    for src, dest in assets.bundled_datas(repo):
        src_rel = Path(src).relative_to(repo)
        landed = (
            Path(dest) if (repo / src_rel).is_dir() else Path(dest) / src_rel.name
        )
        if landed.as_posix() != src_rel.as_posix():
            wrong.append(f"{src_rel.as_posix()} → {landed.as_posix()}")
    assert not wrong, f"这些资源在 exe 里的落点偏离了仓库内相对位置：{wrong}"


def test_bundled_datas_honours_an_explicit_repo(tmp_path: Path) -> None:
    """``bundled_datas(repo)`` 用传入的根，不吃 ``_MEIPASS`` —— 打包时仓库根
    与运行期解包根是两个不同的地方，取错一个就会收到一堆不存在的源路径。
    """
    datas = assets.bundled_datas(tmp_path)
    assert datas, "清单不该为空"
    assert all(Path(src).is_relative_to(tmp_path) for src, _ in datas)
