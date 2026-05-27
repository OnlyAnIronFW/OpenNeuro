#!/usr/bin/env python
"""B站 Cookie 自动提取脚本
从本地浏览器 (Chrome/Edge/Firefox) 提取已登录的 B站 Cookie，
保存到 data/bili_cookie.json 供适配器使用。

用法:
  python scripts/get_bili_cookie.py          # 自动提取
  python scripts/get_bili_cookie.py --manual # 手动输入
  python scripts/get_bili_cookie.py --test   # 测试已有cookie
"""

import argparse
import json
import os
import sys
from pathlib import Path

COOKIE_FILE = Path("data/bili_cookie.json")


def extract_from_browser() -> dict | None:
    """从本地浏览器提取 B站 Cookie"""
    browsers = []
    try:
        import browser_cookie3
        browsers = [
            ("Chrome", browser_cookie3.chrome),
            ("Edge", browser_cookie3.edge),
            ("Firefox", browser_cookie3.firefox),
            ("Chromium", browser_cookie3.chromium),
        ]
    except ImportError:
        print("[!] browser_cookie3 未安装。正在安装...")
        os.system(f"{sys.executable} -m pip install browser-cookie3 -q")
        try:
            import browser_cookie3
            browsers = [
                ("Chrome", browser_cookie3.chrome),
                ("Edge", browser_cookie3.edge),
                ("Firefox", browser_cookie3.firefox),
            ]
        except ImportError:
            print("[!] 安装失败，请手动输入Cookie")
            return None

    for name, loader in browsers:
        try:
            cookies = loader(domain_name="bilibili.com")
            sessdata = None
            csrf = None
            for c in cookies:
                if c.name == "SESSDATA":
                    sessdata = c.value
                elif c.name == "bili_jct":
                    csrf = c.value

            if sessdata:
                print(f"[+] 从 {name} 提取成功!")
                return {"sessdata": sessdata, "csrf": csrf or sessdata, "source": name}
        except Exception as e:
            pass

    return None


def test_cookie(sessdata: str, csrf: str, room_id: int = 4538234) -> bool:
    """测试Cookie是否可用"""
    try:
        import aiohttp, asyncio
        async def _test():
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": f"https://live.bilibili.com/{room_id}",
            }
            cookies = {"SESSDATA": sessdata, "bili_jct": csrf}
            async with aiohttp.ClientSession(headers=headers, cookies=cookies) as s:
                async with s.get(
                    "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo",
                    params={"id": room_id, "type": 0}, timeout=10
                ) as r:
                    data = await r.json()
                    return data.get("code") == 0
        return asyncio.run(_test())
    except Exception:
        return False


def open_browser_and_wait():
    """打开B站让用户登录, 然后重试提取"""
    import webbrowser
    print("[*] 正在打开B站登录页...")
    webbrowser.open("https://passport.bilibili.com/login")
    input("[*] 请在浏览器中登录B站，登录完成后按 Enter 继续...")
    return extract_from_browser()


def main():
    parser = argparse.ArgumentParser(description="B站Cookie提取工具")
    parser.add_argument("--manual", action="store_true", help="手动输入Cookie")
    parser.add_argument("--test", action="store_true", help="测试已有Cookie")
    parser.add_argument("--room", type=int, default=4538234, help="测试房间号")
    args = parser.parse_args()

    # 测试模式
    if args.test:
        if COOKIE_FILE.exists():
            data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            print(f"测试Cookie (来源: {data.get('source', 'unknown')})...")
            ok = test_cookie(data["sessdata"], data["csrf"], args.room)
            if ok:
                print(f"[+] Cookie有效! 房间{args.room}可达")
            else:
                print("[-] Cookie已过期, 请重新获取")
                COOKIE_FILE.unlink()
        else:
            print("[!] 没有保存的Cookie, 请先运行提取")
        return

    # 手动输入
    if args.manual:
        sessdata = input("请输入 SESSDATA: ").strip()
        csrf = input("请输入 bili_jct (直接回车=SESSDATA): ").strip()
        if not sessdata:
            print("[-] 未输入Cookie")
            return
        cookie_data = {"sessdata": sessdata, "csrf": csrf or sessdata, "source": "manual"}
    else:
        # 自动提取
        print("[*] 正在从浏览器提取B站Cookie...")
        cookie_data = extract_from_browser()
        if not cookie_data:
            print("[*] 未检测到登录状态，帮你打开B站登录页...")
            cookie_data = open_browser_and_wait()
        if not cookie_data:
            print("[-] 仍然提取失败。请使用 --manual 手动输入")
            return

    # 测试
    print(f"[*] 测试Cookie (房间{args.room})...")
    ok = test_cookie(cookie_data["sessdata"], cookie_data["csrf"], args.room)
    if ok:
        print(f"[+] Cookie有效! 已保存到 {COOKIE_FILE}")
        COOKIE_FILE.write_text(json.dumps(cookie_data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print("[-] Cookie无效或已过期, 请重新登录B站")


if __name__ == "__main__":
    main()
