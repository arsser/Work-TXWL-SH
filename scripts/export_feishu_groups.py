#!/usr/bin/env python3
"""
Enumerate Feishu group chats for the logged-in user (lark-cli --as user),
then build a markdown table for docs +create.

Requires: lark-cli on PATH or LARK_CLI env.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Optional

LARK_CLI = os.environ.get("LARK_CLI", "lark-cli")
# 消息条数：单次拉取最多 50 条（升序首页）；若 has_more 则记为 ≥50（非全量）
SAMPLE_PAGE_SIZE = 50
PAGE_DELAY_MS = 50
# 可选：仅导出前 N 个「群/话题群」（过滤后计数），0 表示不限制
MAX_GROUPS = int(os.environ.get("FEISHU_GROUP_EXPORT_LIMIT", "0"))


def sample_last_and_count(chat_id: str) -> tuple[str, str, str]:
    """一次拉取：倒序首页 → 最后一条时间 + 条数估算（≥50 或准确≤50）。"""
    try:
        blob = subprocess.run(
            [
                LARK_CLI,
                "im",
                "+chat-messages-list",
                "--as",
                "user",
                "--chat-id",
                chat_id,
                "--start",
                "2000-01-01T00:00:00+08:00",
                "--end",
                "2099-12-31T23:59:59+08:00",
                "--page-size",
                str(SAMPLE_PAGE_SIZE),
                "--sort",
                "desc",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        root = json.loads(blob.stdout)
        if root.get("ok") is False:
            return "", "—", str(root.get("error", blob.stdout[:120]))
        data = root.get("data", {})
        msgs = data.get("messages", [])
        if not msgs:
            return "", "0", ""
        last_t = str(msgs[0].get("create_time", ""))
        n = len(msgs)
        if data.get("has_more"):
            return last_t, f"≥{n}（仅末屏抽样，非全群总数）", ""
        return last_t, str(n), ""
    except Exception as e:
        return "", "—", str(e)[:80]


def parse_cli_json(stdout: str) -> dict[str, Any]:
    """Parse lark-cli JSON: shortcuts use {ok,data}; native uses {code,msg,data}."""
    out = stdout.strip()
    if not out:
        raise RuntimeError("empty stdout")
    root = json.loads(out)
    if "ok" in root:
        if root.get("ok") is False:
            raise RuntimeError(json.dumps(root, ensure_ascii=False)[:2000])
        return root.get("data") if isinstance(root.get("data"), dict) else {}
    if root.get("code") != 0:
        raise RuntimeError(json.dumps(root, ensure_ascii=False)[:2000])
    d = root.get("data")
    return d if isinstance(d, dict) else {}


def run_json(args: list[str]) -> dict[str, Any]:
    r = subprocess.run(
        [LARK_CLI, *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(f"lark-cli failed: {args}\nstderr: {r.stderr}\nstdout: {r.stdout[:2000]}")
    return parse_cli_json(r.stdout)


def list_all_chats() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    while True:
        params: dict[str, Any] = {"page_size": 100, "sort_type": "ByActiveTimeDesc"}
        if page_token:
            params["page_token"] = page_token
        args = [
            "im",
            "chats",
            "list",
            "--as",
            "user",
            "--params",
            json.dumps(params, separators=(",", ":")),
            "--format",
            "json",
        ]
        blob = subprocess.run(
            [LARK_CLI, *args],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if blob.returncode != 0:
            raise RuntimeError(blob.stderr or blob.stdout)
        d = parse_cli_json(blob.stdout)
        batch = d.get("items", [])
        items.extend(batch)
        if not d.get("has_more"):
            break
        page_token = d.get("page_token")
        if not page_token:
            break
        time.sleep(PAGE_DELAY_MS / 1000.0)
    return items


def chats_get(chat_id: str) -> dict[str, Any]:
    return run_json(
        [
            "im",
            "chats",
            "get",
            "--as",
            "user",
            "--params",
            json.dumps({"chat_id": chat_id}, separators=(",", ":")),
            "--format",
            "json",
        ]
    )


def members_total(chat_id: str) -> tuple[Optional[int], str]:
    try:
        d = run_json(
            [
                "im",
                "chat.members",
                "get",
                "--as",
                "user",
                "--params",
                json.dumps({"chat_id": chat_id, "page_size": 1}, separators=(",", ":")),
                "--format",
                "json",
            ]
        )
        mt = d.get("member_total")
        if isinstance(mt, int):
            return mt, ""
        if isinstance(mt, str) and mt.isdigit():
            return int(mt), ""
        return None, ""
    except Exception as e:
        return None, str(e)[:80]


def last_message_time(chat_id: str) -> tuple[str, str]:
    """Deprecated path — 保留占位；逻辑已并入 sample_last_and_count。"""
    lt, _, err = sample_last_and_count(chat_id)
    return lt, err


@dataclass
class Row:
    name: str
    chat_id: str
    chat_mode: str
    create_time: str
    member_total: str
    msg_total: str
    last_msg_time: str
    note: str


def main() -> None:
    rows: list[Row] = []
    raw_items = list_all_chats()
    seen: set[str] = set()
    group_count = 0

    for it in raw_items:
        if MAX_GROUPS and group_count >= MAX_GROUPS:
            break
        cid = it.get("chat_id")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        name = (it.get("name") or "").replace("|", "\\|")
        try:
            g = chats_get(cid)
        except Exception as e:
            rows.append(
                Row(
                    name=name or cid,
                    chat_id=cid,
                    chat_mode="?",
                    create_time="—",
                    member_total="—",
                    msg_total="—",
                    last_msg_time="—",
                    note=f"get chat: {e}"[:120],
                )
            )
            continue

        mode = str(g.get("chat_mode") or "")
        # 仅群 / 话题群；跳过单聊等
        if mode not in ("group", "topic"):
            continue

        if MAX_GROUPS and group_count >= MAX_GROUPS:
            break
        group_count += 1
        # 创建时间：当前 get 接口通常不返回，占位
        ctime = str(g.get("create_time") or g.get("create_time_sec") or "—")

        mt, _ = members_total(cid)
        member_s = str(mt) if mt is not None else "—"

        last_t, msg_n, msg_err = sample_last_and_count(cid)
        note = ""
        if ctime == "—":
            note = "创建时间：OpenAPI 群详情未返回该字段"
        if msg_err:
            note = (note + "；" if note else "") + f"消息计数: {msg_err}"

        rows.append(
            Row(
                name=name or cid,
                chat_id=cid,
                chat_mode=mode,
                create_time=ctime,
                member_total=member_s,
                msg_total=msg_n,
                last_msg_time=last_t or "—",
                note=note.strip("；"),
            )
        )
        time.sleep(PAGE_DELAY_MS / 1000.0)

    # sort by last message time desc (best effort parse)
    def sort_key(r: Row) -> str:
        return r.last_msg_time

    rows.sort(key=sort_key, reverse=True)

    lines: list[str] = [
        "# 我参与的飞书群聊一览",
        "",
        "> 生成说明：使用本机 `lark-cli`（用户身份）拉取「群列表 + 群详情 + 成员总数 + 消息列表」。",
    ]
    if MAX_GROUPS > 0:
        lines.append(
            f"> **导出范围**：本次设置 `FEISHU_GROUP_EXPORT_LIMIT={MAX_GROUPS}`，仅处理在「群列表」遍历中**前 {MAX_GROUPS} 个** group/topic 群；要全量请设该环境变量为 `0` 后重新运行 `scripts/export_feishu_groups.py`（耗时与群数成正比）。"
        )
        lines.append("")
    lines.extend(
        [
            "> **创建时间**：`im/v1/chats` 详情接口多数租户下**不返回**建群时间，表中为 `—` 或接口偶发字段。",
            "> **消息总数**：OpenAPI **无汇总字段**；表中为**倒序首屏最多 "
            f"{SAMPLE_PAGE_SIZE} 条**的抽样：若本屏即全部历史则为**准确条数**；若仍有 `has_more` 则记为 "
            f"`≥{SAMPLE_PAGE_SIZE}`（**非全群总数**）。",
            "> **最后一条消息时间**：取该群按时间倒序第一条可见消息的 `create_time`。",
            "",
            f"共 **{len(rows)}** 个群（已排除单聊等非 group/topic）。",
            "",
            "| 群名称 | chat_id | 类型 | 创建时间 | 人数(member_total) | 消息总数(估算) | 最后一条消息时间 | 备注 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for r in rows:
        lines.append(
            "| {name} | `{cid}` | {mode} | {ct} | {mem} | {msg} | {lt} | {note} |".format(
                name=r.name,
                cid=r.chat_id,
                mode=r.chat_mode,
                ct=r.create_time,
                mem=r.member_total,
                msg=r.msg_total,
                lt=r.last_msg_time,
                note=(r.note or "").replace("|", "\\|"),
            )
        )

    out_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "Raw_Data",
        "feishu_groups_export.md",
    )
    out_path = os.path.normpath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(out_path)


if __name__ == "__main__":
    main()
