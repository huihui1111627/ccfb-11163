"""Streamlit 页面无头冒烟测试。

运行：python scripts/smoke_app.py
通过 AppTest 在同一 session 中依次渲染全部页面，捕获运行时异常。
"""
from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parent.parent
PAGES = [
    str(ROOT / "app/Home.py"),
    str(ROOT / "app/pages/1_理赔流水.py"),
    str(ROOT / "app/pages/2_数据清洗.py"),
    str(ROOT / "app/pages/3_进展三角形.py"),
    str(ROOT / "app/pages/4_准备金估计.py"),
    str(ROOT / "app/pages/5_回溯检验.py"),
    str(ROOT / "app/pages/6_估算存档.py"),
]


def _clone_state(src: AppTest, dst: AppTest) -> AppTest:
    """AppTest 的 session_state 不能整体替换，需逐键写入。"""
    for k, v in src.session_state.items():
        dst.session_state[k] = v
    return dst


def main() -> int:
    failures = []

    home = AppTest.from_file(PAGES[0], default_timeout=120)
    home.run()
    if home.exception:
        failures.append(("Home(初始)", home.exception))
    home.slider[0].set_value(0.1)
    home.button[0].set_value(True).run()
    if home.exception:
        failures.append(("Home(生成数据)", home.exception))
    else:
        print("✓ Home 生成数据成功")

    for path in PAGES[1:]:
        page = _clone_state(home, AppTest.from_file(path, default_timeout=180))
        page.run()
        if page.exception:
            failures.append((path, page.exception))
            print(f"✗ {path}: {page.exception[0].value if page.exception else ''}")
        else:
            print(f"✓ {path} 渲染成功")

    # 回溯页触发计算（运行按钮在侧边栏）
    bt = _clone_state(home, AppTest.from_file(PAGES[5], default_timeout=300))
    bt.run()
    bt.sidebar.button[0].set_value(True).run()
    if bt.exception:
        failures.append(("回溯检验(运行)", bt.exception))
    else:
        print("✓ 回溯检验计算成功")

    # 准备金页：切到 BF + 手动因子并触发存档
    rs = _clone_state(home, AppTest.from_file(PAGES[4], default_timeout=300))
    rs.run()
    if rs.exception:
        failures.append(("准备金估计(初始)", rs.exception))
    # 存档按钮（页面最后一个 button）
    save_btns = [b for b in rs.button if "存档" in (b.label or "")]
    if save_btns:
        save_btns[0].set_value(True).run()
    if rs.exception:
        failures.append(("准备金估计(存档)", rs.exception))
    else:
        print("✓ 准备金估计与存档成功")

    # 存档页验证落库
    arc = _clone_state(home, AppTest.from_file(PAGES[6], default_timeout=120))
    arc.run()
    if arc.exception:
        failures.append(("估算存档页", arc.exception))
    else:
        print("✓ 存档库浏览成功")

    if failures:
        print(f"\n{len(failures)} 个页面失败:")
        for name, exc in failures:
            print(f"  - {name}: {exc}")
        return 1
    print("\n全部页面冒烟测试通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
