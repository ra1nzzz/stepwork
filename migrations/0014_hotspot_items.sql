-- Migration: 0014_hotspot_items.sql
-- Version:   0014
-- Upstream:  0013_brand_style_dna.sql
-- Purpose:   把「上游热点」从事后即焚的 MCP 调用结果，变成库里可追溯的事实。
--
--            此前 ``CallMcpTool('discover_hotspots')`` 返回完就散了：没有落库，
--            于是三件事都做不了 ——
--              ① 推荐无从去重（不知道上周推过什么，会反复推同一批热点）；
--              ② 反馈无处可记（用户点了「不感兴趣」，下次照推）；
--              ③ 热点与成片之间断链（这条片到底追的哪个热点，无从回溯）。
--            本表解决 ①③，``hotspot_feedback`` 解决 ②。
--
--            两表都只存**上游条目的引用**（id/标题/链接/热度），不存正文：
--            正文属外部未核实内容，按 PRD-AGT-003 也不该进库当素材。
-- Spec:      ROADMAP §S5；上游仓库 github.com/ra1nzzz/stepwork-hotspot-mcp

CREATE TABLE IF NOT EXISTS hotspot_items (
    id               TEXT PRIMARY KEY,
    -- 一次 discover 的批次号。同批次内可比，跨批次只用于追溯
    batch_id         TEXT NOT NULL,
    workspace_id     TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    -- 源 id（如 toutiao_hot / douhot）；与上游 SOURCES 的 key 一致
    source           TEXT NOT NULL,
    title            TEXT NOT NULL,
    url              TEXT NOT NULL DEFAULT '',
    -- 上游已去标签压空白的摘要；可能为空
    summary          TEXT NOT NULL DEFAULT '',
    -- 上游给的发布时间（ISO 8601）；榜单类源通常没有 → NULL
    published_at     TEXT,
    -- 上游热度值。**量纲跨源不可比**（抖音千万级 vs GitHub star 千级），
    -- 只能源内比；跨源比较一律用源内分位，见 runtime/hotspot/rank.py
    score            REAL,
    -- 源特有字段（board / parse / rank / language …），JSON 对象
    meta_json        TEXT NOT NULL DEFAULT '{}',
    -- 本仓抓取时刻（不是上游发布时间）
    discovered_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hotspot_items_workspace
    ON hotspot_items(workspace_id, discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_hotspot_items_batch
    ON hotspot_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_hotspot_items_source
    ON hotspot_items(workspace_id, source, discovered_at DESC);
-- 标题去重：同工作区同标题的热点，推荐时要能查出来降权
CREATE INDEX IF NOT EXISTS idx_hotspot_items_title
    ON hotspot_items(workspace_id, title);

-- 用户对热点的态度。**主键即「一条热点在一个工作区只有一个结论」**：
-- 重复记录只会让学习逻辑无所适从（到底听哪次的）。
CREATE TABLE IF NOT EXISTS hotspot_feedback (
    hotspot_id       TEXT NOT NULL,
    workspace_id     TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    project_id       TEXT REFERENCES content_projects(id) ON DELETE SET NULL,
    -- adopted（采纳成选题）/ ignored（看过，不感兴趣）/ rejected（明确不合适）
    verdict          TEXT NOT NULL,
    -- 用户给的理由（可空）。AI 写理由时也会读它：用户说过「太泛」的话题
    -- 下次就该降权
    reason           TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (hotspot_id, workspace_id)
);
CREATE INDEX IF NOT EXISTS idx_hotspot_feedback_workspace
    ON hotspot_feedback(workspace_id, verdict);
